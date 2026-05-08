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

After changing files under `Ableton_Live_MCP/`, reinstall the Remote Script and reload the Control Surface before treating Live validation as current. The running Control Surface does not pick up repository edits automatically; stale installed scripts can make bridge behavior, timeout handling, or generated-device triggers look broken after the source has been fixed. `ableton-live-mcp-validate` checks both the installed Remote Script files and the running `live_ping` script hash; do not ignore a stale/missing hash unless deliberately validating old code.

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

Treat generated UI as an open design surface, not a knob bank. Native patches may use multisliders, pictslider-style XY controls, button matrices, piano keyboards, sequencer lanes, animated meters, waveform/scope displays, live.text controls, swatches, toggles, and other Max UI objects. Web patches may use canvas, SVG, WebGL/Three.js, piano rolls, reactive scenes, custom gestures, and generated assets. Keep the implementation free-form and device-specific while validating that every authored control or visualization is visible, unclipped, and meaningfully connected to device state or audio/MIDI telemetry.

Native and web UI binding sources must be agent-settable, not only user-settable. When a control has `ui_bind`/`ui_bindings`, validate both directions: move the UI source and confirm the target state changes, then send an agent `set` to the source ID and confirm the host reports `changed: 1`, updates the visible source, and writes the intended target value in status `state`.

For telemetry bindings from output-only Max objects such as meters, analyzers, MIDI parsers, or other probes, set `report: false` and leave the source non-settable, or explicitly use `source_settable: false`. Do not let restored target state write back into signal-rate objects such as `peakamp~`.

Generated devices should not appear as skinny slivers in Live's device rack. Size the device to the authored UI: provide meaningful `presentation_rect` bounds for native objects and web UI panels, or set an explicit top-level `device_width`/`devicewidth` when the intended design needs more room. The host builder infers Max `devicewidth` from those bounds with padding, and the runtime host reports/applies the intended width during hot reloads. This sizing remains data-driven; do not introduce fixed templates or limit agents to preapproved panel layouts.

For web UI, `webui` may be an object or array of objects; each entry can choose its own `id`, `presentation_rect`, `patching_rect`, object type, attrs, and local HTML file. `jweb~`/`jweb` are supported, with `jbrowser~`/`jbrowser` accepted as aliases for Live 12+. For instruments that respond to Live clips, route MIDI with `midiin` into `midiparse` and parse the note-list outlet; `notein` is not sufficient for this validation path. Validate in Live before calling the work done: device class matches role, native and/or web UI is visible and unclipped, controls can change device values, values can be changed by agent commands, instruments produce audio from MIDI, and hot reload does not interrupt playback.

Generated web UI should acknowledge basic readiness before expensive work. Send a small `uiReady`/equivalent message from a classic bootstrap script before loading large modules, WebGL scenes, models, or generated bundles; then report feature readiness such as `threeReady` separately and provide a fallback/error state instead of letting one import or renderer failure leave a blank panel.

Keep stable `webui.id` values across hot reloads when the design is an evolution of the same panel. The generated host reuses an existing browser object for a matching stable id/object type to avoid CEF churn and playback-visible reload failures in stressed sets; set `reuse: false` only when the panel deliberately needs a fresh browser instance. This is a lifecycle hint, not a layout restriction.

Use the host's `web_read_scheduled`, `web_read_attempts`, `web_read_pending`, `web_loaded`, `web_url`, `web_title`, and per-panel `web_<id>_loaded`, `web_<id>_read_attempts`, `web_<id>_url`, and `web_<id>_title` status state to distinguish host delivery, browser load, and custom JavaScript readiness. The host tags browser messages and retries reads under load; if attempts exhaust after selecting the device and showing Detail/Device Chain, the embedded browser itself did not finish loading, so inspect the Max/CEF logs and active renderer process count before debugging the generated HTML.

For generated MIDI effects, validate the transformed MIDI itself, not only downstream audio. Put the MIDI effect before a known-good instrument, expose compact telemetry with `ui_bind` on message-rate objects such as input pitch, output pitch, gate, or velocity, and verify both the MIDI effect status and the downstream instrument status agree on the transformed note values while the track meters are nonzero.

For rich web UI, put large libraries, models, images, fonts, or generated bundles in the webui `assets` map/list and reference the written relative files from HTML/JS. After materialization, command patches should carry only paths and small metadata, not bulky `html`, `css`, `js`, `controls`, or asset source content. This keeps creative UIs such as Three.js scenes practical without making M4L command files slow or fragile.

When stress-testing many simultaneous web UI devices, a fresh `jweb` panel can fail to start while already-instantiated panels keep running. Before blaming the generated patch, select the target track/device and show Detail/Device Chain; if status state still does not move, retry in a less-crowded set or reuse an already-active host, and record that separately from command-delivery failures.

For high-rate web UI gestures, animation clocks, piano rolls, sequencers, and meters, send `set_silent`/`param_silent` or batched `set_many_silent`/`param_many_silent` from the web panel instead of `set`/`param`; then use explicit status commands for validation snapshots. Use normal `set` when an event should acknowledge through the status file.

For long generated-device soaks, track transport state separately from command/device failures. If Live transport stops, relaunch the target clip and count that as a transport event; do not let every later silent meter sample inflate the device failure count. Keep command acknowledgements, connection errors, transport stops, restarts, and meter readings as separate counters.

In heavy generated sets, timeline seeks and repeated `transport play` with `time: 0` can be much slower than compact status/value commands. Prefer checking transport status, using continue/play without a seek, or relaunching only the target clip when possible; reserve timeline resets for tests that specifically need them and give those calls a realistic timeout.

Bridge calls in heavily stressed Live sets can take tens of seconds even when Live eventually responds. Treat short socket timeouts as inconclusive under load; retry with a longer timeout and record bridge latency separately from generated-device failures.

For parameterized MSP patches, do not rely on sending agent values directly into signal-processing objects like `*~`, filters, or `plugout~`. Create explicit message-rate controls such as `flonum`, `live.dial`, or other UI/message objects, bind agent/web/native controls to those IDs, and patch their outlets to the intended MSP control inlets. Avoid generated IDs that can collide with role I/O names or host/static Max objects, such as `plugin`, `plugout`, `midiin`, `midiout`, `js`, `script`, `status`, or generic `out`.

Use `jbrowser~`/`jbrowser` as compatibility aliases for control-panel web UI. If a generated device explicitly needs web-audio signal outlets, request `jweb~` deliberately and wire signal/message outlets in the generated patch instead of assuming the control-panel default.

When validating a generated M4L command, pass `wait_status: true` with a short `status_timeout` so the bridge returns the host's reload/set status in the same tool call instead of using separate sleeps and file reads. Treat the command file as the reliable delivery path for all M4L commands; UDP is only a low-latency hint because multiple loaded Max devices on one UDP port are not a sufficient reliability guarantee.

Generated hosts should use deterministic per-instance `udpreceive` ports derived from the instance id, not one shared M4L port. Value-only UDP hints should stay slim and omit recovery patches; the command file must still contain the full patch/spec so JS reload and set reopen recovery keep working. Large generated update payloads may skip UDP entirely and rely on the host's command-file poll rather than failing the command with an OS datagram-size error.

For generated `jweb`/`jbrowser~` panels, issue the first `read` immediately after object creation; use scheduled retries only after that first read. In stressed sets, Max JS `Task` scheduling can lag or stall, so the initial web UI load must not depend solely on a later task callback.

When a generated web panel has a local `html_path`, pass that filesystem path to the Max `readfile` message first and keep `url`/`html_url` available for fallback `read` attempts. The host alternates to URL-style `read` on retries when a local page does not acknowledge, because different Max/CEF states can prefer different load paths.

For direct generated audio-effect graphs, connect `plugin` outlet 0/1 through the generated processing objects and into `plugout` inlet 0/1. The static `audio-in-l`, `audio-in-r`, `audio-out-l`, and `audio-out-r` objects are named send/receive bus endpoints for cross-patcher routing, not direct signal sources/destinations for a simple pass-through chain.

When `live_agent_m4l_device` does not need to build or resolve a Live track/device, the MCP server may write the command file directly and then wait for host status. This is the preferred fast path for value-only `set`, `status`, `clear`, and `build: false` hot-reload commands.

For iterative generated M4L updates, reuse the installed role host. `live_agent_m4l_device` skips rebuilds by default for `set`, `status`, `clear`, and value-only calls; pass `build: true` only when creating/replacing the host AMXD.

Hot reloads should preserve current values for matching generated parameter IDs. When changing a patch/spec, keep stable IDs for controls and controlled objects that should retain state; change IDs deliberately when the new design should reset a value.

In large or stressed Live sets, do not pass short `timeout` values such as `10` to `live_batch`, transport, browser, or generated-device operations unless deliberately probing latency. The Remote Script and Python client default to a longer main-thread timeout so ordinary playback/control commands can survive Live UI stalls.

Short `timeout` values are non-strict by default: the Remote Script and Python client clamp them to the default main-thread wait to avoid accidental failures in stressed sets. Use `strict_timeout: true` only for deliberate latency probes where a timeout failure is the expected signal.

A build/install can succeed before Live's browser has indexed the new AMXD. In that case `live_agent_m4l_device` may return `loaded: false` with `load_error` while still returning `built`, `command_file`, and `status_file`. Treat that as a recoverable indexing/load condition: reuse an already loaded compatible host for immediate validation, or retry loading after the browser catches up.

When a fresh generated AMXD is built and loaded in one `live_agent_m4l_device` call, the MCP server should retry the load client-side for a short window instead of sleeping inside Live's Remote Script. Use `load_retry_timeout` and `load_retry_interval` only for browser-indexing delays; value-only `set` calls should continue to skip rebuilds and load retries.

The generated device command file is also the host's recovery payload when Max reloads JS or a set is reopened. Patch/update commands write the patch. Normal `set` and `status` commands should also write the command file, but must include the most recent patch/spec in the payload so they do not destroy recovery. Commands need unique IDs; otherwise Max's file poll dedupe can ignore a repeated status or value command. If a JS reload resets runtime state, the host should recover from the persisted patch before handling the next `set` or `status`; if status still reports `dynamic_objects: 0`, reload a patch/update command before continuing value-only validation.

When a host AMXD is loaded after its command file has already been written, the JS `loadbang` should explicitly start the static low-priority `metro` poller and consume the prewritten command file once. The host should also include `live.thisdevice` and self-starting metro attributes as secondary startup paths. The same polling bang should service pending web reads so browser retries do not depend solely on Max JS `Task` scheduling.

Generated host AMXDs should include a static `filewatch <command_file>` object started from `loadbang`/`live.thisdevice`. Send the command-file path into `filewatch` explicitly before sending `1`, and route its output through a tagged JS path such as `prepend __filewatch` so status can prove whether a file-change wakeup fired. Treat `filewatch` as one observable wake hint, not as proof of delivery; in stressed sets it has been observed not to fire after external command-file writes.

Generated hosts should also use activity already present in Live as a command-file wake source. For audio-effect and instrument hosts, include a low-rate signal-edge wake path such as `phasor~` -> `>~` -> `edge~` -> `prepend __signal_wake` -> JS, and keep that signal branch computed by routing it through a zero-gain signal sink into `plugout~`; for instrument and MIDI-effect hosts, route static `midiin` into JS. These wake messages must only poll the command file and pending web reads; they must not constrain or reinterpret the generated creative patch, consume MIDI, impose a pass-through, or report continuously. Status should expose the last wake source for validation.

After a generated spec loads, the host should also create and explicitly start a hidden dynamic low-priority `qmetro` connected back into the JS object as a secondary wakeup path. Do not assume any wake mechanism is sufficient in a stressed Live set; filewatch, timers, UDP, signal-edge wakes, MIDI wakes, and Live parameter writes have all been observed to miss follow-up command files after load. Every follow-up `set`/`status` must be validated by a matching status `command_id`; if it does not ack, stop claiming hot reload works in that set and ask for permission to reload/simplify the set or load a fresh host in a cleaner validation set.

When `wait_status` times out, preserve the expected command id/event, mismatch reason, and a compact last-status summary in the MCP response. Do not bury this failure behind a generic timeout; agents need the last acked command id, wake-source telemetry, and web-read state to decide whether to retry, rebuild, or ask for a cleaner validation set.

Setting a generated `live.numbox`/`live.*` parameter through the Live API does not reliably emit from the UI object's Max outlet. If the host observes Live parameters, the observer path must remain generic: observe the generated device's current parameters and route names that match `ui_bindings` through the same binding code as native UI gestures. Treat observer output as opportunistic state sync until validated by a status update.

Create or refresh `LiveAPI` observers only from the `live.thisdevice`/deferred low-priority path or a later `Task`, never from JS global initialization. Cycling '74 documents that `live.thisdevice` is the readiness signal for the Live API and that JS LiveAPI work must not run in Max's high-priority scheduler. Use static `live.path this_device` and `live.path this_device parameters 1` objects, bang them from `loadbang`/`live.thisdevice`, and also keep explicit `path ...` messages as a compatibility fallback; do not hardcode track or device indexes.

Generated hosts may also create Max-object `live.path`/`live.observer value` observers for Live parameters that need to react to Live API writes. Observe the hidden trigger at `this_device parameters 1`; for generated bound `live.*` controls, assign observer indexes from the generated parameter creation order and route observer output through the same `ui_bindings` code as native UI gestures. This must remain general purpose and must be validated per host because Live parameter notifications may produce initial values without reliably waking later command-file reads.

Generated hosts also expose a hidden Live parameter used only to try to wake the command-file poller; Live may surface it as `Agent Poll`, `Agent M4L Poll`, or the Max box name `command-trigger`. After writing a command file, the Remote Script may toggle one of those names on the target generated device when a target track is available, but the command is not successful until the status file reports the matching `command_id`.
