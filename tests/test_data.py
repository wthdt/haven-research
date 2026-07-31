from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from haven.data import (  # noqa: E402
    _download_bytes,
    _number,
    fetch_fred_series,
    supplement_ndx_from_nasdaq,
)


class HavenDataTests(unittest.TestCase):
    @patch("haven.data.time.sleep")
    @patch(
        "haven.data.urllib.request.urlopen",
        side_effect=TimeoutError("network timeout"),
    )
    def test_download_timeout_has_bounded_retries(
        self,
        urlopen_mock,
        sleep_mock,
    ) -> None:
        with self.assertRaises(RuntimeError):
            _download_bytes(
                "https://example.invalid/data",
                retries=2,
                timeout_seconds=0.25,
            )

        self.assertEqual(urlopen_mock.call_count, 2)
        self.assertEqual(
            [call.kwargs["timeout"] for call in urlopen_mock.call_args_list],
            [0.25, 0.25],
        )
        sleep_mock.assert_called_once_with(1.5)

    def test_download_timeout_must_be_positive(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "timeout_seconds must be positive",
        ):
            _download_bytes(
                "https://example.invalid/data",
                timeout_seconds=0.0,
            )

    def test_nasdaq_index_placeholder_volume_is_missing(self) -> None:
        self.assertTrue(np.isnan(_number("--")))

    @patch(
        "haven.data._download_bytes",
        side_effect=RuntimeError("refresh unavailable"),
    )
    def test_fred_refresh_can_use_existing_cache_when_opted_in(
        self,
        _download_mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache_dir = Path(temporary)
            (cache_dir / "fred_test.csv").write_text(
                "DATE,TEST\n2026-07-27,123.45\n",
                encoding="utf-8",
            )
            series = fetch_fred_series(
                "TEST",
                cache_dir,
                force=True,
                request_retries=1,
                request_timeout_seconds=0.25,
                allow_cache_on_refresh_error=True,
            )

        self.assertEqual(float(series.iloc[-1]), 123.45)
        self.assertEqual(series.attrs["refresh_status"], "CACHE_FALLBACK")

    @patch("haven.data.fetch_nasdaq_history")
    def test_ndx_tail_requires_matching_overlap_and_only_appends_new_rows(
        self,
        history_mock,
    ) -> None:
        fred = pd.Series(
            [28454.81, 28128.34, 28039.21],
            index=pd.to_datetime(
                ["2026-07-23", "2026-07-24", "2026-07-27"]
            ),
            name="NASDAQ100",
        )
        history_mock.return_value = pd.DataFrame(
            {
                "open": [0.0] * 6,
                "high": [0.0] * 6,
                "low": [0.0] * 6,
                "close": [
                    28454.81,
                    28128.34,
                    28039.21,
                    27763.13,
                    27192.31,
                    28106.35,
                ],
                "volume": [np.nan] * 6,
            },
            index=pd.to_datetime(
                [
                    "2026-07-23",
                    "2026-07-24",
                    "2026-07-27",
                    "2026-07-28",
                    "2026-07-29",
                    "2026-07-30",
                ]
            ),
        )

        combined, metadata = supplement_ndx_from_nasdaq(
            fred,
            end="2026-07-31",
            cache_dir=Path("/unused"),
            force=True,
        )

        self.assertEqual(str(combined.index.max().date()), "2026-07-30")
        self.assertEqual(float(combined.iloc[-1]), 28106.35)
        self.assertEqual(metadata["appended_rows"], 3)
        self.assertEqual(metadata["maximum_overlap_difference"], 0.0)

    @patch("haven.data.fetch_nasdaq_history")
    def test_ndx_tail_mismatch_triggers_data_guard(
        self,
        history_mock,
    ) -> None:
        fred = pd.Series(
            [100.0, 101.0, 102.0],
            index=pd.to_datetime(
                ["2026-07-23", "2026-07-24", "2026-07-27"]
            ),
            name="NASDAQ100",
        )
        history_mock.return_value = pd.DataFrame(
            {
                "close": [100.0, 101.0, 103.0],
            },
            index=fred.index,
        )

        with self.assertRaisesRegex(RuntimeError, "重叠值不一致"):
            supplement_ndx_from_nasdaq(
                fred,
                end="2026-07-31",
                cache_dir=Path("/unused"),
                force=True,
                maximum_overlap_difference=0.02,
            )
