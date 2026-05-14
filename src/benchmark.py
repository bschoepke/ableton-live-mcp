from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections.abc import Callable
from typing import Any

from bridge import AbletonBridgeClient, AbletonBridgeError
from debug import require_debug_cli


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


def _skip(name: str, exc: Exception) -> dict[str, Any]:
    return {"name": name, "ok": True, "skipped": True, "reason": str(exc)}


def _benchmarks(include_browser: bool) -> list[tuple[str, RequestFactory, bool]]:
    items: list[tuple[str, RequestFactory, bool]] = [
        ("ping", lambda: ("ping", {}), False),
        ("song_compact_get", lambda: ("get", {
            "ref": {"path": "live_set"},
            "properties": ["tempo", "signature_numerator", "signature_denominator", "current_song_time"],
            "children": {"tracks": 8, "scenes": 8, "return_tracks": 4},
        }), False),
        ("set_summary_existing_project", lambda: ("set_summary", {
            "track_limit": 16,
            "clip_slot_limit": 4,
            "device_limit": 4,
            "arrangement_clip_limit": 4,
            "include_return_tracks": True,
            "include_master_track": True,
        }), False),
        ("set_summary_targeted_track", lambda: ("set_summary", {
            "track_query": "Audit Existing MIDI",
            "track_limit": 1,
            "clip_slot_limit": 2,
            "device_limit": 2,
            "arrangement_clip_limit": 4,
            "include_return_tracks": False,
            "include_master_track": False,
        }), True),
        ("batch_status", lambda: ("batch", {
            "operations": [
                {"method": "get", "params": {"ref": {"path": "live_set"}, "properties": ["tempo"]}},
                {"method": "children", "params": {"ref": {"path": "live_set"}, "child": "tracks", "limit": 4}},
                {"method": "eval", "params": {"expr": "len(song.tracks), len(song.scenes), len(song.return_tracks)"}},
            ],
        }), False),
        ("agent_m4l_command_update", lambda: ("agent_m4l_device", {
            "role": "audio_effect",
            "instance_id": "Benchmark M4L Direct",
            "command": "update",
            "load": False,
            "udp": False,
            "id": "benchmark-m4l-update",
            "patch": {
                "device_width": 280,
                "device_height": 130,
                "objects": [{"id": "probe_value", "text": "flonum", "presentation_rect": [12, 12, 80, 22]}],
                "connections": [],
            },
        }), False),
        ("device_parameter_filter", lambda: ("device_parameters", {
            "ref": {"path": "live_set tracks 0 devices 0"},
            "query": "filter",
            "limit": 8,
        }), True),
        ("clip_warp_marker_inspect", lambda: ("clip_warp_markers", {
            "ref": {"path": "live_set tracks 0 arrangement_clips 0"},
            "limit": 16,
        }), True),
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
            ("browser_sample_first_good", lambda: ("browser_search", {
                "query": "cowbell",
                "roots": ["drums", "samples", "user_library"],
                "limit": 1,
                "max_depth": 7,
                "max_visited": 8000,
                "stop_on_limit": True,
                "stop_score": 1,
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
            if optional:
                checks.append(_skip(name, exc))
            else:
                checks.append(_fail(name, exc))
    hard_failures = [check for check in checks if not check["ok"] and not check.get("optional")]
    medians = [check["median_ms"] for check in checks if check["ok"] and not check.get("skipped")]
    sizes = [check["median_bytes"] for check in checks if check["ok"] and not check.get("skipped")]
    output = {
        "ok": not hard_failures,
        "iterations": iterations,
        "checks": checks,
        "summary": {
            "total": len(checks),
            "passed": sum(1 for check in checks if check["ok"]),
            "skipped": sum(1 for check in checks if check.get("skipped")),
            "failed": sum(1 for check in checks if not check["ok"]),
            "hard_failed": len(hard_failures),
            "median_of_medians_ms": round(statistics.median(medians), 3) if medians else None,
            "max_median_ms": max(medians) if medians else None,
            "max_median_bytes": max(sizes) if sizes else None,
        },
    }
    return (0 if output["ok"] else 1), output


def main() -> int:
    if not require_debug_cli("ableton-live-mcp benchmark"):
        return 2
    parser = argparse.ArgumentParser(description="Benchmark common Ableton Live MCP workflows.")
    parser.add_argument("--iterations", type=int, default=3, help="Iterations per benchmark. Default: 3.")
    parser.add_argument("--no-browser", action="store_true", help="Skip browser/library/plugin traversal benchmarks.")
    args = parser.parse_args()
    try:
        code, output = run_benchmark(iterations=max(1, args.iterations), include_browser=not args.no_browser)
    except AbletonBridgeError as exc:
        print(f"Ableton Live MCP benchmark failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(output, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
