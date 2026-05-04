# AgentAudioTap

AgentAudioTap is a Max for Live audio effect for sampling audio at its insertion point in an Ableton signal chain.

Build and install it into the Ableton User Library:

```sh
.venv/bin/python scripts/build_agent_audio_tap.py --install
```

The device records stereo pass-through audio with `sfrecord~ 2`. Control paths:

- MCP/file command: write `/tmp/agent_audio_tap_command.json` with `{"id":"unique","command":"open","path":"/tmp/tap.wav"}`, then `start` and `stop` commands.
- MCP tool: `live_agent_audio_tap` forwards `command` and optional `path` through the Ableton Remote Script.
- MIDI trigger: note 60 starts recording with the previously opened path; note 61 stops recording.
- OSC/UDP fallback: `/agent_audio_tap start /tmp/tap.wav` or `/agent_audio_tap stop` on UDP port `17654`.

Use the MCP/file path to set filenames, then MIDI notes for arrangement-timed start/stop when desired.

Default placement guidance: put one AgentAudioTap on the master track and solo the track or group being analyzed. Use per-track insertion only when the target is a specific point inside a signal chain, such as before or after one effect.
