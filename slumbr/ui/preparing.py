"""First-run engine preparation with a progress dialog.

Building the transcriber + streaming engine downloads model weights on the
first run (Whisper large-v3-turbo ~800 MB, Moonshine ~180 MB, etc.). That
used to happen synchronously in ``App.__init__`` *after* the wizard closed —
so the user stared at a frozen nothing for 30 s to several minutes with no
hint anything was happening, then a crash if the download failed.

``prepare_engines`` moves that work onto a worker thread behind a modal
"Preparing Slumbr" dialog. The dialog only appears if prep is still running
after a short delay, so warm/cached launches don't flash it. A failure
becomes a clear error dialog instead of a silent startup crash.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QEventLoop, QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QMessageBox,
    QProgressBar,
    QVBoxLayout,
)

from ..config import SlumbrConfig
from ..stt.factory import build_transcriber
from ..stt.streaming_engine import StreamingASREngine
from ..theme import BG_DARK, BORDER, TEXT_PRIMARY, TEXT_SECONDARY, VIOLET_PRIMARY

log = logging.getLogger(__name__)

# Don't flash the dialog on a warm/cached launch — only show it if prep is
# still running after this long.
_SHOW_AFTER_MS = 500


class _EngineWorker(QObject):
    """Builds + warms the transcriber and the streaming engine off the main
    thread. None of these are Qt objects, so they're safe to construct here."""

    ready = Signal(object, object)  # (transcriber, streaming_engine)
    failed = Signal(str)

    def __init__(self, config: SlumbrConfig) -> None:
        super().__init__()
        self._config = config

    def run(self) -> None:
        try:
            transcriber = build_transcriber(
                self._config.backend,
                language=self._config.language or None,
                initial_prompt=self._config.initial_prompt,
            )
            transcriber.warm_up()
            streaming = StreamingASREngine(
                enable_streaming_leading_edge=self._config.streaming_visual_leading_edge,
            )
            self.ready.emit(transcriber, streaming)
        except Exception as e:  # noqa: BLE001
            log.exception("engine preparation failed")
            self.failed.emit(str(e))


class _PreparingDialog(QDialog):
    def __init__(
        self, model: str, app_icon: QIcon | None = None, accent: str = VIOLET_PRIMARY
    ) -> None:
        super().__init__()
        self.setWindowTitle("Slumbr")
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)  # can't close mid-prep
        if app_icon is not None:
            self.setWindowIcon(app_icon)
        self.setModal(True)
        self.setFixedWidth(440)
        self.setStyleSheet(
            f"QDialog {{ background: {BG_DARK}; }}"
            f"QLabel {{ color: {TEXT_PRIMARY}; }}"
            f"QProgressBar {{ background: {BG_DARK}; border: 1px solid {BORDER}; "
            f"border-radius: 6px; height: 8px; }}"
            f"QProgressBar::chunk {{ background: {accent}; border-radius: 6px; }}"
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(12)

        title = QLabel("Preparing Slumbr…")
        tf = QFont()
        tf.setPointSize(15)
        tf.setBold(True)
        title.setFont(tf)
        lay.addWidget(title)

        sub = QLabel(
            f"Loading the {model} model. The first run downloads it once "
            "(this can take a minute on a slow connection) — after that, "
            "startup is instant."
        )
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {TEXT_SECONDARY};")
        lay.addWidget(sub)

        bar = QProgressBar()
        bar.setRange(0, 0)  # indeterminate — we can't get a real % across backends
        bar.setTextVisible(False)
        lay.addWidget(bar)


def prepare_engines(
    config: SlumbrConfig, app_icon: QIcon | None = None
) -> tuple[object, object]:
    """Build + warm the transcriber and streaming engine, showing a progress
    dialog if it's slow. Returns ``(transcriber, streaming_engine)``.

    On failure (e.g. model download failed, no network on first run) shows a
    clear error dialog and raises ``SystemExit(1)`` rather than crashing
    silently — there's no usable app without an engine.
    """
    worker = _EngineWorker(config)
    thread = QThread()
    worker.moveToThread(thread)

    result: dict[str, object] = {}
    loop = QEventLoop()

    def _on_ready(transcriber: object, streaming: object) -> None:
        result["ok"] = (transcriber, streaming)
        loop.quit()

    def _on_failed(msg: str) -> None:
        result["err"] = msg
        loop.quit()

    worker.ready.connect(_on_ready)
    worker.failed.connect(_on_failed)
    worker.ready.connect(thread.quit)
    worker.failed.connect(thread.quit)
    thread.started.connect(worker.run)

    model = config.backend.model if config.backend else "speech"
    dialog = _PreparingDialog(model, app_icon, config.accent_color)

    thread.start()
    # Only show the dialog if prep hasn't already finished — keeps cached
    # launches from flashing a window.
    QTimer.singleShot(_SHOW_AFTER_MS, lambda: (not result) and dialog.show())
    loop.exec()  # pumps events (keeps the dialog animating) until prep ends
    dialog.close()
    thread.wait(3000)

    if "err" in result:
        QMessageBox.critical(
            None,
            "Slumbr — startup error",
            f"Couldn't load the speech engine:\n\n{result['err']}\n\n"
            "If this is the first run, check your internet connection — the "
            "model downloads once — then relaunch Slumbr.",
        )
        raise SystemExit(1)

    transcriber, streaming = result["ok"]  # type: ignore[misc]
    return transcriber, streaming
