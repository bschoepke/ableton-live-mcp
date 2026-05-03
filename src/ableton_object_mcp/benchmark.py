from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections.abc import Callable
from typing import Any

from .bridge import AbletonBridgeClient, AbletonBridgeError


RequestFactory = Callable[[], tuple[str, dict[str, Any]]]


def _response_size(value: Any) -> int:
    return len(json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8"))


def _measure(client: AbletonBridgeClient, name: str, request: RequestFactory, iterations: int) -> dict[str, Any]:
    samples: list[dict[str, Any]] = []
    for _index in range(iterations):
        method, params = request()
        start = time.perf_counter()
        result = client.request(method, params)
        elapsed_ms = (time.perf_counter() - start) * 1000
        samples.append({
            "ms": round(elapsed_ms, 3),
            "bytes": _response_size(result),
            "method": method,
        })
    timings = [sample["ms"] for sample in samples]
    sizes = [sample["bytes"] for sample in samples]
    return {
        "name": name,
        "ok": True,
        "iterations": iterations,
        "method": samples[0]["method"] if samples else None,
        "min_ms": min(timings),
        "median_ms": round(statistics.median(timings), 3),
        "max_ms": max(timings),
        "median_bytes": int(statistics.median(sizes)),
        "max_bytes": max(sizes),
    }


def _fail(name: str, exc: Exception) -> dict[str, Any]:
    return {"name": name, "ok": False, "error": str(exc)}


def _benchmarks(include_browser: bool) -> list[tuple[str, RequestFactory, bool]]:
    items: list[tuple[str, RequestFactory, bool]] = [
        ("ping", lambda: ("ping", {}), False),
        ("song_compact_get", lambda: ("get", {
            "ref": {"path": "live_set"},
            "properties": ["tempo", "signature_numerator", "signature_denominator", "current_song_time"],
            "children": {"tracks": 8, "scenes": 8, "return_tracks": 4},
        }), False),
        ("batch_status", lambda: ("batch", {
            "operations": [
                {"method": "get", "params": {"ref": {"path": "live_set"}, "properties": ["tempo"]}},
                {"method": "children", "params": {"ref": {"path": "live_set"}, "child": "tracks", "limit": 4}},
                {"method": "eval", "params": {"expr": "len(song.tracks), len(song.scenes), len(song.return_tracks)"}},
            ],
        }), False),
        ("arrangement_capability_summary", lambda: ("eval", {
            "expr": "sorted([n for n in dir(song) if 'track' in n.lower() or 'scene' in n.lower() or 'clip' in n.lower()])[:60]",
            "max_items": 80,
        }), False),
    ]
    if include_browser:
        items.extend([
            ("browser_roots", lambda: ("browser_roots", {}), False),
            ("browser_drum_search", lambda: ("browser_search", {
                "query": "drum",
                "roots": ["instruments", "drums"],
                "limit": 8,
                "max_depth": 5,
                "max_visited": 4000,
            }), False),
            ("browser_sample_search", lambda: ("browser_search", {
                "query": "cowbell",
                "roots": ["drums", "samples", "user_library"],
                "limit": 8,
                "max_depth": 7,
                "max_visited": 8000,
            }), True),
            ("browser_plugin_search", lambda: ("browser_search", {
                "query": "",
                "roots": ["plugins"],
                "limit": 8,
                "max_depth": 4,
                "max_visited": 2000,
                "include_folders": True,
                "loadable_only": False,
            }), True),
        ])
    return items


def run_benchmark(
    client: AbletonBridgeClient | None = None,
    *,
    iterations: int = 3,
    include_browser: bool = True,
) -> tuple[int, dict[str, Any]]:
    client = client or AbletonBridgeClient()
    checks: list[dict[str, Any]] = []
    for name, request, optional in _benchmarks(include_browser):
        try:
            checks.append(_measure(client, name, request, iterations))
        except Exception as exc:
            failed = _fail(name, exc)
            failed["optional"] = optional
            checks.append(failed)
    hard_failures = [check for check in checks if not check["ok"] and not check.get("optional")]
    medians = [check["median_ms"] for check in checks if check["ok"]]
    sizes = [check["median_bytes"] for check in checks if check["ok"]]
    output = {
        "ok": not hard_failures,
        "iterations": iterations,
        "checks": checks,
        "summary": {
            "total": len(checks),
            "passed": sum(1 for check in checks if check["ok"]),
            "failed": sum(1 for check in checks if not check["ok"]),
            "hard_failed": len(hard_failures),
            "median_of_medians_ms": round(statistics.median(medians), 3) if medians else None,
            "max_median_ms": max(medians) if medians else None,
            "max_median_bytes": max(sizes) if sizes else None,
        },
    }
    return (0 if output["ok"] else 1), output


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark common Ableton Object MCP workflows.")
    parser.add_argument("--iterations", type=int, default=3, help="Iterations per benchmark. Default: 3.")
    parser.add_argument("--no-browser", action="store_true", help="Skip browser/library/plugin traversal benchmarks.")
    args = parser.parse_args()
    try:
        code, output = run_benchmark(iterations=max(1, args.iterations), include_browser=not args.no_browser)
    except AbletonBridgeError as exc:
        print(f"Ableton Object MCP benchmark failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(output, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
