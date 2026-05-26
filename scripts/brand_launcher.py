"""Rebrand the Slumbr.exe launcher's embedded identity.

`Slumbr.exe` is a byte-copy of the venv's `pythonw.exe`, so out of the box its
PE version resource still says ``FileDescription = "Python"`` and it carries
Python's icon — which is exactly what the Windows **taskbar** shows when you pin
it. Renaming the file doesn't touch what's baked inside it.

This rewrites the version strings to Slumbr and replaces the embedded icon with
the brand mark, using pywin32's native resource APIs (`win32verstamp` +
`win32api.Update­Resource`) — no external tool, nothing downloaded. Best-effort
and idempotent; install.ps1 runs it right after copying pythonw -> Slumbr.exe.

    python scripts/brand_launcher.py <Slumbr.exe> <icon.ico> <version>
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path
from types import SimpleNamespace

_RT_ICON = 3
_RT_GROUP_ICON = 14
_LANG = 0x0409  # en-US


def stamp_version(exe: str, version: str) -> None:
    """Rewrite the PE version resource so the taskbar/Explorer read 'Slumbr'."""
    import win32verstamp

    parts = (version.split(".") + ["0", "0", "0", "0"])[:4]
    opts = SimpleNamespace(
        version=".".join(parts),
        product="Slumbr",
        company="Sleepy Productions",
        description="Slumbr",  # FileDescription — the taskbar pin label
        copyright="Sleepy Productions",
        trademarks=None,
        comments="Local, offline voice-to-text dictation for Windows.",
        internal_name="Slumbr",
        original_filename="Slumbr.exe",
        dll=False,
        debug=False,
        verbose=False,
    )
    win32verstamp.stamp(exe, opts)


def _ico_entries(ico: bytes) -> list[tuple]:
    _reserved, _typ, count = struct.unpack("<HHH", ico[:6])
    out = []
    off = 6
    for _ in range(count):
        # ICONDIRENTRY: w,h,colors,res,planes,bits,bytesInRes,imageOffset
        w, h, colors, res, planes, bits, size, offset = struct.unpack(
            "<BBBBHHII", ico[off : off + 16]
        )
        out.append((w, h, colors, res, planes, bits, size, offset))
        off += 16
    return out


def set_icon(exe: str, ico_path: str) -> None:
    """Replace the exe's main icon (RT_GROUP_ICON id 1 + its RT_ICON images)
    with the brand .ico, so the pinned/Explorer icon is the white mark."""
    import win32api

    ico = Path(ico_path).read_bytes()
    entries = _ico_entries(ico)
    h = win32api.BeginUpdateResource(exe, False)
    grp = struct.pack("<HHH", 0, 1, len(entries))  # GRPICONDIR header
    for i, (w, hh, colors, res, planes, bits, size, offset) in enumerate(entries, start=1):
        win32api.UpdateResource(h, _RT_ICON, i, ico[offset : offset + size], _LANG)
        # GRPICONDIRENTRY (14 bytes): w,h,colors,res,planes,bits,bytesInRes,id
        grp += struct.pack("<BBBBHHIH", w, hh, colors, res, planes, bits, size, i)
    win32api.UpdateResource(h, _RT_GROUP_ICON, 1, grp, _LANG)
    win32api.EndUpdateResource(h, False)


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: brand_launcher.py <Slumbr.exe> <icon.ico> <version>")
        return 2
    exe, ico, version = sys.argv[1], sys.argv[2], sys.argv[3]
    stamp_version(exe, version)
    set_icon(exe, ico)
    print(f"branded {Path(exe).name}: FileDescription=Slumbr + icon embedded (v{version})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
