from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    configured = os.environ.get("HERMES_HOME", "").strip()
    hermes_home = (
        Path(configured).expanduser().resolve()
        if configured
        else (Path.home() / ".hermes").resolve()
    )
    cli = (
        hermes_home
        / "plugins"
        / "haven_market_model"
        / "cli.py"
    )
    if not cli.exists():
        print(f"Haven cron adapter not found: {cli}", file=sys.stderr)
        return 2
    completed = subprocess.run(
        [sys.executable, str(cli), "close-update"],
        text=True,
        capture_output=True,
        timeout=1800,
        check=False,
    )
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.returncode != 0:
        if completed.stderr:
            print(completed.stderr, file=sys.stderr, end="")
        return completed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
