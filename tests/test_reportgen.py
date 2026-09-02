from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

from reportgen import (
    HistoryRow,
    build_periods,
    generate_site,
    parse_name_line,
    publish_site,
    read_history,
    user_slug,
)


TZ = timezone(timedelta(hours=3))


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag not in {"a", "link"}:
            return
        values = dict(attrs)
        href = values.get("href")
        if href:
            self.links.append(href)


def row(
    when: datetime,
    peer: str,
    rx: int,
    tx: int,
    *,
    name: str = "",
    ip: str = "10.0.0.2/32",
) -> HistoryRow:
    return HistoryRow(
        timestamp=when,
        peer=peer,
        name=name,
        ip=ip,
        rx_bytes=rx,
        tx_bytes=tx,
        interval=60,
        handshake=0,
    )


class ReportGeneratorTests(unittest.TestCase):
    def test_name_map_keeps_base64_padding(self) -> None:
        self.assertEqual(
            parse_name_line("abc123==:phone"),
            ("abc123==", "phone"),
        )
        self.assertEqual(
            parse_name_line("abc123==phone"),
            ("abc123=", "phone"),
        )

    def test_periods_use_calendar_boundaries(self) -> None:
        now = datetime(2026, 9, 2, 17, 0, tzinfo=TZ)
        rows = [
            row(datetime(2026, 9, 2, 10, 0, tzinfo=TZ), "peer-a", 100, 50),
            row(datetime(2026, 9, 1, 10, 0, tzinfo=TZ), "peer-a", 200, 100),
            row(datetime(2026, 8, 31, 10, 0, tzinfo=TZ), "peer-b", 300, 200),
        ]

        daily = build_periods(rows, "daily", now, 31)
        weekly = build_periods(rows, "weekly", now, 12)
        monthly = build_periods(rows, "monthly", now, 12)

        self.assertEqual([item.key for item in daily], [
            "2026-09-02",
            "2026-09-01",
            "2026-08-31",
        ])
        self.assertEqual(weekly[0].key, "2026-W36")
        self.assertEqual(weekly[0].total, 950)
        self.assertEqual([item.key for item in monthly], ["2026-09", "2026-08"])

    def test_history_parser_sanitizes_invalid_counters(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            history = Path(temporary) / "history.csv"
            history.write_text(
                "#WGSTAT:1\n"
                "timestamp;date;time;peer;name;ip;rx_bytes;tx_bytes;interval;handshake\n"
                "1788361200;2026-09-02;17:00:00;peer-a;Alice;10.0.0.2/32;-1;bad;60;0\n",
                encoding="utf-8",
            )
            rows = read_history(history, TZ)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].rx_bytes, 0)
        self.assertEqual(rows[0].tx_bytes, 0)

    def test_site_contains_archives_and_user_details(self) -> None:
        now = datetime(2026, 9, 2, 17, 0, tzinfo=TZ)
        rows = [
            row(datetime(2026, 9, 2, 10, 5, tzinfo=TZ), "peer-a", 100, 50),
            row(datetime(2026, 9, 2, 11, 5, tzinfo=TZ), "peer-a", 200, 100),
            row(
                datetime(2026, 9, 1, 8, 0, tzinfo=TZ),
                "peer-b",
                300,
                200,
                ip="10.0.0.3/32",
            ),
        ]
        cfg = {
            "TITLE": "Test AWGStat",
            "VERSION": "1.2.0-test",
            "DAILY_REPORTS": "31",
            "WEEKLY_REPORTS": "12",
            "MONTHLY_REPORTS": "12",
            "DETAIL_ROWS": "50",
        }
        names = {"peer-a": "Alice <Admin>", "peer-b": "Bob"}

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            result = generate_site(output, cfg, rows, names, now)
            slug = user_slug("peer-a")
            index = (output / "index.html").read_text(encoding="utf-8")
            period = (
                output / "reports" / "daily" / "2026-09-02" / "index.html"
            ).read_text(encoding="utf-8")
            user = (
                output
                / "reports"
                / "daily"
                / "2026-09-02"
                / "users"
                / f"{slug}.html"
            ).read_text(encoding="utf-8")

            self.assertTrue((output / "reports" / "daily" / "index.html").exists())
            self.assertIn("reports/daily/2026-09-02/index.html", index)
            self.assertIn("Alice &lt;Admin&gt;", period)
            self.assertNotIn("Alice <Admin>", period)
            self.assertIn("Трафик по часам", user)
            self.assertIn("10:00–10:59", user)
            self.assertGreater(result["pages"], 4)
            self.assertGreater(result["user_pages"], 0)

    def test_all_generated_local_links_resolve(self) -> None:
        now = datetime(2026, 9, 2, 17, 0, tzinfo=TZ)
        rows = [
            row(datetime(2026, 9, 2, 10, 0, tzinfo=TZ), "peer-a", 100, 50),
            row(datetime(2026, 8, 31, 10, 0, tzinfo=TZ), "peer-b", 200, 100),
        ]
        cfg = {
            "TITLE": "Link test",
            "VERSION": "test",
            "DAILY_REPORTS": "31",
            "WEEKLY_REPORTS": "12",
            "MONTHLY_REPORTS": "12",
            "DETAIL_ROWS": "10",
        }

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            generate_site(output, cfg, rows, {}, now)
            (output / "style.css").write_text("", encoding="utf-8")

            for page in output.rglob("*.html"):
                parser = LinkParser()
                parser.feed(page.read_text(encoding="utf-8"))
                for href in parser.links:
                    if "://" in href or href.startswith(("#", "mailto:")):
                        continue
                    target = (page.parent / href).resolve()
                    self.assertTrue(
                        target.exists(),
                        f"{page.relative_to(output)} links to missing {href}",
                    )

    def test_publish_preserves_unrelated_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stage = root / "stage"
            webroot = root / "www"
            (stage / "reports").mkdir(parents=True)
            webroot.mkdir()
            (stage / "index.html").write_text("new index", encoding="utf-8")
            (stage / "style.css").write_text("new style", encoding="utf-8")
            (stage / "reports" / "new.html").write_text("new", encoding="utf-8")
            (webroot / "keep.txt").write_text("keep", encoding="utf-8")
            (webroot / "reports").mkdir()
            (webroot / "reports" / "old.html").write_text("old", encoding="utf-8")

            publish_site(stage, webroot)

            self.assertEqual((webroot / "keep.txt").read_text(), "keep")
            self.assertTrue((webroot / "reports" / "new.html").exists())
            self.assertFalse((webroot / "reports" / "old.html").exists())


if __name__ == "__main__":
    unittest.main()
