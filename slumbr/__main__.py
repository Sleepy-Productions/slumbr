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


def _install_crash_excepthook() -> None:
    """On an uncaught exception, auto-write a traceback crash log before the
    default handler runs — so a hard crash leaves a breadcrumb even when the
    next-launch recovery flow can't reconstruct what happened. KeyboardInterrupt
    is left alone (not a crash)."""
    import traceback

    prev = sys.excepthook

    def hook(exc_type, exc, tb):
        if not issubclass(exc_type, KeyboardInterrupt):
            try:
                from .session_logs import write_crash_traceback

                write_crash_traceback("".join(traceback.format_exception(exc_type, exc, tb)))
            except Exception:  # noqa: BLE001
                pass
        prev(exc_type, exc, tb)

    sys.excepthook = hook


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

    # Tag the process as "Slumbr" (AUMID) before any window exists, so the
    # taskbar / pinning / Start treat it as Slumbr, not the host pythonw.exe.
    from .winident import set_process_app_id

    set_process_app_id()
    _install_crash_excepthook()

    # Single-instance: if Slumbr is already running, surface it and exit instead
    # of starting a second copy (which would double the Caps Lock hook + leave a
    # stray taskbar button + make every relaunch look like a "crash").
    from .session_logs import another_instance_running, focus_existing

    if another_instance_running():
        log.info("Slumbr is already running — focusing it instead of starting a second copy")
        focus_existing()
        return 0

    log.info("Slumbr %s — tap Caps Lock to dictate", __version__)
    return SlumbrApp().run()


if __name__ == "__main__":
    sys.exit(main())
