from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

from reportgen import (
    HistoryRow,
    OnlinePeer,
    OnlineSnapshot,
    aggregate_rows,
    build_all_time_report,
    build_periods,
    build_rate_series,
    generate_online_site,
    generate_site,
    online_peer_status,
    online_period_totals,
    online_snapshot_status,
    parse_name_line,
    period_dirname,
    publish_online_site,
    publish_site,
    read_history,
    read_online_state,
    render_graph_svg,
    render_online_graph_svg,
    render_user_rows,
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
        if tag not in {"a", "img", "link", "script"}:
            return
        values = dict(attrs)
        target = values.get("href") or values.get("src")
        if target:
            self.links.append(target)


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


def online_peer(
    when: datetime,
    peer: str,
    rx: int,
    tx: int,
    *,
    name: str = "",
    ip: str = "10.0.0.2/32",
    interval: int = 60,
    handshake: int | None = None,
) -> OnlinePeer:
    return OnlinePeer(
        timestamp=when,
        peer=peer,
        name=name,
        ip=ip,
        rx_bytes=rx,
        tx_bytes=tx,
        interval=interval,
        handshake=int(when.timestamp()) if handshake is None else handshake,
        rx_total=1000,
        tx_total=2000,
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
        all_time = build_all_time_report(rows, now)

        self.assertEqual([item.key for item in daily], [
            "2026-09-02",
            "2026-09-01",
            "2026-08-31",
        ])
        self.assertEqual(weekly[0].key, "2026-W36")
        self.assertEqual(weekly[0].total, 950)
        self.assertEqual([item.key for item in monthly], ["2026-09", "2026-08"])
        self.assertEqual(all_time[0].label, "31/08/2026 - 02/09/2026")
        self.assertEqual(all_time[0].total, 950)

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

    def test_online_state_parser_reads_heartbeat_and_counters(self) -> None:
        now = datetime(2026, 9, 3, 14, 0, tzinfo=TZ)
        epoch = int(now.timestamp())
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "online.csv"
            state.write_text(
                f"#AWGSTAT-ONLINE:1;{epoch}\n"
                "sample_timestamp;peer;name;ip;rx_bytes;tx_bytes;interval;"
                "handshake;rx_total;tx_total\n"
                f"{epoch};peer-a;Alice <Admin>;10.0.0.2/32;120;bad;60;"
                f"{epoch - 30};1000;-4\n",
                encoding="utf-8",
            )
            snapshot = read_online_state(state, TZ)

        self.assertEqual(snapshot.timestamp, now)
        self.assertEqual(len(snapshot.peers), 1)
        self.assertEqual(snapshot.peers[0].rx_bytes, 120)
        self.assertEqual(snapshot.peers[0].tx_bytes, 0)
        self.assertEqual(snapshot.peers[0].tx_total, 0)

    def test_online_status_distinguishes_fresh_active_stale_and_empty(self) -> None:
        now = datetime(2026, 9, 3, 14, 0, tzinfo=TZ)
        traffic = online_peer(now - timedelta(seconds=20), "peer-a", 120, 60)
        active = online_peer(now - timedelta(seconds=20), "peer-b", 0, 0)
        fresh = OnlineSnapshot(now - timedelta(seconds=20), [traffic, active])

        self.assertEqual(online_snapshot_status(fresh, now, 3), "FRESH")
        self.assertEqual(
            online_peer_status(traffic, fresh, now, 3, 3),
            "TRAFFIC",
        )
        self.assertEqual(
            online_peer_status(active, fresh, now, 3, 3),
            "ACTIVE",
        )
        stale = OnlineSnapshot(now - timedelta(minutes=4), [traffic])
        self.assertEqual(online_snapshot_status(stale, now, 3), "STALE")
        self.assertEqual(
            online_peer_status(traffic, stale, now, 3, 3),
            "STALE",
        )
        self.assertEqual(
            online_snapshot_status(OnlineSnapshot(), now, 3),
            "NO SNAPSHOT",
        )

    def test_online_period_totals_use_calendar_and_rolling_windows(self) -> None:
        now = datetime(2026, 9, 3, 15, 0, tzinfo=TZ)
        rows = [
            row(datetime(2026, 9, 3, 1, 0, tzinfo=TZ), "peer-a", 100, 50),
            row(datetime(2026, 9, 1, 1, 0, tzinfo=TZ), "peer-a", 1000, 500),
            row(datetime(2026, 8, 10, 1, 0, tzinfo=TZ), "peer-a", 200, 100),
            row(datetime(2026, 8, 1, 1, 0, tzinfo=TZ), "peer-a", 400, 200),
            row(datetime(2026, 8, 20, 1, 0, tzinfo=TZ), "peer-b", 50, 50),
            row(datetime(2026, 9, 3, 16, 0, tzinfo=TZ), "peer-a", 999, 999),
        ]

        totals = online_period_totals(rows, now)

        self.assertEqual(totals["peer-a"], (150, 1650, 1950))
        self.assertEqual(totals["peer-b"], (0, 0, 100))

    def test_rate_series_is_chronological_and_zero_filled(self) -> None:
        now = datetime(2026, 9, 3, 10, 3, 30, tzinfo=TZ)
        rows = [
            row(datetime(2026, 9, 3, 10, 3, tzinfo=TZ), "peer-a", 60, 120),
            row(datetime(2026, 9, 3, 10, 1, tzinfo=TZ), "peer-a", 120, 60),
        ]

        series = build_rate_series(rows, now, 3)

        self.assertEqual(
            [item[0].strftime("%H:%M") for item in series],
            ["10:01", "10:02", "10:03"],
        )
        self.assertEqual(series[0][1:], (2.0, 1.0))
        self.assertEqual(series[1][1:], (0.0, 0.0))
        self.assertEqual(series[2][1:], (1.0, 2.0))

    def test_detail_limit_zero_hides_raw_intervals(self) -> None:
        rows = [
            row(datetime(2026, 9, 2, 10, 0, tzinfo=TZ), "peer-a", 100, 50),
        ]
        summary = aggregate_rows(rows)["peer-a"]

        self.assertIn(
            "detail is disabled",
            render_user_rows(summary, rows, 0),
        )

    def test_graph_orders_days_chronologically_across_months(self) -> None:
        rows = [
            row(datetime(2026, 9, 1, 10, 0, tzinfo=TZ), "peer-a", 100, 50),
            row(datetime(2026, 8, 31, 10, 0, tzinfo=TZ), "peer-a", 100, 50),
        ]
        graph = render_graph_svg(rows)

        self.assertLess(graph.index("31/08"), graph.index("01/09"))

    def test_online_graph_orders_minutes_and_escapes_title(self) -> None:
        now = datetime(2026, 9, 3, 10, 3, 30, tzinfo=TZ)
        rows = [
            row(datetime(2026, 9, 3, 10, 3, tzinfo=TZ), "peer-a", 60, 120),
            row(datetime(2026, 9, 3, 10, 1, tzinfo=TZ), "peer-a", 120, 60),
        ]

        graph = render_online_graph_svg(
            rows,
            now,
            3,
            "Alice <Admin> & phone",
        )

        self.assertLess(graph.index("10:01"), graph.index("10:03"))
        self.assertIn("Alice &lt;Admin&gt; &amp; phone", graph)
        self.assertNotIn("Alice <Admin>", graph)

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
            "VERSION": "2.1.1-test",
            "DAILY_REPORTS": "31",
            "WEEKLY_REPORTS": "12",
            "MONTHLY_REPORTS": "12",
            "DETAIL_ROWS": "50",
            "ONLINE_WINDOW_MINUTES": "60",
            "ONLINE_ACTIVE_MINUTES": "3",
            "ONLINE_STALE_MINUTES": "3",
            "ONLINE_REFRESH_SECONDS": "60",
        }
        names = {"peer-a": "Alice <Admin>", "peer-b": "Bob"}
        snapshot = OnlineSnapshot(
            timestamp=now - timedelta(seconds=20),
            peers=[
                online_peer(
                    now - timedelta(seconds=20),
                    "peer-a",
                    600,
                    300,
                    name="Alice <Admin>",
                ),
                online_peer(
                    now - timedelta(seconds=20),
                    "peer-b",
                    0,
                    0,
                    name="Bob",
                    ip="10.0.0.3/32",
                ),
            ],
        )

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            result = generate_site(output, cfg, rows, names, now, snapshot)
            slug = user_slug("peer-a")
            report_dir = period_dirname(
                build_periods(rows, "daily", now, 31)[0]
            )
            index = (output / "index.html").read_text(encoding="utf-8")
            category = (
                output / "daily" / "index.html"
            ).read_text(encoding="utf-8")
            period = (
                output / "daily" / report_dir / "index.html"
            ).read_text(encoding="utf-8")
            user_root = output / "daily" / report_dir / slug
            user = (user_root / f"{slug}.html").read_text(encoding="utf-8")
            datetime_report = (
                user_root / f"d{slug}.html"
            ).read_text(encoding="utf-8")
            online = (
                output / "online" / "index.html"
            ).read_text(encoding="utf-8")
            online_user = (
                output / "online" / slug / "index.html"
            ).read_text(encoding="utf-8")

            self.assertTrue((output / "daily" / "index.html").exists())
            self.assertIn("daily/index.html", index)
            self.assertIn("total/index.html", index)
            self.assertIn("ALL TIME REPORT", index)
            self.assertGreater(
                index.index("ONLINE REPORT"),
                index.index("ALL TIME REPORT"),
            )
            self.assertIn("online/index.html", index)
            self.assertTrue((output / "total" / "index.html").exists())
            self.assertEqual(
                len(list((output / "online").glob("traffic-*.svg"))),
                1,
            )
            self.assertIn("02Sep2026-02Sep2026", category)
            self.assertIn("Alice &lt;Admin&gt;", period)
            self.assertNotIn("Alice <Admin>", period)
            self.assertIn("TRAFFIC INTERVAL", user)
            self.assertIn("Date/time report", datetime_report)
            self.assertIn("10H", datetime_report)
            self.assertTrue((user_root / "graph.html").exists())
            self.assertTrue((user_root / "graph.svg").exists())
            self.assertTrue(
                (output / "daily" / report_dir / "sarg-date").exists()
            )
            self.assertTrue(
                (output / "daily" / report_dir / "sarg-users").exists()
            )
            self.assertIn('http-equiv="refresh" content="60"', online)
            self.assertIn("CURRENT PEERS", online)
            self.assertIn("TRAFFIC RATE", online)
            self.assertIn("Alice &lt;Admin&gt;", online)
            self.assertNotIn("Alice <Admin>", online)
            self.assertLess(online.index("TODAY"), online.index("WG COUNTERS"))
            self.assertLess(
                online.index("THIS MONTH"),
                online.index("WG COUNTERS"),
            )
            self.assertLess(
                online.index("LAST 30 DAYS"),
                online.index("WG COUNTERS"),
            )
            self.assertIn("ONLINE USER REPORT", online_user)
            self.assertEqual(
                len(list((output / "online" / slug).glob("traffic-*.svg"))),
                1,
            )
            self.assertGreater(result["pages"], 4)
            self.assertGreater(result["user_pages"], 0)
            self.assertEqual(result["online_user_pages"], 2)

    def test_online_site_handles_stale_and_empty_snapshots(self) -> None:
        now = datetime(2026, 9, 3, 14, 0, tzinfo=TZ)
        cfg = {
            "TITLE": "Online states",
            "VERSION": "test",
            "DAILY_REPORTS": "1",
            "WEEKLY_REPORTS": "1",
            "MONTHLY_REPORTS": "1",
            "DETAIL_ROWS": "10",
            "ONLINE_WINDOW_MINUTES": "60",
            "ONLINE_ACTIVE_MINUTES": "3",
            "ONLINE_STALE_MINUTES": "3",
            "ONLINE_REFRESH_SECONDS": "60",
        }
        stale = OnlineSnapshot(
            timestamp=now - timedelta(minutes=5),
            peers=[
                online_peer(
                    now - timedelta(minutes=5),
                    "peer-a",
                    600,
                    300,
                    name="Alice",
                ),
            ],
        )

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            generate_online_site(output, cfg, [], {}, now, stale)
            stale_html = (
                output / "online" / "index.html"
            ).read_text(encoding="utf-8")
            self.assertIn("STALE", stale_html)
            self.assertIn(
                '<td class="data">—</td><td class="data">—</td>',
                stale_html,
            )

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            generate_online_site(
                output,
                cfg,
                [],
                {},
                now,
                OnlineSnapshot(),
            )
            empty_html = (
                output / "online" / "index.html"
            ).read_text(encoding="utf-8")
            self.assertIn("NO SNAPSHOT", empty_html)
            self.assertIn(
                "No peers in the current WireGuard snapshot",
                empty_html,
            )
            self.assertIn('colspan="14"', empty_html)
            self.assertIn("No traffic in the recent window", empty_html)

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
            for directory in (
                "daily",
                "weekly",
                "monthly",
                "total",
                "online",
                "images",
            ):
                (stage / directory).mkdir(parents=True)
                (stage / directory / "new.html").write_text(
                    "new",
                    encoding="utf-8",
                )
            webroot.mkdir()
            (stage / "index.html").write_text("new index", encoding="utf-8")
            (stage / "style.css").write_text("new style", encoding="utf-8")
            (webroot / "keep.txt").write_text("keep", encoding="utf-8")
            (webroot / "reports").mkdir()
            (webroot / "reports" / "old.html").write_text("old", encoding="utf-8")

            publish_site(stage, webroot)

            self.assertEqual((webroot / "keep.txt").read_text(), "keep")
            self.assertTrue((webroot / "daily" / "new.html").exists())
            self.assertTrue((webroot / "total" / "new.html").exists())
            self.assertTrue((webroot / "online" / "new.html").exists())
            self.assertTrue((webroot / "images" / "new.html").exists())
            self.assertFalse((webroot / "reports").exists())

    def test_online_publish_replaces_only_live_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stage = root / "stage"
            webroot = root / "www"
            (stage / "online").mkdir(parents=True)
            (stage / "online" / "index.html").write_text(
                "new online",
                encoding="utf-8",
            )
            (stage / "index.html").write_text("new root", encoding="utf-8")
            (webroot / "online").mkdir(parents=True)
            (webroot / "daily").mkdir()
            (webroot / "online" / "index.html").write_text(
                "old online",
                encoding="utf-8",
            )
            (webroot / "daily" / "index.html").write_text(
                "keep daily",
                encoding="utf-8",
            )
            (webroot / "index.html").write_text("old root", encoding="utf-8")
            (webroot / "keep.txt").write_text("keep", encoding="utf-8")

            publish_online_site(stage, webroot)

            self.assertEqual(
                (webroot / "online" / "index.html").read_text(),
                "new online",
            )
            self.assertEqual((webroot / "index.html").read_text(), "new root")
            self.assertEqual(
                (webroot / "daily" / "index.html").read_text(),
                "keep daily",
            )
            self.assertEqual((webroot / "keep.txt").read_text(), "keep")

    def test_cron_serializes_collection_before_online_publication(self) -> None:
        root = Path(__file__).resolve().parents[1]
        cron = (root / "cron" / "awgstat").read_text(encoding="utf-8")
        cycle = (root / "awgstat-cycle.sh").read_text(encoding="utf-8")
        install = (root / "install.sh").read_text(encoding="utf-8")

        self.assertIn(
            "* * * * * root /opt/wgstats/awgstat-cycle.sh",
            cron,
        )
        self.assertNotIn(
            "root /opt/wgstats/wgstats.sh >/dev/null",
            cron,
        )
        regular_cycle = cycle.split('"${SCRIPT_DIR}/wgstats.sh"', 1)[1]
        self.assertIn("htmlgen.py", regular_cycle)
        self.assertIn("--online", cycle)
        self.assertIn("--scheduled", cycle)
        self.assertIn('TZ="${REPORT_TZ:-Europe/Moscow}"', cycle)
        self.assertIn('"00:01"', cycle)
        self.assertIn("htmlgen.py\" --force", cycle)
        self.assertIn('install -m 0755 "${SRC}/awgstat-cycle.sh"', install)
        self.assertIn('ensure_config_key "ONLINE_STATE"', install)
        self.assertNotIn('htmlgen.py" --force || true', install)


if __name__ == "__main__":
    unittest.main()
