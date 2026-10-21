import json

from bridge import AbletonBridgeError
from server import make_server


class MeterBridge:
    """Plays a scripted timeline: each exec of the sampling code advances one row."""

    def __init__(self, timeline, playing=True, soloed=()):
        self.timeline = list(timeline)
        self.soloed = list(soloed)
        self.playing = playing
        self.calls = []

    def request(self, method, params):
        self.calls.append(params.get("code") or params.get("expr"))
        if method == "meter_capture":
            raise AbletonBridgeError("'AbletonLiveMCP' object has no attribute '_rpc_meter_capture'")
        code = params.get("code", "")
        if method == "eval":
            return self.soloed if "solo" in params["expr"] else (self.timeline[0][0] if self.timeline else 0.0)
        if "start_playing" in code:
            self.playing = True
            return None
        if "stop_playing" in code:
            self.playing = False
            return None
        if "current_song_time =" in code:
            return None
        song_time, master, tracks = self.timeline.pop(0) if len(self.timeline) > 1 else self.timeline[0]
        return [song_time, self.playing, master, tracks]


def _call(server, arguments):
    response = server.handle({
        "jsonrpc": "2.0", "id": 9, "method": "tools/call",
        "params": {"name": "live_meters", "arguments": arguments},
    })
    return response["result"]["structuredContent"]


TIMELINE = [
    (0.0, 0.2, [("Drums", 0.5), ("Pad", 0.4), ("Spare", 0.0)]),
    (4.0, 0.3, [("Drums", 0.6), ("Pad", 0.2), ("Spare", 0.0)]),
    (8.0, 0.9, [("Drums", 0.9), ("Pad", 0.1), ("Spare", 0.0)]),
    (12.0, 0.8, [("Drums", 0.8), ("Pad", 0.1), ("Spare", 0.0)]),
    (16.0, 0.1, [("Drums", 0.0), ("Pad", 0.3), ("Spare", 0.0)]),
]


def test_meters_summarise_tracks_sections_and_silence():
    bridge = MeterBridge(TIMELINE)
    result = _call(make_server(bridge), {
        "play_from": 0, "until_beat": 16, "interval": 0.02,
        "sections": [{"name": "Intro", "start": 0, "end": 8}, {"name": "Drop", "start": 8, "end": 16}],
    })
    assert result["samples"] == 5
    assert result["method"] == "socket_poll"
    assert result["beats"] == [0.0, 16.0]
    assert result["master"] == {"peak": 0.9, "mean": 0.46}
    assert result["tracks"]["Drums"] == {"peak": 0.9, "mean": 0.56}
    assert result["silent"] == ["Spare"]
    assert result["sections"]["Intro"]["master"] == {"peak": 0.3, "mean": 0.25}
    assert result["sections"]["Drop"]["loudest"][0] == {"track": "Drums", "peak": 0.9}
    assert any("start_playing" in (c or "") for c in bridge.calls)
    assert bridge.playing is False


def test_meters_can_filter_tracks_and_keep_playing():
    bridge = MeterBridge(TIMELINE)
    result = _call(make_server(bridge), {"play_from": 0, "until_beat": 8, "tracks": ["Pad"], "keep_playing": True, "interval": 0.02})
    assert list(result["tracks"]) == ["Pad"]
    assert bridge.playing is True


def test_meters_stop_early_and_hint_when_nothing_plays():
    bridge = MeterBridge([(0.0, 0.0, [("Drums", 0.0)])], playing=False)
    result = _call(make_server(bridge), {"duration": 5, "interval": 0.02})
    assert result["samples"] == 4
    assert "play_from" in result["hint"]
    assert not any("start_playing" in (c or "") for c in bridge.calls)


class CaptureBridge:
    """A Remote Script with meter_capture: rows are buffered on Live's timer and returned on stop."""

    def __init__(self, rows, names):
        self.rows, self.names = rows, names
        self.actions = []
        self.playing = False

    def request(self, method, params):
        if method == "meter_capture":
            self.actions.append(params["action"])
            if params["action"] == "start":
                return {"capturing": True, "tracks": self.names}
            if params["action"] == "status":
                return {"capturing": self.actions.count("status") < 2, "samples": len(self.rows), "playing": True}
            return {"capturing": False, "samples": len(self.rows), "names": self.names, "rows_json": json.dumps(self.rows)}
        if method == "eval":
            return [] if "solo" in params["expr"] else 32.0
        self.playing = "start_playing" in params.get("code", "") or (self.playing and "stop_playing" not in params.get("code", ""))
        return None


def test_meters_prefer_remote_tick_capture():
    rows = [[32.0, 0.5, 0.4, 0.1], [33.0, 0.7, 0.8, 0.0], [34.0, 0.6, 0.6, 0.0]]
    bridge = CaptureBridge(rows, ["Drums", "Pad"])
    result = _call(make_server(bridge), {"play_from": 32, "duration": 5,
                                         "sections": [{"name": "Drop", "start": 32, "end": 40}]})
    assert result["method"] == "remote_tick"
    assert bridge.actions[0] == "start" and bridge.actions[-1] == "stop"
    assert result["samples"] == 3
    assert result["master"] == {"peak": 0.7, "mean": 0.6}
    assert result["tracks"]["Drums"] == {"peak": 0.8, "mean": 0.6}
    assert result["sections"]["Drop"]["loudest"][0] == {"track": "Drums", "peak": 0.8}
    assert bridge.playing is False


def test_meters_warn_when_tracks_are_soloed():
    bridge = MeterBridge(TIMELINE, soloed=["Drums"])
    result = _call(make_server(bridge), {"play_from": 0, "until_beat": 8, "interval": 0.02})
    assert result["soloed"] == ["Drums"]
    assert "soloed" in result["warning"]
