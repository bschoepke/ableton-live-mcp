# Ableton Live MCP Setup

1. From this repository checkout, install the package into the agent environment:

   ```sh
   python -m pip install -e ".[dev]"
   ```

2. Install the Ableton Remote Script:

   ```sh
   ableton-live-mcp-install-remote-script
   ```

3. Start Ableton Live, open Settings, and select `Ableton_Live_MCP` as a Control Surface.

4. Register the MCP server in your MCP client:

   ```json
   {
     "mcpServers": {
       "ableton": {
         "command": "ableton-live-mcp"
       }
     }
   }
   ```

5. Validate the connection while Ableton is running:

   ```sh
   ableton-live-mcp-validate
   ```

The Remote Script binds only to `127.0.0.1`. If Ableton was already open when the script was installed, restart Ableton or reload the Control Surface.

## Repository operations

Never push commits, branches, or tags to a remote without explicit user authorization.

## Bridge reliability

Persistent localhost bridge sockets can be closed after an idle period. The client should proactively reconnect with `ABLETON_MCP_IDLE_TIMEOUT` and retry stale `id: null` `"timed out"` responses from older Remote Scripts. The Remote Script should close idle clients silently, without sending JSON-RPC timeout errors that can be misread as the next request's response. Use per-call `timeout` only for work that genuinely needs longer on Live's main thread.

## Max for Live devices
This repo includes `m4l/AgentAudioTap.amxd`, a Max for Live audio effect that lets an agent record the audio signal at the device's insertion point for analysis. Build/install it with:

```sh
.venv/bin/python scripts/build_agent_audio_tap.py --install
```

For validation captures, prefer one atomic `live_agent_audio_tap` `start` command with `path`, then a later `stop`. Avoid separate `open` then `start` command-file writes unless you also add an acknowledgement wait; otherwise the tap can poll only the later `start` and Max may log `sfrecord~: start requested without preceding open`.

Before using AgentAudioTap WAVs as pass/fail evidence, sanity-check the measurement path by changing the target track volume or solo/mute state and confirming the capture or Live meters respond. If a capture ignores an obvious source change, rebuild/reload the tap and use Live's `output_meter_left/right` as the validation signal until the tap path is trustworthy again.

## Dynamic Agent M4L devices

For generated instruments, audio effects, MIDI effects, and web UI devices, use `live_agent_m4l_device` or:

```sh
ableton-live-mcp-build-m4l-device --role instrument my_inst
```

The builder must preserve Ableton's role-specific AMXD wrappers from the installed Live 12+ blank Max Device templates. Do not hand-roll a generic AMXD container for instruments or MIDI effects; Live can load it as an audio effect, leaving MIDI synth tracks silent.

Do not hardcode macOS app bundle paths, Windows install paths, User Library paths, command/status temp paths, or Ableton point-release names. Use `ableton_paths.py` discovery, `ABLETON_USER_LIBRARY`, `ABLETON_MAX_DEVICE_TEMPLATE_DIR`, `ABLETON_LIVE_PATH`, or `ABLETON_MCP_STATE_DIR` when defaults are not enough.

The dynamic host exposes role I/O objects but must not impose fixed audio or MIDI pass-through patch cords. Generated specs must explicitly connect `plugin`/`midiin` to generated processing and then to `plugout`/`midiout` as needed, so the agent can build replacement effects, generators, pass-through processors, or MIDI transformers without hidden dry paths. Audio-capable hosts also expose static instance-scoped MSP buses named `agent_m4l_<instance>_audio_in_l`, `agent_m4l_<instance>_audio_in_r`, `agent_m4l_<instance>_audio_out_l`, and `agent_m4l_<instance>_audio_out_r`; prefer generated `receive~` objects for inputs and generated `send~` objects for outputs when validating audio effects, because Live device I/O must remain wired at AMXD load time. If role-level static I/O changes, rebuild and reload the host AMXD; dynamically adding another `plugout~` after load is not a reliable way to redefine Live device I/O.

MCP must remain general purpose. Do not restrict generated devices to fixed UI slots, templates, or preapproved layouts. The agent may generate arbitrary native M4L UI, arbitrary web UI, or a mix. Put Live device-view placement in each object's `presentation_rect`, use `box_attrs` only for box/presentation attributes, use object `messages` for runtime configuration when a Max object does not accept creation attributes reliably, and bind any native UI object to any generated parameter/object with `ui_bindings` or an object-level `ui_bind`/`bind` field.

Generated devices should not appear as skinny slivers in Live's device rack. Size the device to the authored UI: provide meaningful `presentation_rect` bounds for native objects and web UI panels, or set an explicit top-level `device_width`/`devicewidth` when the intended design needs more room. The host builder infers Max `devicewidth` from those bounds with padding, and the runtime host reports/applies the intended width during hot reloads. This sizing remains data-driven; do not introduce fixed templates or limit agents to preapproved panel layouts.

For web UI, `webui` may be an object or array of objects; each entry can choose its own `id`, `presentation_rect`, `patching_rect`, object type, attrs, and local HTML file. `jweb~`/`jweb` are supported, with `jbrowser~`/`jbrowser` accepted as aliases for Live 12+. For instruments that respond to Live clips, route MIDI with `midiin` into `midiparse` and parse the note-list outlet; `notein` is not sufficient for this validation path. Validate in Live before calling the work done: device class matches role, native and/or web UI is visible and unclipped, controls can change device values, values can be changed by agent commands, instruments produce audio from MIDI, and hot reload does not interrupt playback.

For long generated-device soaks, track transport state separately from command/device failures. If Live transport stops, relaunch the target clip and count that as a transport event; do not let every later silent meter sample inflate the device failure count. Keep command acknowledgements, connection errors, transport stops, restarts, and meter readings as separate counters.

For parameterized MSP patches, do not rely on sending agent values directly into signal-processing objects like `*~`, filters, or `plugout~`. Create explicit message-rate controls such as `flonum`, `live.dial`, or other UI/message objects, bind agent/web/native controls to those IDs, and patch their outlets to the intended MSP control inlets. Avoid generated IDs that can collide with role I/O names or Max conventions, such as `plugin`, `plugout`, `midiin`, `midiout`, or generic `out`.

Use `jbrowser~`/`jbrowser` as compatibility aliases for control-panel web UI. If a generated device explicitly needs web-audio signal outlets, request `jweb~` deliberately and wire signal/message outlets in the generated patch instead of assuming the control-panel default.

When validating a generated M4L command, pass `wait_status: true` with a short `status_timeout` so the bridge returns the host's reload/set status in the same tool call instead of using separate sleeps and file reads. Treat the command file as the reliable delivery path for all M4L commands; UDP is only a low-latency hint because multiple loaded Max devices on one UDP port are not a sufficient reliability guarantee.

Generated hosts should use deterministic per-instance `udpreceive` ports derived from the instance id, not one shared M4L port. Value-only UDP hints should stay slim and omit recovery patches; the command file must still contain the full patch/spec so JS reload and set reopen recovery keep working.

When `live_agent_m4l_device` does not need to build or resolve a Live track/device, the MCP server may write the command file directly and then wait for host status. This is the preferred fast path for value-only `set`, `status`, `clear`, and `build: false` hot-reload commands.

For iterative generated M4L updates, reuse the installed role host. `live_agent_m4l_device` skips rebuilds by default for `set`, `status`, `clear`, and value-only calls; pass `build: true` only when creating/replacing the host AMXD.

A build/install can succeed before Live's browser has indexed the new AMXD. In that case `live_agent_m4l_device` may return `loaded: false` with `load_error` while still returning `built`, `command_file`, and `status_file`. Treat that as a recoverable indexing/load condition: reuse an already loaded compatible host for immediate validation, or retry loading after the browser catches up.

When a fresh generated AMXD is built and loaded in one `live_agent_m4l_device` call, the MCP server should retry the load client-side for a short window instead of sleeping inside Live's Remote Script. Use `load_retry_timeout` and `load_retry_interval` only for browser-indexing delays; value-only `set` calls should continue to skip rebuilds and load retries.

The generated device command file is also the host's recovery payload when Max reloads JS or a set is reopened. Patch/update commands write the patch. Normal `set` and `status` commands should also write the command file, but must include the most recent patch/spec in the payload so they do not destroy recovery. Commands need unique IDs; otherwise Max's file poll dedupe can ignore a repeated status or value command. If a JS reload resets runtime state, the host should recover from the persisted patch before handling the next `set` or `status`; if status still reports `dynamic_objects: 0`, reload a patch/update command before continuing value-only validation.
