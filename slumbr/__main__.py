"""Slumbr entrypoint.

Run with `python -m slumbr`. Boots the Qt app + tray + Caps Lock hook.
Pass `--debug` to flip the root logger to DEBUG.
"""

from __future__ import annotations

import argparse
import logging
import sys

from . import __version__
from .app import SlumbrApp

log = logging.getLogger("slumbr")


def main() -> int:
    parser = argparse.ArgumentParser(prog="slumbr", description="Slumbr — local voice dictation")
    parser.add_argument("--debug", action="store_true", help="enable DEBUG logging")
    parser.add_argument(
        "--version", action="version", version=f"Slumbr {__version__}"
    )
    args = parser.parse_args()

    if args.debug:
        # Only Slumbr's modules — third-party libs stay at WARNING (see
        # _configure_logging in __init__.py) so the stream is readable.
        logging.getLogger("slumbr").setLevel(logging.DEBUG)

    log.info("Slumbr %s — tap Caps Lock to dictate", __version__)
    return SlumbrApp().run()


if __name__ == "__main__":
    sys.exit(main())
