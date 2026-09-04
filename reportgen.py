#!/usr/bin/env python3
"""Generate classic SARG-style static reports from AWGStat history."""

from __future__ import annotations

import hashlib
import html
import json
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
OWNED_DIRECTORIES = (*REPORT_KINDS, "online", "images")
OWNED_FILES = ("index.html", "style.css")
MONTH_ABBREVIATIONS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)
ONLINE_GRAPH_PERIODS = (
    (60, "LAST 60 MINUTES"),
    (360, "LAST 6 HOURS"),
    (720, "LAST 12 HOURS"),
    (1440, "LAST 24 HOURS"),
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


@dataclass(frozen=True)
class OnlinePeer:
    timestamp: datetime
    peer: str
    name: str
    ip: str
    rx_bytes: int
    tx_bytes: int
    interval: int
    handshake: int
    rx_total: int
    tx_total: int

    @property
    def total(self) -> int:
        return self.rx_bytes + self.tx_bytes

    @property
    def rx_rate(self) -> float:
        return self.rx_bytes / self.interval if self.interval > 0 else 0.0

    @property
    def tx_rate(self) -> float:
        return self.tx_bytes / self.interval if self.interval > 0 else 0.0

    @property
    def total_rate(self) -> float:
        return self.rx_rate + self.tx_rate

    @property
    def wg_total(self) -> int:
        return self.rx_total + self.tx_total


@dataclass
class OnlineSnapshot:
    timestamp: datetime | None = None
    peers: list[OnlinePeer] = field(default_factory=list)


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


def fmt_rate(value: float) -> str:
    value = max(value, 0.0)
    if 0 < value < 1:
        return f"{value:.1f} B/s"
    return f"{fmt_bytes(round(value))}/s"


def fmt_age(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    value = max(0, int(seconds))
    if value < 60:
        return f"{value} s"
    minutes, seconds = divmod(value, 60)
    if minutes < 60:
        return f"{minutes} min {seconds} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min"


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


def read_online_state(path: Path, tz=timezone.utc) -> OnlineSnapshot:
    if not path.exists():
        return OnlineSnapshot()

    snapshot_time: datetime | None = None
    peers: dict[str, OnlinePeer] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for line in handle:
            if line.startswith("#AWGSTAT-ONLINE:"):
                marker = line.rstrip("\r\n").split(";", 1)
                if len(marker) == 2:
                    try:
                        snapshot_time = datetime.fromtimestamp(
                            int(marker[1]),
                            tz=timezone.utc,
                        ).astimezone(tz)
                    except (ValueError, OSError, OverflowError):
                        snapshot_time = None
                continue
            if line.startswith("sample_timestamp;") or not line.strip():
                continue
            parts = line.rstrip("\r\n").split(";")
            if len(parts) < 10:
                continue
            try:
                timestamp = datetime.fromtimestamp(
                    int(parts[0]),
                    tz=timezone.utc,
                ).astimezone(tz)
            except (ValueError, OSError, OverflowError):
                continue
            peer = parts[1].strip()
            if not peer:
                continue
            peers[peer] = OnlinePeer(
                timestamp=timestamp,
                peer=peer,
                name=parts[2].strip(),
                ip=parts[3].strip(),
                rx_bytes=parse_nonnegative_int(parts[4]),
                tx_bytes=parse_nonnegative_int(parts[5]),
                interval=parse_nonnegative_int(parts[6]),
                handshake=parse_nonnegative_int(parts[7]),
                rx_total=parse_nonnegative_int(parts[8]),
                tx_total=parse_nonnegative_int(parts[9]),
            )

    ordered = sorted(peers.values(), key=lambda item: item.peer)
    if snapshot_time is None and ordered:
        snapshot_time = max(item.timestamp for item in ordered)
    return OnlineSnapshot(timestamp=snapshot_time, peers=ordered)


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


def recent_history_rows(
    rows: Iterable[HistoryRow],
    now: datetime,
    window_minutes: int,
) -> list[HistoryRow]:
    cutoff = now - timedelta(minutes=max(window_minutes, 1))
    return [
        row
        for row in rows
        if cutoff <= row.timestamp <= now
    ]


def online_period_totals(
    rows: Iterable[HistoryRow],
    now: datetime,
) -> dict[str, tuple[int, int, int]]:
    """Return per-peer totals for today, this month, and the rolling 30 days."""
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = today_start.replace(day=1)
    last_30_days_start = now - timedelta(days=30)
    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for row in rows:
        if row.timestamp > now:
            continue
        if row.timestamp >= today_start:
            totals[row.peer][0] += row.total
        if row.timestamp >= month_start:
            totals[row.peer][1] += row.total
        if row.timestamp >= last_30_days_start:
            totals[row.peer][2] += row.total
    return {
        peer: (values[0], values[1], values[2])
        for peer, values in totals.items()
    }


def build_rate_series(
    rows: Iterable[HistoryRow],
    now: datetime,
    window_minutes: int,
) -> list[tuple[datetime, float, float]]:
    count = max(window_minutes, 1)
    end = now.replace(second=0, microsecond=0)
    start = end - timedelta(minutes=count - 1)
    rates: dict[datetime, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for row in rows:
        minute = row.timestamp.replace(second=0, microsecond=0)
        if minute < start or minute > end:
            continue
        interval = row.interval if row.interval > 0 else 60
        rates[minute][0] += row.rx_bytes / interval
        rates[minute][1] += row.tx_bytes / interval
    return [
        (
            start + timedelta(minutes=index),
            rates[start + timedelta(minutes=index)][0],
            rates[start + timedelta(minutes=index)][1],
        )
        for index in range(count)
    ]


def build_online_graph_points(
    rows: Iterable[HistoryRow],
    now: datetime,
) -> list[tuple[int, float, float]]:
    """Build compact non-zero per-minute rates for interactive graph ranges."""
    rates: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for row in rows:
        if row.timestamp > now or row.total <= 0:
            continue
        minute = int(row.timestamp.timestamp()) // 60 * 60
        interval = row.interval if row.interval > 0 else 60
        rates[minute][0] += row.rx_bytes / interval
        rates[minute][1] += row.tx_bytes / interval
    return [
        (minute, round(values[0], 3), round(values[1], 3))
        for minute, values in sorted(rates.items())
    ]


def render_online_graph_data(
    rows: Iterable[HistoryRow],
    now: datetime,
) -> str:
    points = build_online_graph_points(rows, now)
    payload = {
        "version": 1,
        "generated": int(now.timestamp()),
        "timezone": str(now.tzinfo or ""),
        "first": points[0][0] if points else None,
        "last": points[-1][0] if points else None,
        "points": points,
    }
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n"


def snapshot_age(snapshot: OnlineSnapshot, now: datetime) -> float | None:
    if snapshot.timestamp is None:
        return None
    return max(0.0, (now - snapshot.timestamp).total_seconds())


def online_snapshot_status(
    snapshot: OnlineSnapshot,
    now: datetime,
    stale_minutes: int,
) -> str:
    age = snapshot_age(snapshot, now)
    if age is None:
        return "NO SNAPSHOT"
    if age > max(stale_minutes, 1) * 60:
        return "STALE"
    return "FRESH"


def handshake_age(peer: OnlinePeer, now: datetime) -> float | None:
    if peer.handshake <= 0:
        return None
    try:
        handshake = datetime.fromtimestamp(
            peer.handshake,
            tz=timezone.utc,
        ).astimezone(now.tzinfo)
    except (ValueError, OSError, OverflowError):
        return None
    return max(0.0, (now - handshake).total_seconds())


def online_peer_status(
    peer: OnlinePeer,
    snapshot: OnlineSnapshot,
    now: datetime,
    active_minutes: int,
    stale_minutes: int,
) -> str:
    state = online_snapshot_status(snapshot, now, stale_minutes)
    if state != "FRESH":
        return state
    if peer.total > 0:
        return "TRAFFIC"
    age = handshake_age(peer, now)
    if age is not None and age <= max(active_minutes, 1) * 60:
        return "ACTIVE"
    return "IDLE"


def resolved_online_name(peer: OnlinePeer, names: dict[str, str]) -> str:
    return lookup_name(names, peer.peer, peer.name) or "неизвестный"


def nav_markup(root_prefix: str) -> str:
    links = [
        (f"{root_prefix}index.html", "REPORT INDEX"),
        (f"{root_prefix}daily/index.html", "DAILY"),
        (f"{root_prefix}weekly/index.html", "WEEKLY"),
        (f"{root_prefix}monthly/index.html", "MONTHLY"),
        (f"{root_prefix}total/index.html", "ALL TIME"),
        (f"{root_prefix}online/index.html", "ONLINE"),
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
    refresh_seconds: int = 0,
) -> str:
    rows = [f'<tr><th class="title_c">{html.escape(site_title)}</th></tr>']
    for text, strong in header_rows:
        tag = "th" if strong else "td"
        rows.append(f'<tr><{tag} class="header_c">{html.escape(text)}</{tag}></tr>')
    refresh = ""
    if refresh_seconds > 0:
        refresh = (
            f'  <meta http-equiv="refresh" content="{refresh_seconds}">\n'
            '  <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">\n'
        )
    return f"""<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01//EN" "http://www.w3.org/TR/html4/strict.dtd">
<html lang="ru">
<head>
  <meta http-equiv="Content-Type" content="text/html; charset=UTF-8">
  <meta name="generator" content="AWGStat {html.escape(version)}">
{refresh}  <title>{html.escape(page_title)}</title>
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
    snapshot: OnlineSnapshot,
    history_rows: list[HistoryRow],
    now: datetime,
    window_minutes: int,
    active_minutes: int,
    stale_minutes: int,
    refresh_seconds: int,
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
    recent = recent_history_rows(history_rows, now, window_minutes)
    recent_users = aggregate_rows(recent)
    recent_total = sum(item.total for item in recent)
    state = online_snapshot_status(snapshot, now, stale_minutes)
    statuses = [
        online_peer_status(
            peer,
            snapshot,
            now,
            active_minutes,
            stale_minutes,
        )
        for peer in snapshot.peers
    ]
    active_count = sum(status in {"TRAFFIC", "ACTIVE"} for status in statuses)
    current_rate = (
        sum(peer.total_rate for peer in snapshot.peers)
        if state == "FRESH"
        else 0.0
    )
    rows.append(
        "<tr>"
        '<td class="data2"><a href="online/index.html">ONLINE REPORT</a></td>'
        '<td class="data">LIVE</td>'
        f'<td class="data2">Last {window_minutes} min · {html.escape(state)}</td>'
        f'<td class="data">{fmt_count(active_count)} / {fmt_count(len(recent_users))}</td>'
        f'<td class="data">{fmt_bytes(recent_total)}</td>'
        f'<td class="data">{fmt_rate(current_rate)}</td>'
        "</tr>"
    )
    content = (
        '<div class="index"><table cellpadding="1" cellspacing="2">'
        '<thead><tr><th class="header_l">REPORT TYPE</th>'
        '<th class="header_l">PERIODS</th>'
        '<th class="header_l">LATEST PERIOD</th>'
        '<th class="header_l">USERS</th>'
        '<th class="header_l">BYTES</th>'
        '<th class="header_l">AVERAGE / RATE</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div>"
        '<div class="report-note">ONLINE users: active now / seen in the recent window.</div>'
    )
    return render_document(
        site_title,
        "AWGStat reports",
        version,
        updated,
        "",
        [("AWGStat reports", True)],
        content,
        refresh_seconds,
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


def status_class(status: str) -> str:
    if status in {"FRESH", "TRAFFIC"}:
        return "online-status-traffic"
    if status == "ACTIVE":
        return "online-status-active"
    if status in {"STALE", "NO SNAPSHOT"}:
        return "online-status-stale"
    return "online-status-idle"


def status_markup(status: str) -> str:
    return (
        f'<span class="online-status {status_class(status)}">'
        f"{html.escape(status)}</span>"
    )


def handshake_text(peer: OnlinePeer, now: datetime) -> str:
    age = handshake_age(peer, now)
    if age is None:
        return "never"
    handshake = datetime.fromtimestamp(
        peer.handshake,
        tz=timezone.utc,
    ).astimezone(now.tzinfo)
    return f"{fmt_datetime(handshake)} ({fmt_age(age)} ago)"


def render_online_graph_svg(
    rows: list[HistoryRow],
    now: datetime,
    window_minutes: int,
    title: str,
) -> str:
    series = build_rate_series(rows, now, window_minutes)
    width, height = 900, 360
    left, top, right, bottom = 76, 38, 25, 62
    plot_width = width - left - right
    plot_height = height - top - bottom
    maximum = max(
        (max(rx_rate, tx_rate) for _, rx_rate, tx_rate in series),
        default=0.0,
    )
    maximum = max(maximum, 1.0)

    def point(index: int, value: float) -> tuple[float, float]:
        divisor = max(len(series) - 1, 1)
        x = left + plot_width * index / divisor
        y = top + plot_height - value * plot_height / maximum
        return x, y

    rx_points = " ".join(
        f"{x:.1f},{y:.1f}"
        for index, (_, rx_rate, _) in enumerate(series)
        for x, y in [point(index, rx_rate)]
    )
    tx_points = " ".join(
        f"{x:.1f},{y:.1f}"
        for index, (_, _, tx_rate) in enumerate(series)
        for x, y in [point(index, tx_rate)]
    )
    grid: list[str] = []
    for step in range(6):
        y = top + plot_height * step / 5
        value = maximum * (5 - step) / 5
        grid.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}" stroke="#d8d8d8"/>'
            f'<text x="{left - 8}" y="{y + 3:.1f}" text-anchor="end">{html.escape(fmt_rate(value))}</text>'
        )

    labels: list[str] = []
    label_step = max(1, len(series) // 6)
    label_indexes = set(range(0, len(series), label_step))
    if series:
        label_indexes.add(len(series) - 1)
    for index in sorted(label_indexes):
        timestamp = series[index][0]
        x, _ = point(index, 0)
        labels.append(
            f'<text x="{x:.1f}" y="{height - 35}" text-anchor="middle">'
            f"{timestamp:%H:%M}</text>"
        )

    safe_title = html.escape(title)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{safe_title}">
<title>{safe_title}</title>
<rect width="100%" height="100%" fill="white"/>
<g font-family="Tahoma,Verdana,Arial,sans-serif" font-size="9" fill="#000">
<text x="{left}" y="17" font-weight="bold">{safe_title}</text>
{''.join(grid)}
<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_height}" stroke="#333"/>
<line x1="{left}" y1="{top + plot_height}" x2="{width - right}" y2="{top + plot_height}" stroke="#333"/>
<polyline points="{rx_points}" fill="none" stroke="#436EEE" stroke-width="2"/>
<polyline points="{tx_points}" fill="none" stroke="#FF8C00" stroke-width="2"/>
{''.join(labels)}
<rect x="{width - 150}" y="10" width="10" height="10" fill="#436EEE"/><text x="{width - 135}" y="19">RX/s</text>
<rect x="{width - 85}" y="10" width="10" height="10" fill="#FF8C00"/><text x="{width - 70}" y="19">TX/s</text>
</g>
</svg>
"""


def render_online_summary(
    snapshot: OnlineSnapshot,
    recent_rows: list[HistoryRow],
    now: datetime,
    active_minutes: int,
    stale_minutes: int,
    refresh_seconds: int,
) -> str:
    state = online_snapshot_status(snapshot, now, stale_minutes)
    age = snapshot_age(snapshot, now)
    statuses = [
        online_peer_status(
            peer,
            snapshot,
            now,
            active_minutes,
            stale_minutes,
        )
        for peer in snapshot.peers
    ]
    active_count = sum(status in {"TRAFFIC", "ACTIVE"} for status in statuses)
    recent_total = sum(row.total for row in recent_rows)
    recent_users = len(aggregate_rows(recent_rows))
    last_traffic = max(
        (row.timestamp for row in recent_rows),
        default=None,
    )
    if state == "FRESH":
        rx_rate = fmt_rate(sum(peer.rx_rate for peer in snapshot.peers))
        tx_rate = fmt_rate(sum(peer.tx_rate for peer in snapshot.peers))
        total_rate = fmt_rate(sum(peer.total_rate for peer in snapshot.peers))
    else:
        rx_rate = tx_rate = total_rate = "—"
    rows = (
        f'<tr><th class="header_l">COLLECTOR STATUS</th><td class="data2">{status_markup(state)}</td></tr>'
        f'<tr><th class="header_l">SNAPSHOT</th><td class="data2">{html.escape(fmt_datetime(snapshot.timestamp))}</td></tr>'
        f'<tr><th class="header_l">SNAPSHOT AGE</th><td class="data2">{html.escape(fmt_age(age))}</td></tr>'
        f'<tr><th class="header_l">AUTO REFRESH</th><td class="data2">{refresh_seconds} s</td></tr>'
        f'<tr><th class="header_l">ACTIVE PEERS</th><td class="data2">{active_count} / {len(snapshot.peers)}</td></tr>'
        f'<tr><th class="header_l">RECENT USERS</th><td class="data2">{recent_users}</td></tr>'
        f'<tr><th class="header_l">CURRENT RX / TX</th><td class="data2">{rx_rate} / {tx_rate}</td></tr>'
        f'<tr><th class="header_l">CURRENT TOTAL RATE</th><td class="data2">{total_rate}</td></tr>'
        f'<tr><th class="header_l">WINDOW TRAFFIC</th><td class="data2">{fmt_bytes(recent_total)}</td></tr>'
        f'<tr><th class="header_l">LAST TRAFFIC</th><td class="data2">{html.escape(fmt_datetime(last_traffic))}</td></tr>'
    )
    return (
        '<div class="report"><table class="online-summary" cellpadding="2" cellspacing="1">'
        f"<tbody>{rows}</tbody></table></div>"
    )


def render_online_peers_table(
    snapshot: OnlineSnapshot,
    history_rows: list[HistoryRow],
    now: datetime,
    names: dict[str, str],
    active_minutes: int,
    stale_minutes: int,
) -> str:
    state = online_snapshot_status(snapshot, now, stale_minutes)
    period_totals = online_period_totals(history_rows, now)
    order = {"TRAFFIC": 0, "ACTIVE": 1, "IDLE": 2, "STALE": 3, "NO SNAPSHOT": 4}
    peers = sorted(
        snapshot.peers,
        key=lambda peer: (
            order.get(
                online_peer_status(
                    peer,
                    snapshot,
                    now,
                    active_minutes,
                    stale_minutes,
                ),
                9,
            ),
            -peer.total_rate,
            resolved_online_name(peer, names).casefold(),
        ),
    )
    body: list[str] = []
    for rank, peer in enumerate(peers, start=1):
        status = online_peer_status(
            peer,
            snapshot,
            now,
            active_minutes,
            stale_minutes,
        )
        slug = user_slug(peer.peer)
        today, this_month, last_30_days = period_totals.get(
            peer.peer,
            (0, 0, 0),
        )
        rate_cells = (
            f'<td class="data">{fmt_rate(peer.rx_rate)}</td>'
            f'<td class="data">{fmt_rate(peer.tx_rate)}</td>'
            f'<td class="data">{fmt_rate(peer.total_rate)}</td>'
            if state == "FRESH"
            else '<td class="data">—</td><td class="data">—</td><td class="data">—</td>'
        )
        body.append(
            "<tr>"
            f'<td class="data">{rank}</td>'
            f'<td class="data2"><a href="{slug}/index.html"><img class="report-icon" src="../images/graph.svg" title="Live graphic report" alt="G"></a></td>'
            f'<td class="data2">{status_markup(status)}</td>'
            f'<td class="data2"><a href="{slug}/index.html">{html.escape(resolved_online_name(peer, names))}</a></td>'
            f'<td class="data2">{html.escape(clean_ip(peer.ip) or "—")}</td>'
            f'<td class="data2">{html.escape(handshake_text(peer, now))}</td>'
            f'<td class="data">{fmt_count(peer.interval)} s</td>'
            f"{rate_cells}"
            f'<td class="data">{fmt_bytes(today)}</td>'
            f'<td class="data">{fmt_bytes(this_month)}</td>'
            f'<td class="data">{fmt_bytes(last_30_days)}</td>'
            f'<td class="data">{fmt_bytes(peer.wg_total)}</td>'
            "</tr>"
        )
    if not body:
        body.append(empty_row(14, "No peers in the current WireGuard snapshot"))
    return (
        '<div class="report report-scroll"><table cellpadding="1" cellspacing="2">'
        '<thead><tr><th class="header_l">NUM</th><th class="header_l"></th>'
        '<th class="header_l">STATUS</th><th class="header_l">USERID</th>'
        '<th class="header_l">USERIP</th><th class="header_l">LAST HANDSHAKE</th>'
        '<th class="header_l">SAMPLE</th><th class="header_l">RX/s</th>'
        '<th class="header_l">TX/s</th><th class="header_l">TOTAL/s</th>'
        '<th class="header_l">TODAY</th><th class="header_l">THIS MONTH</th>'
        '<th class="header_l">LAST 30 DAYS</th>'
        '<th class="header_l" title="Since the WireGuard interface was created">WG COUNTERS</th></tr></thead>'
        f"<tbody>{''.join(body)}</tbody></table></div>"
        '<div class="report-note">TODAY and THIS MONTH use the report timezone; LAST 30 DAYS is a rolling 30-day total. WG COUNTERS reset when the WireGuard interface is recreated. TRAFFIC means bytes changed in the latest sample; ACTIVE means a recent handshake.</div>'
    )


def render_online_ranking(
    recent_rows: list[HistoryRow],
    names: dict[str, str],
    window_minutes: int,
) -> str:
    summaries = aggregate_rows(recent_rows)
    ordered = sorted(
        summaries.values(),
        key=lambda item: (-item.total, resolved_name(item, names).casefold()),
    )
    total = sum(item.total for item in ordered)
    body: list[str] = []
    for rank, summary in enumerate(ordered, start=1):
        slug = user_slug(summary.peer)
        average_rate = summary.total / max(window_minutes * 60, 1)
        body.append(
            "<tr>"
            f'<td class="data">{rank}</td>'
            f'<td class="data2"><a href="{slug}/index.html"><img class="report-icon" src="../images/graph.svg" title="Live graphic report" alt="G"></a></td>'
            f'<td class="data2"><a href="{slug}/index.html">{html.escape(resolved_name(summary, names))}</a></td>'
            f'<td class="data2">{html.escape(summary.ip or "—")}</td>'
            f'<td class="data">{fmt_count(summary.samples)}</td>'
            f'<td class="data">{fmt_datetime(summary.last_seen)}</td>'
            f'<td class="data">{fmt_bytes(summary.rx_bytes)}</td>'
            f'<td class="data">{fmt_bytes(summary.tx_bytes)}</td>'
            f'<td class="data">{fmt_bytes(summary.total)}</td>'
            f'<td class="data">{fmt_rate(average_rate)}</td>'
            f'<td class="data">{fmt_percent(summary.total, total)}</td>'
            "</tr>"
        )
    if not body:
        body.append(empty_row(11, "No traffic in the recent window"))
    return (
        '<div class="report report-scroll"><table cellpadding="1" cellspacing="2">'
        '<thead><tr><th class="header_l">NUM</th><th class="header_l"></th>'
        '<th class="header_l">USERID</th><th class="header_l">USERIP</th>'
        '<th class="header_l">SAMPLES</th><th class="header_l">LAST TRAFFIC</th>'
        '<th class="header_l">RX</th><th class="header_l">TX</th>'
        '<th class="header_l">BYTES</th><th class="header_l">AVG RATE</th>'
        '<th class="header_l">%BYTES</th></tr></thead>'
        f"<tbody>{''.join(body)}</tbody></table></div>"
    )


def render_online(
    site_title: str,
    version: str,
    updated: str,
    snapshot: OnlineSnapshot,
    history_rows: list[HistoryRow],
    recent_rows: list[HistoryRow],
    names: dict[str, str],
    now: datetime,
    window_minutes: int,
    active_minutes: int,
    stale_minutes: int,
    refresh_seconds: int,
    graph_filenames: dict[int, str],
) -> str:
    default_graph_minutes = (
        window_minutes
        if window_minutes in graph_filenames
        else ONLINE_GRAPH_PERIODS[0][0]
    )
    graph_options = []
    for minutes, label in ONLINE_GRAPH_PERIODS:
        selected = ' selected="selected"' if minutes == default_graph_minutes else ""
        graph_options.append(
            f'<option value="{minutes}" data-graph-src="'
            f'{html.escape(graph_filenames[minutes], quote=True)}"{selected}>'
            f"{html.escape(label)}</option>"
        )
    graph_options.append('<option value="custom">CUSTOM DATE / TIME</option>')
    custom_end = now.replace(second=0, microsecond=0)
    custom_start = custom_end - timedelta(minutes=60)
    controls = (
        '<div class="graph-controls">'
        '<label for="traffic-period">GRAPH PERIOD</label> '
        f'<select id="traffic-period">{"".join(graph_options)}</select> '
        '<span id="custom-period" class="custom-period">'
        '<label for="traffic-from">FROM</label> '
        f'<input id="traffic-from" type="datetime-local" step="60" value="{custom_start:%Y-%m-%dT%H:%M}"> '
        '<label for="traffic-to">TO</label> '
        f'<input id="traffic-to" type="datetime-local" step="60" value="{custom_end:%Y-%m-%dT%H:%M}"> '
        '<button id="traffic-apply" type="button">APPLY</button>'
        "</span>"
        '<span id="traffic-graph-status" class="graph-status" aria-live="polite"></span>'
        "</div>"
    )
    graph = (
        '<div class="report graph-report"><table cellpadding="0" cellspacing="2">'
        '<tr><td>'
        f'<img id="traffic-graph-image" src="{html.escape(graph_filenames[default_graph_minutes], quote=True)}" alt="Recent RX/TX rate graph">'
        '<svg id="traffic-custom-graph" class="custom-traffic-graph" '
        'xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 360" '
        'role="img" aria-label="Custom RX/TX rate graph"></svg>'
        "</td></tr>"
        "</table></div>"
    )
    content = (
        render_online_summary(
            snapshot,
            recent_rows,
            now,
            active_minutes,
            stale_minutes,
            refresh_seconds,
        )
        + '<div class="online-section">CURRENT PEERS</div>'
        + render_online_peers_table(
            snapshot,
            history_rows,
            now,
            names,
            active_minutes,
            stale_minutes,
        )
        + f'<div id="traffic-rate-title" class="online-section">TRAFFIC RATE — {html.escape(dict(ONLINE_GRAPH_PERIODS)[default_graph_minutes])}</div>'
        + controls
        + graph
        + f'<div class="online-section">TOP TRAFFIC — LAST {window_minutes} MINUTES</div>'
        + render_online_ranking(recent_rows, names, window_minutes)
        + '<div class="report-note">Rates and rankings use AWGStat sampling intervals. AmneziaWG does not expose sites, URLs, or application sessions.</div>'
        + '<script type="text/javascript" src="graph-controls.js"></script>'
    )
    return render_document(
        site_title,
        "Online traffic report",
        version,
        updated,
        "../",
        [
            (f"Window: last {window_minutes} minutes", False),
            ("ONLINE REPORT", True),
        ],
        content,
        refresh_seconds,
    )


def render_online_user(
    site_title: str,
    version: str,
    updated: str,
    peer_id: str,
    snapshot: OnlineSnapshot,
    peer_rows: list[HistoryRow],
    names: dict[str, str],
    now: datetime,
    window_minutes: int,
    active_minutes: int,
    stale_minutes: int,
    refresh_seconds: int,
    detail_limit: int,
    graph_filename: str,
) -> str:
    current = next(
        (peer for peer in snapshot.peers if peer.peer == peer_id),
        None,
    )
    summary = aggregate_rows(peer_rows).get(
        peer_id,
        TrafficSummary(peer=peer_id),
    )
    fallback_name = current.name if current else summary.name
    name = lookup_name(names, peer_id, fallback_name) or "неизвестный"
    if current is None:
        state = online_snapshot_status(snapshot, now, stale_minutes)
        status = state if state != "FRESH" else "NOT IN SNAPSHOT"
        ip = summary.ip or "—"
        sample = "—"
        handshake = "—"
        rx_rate = tx_rate = total_rate = wg_total = "—"
    else:
        status = online_peer_status(
            current,
            snapshot,
            now,
            active_minutes,
            stale_minutes,
        )
        ip = clean_ip(current.ip) or summary.ip or "—"
        sample = f"{current.interval} s"
        handshake = handshake_text(current, now)
        if online_snapshot_status(snapshot, now, stale_minutes) == "FRESH":
            rx_rate = fmt_rate(current.rx_rate)
            tx_rate = fmt_rate(current.tx_rate)
            total_rate = fmt_rate(current.total_rate)
        else:
            rx_rate = tx_rate = total_rate = "—"
        wg_total = fmt_bytes(current.wg_total)

    summary_rows = (
        f'<tr><th class="header_l">STATUS</th><td class="data2">{status_markup(status)}</td></tr>'
        f'<tr><th class="header_l">USERID</th><td class="data2">{html.escape(name)}</td></tr>'
        f'<tr><th class="header_l">USERIP</th><td class="data2">{html.escape(ip)}</td></tr>'
        f'<tr><th class="header_l">SNAPSHOT</th><td class="data2">{html.escape(fmt_datetime(snapshot.timestamp))}</td></tr>'
        f'<tr><th class="header_l">LAST HANDSHAKE</th><td class="data2">{html.escape(handshake)}</td></tr>'
        f'<tr><th class="header_l">SAMPLE</th><td class="data2">{html.escape(sample)}</td></tr>'
        f'<tr><th class="header_l">CURRENT RX / TX</th><td class="data2">{rx_rate} / {tx_rate}</td></tr>'
        f'<tr><th class="header_l">CURRENT TOTAL RATE</th><td class="data2">{total_rate}</td></tr>'
        f'<tr><th class="header_l">WINDOW RX / TX</th><td class="data2">{fmt_bytes(summary.rx_bytes)} / {fmt_bytes(summary.tx_bytes)}</td></tr>'
        f'<tr><th class="header_l">WINDOW TOTAL</th><td class="data2">{fmt_bytes(summary.total)}</td></tr>'
        f'<tr><th class="header_l">WG COUNTERS</th><td class="data2">{wg_total}</td></tr>'
    )
    content = (
        '<div class="report"><table cellpadding="2" cellspacing="1">'
        '<tr><td class="link"><a href="../index.html">ONLINE REPORT</a></td></tr>'
        "</table></div>"
        '<div class="report"><table class="online-summary" cellpadding="2" cellspacing="1">'
        f"<tbody>{summary_rows}</tbody></table></div>"
        '<div class="online-section">TRAFFIC RATE</div>'
        '<div class="report graph-report"><table cellpadding="0" cellspacing="2">'
        f'<tr><td><img src="{html.escape(graph_filename, quote=True)}" alt="User RX/TX rate graph"></td></tr>'
        "</table></div>"
        '<div class="online-section">RECENT TRAFFIC INTERVALS</div>'
        + render_user_rows(summary, peer_rows, detail_limit)
    )
    return render_document(
        site_title,
        "Online user report",
        version,
        updated,
        "../../",
        [
            (f"Window: last {window_minutes} minutes", False),
            (f"User: {name}", False),
            ("ONLINE USER REPORT", True),
        ],
        content,
        refresh_seconds,
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


def online_settings(cfg: dict[str, str]) -> tuple[int, int, int, int]:
    return (
        config_int(
            cfg,
            "ONLINE_WINDOW_MINUTES",
            60,
            minimum=5,
            maximum=1440,
        ),
        config_int(
            cfg,
            "ONLINE_ACTIVE_MINUTES",
            3,
            minimum=1,
            maximum=60,
        ),
        config_int(
            cfg,
            "ONLINE_STALE_MINUTES",
            3,
            minimum=1,
            maximum=60,
        ),
        config_int(
            cfg,
            "ONLINE_REFRESH_SECONDS",
            60,
            minimum=15,
            maximum=300,
        ),
    )


def build_report_periods(
    cfg: dict[str, str],
    rows: list[HistoryRow],
    now: datetime,
) -> dict[str, list[PeriodReport]]:
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
    return periods


def write_online_report(
    output: Path,
    cfg: dict[str, str],
    rows: list[HistoryRow],
    names: dict[str, str],
    snapshot: OnlineSnapshot,
    now: datetime,
    site_title: str,
    version: str,
    updated: str,
) -> int:
    window_minutes, active_minutes, stale_minutes, refresh_seconds = (
        online_settings(cfg)
    )
    detail_limit = config_int(cfg, "DETAIL_ROWS", 200, maximum=5000)
    recent_rows = recent_history_rows(rows, now, window_minutes)
    graph_filenames = {
        minutes: f"traffic-{minutes}-{int(now.timestamp())}.svg"
        for minutes, _label in ONLINE_GRAPH_PERIODS
    }
    user_graph_filename = f"traffic-{int(now.timestamp())}.svg"
    write_text(
        output / "online" / "index.html",
        render_online(
            site_title,
            version,
            updated,
            snapshot,
            rows,
            recent_rows,
            names,
            now,
            window_minutes,
            active_minutes,
            stale_minutes,
            refresh_seconds,
            graph_filenames,
        ),
    )
    for minutes, label in ONLINE_GRAPH_PERIODS:
        write_text(
            output / "online" / graph_filenames[minutes],
            render_online_graph_svg(
                recent_history_rows(rows, now, minutes),
                now,
                minutes,
                f"Total traffic rate — {label.lower()}",
            ),
        )
    write_text(
        output / "online" / "traffic-history.json",
        render_online_graph_data(rows, now),
    )
    shutil.copyfile(
        SCRIPT_DIR / "online.js",
        output / "online" / "graph-controls.js",
    )

    rows_by_peer: dict[str, list[HistoryRow]] = defaultdict(list)
    for row in recent_rows:
        rows_by_peer[row.peer].append(row)
    peer_ids = {peer.peer for peer in snapshot.peers}
    peer_ids.update(rows_by_peer)
    for peer_id in sorted(peer_ids):
        peer_rows = rows_by_peer[peer_id]
        slug = user_slug(peer_id)
        current = next(
            (peer for peer in snapshot.peers if peer.peer == peer_id),
            None,
        )
        fallback = current.name if current else ""
        summary = aggregate_rows(peer_rows).get(peer_id)
        if summary is not None and not fallback:
            fallback = summary.name
        name = lookup_name(names, peer_id, fallback) or "неизвестный"
        write_text(
            output / "online" / slug / "index.html",
            render_online_user(
                site_title,
                version,
                updated,
                peer_id,
                snapshot,
                peer_rows,
                names,
                now,
                window_minutes,
                active_minutes,
                stale_minutes,
                refresh_seconds,
                detail_limit,
                user_graph_filename,
            ),
        )
        write_text(
            output / "online" / slug / user_graph_filename,
            render_online_graph_svg(
                peer_rows,
                now,
                window_minutes,
                f"{name} RX/TX rate",
            ),
        )
    return 1 + len(peer_ids)


def generate_online_site(
    output: Path,
    cfg: dict[str, str],
    rows: list[HistoryRow],
    names: dict[str, str],
    now: datetime,
    snapshot: OnlineSnapshot | None = None,
) -> dict[str, int]:
    snapshot = snapshot or OnlineSnapshot()
    site_title = cfg.get("TITLE", "AWGStat")
    version = read_version(cfg)
    updated = now.strftime("%d/%m/%Y %H:%M")
    periods = build_report_periods(cfg, rows, now)
    window_minutes, active_minutes, stale_minutes, refresh_seconds = (
        online_settings(cfg)
    )
    write_text(
        output / "index.html",
        render_root(
            site_title,
            version,
            updated,
            periods,
            snapshot,
            rows,
            now,
            window_minutes,
            active_minutes,
            stale_minutes,
            refresh_seconds,
        ),
    )
    online_pages = write_online_report(
        output,
        cfg,
        rows,
        names,
        snapshot,
        now,
        site_title,
        version,
        updated,
    )
    return {
        "pages": 1 + online_pages,
        "online_user_pages": max(online_pages - 1, 0),
    }


def generate_site(
    output: Path,
    cfg: dict[str, str],
    rows: list[HistoryRow],
    names: dict[str, str],
    now: datetime,
    snapshot: OnlineSnapshot | None = None,
) -> dict[str, int]:
    snapshot = snapshot or OnlineSnapshot()
    site_title = cfg.get("TITLE", "AWGStat")
    version = read_version(cfg)
    updated = now.strftime("%d/%m/%Y %H:%M")
    detail_limit = config_int(cfg, "DETAIL_ROWS", 200, maximum=5000)

    periods = build_report_periods(cfg, rows, now)
    window_minutes, active_minutes, stale_minutes, refresh_seconds = (
        online_settings(cfg)
    )

    write_text(output / "images" / "awgstat.svg", AWGSTAT_LOGO)
    write_text(output / "images" / "graph.svg", GRAPH_ICON)
    write_text(output / "images" / "datetime.svg", DATETIME_ICON)
    write_text(
        output / "index.html",
        render_root(
            site_title,
            version,
            updated,
            periods,
            snapshot,
            rows,
            now,
            window_minutes,
            active_minutes,
            stale_minutes,
            refresh_seconds,
        ),
    )

    online_pages = write_online_report(
        output,
        cfg,
        rows,
        names,
        snapshot,
        now,
        site_title,
        version,
        updated,
    )
    page_count = 1 + online_pages
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
        "online_user_pages": max(online_pages - 1, 0),
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


def publish_owned(
    stage: Path,
    webroot: Path,
    owned: tuple[str, ...],
) -> None:
    """Transactionally replace selected AWGStat-owned output paths."""
    webroot.mkdir(parents=True, exist_ok=True)
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


def publish_site(stage: Path, webroot: Path) -> None:
    """Replace the complete AWGStat output and preserve unrelated files."""
    publish_owned(
        stage,
        webroot,
        (*OWNED_DIRECTORIES, *OWNED_FILES),
    )

    # v1.x owned this directory; remove it only after the v2 tree is complete.
    for legacy_name in ("reports", ".reports-old"):
        legacy = webroot / legacy_name
        if legacy.exists():
            remove_path(legacy)


def publish_online_site(stage: Path, webroot: Path) -> None:
    """Replace only the live report and root index."""
    publish_owned(stage, webroot, ("online", "index.html"))


def main() -> int:
    cfg = load_config()
    changed = expand(cfg["CHANGED"], cfg)
    history = expand(cfg["HISTORY"], cfg)
    names_path = expand(cfg["NAMES"], cfg)
    online_path = expand(
        cfg.get("ONLINE_STATE", "${WORKDIR}/online.csv"),
        cfg,
    )
    webroot = Path(cfg["WEBROOT"])
    index_path = webroot / "index.html"
    force = "--force" in sys.argv
    online_only = "--online" in sys.argv
    scheduled = "--scheduled" in sys.argv
    tz = resolve_tz(cfg.get("REPORT_TZ", "Europe/Moscow"))
    now = datetime.now(tz)
    key, label, start, end = period_metadata("daily", now)
    current_daily = PeriodReport("daily", key, label, start, end)

    structure_missing = (
        not index_path.exists()
        or not (webroot / "style.css").exists()
        or any(not (webroot / name).exists() for name in OWNED_DIRECTORIES)
        or not (
            webroot
            / "daily"
            / period_dirname(current_daily)
            / "index.html"
        ).exists()
    )
    need_rebuild = (
        force
        or structure_missing
        or changed.exists()
        or names_map_changed(names_path, index_path)
    )
    build_online_only = (
        (online_only and not force and not structure_missing)
        or (scheduled and not need_rebuild)
    )
    if not need_rebuild and not build_online_only:
        return 0

    names = load_names(names_path)
    rows = read_history(history, tz)
    snapshot = read_online_state(online_path, tz)

    static_src = SCRIPT_DIR / "style.css"
    webroot.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".awgstat-build-",
        dir=webroot.parent,
    ) as temporary:
        stage = Path(temporary)
        if build_online_only:
            generate_online_site(
                stage,
                cfg,
                rows,
                names,
                now,
                snapshot,
            )
            publish_online_site(stage, webroot)
        else:
            generate_site(
                stage,
                cfg,
                rows,
                names,
                now,
                snapshot,
            )
            if static_src.exists():
                shutil.copy2(static_src, stage / "style.css")
            else:
                write_text(stage / "style.css", "")
            publish_site(stage, webroot)

    if not build_online_only:
        changed.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
