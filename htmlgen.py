#!/usr/bin/env python3
"""AWGStat HTML generator — rebuilds when traffic or names.map changed."""

from __future__ import annotations

import html
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SCRIPT_DIR = Path(__file__).resolve().parent


def read_version(cfg: dict[str, str]) -> str:
    if cfg.get("VERSION"):
        return cfg["VERSION"]
    version_file = SCRIPT_DIR / "VERSION"
    if version_file.exists():
        return version_file.read_text(encoding="utf-8").strip() or "0.0.0"
    return "0.0.0"


def load_config() -> dict[str, str]:
    cfg: dict[str, str] = {}
    config_path = SCRIPT_DIR / "config"
    if not config_path.exists():
        sys.exit(f"config not found: {config_path}")

    for raw in config_path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        key, val = line.split("=", 1)
        cfg[key.strip()] = val.strip().strip('"')
    return cfg


def expand(path: str, cfg: dict[str, str]) -> Path:
    workdir = cfg.get("WORKDIR", "/opt/wgstats")
    return Path(path.replace("${WORKDIR}", workdir))


def resolve_tz(name: str):
    name = (name or "").strip() or "Europe/Moscow"
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return timezone.utc


def fmt_bytes(value: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    size = float(max(value, 0))
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def parse_name_line(line: str) -> tuple[str, str] | None:
    """Parse names.map line. Canonical format: pubkey:name (colon)."""
    line = line.split("#", 1)[0].strip()
    if not line:
        return None
    if ":" in line:
        key, val = line.split(":", 1)
    elif "=" in line:
        # Legacy broken '=' / '==' format
        key, val = line.rsplit("=", 1)
    else:
        return None
    key, val = key.strip(), val.strip()
    if val.startswith(":"):
        val = val[1:]
    if not key or not val:
        return None
    return key, val


def load_names(path: Path) -> dict[str, str]:
    names: dict[str, str] = {}
    if not path.exists():
        return names
    for line in path.read_text(encoding="utf-8").splitlines():
        parsed = parse_name_line(line)
        if not parsed:
            continue
        key, val = parsed
        prev = names.get(key)
        if prev is None:
            names[key] = val
        elif prev.startswith("неизвестный") and not val.startswith("неизвестный"):
            names[key] = val
    return names


def lookup_name(names: dict[str, str], peer: str, fallback: str = "") -> str:
    if peer in names:
        return names[peer]
    # Tolerate accidental missing/extra base64 padding
    alt = peer.rstrip("=")
    if alt in names:
        return names[alt]
    if not peer.endswith("=") and (peer + "=") in names:
        return names[peer + "="]
    return fallback


def read_history(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []

    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8", newline="") as fh:
        for line in fh:
            if line.startswith("#WGSTAT:"):
                continue
            if line.startswith("timestamp;"):
                continue
            if not line.strip():
                continue
            parts = line.rstrip("\n").split(";")
            if len(parts) < 10:
                continue
            rows.append(
                {
                    "timestamp": parts[0],
                    "peer": parts[3],
                    "name": parts[4],
                    "ip": parts[5],
                    "rx_bytes": parts[6],
                    "tx_bytes": parts[7],
                }
            )
    return rows


def aggregate(
    rows: list[dict[str, str]],
    tz,
) -> dict[str, dict[str, object]]:
    now = datetime.now(tz)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)
    month_start = now - timedelta(days=30)

    stats: dict[str, dict[str, object]] = defaultdict(
        lambda: {
            "name": "",
            "ip": "",
            "today": 0,
            "week": 0,
            "month": 0,
            "total": 0,
        }
    )

    for row in rows:
        try:
            ts = datetime.fromtimestamp(int(row["timestamp"]), tz=timezone.utc).astimezone(tz)
            rx = int(row["rx_bytes"])
            tx = int(row["tx_bytes"])
        except (ValueError, KeyError, OSError):
            continue

        peer = row["peer"]
        total = rx + tx
        item = stats[peer]
        if row.get("name"):
            item["name"] = row["name"]
        if row.get("ip"):
            item["ip"] = row["ip"]
        item["total"] = int(item["total"]) + total
        if ts >= today_start:
            item["today"] = int(item["today"]) + total
        if ts >= week_start:
            item["week"] = int(item["week"]) + total
        if ts >= month_start:
            item["month"] = int(item["month"]) + total

    return stats


def clean_ip(ip: str) -> str:
    return ip.split("/", 1)[0] if ip else ""


def names_map_changed(names_path: Path, index_path: Path) -> bool:
    """Rebuild HTML when names.map is newer than the published page.

    Exception to the 'no traffic → no page' rule: renames/swaps must redraw.
    """
    if not names_path.exists():
        return False
    if not index_path.exists():
        return True
    return names_path.stat().st_mtime > index_path.stat().st_mtime


def render_html(
    title: str,
    version: str,
    rows: list[tuple[str, str, int, int, int, int]],
    updated: str,
) -> str:
    body_rows = []
    for name, ip, today, week, month, total in rows:
        body_rows.append(
            "<tr>"
            f"<td>{html.escape(name or '—')}</td>"
            f"<td>{html.escape(ip or '—')}</td>"
            f"<td class=\"num\">{html.escape(fmt_bytes(today))}</td>"
            f"<td class=\"num\">{html.escape(fmt_bytes(week))}</td>"
            f"<td class=\"num\">{html.escape(fmt_bytes(month))}</td>"
            f"<td class=\"num\">{html.escape(fmt_bytes(total))}</td>"
            "</tr>"
        )

    rows_html = "\n".join(body_rows) if body_rows else (
        '<tr><td colspan="6" class="empty">No traffic recorded yet</td></tr>'
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="generator" content="AWGStat {html.escape(version)}">
  <title>{html.escape(title)}</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <header class="topbar">
    <h1>{html.escape(title)} <span class="version">v{html.escape(version)}</span></h1>
    <p class="updated">Updated: {html.escape(updated)}</p>
  </header>
  <main>
    <table>
      <thead>
        <tr>
          <th>Name</th>
          <th>IP</th>
          <th class="num">Today</th>
          <th class="num">Week</th>
          <th class="num">Month</th>
          <th class="num">Total</th>
        </tr>
      </thead>
      <tbody>
        {rows_html}
      </tbody>
    </table>
  </main>
</body>
</html>
"""


def main() -> int:
    cfg = load_config()
    changed = expand(cfg["CHANGED"], cfg)
    history = expand(cfg["HISTORY"], cfg)
    names_path = expand(cfg["NAMES"], cfg)
    webroot = Path(cfg["WEBROOT"])
    index_path = webroot / "index.html"
    title = cfg.get("TITLE", "AWGStat")
    version = read_version(cfg)
    tz = resolve_tz(cfg.get("REPORT_TZ", "Europe/Moscow"))
    force = "--force" in sys.argv
    static_src = SCRIPT_DIR / "static" / "style.css"
    if not static_src.exists():
        static_src = SCRIPT_DIR / "style.css"

    need_rebuild = (
        force
        or changed.exists()
        or names_map_changed(names_path, index_path)
    )
    if not need_rebuild:
        return 0

    names = load_names(names_path)
    rows = read_history(history)
    stats = aggregate(rows, tz)

    table_rows: list[tuple[str, str, int, int, int, int]] = []
    for peer, item in stats.items():
        name = lookup_name(names, peer, str(item["name"] or ""))
        ip = clean_ip(str(item["ip"]))
        table_rows.append(
            (
                name,
                ip,
                int(item["today"]),
                int(item["week"]),
                int(item["month"]),
                int(item["total"]),
            )
        )

    table_rows.sort(key=lambda r: (-r[5], r[0].lower(), r[1]))

    updated = datetime.now(tz).strftime("%Y-%m-%d %H:%M")
    webroot.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        render_html(title, version, table_rows, updated), encoding="utf-8"
    )

    if static_src.exists():
        shutil.copy2(static_src, webroot / "style.css")

    changed.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
