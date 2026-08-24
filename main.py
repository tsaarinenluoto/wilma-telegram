#!/usr/bin/env python3

import argparse
import asyncio
import logging

from wilma_telegram.poller import run_loop, run_once


def main() -> None:
    parser = argparse.ArgumentParser(description="Forward new Wilma messages to Telegram")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Check once and exit (useful for cron)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.once:
        asyncio.run(run_once())
    else:
        asyncio.run(run_loop())


if __name__ == "__main__":
    main()
