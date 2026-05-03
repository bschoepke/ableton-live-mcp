from __future__ import annotations

import json
import sys

from bridge import AbletonBridgeClient, AbletonBridgeError


def main() -> int:
    client = AbletonBridgeClient()
    checks = [
        ("ping", "ping", {}),
        ("song", "get", {"ref": {"path": "live_set"}, "properties": ["tempo", "signature_numerator", "signature_denominator"], "children": ["tracks", "scenes"], "child_limit": 5}),
        ("application", "eval", {"expr": "app.get_major_version() if hasattr(app, 'get_major_version') else app.get_version_string().split('.')[0]"}),
    ]
    results = {}
    try:
        for name, method, params in checks:
            results[name] = client.request(method, params)
    except AbletonBridgeError as exc:
        print(f"Ableton Live MCP validation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(results, indent=2, sort_keys=True))
    version_text = json.dumps(results).lower()
    if '"major": 12' not in version_text and '"12' not in version_text and "live 12" not in version_text:
        print("Validation reached Ableton, but could not confirm Ableton Live 12 from version data.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
