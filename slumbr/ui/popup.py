"""Recording popup with live audio visualizer.

A compact, frameless, always-on-top status bar shown while RECORDING /
TRANSCRIBING. Designed to feel alive — the center is an animated bar
visualizer driven directly by the mic input, easing toward each new
chunk's RMS at ~60 fps so it doesn't look chunky.

The popup positions itself near the user's mouse cursor on every show,
on whichever screen the cursor is on. It NEVER takes focus, so paste
goes to whatever window the user was already in.

Audio flow
----------
The PortAudio capture thread calls `Visualizer.push_samples(np_array)`
via a `Qt.QueuedConnection` signal in the app layer — never call it
directly from the audio thread, that path crosses thread boundaries
and would race with paintEvent.
"""

from __future__ import annotations

import time

import numpy as np
from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QFontMetricsF,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..theme import (
    BG_PANEL,
    BORDER,
    COLOR_ERROR,
    COLOR_IDLE,
    COLOR_RECORDING,
    COLOR_SENT,
    COLOR_TRANSCRIBING,
    TEXT_SECONDARY,
    VIOLET_PRIMARY,
)

_POPUP_W = 200
_POPUP_H = 40
# When live partial text is present we grow vertically; width stays the same so
# the popup never lurches sideways mid-utterance.
_POPUP_W_EXPANDED = 360
_POPUP_H_EXPANDED = 92
# ``compact mode`` (Settings → Behavior → "Compact recording popup"): the popup
# strips down to just the audio visualizer — no status dot, no elapsed label,
# no partial-transcript panel. Smaller window, no expansion when partials
# arrive. For users who find the live word preview distracting.
#
# Sizing: the visualizer's natural minimum is 89×24 (14 bars × 3 px + 13 gaps
# × 3 px + 8 px padding, fixed 24 px high). 110×30 frames it cleanly — bars
# get ~6 px breathing room each side and the full 24 px of height after
# layout margins (3 px top/bottom × 2).
_POPUP_W_COMPACT = 110
_POPUP_H_COMPACT = 30
# Trim long partials from the head so the most recent words are always
# visible — chars not words because the partial can include long unbroken
# strings (URLs, etc).
_PARTIAL_MAX_CHARS = 180

# Committed chars render at full opacity in VIOLET_PRIMARY. Tentative
# chars sit at this opacity so the user reads them as "still settling"
# without ghosting — bright enough to feel present, dim enough to
# distinguish from committed. Promoted instantly to 1.0 when LA-2
# commits them.
_TENTATIVE_ALPHA = 0.60
# Typewriter cadence for *newly committed* characters that weren't
# previously on screen. At 1.4 chars per 16 ms tick ≈ 87 chars/sec —
# fast enough that it reads as "appearing" rather than "typing," slow
# enough that the eye picks up the left-to-right motion. Tentative
# chars and chars promoted from tentative→committed do NOT typewriter;
# they jump instantly.
_TYPEWRITER_CHARS_PER_TICK = 1.4


class _StatusDot(QWidget):
    """14×14 px state indicator with a slow breathing halo.

    The inner dot is solid; the halo around it pulses in opacity at
    ~1 Hz to give the popup a heartbeat. Subtle enough to not be
    distracting, present enough that the eye registers "alive."
    Pulsing only runs while the dot is in a non-idle color.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self._color = QColor(COLOR_IDLE)
        self._pulsing = False
        self._t0 = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(33)  # ~30 fps is plenty for a slow pulse
        self._timer.timeout.connect(self.update)

    def set_color(self, color: QColor | str) -> None:
        self._color = QColor(color)
        # Pulse only when actively recording / transcribing — not at
        # rest (idle gray). Cheap check by color equality.
        new_pulsing = self._color != QColor(COLOR_IDLE)
        if new_pulsing != self._pulsing:
            self._pulsing = new_pulsing
            if self._pulsing:
                self._t0 = time.monotonic()
                self._timer.start()
            else:
                self._timer.stop()
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        # Halo alpha breathes between ~40 and ~120 over a 1 s cycle
        # while pulsing; sits at 60 otherwise.
        if self._pulsing:
            phase = 2.0 * np.pi * (time.monotonic() - self._t0) * 1.0  # 1 Hz
            halo_alpha = int(80 + 40 * (0.5 + 0.5 * np.sin(phase)))
        else:
            halo_alpha = 60
        glow = QColor(self._color)
        glow.setAlpha(halo_alpha)
        p.setBrush(glow)
        p.drawEllipse(QRect(0, 0, 14, 14))
        # Main dot
        p.setBrush(self._color)
        p.drawEllipse(QRect(3, 3, 8, 8))


class _Visualizer(QWidget):
    """Centered audio level meter. 14 bars, ~60 fps animation.

    Design notes (tuned with the user):
    - **Gain + perceptual curve.** Raw RMS for normal speech hovers
      0.02-0.08; the sqrt of `gain*rms` clipped to 1.0 maps that to
      most of the bar height so the bars *feel* responsive instead of
      hugging the floor.
    - **Asymmetric easing.** Attack (level rising) is fast so the
      visualizer snaps to your voice. Release (level falling) is slow
      so bars don't snap dead between syllables — they fall like a
      real VU meter.
    - **Idle ripple.** When real audio is quiet (between syllables or
      during the dead-air gap before speech), bars don't sit at zero —
      they pulse in a gentle left-to-right ripple. The popup reads as
      "I'm listening" even when nothing's coming through yet. Real
      audio dominates the moment any voice arrives.
    """

    BAR_COUNT = 14
    BAR_WIDTH = 3
    BAR_GAP = 3
    GAIN = 12.0
    ATTACK = 0.50  # toward target when rising
    RELEASE = 0.10  # toward target when falling
    # Idle ripple parameters. `IDLE_BASE` is the resting bar height the
    # ripple oscillates around, `IDLE_AMPLITUDE` is how far it swings,
    # `IDLE_FREQ_HZ` is the breathing rate.
    IDLE_BASE = 0.06
    IDLE_AMPLITUDE = 0.045
    IDLE_FREQ_HZ = 1.4
    # When real audio level exceeds this, ripple fades out and lets
    # the real bars take over. Below this, ripple shows through.
    IDLE_CUTOFF = 0.12

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(24)
        self.setMinimumWidth(
            self.BAR_COUNT * self.BAR_WIDTH + (self.BAR_COUNT - 1) * self.BAR_GAP + 8
        )
        self._levels = np.zeros(self.BAR_COUNT, dtype=np.float32)
        self._targets = np.zeros(self.BAR_COUNT, dtype=np.float32)
        self._active = False
        self._t0 = 0.0  # monotonic time when ripple started

        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def push_samples(self, samples: np.ndarray) -> None:
        """Called on the Qt main thread (queued from audio thread)."""
        if samples.ndim > 1:
            samples = samples.reshape(-1)
        if samples.size == 0:
            return
        windows = np.array_split(samples, self.BAR_COUNT)
        rms = np.array(
            [float(np.sqrt(np.mean(w * w))) if w.size else 0.0 for w in windows],
            dtype=np.float32,
        )
        amplified = np.clip(rms * self.GAIN, 0.0, 1.0)
        # Perceptual curve — quiet speech lands at a visible level.
        self._targets = np.sqrt(amplified)

    def start(self) -> None:
        self._active = True
        self._t0 = time.monotonic()
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._active = False
        self._targets = np.zeros(self.BAR_COUNT, dtype=np.float32)

    def _tick(self) -> None:
        diff = self._targets - self._levels
        # Attack (rising) is fast, release (falling) is slow — VU-meter feel.
        step = np.where(diff >= 0, diff * self.ATTACK, diff * self.RELEASE)
        self._levels = self._levels + step

        # Idle ripple: a gentle sine wave across bars, only blended in
        # when real audio is quiet. The blend factor `fade` goes 1→0 as
        # real audio crosses IDLE_CUTOFF, so the ripple disappears the
        # moment the user starts speaking.
        if self._active:
            elapsed = time.monotonic() - self._t0
            real_max = float(self._levels.max())
            fade = max(0.0, min(1.0, 1.0 - real_max / max(self.IDLE_CUTOFF, 1e-6)))
            if fade > 0.01:
                phase = 2.0 * np.pi * self.IDLE_FREQ_HZ * elapsed
                # Per-bar phase offset gives the left-to-right wave.
                offsets = np.arange(self.BAR_COUNT, dtype=np.float32) * 0.45
                ripple = self.IDLE_BASE + self.IDLE_AMPLITUDE * (
                    0.5 + 0.5 * np.sin(phase - offsets)
                )
                # Take the max of real level and ripple so loud syllables
                # always win; ripple only shows where real is quiet.
                self._levels = np.maximum(self._levels, ripple * fade)

        if not self._active and float(self._levels.max()) < 0.005:
            self._timer.stop()
            self._levels = np.zeros(self.BAR_COUNT, dtype=np.float32)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        h = self.height()
        total_w = self.BAR_COUNT * self.BAR_WIDTH + (self.BAR_COUNT - 1) * self.BAR_GAP
        x = (w - total_w) // 2
        cy = h // 2
        max_half = (h - 4) // 2

        for i in range(self.BAR_COUNT):
            level = float(self._levels[i])
            bar_h = max(2, int(level * 2 * max_half))
            y = cy - bar_h // 2
            color = QColor(VIOLET_PRIMARY)
            color.setAlpha(int(170 + 80 * level))
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(
                x + i * (self.BAR_WIDTH + self.BAR_GAP),
                y,
                self.BAR_WIDTH,
                bar_h,
                1.5,
                1.5,
            )


class _PartialTextRenderer(QWidget):
    """Typewriter-revealed live partial transcript.

    Design (post-fade):
    - Tentative chars (the leading edge from Moonshine) appear *instantly*
      at `_TENTATIVE_ALPHA`. No fade — the eye reads any fade as latency.
    - Brand-new *committed* chars (text that appears for the first time
      already in the committed state, e.g. from a VAD-finalized segment)
      sweep in left-to-right at typewriter speed. This gives the visual
      rhythm that ChatGPT/Wispr Flow streaming has.
    - Chars that transition tentative→committed (LA-2 promotes them)
      are already visible; they brighten instantly to full alpha. No
      visual "second arrival."

    Implementation model: each char has an `appear_at` cursor value.
    A monotonic `_cursor` advances at `_TYPEWRITER_CHARS_PER_TICK` per
    16 ms tick. A char paints at its target alpha once `_cursor >=
    appear_at[i]`; before that it's invisible. Tentative chars get
    `appear_at = -1` (always visible).
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        f = QFont()
        f.setPointSize(9)
        self.setFont(f)
        self._color = QColor(VIOLET_PRIMARY)

        self._chars: list[str] = []
        self._target_alpha: np.ndarray = np.zeros(0, dtype=np.float32)
        self._appear_at: np.ndarray = np.zeros(0, dtype=np.float32)
        self._cursor: float = 0.0

        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    # ------------------------------------------------------------------ API
    def set_partial(self, committed: str, tentative: str) -> None:
        """Diff against current text; reveal new committed chars via cursor."""
        if committed and tentative:
            new_text = f"{committed} {tentative}"
            split_idx = len(committed) + 1  # tentative starts after the space
        else:
            new_text = committed or tentative
            split_idx = len(committed)
        if len(new_text) > _PARTIAL_MAX_CHARS:
            drop = len(new_text) - _PARTIAL_MAX_CHARS
            new_text = "…" + new_text[drop + 1 :]
            split_idx = max(1, split_idx - drop)

        # Longest common prefix preserves the appear_at schedule of
        # already-displayed chars. That's why a tentative→committed
        # promotion never flickers: the char's appear_at stays at -1
        # (or whatever past value) and only target_alpha jumps.
        #
        # CASE-INSENSITIVE: tentative text from Moonshine and finalized
        # text after VAD can differ in capitalization (e.g. "hello" vs
        # "Hello"). Treating them as the same character for diff
        # purposes lets the popup preserve visibility across the
        # finalization transition — only the rendered glyph changes
        # case in place. Without this, the LCP collapses to 0 at every
        # phrase boundary and the popup hard-resets.
        prev = "".join(self._chars)
        lcp_len = 0
        for a, b in zip(prev, new_text, strict=False):
            if a.lower() == b.lower():
                lcp_len += 1
            else:
                break

        new_len = len(new_text)
        new_targets = np.empty(new_len, dtype=np.float32)
        new_appear = np.empty(new_len, dtype=np.float32)

        # Preserve appear_at for matched prefix.
        if lcp_len:
            new_appear[:lcp_len] = self._appear_at[:lcp_len]

        # Targets: split tells us committed vs tentative.
        new_targets[:split_idx] = 1.0
        new_targets[split_idx:] = _TENTATIVE_ALPHA

        # Reformat detection: typewriter only fires for pure
        # extensions — cases where every previously-displayed char is
        # still present and only NEW chars are appended at the tail.
        # If ANY previously-displayed char got replaced (punctuation
        # shifted, model rewrote a word, capitalization changed past
        # the LCP), treat the whole update as a reformat and render
        # instantly. The typewriter sweeping a re-formatted line
        # visually clears the screen and writes from top-left, which
        # reads as a hard reset.
        prev_len = len(prev)
        is_reformat = prev_len > 0 and lcp_len < prev_len

        # Schedule new chars beyond the LCP. Committed new chars
        # typewriter in (unless this is a reformat, in which case
        # everything appears instantly); tentative new chars always
        # appear instantly.
        if new_len > lcp_len:
            # Start scheduling from whichever is later: the live cursor,
            # or the last-already-scheduled char among the preserved
            # prefix (so we never schedule a char before one to its left).
            sched = float(self._cursor)
            if lcp_len > 0:
                sched = max(sched, float(np.max(new_appear[:lcp_len])))
            for i in range(lcp_len, new_len):
                if i < split_idx and not is_reformat:
                    sched += 1.0
                    new_appear[i] = sched
                else:
                    new_appear[i] = -1.0  # visible immediately

        self._chars = list(new_text)
        self._target_alpha = new_targets
        self._appear_at = new_appear

        # Only run the tick timer when at least one char is still
        # behind the cursor. Saves 60 Hz repaints once everything is
        # revealed.
        if new_len and float(np.max(new_appear)) > self._cursor:
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def clear(self) -> None:
        self._chars = []
        self._target_alpha = np.zeros(0, dtype=np.float32)
        self._appear_at = np.zeros(0, dtype=np.float32)
        self._cursor = 0.0
        self._timer.stop()
        self.update()

    def has_text(self) -> bool:
        return bool(self._chars)

    # ----------------------------------------------------------------- tick
    def _tick(self) -> None:
        if self._appear_at.size == 0:
            self._timer.stop()
            return
        max_appear = float(np.max(self._appear_at))
        if self._cursor >= max_appear:
            self._timer.stop()
            return
        self._cursor += _TYPEWRITER_CHARS_PER_TICK
        self.update()

    # ---------------------------------------------------------------- paint
    def paintEvent(self, _event) -> None:  # noqa: N802
        if not self._chars:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setFont(self.font())
        fm = QFontMetricsF(self.font())
        line_h = fm.height()
        space_w = fm.horizontalAdvance(" ")
        max_w = float(self.width())
        baseline_y = fm.ascent()
        x = 0.0
        # Word-wrap by greedy whitespace breaks; render char-by-char so
        # per-char alpha takes effect. We compute word widths up front so
        # a word that wouldn't fit at the current x advances to the next
        # line before any of its chars are drawn.
        i = 0
        n = len(self._chars)
        while i < n:
            ch = self._chars[i]
            if ch == " ":
                # Defer drawing the space until we know the next word fits.
                # If end of line, just skip; otherwise advance and draw.
                # Find the next word's end.
                j = i + 1
                while j < n and self._chars[j] != " ":
                    j += 1
                word = "".join(self._chars[i + 1 : j])
                word_w = fm.horizontalAdvance(word) if word else 0.0
                if x + space_w + word_w > max_w and x > 0.0:
                    # Wrap before this word; skip the space.
                    x = 0.0
                    baseline_y += line_h
                else:
                    x += space_w
                i += 1
                continue

            # Non-space char. Find the run to the next space to check if
            # the whole word still fits on this line.
            if i == 0 or self._chars[i - 1] == " ":
                j = i
                while j < n and self._chars[j] != " ":
                    j += 1
                word_w = fm.horizontalAdvance("".join(self._chars[i:j]))
                if x + word_w > max_w and x > 0.0:
                    x = 0.0
                    baseline_y += line_h

            appear = float(self._appear_at[i])
            if appear < 0 or self._cursor >= appear:
                alpha = float(self._target_alpha[i])
            else:
                alpha = 0.0
            if alpha > 0.005:
                color = QColor(self._color)
                color.setAlphaF(min(1.0, max(0.0, alpha)))
                p.setPen(color)
                p.drawText(QPoint(int(x), int(baseline_y)), ch)
            x += fm.horizontalAdvance(ch)
            i += 1


_RESIZE_DURATION_S = 0.16


class RecordingPopup(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(_POPUP_W, _POPUP_H)
        # Compact mode = just the visualizer. Set via ``set_compact``
        # from app.py at startup + on config change. Affects:
        #   - dot + elapsed label + partial panel visibility
        #   - popup window size
        #   - whether ``set_partial`` does anything
        #   - target size used by the collapse / expand animation
        self._compact = False

        # Cursor-follow: while the popup is visible (RECORDING /
        # TRANSCRIBING) it tracks the user's mouse at ~60 Hz so it
        # stays anchored above-right of wherever the cursor is now.
        # Paused during the resize animation so the bottom-anchor
        # math in ``_resize_tick`` doesn't fight the follow updates.
        # Opt-in (default off) because moving the mouse over a
        # terminal with xterm mouse-tracking enabled echoes motion
        # codes into stdin, which corrupts pasted transcripts.
        self._follow_enabled = False
        self._follow_timer = QTimer(self)
        self._follow_timer.setInterval(16)
        self._follow_timer.timeout.connect(self._follow_tick)

        # Smooth resize animation state. The popup eases between compact
        # and expanded shapes over `_RESIZE_DURATION_S` with an ease-out
        # cubic, so first-text and end-of-utterance feel like the popup
        # is breathing rather than snapping. Top-anchored: the TOP edge
        # stays put while the popup grows DOWNWARD (dropdown feel), so
        # streaming text flows top→bottom the way you read it. The popup
        # is anchored below the cursor (see _reposition) so growing down
        # stays clear of what the user is reading above.
        self._resize_timer = QTimer(self)
        self._resize_timer.setInterval(16)
        self._resize_timer.timeout.connect(self._resize_tick)
        self._resize_t0 = 0.0
        self._resize_from: tuple[int, int] = (_POPUP_W, _POPUP_H)
        self._resize_to: tuple[int, int] = (_POPUP_W, _POPUP_H)
        self._resize_top_y: int = 0  # screen-space y of the pinned top edge

        # Outcome flash. After a successful paste the popup turns green and
        # shows "✓ Sent"; on a failure it turns red with "✗ Failed". Either
        # way it auto-hides after a beat so the user gets an unmistakable
        # signal. ``_flash_color`` is the active flash tint (None = no flash).
        self._flash_color: str | None = None
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._finish_flash)

        self._dot = _StatusDot(self)

        self._elapsed_label = QLabel("0:00", self)
        self._elapsed_label.setStyleSheet(f"color: {TEXT_SECONDARY};")
        mono = QFont("Consolas")
        mono.setPointSize(9)
        self._elapsed_label.setFont(mono)

        self._visualizer = _Visualizer(self)

        # Top row: [dot] [visualizer flexing] [elapsed time]. Stays compact
        # whenever the popup has no partial text — keeps the resting size
        # tiny like the user wanted.
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        top.addWidget(self._dot)
        top.addWidget(self._visualizer, stretch=1)
        top.addWidget(self._elapsed_label)

        # Partial transcript renderer: hidden by default, shown when the
        # streaming engine produces text. Custom-painted with per-char
        # fade — see `_PartialTextRenderer` above for the rationale.
        self._partial = _PartialTextRenderer(self)
        self._partial.setVisible(False)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 6, 12, 6)
        root.setSpacing(4)
        root.addLayout(top)
        root.addWidget(self._partial)

        # Materialize the native HWND up front. Without this, the first
        # show_recording() pays a 30-80 ms DWM cold-paint cost that the
        # user feels as hotkey lag. Subsequent shows are fast either way.
        self.create()

    # ------------------------------------------------------------------ paint
    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # Tinted border while flashing an outcome (green sent / red failed);
        # normal border otherwise.
        border_color = self._flash_color if self._flash_color is not None else BORDER
        width = 2 if self._flash_color is not None else 1
        p.setPen(QPen(QColor(border_color), width))
        p.setBrush(QBrush(QColor(BG_PANEL)))
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 12, 12)

    # ------------------------------------------------------------- placement
    def _reposition(self) -> None:
        cursor_pos = QCursor.pos()
        screen = QApplication.screenAt(cursor_pos) or QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        # Anchor below-right of the cursor so the popup can open DOWNWARD
        # (dropdown-style) as live text streams in — see _resize_tick. Sits
        # below the caret so the growth doesn't cover what's above it.
        x = cursor_pos.x() + 16
        y = cursor_pos.y() + 24
        # Clamp to screen bounds.
        x = max(geo.x() + 8, min(x, geo.x() + geo.width() - self.width() - 8))
        y = max(geo.y() + 8, min(y, geo.y() + geo.height() - self.height() - 8))
        self.move(QPoint(x, y))

    # ------------------------------------------------------------------ API
    def show_recording(self) -> None:
        # Cancel any in-flight outcome flash from the previous utterance.
        self._flash_timer.stop()
        self._flash_color = None
        self._dot.set_color(COLOR_RECORDING)
        self._elapsed_label.setText("0:00")
        self._visualizer.start()
        # Start compact — the streaming worker will expand us once it has
        # something to show. Snap rather than animate: the popup isn't
        # visible yet, and a stale animation from a previous session
        # would otherwise show the popup mid-resize on the first frame.
        self._collapse_partial(animated=False)
        self._reposition()
        self.show()
        self.raise_()
        if self._follow_enabled:
            self._follow_timer.start()

    def show_transcribing(self) -> None:
        self._dot.set_color(COLOR_TRANSCRIBING)
        self._elapsed_label.setText("…")
        self._visualizer.stop()
        # Keep partial text visible during the final transcribe — it's the
        # closest preview the user has to what's about to be pasted.
        self._reposition()
        self.show()
        self.raise_()
        # Idempotent — keeps following through the brief transcribe phase.
        if self._follow_enabled:
            self._follow_timer.start()

    def push_samples(self, samples: np.ndarray) -> None:
        self._visualizer.push_samples(samples)

    def set_partial(self, committed: str, tentative: str = "") -> None:
        """Update the live partial transcript.

        `committed` chars render at full opacity; `tentative` chars render
        at the dim opacity. An empty (committed, tentative) collapses the
        partial area. Popup is NOT repositioned mid-utterance — anchoring
        only happens on show_recording / show_transcribing.

        No-op in compact mode — the partial panel doesn't exist there.
        """
        if self._compact:
            return
        committed = committed.strip()
        tentative = tentative.strip()
        if not committed and not tentative:
            self._collapse_partial()
            return
        self._partial.set_partial(committed, tentative)
        self._partial.setVisible(True)
        if self.size().width() != _POPUP_W_EXPANDED:
            self._animate_resize_to(_POPUP_W_EXPANDED, _POPUP_H_EXPANDED)

    def set_elapsed(self, seconds: float) -> None:
        m, s = divmod(int(seconds), 60)
        self._elapsed_label.setText(f"{m}:{s:02d}")

    def flash_sent(self) -> None:
        """Confirm a successful paste with a brief green "✓ Sent"."""
        self._flash(COLOR_SENT, "✓ Sent", 700)

    def flash_error(self) -> None:
        """Signal a failure with a brief red "✗ Failed". Held a touch longer
        than the sent flash so the user actually registers it (the tray
        notification carries the detail)."""
        self._flash(COLOR_ERROR, "✗ Failed", 1600)

    def _flash(self, color: str, label: str, duration_ms: int) -> None:
        """Tint the popup (border + dot + label) for ``duration_ms`` as an
        outcome cue, then auto-hide. The border tint shows in compact mode
        too; the dot + label only exist in the normal layout.
        """
        self._visualizer.stop()
        self._follow_timer.stop()
        self._resize_timer.stop()
        self._collapse_partial(animated=False)
        self._flash_color = color
        if not self._compact:
            self._dot.set_color(color)
            self._elapsed_label.setText(label)
        if not self.isVisible():
            self.show()
            self.raise_()
        self.update()
        self._flash_timer.start(duration_ms)

    def _finish_flash(self) -> None:
        self._flash_color = None
        self.hide_popup()

    def hide_popup(self) -> None:
        self._flash_color = None
        self._visualizer.stop()
        self._follow_timer.stop()
        self._collapse_partial()
        self.hide()

    def _follow_tick(self) -> None:
        """Re-anchor the popup to the cursor every 16 ms while visible.

        Skipped during the resize animation — that path owns the popup
        position momentarily (bottom-anchoring as it grows upward) and
        a follow update mid-resize would fight the bottom-anchor math.
        Resize windows are brief (~250 ms), so the user feels a slight
        pause in follow during partial-text expansion — acceptable.
        """
        if not self.isVisible():
            return
        if self._resize_timer.isActive():
            return
        self._reposition()

    def set_follow_cursor(self, enabled: bool) -> None:
        """Toggle whether the popup tracks the cursor while visible.

        Off by default. Hot-applies — if the popup is currently visible
        we start / stop the follow timer immediately.
        """
        if enabled == self._follow_enabled:
            return
        self._follow_enabled = enabled
        if enabled and self.isVisible():
            self._follow_timer.start()
        elif not enabled:
            self._follow_timer.stop()

    def set_compact(self, compact: bool) -> None:
        """Toggle the just-the-bars compact look.

        Hides the status dot, the elapsed-time label, and the partial-
        transcript panel; tightens layout margins; resizes the popup
        to the compact dimensions. Idempotent — calling with the
        current value is a no-op. Safe to call at any time including
        while the popup is visible.
        """
        if compact == self._compact:
            return
        self._compact = compact
        self._dot.setVisible(not compact)
        self._elapsed_label.setVisible(not compact)
        # The partial panel is permanently hidden in compact mode and
        # only conditionally visible in normal mode — set_partial /
        # _collapse_partial flip its visibility there.
        self._partial.setVisible(False)
        if compact:
            self._partial.clear()
        # Tighten margins so the visualizer fills the small frame.
        layout = self.layout()
        if layout is not None:
            if compact:
                layout.setContentsMargins(8, 3, 8, 3)
            else:
                layout.setContentsMargins(12, 6, 12, 6)
        # Snap to the new resting size — a config change is a one-shot
        # event, not the per-utterance breathing the animation is for.
        self._resize_timer.stop()
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        w, h = self._resting_size()
        self.setFixedSize(w, h)
        # If we're currently visible, re-anchor so the change doesn't
        # leave the popup mid-screen at the wrong size, and make sure
        # cursor-follow keeps tracking (so the toggle doesn't feel like
        # it "locks" the popup in place mid-session).
        if self.isVisible():
            self._reposition()
            if self._follow_enabled and not self._follow_timer.isActive():
                self._follow_timer.start()

    # ------------------------------------------------------------- internal
    def _resting_size(self) -> tuple[int, int]:
        """Width / height the popup returns to between partials."""
        if self._compact:
            return _POPUP_W_COMPACT, _POPUP_H_COMPACT
        return _POPUP_W, _POPUP_H

    def _collapse_partial(self, animated: bool = True) -> None:
        """Shrink back to the resting shape. Animate when called
        mid-session (a graceful end-of-utterance); snap when called
        from show_recording (the popup isn't visible yet)."""
        self._partial.clear()
        self._partial.setVisible(False)
        target_w, target_h = self._resting_size()
        if animated and self.isVisible() and self.size().width() != target_w:
            self._animate_resize_to(target_w, target_h)
        else:
            self._resize_timer.stop()
            self.setMinimumSize(0, 0)
            self.setMaximumSize(16777215, 16777215)
            self.setFixedSize(target_w, target_h)

    def _animate_resize_to(self, target_w: int, target_h: int) -> None:
        """Smoothly resize the popup, keeping the TOP edge anchored.

        Top-anchoring means the popup grows downward as it expands — a
        dropdown opening, matching how the eye reads streaming text. The
        popup is anchored below the cursor so this growth stays clear of
        what the user is reading above it.
        """
        if (self.width(), self.height()) == (target_w, target_h):
            return
        # Remove fixed-size constraint so we can resize incrementally.
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self._resize_from = (self.width(), self.height())
        self._resize_to = (target_w, target_h)
        self._resize_top_y = self.y()
        self._resize_t0 = time.monotonic()
        if not self._resize_timer.isActive():
            self._resize_timer.start()

    def _resize_tick(self) -> None:
        elapsed = time.monotonic() - self._resize_t0
        t = min(1.0, elapsed / _RESIZE_DURATION_S)
        # Ease-out cubic: starts fast, decelerates into the target.
        eased = 1.0 - (1.0 - t) ** 3
        fw, fh = self._resize_from
        tw, th = self._resize_to
        cur_w = int(fw + (tw - fw) * eased)
        cur_h = int(fh + (th - fh) * eased)
        self.resize(cur_w, cur_h)
        # Pin the top edge so the popup grows downward (dropdown), not up.
        self.move(self.x(), self._resize_top_y)
        if t >= 1.0:
            self._resize_timer.stop()
            self.setFixedSize(tw, th)
            self.move(self.x(), self._resize_top_y)
