"""Small window-motion helpers.

Snappy + seamless per the ui-design motion principle: enter animations are
short (~150 ms) and ease-out (fast start, gentle stop). Kept tiny and
defensive — a motion hiccup must never block a window from showing.
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation
from PySide6.QtWidgets import QWidget

# "large" tier from the motion scale — window/dialog enter.
FADE_IN_MS = 150


def fade_window_in(window: QWidget, ms: int = FADE_IN_MS) -> QPropertyAnimation | None:
    """Animate a top-level window's opacity 0 -> 1 with an ease-out curve.

    Returns the running animation; the caller must keep a reference (e.g.
    ``self._fade_anim = fade_window_in(self)``) or Qt will GC it mid-flight
    and the window snaps to full opacity. Returns ``None`` and leaves the
    window fully opaque if anything goes wrong.
    """
    try:
        anim = QPropertyAnimation(window, b"windowOpacity", window)
        anim.setDuration(ms)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        return anim
    except Exception:
        window.setWindowOpacity(1.0)
        return None
