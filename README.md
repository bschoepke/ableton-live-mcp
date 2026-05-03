# Ableton Object MCP

This repo implements a general-purpose Model Context Protocol server for Ableton Live 12. It is intentionally not a catalog of narrow tools like `create_clip()`. Instead it exposes Live's object model through path resolution, object ids, property access, method calls, child traversal, listeners, and raw expression evaluation inside Live's Python control-surface environment.

## Architecture

- `ableton-object-mcp` is a stdio MCP server used by coding agents.
- `remote_scripts/Ableton_Object_MCP` is an Ableton Control Surface script that opens a localhost JSON RPC bridge.
- The MCP tools map to generic Live object-model operations, so an agent can compose arbitrary workflows supported by Live's APIs.

## Install

1. Install the Python package in the agent environment:

   ```sh
   python -m pip install ableton-object-mcp
   ```

   For local development from a source checkout, use:

   ```sh
   python -m pip install -e ".[dev]"
   ```

2. Install the Ableton Remote Script:

   ```sh
   ableton-object-mcp-install-remote-script
   ```

   This installs `Ableton_Object_MCP` into `~/Music/Ableton/User Library/Remote Scripts`. The package also ships an `AbletonMCP` alias for compatibility with earlier local installs; select only one Control Surface in Live, because both aliases bind the same local port.

3. Start Ableton Live 12, open Settings, and select `Ableton_Object_MCP` as a Control Surface.

4. Register the MCP server with your MCP client. The command is:

   ```sh
   ableton-object-mcp
   ```

   Example MCP client configuration:

   ```json
   {
     "mcpServers": {
       "ableton": {
         "command": "ableton-object-mcp"
       }
     }
   }
   ```

5. Validate the bridge:

   ```sh
   ableton-object-mcp-validate
   ```

6. Run the broader non-destructive smoke suite before publishing or debugging customer reports:

   ```sh
   ableton-object-mcp-smoke
   ```

7. Benchmark common non-destructive workflows when optimizing latency or token use:

   ```sh
   ableton-object-mcp-benchmark
   ```

8. Run destructive real-prompt audits only against disposable sets:

   ```sh
   ableton-object-mcp-prompt-audit --yes
   ```

Publish releases from a clean git archive or build artifact, not by zipping a working directory. Local generated audio and cache folders are ignored but may still exist in development workspaces.

## MCP Tools

- `live_get`: resolve a Live API path or object id and return selected properties, children, and compact object ids; use `detail: true` for `canonical_path`/`repr`.
- `live_set_summary`: return a compact non-destructive summary of the open project, including tracks, devices, Session clips, optional Arrangement clips, return tracks, and master devices.
- `live_set`: set a writable property.
- `live_call`: call an object method with positional and keyword arguments.
- `live_children`: list children from an object.
- `live_device_parameters`: list compact device parameter metadata and return parameter ids for deliberate `live_set` updates.
- `live_clip_notes`: list MIDI notes from a clip with note ids, pitch, time, duration, and velocity.
- `live_clip_update_notes`: update existing MIDI notes by `note_id`.
- `live_clip_envelope`: inspect or edit one clip automation envelope for a parameter.
- `live_clip_warp_markers`: inspect or edit audio clip warp state and markers.
- `live_batch`: run several generic bridge operations in one Live main-thread request.
- `live_browser_roots`: list available `app.browser` root categories.
- `live_browser_capabilities`: list available browser roots, filter types, and whether the installed Live build exposes semantic/similarity search through the Python object model.
- `live_browser_search`: bounded search over any available `app.browser` roots.
- `live_browser_load`: load a browser item returned by `live_browser_search`.
- `live_browser_preview`: preview a browser item or stop previewing.
- `live_eval`: evaluate a Python expression with `song`, `app`, `obj`, and `Live` bindings.
- `live_observe`: add or remove a listener for a property; events are retained by the bridge.
- `live_events`: drain retained listener events.
- `live_ping`: report bridge and Live version details.

`live_eval` is powerful by design. Bind the bridge only to `127.0.0.1`, use it for local agent automation, and do not expose the port to untrusted networks.

## Validation

Use `ableton-object-mcp-validate` for a quick connection/version check. Use `ableton-object-mcp-smoke` for broader object-model coverage against a running Live instance: bounded `get`/`children`, `eval`, `batch`, browser roots/search, plugin root discovery, listeners, and event draining. Use `ableton-object-mcp-benchmark` to record latency and payload sizes for common non-destructive workflows, including compact existing-set summaries and optional project-content probes. The smoke and benchmark suites are intentionally non-destructive; they do not create tracks, clips, devices, or modify the open set. Use `ableton-object-mcp-prompt-audit --yes` for destructive end-to-end prompt workflows such as creating an EDM arrangement, loading an installed sample, editing existing MIDI notes, editing automation/warp state, and discovering plugins.

## Agent Usage Guide

This MCP is intentionally general. Agents should use the full Live object model exposed by the installed Ableton Live version: any available object, property, method, browser item, device, clip, track, scene, listener, or Arrangement API can be composed through the generic tools. The guidance below is meant to make common workflows reliable, not to limit what the model may do.

Prefer Ableton library content before generating assets. When a user asks for instruments, drums, samples, effects, loops, plugins, or genre material, start with `live_browser_roots` and `live_browser_search`, then load results with `live_browser_load` or inspect/load them manually through the generic object model. Browser contents vary by Live version, SKU, installed packs, user folders, indexed third-party audio plugins, and indexed third-party content, so discover what is available at runtime and fall back gracefully. Generate synthetic audio only when the user asks for it, when no suitable library content is available, or when the task specifically needs custom rendered material.

Common workflows that work well:

- Batch multi-step edits with `live_exec`, setting `result` to a compact summary instead of returning large object dumps.
- For existing-project prompts, start with `live_set_summary` to understand the current set before editing in place.
- If the prompt names a track, pass `track_query` to `live_set_summary` to avoid returning unrelated tracks.
- For Arrangement-editing prompts, request `arrangement_clip_limit` in `live_set_summary` so clip names, ids, and positions are available without a custom object walk.
- Batch independent generic operations with `live_batch` when the work does not need custom Python code.
- Discover library content with `live_browser_search` using bounded roots, depth, and result limits. Search results include reusable BrowserItem ids.
- Preview candidate samples or presets with `live_browser_preview` before loading them, then call it with `stop: true` when done.
- Discover third-party audio plugins through the `plugins` browser root. Plugin formats/vendors are whatever the local Live install indexes, for example AU/VST roots on that machine.
- Load devices or presets by traversing `app.browser` to a loadable `BrowserItem`, selecting the target track with `song.view.selected_track`, then calling `app.browser.load_item(item)`.
- Load individual samples the same way: create/select a MIDI track, load the sample `BrowserItem`, then create MIDI notes for the generated sample device. This is the reliable path for “put this sample in Simpler” style prompts.
- Inspect device parameters with `live_device_parameters` before setting them. Use returned parameter ids with `live_set` on `value`, and verify `display`/`display_value` afterward. Many Live parameters expose normalized internal values even when the UI shows dB, Hz, ms, or percent.
- For existing MIDI clip edits, inspect with `live_clip_notes` and update with `live_clip_update_notes`. When using raw `live_exec`, mutate the `MidiNote` objects returned by `clip.get_all_notes_extended()` and pass that same vector to `clip.apply_note_modifications`; do not construct `MidiNoteSpecification` for existing notes.
- For existing clip automation edits, get the target parameter id from `live_device_parameters` or `live_get`, then use `live_clip_envelope` to inspect, create, clear a range, and insert step automation.
- For existing audio clip warp edits, use `live_clip_warp_markers`. Raw object-model calls require `Live.Clip.WarpMarker(sample_time, beat_time)` for new markers and `clip.move_warp_marker(marker_beat_time, beat_time_delta)` for moving existing markers.
- Create Session MIDI clips with `clip_slot.create_clip(length)` and add notes with `Live.Clip.MidiNoteSpecification(pitch, start_time, duration, velocity, mute)`.
- Place existing Session clips into the timeline with `track.duplicate_clip_to_arrangement(slot.clip, destination_time)`.
- Create Arrangement audio clips from local files with `track.create_audio_clip(path, destination_time)`.
- For generated WAV hooks that are not indexed in Live's browser, create Arrangement audio clips from the file path. Use browser loading for indexed library/user samples.
- Search browser categories with bounded traversal and name filters before assuming a sample or preset exists.
- Return only stable summaries after large edits, for example track names, device names, clip counts, and selected clip names.

Common errors to avoid:

- `live_eval` uses Python `eval`; use `live_exec` for assignment statements or multi-line imperative code.
- `ClipSlot.create_clip` and other numeric Live API arguments require numbers, not stringified numbers.
- `Simpler.sample` is readable through the Live API but not directly settable; load samples through browser workflows when possible, or use audio clips as a fallback.
- Device parameter `value` is not always in the same units shown in the UI. Avoid writing dB/Hz/ms values blindly; inspect min/max/display metadata and verify the display string after setting.
- Browser roots differ in shape: some expose `iter_children`, while vectors such as `user_folders` may need normal iteration.
- Cue marker creation and naming may not be reliable through every bridge/session state; arrange actual clips first and treat cue points as optional.
- Do not assume Suite-only devices, third-party plugins, specific packs, or large factory libraries exist. Discover, choose the best available item, and keep the set usable if the preferred item is missing.
- Avoid broad recursive browser dumps and full device parameter dumps unless required; they are slow and expensive.
- `live_browser_search` is a convenience layer over `app.browser`; use `live_eval` for custom ranking, metadata, unusual browser roots, or workflows not covered by the search schema.
- Live 12 Sound Similarity/Semantic Search is a Browser feature, but in Live 12.3.8 the Python object model exposed through Control Surfaces does not show semantic/similarity search methods on `app.browser`. Use `live_browser_capabilities` to check the current Live build. If future versions expose it, use the generic object-model tools to call it; otherwise fall back to tags/name/path browser search.
- Object summaries are compact by default. Set `detail: true` when `repr` or `canonical_path` is needed.
- Do not reuse raw `_live_ptr` values returned manually from `live_exec` as bridge ids. Use ids returned by bridge summaries such as `live_get`, `live_set_summary`, `live_device_parameters`, or `live_browser_search`.
- Tracebacks are omitted by default to keep common Live API errors readable. Set `include_traceback: true` on the bridge request or `ABLETON_MCP_TRACEBACK=1` in the Python client environment when debugging.

Token and latency tips:

- Prefer one `live_exec` call for a coherent edit over many small `live_call` calls.
- Prefer `live_batch` for several ordinary `get`, `set`, `call`, `children`, or `eval` operations that should share one bridge round trip.
- Batch related browser searches with `live_batch`; use `stop_on_limit: true` for exact built-in device lookups, add `stop_score: 1` for “first good sample/plugin match” lookups, and leave early stop false when global ranking matters.
- Prefer `live_browser_search` over ad hoc recursive browser traversals for normal library lookup.
- Pass `roots: ["plugins"]` to search installed third-party audio plugins specifically.
- Ask `live_get` only for specific properties and children.
- Use `live_children.limit`, `live_get.child_limit`, or `live_get.children` as `{name: limit}` when inspecting large collections.
- Use `max_items` and `max_depth` to bound encoded `live_eval` and `live_call` results; use `-1` only when a full response is truly needed.
- When reading device parameters, filter by device and parameter names instead of returning every parameter.
- Return compact dictionaries/lists from `live_eval` rather than raw Live objects.
- Keep browser traversal bounded by depth, result count, and likely path/name filters.
