from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from htmlgen import _enhance_global_page, _enhance_user_page, _period_bounds


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

    def test_global_page_gets_extended_periods_once(self) -> None:
        now = datetime(2026, 9, 10, 11, 0, tzinfo=timezone.utc)
        bounds = _period_bounds(now)
        with tempfile.TemporaryDirectory() as temporary:
            page = Path(temporary) / "index.html"
            page.write_text(
                '<select id="traffic-period">'
                '<option value="60" data-graph-src="traffic-60.svg">LAST 60 MINUTES</option>'
                '<option value="custom">CUSTOM DATE / TIME</option>'
                '</select>',
                encoding="utf-8",
            )
            _enhance_global_page(page, bounds)
            _enhance_global_page(page, bounds)
            content = page.read_text(encoding="utf-8")

        self.assertEqual(content.count("LAST 7 DAYS"), 1)
        self.assertEqual(content.count("LAST 30 DAYS"), 1)
        self.assertEqual(content.count("PREVIOUS MONTH"), 1)

    def test_user_page_gets_same_selector_and_script(self) -> None:
        now = datetime(2026, 9, 10, 11, 0, tzinfo=timezone.utc)
        bounds = _period_bounds(now)
        with tempfile.TemporaryDirectory() as temporary:
            page = Path(temporary) / "index.html"
            page.write_text(
                '<div class="online-section">TRAFFIC RATE</div>'
                '<div class="report graph-report"><table cellpadding="0" cellspacing="2">'
                '<tr><td><img src="traffic-123.svg" alt="User RX/TX rate graph"></td></tr>'
                '</table></div>',
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


if __name__ == "__main__":
    unittest.main()
