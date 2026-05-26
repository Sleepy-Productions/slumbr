"""Set Slumbr's version everywhere at once — one command, no drift.

    .\\.venv\\Scripts\\python.exe scripts\\set_version.py 0.3.1

Updates every place the version is hard-coded:
  - pyproject.toml        (package metadata / pip)
  - slumbr/__init__.py    (__version__ — the runtime source of truth)
  - packaging/slumbr.iss  (installer AppVersion)
  - README.md             (the shields.io version badge + its alt text)

The About screen and `slumbr --version` read ``slumbr.__version__`` at runtime,
so the in-app UI follows automatically — nothing to touch there. The CHANGELOG
is deliberately left for you to write by hand (it needs real release notes).

Scheme-agnostic: pass whatever string you use — SemVer (0.3.1), CalVer
(2026.05), or a plain counter — it just syncs that exact string into all files.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (file, regex with the version as group 1, replacement template using {v})
_EDITS = [
    (ROOT / "pyproject.toml", r'(?m)^version = "([^"]+)"', 'version = "{v}"'),
    (ROOT / "slumbr" / "__init__.py", r'(?m)^__version__ = "([^"]+)"', '__version__ = "{v}"'),
    (ROOT / "packaging" / "slumbr.iss", r'(?m)^#define AppVersion "([^"]+)"', '#define AppVersion "{v}"'),
    (ROOT / "README.md", r'version-([0-9][^-\s"]*)-2b2d31', "version-{v}-2b2d31"),
    (ROOT / "README.md", r'alt="version ([^"]+)"', 'alt="version {v}"'),
]


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python scripts/set_version.py <version>   e.g. 0.3.1")
        return 2
    v = sys.argv[1].strip().lstrip("v")
    if not v:
        print("error: empty version")
        return 2

    changed: list[str] = []
    for path, pattern, repl in _EDITS:
        text = path.read_text(encoding="utf-8")
        new, n = re.subn(pattern, repl.format(v=v), text)
        if n == 0:
            print(f"  WARN: no version match in {path.name}")
            continue
        if new != text:
            path.write_text(new, encoding="utf-8")
            changed.append(f"{path.name}×{n}")

    print(f"version -> {v}  ({', '.join(changed) if changed else 'no changes'})")
    print(f"next: add a CHANGELOG entry, commit, then `git tag v{v}` when releasing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
