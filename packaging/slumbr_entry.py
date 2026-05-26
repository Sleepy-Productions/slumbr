"""PyInstaller entry point — the frozen-app equivalent of `python -m slumbr`.

PyInstaller runs the entry *script* as the top-level ``__main__`` with no
parent package, so the relative imports inside ``slumbr/__main__.py``
(``from . import __version__``) raise "attempted relative import with no
known parent package". Importing ``slumbr.__main__`` as a real submodule
first gives it its parent package, so those imports resolve.
"""

import sys

from slumbr.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
