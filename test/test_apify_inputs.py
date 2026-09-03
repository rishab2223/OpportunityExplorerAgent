from __future__ import annotations

import unittest

from src.scrape.apify_jobs import (
    LINKEDIN_DATE_POSTED,
    LINKEDIN_LOOKBACK_SECONDS,
    _with_lookback,
)

URL = (
    "https://www.linkedin.com/jobs/search/?keywords=software%20engineer"
    "&geoId=102713980&sortBy=DD"
)


class WithLookbackTests(unittest.TestCase):
    def test_appends_f_tpr_when_missing(self) -> None:
        self.assertEqual(_with_lookback(URL, "3d"), URL + "&f_TPR=r259200")
        self.assertEqual(_with_lookback(URL, "24h"), URL + "&f_TPR=r86400")
        self.assertEqual(_with_lookback(URL, "7d"), URL + "&f_TPR=r604800")

    def test_replaces_existing_f_tpr(self) -> None:
        stale = URL + "&f_TPR=r86400"
        self.assertEqual(_with_lookback(stale, "3d"), URL + "&f_TPR=r259200")

    def test_replaces_f_tpr_in_the_middle(self) -> None:
        url = "https://www.linkedin.com/jobs/search/?f_TPR=r86400&geoId=1&sortBy=DD"
        self.assertEqual(
            _with_lookback(url, "7d"),
            "https://www.linkedin.com/jobs/search/?f_TPR=r604800&geoId=1&sortBy=DD",
        )

    def test_url_without_query_string(self) -> None:
        self.assertEqual(
            _with_lookback("https://www.linkedin.com/jobs/search", "3d"),
            "https://www.linkedin.com/jobs/search?f_TPR=r259200",
        )

    def test_unknown_window_defaults_to_three_days(self) -> None:
        self.assertEqual(_with_lookback(URL, "??"), URL + "&f_TPR=r259200")


class MappingTests(unittest.TestCase):
    def test_every_posted_within_value_is_covered(self) -> None:
        for window in ("24h", "3d", "7d"):
            self.assertIn(window, LINKEDIN_LOOKBACK_SECONDS)
            self.assertIn(window, LINKEDIN_DATE_POSTED)
