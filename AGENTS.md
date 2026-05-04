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

## Max for Live devices
This repo includes `m4l/AgentAudioTap.amxd`, a Max for Live audio effect that lets an agent record the audio signal at the device's insertion point for analysis. Build/install it with:

```sh
.venv/bin/python scripts/build_agent_audio_tap.py --install
```
