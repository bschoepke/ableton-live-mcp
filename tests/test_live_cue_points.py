import pytest

from server import make_server


class FakeCue:
    def __init__(self, song, time, name):
        self.song, self.time, self.name = song, time, name

    def jump(self):
        self.song.pending_time = self.time


class FakeSong:
    """Mimics Live: current_song_time assignments only land after the request that made them."""

    def __init__(self, cues=(), length=256.0):
        self._time = 0.0
        self.pending_time = None
        self.is_playing = False
        self.song_length = length
        self.cue_points = [FakeCue(self, t, n) for t, n in cues]

    @property
    def current_song_time(self):
        return self._time

    @current_song_time.setter
    def current_song_time(self, value):
        self.pending_time = float(value)

    def set_or_delete_cue(self):
        existing = [c for c in self.cue_points if c.time == self._time]
        if existing:
            self.cue_points.remove(existing[0])
        else:
            self.cue_points.append(FakeCue(self, self._time, str(len(self.cue_points) + 1)))


class CueBridge:
    def __init__(self, song):
        self.song = song
        self.toggles = 0

    def request(self, method, params):
        if self.song.pending_time is not None:
            self.song._time, self.song.pending_time = self.song.pending_time, None
        # Runs the tool's own code strings against the fake song, as the Remote Script would in Live.
        env = {"song": self.song}
        if method == "eval":
            return eval(params["expr"], env)
        if method == "exec":
            self.toggles += "set_or_delete_cue" in params["code"]
            exec(params["code"], env)
            return env.get("result")
        raise AssertionError("unexpected method %r" % method)


def _call(server, arguments):
    response = server.handle({
        "jsonrpc": "2.0", "id": 9, "method": "tools/call",
        "params": {"name": "live_cue_points", "arguments": arguments},
    })
    if "error" in response:
        raise RuntimeError(response["error"]["message"])
    return response["result"]["structuredContent"]


def _names(song):
    return sorted((c.time, c.name) for c in song.cue_points)


def test_add_waits_for_playhead_then_names_and_restores_position():
    song = FakeSong([(0.0, "Intro")])
    song._time = 70.0
    result = _call(make_server(CueBridge(song)), {"action": "add", "time": 32, "name": "Build"})
    assert result["added"] is True
    assert result["cue_point"] == {"time": 32.0, "name": "Build"}
    assert _names(song) == [(0.0, "Intro"), (32.0, "Build")]
    assert song._time == 70.0


def test_add_on_existing_cue_does_not_toggle_it_away():
    song = FakeSong([(32.0, "Build")])
    bridge = CueBridge(song)
    result = _call(make_server(bridge), {"action": "add", "time": 32})
    assert result["existed"] is True and result["added"] is False
    assert bridge.toggles == 0
    assert _names(song) == [(32.0, "Build")]


def test_rename_jump_and_delete_by_name():
    song = FakeSong([(0.0, "Intro"), (64.0, "Drop")])
    server = make_server(CueBridge(song))
    assert _call(server, {"action": "rename", "name": "Drop", "new_name": "Drop 1"})["cue_point"]["name"] == "Drop 1"
    assert _call(server, {"action": "jump", "name": "Drop 1"})["jumped"]["time"] == 64.0
    assert _call(server, {"action": "delete", "name": "Drop 1"})["deleted"] == {"time": 64.0, "name": "Drop 1"}
    assert _names(song) == [(0.0, "Intro")]
    assert _call(server, {"action": "list"}) == {"cue_points": [{"time": 0.0, "name": "Intro"}]}


@pytest.mark.parametrize("arguments, message", [
    ({"action": "add", "time": 999}, "past the end of the song"),
    ({"action": "delete", "name": "missing"}, "0 cue points match"),
    ({"action": "rename", "time": 0}, "needs new_name"),
])
def test_invalid_requests_are_rejected(arguments, message):
    song = FakeSong([(0.0, "Intro")])
    with pytest.raises(RuntimeError, match=message):
        _call(make_server(CueBridge(song)), arguments)
    assert _names(song) == [(0.0, "Intro")]


def test_add_refuses_while_playing():
    song = FakeSong()
    song.is_playing = True
    with pytest.raises(RuntimeError, match="stop the transport"):
        _call(make_server(CueBridge(song)), {"action": "add", "time": 16})
    assert song.cue_points == []
