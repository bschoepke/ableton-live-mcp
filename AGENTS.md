# Ableton Live MCP Setup

MCP initialize instructions are intentionally compact; when they point to `AGENTS.md`, use this file for repo-specific Ableton, Max for Live, reliability, validation, and recovery guidance.

1. From this repository checkout, install the package into the agent environment:

   ```sh
   python -m pip install -e ".[dev]"
   ```

   The `dev` extra intentionally includes the Ableton-only visual capture dependencies used for M4L UI verification. For a lean runtime install that still needs screenshots, install the `visual` extra too.

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

   For a deliberate quick health probe against a stressed or possibly wedged set, use a bounded strict check such as:

   ```sh
   ableton-live-mcp-validate --timeout 3 --strict-timeout --allow-stale-remote-script
   ```

The Remote Script binds only to `127.0.0.1`. If Ableton was already open when the script was installed, restart Ableton or reload the Control Surface.

After changing files under `Ableton_Live_MCP/`, reinstall the Remote Script and reload the Control Surface before treating Live validation as current. The running Control Surface does not pick up repository edits automatically; stale installed scripts can make bridge behavior, timeout handling, or generated-device triggers look broken after the source has been fixed. `ableton-live-mcp-validate` checks the installed Remote Script files plus the running `live_ping` runtime version, loaded-code fingerprint, and script hash; do not ignore a stale/missing runtime marker, code fingerprint, or hash unless deliberately validating old code.

Reloading the Control Surface or restarting Live can interrupt playback and the user's current set. Ask for explicit user authorization before doing either, then rerun `ableton-live-mcp-validate` and require `runtime_current: true` and `live_mutations_safe: true` before claiming current-runtime e2e validation or sending generated-device mutations.

If a known MCP tool appears in the active client with an obviously stale or no-argument schema, such as `live_agent_audio_tap` rejecting `{"command": "stop"}` or `live_transport` rejecting `{"action": "play"}`, stop using that client schema. Run `ableton-live-mcp-validate` and reload/restart the MCP server or MCP client session so `tools/list` is refreshed; do not keep trying calls that the active schema cannot represent.

Save, crash-report, and "Recover last Live Set" dialogs are common after forced recovery. Treat any Live modal as a hard blocker for MCP mutations: inspect the Ableton Live UI, resolve only the Ableton dialog, then rerun validation before continuing. If the user has authorized a fresh disposable validation set and the open set contains only generated test material, choose the non-saving/non-recovery path such as "Don't Save" or "No"; if user work might be present, stop and ask before dismissing it.

If validation fails with `live_error`, preserve `live_failure_type`, `runtime_mismatch`, and `runtime_next_action` in the investigation notes. `live_main_thread_timeout` often means a modal dialog or heavy UI/indexing work is blocking Live; inspect the UI before sending more mutations. `bridge_not_listening` means the Control Surface bridge is not accepting localhost connections. Installed files may still be current in either case, so do not infer current-runtime behavior from source tests alone.

When the bridge socket still responds but Live's main thread does not execute scheduled work, validation reports `live_failure_type: "live_main_thread_hung"` and may include `bridge_status.server_thread_responsive: true`. Treat this as a real Live hang, not a retryable command failure: stop sending Live API mutations, avoid piling up queued callbacks, and ask for explicit authorization before reloading the Control Surface or restarting Live. Use `live_bridge_status` only for no-Live-API socket-thread diagnostics while the set is wedged.

When validation reports `live_failure_type: "bridge_unresponsive"` or `"live_process_unresponsive"`, even the socket-thread `live_bridge_status` probe timed out. Do not keep probing or retry with longer Live API calls; preserve diagnostics, use Ableton-only visual capture or an OS process sample if the display/session allows it, and recover/restart/reload Live only with user authorization.

## Repository operations

Never push commits, branches, or tags to a remote without explicit user authorization.

## Bridge reliability

Persistent localhost bridge sockets can be closed after an idle period. The client should proactively reconnect with `ABLETON_MCP_IDLE_TIMEOUT` and retry stale `id: null` `"timed out"` responses from older Remote Scripts. The Remote Script should close idle clients silently, without sending JSON-RPC timeout errors that can be misread as the next request's response. Use per-call `timeout` only for work that genuinely needs longer on Live's main thread.

Keep TCP connection failure bounded separately from Live work. `ABLETON_MCP_CONNECT_TIMEOUT` controls the short localhost connect attempt, while `ABLETON_MCP_TIMEOUT` and per-call `timeout` control how long to wait for operations that are already connected and running inside Live.

After a request has been written to the bridge socket, response timeouts must fail closed rather than retrying automatically. Treat the mutation status as unknown until a later compact status/summary check proves whether Live applied it; blind retry can duplicate clips, devices, transport actions, or generated M4L commands. The Python client may enter a short stall cooldown after a sent-call timeout or Live-main-thread timeout; ordinary Live API calls should fail fast during that window while `live_bridge_status` remains available for diagnosis.

When using `strict_timeout: true` for a deliberate latency probe, the Python client response deadline should honor that shorter timeout too. Non-strict calls may keep the longer default because stressed Live sets often respond late but correctly.

## Max for Live devices
This repo includes `m4l/AgentAudioTap.amxd`, a Max for Live audio effect that lets an agent record the audio signal at the device's insertion point for analysis. Build/install it with:

```sh
.venv/bin/python scripts/build_agent_audio_tap.py --install
```

For validation captures, call `live_agent_audio_tap` with a `command` field. Prefer one atomic `{"command": "start", "path": "..."}` command, then a later `{"command": "stop"}`. Avoid separate `open` then `start` command-file writes unless you also add an acknowledgement wait; otherwise the tap can poll only the later `start` and Max may log `sfrecord~: start requested without preceding open`.

Before using AgentAudioTap WAVs as pass/fail evidence, sanity-check the measurement path by changing the target track volume or solo/mute state and confirming the capture or Live meters respond. If a capture ignores an obvious source change, rebuild/reload the tap and use Live's `output_meter_left/right` as the validation signal until the tap path is trustworthy again.

## Visual validation captures

For M4L UI visual validation, use `live_visual_capture` or `ableton-live-mcp-capture-window` to capture only Ableton Live windows. This tool is never a general screenshot API: do not add arguments or workflows that capture arbitrary apps, monitors, desktops, browser windows, terminals, or user-selected window handles. The implementation must enumerate candidate OS windows, filter them to verified Ableton Live processes first, and only then apply optional title filters.

After creating, hot-reloading, or resizing generated M4L UI, the agent MUST visually verify the result with the Ableton-only capture tool before claiming the UI is aligned, visible, unclipped, or polished. Use host status and presentation geometry as supporting evidence, but do not treat them as a substitute for a Live-window visual capture when pixel validation is available. If the capture tool is unavailable because required OS permissions or optional backend packages are missing, record that blocker explicitly and continue with geometry/status checks only as a weaker fallback.

Prefer focused captures when validating generated devices: select the target track/device first, then use the `device-detail` region and `max_width`/`max_height` downscaling for first-pass inspection. The device-detail crop reflects Live's current selection; if the captured device label is not the target device, the capture is not valid evidence for that target. Capture the full Ableton window only when checking track/device selection or chain context. Cropping and downscaling are post-processing steps on a verified Ableton Live window capture; they must not become a path to capture arbitrary windows or screens. This keeps UI iteration low-latency and token-efficient while preserving the Ableton-only privacy boundary.

If the capture result reports `warning: blank_capture` or postprocess content stats show `blank: true`, treat that screenshot as invalid validation evidence. A locked laptop, sleeping display, or locked OS session can make otherwise valid Ableton-window capture return a blank image; stop retrying capture backends in that case, ask the user to unlock/wake the display, and record the blocker. Do not keep running hot-reload visual iteration or audio-reactive visual validation loops while capture is blank; switch to local tests or nonvisual status probes until the Live window is capturable again. Otherwise retry with the other backend, bring the Ableton Live window onto a capturable display/space, or continue with status-only evidence while recording the visual validation blocker. Do not claim UI visibility or audio-reactive visuals from a blank capture.

On Windows, the optional backend may use `windows-capture` / Windows Graphics Capture so an Ableton Live window can be captured from the compositor even when it is occluded by the agent UI. On macOS, use the OS window-id capture path (`screencapture`/Quartz or a future ScreenCaptureKit backend) against verified Ableton Live window IDs. Both paths may require the user's OS screen-recording permission. If capture is unavailable, fall back to host status, generated presentation geometry, and web UI telemetry rather than broadening capture permissions.

## Dynamic Agent M4L devices

For generated instruments, audio effects, MIDI effects, and web UI devices, use `live_agent_m4l_device` or:

```sh
ableton-live-mcp-build-m4l-device --role instrument my_inst
```

The builder must preserve Ableton's role-specific AMXD wrappers from the installed Live 12+ blank Max Device templates. Do not hand-roll a generic AMXD container for instruments or MIDI effects; Live can load it as an audio effect, leaving MIDI synth tracks silent.

Do not hardcode macOS app bundle paths, Windows install paths, User Library paths, command/status temp paths, or Ableton point-release names. Use `ableton_paths.py` discovery, `ABLETON_USER_LIBRARY`, `ABLETON_MAX_DEVICE_TEMPLATE_DIR`, `ABLETON_LIVE_PATH`, or `ABLETON_MCP_STATE_DIR` when defaults are not enough.

After changing `m4l/agent_m4l_host.js`, rebuild/reinstall generated Agent M4L hosts before validating host-runtime behavior. Remote Script installation only updates `Ableton_Live_MCP`; it does not update companion JS already copied beside existing generated AMXDs, and hot-reloading a patch cannot replace stale host JS inside an already loaded Max device.

`ableton-live-mcp-validate --skip-live` checks generated and installed Agent M4L companion JS copies when matching `AgentM4L_*.amxd` files exist. Require `m4l_host.current: true` before testing fresh generated hosts; if stale or missing, run `ableton-live-mcp-sync-m4l-host` or rebuild the host before continuing.

The dynamic host exposes role I/O objects but must not impose fixed audio or MIDI pass-through patch cords. Generated specs must explicitly connect `plugin`/`midiin` to generated processing and then to `plugout`/`midiout` as needed, so the agent can build replacement effects, generators, pass-through processors, or MIDI transformers without hidden dry paths. Audio-capable hosts also expose static instance-scoped MSP buses named `agent_m4l_<instance>_audio_in_l`, `agent_m4l_<instance>_audio_in_r`, `agent_m4l_<instance>_audio_out_l`, and `agent_m4l_<instance>_audio_out_r`; prefer generated `receive~` objects for inputs and generated `send~` objects for outputs when validating audio effects, because Live device I/O must remain wired at AMXD load time. If role-level static I/O changes, rebuild and reload the host AMXD; dynamically adding another `plugout~` after load is not a reliable way to redefine Live device I/O.

MCP must remain general purpose. Do not restrict generated devices to fixed UI slots, templates, or preapproved layouts. The agent may generate arbitrary native M4L UI, arbitrary web UI, or a mix. Put Live device-view placement in each object's `presentation_rect`, use `box_attrs` only for box/presentation attributes, use object `messages` for runtime configuration when a Max object does not accept creation attributes reliably, and bind any native UI object to any generated parameter/object with `ui_bindings` or an object-level `ui_bind`/`bind` field.

Treat generated UI as an open design surface, not a knob bank. Native patches may use multisliders, pictslider-style XY controls, button matrices, piano keyboards, sequencer lanes, animated meters, waveform/scope displays, live.text controls, swatches, toggles, and other Max UI objects. Web patches may use canvas, SVG, WebGL/Three.js, piano rolls, reactive scenes, custom gestures, and generated assets. Keep the implementation free-form and device-specific while validating that every authored control or visualization is visible, unclipped, and meaningfully connected to device state or audio/MIDI telemetry.

For complex generated specs, run `live_agent_m4l_device` with `preflight_only: true` before touching Live, or with `preflight: true` on the real call. Preflight is advisory and must stay general purpose: use it to catch duplicate IDs, missing patch-cord endpoints, missing binding sources, oversized UDP hints, and unmaterialized web UI payloads, not to enforce fixed layouts or block unusual but valid designs. A clean preflight is not e2e validation; it only reduces avoidable Live round trips.

For value-only updates, preflight can recover the last patch from `command_file`/sidecar and verify set IDs against generated objects and binding targets before writing another command. Use it when iterating many controls or web gestures so unknown IDs fail locally instead of burning a Live command round trip.

For hot-reloading an existing generated device without a target track/ref, pass `load: false` so the MCP takes the direct command-file path instead of rebuilding or scheduling Live main-thread work. Use explicit `build: true` only when the goal is to create or refresh an AMXD wrapper without loading it; use a target track/ref when you want Live to also poke the device's `Agent Poll` parameter for lower-latency delivery.

When `wait_status` times out, inspect `timeout_reason`, `status_file_updated_after_command`, and `last_status_age_seconds` before retrying. If `timeout_reason` is `host_not_woken` or `status_file_updated_after_command` is `false`, the host did not wake for that command; check the selected/loaded device, Live lock/sleep state, Control Surface responsiveness, and whether visual capture is blank before sending more mutations.

Native and web UI binding sources must be agent-settable, not only user-settable. When a control has `ui_bind`/`ui_bindings`, validate both directions: move the UI source and confirm the target state changes, then send an agent `set` to the source ID and confirm the host reports `changed: 1`, updates the visible source, and writes the intended target value in status `state`.

If a native source control needs a nonstandard write-back message to stay visually synchronized, set `source_message`/`source_set_message` and optional `source_args` on the binding. Keep this per-binding and data-driven; do not special-case particular UI layouts or control types in the MCP.

For telemetry bindings from output-only Max objects such as meters, analyzers, MIDI parsers, or other probes, set `report: false` and leave the source non-settable, or explicitly use `source_settable: false`. Do not let restored target state write back into signal-rate objects such as `peakamp~`. The host suppresses/throttles non-command binding status writes after command acknowledgements and coalesces web state pushes so high-rate telemetry cannot mask a matching `status`/`set`/`reload` ack or flood Live's main thread, but agents should still keep telemetry quiet and use explicit status snapshots for validation.

Generated devices should not appear as skinny slivers in Live's device rack, and authored panels should not be clipped by a stale default presentation size. Size the device to the authored UI: provide meaningful `presentation_rect` bounds for native objects and web UI panels, or set explicit top-level `device_width`/`devicewidth` and `device_height`/`deviceheight` when the intended design needs more room. The host builder infers Max `devicewidth` and `openrect` bounds from those authored rectangles with padding, and the runtime host reports/applies the intended width/height during hot reloads. Keep the top-level Max `rect` and `openrect` origin-aligned to the device bounds; do not add off-origin window coordinates or extra hidden height to make room for internal patching objects, because that can push the visible M4L UI down/right inside Live. Internal objects may live outside the visible presentation via `patching_rect`; only authored `presentation_rect` geometry should define the Live device viewport. This sizing remains data-driven; do not introduce fixed templates or limit agents to preapproved panel layouts.

Preflight presentation geometry is advisory. Use it to catch obvious clipping risks such as presentation bounds exceeding the declared device width/height, very tall device viewports, or very wide device viewports that require visual capture in the actual chain context. Do not reject unusual free-form UIs solely because they are large, scrollable, horizontally wide, or use custom native/web layouts. Wide devices can be valid, but when testing them after other wide devices, Live's device-chain horizontal scroll and selected-device state can make a downstream panel appear as a skinny strip. For multi-device chains, plan the viewport budget for the actual chain if multiple generated devices need to be visible together, or validate each device in a fresh/clean context before making claims about that device's full UI.

Hot reload can update reported `device_width`/`device_height`, but Live may keep the previous device container width after shrinking an already-loaded generated device. Treat visual shrink validation as fresh-instance work: rebuild/reload or use a fresh host when reducing width, then capture the actual Live detail view. Growth, web panel moves, and object placement still require visual capture; do not infer success from status geometry alone.

For web UI, `webui` may be an object or array of objects; each entry can choose its own `id`, `presentation_rect`, `patching_rect`, object type, attrs, and local HTML file. `jweb~`/`jweb` are supported, with `jbrowser~`/`jbrowser` accepted as aliases for Live 12+. For instruments that respond to Live clips, route MIDI with `midiin` into `midiparse` and parse the note-list outlet; `notein` is not sufficient for this validation path. Validate in Live before calling the work done: device class matches role, native and/or web UI is visible and unclipped, controls can change device values, values can be changed by agent commands, instruments produce audio from MIDI, and hot reload does not interrupt playback.

Generated web UI should acknowledge basic readiness before expensive work. Use the injected `window.agentM4L.outlet(...)` helper or an equivalent retrying classic bootstrap script before loading large modules, WebGL scenes, models, or generated bundles; it queues readiness/error messages until the embedded Max bridge is available. Then report feature readiness such as `threeReady` separately and provide a fallback/error state instead of letting one import or renderer failure leave a blank panel.

Generated web UI should also provide a low-rate generic heartbeat such as `agent_web_tick` through `window.agentM4L.outlet(...)`; the host treats it only as a throttled wake hint for command-file polling and pending web reads. This must not constrain the visual design, and it must not become a high-frequency animation or audio telemetry path.

Generated web UI should react through the generic host state channel, not bespoke templates. Listen for `agentm4lstate` events, read `window.agentM4L.state`, or set `window.agentM4L.onstate = (state) => ...`; the host still sends a legacy `state` inlet message for pages that use `max.bindInlet`. Use this for audio-reactive Three.js/WebGL scenes, piano rolls, sequencers, meters, and other custom panels that need to follow Max telemetry or parameter changes.

Creative web panels must budget their own renderer work. Prefer event-driven redraws or cap canvas/WebGL/Three.js loops around 24-30 FPS unless the validation specifically needs a higher frame rate, and pause or lower the rate when the visual is idle, transport is stopped, or no recent audio/MIDI telemetry has changed. A beautiful panel that keeps Live or Max Helper renderers hot while silent is a reliability failure, even if command delivery still works.

For audio-reactive web visuals, validation must prove reactivity to real audio telemetry, not only parameter changes. Drive web state from an audio probe such as `peakamp~`/meter/analyzer via a non-settable telemetry binding, then compare at least two states caused by changing the actual audio source path, such as upstream instrument/device on versus off or signal versus silence. Require both status evidence that the audio-derived telemetry changed and nonblank Ableton visual captures showing the web visual changed.

For audio-effect validation, prefer the cleanest available source/silence toggle over fighting transport mechanics. If arrangement imports or timeline seeks behave unpredictably in a busy set, load the generated effect on the master track or another known audio path fed by an already-playing instrument/clip, then compare telemetry and captures with upstream tracks unmuted versus muted/stopped. This validates the effect and web visual reactivity without depending on brittle arrangement-playhead setup.

Keep stable `webui.id` values across hot reloads when the design is an evolution of the same panel. The generated host reuses an existing browser object for a matching stable id/object type to avoid CEF churn and playback-visible reload failures in stressed sets; set `reuse: false` when changing a web panel's object type, dimensions, `presentation_rect`, or other geometry that Live/Max may not reliably refresh in place. This is a lifecycle hint, not a layout restriction.

If a freshly built generated AMXD reports a `load_error` such as "Device ... not found" but the build and install paths succeeded, treat it as a Live browser indexing delay before treating it as a generation failure. Retry with a longer `load_retry_timeout`, or use `live_browser_search` in `user_library` followed by `live_browser_load` once the item appears.

Use the host's `web_read_scheduled`, `web_read_attempts`, `web_read_pending`, `web_loaded`, `web_url`, `web_title`, and per-panel `web_<id>_loaded`, `web_<id>_read_attempts`, `web_<id>_url`, and `web_<id>_title` status state to distinguish host delivery, browser load, and custom JavaScript readiness. The host tags browser messages and retries reads under load; if attempts exhaust after selecting the device and showing Detail/Device Chain, the embedded browser itself did not finish loading, so inspect the Max/CEF logs and active renderer process count before debugging the generated HTML.

`webui_read` status events are diagnostic delivery checkpoints, not proof that the browser page ran. Treat `webui_read` as evidence that the host attempted a `readfile`/`read`; require `web_loaded`, panel-specific loaded/ready/title/url state, or custom readiness telemetry before considering a web UI validated.

Once a panel reports URL/title/ready/error telemetry, the host should clear any remaining scheduled read retries for that panel so `web_read_pending` only reflects unresolved browser loads. If `web_loaded` is true but pending reads keep climbing for the same panel, treat that as stale retry bookkeeping to fix before using the status as a validation signal.

When `wait_status` returns `reload_seen: true` with `webui_status: read_exhausted`, the generated patch did hot reload, but the embedded browser did not acknowledge after the read retry series. Treat that as a web UI validation failure, not as a general M4L command-delivery failure.

For generated MIDI effects, validate the transformed MIDI itself, not only downstream audio. Put the MIDI effect before a known-good instrument, expose compact telemetry with `ui_bind` on message-rate objects such as input pitch, output pitch, gate, or velocity, and verify both the MIDI effect status and the downstream instrument status agree on the transformed note values while the track meters are nonzero.

For generated instrument tests, prefer `live_clip_add_notes` targeting an empty clip slot with `create_clip_length`, `clip_name`, and optional `fire` over arbitrary `live_exec` note-writing code. This keeps MIDI setup JSON-safe, bounded, and easier to reason about after a sent-call timeout.

For repeated ad-hoc instrument tests that need a clean pattern in the same Session slot, prefer `replace_existing_clip: true` with `create_clip_length` over `clear: true`; replacing the slot clip avoids note-clear APIs and reduces the chance of Live showing the legacy MIDI Remote Script warning modal.

If Live shows the modal warning that a custom MIDI Remote Script uses an older process to modify MIDI notes, treat that as a validation blocker and preserve it in notes. Do not click through automatically. Prefer the extended note APIs through `live_clip_add_notes`; only pass `allow_legacy_note_api: true` for deliberate compatibility testing in a disposable set.

`live_exec` and `live_eval` reject obsolete MIDI note methods such as `set_notes`, `get_notes`, `remove_notes`, and selected-note APIs by default. Do not bypass this for ordinary generated-device work; use the JSON-safe note helpers instead. `allow_legacy_note_api: true` is only for deliberate compatibility probes in disposable sets.

MIDI note helper responses should report `note_api: "extended"` during normal validation. If a call reports `note_api: "legacy"` or refuses a legacy note API path, treat that as compatibility/debug evidence, not a green e2e signal for Live 12+ generated-device tests.

For rich web UI, put large libraries, models, images, fonts, or generated bundles in the webui `assets` map/list and reference the written relative files from HTML/JS. Asset entries may carry inline `content`/`text`/`base64` or copy bytes from a local `source_path`/`file_path`, which lets generated Three.js/WebGL panels bundle real local modules without stuffing the command JSON. After materialization, command patches should carry only paths and small metadata, not bulky `html`, `css`, `js`, `controls`, or asset source content. This keeps creative UIs such as Three.js scenes practical without making M4L command files slow or fragile.

When an existing local `html_path`/`path` is supplied with source assets, materialize those assets next to that HTML file so ordinary relative references such as `lib/three.module.js`, textures, models, and generated bundles resolve inside `jweb`/`jbrowser`. Use the generated web UI directory only when the HTML is generated by the agent or the target path is a URL.

When stress-testing many simultaneous web UI devices, a fresh `jweb` panel can fail to start while already-instantiated panels keep running. Before blaming the generated patch, select the target track/device and show Detail/Device Chain; if status state still does not move, retry in a less-crowded set or reuse an already-active host, and record that separately from command-delivery failures.

If an OS sample of a hung Live process shows Max CEF web-message handling followed by `jspatcher_remove`/`cweb_free`, treat the loaded generated host as stale or in an unsafe browser-teardown path. Stop sending M4L reload/set commands, preserve the sample path in notes, recover Live only with user authorization, then rebuild/reinstall the generated host and replace loaded stale devices. Current generated-host status reports include `host_runtime_version`; use it as evidence that the loaded device is running the expected host code, not just that files on disk are current.

For high-rate web UI gestures, animation clocks, piano rolls, sequencers, and meters, send `set_silent`/`param_silent` or batched `set_many_silent`/`param_many_silent` from the web panel instead of `set`/`param`; then use explicit status commands for validation snapshots. Silent updates also skip host-to-web state echo unless a value item sets `push_state`/`pushState` or `echo_state`/`echoState`. Use normal `set` when an event should acknowledge through the status file.

For creative state that is naturally a list or object, keep it generic: route arrays to Max objects with `set_message` or `list_message`, and route JSON-like objects with `object_message`/`json_message` when a specific Max object expects serialized data. Do not flatten step patterns, piano-roll notes, or custom gesture payloads into unrelated scalar controls just to fit a knob model.

For long generated-device soaks, track transport state separately from command/device failures. If Live transport stops, relaunch the target clip and count that as a transport event; do not let every later silent meter sample inflate the device failure count. Keep command acknowledgements, connection errors, transport stops, restarts, and meter readings as separate counters.

In heavy generated sets, timeline seeks and repeated `transport play` with `time: 0` can be much slower than compact status/value commands. Prefer checking transport status, using continue/play without a seek, or relaunching only the target clip when possible; reserve timeline resets for tests that specifically need them and give those calls a realistic timeout.

Bridge calls in heavily stressed Live sets can take tens of seconds even when Live eventually responds. Treat short socket timeouts as inconclusive under load; retry with a longer timeout and record bridge latency separately from generated-device failures.

For bridge health checks in a stressed set, use an explicit longer wait. If the active MCP client has a stale `live_ping` schema that does not expose `timeout`, call `live_batch` with one `ping` operation and `timeout` around 45 seconds before concluding the bridge is unavailable.

For parameterized MSP patches, do not rely on sending agent values directly into signal-processing objects like `*~`, filters, or `plugout~`. Create explicit message-rate controls such as `flonum`, `live.dial`, or other UI/message objects, bind agent/web/native controls to those IDs, and patch their outlets to the intended MSP control inlets. Avoid generated IDs that can collide with role I/O names or host/static Max objects, such as `plugin`, `plugout`, `midiin`, `midiout`, `js`, `script`, `status`, or generic `out`.

Use `jbrowser~`/`jbrowser` as compatibility aliases for control-panel web UI. If a generated device explicitly needs web-audio signal outlets, request `jweb~` deliberately and wire signal/message outlets in the generated patch instead of assuming the control-panel default.

When validating a generated M4L command, pass `wait_status: true` so the bridge returns the host's reload/set status in the same tool call instead of using separate sleeps and file reads. For web UI updates, omit `status_timeout` unless deliberately probing latency; the MCP server uses a longer default so the web-read retry series can finish or report terminal exhaustion. For value-only/non-web commands, a short explicit timeout is still appropriate. Treat the command file as the reliable delivery path for all M4L commands; UDP is only a low-latency hint because multiple loaded Max devices on one UDP port are not a sufficient reliability guarantee.

Use `status_detail: "summary"` or `compact_status: true` when you only need command acknowledgement, web-read diagnostics, dimensions, and binding source/target metadata. Use the default full status when validating exact generated state values.

When validating a small number of audio/MIDI telemetry values, combine `compact_status: true` with `status_state_keys` such as `["level_meter"]` so the response includes only the requested state values plus standard web/wake diagnostics. Do not read or print the full status JSON just to prove one meter or probe changed.

Use `compact_result: true` or `result_detail: "summary"` for iterative generated-device work that only needs command proof, built file paths, load state, web UI materialization, preflight, status, and a short track/device preview. This is especially useful for creative web/native devices whose source specs, assets, or Live track summaries would otherwise dominate the MCP response.

Generated hosts should use deterministic per-instance `udpreceive` ports derived from the instance id, not one shared M4L port. Value-only UDP hints should stay slim and omit recovery patches; the command file must still contain the full patch/spec so JS reload and set reopen recovery keep working. Large generated update payloads may skip UDP entirely and rely on the host's command-file poll rather than failing the command with an OS datagram-size error.

For generated `jweb`/`jbrowser~` panels, issue the first `read` immediately after object creation; use scheduled retries only after that first read. In stressed sets, Max JS `Task` scheduling can lag or stall, so the initial web UI load must not depend solely on a later task callback.

Pending web UI reads should also be serviced by ordinary device activity such as UI bindings, value updates, MIDI wakes, and audio meter telemetry, but activity wakes must respect retry backoff and must not collapse all retries into one burst. Do not make browser startup depend on a single timer path; creative devices with reactive meters or sequencers should help wake their own web panel while remaining fully general purpose.

The command-file poll path must also service due web UI reads, even when no command file exists or no new command is applied. Browser retry progress should not require a separate Max JS `Task` to fire in stressed sets.

When scheduling browser read retries, arm a due-time task without repeatedly canceling/rescheduling the same `Task` from inside its callback. Max JS `Task` callbacks can be fragile under load; duplicate wakes are safer than stranding all later retries, as long as each wake checks due time before reading.

Pre-arm the full web-read retry series with per-attempt due times when a panel is created. Do not rely on scheduling attempt N+1 from inside attempt N; in stressed sets that callback chain can stop after one fallback attempt even while the host is otherwise alive.

Each pre-armed retry should have its own scheduled wake. Keeping only one future wake can still strand the final retry in overloaded sets; multiple due-time-checked wakes are acceptable because the read queue skips loaded panels and not-yet-due attempts.

When a generated web panel has a local `html_path`, pass that filesystem path to the Max `readfile` message first and keep `url`/`html_url` available for fallback `read` attempts. The host alternates to URL-style `read` on retries when a local page does not acknowledge, because different Max/CEF states can prefer different load paths.

For direct generated audio-effect graphs, connect `plugin` outlet 0/1 through the generated processing objects and into `plugout` inlet 0/1. The static `audio-in-l`, `audio-in-r`, `audio-out-l`, and `audio-out-r` objects are named send/receive bus endpoints for cross-patcher routing, not direct signal sources/destinations for a simple pass-through chain.

When `live_agent_m4l_device` does not need to build or resolve a Live track/device, the MCP server may write the command file directly and then wait for host status. This is the preferred fast path for value-only `set`, `status`, `clear`, and `build: false` hot-reload commands.

For iterative generated M4L updates, reuse the installed role host. `live_agent_m4l_device` skips rebuilds by default for `set`, `status`, `clear`, and value-only calls; pass `build: true` only when creating/replacing the host AMXD.

Generated M4L UI remains freeform: native, web, hybrid, Three.js, piano rolls, sequencers, and custom layouts are all allowed. Reliability still requires lifecycle discipline: reuse stable instance IDs and existing hosts during iteration, and clear or remove stale test devices/tracks before creating more web UI instances. Use `live_agent_m4l_cleanup` without `delete` for a dry run; ask before rerunning it with `delete: true`. Each `jweb`/`jbrowser` view can create renderer work in Max; if OS diagnostics show many `Max Helper (Renderer)` processes or high Live/Max renderer CPU, stop creating new web views and recover Live before continuing.

Hot reloads should preserve current values for matching generated parameter IDs. When changing a patch/spec, keep stable IDs for controls and controlled objects that should retain state; change IDs deliberately when the new design should reset a value.

In large or stressed Live sets, do not pass short `timeout` values such as `10` to `live_batch`, transport, browser, or generated-device operations unless deliberately probing latency. The Remote Script and Python client default to a longer main-thread timeout so ordinary playback/control commands can survive Live UI stalls.

Short `timeout` values are non-strict by default: the Remote Script and Python client clamp them to the default main-thread wait to avoid accidental failures in stressed sets. Use `strict_timeout: true` only for deliberate latency probes where a timeout failure is the expected signal.

The Remote Script must serialize scheduled Live main-thread calls. If one Live API request is already scheduled or running, later Live API requests should fail fast instead of queueing more callbacks behind it; `live_bridge_status` must still answer from the socket thread. After a Live main-thread timeout, the Remote Script enters a short stall cooldown, and if the timed-out callback had already started it keeps the in-flight gate closed until that callback returns. For ordinary work, recover Live first and then rerun validation; use `force_main_thread_probe` only for a deliberate bounded health probe, not for mutations.

Do not run Live API probes, validation, smoke/regression harnesses, generated-device loads, or transport commands in parallel. Parallel local file reads and local tests are fine, but concurrent Live main-thread calls can trip the in-flight guard and produce a false `live_mutations_safe: false` result even while each command would pass sequentially.

A build/install can succeed before Live's browser has indexed the new AMXD. In that case `live_agent_m4l_device` may return `loaded: false` with `load_error` while still returning `built`, `command_file`, and `status_file`. Treat that as a recoverable indexing/load condition: reuse an already loaded compatible host for immediate validation, or retry loading after the browser catches up.

When a fresh generated AMXD is built and loaded in one `live_agent_m4l_device` call, the Remote Script should prefer `track.insert_device(device_name, index)` when Live exposes it, with browser item loading as fallback. Browser loading can block Live's main thread in overloaded sets after the request has already started, so repeated fresh loads are higher risk than reusing an existing generated host. The MCP server should retry failed fresh loads client-side for a short window instead of sleeping inside Live's Remote Script. Use `load_retry_timeout` and `load_retry_interval` only for indexing/load delays; value-only `set` calls should continue to skip rebuilds and load retries.

Patch/update commands write the generated spec to the command file and recovery sidecar. Normal `set` and `status` commands should keep the command file slim and store the most recent patch/spec in `<command_file>.recovery.json`, so value changes do not force Max to repeatedly read and parse bulky UI specs. Commands need unique IDs; otherwise Max's file poll dedupe can ignore a repeated status or value command. If a JS reload resets runtime state, the host should recover from the sidecar patch before handling the next `set` or `status`; if status still reports `dynamic_objects: 0`, reload a patch/update command before continuing value-only validation.

When a host AMXD is loaded after its command file has already been written, the JS `loadbang` should explicitly start the static low-priority `metro` poller and consume the prewritten command file once. The host should also include `live.thisdevice` and self-starting metro attributes as secondary startup paths. The same polling bang should service pending web reads so browser retries do not depend solely on Max JS `Task` scheduling. Fallback pollers must be low-rate and activity wakes must be throttled; a generated set with several devices should not hammer the command file from every timer, MIDI byte, and signal edge.

Generated host AMXDs should include a static `filewatch <command_file>` object started from `loadbang`/`live.thisdevice`. Send the command-file path into `filewatch` explicitly before sending `1`, and route its output through a tagged JS path such as `prepend __filewatch` so status state can prove whether a file-change wakeup fired. Treat `filewatch` as one observable wake hint, not as proof of delivery; in stressed sets it has been observed not to fire after external command-file writes. Filewatch diagnostics must not write a separate status-file event after a command ack, because that can overwrite the matching `set`/`status`/`reload` acknowledgement and cause false `event_mismatch` timeouts.

Generated hosts should also use activity already present in Live as a command-file wake source. For audio-effect and instrument hosts, include a low-rate signal-edge wake path such as `phasor~` -> `>~` -> `edge~` -> `prepend __signal_wake` -> JS, and keep that signal branch computed by routing it through a zero-gain signal sink into `plugout~`; for instrument and MIDI-effect hosts, route static `midiin` through a tagged message such as `prepend __midi_wake` into JS. These wake messages must only poll the command file and pending web reads through the throttled activity-wake path; they must not constrain or reinterpret the generated creative patch, consume MIDI, impose a pass-through, or report continuously. Status should expose the last wake source and skipped-wake count for validation.

After a generated spec loads, the host should also create and explicitly start a hidden dynamic low-priority `qmetro` connected back into the JS object as a secondary wakeup path. Keep it slow enough to be a recovery path, not a steady main-thread load source; UDP, filewatch, and the hidden command-trigger parameter are the fast hints. Do not assume any wake mechanism is sufficient in a stressed Live set; filewatch, timers, UDP, signal-edge wakes, MIDI wakes, and Live parameter writes have all been observed to miss follow-up command files after load. Every follow-up `set`/`status` must be validated by a matching status `command_id`; if it does not ack, stop claiming hot reload works in that set and ask for permission to reload/simplify the set or load a fresh host in a cleaner validation set.

When `wait_status` times out, preserve the expected command id/event, mismatch reason, and a compact last-status summary in the MCP response. Do not bury this failure behind a generic timeout; agents need the last acked command id, wake-source telemetry, and web-read state to decide whether to retry, rebuild, or ask for a cleaner validation set.

Generated hosts must not route full JSON status payloads to Max `print` objects or the Max Console. Status is delivered through the status file; any optional outlet/console diagnostic must be one compact line with event, command id, and small counts only. A `printobj: Too many lines ... output truncated` warning is a validation failure and means console/status output must be reduced before continuing.

Generated hosts should write compact status JSON without fixed-size padding. The MCP server intentionally tolerates stale trailing bytes from Max file writes, so do not reintroduce large padded status files as a truncation workaround; they slow down status polling and increase token pressure during creative UI iteration.

After removing a console/status sink from the host wrapper, run `ableton-live-mcp-sync-m4l-host` before validating. The sync command updates companion JS and repairs stale generated/installed AgentM4L wrapper AMXDs/maxpats that still contain `print AgentM4L_*`; `ableton-live-mcp-validate --skip-live` should report no `stale_wrappers` before Live e2e testing resumes.

Setting a generated `live.numbox`/`live.*` parameter through the Live API does not reliably emit from the UI object's Max outlet. If the host observes Live parameters, the observer path must remain generic: observe the generated device's current parameters and route names that match `ui_bindings` through the same binding code as native UI gestures. Treat observer output as opportunistic state sync until validated by a status update.

Direct JS `LiveAPI` parameter observers are opt-in only via generated spec flags such as `live_api_observers`; do not enable them for ordinary generated-device validation. Never refresh direct JS `LiveAPI` observers from fallback poll ticks, MIDI wakes, signal wakes, or browser-read retries. Cycling '74 documents that `live.thisdevice` is the readiness signal for the Live API and that JS LiveAPI work must not run in Max's high-priority scheduler. Use static `live.path this_device` and `live.path this_device parameters 1` objects, bang them from `loadbang`/`live.thisdevice`, and keep explicit `path ...` messages as a compatibility fallback; do not hardcode track or device indexes.

Generated hosts may also create Max-object `live.path`/`live.observer value` observers for Live parameters that need to react to Live API writes. Observe the hidden trigger at `this_device parameters 1`; for generated bound `live.*` controls, assign observer indexes from the generated parameter creation order and route observer output through the same `ui_bindings` code as native UI gestures. This must remain general purpose and must be validated per host because Live parameter notifications may produce initial values without reliably waking later command-file reads.

When routing Max-object `live.observer value` output into generated bindings, normalize observer atoms first. Max may output `value <number>` rather than a bare number; the binding layer must apply the observed numeric value, not the literal symbol.

Generated hosts also expose a hidden Live parameter used only to try to wake the command-file poller; Live may surface it as `Agent Poll`, `Agent M4L Poll`, or the Max box name `command-trigger`. After writing a command file, the Remote Script may toggle one of those names on the target generated device when a target track is available, but the command is not successful until the status file reports the matching `command_id`.
