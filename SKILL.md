---
name: ableton-live-mcp
description: Use when working in the ableton-live-mcp repository or driving Ableton Live through its MCP bridge, especially for Windows/PowerShell command execution, Live validation, or Python bridge scripts.
---

# Ableton Live MCP

## Windows PowerShell Python

On Windows/PowerShell, do not use Bash heredocs such as `python - <<'PY'`; PowerShell treats `<` as redirection and the command fails before Python runs.

For multi-line Python, pipe a single-quoted here-string into Python:

```powershell
@'
from bridge import AbletonBridgeClient
client = AbletonBridgeClient()
print(client.request("ping", {"timeout": 10}))
'@ | python -
```

For short one-liners, `python -c "..."` is fine, but avoid complex nested quotes for JSON or multiline Live scripts. Use the here-string form for Live bridge scripts, generated code strings, or anything with nested quotes.
