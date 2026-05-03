from __future__ import annotations

import os
import sys


DEBUG_ENV_VAR = "ABLETON_LIVE_MCP_DEBUG"


def debug_enabled() -> bool:
    return os.environ.get(DEBUG_ENV_VAR, "").strip().lower() in {"1", "true", "yes", "on"}


def require_debug_cli(command: str) -> bool:
    if debug_enabled():
        return True
    print(
        "%s is a debug/development command. Set %s=1 to run it from a source checkout or debug build."
        % (command, DEBUG_ENV_VAR),
        file=sys.stderr,
    )
    return False
