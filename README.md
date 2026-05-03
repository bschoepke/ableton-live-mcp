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

Publish releases from a clean git archive or build artifact, not by zipping a working directory. Local generated audio and cache folders are ignored but may still exist in development workspaces.

## MCP Tools

- `live_get`: resolve a Live API path or object id and return selected properties, children, and compact object ids; use `detail: true` for `canonical_path`/`repr`.
- `live_set`: set a writable property.
- `live_call`: call an object method with positional and keyword arguments.
- `live_children`: list children from an object.
- `live_batch`: run several generic bridge operations in one Live main-thread request.
- `live_browser_roots`: list available `app.browser` root categories.
- `live_browser_search`: bounded search over any available `app.browser` roots.
- `live_browser_load`: load a browser item returned by `live_browser_search`.
- `live_eval`: evaluate a Python expression with `song`, `app`, `obj`, and `Live` bindings.
- `live_observe`: add or remove a listener for a property; events are retained by the bridge.
- `live_events`: drain retained listener events.
- `live_ping`: report bridge and Live version details.

`live_eval` is powerful by design. Bind the bridge only to `127.0.0.1`, use it for local agent automation, and do not expose the port to untrusted networks.

## Validation

Use `ableton-object-mcp-validate` for a quick connection/version check. Use `ableton-object-mcp-smoke` for broader object-model coverage against a running Live instance: bounded `get`/`children`, `eval`, `batch`, browser roots/search, plugin root discovery, listeners, and event draining. The smoke suite is intentionally non-destructive; it does not create tracks, clips, devices, or modify the open set.

## Agent Usage Guide

This MCP is intentionally general. Agents should use the full Live object model exposed by the installed Ableton Live version: any available object, property, method, browser item, device, clip, track, scene, listener, or Arrangement API can be composed through the generic tools. The guidance below is meant to make common workflows reliable, not to limit what the model may do.

Prefer Ableton library content before generating assets. When a user asks for instruments, drums, samples, effects, loops, plugins, or genre material, start with `live_browser_roots` and `live_browser_search`, then load results with `live_browser_load` or inspect/load them manually through the generic object model. Browser contents vary by Live version, SKU, installed packs, user folders, indexed third-party audio plugins, and indexed third-party content, so discover what is available at runtime and fall back gracefully. Generate synthetic audio only when the user asks for it, when no suitable library content is available, or when the task specifically needs custom rendered material.

Common workflows that work well:

- Batch multi-step edits with `live_eval` and `exec(..., globals())`, returning a compact summary instead of large object dumps.
- Batch independent generic operations with `live_batch` when the work does not need custom Python code.
- Discover library content with `live_browser_search` using bounded roots, depth, and result limits. Search results include reusable BrowserItem ids.
- Discover third-party audio plugins through the `plugins` browser root. Plugin formats/vendors are whatever the local Live install indexes, for example AU/VST roots on that machine.
- Load devices or presets by traversing `app.browser` to a loadable `BrowserItem`, selecting the target track with `song.view.selected_track`, then calling `app.browser.load_item(item)`.
- Create Session MIDI clips with `clip_slot.create_clip(length)` and add notes with `Live.Clip.MidiNoteSpecification(pitch, start_time, duration, velocity, mute)`.
- Place existing Session clips into the timeline with `track.duplicate_clip_to_arrangement(slot.clip, destination_time)`.
- Create Arrangement audio clips from local files with `track.create_audio_clip(path, destination_time)`.
- Search browser categories with bounded traversal and name filters before assuming a sample or preset exists.
- Return only stable summaries after large edits, for example track names, device names, clip counts, and selected clip names.

Common errors to avoid:

- `live_eval` uses Python `eval`; assignment statements or multi-line imperative code must be wrapped in `exec(...)`.
- `ClipSlot.create_clip` and other numeric Live API arguments require numbers, not stringified numbers.
- `Simpler.sample` is readable through the Live API but not directly settable; load samples through browser workflows when possible, or use audio clips as a fallback.
- Browser roots differ in shape: some expose `iter_children`, while vectors such as `user_folders` may need normal iteration.
- Cue marker creation and naming may not be reliable through every bridge/session state; arrange actual clips first and treat cue points as optional.
- Do not assume Suite-only devices, third-party plugins, specific packs, or large factory libraries exist. Discover, choose the best available item, and keep the set usable if the preferred item is missing.
- Avoid broad recursive browser dumps and full device parameter dumps unless required; they are slow and expensive.
- `live_browser_search` is a convenience layer over `app.browser`; use `live_eval` for custom ranking, metadata, unusual browser roots, or workflows not covered by the search schema.
- Object summaries are compact by default. Set `detail: true` when `repr` or `canonical_path` is needed.
- Tracebacks are omitted by default to keep common Live API errors readable. Set `include_traceback: true` on the bridge request or `ABLETON_MCP_TRACEBACK=1` in the Python client environment when debugging.

Token and latency tips:

- Prefer one `live_eval` call for a coherent edit over many small `live_call` calls.
- Prefer `live_batch` for several ordinary `get`, `set`, `call`, `children`, or `eval` operations that should share one bridge round trip.
- Prefer `live_browser_search` over ad hoc recursive browser traversals for normal library lookup.
- Pass `roots: ["plugins"]` to search installed third-party audio plugins specifically.
- Ask `live_get` only for specific properties and children.
- Use `live_children.limit`, `live_get.child_limit`, or `live_get.children` as `{name: limit}` when inspecting large collections.
- Use `max_items` and `max_depth` to bound encoded `live_eval` and `live_call` results; use `-1` only when a full response is truly needed.
- When reading device parameters, filter by device and parameter names instead of returning every parameter.
- Return compact dictionaries/lists from `live_eval` rather than raw Live objects.
- Keep browser traversal bounded by depth, result count, and likely path/name filters.
