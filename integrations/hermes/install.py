from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


PLUGIN_NAME = "haven-market-model"
PLUGIN_FOLDER = "haven_market_model"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install the Haven v0.4 adapter into Hermes Agent."
    )
    parser.add_argument(
        "--hermes-home",
        type=Path,
        help="Hermes home; defaults to HERMES_HOME or ~/.hermes.",
    )
    parser.add_argument(
        "--engine-root",
        type=Path,
        help="Path to haven_research; defaults to this repository.",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Replace an existing adapter after making a timestamped backup.",
    )
    parser.add_argument(
        "--no-enable",
        action="store_true",
        help="Install but do not enable the Hermes plugin.",
    )
    parser.add_argument(
        "--create-cron",
        action="store_true",
        help="Create the post-close no-agent cron job.",
    )
    parser.add_argument(
        "--deliver",
        help=(
            "Hermes delivery target, e.g. telegram or "
            "telegram:-1001234567890. Required with --create-cron."
        ),
    )
    return parser


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
    )


def main() -> int:
    args = _parser().parse_args()
    integration_root = Path(__file__).resolve().parent
    default_engine = integration_root.parents[1]
    engine_root = (args.engine_root or default_engine).expanduser().resolve()
    hermes_home = (
        args.hermes_home.expanduser().resolve()
        if args.hermes_home
        else Path(
            os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))
        ).expanduser().resolve()
    )
    required = [
        engine_root / "scripts" / "run_live_shadow_v0_4.py",
        engine_root / "src" / "haven",
        engine_root / "config" / "haven_v0_4_enriched_indicators.yaml",
    ]
    if not all(path.exists() for path in required):
        print(
            f"Invalid Haven engine root: {engine_root}",
            file=sys.stderr,
        )
        return 2
    if args.create_cron and not args.deliver:
        print("--deliver is required with --create-cron", file=sys.stderr)
        return 2

    plugins_dir = hermes_home / "plugins"
    scripts_dir = hermes_home / "scripts"
    backups_dir = hermes_home / "data" / "haven-market-model" / "backups"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    scripts_dir.mkdir(parents=True, exist_ok=True)
    source_plugin = integration_root / PLUGIN_FOLDER
    target_plugin = plugins_dir / PLUGIN_FOLDER

    if target_plugin.exists():
        if not args.update:
            print(
                f"{target_plugin} already exists; rerun with --update.",
                file=sys.stderr,
            )
            return 2
        backups_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = backups_dir / f"{PLUGIN_FOLDER}-{stamp}"
        shutil.move(str(target_plugin), str(backup))
        print(f"Backed up existing adapter to {backup}")

    shutil.copytree(
        source_plugin,
        target_plugin,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    (target_plugin / "engine_path.json").write_text(
        json.dumps(
            {"engine_root": str(engine_root)},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    shutil.copy2(
        integration_root / "haven-close-update.py",
        scripts_dir / "haven-close-update.py",
    )

    missing = [
        package
        for package in ["pandas", "numpy", "matplotlib", "yaml"]
        if importlib.util.find_spec(package) is None
    ]
    if missing:
        print(
            "Missing Python packages: "
            + ", ".join(missing)
            + f"\nInstall with: {sys.executable} -m pip install -r "
            + str(engine_root / "requirements.txt"),
            file=sys.stderr,
        )
        return 3

    if not args.no_enable:
        enabled = _run(
            ["hermes", "plugins", "enable", PLUGIN_NAME]
        )
        if enabled.returncode != 0:
            print(
                "Plugin files were installed, but Hermes could not enable "
                f"them automatically:\n{enabled.stderr or enabled.stdout}",
                file=sys.stderr,
            )
            return 4

    if args.create_cron:
        listed = _run(["hermes", "cron", "list"])
        if "Haven close update" in listed.stdout:
            print("Cron job already exists; no duplicate was created.")
        else:
            created = _run(
                [
                    "hermes",
                    "cron",
                    "create",
                    "20 * * * 1-5",
                    "--no-agent",
                    "--script",
                    "haven-close-update.py",
                    "--deliver",
                    args.deliver,
                    "--name",
                    "Haven close update",
                ]
            )
            if created.returncode != 0:
                print(
                    "Adapter installed, but cron creation failed:\n"
                    + (created.stderr or created.stdout),
                    file=sys.stderr,
                )
                return 5

    print(f"Installed {PLUGIN_NAME} in {target_plugin}")
    print(
        "Cron wrapper checks America/New_York 16:15–17:30 and emits only "
        "one message for each new completed trading date."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
