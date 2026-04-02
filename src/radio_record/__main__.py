"""CLI entry point for radio-record.

Usage::

    # With uv (recommended)
    uv run radio-record
    uv run radio-record -o ~/Music/output -v

    # Or as a module
    uv run python -m radio_record
"""

import argparse
import logging
import os
import re
import signal
from pathlib import Path
from urllib.parse import urlparse

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

    default_url = os.environ.get("RADIO_RECORD_URL", "http://192.168.1.50:8000/metal")

    parser.add_argument(
        "url",
        nargs="?",
        default=default_url,
        help="Stream URL (default: http://192.168.1.50:8000/metal, or set RADIO_RECORD_URL env var)",
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=None,
        help="Output directory for recorded tracks (default: derived from stream URL)",
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

    if args.output is None:
        parsed = urlparse(args.url)
        # Use hostname without port; strip common TLD-like suffixes aren't
        # needed — just use the hostname and path to build a readable name.
        name = parsed.hostname or "radio"
        # Strip common prefixes/suffixes that add no value
        name = re.sub(r"^(www|stream|listen|radio)\.", "", name)
        name = re.sub(r"\.(com|org|net|io|fm|cc|tv|gg)$", "", name)
        # Append path segments (skip empty, generic, and redundant ones)
        for segment in parsed.path.strip("/").split("/"):
            if segment and segment.lower() not in ("listen", "stream", "radio") \
                    and segment.lower() not in name.lower():
                name += f"-{segment}"
        # Sanitize to filesystem-safe characters
        name = re.sub(r"[^\w\-.]", "-", name)
        name = re.sub(r"-+", "-", name).strip("-")
        args.output = Path(f"./{name or 'radio-record'}")

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
