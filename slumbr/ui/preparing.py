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

from .._bundled import bundled_models_root
from ..config import BackendConfig, SlumbrConfig
from ..stt.factory import build_transcriber
from ..stt.streaming_engine import StreamingASREngine
from ..theme import BG_DARK, BORDER, FONT_DISPLAY, TEXT_PRIMARY, TEXT_SECONDARY, VIOLET_PRIMARY

log = logging.getLogger(__name__)

# Don't flash the dialog on a warm/cached launch — only show it if prep is
# still running after this long.
_SHOW_AFTER_MS = 500


def _cpu_fallback_backend() -> BackendConfig:
    """The universal safety net: Moonshine on CPU. It's bundled in every build
    (CPU + NVIDIA), needs no GPU, and only a one-time ~180 MB model download — so
    it can stand in for ANY GPU backend that fails to load on a user's machine
    (bad/old driver, missing wheels in the wrong frozen build, ONNX export
    failure, OOM on warm-up). Without this, a single backend-load exception used
    to hard-exit the whole app on someone else's hardware."""
    return BackendConfig(name="moonshine", model="moonshine-base-en-int8")


class _EngineWorker(QObject):
    """Builds + warms the transcriber and the streaming engine off the main
    thread. None of these are Qt objects, so they're safe to construct here."""

    ready = Signal(object, object)   # (transcriber, streaming_engine)
    failed = Signal(str)
    fell_back = Signal(object, str)  # (fallback BackendConfig, human reason)

    def __init__(self, config: SlumbrConfig) -> None:
        super().__init__()
        self._config = config

    def _build(self, backend: BackendConfig | None) -> object:
        transcriber = build_transcriber(
            backend,
            language=self._config.language or None,
            initial_prompt=self._config.initial_prompt,
        )
        transcriber.warm_up()
        return transcriber

    def run(self) -> None:
        primary = self._config.backend
        try:
            transcriber = self._build(primary)
        except Exception as e:  # noqa: BLE001
            log.exception(
                "engine prep failed for backend=%s — trying CPU fallback",
                getattr(primary, "name", primary),
            )
            fallback = _cpu_fallback_backend()
            # If the primary WAS already the CPU fallback, there's nowhere left
            # to fall — surface the real error.
            if primary is not None and primary.name == fallback.name:
                self.failed.emit(str(e))
                return
            try:
                transcriber = self._build(fallback)
            except Exception as e2:  # noqa: BLE001
                log.exception("CPU fallback engine also failed")
                self.failed.emit(f"{e}\n\n(CPU fallback also failed: {e2})")
                return
            self.fell_back.emit(
                fallback,
                f"Couldn't start the {getattr(primary, 'name', 'configured')} engine:\n\n"
                f"{e}\n\nSlumbr switched to the built-in CPU engine (Moonshine) so it "
                "keeps working. You can change this any time in Settings → Engine.",
            )
        try:
            streaming = StreamingASREngine(
                enable_streaming_leading_edge=self._config.streaming_visual_leading_edge,
            )
        except Exception as e:  # noqa: BLE001
            log.exception("streaming engine init failed")
            self.failed.emit(str(e))
            return
        self.ready.emit(transcriber, streaming)


class _PreparingDialog(QDialog):
    def __init__(
        self,
        model: str,
        app_icon: QIcon | None = None,
        accent: str = VIOLET_PRIMARY,
        bundled: bool = False,
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
        tf = QFont(FONT_DISPLAY)
        tf.setPointSize(15)
        tf.setBold(True)
        title.setFont(tf)
        lay.addWidget(title)

        if bundled:
            # Frozen build ships the model — no download, just the one-time
            # load into memory/VRAM. After the OS file cache warms, instant.
            sub_text = (
                f"Loading the {model} model into memory. "
                "This only takes a moment on the first launch — after that, "
                "startup is instant."
            )
        else:
            sub_text = (
                f"Loading the {model} model. The first run downloads it once "
                "(this can take a minute on a slow connection) — after that, "
                "startup is instant."
            )
        sub = QLabel(sub_text)
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
    worker.fell_back.connect(lambda fb, reason: result.update(fell_back=(fb, reason)))
    worker.ready.connect(thread.quit)
    worker.failed.connect(thread.quit)
    thread.started.connect(worker.run)

    model = config.backend.model if config.backend else "speech"
    dialog = _PreparingDialog(
        model, app_icon, config.accent_color, bundled=bundled_models_root() is not None
    )

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

    # The primary backend failed but the CPU fallback worked — persist the
    # switch so the next launch is instant (and doesn't re-hit the failure),
    # then tell the user (informational, NOT fatal — the app keeps running).
    if "fell_back" in result:
        fb, reason = result["fell_back"]  # type: ignore[misc]
        try:
            config.backend = fb
            config.save()
        except Exception as e:  # noqa: BLE001
            log.warning("could not persist CPU-fallback backend: %s", e)
        QMessageBox.information(None, "Slumbr — switched to the CPU engine", reason)

    transcriber, streaming = result["ok"]  # type: ignore[misc]
    return transcriber, streaming
