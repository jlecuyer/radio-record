"""CLI entry point for radio-record.

Usage::

    # With uv (recommended)
    uv run radio-record
    uv run radio-record -o ~/Music/radio-hyrule -v

    # Or as a module
    uv run python -m radio_record
"""

import argparse
import logging
import os
import signal
import sys
from pathlib import Path

from dotenv import load_dotenv

from .recorder import Recorder

load_dotenv()


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(
        prog="radio-record",
        description=(
            "Record and organise individual tracks from an Icecast internet "
            "radio stream.  Tracks are split at metadata boundaries, "
            "de-duplicated, and partial recordings are automatically "
            "replaced when a complete version is captured."
        ),
    )

    default_url = os.environ.get("RADIO_RECORD_URL")

    parser.add_argument(
        "url",
        nargs="?",
        default=default_url,
        help="Stream URL (or set RADIO_RECORD_URL env var)",
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=Path("./radio_record"),
        help="Output directory for recorded tracks (default: ./radio_record)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug-level logging",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Connection / read timeout in seconds (default: 30)",
    )
    parser.add_argument(
        "--retry-delay",
        type=int,
        default=10,
        help="Seconds to wait before reconnecting after an error (default: 10)",
    )
    args = parser.parse_args(argv)

    if not args.url:
        parser.error("stream URL is required (pass as argument or set RADIO_RECORD_URL in .env)")

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    args.output.mkdir(parents=True, exist_ok=True)

    recorder = Recorder(
        url=args.url,
        output_dir=args.output,
        timeout=args.timeout,
        retry_delay=args.retry_delay,
    )

    # Graceful shutdown: first Ctrl+C saves the current track and exits.
    # Second Ctrl+C kills immediately (default SIGINT handler restored).
    def _handle_signal(sig, frame):
        logging.info("Shutting down (Ctrl+C again to force)...")
        recorder.stop()
        signal.signal(signal.SIGINT, signal.SIG_DFL)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    recorder.run()


if __name__ == "__main__":
    main()
