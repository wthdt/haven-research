from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from haven.data import _download_bytes  # noqa: E402


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
