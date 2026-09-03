#!/usr/bin/env python3
"""Generate classic SARG-style static reports from AWGStat history."""

from __future__ import annotations

import hashlib
import html
import os
import shutil
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


SCRIPT_DIR = Path(__file__).resolve().parent
CALENDAR_REPORT_KINDS = ("daily", "weekly", "monthly")
REPORT_KINDS = (*CALENDAR_REPORT_KINDS, "total")
KIND_TITLES = {
    "daily": "DAILY REPORTS",
    "weekly": "WEEKLY REPORTS",
    "monthly": "MONTHLY REPORTS",
    "total": "ALL TIME REPORT",
}
KIND_DESCRIPTIONS = {
    "daily": "Daily report index",
    "weekly": "Weekly report index",
    "monthly": "Monthly report index",
    "total": "All-time report index",
}
KIND_CONFIG_KEYS = {
    "daily": "DAILY_REPORTS",
    "weekly": "WEEKLY_REPORTS",
    "monthly": "MONTHLY_REPORTS",
}
KIND_DEFAULT_LIMITS = {
    "daily": 31,
    "weekly": 12,
    "monthly": 12,
}
OWNED_DIRECTORIES = (*REPORT_KINDS, "images")
OWNED_FILES = ("index.html", "style.css")
MONTH_ABBREVIATIONS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


@dataclass(frozen=True)
class HistoryRow:
    timestamp: datetime
    peer: str
    name: str
    ip: str
    rx_bytes: int
    tx_bytes: int
    interval: int = 0
    handshake: int = 0

    @property
    def total(self) -> int:
        return self.rx_bytes + self.tx_bytes


@dataclass
class TrafficSummary:
    peer: str
    name: str = ""
    ip: str = ""
    rx_bytes: int = 0
    tx_bytes: int = 0
    samples: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None

    @property
    def total(self) -> int:
        return self.rx_bytes + self.tx_bytes

    def add(self, row: HistoryRow) -> None:
        if row.name:
            self.name = row.name
        if row.ip:
            self.ip = clean_ip(row.ip)
        self.rx_bytes += row.rx_bytes
        self.tx_bytes += row.tx_bytes
        self.samples += 1
        if self.first_seen is None or row.timestamp < self.first_seen:
            self.first_seen = row.timestamp
        if self.last_seen is None or row.timestamp > self.last_seen:
            self.last_seen = row.timestamp


@dataclass
class PeriodReport:
    kind: str
    key: str
    label: str
    start: datetime
    end: datetime
    rows: list[HistoryRow] = field(default_factory=list)
    users: dict[str, TrafficSummary] = field(default_factory=dict)

    @property
    def rx_bytes(self) -> int:
        return sum(item.rx_bytes for item in self.users.values())

    @property
    def tx_bytes(self) -> int:
        return sum(item.tx_bytes for item in self.users.values())

    @property
    def total(self) -> int:
        return self.rx_bytes + self.tx_bytes

    @property
    def samples(self) -> int:
        return sum(item.samples for item in self.users.values())


def read_version(cfg: dict[str, str]) -> str:
    if cfg.get("VERSION"):
        return cfg["VERSION"]
    version_file = SCRIPT_DIR / "VERSION"
    if version_file.exists():
        return version_file.read_text(encoding="utf-8").strip() or "0.0.0"
    return "0.0.0"


def load_config(path: Path | None = None) -> dict[str, str]:
    cfg: dict[str, str] = {}
    config_path = path or SCRIPT_DIR / "config"
    if not config_path.exists():
        sys.exit(f"config not found: {config_path}")

    for raw in config_path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        cfg[key.strip()] = value.strip().strip('"')
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


def parse_nonnegative_int(value: str) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def config_int(
    cfg: dict[str, str],
    key: str,
    default: int,
    *,
    minimum: int = 0,
    maximum: int = 10000,
) -> int:
    try:
        value = int(cfg.get(key, str(default)))
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


def fmt_bytes(value: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    size = float(max(value, 0))
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def fmt_count(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def fmt_datetime(value: datetime | None) -> str:
    return value.strftime("%d/%m/%Y-%H:%M") if value else "—"


def fmt_percent(part: int, whole: int) -> str:
    if whole <= 0:
        return "0.00%"
    return f"{part * 100 / whole:.2f}%"


def clean_ip(ip: str) -> str:
    value = (ip or "").split(",", 1)[0].strip()
    return value.split("/", 1)[0] if value else ""


def parse_name_line(line: str) -> tuple[str, str] | None:
    """Parse canonical pubkey:name and tolerate legacy equals-based records."""
    line = line.split("#", 1)[0].strip()
    if not line:
        return None
    if ":" in line:
        key, value = line.split(":", 1)
    elif "=" in line:
        key, value = line.rsplit("=", 1)
    else:
        return None
    key, value = key.strip(), value.strip()
    if value.startswith(":"):
        value = value[1:]
    if not key or not value:
        return None
    return key, value


def load_names(path: Path) -> dict[str, str]:
    names: dict[str, str] = {}
    if not path.exists():
        return names
    for line in path.read_text(encoding="utf-8").splitlines():
        parsed = parse_name_line(line)
        if not parsed:
            continue
        key, value = parsed
        previous = names.get(key)
        if previous is None:
            names[key] = value
        elif previous.startswith("неизвестный") and not value.startswith("неизвестный"):
            names[key] = value
    return names


def lookup_name(names: dict[str, str], peer: str, fallback: str = "") -> str:
    if peer in names:
        return names[peer]
    without_padding = peer.rstrip("=")
    if without_padding in names:
        return names[without_padding]
    if not peer.endswith("=") and f"{peer}=" in names:
        return names[f"{peer}="]
    return fallback


def read_history(path: Path, tz=timezone.utc) -> list[HistoryRow]:
    if not path.exists():
        return []

    rows: list[HistoryRow] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for line in handle:
            if line.startswith("#WGSTAT:") or line.startswith("timestamp;"):
                continue
            if not line.strip():
                continue
            parts = line.rstrip("\r\n").split(";")
            if len(parts) < 10:
                continue
            try:
                timestamp = datetime.fromtimestamp(int(parts[0]), tz=timezone.utc)
            except (ValueError, OSError, OverflowError):
                continue
            peer = parts[3].strip()
            if not peer:
                continue
            rows.append(
                HistoryRow(
                    timestamp=timestamp.astimezone(tz),
                    peer=peer,
                    name=parts[4].strip(),
                    ip=parts[5].strip(),
                    rx_bytes=parse_nonnegative_int(parts[6]),
                    tx_bytes=parse_nonnegative_int(parts[7]),
                    interval=parse_nonnegative_int(parts[8]),
                    handshake=parse_nonnegative_int(parts[9]),
                )
            )
    return rows


def aggregate_rows(rows: Iterable[HistoryRow]) -> dict[str, TrafficSummary]:
    summaries: dict[str, TrafficSummary] = {}
    for row in rows:
        summary = summaries.setdefault(row.peer, TrafficSummary(peer=row.peer))
        summary.add(row)
    return summaries


def period_metadata(kind: str, value: datetime) -> tuple[str, str, datetime, datetime]:
    midnight = value.replace(hour=0, minute=0, second=0, microsecond=0)
    if kind == "daily":
        start = midnight
        end = start + timedelta(days=1)
        key = start.strftime("%Y-%m-%d")
        label = start.strftime("%d/%m/%Y")
    elif kind == "weekly":
        start = midnight - timedelta(days=value.weekday())
        end = start + timedelta(days=7)
        iso = start.isocalendar()
        key = f"{iso.year}-W{iso.week:02d}"
        label = f"{start:%d/%m/%Y} - {(end - timedelta(days=1)):%d/%m/%Y}"
    elif kind == "monthly":
        start = midnight.replace(day=1)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
        key = start.strftime("%Y-%m")
        label = f"{start:%d/%m/%Y} - {(end - timedelta(days=1)):%d/%m/%Y}"
    else:
        raise ValueError(f"unknown report kind: {kind}")
    return key, label, start, end


def build_periods(
    rows: list[HistoryRow],
    kind: str,
    now: datetime,
    limit: int,
) -> list[PeriodReport]:
    buckets: dict[str, PeriodReport] = {}
    current_key, current_label, current_start, current_end = period_metadata(kind, now)
    buckets[current_key] = PeriodReport(
        kind=kind,
        key=current_key,
        label=current_label,
        start=current_start,
        end=current_end,
    )

    for row in rows:
        key, label, start, end = period_metadata(kind, row.timestamp)
        period = buckets.setdefault(
            key,
            PeriodReport(kind=kind, key=key, label=label, start=start, end=end),
        )
        period.rows.append(row)

    periods = sorted(buckets.values(), key=lambda item: item.start, reverse=True)
    if limit > 0:
        periods = periods[:limit]
    for period in periods:
        period.users = aggregate_rows(period.rows)
    return periods


def build_all_time_report(
    rows: list[HistoryRow],
    now: datetime,
) -> list[PeriodReport]:
    """Build one report covering every retained traffic interval."""
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    row_days = [
        row.timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
        for row in rows
    ]
    start = min(row_days, default=today)
    last_day = max(row_days, default=today)
    last_day = max(last_day, today)
    end = last_day + timedelta(days=1)
    period = PeriodReport(
        kind="total",
        key="all-time",
        label=f"{start:%d/%m/%Y} - {last_day:%d/%m/%Y}",
        start=start,
        end=end,
        rows=list(rows),
    )
    period.users = aggregate_rows(period.rows)
    return [period]


def resolved_name(summary: TrafficSummary, names: dict[str, str]) -> str:
    return lookup_name(names, summary.peer, summary.name) or "неизвестный"


def user_slug(peer: str) -> str:
    return hashlib.sha256(peer.encode("utf-8")).hexdigest()[:16]


def period_dirname(period: PeriodReport) -> str:
    """Use the classic SARG European file-tree naming convention."""
    last_day = period.end - timedelta(days=1)
    start = (
        f"{period.start.day:02d}"
        f"{MONTH_ABBREVIATIONS[period.start.month - 1]}"
        f"{period.start.year:04d}"
    )
    end = (
        f"{last_day.day:02d}"
        f"{MONTH_ABBREVIATIONS[last_day.month - 1]}"
        f"{last_day.year:04d}"
    )
    return f"{start}-{end}"


def grouped_summaries(
    rows: list[HistoryRow],
    key_fn: Callable[[HistoryRow], str],
) -> list[tuple[str, TrafficSummary]]:
    groups: dict[str, list[HistoryRow]] = defaultdict(list)
    for row in rows:
        groups[key_fn(row)].append(row)
    result: list[tuple[str, TrafficSummary]] = []
    for key in sorted(groups):
        summary = next(iter(aggregate_rows(groups[key]).values()))
        result.append((key, summary))
    return result


def hourly_summaries(rows: list[HistoryRow]) -> list[tuple[str, TrafficSummary]]:
    groups: dict[int, list[HistoryRow]] = defaultdict(list)
    for row in rows:
        groups[row.timestamp.hour].append(row)
    peer = rows[0].peer if rows else ""
    result: list[tuple[str, TrafficSummary]] = []
    for hour in range(24):
        if groups[hour]:
            summary = next(iter(aggregate_rows(groups[hour]).values()))
        else:
            summary = TrafficSummary(peer=peer)
        result.append((f"{hour:02d}H", summary))
    return result


def nav_markup(root_prefix: str) -> str:
    links = [
        (f"{root_prefix}index.html", "REPORT INDEX"),
        (f"{root_prefix}daily/index.html", "DAILY"),
        (f"{root_prefix}weekly/index.html", "WEEKLY"),
        (f"{root_prefix}monthly/index.html", "MONTHLY"),
        (f"{root_prefix}total/index.html", "ALL TIME"),
    ]
    return (
        '<div class="navigation">'
        + " | ".join(
            f'<a href="{html.escape(href, quote=True)}">{label}</a>'
            for href, label in links
        )
        + "</div>\n"
    )


def render_document(
    site_title: str,
    page_title: str,
    version: str,
    updated: str,
    root_prefix: str,
    header_rows: list[tuple[str, bool]],
    content: str,
) -> str:
    rows = [f'<tr><th class="title_c">{html.escape(site_title)}</th></tr>']
    for text, strong in header_rows:
        tag = "th" if strong else "td"
        rows.append(f'<tr><{tag} class="header_c">{html.escape(text)}</{tag}></tr>')
    return f"""<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01//EN" "http://www.w3.org/TR/html4/strict.dtd">
<html lang="ru">
<head>
  <meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
  <meta name="generator" content="AWGStat {html.escape(version)}">
  <title>{html.escape(page_title)}</title>
  <link rel="stylesheet" href="{html.escape(root_prefix, quote=True)}style.css" type="text/css">
</head>
<body class="body">
<div class="logo"><a href="{html.escape(root_prefix, quote=True)}index.html"><img src="{html.escape(root_prefix, quote=True)}images/awgstat.svg" alt="AWGStat"></a>&nbsp;AmneziaWG Traffic Analysis Report</div>
{nav_markup(root_prefix)}
<div class="title"><table cellpadding="0" cellspacing="0">
{''.join(rows)}
</table></div>
{content}
<div class="info">Generated by <a href="https://github.com/arma35/awgstat">AWGStat-{html.escape(version)}</a> on {html.escape(updated)}<br>VPN traffic only: AmneziaWG does not expose visited sites or URLs.</div>
</body>
</html>
"""


def empty_row(columns: int, message: str = "No data") -> str:
    return f'<tr><td class="data3" colspan="{columns}">{html.escape(message)}</td></tr>'


def render_root(
    site_title: str,
    version: str,
    updated: str,
    periods: dict[str, list[PeriodReport]],
) -> str:
    rows: list[str] = []
    for kind in REPORT_KINDS:
        latest = periods[kind][0]
        average = latest.total // len(latest.users) if latest.users else 0
        rows.append(
            "<tr>"
            f'<td class="data2"><a href="{kind}/index.html">{KIND_TITLES[kind]}</a></td>'
            f'<td class="data">{fmt_count(len(periods[kind]))}</td>'
            f'<td class="data2">{html.escape(latest.label)}</td>'
            f'<td class="data">{fmt_count(len(latest.users))}</td>'
            f'<td class="data">{fmt_bytes(latest.total)}</td>'
            f'<td class="data">{fmt_bytes(average)}</td>'
            "</tr>"
        )
    content = (
        '<div class="index"><table cellpadding="1" cellspacing="2">'
        '<thead><tr><th class="header_l">REPORT TYPE</th>'
        '<th class="header_l">PERIODS</th>'
        '<th class="header_l">LATEST PERIOD</th>'
        '<th class="header_l">USERS</th>'
        '<th class="header_l">BYTES</th>'
        '<th class="header_l">AVERAGE</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )
    return render_document(
        site_title,
        "AWGStat reports",
        version,
        updated,
        "",
        [("AWGStat reports", True)],
        content,
    )


def render_period_table(periods: list[PeriodReport], updated: str) -> str:
    rows: list[str] = []
    for period in periods:
        average = period.total // len(period.users) if period.users else 0
        dirname = period_dirname(period)
        rows.append(
            "<tr>"
            f'<td class="data2"><a href="{dirname}/index.html">{dirname}</a></td>'
            f'<td class="data2">{html.escape(updated)}</td>'
            f'<td class="data">{fmt_count(len(period.users))}</td>'
            f'<td class="data">{fmt_bytes(period.total)}</td>'
            f'<td class="data">{fmt_bytes(average)}</td>'
            "</tr>"
        )
    if not rows:
        rows.append(empty_row(5))
    return (
        '<div class="index"><table cellpadding="1" cellspacing="2">'
        '<thead><tr><th class="header_l">FILE/PERIOD</th>'
        '<th class="header_l">CREATION DATE</th>'
        '<th class="header_l">USERS</th>'
        '<th class="header_l">BYTES</th>'
        '<th class="header_l">AVERAGE</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def render_category(
    site_title: str,
    version: str,
    updated: str,
    kind: str,
    periods: list[PeriodReport],
) -> str:
    return render_document(
        site_title,
        KIND_DESCRIPTIONS[kind],
        version,
        updated,
        "../",
        [
            (KIND_TITLES[kind], False),
            (f"Available reports: {len(periods)}", True),
        ],
        render_period_table(periods, updated),
    )


def render_users_table(period: PeriodReport, names: dict[str, str]) -> str:
    ordered = sorted(
        period.users.values(),
        key=lambda item: (-item.total, resolved_name(item, names).casefold(), item.ip),
    )
    rows: list[str] = []
    for rank, summary in enumerate(ordered, start=1):
        slug = user_slug(summary.peer)
        rows.append(
            "<tr>"
            f'<td class="data">{rank}</td>'
            '<td class="data2">'
            f'<a href="{slug}/graph.html"><img class="report-icon" src="../../images/graph.svg" title="Graphic report" alt="G"></a>&nbsp;'
            f'<a href="{slug}/d{slug}.html"><img class="report-icon" src="../../images/datetime.svg" title="Date/time report" alt="T"></a>'
            "</td>"
            f'<td class="data2"><a href="{slug}/{slug}.html">{html.escape(resolved_name(summary, names))}</a></td>'
            f'<td class="data2">{html.escape(summary.ip or "—")}</td>'
            f'<td class="data">{fmt_count(summary.samples)}</td>'
            f'<td class="data">{fmt_bytes(summary.rx_bytes)}</td>'
            f'<td class="data">{fmt_bytes(summary.tx_bytes)}</td>'
            f'<td class="data">{fmt_bytes(summary.total)}</td>'
            f'<td class="data">{fmt_percent(summary.total, period.total)}</td>'
            "</tr>"
        )
    if not rows:
        rows.append(empty_row(9, "No traffic in this period"))

    total_users = len(ordered)
    average_samples = period.samples // total_users if total_users else 0
    average_rx = period.rx_bytes // total_users if total_users else 0
    average_tx = period.tx_bytes // total_users if total_users else 0
    average_total = period.total // total_users if total_users else 0
    footer = (
        "<tfoot><tr><td></td><td></td>"
        '<th class="header_l" colspan="2">TOTAL</th>'
        f'<th class="header_r">{fmt_count(period.samples)}</th>'
        f'<th class="header_r">{fmt_bytes(period.rx_bytes)}</th>'
        f'<th class="header_r">{fmt_bytes(period.tx_bytes)}</th>'
        f'<th class="header_r">{fmt_bytes(period.total)}</th><td></td></tr>'
        "<tr><td></td><td></td>"
        '<th class="header_l" colspan="2">AVERAGE</th>'
        f'<th class="header_r">{fmt_count(average_samples)}</th>'
        f'<th class="header_r">{fmt_bytes(average_rx)}</th>'
        f'<th class="header_r">{fmt_bytes(average_tx)}</th>'
        f'<th class="header_r">{fmt_bytes(average_total)}</th><td></td></tr></tfoot>'
    )
    return (
        '<div class="report report-scroll"><table cellpadding="1" cellspacing="2">'
        '<thead><tr><th class="header_l">NUM</th>'
        '<th class="header_l"></th>'
        '<th class="header_l">USERID</th>'
        '<th class="header_l">USERIP</th>'
        '<th class="header_l" title="AWGStat traffic sampling intervals">CONNECT</th>'
        '<th class="header_c" colspan="2">RX-TX</th>'
        '<th class="header_l">BYTES</th>'
        '<th class="header_l">%BYTES</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody>{footer}</table></div>"
        '<div class="report-note">CONNECT is the number of AWGStat traffic intervals, not TCP connections.</div>'
    )


def render_period(
    site_title: str,
    version: str,
    updated: str,
    period: PeriodReport,
    names: dict[str, str],
) -> str:
    links = (
        '<div class="report"><table cellpadding="1" cellspacing="2">'
        f'<tr><td class="link"><a href="../index.html">{KIND_TITLES[period.kind]}</a></td></tr>'
        "</table></div>"
    )
    return render_document(
        site_title,
        f"AWGStat report for {period.label}",
        version,
        updated,
        "../../",
        [
            (f"Period: {period.label}", False),
            ("Sort: BYTES, reverse", False),
            ("Top users", True),
        ],
        links + render_users_table(period, names),
    )


def render_user_rows(
    summary: TrafficSummary,
    rows: list[HistoryRow],
    detail_limit: int,
) -> str:
    if detail_limit <= 0:
        return '<div class="report-note">Traffic interval detail is disabled.</div>'

    selected = sorted(rows, key=lambda item: item.timestamp, reverse=True)
    selected = selected[:detail_limit]
    body: list[str] = []
    for row in selected:
        body.append(
            "<tr>"
            f'<td class="data2">{fmt_datetime(row.timestamp)}</td>'
            f'<td class="data2">{html.escape(clean_ip(row.ip) or "—")}</td>'
            f'<td class="data">{fmt_count(row.interval)} s</td>'
            f'<td class="data">{fmt_bytes(row.rx_bytes)}</td>'
            f'<td class="data">{fmt_bytes(row.tx_bytes)}</td>'
            f'<td class="data">{fmt_bytes(row.total)}</td>'
            f'<td class="data">{fmt_percent(row.total, summary.total)}</td>'
            "</tr>"
        )
    if not body:
        body.append(empty_row(7))

    average_rx = summary.rx_bytes // summary.samples if summary.samples else 0
    average_tx = summary.tx_bytes // summary.samples if summary.samples else 0
    average_total = summary.total // summary.samples if summary.samples else 0
    average_interval = (
        sum(row.interval for row in rows) // len(rows)
        if rows
        else 0
    )
    footer = (
        '<tfoot><tr><th class="header_l" colspan="2">TOTAL</th>'
        f'<th class="header_r">{fmt_count(sum(row.interval for row in rows))} s</th>'
        f'<th class="header_r">{fmt_bytes(summary.rx_bytes)}</th>'
        f'<th class="header_r">{fmt_bytes(summary.tx_bytes)}</th>'
        f'<th class="header_r">{fmt_bytes(summary.total)}</th>'
        '<td></td></tr><tr><th class="header_l" colspan="2">AVERAGE</th>'
        f'<th class="header_r">{fmt_count(average_interval)} s</th>'
        f'<th class="header_r">{fmt_bytes(average_rx)}</th>'
        f'<th class="header_r">{fmt_bytes(average_tx)}</th>'
        f'<th class="header_r">{fmt_bytes(average_total)}</th>'
        "<td></td></tr></tfoot>"
    )
    limit_note = ""
    if detail_limit > 0 and len(rows) > detail_limit:
        limit_note = (
            f'<div class="report-note">Showing the latest {detail_limit} of '
            f"{len(rows)} traffic intervals. Totals include all intervals.</div>"
        )
    return (
        '<div class="report report-scroll"><table cellpadding="2" cellspacing="1">'
        '<thead><tr><th class="header_l">TRAFFIC INTERVAL</th>'
        '<th class="header_l">IP/NAME</th>'
        '<th class="header_l">INTERVAL</th>'
        '<th class="header_l">RX</th>'
        '<th class="header_l">TX</th>'
        '<th class="header_l">BYTES</th>'
        '<th class="header_l">%BYTES</th></tr></thead>'
        f"<tbody>{''.join(body)}</tbody>{footer}</table></div>{limit_note}"
    )


def user_report_links(slug: str) -> str:
    return (
        '<div class="report"><table cellpadding="1" cellspacing="2">'
        '<tr><td class="link">'
        f'<a href="{slug}.html">User report</a>&nbsp; | &nbsp;'
        f'<a href="graph.html">Graphic report</a>&nbsp; | &nbsp;'
        f'<a href="d{slug}.html">Date/time report</a>'
        "</td></tr></table></div>"
    )


def render_user(
    site_title: str,
    version: str,
    updated: str,
    period: PeriodReport,
    summary: TrafficSummary,
    rows: list[HistoryRow],
    names: dict[str, str],
    detail_limit: int,
) -> str:
    name = resolved_name(summary, names)
    slug = user_slug(summary.peer)
    return render_document(
        site_title,
        "User report",
        version,
        updated,
        "../../../",
        [
            (f"Period: {period.label}", False),
            (f"User: {name}", False),
            ("Sort: DATE/TIME, reverse", False),
            ("User report", True),
        ],
        user_report_links(slug)
        + render_user_rows(summary, rows, detail_limit),
    )


def render_user_datetime(
    site_title: str,
    version: str,
    updated: str,
    period: PeriodReport,
    summary: TrafficSummary,
    rows: list[HistoryRow],
    names: dict[str, str],
) -> str:
    name = resolved_name(summary, names)
    slug = user_slug(summary.peer)
    values: dict[tuple[str, int], int] = defaultdict(int)
    dates: set[str] = set()
    for row in rows:
        day = row.timestamp.strftime("%Y-%m-%d")
        dates.add(day)
        values[(day, row.timestamp.hour)] += row.total
    ordered_dates = sorted(dates)
    hour_totals = [0] * 24
    table_rows: list[str] = []
    for day in ordered_dates:
        row_total = 0
        cells: list[str] = []
        for hour in range(24):
            value = values[(day, hour)]
            row_total += value
            hour_totals[hour] += value
            cells.append(
                f'<td class="data">{fmt_bytes(value) if value else ""}</td>'
            )
        label = datetime.strptime(day, "%Y-%m-%d").strftime("%d/%m/%Y")
        table_rows.append(
            f'<tr><td class="data">{label}</td>{"".join(cells)}'
            f'<td class="data">{fmt_bytes(row_total)}</td></tr>'
        )
    if not table_rows:
        table_rows.append(empty_row(26))

    headers = "".join(
        f'<th class="header_c">{hour:02d}H<br>BYTES</th>'
        for hour in range(24)
    )
    total_cells = "".join(
        f'<th class="header_r">{fmt_bytes(value) if value else ""}</th>'
        for value in hour_totals
    )
    table = (
        '<div class="report report-scroll"><table class="hourly" cellpadding="0" cellspacing="2">'
        f'<thead><tr><th class="header_c">DATE</th>{headers}'
        '<th class="header_c">TOTAL<br>BYTES</th></tr></thead>'
        f"<tbody>{''.join(table_rows)}</tbody>"
        f'<tfoot><tr><th class="header_l">TOTAL</th>{total_cells}'
        f'<th class="header_r">{fmt_bytes(sum(hour_totals))}</th></tr></tfoot>'
        "</table></div>"
    )
    return render_document(
        site_title,
        "Date/time report",
        version,
        updated,
        "../../../",
        [
            (f"Period: {period.label}", False),
            (f"User: {name}", False),
            ("Date/time report", True),
        ],
        user_report_links(slug) + table,
    )


def render_graph_svg(rows: list[HistoryRow]) -> str:
    unique_days = {row.timestamp.strftime("%Y-%m-%d") for row in rows}
    if len(unique_days) > 1:
        groups = grouped_summaries(
            rows,
            lambda row: row.timestamp.strftime("%Y-%m-%d"),
        )
        groups = [
            (datetime.strptime(label, "%Y-%m-%d").strftime("%d/%m"), summary)
            for label, summary in groups
        ]
    else:
        groups = hourly_summaries(rows)

    width, height = 900, 360
    left, top, right, bottom = 70, 35, 25, 65
    plot_width = width - left - right
    plot_height = height - top - bottom
    maximum = max((max(item.rx_bytes, item.tx_bytes) for _, item in groups), default=0)
    maximum = max(maximum, 1)
    slot = plot_width / max(len(groups), 1)
    bar_width = max(2.0, min(12.0, slot * 0.32))
    bars: list[str] = []
    labels: list[str] = []
    for index, (label, item) in enumerate(groups):
        center = left + slot * index + slot / 2
        rx_height = item.rx_bytes * plot_height / maximum
        tx_height = item.tx_bytes * plot_height / maximum
        bars.append(
            f'<rect x="{center - bar_width - 1:.1f}" y="{top + plot_height - rx_height:.1f}" width="{bar_width:.1f}" height="{rx_height:.1f}" fill="#436EEE"/>'
            f'<rect x="{center + 1:.1f}" y="{top + plot_height - tx_height:.1f}" width="{bar_width:.1f}" height="{tx_height:.1f}" fill="#FF8C00"/>'
        )
        labels.append(
            f'<text x="{center:.1f}" y="{height - 38}" text-anchor="middle">{html.escape(label)}</text>'
        )
    grid: list[str] = []
    for step in range(6):
        y = top + plot_height * step / 5
        value = int(maximum * (5 - step) / 5)
        grid.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}" stroke="#d8d8d8"/>'
            f'<text x="{left - 8}" y="{y + 3:.1f}" text-anchor="end">{html.escape(fmt_bytes(value))}</text>'
        )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="RX and TX traffic graph">
<rect width="100%" height="100%" fill="white"/>
<g font-family="Tahoma,Verdana,Arial,sans-serif" font-size="9" fill="#000">
{''.join(grid)}
<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_height}" stroke="#333"/>
<line x1="{left}" y1="{top + plot_height}" x2="{width - right}" y2="{top + plot_height}" stroke="#333"/>
{''.join(bars)}
{''.join(labels)}
<rect x="{left}" y="10" width="10" height="10" fill="#436EEE"/><text x="{left + 15}" y="19">RX</text>
<rect x="{left + 55}" y="10" width="10" height="10" fill="#FF8C00"/><text x="{left + 70}" y="19">TX</text>
</g>
</svg>
"""


def render_user_graph(
    site_title: str,
    version: str,
    updated: str,
    period: PeriodReport,
    summary: TrafficSummary,
    names: dict[str, str],
) -> str:
    name = resolved_name(summary, names)
    slug = user_slug(summary.peer)
    content = (
        user_report_links(slug)
        + '<div class="report graph-report"><table cellpadding="0" cellspacing="2">'
        '<tr><td><img src="graph.svg" alt="RX/TX traffic graph"></td></tr>'
        "</table></div>"
    )
    return render_document(
        site_title,
        "Graphic report",
        version,
        updated,
        "../../../",
        [
            (f"Period: {period.label}", False),
            (f"User: {name}", False),
            ("Graphic report", True),
        ],
        content,
    )


AWGSTAT_LOGO = """<svg xmlns="http://www.w3.org/2000/svg" width="112" height="36" viewBox="0 0 112 36">
<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#eaffff"/><stop offset=".45" stop-color="#16b8d4"/><stop offset="1" stop-color="#006699"/></linearGradient></defs>
<rect width="112" height="36" fill="white"/>
<text x="4" y="25" font-family="Verdana,Tahoma,Arial,sans-serif" font-size="20" font-weight="bold" font-style="italic" fill="url(#g)" stroke="#004b6b" stroke-width=".55">AWGStat</text>
<path d="M4 29h102M8 32h94" stroke="#006699" stroke-width="1.4"/>
</svg>
"""

GRAPH_ICON = """<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 14 14">
<rect width="14" height="14" fill="white"/><rect x="1" y="7" width="3" height="6" fill="#0066ff"/><rect x="5.5" y="3" width="3" height="10" fill="#00b530"/><rect x="10" y="8" width="3" height="5" fill="#ff00bd"/><path d="M.5 13.5h13" stroke="#222"/>
</svg>
"""

DATETIME_ICON = """<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 14 14">
<rect x=".5" y="2.5" width="9" height="10" fill="#fff" stroke="#111"/><path d="M1 5h8M3 1v3M7 1v3" stroke="#111"/><path d="M2 7h2v2H2zM5 7h2v2H5zM2 10h2v2H2z" fill="#e44"/><circle cx="10.5" cy="9.5" r="3" fill="#fff" stroke="#111"/><path d="M10.5 7.7v2l1.3.8" fill="none" stroke="#111"/>
</svg>
"""


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def generate_site(
    output: Path,
    cfg: dict[str, str],
    rows: list[HistoryRow],
    names: dict[str, str],
    now: datetime,
) -> dict[str, int]:
    site_title = cfg.get("TITLE", "AWGStat")
    version = read_version(cfg)
    updated = now.strftime("%d/%m/%Y %H:%M")
    detail_limit = config_int(cfg, "DETAIL_ROWS", 200, maximum=5000)

    periods: dict[str, list[PeriodReport]] = {}
    for kind in CALENDAR_REPORT_KINDS:
        limit = config_int(
            cfg,
            KIND_CONFIG_KEYS[kind],
            KIND_DEFAULT_LIMITS[kind],
            maximum=1000,
        )
        periods[kind] = build_periods(rows, kind, now, limit)
    periods["total"] = build_all_time_report(rows, now)

    write_text(output / "images" / "awgstat.svg", AWGSTAT_LOGO)
    write_text(output / "images" / "graph.svg", GRAPH_ICON)
    write_text(output / "images" / "datetime.svg", DATETIME_ICON)
    write_text(
        output / "index.html",
        render_root(site_title, version, updated, periods),
    )

    page_count = 1
    user_page_count = 0
    for kind in REPORT_KINDS:
        write_text(
            output / kind / "index.html",
            render_category(site_title, version, updated, kind, periods[kind]),
        )
        page_count += 1
        for period in periods[kind]:
            period_root = output / kind / period_dirname(period)
            write_text(
                period_root / "index.html",
                render_period(site_title, version, updated, period, names),
            )
            write_text(
                period_root / "sarg-date",
                f"{now:%Y-%m-%d %H:%M:%S} {int(bool(now.dst()))}\n",
            )
            write_text(period_root / "sarg-users", f"{len(period.users)}\n")
            write_text(
                period_root / "sarg-general",
                f"TOTAL\t{period.samples}\t{period.total}\t0\t{period.rx_bytes}\t{period.tx_bytes}\n",
            )
            page_count += 1

            rows_by_peer: dict[str, list[HistoryRow]] = defaultdict(list)
            for row in period.rows:
                rows_by_peer[row.peer].append(row)
            for peer in sorted(period.users):
                summary = period.users[peer]
                slug = user_slug(peer)
                user_root = period_root / slug
                peer_rows = rows_by_peer[peer]
                write_text(
                    user_root / f"{slug}.html",
                    render_user(
                        site_title,
                        version,
                        updated,
                        period,
                        summary,
                        peer_rows,
                        names,
                        detail_limit,
                    ),
                )
                write_text(
                    user_root / f"d{slug}.html",
                    render_user_datetime(
                        site_title,
                        version,
                        updated,
                        period,
                        summary,
                        peer_rows,
                        names,
                    ),
                )
                write_text(
                    user_root / "graph.html",
                    render_user_graph(
                        site_title,
                        version,
                        updated,
                        period,
                        summary,
                        names,
                    ),
                )
                write_text(user_root / "graph.svg", render_graph_svg(peer_rows))
                page_count += 3
                user_page_count += 1

    return {
        "pages": page_count,
        "user_pages": user_page_count,
        "periods": sum(len(items) for items in periods.values()),
    }


def names_map_changed(names_path: Path, index_path: Path) -> bool:
    if not names_path.exists():
        return False
    if not index_path.exists():
        return True
    return names_path.stat().st_mtime > index_path.stat().st_mtime


def remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def publish_site(stage: Path, webroot: Path) -> None:
    """Transactionally replace AWGStat-owned output and preserve other files."""
    webroot.mkdir(parents=True, exist_ok=True)
    owned = (*OWNED_DIRECTORIES, *OWNED_FILES)
    backups: dict[str, Path] = {}
    installed: list[Path] = []

    for name in owned:
        source = stage / name
        if not source.exists():
            raise FileNotFoundError(f"staged report output is missing: {source}")
        target = webroot / name
        backup = webroot / f".awgstat-old-{name}"
        if backup.exists():
            if target.exists():
                remove_path(backup)
            else:
                backup.replace(target)

    try:
        for name in owned:
            target = webroot / name
            backup = webroot / f".awgstat-old-{name}"
            if target.exists():
                target.replace(backup)
                backups[name] = backup
        for name in owned:
            target = webroot / name
            (stage / name).replace(target)
            installed.append(target)
    except Exception:
        for target in reversed(installed):
            remove_path(target)
        for name, backup in backups.items():
            target = webroot / name
            if backup.exists() and not target.exists():
                backup.replace(target)
        raise

    for backup in backups.values():
        remove_path(backup)

    # v1.x owned this directory; remove it only after the v2 tree is complete.
    for legacy_name in ("reports", ".reports-old"):
        legacy = webroot / legacy_name
        if legacy.exists():
            remove_path(legacy)


def main() -> int:
    cfg = load_config()
    changed = expand(cfg["CHANGED"], cfg)
    history = expand(cfg["HISTORY"], cfg)
    names_path = expand(cfg["NAMES"], cfg)
    webroot = Path(cfg["WEBROOT"])
    index_path = webroot / "index.html"
    force = "--force" in sys.argv

    need_rebuild = (
        force
        or changed.exists()
        or not index_path.exists()
        or any(not (webroot / name).exists() for name in OWNED_DIRECTORIES)
        or names_map_changed(names_path, index_path)
    )
    if not need_rebuild:
        return 0

    tz = resolve_tz(cfg.get("REPORT_TZ", "Europe/Moscow"))
    now = datetime.now(tz)
    names = load_names(names_path)
    rows = read_history(history, tz)

    static_src = SCRIPT_DIR / "style.css"
    webroot.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".awgstat-build-",
        dir=webroot.parent,
    ) as temporary:
        stage = Path(temporary)
        generate_site(stage, cfg, rows, names, now)
        if static_src.exists():
            shutil.copy2(static_src, stage / "style.css")
        else:
            write_text(stage / "style.css", "")
        publish_site(stage, webroot)

    changed.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
