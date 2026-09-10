#!/usr/bin/env python3
"""AWGStat HTML generator entry point with extended ONLINE graph periods."""

from __future__ import annotations

import html
import os
import re
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import reportgen


EXTRA_PERIODS = (
    ("10080", "LAST 7 DAYS", 7 * 24 * 60),
    ("43200", "LAST 30 DAYS", 30 * 24 * 60),
)
USER_DYNAMIC_PERIODS = (
    ("360", "LAST 6 HOURS", 6 * 60),
    ("720", "LAST 12 HOURS", 12 * 60),
    ("1440", "LAST 24 HOURS", 24 * 60),
    *EXTRA_PERIODS,
)


def _epoch(value: datetime) -> int:
    return int(value.timestamp())


def _period_bounds(now: datetime) -> dict[str, tuple[int, int]]:
    end = _epoch(now)
    bounds = {
        value: (end - minutes * 60, end)
        for value, _label, minutes in USER_DYNAMIC_PERIODS
    }
    current_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    previous_month_end = current_month
    previous_month_start = (current_month - timedelta(days=1)).replace(day=1)
    bounds["previous-month"] = (
        _epoch(previous_month_start),
        _epoch(previous_month_end) - 1,
    )
    return bounds


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _dynamic_option(value: str, label: str, bounds: dict[str, tuple[int, int]]) -> str:
    start, end = bounds[value]
    return (
        f'<option value="{html.escape(value, quote=True)}" '
        f'data-from-epoch="{start}" data-to-epoch="{end}">'
        f'{html.escape(label)}</option>'
    )


def _extended_options(bounds: dict[str, tuple[int, int]]) -> str:
    options = [
        _dynamic_option(value, label, bounds)
        for value, label, _minutes in EXTRA_PERIODS
    ]
    options.append(
        _dynamic_option("previous-month", "PREVIOUS MONTH", bounds)
    )
    return "".join(options)


def _enhance_global_page(index_path: Path, bounds: dict[str, tuple[int, int]]) -> None:
    if not index_path.exists():
        return
    page = index_path.read_text(encoding="utf-8")
    marker = '<option value="custom">CUSTOM DATE / TIME</option>'
    if marker not in page:
        return

    page = re.sub(
        r'<option value="10080"[^>]*>LAST 7 DAYS</option>'
        r'<option value="43200"[^>]*>LAST 30 DAYS</option>'
        r'<option value="previous-month"[^>]*>PREVIOUS MONTH</option>',
        "",
        page,
        count=1,
    )
    page = page.replace(marker, _extended_options(bounds) + marker, 1)
    _atomic_write_text(index_path, page)


def _user_controls(
    graph_src: str,
    now: datetime,
    bounds: dict[str, tuple[int, int]],
) -> tuple[str, str]:
    dynamic = [
        _dynamic_option(value, label, bounds)
        for value, label, _minutes in USER_DYNAMIC_PERIODS
    ]
    previous_month = _dynamic_option(
        "previous-month",
        "PREVIOUS MONTH",
        bounds,
    )
    options = (
        f'<option value="60" data-graph-src="{html.escape(graph_src, quote=True)}" '
        'selected="selected">LAST 60 MINUTES</option>'
        + "".join(dynamic)
        + previous_month
        + '<option value="custom">CUSTOM DATE / TIME</option>'
    )
    custom_end = now.replace(second=0, microsecond=0)
    custom_start = custom_end - timedelta(minutes=60)
    controls = (
        '<div class="graph-controls">'
        '<label for="traffic-period">GRAPH PERIOD</label> '
        f'<select id="traffic-period">{options}</select> '
        '<span id="custom-period" class="custom-period">'
        '<label for="traffic-from">FROM</label> '
        f'<input id="traffic-from" type="datetime-local" step="60" value="{custom_start:%Y-%m-%dT%H:%M}"> '
        '<label for="traffic-to">TO</label> '
        f'<input id="traffic-to" type="datetime-local" step="60" value="{custom_end:%Y-%m-%dT%H:%M}"> '
        '<button id="traffic-apply" type="button">APPLY</button>'
        '</span>'
        '<span id="traffic-graph-status" class="graph-status" aria-live="polite"></span>'
        '</div>'
    )
    graph = (
        '<div class="report graph-report"><table cellpadding="0" cellspacing="2">'
        '<tr><td>'
        f'<img id="traffic-graph-image" src="{html.escape(graph_src, quote=True)}" alt="User RX/TX rate graph">'
        '<svg id="traffic-custom-graph" class="custom-traffic-graph" '
        'xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 360" '
        'role="img" aria-label="Custom RX/TX rate graph"></svg>'
        '</td></tr></table></div>'
    )
    return controls, graph


def _enhance_user_page(
    page_path: Path,
    now: datetime,
    bounds: dict[str, tuple[int, int]],
) -> None:
    if not page_path.exists():
        return
    page = page_path.read_text(encoding="utf-8")
    if 'id="traffic-period"' in page:
        return

    marker = '<div class="online-section">TRAFFIC RATE</div>'
    if marker not in page:
        return
    image_match = re.search(
        r'<img src="([^"]+)" alt="User RX/TX rate graph">',
        page,
    )
    if not image_match:
        return
    graph_src = image_match.group(1)

    graph_block = re.compile(
        re.escape(marker)
        + r'<div class="report graph-report"><table cellpadding="0" cellspacing="2">'
        + r'<tr><td><img src="[^"]+" alt="User RX/TX rate graph"></td></tr>'
        + r'</table></div>'
    )
    controls, graph = _user_controls(graph_src, now, bounds)
    replacement = (
        '<div id="traffic-rate-title" class="online-section">'
        'TRAFFIC RATE — LAST 60 MINUTES</div>'
        + controls
        + graph
        + '<script type="text/javascript" src="../graph-controls.js"></script>'
    )
    page, count = graph_block.subn(replacement, page, count=1)
    if count:
        _atomic_write_text(page_path, page)


def enhance_online_reports() -> None:
    cfg = reportgen.load_config()
    tz = reportgen.resolve_tz(cfg.get("REPORT_TZ", "Europe/Moscow"))
    now = datetime.now(tz)
    bounds = _period_bounds(now)
    webroot = Path(cfg["WEBROOT"])
    online_root = webroot / "online"
    if not online_root.exists():
        return

    _enhance_global_page(online_root / "index.html", bounds)

    history_path = reportgen.expand(cfg["HISTORY"], cfg)
    online_state_path = reportgen.expand(
        cfg.get("ONLINE_STATE", "${WORKDIR}/online.csv"),
        cfg,
    )
    rows = reportgen.read_history(history_path, tz)
    snapshot = reportgen.read_online_state(online_state_path, tz)

    rows_by_peer: dict[str, list[reportgen.HistoryRow]] = defaultdict(list)
    for row in rows:
        rows_by_peer[row.peer].append(row)

    peer_ids = set(rows_by_peer)
    peer_ids.update(peer.peer for peer in snapshot.peers)
    for peer_id in peer_ids:
        user_root = online_root / reportgen.user_slug(peer_id)
        if not user_root.exists():
            continue
        _atomic_write_text(
            user_root / "traffic-history.json",
            reportgen.render_online_graph_data(rows_by_peer.get(peer_id, []), now),
        )
        _enhance_user_page(user_root / "index.html", now, bounds)


def main() -> int:
    result = reportgen.main()
    if result != 0:
        return result
    enhance_online_reports()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
