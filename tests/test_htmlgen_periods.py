from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import reportgen
from htmlgen import (
    _enhance_global_page,
    _enhance_user_page,
    _period_bounds,
    _render_history_data,
)


class HtmlGenPeriodTests(unittest.TestCase):
    def test_previous_month_is_calendar_month(self) -> None:
        now = datetime(2026, 9, 10, 11, 0, tzinfo=timezone.utc)
        bounds = _period_bounds(now)
        self.assertEqual(
            bounds["previous-month"],
            (
                int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp()),
                int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp()) - 1,
            ),
        )

    def test_global_page_gets_extended_periods_and_table_once(self) -> None:
        now = datetime(2026, 9, 10, 11, 0, tzinfo=timezone.utc)
        bounds = _period_bounds(now)
        with tempfile.TemporaryDirectory() as temporary:
            page = Path(temporary) / "index.html"
            page.write_text(
                '<select id="traffic-period">'
                '<option value="60" data-graph-src="traffic-60.svg">LAST 60 MINUTES</option>'
                '<option value="custom">CUSTOM DATE / TIME</option>'
                '</select>'
                '<div class="report graph-report"><table></table></div>'
                '<div class="online-section">TOP TRAFFIC — LAST 60 MINUTES</div>',
                encoding="utf-8",
            )
            _enhance_global_page(page, bounds)
            _enhance_global_page(page, bounds)
            content = page.read_text(encoding="utf-8")

        self.assertEqual(content.count("LAST 7 DAYS"), 1)
        self.assertEqual(content.count("LAST 30 DAYS"), 1)
        self.assertEqual(content.count("PREVIOUS MONTH"), 1)
        self.assertEqual(content.count('id="traffic-period-table"'), 1)
        self.assertIn('id="traffic-period-table-body"', content)
        self.assertIn('id="traffic-period-table-total"', content)

    def test_user_page_gets_same_selector_script_and_table(self) -> None:
        now = datetime(2026, 9, 10, 11, 0, tzinfo=timezone.utc)
        bounds = _period_bounds(now)
        with tempfile.TemporaryDirectory() as temporary:
            page = Path(temporary) / "index.html"
            page.write_text(
                '<div class="online-section">TRAFFIC RATE</div>'
                '<div class="report graph-report"><table cellpadding="0" cellspacing="2">'
                '<tr><td><img src="traffic-123.svg" alt="User RX/TX rate graph"></td></tr>'
                '</table></div>'
                '<div class="online-section">RECENT TRAFFIC INTERVALS</div>',
                encoding="utf-8",
            )
            _enhance_user_page(page, now, bounds)
            content = page.read_text(encoding="utf-8")

        self.assertIn('id="traffic-period"', content)
        self.assertIn("LAST 7 DAYS", content)
        self.assertIn("LAST 30 DAYS", content)
        self.assertIn("PREVIOUS MONTH", content)
        self.assertIn("../graph-controls.js", content)
        self.assertIn("traffic-123.svg", content)
        self.assertIn('id="traffic-period-table"', content)
        self.assertIn('id="traffic-period-table-body"', content)

    def test_history_json_v2_contains_exact_bytes(self) -> None:
        now = datetime(2026, 9, 10, 11, 0, tzinfo=timezone.utc)
        rows = [
            reportgen.HistoryRow(
                timestamp=datetime(2026, 9, 10, 10, 59, 10, tzinfo=timezone.utc),
                peer="peer=",
                name="client",
                ip="10.8.1.2/32",
                rx_bytes=6000,
                tx_bytes=3000,
                interval=60,
                handshake=0,
            ),
            reportgen.HistoryRow(
                timestamp=datetime(2026, 9, 10, 10, 59, 50, tzinfo=timezone.utc),
                peer="peer=",
                name="client",
                ip="10.8.1.2/32",
                rx_bytes=1200,
                tx_bytes=600,
                interval=60,
                handshake=0,
            ),
        ]
        payload = json.loads(_render_history_data(rows, now))

        self.assertEqual(payload["version"], 2)
        self.assertEqual(len(payload["points"]), 1)
        point = payload["points"][0]
        self.assertEqual(point[3], 7200)
        self.assertEqual(point[4], 3600)
        self.assertAlmostEqual(point[1], 120.0)
        self.assertAlmostEqual(point[2], 60.0)


if __name__ == "__main__":
    unittest.main()
