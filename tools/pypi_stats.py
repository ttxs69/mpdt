#!/usr/bin/env python3
"""pypi_stats.py — fetch mpdt download stats from pypistats.org (or generic package).

数据来源: https://pypistats.org/api/packages/<pkg>/recent (Apache 2.0, free, no auth)
  → {"data": {"last_day": N, "last_week": N, "last_month": N}, "package": "...", ...}
  → overall endpoint: per-day list (with/without mirrors), 7天窗口可用

Usage:
  python3 tools/pypi_stats.py --package mpdt                 # one-shot print JSON
  python3 tools/pypi_stats.py --package mpdt --check         # compare to baseline + emit if changed
  python3 tools/pypi_stats.py --package mpdt --init          # init baseline (no emit)
  python3 tools/pypi_stats.py --package mpdt --all           # include overall per-day

Use case: 接入 watchers 链路 (WATCHER_STATE_DIR) 或自建 cron。watermark 是
{recent_last_week, recent_last_day, overall_yyyymmdd} 三元组，只有变化才打印。
"""
import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.request

PYPISTATS_BASE = "https://pypistats.org/api/packages"


def _fetch_json(url, timeout=15):
    """Fetch JSON from pypistats (free, no key). Return dict or raise."""
    req = urllib.request.Request(url, headers={"User-Agent": "mpdt-pypi-watcher/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} for {url}: {e.reason}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"URL error for {url}: {e.reason}")
    return json.loads(raw)


def fetch_recent(package):
    return _fetch_json(f"{PYPISTATS_BASE}/{package}/recent")


def fetch_overall(package):
    return _fetch_json(f"{PYPISTATS_BASE}/{package}/overall")


def watermark_path(state_dir, package):
    return os.path.join(os.path.expanduser(state_dir or os.environ.get("WATCHER_STATE_DIR", "~/.hermes/watcher-state")), f"pypi-{package}.json")


def _normalize_recent(data):
    return {
        "last_day": int(data.get("last_day", 0)),
        "last_week": int(data.get("last_week", 0)),
        "last_month": int(data.get("last_month", 0)),
    }


def _normalize_overall(data):
    """data = [{"category": "with_mirrors", "date": "yyyy-mm-dd", "downloads": N}, ...]"""
    bucket = {"with_mirrors": {}, "without_mirrors": {}}
    for row in data or []:
        bucket[row.get("category", "")][row.get("date")] = int(row.get("downloads", 0))
    return bucket


def _read_watermark(path):
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _write_watermark(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, path)


def _diff_marker(a, b):
    """Return list of changed keys whose value differs."""
    changes = []
    for k in sorted(set(list((a or {}).keys()) + list((b or {}).keys()))):
        av = (a or {}).get(k)
        bv = (b or {}).get(k)
        if av != bv:
            changes.append((k, av, bv))
    return changes


def cmd_check(args):
    pkg = args.package
    wp = watermark_path(args.state_dir, pkg)
    try:
        recent_raw = fetch_recent(pkg)
        recent = _normalize_recent(recent_raw.get("data", {}))
    except Exception as e:
        print(f"[ERROR] recent fetch failed: {e}", file=sys.stderr)
        return 4
    overall = {}
    if args.all:
        try:
            overall = _normalize_overall(fetch_overall(pkg).get("data", []))
        except Exception as e:
            print(f"[WARN] overall fetch failed: {e}", file=sys.stderr)

    prev = _read_watermark(wp)
    new_wm = {"recent": recent, "overall": overall, "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    if prev is None:
        _write_watermark(wp, new_wm)
        print(f"[BASELINE] {pkg}: recent={recent} overall_keys={list(overall.keys())}", file=sys.stderr)
        return 0
    # Compare
    prev_recent = prev.get("recent") or {}
    prev_overall = prev.get("overall") or {}
    diffs = []
    for k in ["last_day", "last_week", "last_month"]:
        if prev_recent.get(k) != recent.get(k):
            diffs.append(f"recent.{k}: {prev_recent.get(k)} → {recent.get(k)}")
    for cat in ["with_mirrors", "without_mirrors"]:
        for d, dv in (overall.get(cat) or {}).items():
            pdv = prev_overall.get(cat, {}).get(d)
            if pdv != dv:
                diffs.append(f"overall.{cat}.{d}: {pdv} → {dv}")
    if diffs:
        for d in diffs:
            print(d)
        _write_watermark(wp, new_wm)
        return 0
    # No change — silent
    _write_watermark(wp, new_wm)
    return 0


def cmd_init(args):
    pkg = args.package
    wp = watermark_path(args.state_dir, pkg)
    try:
        recent = _normalize_recent(fetch_recent(pkg).get("data", {}))
        overall = _normalize_overall(fetch_overall(pkg).get("data", [])) if args.all else {}
    except Exception as e:
        print(f"[ERROR] init fetch failed: {e}", file=sys.stderr)
        return 4
    _write_watermark(wp, {"recent": recent, "overall": overall, "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat()})
    print(f"[INIT] {pkg}: recent={recent}", file=sys.stderr)
    return 0


def cmd_print(args):
    pkg = args.package
    try:
        recent = fetch_recent(pkg)
        payload = {"package": pkg, "recent": recent.get("data", {})}
        if args.all:
            payload["overall"] = fetch_overall(pkg).get("data", [])
    except Exception as e:
        print(f"[ERROR] fetch failed: {e}", file=sys.stderr)
        return 4
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def main():
    p = argparse.ArgumentParser(description="Watch PyPI download stats (pypistats).")
    p.add_argument("--package", required=True)
    p.add_argument("--state-dir", default=None, help="Override $WATCHER_STATE_DIR (default: ~/.hermes/watcher-state)")
    p.add_argument("--all", action="store_true", help="Include overall per-day breakdown")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check").call_default = lambda a: None
    sub.add_parser("init")
    sub.add_parser("print")
    args = p.parse_args()
    return {"check": cmd_check, "init": cmd_init, "print": cmd_print}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
