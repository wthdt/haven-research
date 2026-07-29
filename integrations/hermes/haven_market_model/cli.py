from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


if __package__:
    from .tools import (
        close_update_once_with_ledger,
        handle_backtest_v04,
        handle_model_status,
        handle_refresh_close,
        handle_screen_tqqq_calls,
        write_delivery_ledger,
    )
else:
    plugin_parent = Path(__file__).resolve().parent.parent
    if str(plugin_parent) not in sys.path:
        sys.path.insert(0, str(plugin_parent))
    from haven_market_model.tools import (  # type: ignore
        close_update_once_with_ledger,
        handle_backtest_v04,
        handle_model_status,
        handle_refresh_close,
        handle_screen_tqqq_calls,
        write_delivery_ledger,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="haven-market-model")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status")
    status.add_argument("--refresh", action="store_true")
    status.add_argument("--no-shadow", action="store_true")

    sub.add_parser("refresh")

    close = sub.add_parser("close-update")
    close.add_argument("--force-window", action="store_true")

    calls = sub.add_parser("screen-tqqq-calls")
    calls.add_argument("--shares", type=int, required=True)
    calls.add_argument("--cost-basis", type=float)
    calls.add_argument("--historical-premium", type=float, default=0.0)
    calls.add_argument("--cached-chain", action="store_true")
    calls.add_argument("--refresh-model", action="store_true")
    calls.add_argument("--maximum-candidates", type=int, default=5)

    backtest = sub.add_parser("backtest")
    backtest.add_argument("--rerun", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "status":
        print(
            handle_model_status(
                {
                    "refresh": args.refresh,
                    "include_shadow": not args.no_shadow,
                }
            )
        )
    elif args.command == "refresh":
        print(handle_refresh_close({}))
    elif args.command == "close-update":
        result = close_update_once_with_ledger(force_window=args.force_window)
        if result:
            if "||" in result:
                signal_date, message = result.split("||", 1)
                print(message)
                # Write ledger AFTER printing (delivery attempted)
                write_delivery_ledger(signal_date)
            else:
                print(result)
    elif args.command == "screen-tqqq-calls":
        payload = {
            "shares": args.shares,
            "historical_premium": args.historical_premium,
            "force_option_chain": not args.cached_chain,
            "refresh_model": args.refresh_model,
            "maximum_candidates": args.maximum_candidates,
        }
        if args.cost_basis is not None:
            payload["cost_basis"] = args.cost_basis
        print(handle_screen_tqqq_calls(payload))
    elif args.command == "backtest":
        print(handle_backtest_v04({"rerun": args.rerun}))
    else:
        print(json.dumps({"error": "unknown command"}))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
