from __future__ import annotations

import importlib.util
import json
import socket
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
        self.user_library = FakeBrowserItem("User Library", folder=True, children=[
            FakeBrowserItem("Presets", folder=True, children=[
                FakeBrowserItem("Audio Effects", folder=True, children=[
                    FakeBrowserItem("Max Audio Effect", folder=True, children=[
                        FakeBrowserItem("AgentAudioTap", loadable=True, device=True),
                        FakeBrowserItem("AgentM4L_audio_effect_Wobble", loadable=True, device=True),
                    ]),
                ]),
                FakeBrowserItem("Instruments", folder=True, children=[
                    FakeBrowserItem("Max Instrument", folder=True, children=[
                        FakeBrowserItem("AgentM4L_instrument_Lead", loadable=True, device=True),
                    ]),
                ]),
            ]),
        ])
        self.loaded = []
        self.previewed = []
        self.stopped_preview = False

    def load_item(self, item):
        self.loaded.append(item.name)

    def preview_item(self, item):
        self.previewed.append(item.name)

    def stop_preview(self):
        self.stopped_preview = True


class FakeApplication:
    def __init__(self):
        self.browser = FakeBrowser()

    def get_version_string(self):
        return "test-live-version"


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


class FakeParameter:
    def __init__(self, name, value=0.5, minimum=0.0, maximum=1.0, quantized=False, items=None):
        self.name = name
        self.value = value
        self.min = minimum
        self.max = maximum
        self.default_value = value
        self.display_value = value * 100
        self.is_quantized = quantized
        self.value_items = items or []

    def str_for_value(self, value):
        return "%s display" % value


class FakeDevice:
    def __init__(self):
        self.name = "Compressor"
        self.parameters = FakeVector([
            FakeParameter("Device On", 1.0, quantized=True, items=["Off", "On"]),
            FakeParameter("Threshold", 0.85),
            FakeParameter("Ratio", 0.75),
        ])


class FakeEnvelope:
    def __init__(self):
        self._events = []

    def events_in_range(self, start_time, end_time):
        return [event for event in self._events if start_time <= event.time < end_time]

    def delete_events_in_range(self, start_time, end_time):
        self._events = [event for event in self._events if not (start_time <= event.time < end_time)]

    def insert_step(self, time, duration, value):
        self._events.append(types.SimpleNamespace(time=time, value=value))
        self._events.append(types.SimpleNamespace(time=time + duration, value=value))

    def value_at_time(self, _time):
        return self._events[0].value if self._events else 0.0


class FakeWarpMarker:
    def __init__(self, sample_time, beat_time):
        self.sample_time = sample_time
        self.beat_time = beat_time


class FakeMidiNoteSpecification:
    def __init__(self, pitch, start_time, duration, velocity, mute=False):
        self.pitch = pitch
        self.start_time = start_time
        self.duration = duration
        self.velocity = velocity
        self.mute = mute


class FakeClip:
    _next_note_id = 100

    def __init__(self, name, *, midi=True, start_time=0.0, end_time=4.0):
        self.name = name
        self.is_midi_clip = midi
        self.is_audio_clip = not midi
        self.is_session_clip = True
        self.is_arrangement_clip = False
        self.start_time = start_time
        self.end_time = end_time
        self.length = 4.0
        self.loop_start = 0.0
        self.loop_end = 4.0
        self.muted = False
        self.has_envelopes = False
        self._notes = [
            types.SimpleNamespace(
                note_id=1,
                pitch=60,
                start_time=0.0,
                duration=0.5,
                velocity=40.0,
                mute=False,
                probability=1.0,
                velocity_deviation=0.0,
                release_velocity=64.0,
            )
        ]
        self._envelopes = {}
        self.warping = True
        self.warp_mode = 0
        self.available_warp_modes = FakeVector([0, 1, 2, 3, 4, 6])
        self.warp_markers = FakeVector([FakeWarpMarker(0.0, 0.0), FakeWarpMarker(2.0, 4.0)])
        self.legacy_remove_notes_called = False

    def get_all_notes_extended(self):
        return self._notes

    def add_new_notes(self, specs):
        for spec in specs:
            self._notes.append(types.SimpleNamespace(
                note_id=FakeClip._next_note_id,
                pitch=spec.pitch,
                start_time=spec.start_time,
                duration=spec.duration,
                velocity=spec.velocity,
                mute=spec.mute,
                probability=1.0,
                velocity_deviation=0.0,
                release_velocity=64.0,
            ))
            FakeClip._next_note_id += 1

    def remove_notes_extended(self, from_pitch, pitch_span, from_time, time_span):
        pitch_end = from_pitch + pitch_span
        time_end = from_time + time_span
        self._notes = [
            note for note in self._notes
            if not (from_pitch <= note.pitch < pitch_end and from_time <= note.start_time < time_end)
        ]

    def remove_notes(self, from_time, from_pitch, time_span, pitch_span):
        self.legacy_remove_notes_called = True
        pitch_end = from_pitch + pitch_span
        time_end = from_time + time_span
        self._notes = [
            note for note in self._notes
            if not (from_pitch <= note.pitch < pitch_end and from_time <= note.start_time < time_end)
        ]

    def apply_note_modifications(self, notes):
        updates = {note.note_id: note for note in notes}
        self._notes = [updates.get(note.note_id, note) for note in self._notes]

    def automation_envelope(self, parameter):
        return self._envelopes.get(parameter)

    def create_automation_envelope(self, parameter):
        envelope = FakeEnvelope()
        self._envelopes[parameter] = envelope
        self.has_envelopes = True
        return envelope

    def clear_envelope(self, parameter):
        self._envelopes.pop(parameter, None)
        self.has_envelopes = bool(self._envelopes)

    def add_warp_marker(self, marker):
        self.warp_markers.append(marker)

    def move_warp_marker(self, beat_time, beat_time_delta):
        for marker in self.warp_markers:
            if marker.beat_time == beat_time:
                marker.beat_time += beat_time_delta
                return
        raise RuntimeError("The specified warp marker doesn't exist")

    def remove_warp_marker(self, beat_time):
        for index, marker in enumerate(self.warp_markers):
            if marker.beat_time == beat_time:
                del self.warp_markers[index]
                return
        raise RuntimeError("The specified warp marker doesn't exist")


class FakeClipSlot:
    def __init__(self, clip=None):
        self.clip = clip
        self.has_clip = clip is not None
        self.fired = False
        self.deleted = False

    def create_clip(self, length):
        self.clip = FakeClip("Created Clip", end_time=float(length))
        self.clip._notes = []
        self.clip.length = float(length)
        self.clip.loop_end = float(length)
        self.has_clip = True

    def delete_clip(self):
        self.clip = None
        self.has_clip = False
        self.deleted = True

    def fire(self):
        self.fired = True


class FakeTrack:
    def __init__(self, name, devices=None, clip_slots=None, arrangement_clips=None):
        self.name = name
        self.mute = False
        self.solo = False
        self.arm = False
        self.implicit_arm = False
        self.can_be_armed = True
        self.is_foldable = False
        self.devices = FakeVector(devices or [])
        self.clip_slots = FakeVector(clip_slots or [])
        self.arrangement_clips = FakeVector(arrangement_clips or [])

    def duplicate_clip_to_arrangement(self, clip, destination_time):
        copied = FakeClip(clip.name, midi=clip.is_midi_clip, start_time=destination_time, end_time=destination_time + clip.length)
        copied.is_session_clip = False
        copied.is_arrangement_clip = True
        copied._notes = [types.SimpleNamespace(**note.__dict__) for note in clip.get_all_notes_extended()]
        self.arrangement_clips.append(copied)
        return copied

    def create_audio_clip(self, file_path, destination_time):
        clip = FakeClip(Path(file_path).name, midi=False, start_time=destination_time, end_time=destination_time + 4.0)
        self.arrangement_clips.append(clip)
        return clip

    def insert_device(self, device_name, device_index=-1):
        device = FakeDevice()
        device.name = device_name
        if "midi_effect" in device_name:
            device.class_name = "MxDeviceMidiEffect"
        elif "instrument" in device_name:
            device.class_name = "MxDeviceInstrument"
        elif "audio_effect" in device_name:
            device.class_name = "MxDeviceAudioEffect"
        if device_index is None or device_index < 0 or device_index >= len(self.devices):
            self.devices.append(device)
        else:
            self.devices.insert(device_index, device)
        return None

    def delete_device(self, index):
        del self.devices[index]


class FakeSong:
    def __init__(self):
        self.tempo = 120.0
        self.current_song_time = 0.0
        self.signature_numerator = 4
        self.signature_denominator = 4
        self.tracks = FakeVector([
            FakeTrack("Track 1", devices=[FakeDevice()], clip_slots=[FakeClipSlot(FakeClip("Clip 1")), FakeClipSlot()], arrangement_clips=[FakeClip("Arr Clip 1")]),
            FakeTrack("Track 2", devices=[], clip_slots=[FakeClipSlot()], arrangement_clips=[]),
        ])
        self.scenes = FakeVector([types.SimpleNamespace(name="Scene 1")])
        self.return_tracks = FakeVector([
            FakeTrack("A-Reverb"),
        ])
        self.master_track = FakeTrack("Main")
        self.view = types.SimpleNamespace(selected_track=None)
        self.is_playing = False

    def get_beats_loop_start(self):
        return "1.1.1"

    def jump_by(self, offset):
        self.current_song_time = max(0.0, self.current_song_time + float(offset))

    def start_playing(self):
        self.is_playing = True

    def continue_playing(self):
        self.is_playing = True

    def stop_playing(self):
        self.is_playing = False


def load_bridge_module(monkeypatch):
    app = FakeApplication()
    live = types.ModuleType("Live")
    live.Application = types.SimpleNamespace(get_application=lambda: app)
    live.Clip = types.SimpleNamespace(WarpMarker=FakeWarpMarker, MidiNoteSpecification=FakeMidiNoteSpecification)
    framework = types.ModuleType("_Framework")
    control_surface = types.ModuleType("_Framework.ControlSurface")
    control_surface.ControlSurface = FakeControlSurface
    monkeypatch.setitem(sys.modules, "Live", live)
    monkeypatch.setitem(sys.modules, "_Framework", framework)
    monkeypatch.setitem(sys.modules, "_Framework.ControlSurface", control_surface)

    path = Path(__file__).resolve().parents[1] / "Ableton_Live_MCP" / "bridge.py"
    spec = importlib.util.spec_from_file_location("fake_ableton_bridge", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module, app


def make_bridge(monkeypatch):
    module, app = load_bridge_module(monkeypatch)
    bridge = object.__new__(module.AbletonLiveMCP)
    song = FakeSong()
    bridge._objects = {}
    bridge._listeners = {}
    bridge._events = []
    bridge._running = True
    bridge._main_thread_id = threading.current_thread().ident
    bridge._main_thread_timeout_count = 0
    bridge._main_thread_last_timeout_at = 0.0
    bridge._main_thread_last_timeout_method = None
    bridge._main_thread_last_success_at = 0.0
    bridge._main_thread_stall_until = 0.0
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


def test_agent_audio_tap_writes_file_command_by_default(monkeypatch):
    bridge, _song, _app = make_bridge(monkeypatch)
    sent = []

    class FakeSocket:
        def __init__(self, *_args):
            pass

        def sendto(self, payload, address):
            sent.append((payload, address))

        def close(self):
            pass

    monkeypatch.setattr(socket, "socket", FakeSocket)
    written = {}

    class FakeFile:
        def __init__(self, path, mode):
            written["path"] = path
            written["mode"] = mode

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def write(self, value):
            written["value"] = written.get("value", "") + value
            return len(value)

    monkeypatch.setattr(module := load_bridge_module(monkeypatch)[0], "open", lambda path, mode: FakeFile(path, mode), raising=False)
    bridge.__class__ = module.AbletonLiveMCP
    result = bridge._rpc_agent_audio_tap({"command": "start", "path": "tap.wav", "id": "abc"})

    assert result["sent"] is False
    assert result["bytes"] == 0
    assert written == {
        "path": module._temp_file("agent_audio_tap_command.json"),
        "mode": "w",
        "value": '{"id":"abc","command":"start","path":"tap.wav"}',
    }
    assert sent == []


def test_agent_audio_tap_can_opt_into_udp_command(monkeypatch):
    bridge, _song, _app = make_bridge(monkeypatch)
    sent = []

    class FakeSocket:
        def __init__(self, *_args):
            pass

        def sendto(self, payload, address):
            sent.append((payload, address))

        def close(self):
            pass

    monkeypatch.setattr(socket, "socket", FakeSocket)
    module = load_bridge_module(monkeypatch)[0]
    bridge.__class__ = module.AbletonLiveMCP
    result = bridge._rpc_agent_audio_tap({"command": "start", "path": "tap.wav", "id": "abc", "udp": True})

    assert result["sent"] is True
    assert sent == [(
        b"/agent_audio_tap\x00\x00\x00\x00,ss\x00start\x00\x00\x00tap.wav\x00",
        ("127.0.0.1", 17654),
    )]


def test_agent_audio_tap_start_can_use_preopened_path(monkeypatch):
    bridge, _song, _app = make_bridge(monkeypatch)
    sent = []

    class FakeSocket:
        def __init__(self, *_args):
            pass

        def sendto(self, payload, address):
            sent.append((payload, address))

        def close(self):
            pass

    monkeypatch.setattr(socket, "socket", FakeSocket)
    result = bridge._rpc_agent_audio_tap({"command": "start", "id": "abc", "udp": True})
    assert result["path"] is None
    assert sent == [(b"/agent_audio_tap\x00\x00\x00\x00,s\x00\x00start\x00\x00\x00", ("127.0.0.1", 17654))]


def test_agent_audio_tap_setup_loads_on_master_and_solos_target(monkeypatch):
    bridge, song, app = make_bridge(monkeypatch)
    song.tracks[1].devices.append(FakeDevice())
    song.tracks[1].devices[-1].name = "AgentAudioTap"

    result = bridge._rpc_agent_audio_tap_setup({
        "placement": "master",
        "remove_existing": True,
        "solo_track": {"path": "live_set tracks 0"},
        "reset_time": 0,
    })

    assert result["target_track"] == "Main"
    assert result["loaded"] is True
    assert app.browser.loaded == ["AgentAudioTap"]
    assert [track.solo for track in song.tracks] == [True, False]
    assert [device.name for device in song.tracks[1].devices] == []
    assert song.current_song_time == 0.0
    assert song.is_playing is False


def test_agent_m4l_device_writes_command_sends_udp_and_loads(monkeypatch):
    module, app = load_bridge_module(monkeypatch)
    bridge = object.__new__(module.AbletonLiveMCP)
    song = FakeSong()
    bridge._objects = {}
    bridge._listeners = {}
    bridge._events = []
    bridge.song = lambda: song
    sent = []
    written = {}

    class FakeSocket:
        def __init__(self, *_args):
            pass

        def sendto(self, payload, address):
            sent.append((payload, address))

        def close(self):
            pass

    class FakeFile:
        def __init__(self, path, mode):
            self.path = path
            self.mode = mode
            self.value = ""

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            if "w" in self.mode:
                written[self.path] = self.value
            return False

        def write(self, value):
            self.value += value
            return len(value)

    monkeypatch.setattr(socket, "socket", FakeSocket)
    monkeypatch.setattr(module, "open", lambda path, mode: FakeFile(path, mode), raising=False)

    patch = {
        "objects": [{"id": "osc", "text": "cycle~ 110", "x": 160, "y": 140}],
        "connections": [],
    }
    result = bridge._rpc_agent_m4l_device({
        "role": "audio_effect",
        "instance_id": "Wobble",
        "device_name": "AgentM4L_audio_effect_Wobble",
        "target_track": {"path": "live_set tracks 1"},
        "patch": patch,
        "webui": {"html_path": "/tmp/wobble/index.html", "presentation_rect": [0, 0, 320, 160]},
        "device_width": 340,
        "device_height": 180,
        "id": "cmd1",
    })

    assert result["sent"] is True
    assert result["loaded"] is True
    assert app.browser.loaded == []
    assert song.tracks[1].devices[-1].name == "AgentM4L_audio_effect_Wobble"
    command_path = module._temp_file("agent_m4l_Wobble.json")
    recovery_path = "%s.recovery.json" % command_path
    assert '"instance_id":"Wobble"' in written[command_path]
    assert '"cycle~ 110"' in written[command_path]
    assert '"device_width":340' in written[command_path]
    assert '"device_height":180' in written[command_path]
    assert '"webui":{"html_path":"/tmp/wobble/index.html"' in written[command_path]
    assert json.loads(written[recovery_path])["patch"]["objects"][0]["id"] == "osc"
    assert sent[0][1] == ("127.0.0.1", bridge._agent_m4l_port("Wobble"))
    assert b"/agent_m4l" in sent[0][0]


def test_agent_m4l_device_skips_udp_when_update_payload_is_too_large(monkeypatch):
    module, _app = load_bridge_module(monkeypatch)
    bridge = object.__new__(module.AbletonLiveMCP)
    song = FakeSong()
    bridge._objects = {}
    bridge._listeners = {}
    bridge._events = []
    bridge.song = lambda: song
    written = {}

    class FakeSocket:
        def __init__(self, *_args):
            raise AssertionError("large Agent M4L updates should not open UDP sockets")

    class FakeFile:
        def __init__(self, path, mode):
            written["path"] = path
            written["mode"] = mode

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def write(self, value):
            written["value"] = written.get("value", "") + value
            return len(value)

    monkeypatch.setattr(socket, "socket", FakeSocket)
    monkeypatch.setattr(module, "open", lambda path, mode: FakeFile(path, mode), raising=False)

    result = bridge._rpc_agent_m4l_device({
        "role": "instrument",
        "instance_id": "Huge UI",
        "load": False,
        "patch": {"objects": [{"id": "big", "text": "comment " + ("x" * 70000)}]},
        "id": "huge1",
    })

    assert result["sent"] is False
    assert result["udp_skipped"] is True
    assert result["udp_bytes"] > module.AGENT_M4L_MAX_UDP_BYTES
    assert '"comment ' in written["value"]


def test_agent_m4l_device_value_update_does_not_require_patch_or_load(monkeypatch):
    module, _app = load_bridge_module(monkeypatch)
    bridge = object.__new__(module.AbletonLiveMCP)
    song = FakeSong()
    bridge._objects = {}
    bridge._listeners = {}
    bridge._events = []
    bridge.song = lambda: song
    written = {}

    class FakeFile:
        def __init__(self, path, mode):
            written["path"] = path
            written["mode"] = mode

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def write(self, value):
            written["value"] = written.get("value", "") + value
            return len(value)

    monkeypatch.setattr(module, "open", lambda path, mode: FakeFile(path, mode), raising=False)

    result = bridge._rpc_agent_m4l_device({
        "role": "audio_effect",
        "instance_id": "Wobble",
        "command": "set",
        "values": [{"id": "cutoff", "value": 0.72}],
        "udp": False,
        "load": False,
        "id": "set1",
    })

    assert result["command"] == "set"
    assert result["sent"] is False
    assert result["loaded"] is False
    assert result["command_file_written"] is True
    assert '"values":[{"id":"cutoff","value":0.72}]' in written["value"]


def test_agent_m4l_device_triggers_hidden_poll_parameter(monkeypatch):
    module, _app = load_bridge_module(monkeypatch)
    bridge = object.__new__(module.AbletonLiveMCP)
    song = FakeSong()
    bridge._objects = {}
    bridge._listeners = {}
    bridge._events = []
    bridge.song = lambda: song
    device = FakeDevice()
    device.name = "AgentM4L_instrument_Dynamic_Poller_Probe"
    device.class_name = "MxDeviceInstrument"
    poll = FakeParameter("command-trigger", 0.0, 0.0, 1.0)
    device.parameters.append(poll)
    song.tracks[1].devices.append(device)
    written = {}

    class FakeFile:
        def __init__(self, path, mode):
            written["path"] = path
            written["mode"] = mode

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def write(self, value):
            written["value"] = written.get("value", "") + value
            return len(value)

    monkeypatch.setattr(module, "open", lambda path, mode: FakeFile(path, mode), raising=False)

    result = bridge._rpc_agent_m4l_device({
        "role": "instrument",
        "instance_id": "Dynamic Poller Probe",
        "device_name": "AgentM4L_instrument_Dynamic_Poller_Probe",
        "command": "set",
        "values": [{"id": "native_value", "value": 0.72}],
        "target_track": {"path": "live_set tracks 1"},
        "udp": False,
        "load": False,
        "id": "set-poll",
    })

    assert result["triggered"] is True
    assert poll.value == 1.0
    assert '"native_value"' in written["value"]


def test_agent_m4l_value_update_writes_file_with_recovery_patch(monkeypatch):
    module, _app = load_bridge_module(monkeypatch)
    bridge = object.__new__(module.AbletonLiveMCP)
    song = FakeSong()
    bridge._objects = {}
    bridge._listeners = {}
    bridge._events = []
    bridge.song = lambda: song
    sent = []
    stored = {
        module._temp_file("agent_m4l_Wobble.json"): json.dumps({
            "id": "patch1",
            "command": "update",
            "patch": {"objects": [{"id": "cutoff", "text": "flonum"}], "connections": []},
        })
    }

    class FakeSocket:
        def __init__(self, *_args):
            pass

        def sendto(self, payload, address):
            sent.append((payload, address))

        def close(self):
            pass

    class FakeFile:
        def __init__(self, path, mode):
            self.path = path
            self.mode = mode
            self.value = "" if "w" in mode else stored.get(path, "")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            if "w" in self.mode:
                stored[self.path] = self.value
            return False

        def read(self, *_args):
            return self.value

        def write(self, value):
            self.value += value
            return len(value)

    monkeypatch.setattr(socket, "socket", FakeSocket)
    monkeypatch.setattr(module, "open", lambda path, mode: FakeFile(path, mode), raising=False)

    result = bridge._rpc_agent_m4l_device({
        "role": "audio_effect",
        "instance_id": "Wobble",
        "command": "set",
        "values": [{"id": "cutoff", "value": 0.72}],
        "load": False,
        "id": "set1",
    })
    payload = json.loads(stored[module._temp_file("agent_m4l_Wobble.json")])

    assert result["sent"] is True
    assert result["command_file_written"] is True
    assert sent and sent[0][1] == ("127.0.0.1", bridge._agent_m4l_port("Wobble"))
    assert payload["command"] == "set"
    assert payload["values"] == [{"id": "cutoff", "value": 0.72}]
    assert payload["patch"]["objects"][0]["id"] == "cutoff"
    assert b'"values":[{"id":"cutoff","value":0.72}]' in sent[0][0]
    assert b'"patch"' not in sent[0][0]


def test_agent_m4l_recovery_patch_preserves_top_level_bounds(monkeypatch):
    module, _app = load_bridge_module(monkeypatch)
    bridge = object.__new__(module.AbletonLiveMCP)
    stored = {
        module._temp_file("agent_m4l_Panels.json"): json.dumps({
            "objects": [{"id": "dial", "text": "flonum"}],
            "connections": [],
            "device_width": 720,
            "device_height": 260,
        })
    }

    class FakeFile:
        def __init__(self, path, mode):
            self.value = stored.get(path, "")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, *_args):
            return self.value

    monkeypatch.setattr(module, "open", lambda path, mode: FakeFile(path, mode), raising=False)

    patch = bridge._agent_m4l_recovery_patch(module._temp_file("agent_m4l_Panels.json"))

    assert patch["objects"][0]["id"] == "dial"
    assert patch["device_width"] == 720
    assert patch["device_height"] == 260


def test_agent_m4l_recovery_patch_falls_back_to_sidecar(monkeypatch):
    module, _app = load_bridge_module(monkeypatch)
    bridge = object.__new__(module.AbletonLiveMCP)
    command_path = module._temp_file("agent_m4l_Panels.json")
    stored = {
        command_path: json.dumps({"id": "status1", "command": "status", "patch": None}),
        "%s.recovery.json" % command_path: json.dumps({
            "patch": {"objects": [{"id": "dial", "text": "flonum"}], "connections": []},
        }),
    }

    class FakeFile:
        def __init__(self, path, mode):
            self.value = stored.get(path, "")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, *_args):
            return self.value

    monkeypatch.setattr(module, "open", lambda path, mode: FakeFile(path, mode), raising=False)

    patch = bridge._agent_m4l_recovery_patch(command_path)

    assert patch["objects"][0]["id"] == "dial"


def test_agent_m4l_device_top_level_webuis_trigger_update(monkeypatch):
    module, _app = load_bridge_module(monkeypatch)
    bridge = object.__new__(module.AbletonLiveMCP)
    song = FakeSong()
    bridge._objects = {}
    bridge._listeners = {}
    bridge._events = []
    bridge.song = lambda: song
    written = {}

    class FakeFile:
        def __init__(self, path, mode):
            self.path = path
            self.mode = mode
            self.value = ""

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            if "w" in self.mode:
                written[self.path] = self.value
            return False

        def write(self, value):
            self.value += value
            return len(value)

    monkeypatch.setattr(module, "open", lambda path, mode: FakeFile(path, mode), raising=False)

    result = bridge._rpc_agent_m4l_device({
        "role": "audio_effect",
        "instance_id": "Panels",
        "webuis": [
            {"id": "a", "object": "jbrowser~", "html_path": "/state/a.html"},
            {"id": "b", "object": "jweb", "html_path": "/state/b.html"},
        ],
        "udp": False,
        "load": False,
    })
    command_path = module._temp_file("agent_m4l_Panels.json")
    payload = json.loads(written[command_path])

    assert result["command"] == "update"
    assert payload["patch"]["webuis"][0]["object"] == "jbrowser~"
    assert payload["webuis"][1]["html_path"] == "/state/b.html"
    assert json.loads(written["%s.recovery.json" % command_path])["patch"]["webuis"][0]["id"] == "a"


def test_agent_m4l_device_returns_command_id_without_blocking_for_status(monkeypatch, tmp_path):
    module, _app = load_bridge_module(monkeypatch)
    bridge = object.__new__(module.AbletonLiveMCP)
    song = FakeSong()
    bridge._objects = {}
    bridge._listeners = {}
    bridge._events = []
    bridge.song = lambda: song
    command_file = tmp_path / "command.json"
    status_file = tmp_path / "status.json"
    status_file.write_text('{"event":"old"}', encoding="utf-8")
    before = status_file.stat().st_mtime

    class FakeSocket:
        def __init__(self, *_args):
            pass

        def sendto(self, _payload, _address):
            status_file.write_text('{"event":"set","command_id":"set1","dynamic_objects":3,"webuis":1}', encoding="utf-8")
            module.os.utime(str(status_file), (before + 1.0, before + 1.0))

        def close(self):
            pass

    monkeypatch.setattr(socket, "socket", FakeSocket)

    result = bridge._rpc_agent_m4l_device({
        "role": "audio_effect",
        "instance_id": "Wobble",
        "command_file": str(command_file),
        "status_file": str(status_file),
        "command": "set",
        "values": [{"id": "cutoff", "value": 0.72}],
        "load": False,
        "id": "set1",
    })

    assert status_file.stat().st_mtime >= before
    assert result["command_id"] == "set1"
    assert "status" not in result


def test_agent_m4l_generated_command_id_includes_values(monkeypatch):
    module, _app = load_bridge_module(monkeypatch)
    bridge = object.__new__(module.AbletonLiveMCP)
    song = FakeSong()
    bridge._objects = {}
    bridge._listeners = {}
    bridge._events = []
    bridge.song = lambda: song
    written = []

    class FakeFile:
        def __init__(self, _path, _mode):
            self.value = ""

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            written.append(self.value)
            return False

        def write(self, value):
            self.value += value
            return len(value)

    monkeypatch.setattr(module, "open", lambda path, mode: FakeFile(path, mode), raising=False)

    first = bridge._rpc_agent_m4l_device({
        "role": "audio_effect",
        "instance_id": "Wobble",
        "command": "set",
        "values": [{"id": "cutoff", "value": 0.25}],
        "udp": False,
        "load": False,
    })
    first_payload = json.loads(written[-1])
    second = bridge._rpc_agent_m4l_device({
        "role": "audio_effect",
        "instance_id": "Wobble",
        "command": "set",
        "values": [{"id": "cutoff", "value": 0.75}],
        "udp": False,
        "load": False,
    })
    second_payload = json.loads(written[-1])

    assert first["command"] == second["command"] == "set"
    assert first_payload["id"] != second_payload["id"]


def test_agent_m4l_device_falls_back_to_track_insert_device_when_browser_is_stale(monkeypatch):
    bridge, song, app = make_bridge(monkeypatch)
    app.browser.user_library = FakeBrowserItem("User Library", folder=True, children=[])

    result = bridge._rpc_agent_m4l_device({
        "role": "audio_effect",
        "instance_id": "Fresh",
        "device_name": "AgentM4L_audio_effect_Fresh",
        "target_track": {"path": "live_set tracks 1"},
        "patch": {"objects": []},
        "udp": False,
        "id": "fresh1",
    })

    assert result["loaded"] is True
    assert app.browser.loaded == []
    assert song.tracks[1].devices[-1].name == "AgentM4L_audio_effect_Fresh"


def test_agent_m4l_device_prefers_track_insert_device_before_browser_load(monkeypatch):
    bridge, song, app = make_bridge(monkeypatch)
    device_name = "AgentM4L_instrument_Prism_Loom_Keys"
    item = FakeBrowserItem(device_name, loadable=True, device=True)
    bridge._find_browser_item_named = lambda name: item if name == device_name else None

    result = bridge._rpc_agent_m4l_device({
        "role": "instrument",
        "instance_id": "prism_loom_keys_001",
        "name": "Prism Loom Keys",
        "target_track": {"path": "live_set tracks 1"},
        "patch": {"objects": []},
        "udp": False,
        "id": "prism1",
    })

    assert result["loaded"] is True
    assert app.browser.loaded == []
    assert song.tracks[1].devices[-1].name == device_name
    assert song.tracks[1].devices[-1].class_name == "MxDeviceInstrument"


def test_agent_m4l_device_renames_new_device_inserted_before_existing_chain(monkeypatch):
    bridge, song, app = make_bridge(monkeypatch)
    target = song.tracks[1]
    instrument = FakeDevice()
    instrument.name = "Existing Instrument"
    instrument.class_name = "MxDeviceInstrument"
    audio_effect = FakeDevice()
    audio_effect.name = "Existing Audio FX"
    audio_effect.class_name = "MxDeviceAudioEffect"
    target.devices = FakeVector([instrument, audio_effect])
    device_name = "AgentM4L_midi_effect_Pulse_Router_MIDI"
    app.browser.load_item = lambda _item: (_ for _ in ()).throw(AssertionError("browser.load_item should not be needed"))

    result = bridge._rpc_agent_m4l_device({
        "role": "midi_effect",
        "instance_id": "Pulse Router MIDI",
        "device_name": device_name,
        "target_track": {"path": "live_set tracks 1"},
        "patch": {"objects": []},
        "udp": False,
        "id": "midi1",
    })

    assert result["loaded"] is True
    assert app.browser.loaded == []
    assert [device.name for device in target.devices] == [
        device_name,
        "Existing Instrument",
        "Existing Audio FX",
    ]


def test_agent_m4l_device_name_match_requires_matching_role_when_available(monkeypatch):
    bridge, song, app = make_bridge(monkeypatch)
    target = song.tracks[1]
    device_name = "AgentM4L_midi_effect_Pulse_Router_MIDI"
    wrong_role = FakeDevice()
    wrong_role.name = device_name
    wrong_role.class_name = "MxDeviceAudioEffect"
    target.devices = FakeVector([wrong_role])

    result = bridge._rpc_agent_m4l_device({
        "role": "midi_effect",
        "instance_id": "Pulse Router MIDI",
        "device_name": device_name,
        "target_track": {"path": "live_set tracks 1"},
        "patch": {"objects": []},
        "udp": False,
        "id": "midi2",
    })

    assert result["loaded"] is True
    assert app.browser.loaded == []
    assert target.devices[0].name == device_name
    assert target.devices[0].class_name == "MxDeviceMidiEffect"
    assert target.devices[1].class_name == "MxDeviceAudioEffect"


def test_agent_m4l_device_name_uses_title_when_instance_is_stable(monkeypatch):
    bridge, _song, app = make_bridge(monkeypatch)
    device_name = "AgentM4L_instrument_Orbit_Glass_Synth"
    item = FakeBrowserItem(device_name, loadable=True, device=True)
    bridge._find_browser_item_named = lambda name: item if name == device_name else None

    result = bridge._rpc_agent_m4l_device({
        "role": "instrument",
        "instance_id": "orbit_glass_synth_001",
        "name": "Orbit Glass Synth",
        "target_track": {"path": "live_set tracks 1"},
        "patch": {"objects": []},
        "udp": False,
        "id": "orbit1",
    })

    assert result["device_name"] == device_name
    assert result["loaded"] is True
    assert app.browser.loaded == []
    assert _song.tracks[1].devices[-1].name == device_name


def test_agent_m4l_device_reports_load_error_without_dropping_command(monkeypatch):
    bridge, song, app = make_bridge(monkeypatch)
    app.browser.user_library = FakeBrowserItem("User Library", folder=True, children=[])

    def fail_insert(_device_name, _device_index=-1):
        raise RuntimeError("Device AgentM4L_audio_effect_Fresh not found.")

    song.tracks[1].insert_device = fail_insert
    result = bridge._rpc_agent_m4l_device({
        "role": "audio_effect",
        "instance_id": "Fresh",
        "device_name": "AgentM4L_audio_effect_Fresh",
        "target_track": {"path": "live_set tracks 1"},
        "patch": {"objects": []},
        "udp": False,
        "id": "fresh1",
    })

    assert result["sent"] is False
    assert result["loaded"] is False
    assert "not found" in result["load_error"]
    assert result["command_id"] == "fresh1"


def test_transport_tool_seeks_and_retries_play(monkeypatch):
    bridge, song, _app = make_bridge(monkeypatch)
    calls = []

    def start_once_then_later():
        calls.append("start")
        if len(calls) > 1:
            song.is_playing = True

    song.current_song_time = 12.0
    song.start_playing = start_once_then_later
    song.continue_playing = lambda: calls.append("continue")

    result = bridge._rpc_transport({"action": "play", "time": 2.0})

    assert result == {"playing": True, "time": 2.0}
    assert calls == ["start", "continue", "start"]


def test_set_summary_compacts_existing_project_state(monkeypatch):
    bridge, _song, _app = make_bridge(monkeypatch)
    result = bridge._rpc_set_summary({"track_limit": 1, "clip_slot_limit": 1, "device_limit": 1, "arrangement_clip_limit": 1})
    assert result["tempo"] == 120.0
    assert len(result["set_signature"]) == 16
    assert result["scene_count"] == 1
    assert result["tracks"][0]["name"] == "Track 1"
    assert result["tracks"][0]["devices"][0]["name"] == "Compressor"
    assert result["tracks"][0]["clips"][0]["name"] == "Clip 1"
    assert result["tracks"][0]["arrangement_clip_count"] == 1
    assert result["tracks"][0]["arrangement_clips"][0]["name"] == "Arr Clip 1"
    assert result["tracks"][0]["clip_slots_scanned"] == 1
    assert result["tracks"][0]["clip_slots_truncated"] is True
    assert result["tracks"][-1] == {"truncated": True}
    assert result["return_tracks"][0]["name"] == "A-Reverb"
    assert result["master_track"]["name"] == "Main"

    filtered = bridge._rpc_set_summary({"track_query": "track 2", "include_return_tracks": False, "include_master_track": False})
    assert filtered["tracks_scanned"] == 2
    assert [track["name"] for track in filtered["tracks"]] == ["Track 2"]
    assert filtered["return_tracks"] == []


def test_expected_set_signature_blocks_stale_mutation(monkeypatch):
    bridge, song, _app = make_bridge(monkeypatch)
    signature = bridge._rpc_set_summary({})["set_signature"]

    assert bridge._run_on_main("exec", {"code": "result = {'ok': True}", "expected_set_signature": signature}) == {"ok": True}

    song.tracks[0].name = "User Changed Track"
    try:
        bridge._run_on_main("exec", {"code": "result = {'ok': True}", "expected_set_signature": signature})
    except RuntimeError as exc:
        assert "Set changed since last inspection" in str(exc)
    else:
        raise AssertionError("expected stale set signature error")


def test_clip_notes_can_be_listed_and_updated(monkeypatch):
    bridge, _song, _app = make_bridge(monkeypatch)
    notes = bridge._rpc_clip_notes({"ref": {"path": "live_set tracks 0 clip_slots 0 clip"}})
    assert notes["note_api"] == "extended"
    assert notes["note_count"] == 1
    assert notes["notes"][0]["velocity"] == 40.0

    updated = bridge._rpc_clip_update_notes({
        "ref": {"path": "live_set tracks 0 clip_slots 0 clip"},
        "updates": [{"note_id": 1, "velocity": 88.0}],
    })
    assert updated["note_api"] == "extended"
    assert updated["updated"] == 1
    assert updated["notes"][0]["velocity"] == 88.0
    notes = bridge._rpc_clip_notes({"ref": {"path": "live_set tracks 0 clip_slots 0 clip"}})
    assert notes["notes"][0]["velocity"] == 88.0


def test_clip_notes_refuses_legacy_note_api(monkeypatch):
    bridge, _song, _app = make_bridge(monkeypatch)
    monkeypatch.delattr(FakeClip, "get_all_notes_extended")

    try:
        bridge._rpc_clip_notes({"ref": {"path": "live_set tracks 0 clip_slots 0 clip"}})
    except RuntimeError as exc:
        assert "refusing legacy note API" in str(exc)
    else:
        raise AssertionError("expected legacy note API refusal")


def test_clip_update_notes_refuses_legacy_note_api(monkeypatch):
    bridge, _song, _app = make_bridge(monkeypatch)
    monkeypatch.delattr(FakeClip, "apply_note_modifications")

    try:
        bridge._rpc_clip_update_notes({
            "ref": {"path": "live_set tracks 0 clip_slots 0 clip"},
            "updates": [{"note_id": 1, "velocity": 88.0}],
        })
    except RuntimeError as exc:
        assert "refusing legacy note API" in str(exc)
    else:
        raise AssertionError("expected legacy note API refusal")


def test_exec_refuses_obsolete_note_api_code(monkeypatch):
    bridge, _song, _app = make_bridge(monkeypatch)

    try:
        bridge._rpc_exec({"code": "clip = song.tracks[0].clip_slots[0].clip\nclip.set_notes(())"})
    except RuntimeError as exc:
        assert "Refusing obsolete MIDI note API set_notes" in str(exc)
        assert "live_clip_add_notes" in str(exc)
    else:
        raise AssertionError("expected obsolete note API refusal")


def test_eval_refuses_obsolete_note_api_code(monkeypatch):
    bridge, _song, _app = make_bridge(monkeypatch)

    try:
        bridge._rpc_eval({"expr": "song.tracks[0].clip_slots[0].clip.remove_notes(0, 0, 1, 128)"})
    except RuntimeError as exc:
        assert "Refusing obsolete MIDI note API remove_notes" in str(exc)
    else:
        raise AssertionError("expected obsolete note API refusal")


def test_exec_can_explicitly_allow_obsolete_note_api_code(monkeypatch):
    bridge, _song, _app = make_bridge(monkeypatch)
    result = bridge._rpc_exec({
        "code": "result = 'remove_notes(compatibility probe only)'",
        "allow_legacy_note_api": True,
    })
    assert result == "remove_notes(compatibility probe only)"


def test_clip_add_notes_accepts_json_note_specs(monkeypatch):
    bridge, _song, _app = make_bridge(monkeypatch)
    result = bridge._rpc_clip_add_notes({
        "ref": {"path": "live_set tracks 0 clip_slots 0 clip"},
        "clear": True,
        "notes": [
            {"pitch": 64, "start_time": 0.0, "duration": 0.5, "velocity": 72},
            {"pitch": 67, "start_time": 0.5, "duration": 0.5, "velocity": 80, "mute": True},
        ],
    })

    assert result["added"] == 2
    assert result["note_api"] == "extended"
    assert result["note_count"] == 2
    notes = bridge._rpc_clip_notes({"ref": {"path": "live_set tracks 0 clip_slots 0 clip"}})
    assert [note["pitch"] for note in notes["notes"]] == [64, 67]
    assert notes["notes"][1]["mute"] is True


def test_clip_add_notes_can_create_slot_clip_and_launch(monkeypatch):
    bridge, song, _app = make_bridge(monkeypatch)
    slot = song.tracks[1].clip_slots[0]
    assert slot.has_clip is False

    result = bridge._rpc_clip_add_notes({
        "ref": {"path": "live_set tracks 1 clip_slots 0"},
        "create_clip_length": 4.0,
        "clip_name": "Generated Pattern",
        "loop_start": 0.0,
        "loop_end": 4.0,
        "fire": True,
        "notes": [
            {"pitch": 60, "start_time": 0.0, "duration": 0.5, "velocity": 90},
            {"pitch": 67, "start_time": 0.5, "duration": 0.5, "velocity": 76},
        ],
    })

    assert result["created_clip"] is True
    assert result["launched"] is True
    assert result["added"] == 2
    assert result["clip"]["name"] == "Generated Pattern"
    assert slot.has_clip is True
    assert slot.fired is True
    assert slot.clip.loop_end == 4.0
    notes = bridge._rpc_clip_notes({"ref": {"path": "live_set tracks 1 clip_slots 0 clip"}})
    assert [note["pitch"] for note in notes["notes"]] == [60, 67]


def test_clip_add_notes_detects_empty_slot_without_touching_clip(monkeypatch):
    bridge, _song, _app = make_bridge(monkeypatch)

    class EmptySlot:
        has_clip = False

        @property
        def clip(self):
            if not self.has_clip:
                raise RuntimeError("clip is unavailable until create_clip")
            return self._clip

        def create_clip(self, length):
            self._clip = FakeClip("Created Clip", end_time=float(length))
            self._clip._notes = []
            self.has_clip = True

    slot = EmptySlot()
    monkeypatch.setattr(bridge, "_resolve", lambda _ref: slot)

    result = bridge._rpc_clip_add_notes({
        "ref": {"path": "live_set tracks 0 clip_slots 0"},
        "create_clip_length": 4.0,
        "notes": [{"pitch": 72, "start_time": 0.0, "duration": 0.5, "velocity": 90}],
    })

    assert result["created_clip"] is True
    assert result["note_api"] == "extended"
    assert slot.has_clip is True


def test_clip_add_notes_can_replace_slot_clip(monkeypatch):
    bridge, song, _app = make_bridge(monkeypatch)
    slot = song.tracks[0].clip_slots[0]
    old_clip = slot.clip
    assert old_clip.get_all_notes_extended()

    result = bridge._rpc_clip_add_notes({
        "ref": {"path": "live_set tracks 0 clip_slots 0"},
        "replace_existing_clip": True,
        "create_clip_length": 8.0,
        "clip_name": "Fresh Pattern",
        "notes": [{"pitch": 72, "start_time": 0.0, "duration": 0.5, "velocity": 90}],
    })

    assert result["created_clip"] is True
    assert result["replaced_clip"] is True
    assert result["legacy_note_api"] is False
    assert result["note_api"] == "extended"
    assert result["note_count"] == 1
    assert slot.deleted is True
    assert slot.clip is not old_clip
    assert slot.clip.name == "Fresh Pattern"
    assert slot.clip.length == 8.0
    notes = bridge._rpc_clip_notes({"ref": {"path": "live_set tracks 0 clip_slots 0 clip"}})
    assert [note["pitch"] for note in notes["notes"]] == [72]


def test_clip_add_notes_replace_requires_slot_ref(monkeypatch):
    bridge, _song, _app = make_bridge(monkeypatch)

    try:
        bridge._rpc_clip_add_notes({
            "ref": {"path": "live_set tracks 0 clip_slots 0 clip"},
            "replace_existing_clip": True,
            "create_clip_length": 4.0,
            "notes": [{"pitch": 72, "start_time": 0.0, "duration": 0.5, "velocity": 90}],
        })
    except ValueError as exc:
        assert "clip slot ref" in str(exc)
    else:
        raise AssertionError("expected clip slot ref error")


def test_clip_add_notes_requires_length_for_empty_slot(monkeypatch):
    bridge, _song, _app = make_bridge(monkeypatch)

    try:
        bridge._rpc_clip_add_notes({
            "ref": {"path": "live_set tracks 1 clip_slots 0"},
            "notes": [{"pitch": 60, "start_time": 0.0, "duration": 0.5, "velocity": 90}],
        })
    except ValueError as exc:
        assert "create_clip_length" in str(exc)
    else:
        raise AssertionError("expected create_clip_length error")


def test_clip_add_notes_refuses_legacy_clear_by_default(monkeypatch):
    bridge, song, _app = make_bridge(monkeypatch)
    clip = song.tracks[0].clip_slots[0].clip

    def missing_extended(*_args, **_kwargs):
        raise TypeError("legacy only")

    clip.remove_notes_extended = missing_extended

    try:
        bridge._rpc_clip_add_notes({
            "ref": {"path": "live_set tracks 0 clip_slots 0 clip"},
            "clear": True,
            "notes": [{"pitch": 64, "start_time": 0.0, "duration": 0.5, "velocity": 72}],
        })
    except RuntimeError as exc:
        assert "allow_legacy_note_api" in str(exc)
    else:
        raise AssertionError("expected legacy note API refusal")
    assert clip.legacy_remove_notes_called is False


def test_clip_add_notes_allows_legacy_clear_when_explicit(monkeypatch):
    bridge, song, _app = make_bridge(monkeypatch)
    clip = song.tracks[0].clip_slots[0].clip

    def missing_extended(*_args, **_kwargs):
        raise TypeError("legacy only")

    clip.remove_notes_extended = missing_extended

    result = bridge._rpc_clip_add_notes({
        "ref": {"path": "live_set tracks 0 clip_slots 0 clip"},
        "clear": True,
        "allow_legacy_note_api": True,
        "notes": [{"pitch": 64, "start_time": 0.0, "duration": 0.5, "velocity": 72}],
    })

    assert result["legacy_note_api"] is True
    assert result["note_api"] == "legacy"
    assert clip.legacy_remove_notes_called is True


def test_clip_duplicate_to_arrangement_uses_clip_object(monkeypatch):
    bridge, song, _app = make_bridge(monkeypatch)
    before = len(song.tracks[0].arrangement_clips)

    result = bridge._rpc_clip_duplicate_to_arrangement({
        "track": {"path": "live_set tracks 0"},
        "clip": {"path": "live_set tracks 0 clip_slots 0 clip"},
        "destination_time": 16.0,
    })

    assert result["destination_time"] == 16.0
    assert len(song.tracks[0].arrangement_clips) == before + 1
    assert song.tracks[0].arrangement_clips[-1].start_time == 16.0


def test_track_create_audio_clip_imports_local_file_path(monkeypatch, tmp_path):
    bridge, song, _app = make_bridge(monkeypatch)
    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"fake")

    result = bridge._rpc_track_create_audio_clip({
        "ref": {"path": "live_set tracks 1"},
        "file_path": str(audio),
        "destination_time": 32.0,
        "name": "Audio Vocal Hook",
    })

    clip = song.tracks[1].arrangement_clips[-1]
    assert result["clip"]["name"] == "Audio Vocal Hook"
    assert clip.is_audio_clip is True
    assert clip.start_time == 32.0


def test_track_insert_device_uses_device_name_signature(monkeypatch):
    bridge, song, _app = make_bridge(monkeypatch)
    result = bridge._rpc_track_insert_device({
        "ref": {"path": "live_set tracks 1"},
        "device_name": "EQ Eight",
        "device_index": -1,
    })

    assert result["inserted"] is True
    assert song.tracks[1].devices[-1].name == "EQ Eight"


def test_clip_envelope_can_be_inspected_inserted_and_cleared(monkeypatch):
    bridge, song, _app = make_bridge(monkeypatch)
    parameter = song.tracks[0].devices[0].parameters[1]
    parameter_ref = {"id": bridge._parameter_summary(parameter)["id"]}
    clip_ref = {"path": "live_set tracks 0 clip_slots 0 clip"}

    missing = bridge._rpc_clip_envelope({"ref": clip_ref, "parameter": parameter_ref})
    assert missing["has_envelope"] is False
    assert missing["events"] == []

    updated = bridge._rpc_clip_envelope({
        "ref": clip_ref,
        "parameter": parameter_ref,
        "create": True,
        "delete_range": {"start_time": 0.0, "end_time": 4.0},
        "insert_steps": [{"time": 0.0, "duration": 1.0, "value": 0.5}],
    })
    assert updated["has_envelope"] is True
    assert updated["event_count"] == 2
    assert updated["events"][0] == {"time": 0.0, "value": 0.5}
    assert updated["parameter"]["name"] == "Threshold"

    cleared = bridge._rpc_clip_envelope({"ref": clip_ref, "parameter": parameter_ref, "clear": True})
    assert cleared["has_envelope"] is False


def test_clip_velocity_envelope_maps_note_velocities(monkeypatch):
    bridge, song, _app = make_bridge(monkeypatch)
    clip = song.tracks[0].clip_slots[0].clip
    clip._notes = [
        types.SimpleNamespace(note_id=1, pitch=60, start_time=0.0, duration=0.5, velocity=40.0, mute=False, probability=1.0, velocity_deviation=0.0, release_velocity=64.0),
        types.SimpleNamespace(note_id=2, pitch=64, start_time=1.0, duration=0.5, velocity=80.0, mute=False, probability=1.0, velocity_deviation=0.0, release_velocity=64.0),
        types.SimpleNamespace(note_id=3, pitch=67, start_time=2.0, duration=0.5, velocity=120.0, mute=False, probability=1.0, velocity_deviation=0.0, release_velocity=64.0),
    ]
    parameter = song.tracks[0].devices[0].parameters[1]
    parameter_ref = {"id": bridge._parameter_summary(parameter)["id"]}
    result = bridge._rpc_clip_velocity_envelope({
        "ref": {"path": "live_set tracks 0 clip_slots 0 clip"},
        "parameter": parameter_ref,
        "min_value": 0.0,
        "max_value": 1.0,
        "start_time": 0.0,
        "end_time": 4.0,
    })
    assert result["notes_mapped"] == 3
    assert result["event_count"] == 6
    assert [round(event["value"], 3) for event in result["events"][::2]] == [0.315, 0.63, 0.945]


def test_clip_warp_markers_can_be_inspected_and_edited(monkeypatch):
    bridge, _song, _app = make_bridge(monkeypatch)
    clip_ref = {"path": "live_set tracks 0 clip_slots 0 clip"}

    initial = bridge._rpc_clip_warp_markers({"ref": clip_ref})
    assert initial["warping"] is True
    assert initial["marker_count"] == 2

    updated = bridge._rpc_clip_warp_markers({
        "ref": clip_ref,
        "warping": True,
        "warp_mode": 1,
        "move_markers": [{"beat_time": 4.0, "beat_time_delta": 0.25}],
        "add_markers": [{"sample_time": 1.0, "beat_time": 1.0}],
    })
    assert updated["warp_mode"] == 1
    assert {"beat_time": 4.25, "sample_time": 2.0} in updated["markers"]
    assert {"beat_time": 1.0, "sample_time": 1.0} in updated["markers"]

    removed = bridge._rpc_clip_warp_markers({"ref": clip_ref, "remove_beat_times": [1.0]})
    assert {"beat_time": 1.0, "sample_time": 1.0} not in removed["markers"]


def test_device_parameters_are_compact_and_addressable(monkeypatch):
    bridge, _song, _app = make_bridge(monkeypatch)
    result = bridge._rpc_device_parameters({"ref": {"path": "live_set tracks 0 devices 0"}, "query": "threshold"})
    assert len(result) == 1
    assert result[0]["name"] == "Threshold"
    assert result[0]["display"] == "0.85 display"
    assert "id" in result[0]
    param = bridge._resolve({"id": result[0]["id"]})
    assert param.name == "Threshold"

    limited = bridge._rpc_device_parameters({"ref": {"path": "live_set tracks 0 devices 0"}, "query": "threshold", "limit": 5})
    assert limited[-1].get("truncated") is None

    truncated = bridge._rpc_device_parameters({"ref": {"path": "live_set tracks 0 devices 0"}, "limit": 1})
    assert truncated[-1] == {"truncated": True}


def test_parameter_set_validates_and_coerces_values(monkeypatch):
    bridge, song, _app = make_bridge(monkeypatch)
    threshold = song.tracks[0].devices[0].parameters[1]
    threshold_ref = {"id": bridge._parameter_summary(threshold)["id"]}

    changed = bridge._rpc_parameter_set({"ref": threshold_ref, "value": 0.25})
    assert changed["before"]["value"] == 0.85
    assert changed["parameter"]["value"] == 0.25
    assert changed["applied_value"] == 0.25
    assert changed["changed"] is True

    try:
        bridge._rpc_parameter_set({"ref": threshold_ref, "value": 2.0})
    except ValueError as exc:
        assert "above parameter max" in str(exc)
    else:
        raise AssertionError("expected max validation error")

    coerced = bridge._rpc_parameter_set({"ref": threshold_ref, "value": 2.0, "coerce": True})
    assert coerced["parameter"]["value"] == 1.0
    assert coerced["changed"] is True

    device_on = song.tracks[0].devices[0].parameters[0]
    device_on_ref = {"id": bridge._parameter_summary(device_on)["id"]}
    try:
        bridge._rpc_parameter_set({"ref": device_on_ref, "value": 0.6})
    except ValueError as exc:
        assert "quantized parameter" in str(exc)
    else:
        raise AssertionError("expected quantized validation error")
    assert bridge._rpc_parameter_set({"ref": device_on_ref, "value": 0.6, "coerce": True})["parameter"]["value"] == 1


def test_app_browser_path_roots_and_stale_id_errors(monkeypatch):
    bridge, _song, _app = make_bridge(monkeypatch)
    assert bridge._resolve_path("app").get_version_string() == "test-live-version"
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


def test_ping_reports_running_remote_script_hash(monkeypatch):
    bridge, _song, _app = make_bridge(monkeypatch)

    result = bridge._rpc_ping({})

    assert result["ok"] is True
    assert result["remote_script"]["path"].endswith("bridge.py")
    assert len(result["remote_script"]["bridge_sha256"]) == 64


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


def test_batch_get_properties_are_not_double_encoded(monkeypatch):
    bridge, _song, _app = make_bridge(monkeypatch)
    result = bridge._run_on_main("batch", {
        "max_depth": 3,
        "operations": [
            {"method": "get", "params": {
                "ref": {"path": "live_set"},
                "properties": ["tempo", "signature_numerator", "signature_denominator"],
            }},
        ],
    })

    assert result[0]["ok"] is True
    assert result[0]["result"]["properties"] == {
        "tempo": 120.0,
        "signature_numerator": 4,
        "signature_denominator": 4,
    }
    assert result[0]["result"]["children"] == {}


def test_remote_script_read_line_preserves_buffered_requests(monkeypatch):
    bridge, _song, _app = make_bridge(monkeypatch)

    class FakeSocket:
        def __init__(self):
            self.chunks = [b'{"id":1}\n{"id":2}', b'\n{"id":3}\n']

        def recv(self, _size):
            return self.chunks.pop(0) if self.chunks else b""

    sock = FakeSocket()
    line, buffer = bridge._read_line(sock, b"")
    assert line == b'{"id":1}'
    line, buffer = bridge._read_line(sock, buffer)
    assert line == b'{"id":2}'
    line, buffer = bridge._read_line(sock, buffer)
    assert line == b'{"id":3}'
    assert buffer == b""


def test_run_on_main_abandons_request_that_has_not_started(monkeypatch):
    bridge, _song, _app = make_bridge(monkeypatch)
    callbacks = []
    invoked = []
    bridge._main_thread_id = -1
    bridge.schedule_message = lambda _delay, callback: callbacks.append(callback)
    bridge._rpc_marker = lambda _params: invoked.append(True)

    try:
        bridge._run_on_main("marker", {"timeout": 0.001, "strict_timeout": True})
    except RuntimeError as exc:
        assert "Timed out waiting for Live main thread" in str(exc)
    else:
        raise AssertionError("expected timeout")

    assert invoked == []
    callbacks[0]()
    assert invoked == []


def test_bridge_status_does_not_schedule_on_main_thread(monkeypatch):
    bridge, _song, _app = make_bridge(monkeypatch)
    bridge._main_thread_id = -1
    bridge.schedule_message = lambda _delay, _callback: (_ for _ in ()).throw(AssertionError("scheduled main thread"))

    response = bridge._dispatch({"jsonrpc": "2.0", "id": 1, "method": "bridge_status", "params": {}})

    assert response["result"]["ok"] is True
    assert response["result"]["server_thread_responsive"] is True
    assert response["result"]["main_thread"]["timeouts"] == 0


def test_run_on_main_circuit_breaker_after_timeout(monkeypatch):
    bridge, _song, _app = make_bridge(monkeypatch)
    callbacks = []
    bridge._main_thread_id = -1
    bridge.schedule_message = lambda _delay, callback: callbacks.append(callback)
    bridge._rpc_marker = lambda _params: True

    try:
        bridge._run_on_main("marker", {"timeout": 0.001, "strict_timeout": True})
    except RuntimeError as exc:
        assert "Timed out waiting for Live main thread" in str(exc)
    else:
        raise AssertionError("expected timeout")

    try:
        bridge._run_on_main("marker", {"timeout": 0.001, "strict_timeout": True})
    except RuntimeError as exc:
        assert "stall cooldown" in str(exc)
        assert "refusing to enqueue marker" in str(exc)
    else:
        raise AssertionError("expected circuit breaker refusal")

    assert len(callbacks) == 1


def test_run_on_main_clamps_short_non_strict_timeouts(monkeypatch):
    bridge, _song, _app = make_bridge(monkeypatch)

    assert bridge._main_thread_timeout({"timeout": 0.001}) == 30.0
    assert bridge._main_thread_timeout({"timeout": 0.001, "strict_timeout": True}) == 0.001
    assert bridge._main_thread_timeout({"timeout": 45}) == 45.0


def test_handle_client_closes_idle_timeout_without_stale_json_error(monkeypatch):
    bridge, _song, _app = make_bridge(monkeypatch)
    sent = []
    bridge._handler_slots = threading.BoundedSemaphore(16)

    class IdleSocket:
        def settimeout(self, _timeout):
            pass

        def recv(self, _size):
            raise socket.timeout("timed out")

        def sendall(self, payload):
            sent.append(payload)

        def close(self):
            pass

    bridge._handle_client(IdleSocket())

    assert sent == []


def test_handle_client_serves_bridge_status_when_handler_slots_are_saturated(monkeypatch):
    bridge, _song, _app = make_bridge(monkeypatch)
    bridge._handler_slots = threading.BoundedSemaphore(1)
    assert bridge._handler_slots.acquire(False)
    sent = []

    class StatusSocket:
        def __init__(self):
            self.chunks = [
                b'{"jsonrpc":"2.0","id":7,"method":"bridge_status","params":{}}\n',
                b"",
            ]

        def settimeout(self, _timeout):
            pass

        def recv(self, _size):
            return self.chunks.pop(0)

        def sendall(self, payload):
            sent.append(payload)

        def close(self):
            pass

    try:
        bridge._handle_client(StatusSocket())
    finally:
        bridge._handler_slots.release()

    response = json.loads(sent[0].decode("utf-8"))
    assert response["id"] == 7
    assert response["result"]["ok"] is True
    assert response["result"]["server_thread_responsive"] is True


def test_handle_client_rejects_main_thread_request_when_handler_slots_are_saturated(monkeypatch):
    bridge, _song, _app = make_bridge(monkeypatch)
    bridge._handler_slots = threading.BoundedSemaphore(1)
    assert bridge._handler_slots.acquire(False)
    sent = []

    class PingSocket:
        def __init__(self):
            self.chunks = [
                b'{"jsonrpc":"2.0","id":8,"method":"ping","params":{}}\n',
                b"",
            ]

        def settimeout(self, _timeout):
            pass

        def recv(self, _size):
            return self.chunks.pop(0)

        def sendall(self, payload):
            sent.append(payload)

        def close(self):
            pass

    try:
        bridge._handle_client(PingSocket())
    finally:
        bridge._handler_slots.release()

    response = json.loads(sent[0].decode("utf-8"))
    assert response["id"] == 8
    assert response["error"]["message"] == "Too many concurrent Ableton MCP requests"


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

    preview = bridge._rpc_browser_preview({"item": {"id": drums["results"][0]["id"]}})
    assert preview["previewing"] is True
    assert app.browser.previewed == ["505 Cowbell Hi.flac"]
    assert bridge._rpc_browser_preview({"stop": True}) == {"previewing": False}
    assert app.browser.stopped_preview is True


def test_browser_load_can_reresolve_stale_id_by_uri_or_path(monkeypatch):
    bridge, song, app = make_bridge(monkeypatch)
    result = bridge._rpc_browser_search({"query": "cowbell", "roots": ["drums"], "limit": 1, "max_depth": 5})
    item = result["results"][0]
    bridge._objects.clear()

    bridge._rpc_browser_load({"item": item, "target_track": {"path": "live_set tracks 0"}})

    assert app.browser.loaded == ["505 Cowbell Hi.flac"]
    assert song.view.selected_track.name == "Track 1"


def test_browser_capabilities_report_roots_and_semantic_attrs(monkeypatch):
    bridge, _song, _app = make_bridge(monkeypatch)
    result = bridge._rpc_browser_capabilities({})
    assert "plugins" in {root["name"] for root in result["roots"]}
    assert result["semantic_search_exposed"] is False
    assert "instruments" in result["browser_attrs"]


def test_browser_search_can_stop_on_limit(monkeypatch):
    bridge, _song, _app = make_bridge(monkeypatch)
    result = bridge._rpc_browser_search({
        "query": "",
        "roots": ["drums"],
        "limit": 1,
        "include_folders": True,
        "loadable_only": False,
        "stop_on_limit": True,
        "stop_score": 1,
    })
    assert len(result["results"]) == 1
    assert result["visited"] == 1
    assert result["truncated"] is True


def test_browser_stop_on_limit_waits_for_good_score(monkeypatch):
    bridge, _song, app = make_bridge(monkeypatch)
    app.browser.instruments = FakeBrowserItem("instruments", folder=True, children=[
        FakeBrowserItem("Folder", folder=True, children=[
            FakeBrowserItem("Secret Kick.adg", loadable=True),
            FakeBrowserItem("Operator Kick.adv", loadable=True),
        ]),
    ])
    result = bridge._rpc_browser_search({
        "query": "operator kick",
        "roots": ["instruments"],
        "limit": 1,
        "stop_on_limit": True,
    })
    assert result["results"][0]["name"] == "Operator Kick.adv"


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
