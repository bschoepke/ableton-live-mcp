from __future__ import annotations

import importlib.util
import sys
import threading
import types
from pathlib import Path


class FakeControlSurface:
    def __init__(self, _c_instance=None):
        pass

    def component_guard(self):
        class Guard:
            def __enter__(self):
                return None

            def __exit__(self, *_args):
                return False

        return Guard()

    def disconnect(self):
        pass

    def log_message(self, _message):
        pass

    def schedule_message(self, _delay, callback):
        callback()


class FakeBrowserItem:
    def __init__(self, name, *, loadable=False, folder=False, children=None, device=False):
        self.name = name
        self.is_loadable = loadable
        self.is_folder = folder
        self.is_device = device
        self.uri = "fake:%s" % name
        self.source = "fake"
        self._children = children or []

    @property
    def iter_children(self):
        return iter(self._children)


class FakeBrowser:
    def __init__(self):
        self.instruments = FakeBrowserItem("instruments", folder=True, children=[
            FakeBrowserItem("Analog", loadable=True, device=True),
            FakeBrowserItem("Drum Rack", loadable=True, device=True),
        ])
        self.plugins = FakeBrowserItem("plugins", folder=True, children=[
            FakeBrowserItem("AUv2", folder=True, children=[
                FakeBrowserItem("Vendor", folder=True, children=[
                    FakeBrowserItem("Plugin Synth", loadable=True, device=True),
                ]),
            ]),
        ])
        self.drums = FakeBrowserItem("drums", folder=True, children=[
            FakeBrowserItem("Drum Hits", folder=True, children=[
                FakeBrowserItem("Bell", folder=True, children=[
                    FakeBrowserItem("505 Cowbell Hi.flac", loadable=True),
                ]),
            ]),
        ])
        self.loaded = []

    def load_item(self, item):
        self.loaded.append(item.name)


class FakeApplication:
    def __init__(self):
        self.browser = FakeBrowser()

    def get_version_string(self):
        return "12.3.8"


class FakeVector(list):
    pass


class FakeListenerObject:
    def __init__(self):
        self.value = 1
        self.added = []
        self.removed = []

    def add_value_listener(self, callback):
        self.added.append(callback)

    def remove_value_listener(self, callback):
        self.removed.append(callback)


class FakeSong:
    def __init__(self):
        self.tempo = 120.0
        self.current_song_time = 0.0
        self.tracks = FakeVector([types.SimpleNamespace(name="Track 1"), types.SimpleNamespace(name="Track 2")])
        self.scenes = FakeVector([types.SimpleNamespace(name="Scene 1")])
        self.view = types.SimpleNamespace(selected_track=None)

    def get_beats_loop_start(self):
        return "1.1.1"


def load_bridge_module(monkeypatch):
    app = FakeApplication()
    live = types.ModuleType("Live")
    live.Application = types.SimpleNamespace(get_application=lambda: app)
    framework = types.ModuleType("_Framework")
    control_surface = types.ModuleType("_Framework.ControlSurface")
    control_surface.ControlSurface = FakeControlSurface
    monkeypatch.setitem(sys.modules, "Live", live)
    monkeypatch.setitem(sys.modules, "_Framework", framework)
    monkeypatch.setitem(sys.modules, "_Framework.ControlSurface", control_surface)

    path = Path(__file__).resolve().parents[1] / "remote_scripts" / "Ableton_Object_MCP" / "bridge.py"
    spec = importlib.util.spec_from_file_location("fake_ableton_bridge", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module, app


def make_bridge(monkeypatch):
    module, app = load_bridge_module(monkeypatch)
    bridge = object.__new__(module.AbletonObjectMCP)
    song = FakeSong()
    bridge._objects = {}
    bridge._listeners = {}
    bridge._events = []
    bridge._running = True
    bridge._main_thread_id = threading.current_thread().ident
    bridge.song = lambda: song
    return bridge, song, app


def test_resolve_get_children_and_call(monkeypatch):
    bridge, song, _app = make_bridge(monkeypatch)
    assert bridge._resolve_path("live_set tracks 1").name == "Track 2"

    result = bridge._rpc_get({
        "ref": {"path": "live_set"},
        "properties": ["tempo"],
        "children": {"tracks": 1},
    })
    assert result["properties"]["tempo"] == 120.0
    assert len([item for item in result["children"]["tracks"] if not item.get("truncated")]) == 1
    assert result["children"]["tracks"][-1] == {"truncated": True}
    assert "repr" not in result["children"]["tracks"][0]

    children = bridge._rpc_children({"ref": {"path": "live_set"}, "child": "tracks", "limit": 1})
    assert len([item for item in children if not item.get("truncated")]) == 1
    assert children[-1] == {"truncated": True}
    assert bridge._rpc_call({"ref": {"path": "live_set"}, "method": "get_beats_loop_start"}) == "1.1.1"


def test_app_browser_path_roots_and_stale_id_errors(monkeypatch):
    bridge, _song, _app = make_bridge(monkeypatch)
    assert bridge._resolve_path("app").get_version_string() == "12.3.8"
    assert bridge._resolve_path("browser instruments").name == "instruments"
    try:
        bridge._resolve({"id": 123456})
    except KeyError as exc:
        assert "Unknown or stale object id" in str(exc)
    else:
        raise AssertionError("expected stale id error")


def test_batch_and_id_resolution(monkeypatch):
    bridge, song, _app = make_bridge(monkeypatch)
    summary = bridge._object_summary(song)
    result = bridge._rpc_batch({
        "operations": [
            {"method": "get", "params": {"ref": {"id": summary["id"]}, "properties": ["tempo"]}},
            {"method": "children", "params": {"ref": {"path": "live_set"}, "child": "tracks", "limit": 1}},
        ],
    })
    assert [item["ok"] for item in result] == [True, True]
    assert result[0]["result"]["properties"]["tempo"] == 120.0


def test_batch_inherits_response_controls(monkeypatch):
    bridge, _song, _app = make_bridge(monkeypatch)
    result = bridge._rpc_batch({
        "max_items": 2,
        "max_string_length": 3,
        "operations": [
            {"method": "eval", "params": {"expr": "list(range(5))"}},
            {"method": "eval", "params": {"expr": "'abcdef'"}},
        ],
    })
    assert result[0]["result"] == [0, 1, {"truncated": True, "omitted": 3}]
    assert result[1]["result"] == "abc...<truncated 3 chars>"


def test_exec_returns_result_binding(monkeypatch):
    bridge, _song, _app = make_bridge(monkeypatch)
    result = bridge._rpc_exec({"code": "song.tempo = 124\nresult = {'tempo': song.tempo}"})
    assert result == {"tempo": 124}


def test_browser_roots_search_and_load(monkeypatch):
    bridge, song, app = make_bridge(monkeypatch)
    roots = bridge._rpc_browser_roots({})
    assert "plugins" in {root["name"] for root in roots}

    plugins = bridge._rpc_browser_search({
        "query": "plugin synth",
        "roots": ["plugins"],
        "limit": 2,
        "max_depth": 5,
    })
    assert plugins["results"][0]["name"] == "Plugin Synth"
    assert plugins["results"][0]["is_loadable"] is True

    drums = bridge._rpc_browser_search({"query": "cowbell", "roots": ["drums"], "limit": 1, "max_depth": 5})
    assert drums["results"][0]["name"] == "505 Cowbell Hi.flac"

    song.view.selected_track = None
    bridge._rpc_browser_load({"item": {"id": plugins["results"][0]["id"]}, "target_track": {"path": "live_set tracks 0"}})
    assert app.browser.loaded == ["Plugin Synth"]
    assert song.view.selected_track.name == "Track 1"


def test_browser_search_can_stop_on_limit(monkeypatch):
    bridge, _song, _app = make_bridge(monkeypatch)
    result = bridge._rpc_browser_search({
        "query": "",
        "roots": ["drums"],
        "limit": 1,
        "include_folders": True,
        "loadable_only": False,
        "stop_on_limit": True,
    })
    assert len(result["results"]) == 1
    assert result["visited"] == 1
    assert result["truncated"] is True


def test_encode_bounds_cycles_and_detail(monkeypatch):
    bridge, _song, _app = make_bridge(monkeypatch)
    cycle = []
    cycle.append(cycle)
    encoded = bridge._encode(cycle)
    assert encoded == [{"truncated": True, "reason": "cycle"}]

    encoded = bridge._encode(list(range(5)), bridge._encode_options({"max_items": 2}))
    assert encoded == [0, 1, {"truncated": True, "omitted": 3}]

    obj = types.SimpleNamespace(name="Obj")
    assert "repr" in bridge._object_summary(obj, detail=True)


def test_observe_events_and_cleanup(monkeypatch):
    bridge, _song, _app = make_bridge(monkeypatch)
    obj = FakeListenerObject()
    summary = bridge._object_summary(obj)

    first = bridge._rpc_observe({"ref": {"id": summary["id"]}, "property": "value", "enabled": True})
    second = bridge._rpc_observe({"ref": {"id": summary["id"]}, "property": "value", "enabled": True})
    assert first["observing"] is True
    assert second["observing"] is True
    assert len(obj.added) == 1

    obj.value = 2
    obj.added[0]()
    assert bridge._rpc_events({"limit": 10}) == [{"id": summary["id"], "property": "value", "value": 2}]

    bridge._rpc_observe({"ref": {"id": summary["id"]}, "property": "value", "enabled": False})
    assert len(obj.removed) == 1
