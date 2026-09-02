#!/usr/bin/env python3
"""Generate a SARG-style static report tree from AWGStat history."""

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
REPORT_KINDS = ("daily", "weekly", "monthly")
KIND_TITLES = {
    "daily": "Ежедневные отчёты",
    "weekly": "Еженедельные отчёты",
    "monthly": "Ежемесячные отчёты",
}
KIND_SHORT = {
    "daily": "День",
    "weekly": "Неделя",
    "monthly": "Месяц",
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
    return value.strftime("%d.%m.%Y %H:%M") if value else "—"


def fmt_date(value: datetime) -> str:
    return value.strftime("%d.%m.%Y")


def fmt_percent(part: int, whole: int) -> str:
    if whole <= 0:
        return "0.0%"
    return f"{part * 100 / whole:.1f}%"


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
        label = fmt_date(start)
    elif kind == "weekly":
        start = midnight - timedelta(days=value.weekday())
        end = start + timedelta(days=7)
        iso = start.isocalendar()
        key = f"{iso.year}-W{iso.week:02d}"
        label = f"{fmt_date(start)} — {fmt_date(end - timedelta(days=1))}"
    elif kind == "monthly":
        start = midnight.replace(day=1)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
        key = start.strftime("%Y-%m")
        label = start.strftime("%m.%Y")
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


def current_period(periods: list[PeriodReport], kind: str, now: datetime) -> PeriodReport:
    key = period_metadata(kind, now)[0]
    for period in periods:
        if period.key == key:
            return period
    raise LookupError(f"current {kind} period was not generated")


def resolved_name(summary: TrafficSummary, names: dict[str, str]) -> str:
    return lookup_name(names, summary.peer, summary.name) or "неизвестный"


def user_slug(peer: str) -> str:
    return hashlib.sha256(peer.encode("utf-8")).hexdigest()[:16]


def share_markup(value: int, total: int) -> str:
    percent = value * 100 / total if total else 0.0
    width = min(max(percent, 0.0), 100.0)
    return (
        '<div class="share-cell">'
        f'<div class="share-bar"><span style="width:{width:.2f}%"></span></div>'
        f"<span>{percent:.1f}%</span>"
        "</div>"
    )


def breadcrumbs(items: list[tuple[str, str | None]]) -> str:
    parts: list[str] = []
    for label, href in items:
        escaped = html.escape(label)
        if href:
            parts.append(f'<a href="{html.escape(href, quote=True)}">{escaped}</a>')
        else:
            parts.append(f"<span>{escaped}</span>")
    return f'<nav class="breadcrumbs">{"<b>›</b>".join(parts)}</nav>'


def stat_card(label: str, value: str, hint: str = "", href: str | None = None) -> str:
    inner = (
        f'<span class="stat-label">{html.escape(label)}</span>'
        f'<strong>{html.escape(value)}</strong>'
        f'<small>{html.escape(hint)}</small>'
    )
    if href:
        return f'<a class="stat-card" href="{html.escape(href, quote=True)}">{inner}</a>'
    return f'<div class="stat-card">{inner}</div>'


def render_document(
    site_title: str,
    page_title: str,
    version: str,
    updated: str,
    root_prefix: str,
    content: str,
) -> str:
    escaped_site_title = html.escape(site_title)
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="generator" content="AWGStat {html.escape(version)}">
  <title>{html.escape(page_title)} — {escaped_site_title}</title>
  <link rel="stylesheet" href="{root_prefix}style.css">
</head>
<body>
  <header class="topbar">
    <div>
      <a class="brand" href="{root_prefix}index.html">{escaped_site_title}</a>
      <span class="version">v{html.escape(version)}</span>
    </div>
    <p class="updated">Обновлено: {html.escape(updated)}</p>
  </header>
  <main>
    {content}
  </main>
  <footer>
    AWGStat показывает статистику VPN-туннеля: пользователей, адреса и объём
    трафика. Посещённые сайты и URL на этом уровне недоступны.
  </footer>
</body>
</html>
"""


def render_period_table(periods: list[PeriodReport], link_prefix: str) -> str:
    rows: list[str] = []
    for period in periods:
        href = f"{link_prefix}{period.key}/index.html"
        rows.append(
            "<tr>"
            f'<td><a href="{html.escape(href, quote=True)}">{html.escape(period.label)}</a></td>'
            f'<td class="num">{fmt_count(len(period.users))}</td>'
            f'<td class="num">{fmt_count(period.samples)}</td>'
            f'<td class="num rx">{fmt_bytes(period.rx_bytes)}</td>'
            f'<td class="num tx">{fmt_bytes(period.tx_bytes)}</td>'
            f'<td class="num total">{fmt_bytes(period.total)}</td>'
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="6" class="empty">Нет данных</td></tr>')
    return (
        '<div class="table-wrap"><table>'
        "<thead><tr><th>Период</th><th class=\"num\">Пользователи</th>"
        "<th class=\"num\">Интервалы</th><th class=\"num\">RX</th>"
        "<th class=\"num\">TX</th><th class=\"num\">Всего</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def render_root_user_table(
    all_users: dict[str, TrafficSummary],
    today: PeriodReport,
    week: PeriodReport,
    month: PeriodReport,
    names: dict[str, str],
) -> str:
    rows: list[str] = []
    ordered = sorted(
        all_users.values(),
        key=lambda item: (-item.total, resolved_name(item, names).casefold(), item.ip),
    )
    for rank, summary in enumerate(ordered, start=1):
        today_total = today.users.get(summary.peer, TrafficSummary(summary.peer)).total
        week_total = week.users.get(summary.peer, TrafficSummary(summary.peer)).total
        month_summary = month.users.get(summary.peer)
        month_total = month_summary.total if month_summary else 0
        name = html.escape(resolved_name(summary, names))
        if month_summary:
            href = (
                f"reports/monthly/{month.key}/users/{user_slug(summary.peer)}.html"
            )
            name = f'<a href="{href}">{name}</a>'
        rows.append(
            "<tr>"
            f'<td class="rank">{rank}</td><td>{name}</td>'
            f"<td>{html.escape(summary.ip or '—')}</td>"
            f'<td class="num">{fmt_bytes(today_total)}</td>'
            f'<td class="num">{fmt_bytes(week_total)}</td>'
            f'<td class="num">{fmt_bytes(month_total)}</td>'
            f'<td class="num total">{fmt_bytes(summary.total)}</td>'
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="7" class="empty">Трафик пока не зафиксирован</td></tr>')
    return (
        '<div class="table-wrap"><table class="users-table">'
        "<thead><tr><th>#</th><th>Пользователь</th><th>IP</th>"
        "<th class=\"num\">Сегодня</th><th class=\"num\">Неделя</th>"
        "<th class=\"num\">Месяц</th><th class=\"num\">За всё время</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


def render_root(
    site_title: str,
    version: str,
    updated: str,
    periods: dict[str, list[PeriodReport]],
    all_users: dict[str, TrafficSummary],
    names: dict[str, str],
    now: datetime,
) -> str:
    today = current_period(periods["daily"], "daily", now)
    week = current_period(periods["weekly"], "weekly", now)
    month = current_period(periods["monthly"], "monthly", now)
    grand_total = sum(item.total for item in all_users.values())

    cards = "".join(
        (
            stat_card(
                "Сегодня",
                fmt_bytes(today.total),
                f"{len(today.users)} польз. · RX {fmt_bytes(today.rx_bytes)} · TX {fmt_bytes(today.tx_bytes)}",
                f"reports/daily/{today.key}/index.html",
            ),
            stat_card(
                "Текущая неделя",
                fmt_bytes(week.total),
                f"{len(week.users)} польз. · {week.label}",
                f"reports/weekly/{week.key}/index.html",
            ),
            stat_card(
                "Текущий месяц",
                fmt_bytes(month.total),
                f"{len(month.users)} польз. · {month.label}",
                f"reports/monthly/{month.key}/index.html",
            ),
            stat_card(
                "Вся история",
                fmt_bytes(grand_total),
                f"{len(all_users)} пользователей",
            ),
        )
    )

    archive_sections: list[str] = []
    for kind in REPORT_KINDS:
        archive_sections.append(
            '<section class="report-group">'
            f'<div class="section-title"><h2>{KIND_TITLES[kind]}</h2>'
            f'<a href="reports/{kind}/index.html">Все периоды →</a></div>'
            f'{render_period_table(periods[kind][:8], f"reports/{kind}/")}'
            "</section>"
        )

    content = (
        '<section class="intro"><h1>Сводный отчёт</h1>'
        "<p>Статические отчёты по трафику AmneziaWG в структуре, близкой к SARG.</p>"
        "</section>"
        f'<section class="stats-grid">{cards}</section>'
        '<section><div class="section-title"><h2>Пользователи</h2>'
        '<span class="muted">Календарные периоды в часовом поясе отчёта</span></div>'
        f"{render_root_user_table(all_users, today, week, month, names)}</section>"
        f"{''.join(archive_sections)}"
    )
    return render_document(site_title, "Сводный отчёт", version, updated, "", content)


def render_category(
    site_title: str,
    version: str,
    updated: str,
    kind: str,
    periods: list[PeriodReport],
) -> str:
    content = (
        f"{breadcrumbs([('Главная', '../../index.html'), (KIND_TITLES[kind], None)])}"
        f'<section class="intro"><h1>{KIND_TITLES[kind]}</h1>'
        f"<p>Сохранено периодов: {fmt_count(len(periods))}.</p></section>"
        f"{render_period_table(periods, '')}"
    )
    return render_document(
        site_title,
        KIND_TITLES[kind],
        version,
        updated,
        "../../",
        content,
    )


def render_users_table(period: PeriodReport, names: dict[str, str]) -> str:
    ordered = sorted(
        period.users.values(),
        key=lambda item: (-item.total, resolved_name(item, names).casefold(), item.ip),
    )
    rows: list[str] = []
    for rank, summary in enumerate(ordered, start=1):
        href = f"users/{user_slug(summary.peer)}.html"
        rows.append(
            "<tr>"
            f'<td class="rank">{rank}</td>'
            f'<td><a href="{href}">{html.escape(resolved_name(summary, names))}</a></td>'
            f"<td>{html.escape(summary.ip or '—')}</td>"
            f'<td class="num">{fmt_count(summary.samples)}</td>'
            f'<td class="num rx">{fmt_bytes(summary.rx_bytes)}</td>'
            f'<td class="num tx">{fmt_bytes(summary.tx_bytes)}</td>'
            f'<td class="num total">{fmt_bytes(summary.total)}</td>'
            f"<td>{share_markup(summary.total, period.total)}</td>"
            f'<td class="nowrap">{fmt_datetime(summary.last_seen)}</td>'
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="9" class="empty">В этом периоде трафика нет</td></tr>')
    return (
        '<div class="table-wrap"><table class="users-table">'
        "<thead><tr><th>#</th><th>Пользователь</th><th>IP</th>"
        "<th class=\"num\">Интервалы</th><th class=\"num\">RX</th>"
        "<th class=\"num\">TX</th><th class=\"num\">Всего</th>"
        "<th>Доля</th><th>Последняя активность</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def render_period(
    site_title: str,
    version: str,
    updated: str,
    period: PeriodReport,
    names: dict[str, str],
    newer: PeriodReport | None,
    older: PeriodReport | None,
) -> str:
    navigation: list[str] = []
    if newer:
        navigation.append(f'<a href="../{newer.key}/index.html">← {newer.label}</a>')
    navigation.append('<a href="../index.html">Все периоды</a>')
    if older:
        navigation.append(f'<a href="../{older.key}/index.html">{older.label} →</a>')

    cards = "".join(
        (
            stat_card("Всего", fmt_bytes(period.total), f"{period.samples} интервалов"),
            stat_card("Получено (RX)", fmt_bytes(period.rx_bytes)),
            stat_card("Передано (TX)", fmt_bytes(period.tx_bytes)),
            stat_card("Пользователи", fmt_count(len(period.users))),
        )
    )
    content = (
        f"{breadcrumbs([('Главная', '../../../index.html'), (KIND_TITLES[period.kind], '../index.html'), (period.label, None)])}"
        f'<section class="intro"><span class="eyebrow">{KIND_SHORT[period.kind]}</span>'
        f"<h1>{html.escape(period.label)}</h1>"
        f"<p>{fmt_date(period.start)} 00:00 — {fmt_date(period.end)} 00:00</p></section>"
        f'<nav class="period-nav">{"".join(navigation)}</nav>'
        f'<section class="stats-grid">{cards}</section>'
        '<section><div class="section-title"><h2>Рейтинг пользователей</h2>'
        '<span class="muted">Сортировка по общему трафику</span></div>'
        f"{render_users_table(period, names)}</section>"
    )
    return render_document(
        site_title,
        f"{KIND_SHORT[period.kind]} {period.label}",
        version,
        updated,
        "../../../",
        content,
    )


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
    result: list[tuple[str, TrafficSummary]] = []
    peer = rows[0].peer if rows else ""
    for hour in range(24):
        if groups[hour]:
            summary = next(iter(aggregate_rows(groups[hour]).values()))
        else:
            summary = TrafficSummary(peer=peer)
        result.append((f"{hour:02d}:00–{hour:02d}:59", summary))
    return result


def render_breakdown(
    title: str,
    groups: list[tuple[str, TrafficSummary]],
    grand_total: int,
) -> str:
    rows: list[str] = []
    for label, summary in groups:
        rows.append(
            "<tr>"
            f"<td>{html.escape(label)}</td>"
            f'<td class="num">{fmt_count(summary.samples)}</td>'
            f'<td class="num rx">{fmt_bytes(summary.rx_bytes)}</td>'
            f'<td class="num tx">{fmt_bytes(summary.tx_bytes)}</td>'
            f'<td class="num total">{fmt_bytes(summary.total)}</td>'
            f"<td>{share_markup(summary.total, grand_total)}</td>"
            "</tr>"
        )
    return (
        f'<section><div class="section-title"><h2>{html.escape(title)}</h2></div>'
        '<div class="table-wrap"><table><thead><tr><th>Период</th>'
        "<th class=\"num\">Интервалы</th><th class=\"num\">RX</th>"
        "<th class=\"num\">TX</th><th class=\"num\">Всего</th><th>Доля</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>"
    )


def render_recent_rows(rows: list[HistoryRow], limit: int) -> str:
    if limit <= 0:
        return ""
    selected = sorted(rows, key=lambda row: row.timestamp, reverse=True)[:limit]
    body: list[str] = []
    for row in selected:
        body.append(
            "<tr>"
            f'<td class="nowrap">{fmt_datetime(row.timestamp)}</td>'
            f"<td>{html.escape(clean_ip(row.ip) or '—')}</td>"
            f'<td class="num rx">{fmt_bytes(row.rx_bytes)}</td>'
            f'<td class="num tx">{fmt_bytes(row.tx_bytes)}</td>'
            f'<td class="num total">{fmt_bytes(row.total)}</td>'
            f'<td class="num">{fmt_count(row.interval)} с</td>'
            "</tr>"
        )
    return (
        '<section><div class="section-title"><h2>Последние интервалы</h2>'
        f'<span class="muted">Показано до {fmt_count(limit)}</span></div>'
        '<div class="table-wrap"><table><thead><tr><th>Дата и время</th><th>IP</th>'
        "<th class=\"num\">RX</th><th class=\"num\">TX</th>"
        "<th class=\"num\">Всего</th><th class=\"num\">Интервал</th>"
        f"</tr></thead><tbody>{''.join(body)}</tbody></table></div></section>"
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
    cards = "".join(
        (
            stat_card("Всего", fmt_bytes(summary.total), fmt_percent(summary.total, period.total)),
            stat_card("Получено (RX)", fmt_bytes(summary.rx_bytes)),
            stat_card("Передано (TX)", fmt_bytes(summary.tx_bytes)),
            stat_card("Интервалы", fmt_count(summary.samples)),
        )
    )
    daily = grouped_summaries(rows, lambda row: row.timestamp.strftime("%Y-%m-%d"))
    daily = [
        (
            datetime.strptime(label, "%Y-%m-%d").strftime("%d.%m.%Y"),
            item,
        )
        for label, item in daily
    ]
    hourly = hourly_summaries(rows)
    daily_section = (
        render_breakdown("Трафик по дням", daily, summary.total)
        if len(daily) > 1
        else ""
    )
    content = (
        f"{breadcrumbs([('Главная', '../../../../index.html'), (KIND_TITLES[period.kind], '../../index.html'), (period.label, '../index.html'), (name, None)])}"
        f'<section class="intro"><span class="eyebrow">Пользователь</span>'
        f"<h1>{html.escape(name)}</h1>"
        f"<p>IP: <strong>{html.escape(summary.ip or '—')}</strong> · "
        f"активность: {fmt_datetime(summary.first_seen)} — {fmt_datetime(summary.last_seen)}</p>"
        f'<a class="back-link" href="../index.html">← Вернуться к рейтингу</a></section>'
        f'<section class="stats-grid">{cards}</section>'
        f"{daily_section}"
        f'{render_breakdown("Трафик по часам", hourly, summary.total)}'
        f"{render_recent_rows(rows, detail_limit)}"
    )
    return render_document(
        site_title,
        f"{name} — {period.label}",
        version,
        updated,
        "../../../../",
        content,
    )


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def generate_site(
    output: Path,
    cfg: dict[str, str],
    rows: list[HistoryRow],
    names: dict[str, str],
    now: datetime,
) -> dict[str, int]:
    site_title = cfg.get("TITLE", "AWGStat")
    version = read_version(cfg)
    updated = now.strftime("%d.%m.%Y %H:%M")
    detail_limit = config_int(cfg, "DETAIL_ROWS", 200, maximum=5000)

    periods: dict[str, list[PeriodReport]] = {}
    for kind in REPORT_KINDS:
        limit = config_int(
            cfg,
            KIND_CONFIG_KEYS[kind],
            KIND_DEFAULT_LIMITS[kind],
            maximum=1000,
        )
        periods[kind] = build_periods(rows, kind, now, limit)

    all_users = aggregate_rows(rows)
    write_text(
        output / "index.html",
        render_root(site_title, version, updated, periods, all_users, names, now),
    )

    page_count = 1
    user_page_count = 0
    for kind in REPORT_KINDS:
        write_text(
            output / "reports" / kind / "index.html",
            render_category(site_title, version, updated, kind, periods[kind]),
        )
        page_count += 1
        for index, period in enumerate(periods[kind]):
            newer = periods[kind][index - 1] if index > 0 else None
            older = periods[kind][index + 1] if index + 1 < len(periods[kind]) else None
            period_root = output / "reports" / kind / period.key
            write_text(
                period_root / "index.html",
                render_period(
                    site_title,
                    version,
                    updated,
                    period,
                    names,
                    newer,
                    older,
                ),
            )
            page_count += 1
            rows_by_peer: dict[str, list[HistoryRow]] = defaultdict(list)
            for row in period.rows:
                rows_by_peer[row.peer].append(row)
            for peer, summary in period.users.items():
                write_text(
                    period_root / "users" / f"{user_slug(peer)}.html",
                    render_user(
                        site_title,
                        version,
                        updated,
                        period,
                        summary,
                        rows_by_peer[peer],
                        names,
                        detail_limit,
                    ),
                )
                page_count += 1
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


def publish_site(stage: Path, webroot: Path) -> None:
    """Replace only AWGStat-owned output while preserving unrelated web files."""
    webroot.mkdir(parents=True, exist_ok=True)
    staged_reports = stage / "reports"
    target_reports = webroot / "reports"
    old_reports = webroot / ".reports-old"

    if old_reports.exists():
        shutil.rmtree(old_reports)
    if target_reports.exists():
        target_reports.replace(old_reports)
    try:
        staged_reports.replace(target_reports)
    except Exception:
        if old_reports.exists() and not target_reports.exists():
            old_reports.replace(target_reports)
        raise
    if old_reports.exists():
        shutil.rmtree(old_reports)

    os.replace(stage / "index.html", webroot / "index.html")
    os.replace(stage / "style.css", webroot / "style.css")


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
        or not (webroot / "reports").exists()
        or names_map_changed(names_path, index_path)
    )
    if not need_rebuild:
        return 0

    tz = resolve_tz(cfg.get("REPORT_TZ", "Europe/Moscow"))
    now = datetime.now(tz)
    names = load_names(names_path)
    rows = read_history(history, tz)

    static_src = SCRIPT_DIR / "static" / "style.css"
    if not static_src.exists():
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
            (stage / "style.css").write_text("", encoding="utf-8")
        publish_site(stage, webroot)

    changed.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
