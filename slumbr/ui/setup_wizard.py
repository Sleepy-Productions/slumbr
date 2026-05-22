"""First-launch setup wizard.

Fires once when ``SlumbrConfig.backend`` is ``None`` (fresh install or
legacy config that didn't have the field). Six screens:

    1. Welcome          — what's about to happen
    2. Probe            — animated hardware scan
    3. Recommend        — recommended backend + model, "Show alternatives"
    4. Install          — pip-install relevant wheels (no-op in Phase 1)
    5. Smoke-test       — "Say something now" → confirm decode works (skipped
                          in Phase 1 since we don't construct the engine yet —
                          ``app.py`` runs the warm-up after the wizard exits)
    6. Done             — recap + Finish

Phase 1 collapses Install + Smoke-test screens to a single "ready" page
because both Phase-1 backends (cuda_ct2, moonshine) ship in the base wheel;
nothing to install. Phase 2 will re-expose them.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import BackendConfig, SlumbrConfig
from ..hardware.probe import HardwareProfile, probe
from ..hardware.recommend import Recommendation, recommend
from ..theme import (
    BG_DARK,
    BG_PANEL,
    BG_PANEL_HI,
    BORDER,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    VIOLET_DEEP,
    VIOLET_PRIMARY,
    VIOLET_PRIMARY_HOVER,
)
from .tabs.engine import _BACKEND_LABELS  # noqa: PLC2701 (intentional shared list)

log = logging.getLogger(__name__)


def _wizard_qss() -> str:
    return f"""
    QDialog {{ background-color: {BG_DARK}; }}
    QWidget {{ color: {TEXT_PRIMARY}; font-family: "Segoe UI"; }}
    QLabel {{ color: {TEXT_PRIMARY}; }}

    QFrame#card {{
        background-color: {BG_PANEL};
        border: 1px solid {BORDER};
        border-radius: 14px;
    }}

    QPushButton {{
        background-color: {BG_PANEL_HI};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER};
        border-radius: 10px;
        padding: 10px 22px;
        font-weight: 500;
    }}
    QPushButton:hover {{ border: 1px solid {VIOLET_PRIMARY}; }}
    QPushButton:pressed {{ background-color: {VIOLET_DEEP}; }}
    QPushButton:focus {{ border: 1px solid {VIOLET_PRIMARY}; outline: none; }}
    QPushButton#primary {{
        background-color: {VIOLET_PRIMARY};
        border: 1px solid {VIOLET_PRIMARY};
        font-weight: 700;
        padding: 12px 26px;
    }}
    QPushButton#primary:hover {{
        background-color: {VIOLET_PRIMARY_HOVER};
        border: 1px solid {VIOLET_PRIMARY_HOVER};
    }}

    QComboBox {{
        background-color: {BG_PANEL_HI};
        border: 1px solid {BORDER};
        border-radius: 10px;
        padding: 10px 14px;
        min-height: 24px;
    }}
    QComboBox:hover, QComboBox:focus {{ border: 1px solid {VIOLET_PRIMARY}; }}
    QComboBox QAbstractItemView {{
        background-color: {BG_PANEL};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER};
        selection-background-color: {VIOLET_PRIMARY};
        selection-color: {TEXT_PRIMARY};
        outline: 0;
        padding: 6px;
    }}
    """


# ---------------------------------------------------------------- probe worker


class _ProbeWorker(QObject):
    """Runs the hardware probe off the Qt main thread so the "scanning"
    animation actually animates.
    """

    finished = Signal(object)  # HardwareProfile

    def run(self) -> None:
        try:
            profile = probe(timeout_s=3.0)
        except Exception as e:  # noqa: BLE001
            log.warning("probe failed: %s", e)
            profile = HardwareProfile(probe_method="fallback")
        self.finished.emit(profile)


# ---------------------------------------------------------------- the dialog


class SetupWizard(QDialog):
    """Self-contained wizard dialog. The app's startup path calls
    ``exec()`` on this before constructing tray / engine / hotkey,
    so the nested event loop doesn't fight any of those hooks (none
    have been installed yet at that point in startup).

    On accept(), ``config.backend`` has been set + ``config.save()`` called.
    On reject() (e.g. window closed), ``config.backend`` may still be None
    and the caller should treat that as "user cancelled — exit."
    """

    def __init__(
        self,
        config: SlumbrConfig,
        *,
        app_icon: QIcon | None = None,
        on_finished: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        self._on_finished = on_finished
        self.setWindowTitle("Slumbr — First-launch setup")
        if app_icon is not None:
            self.setWindowIcon(app_icon)
        self.setStyleSheet(_wizard_qss())
        self.setMinimumSize(640, 480)
        self.resize(720, 540)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(48, 40, 48, 40)
        outer.setSpacing(22)

        # Brand strip
        brand = QLabel("Slumbr")
        bf = QFont()
        bf.setPointSize(20)
        bf.setBold(True)
        brand.setFont(bf)
        brand.setStyleSheet(f"color: {VIOLET_PRIMARY}; letter-spacing: -0.5px;")
        outer.addWidget(brand)

        # The screen stack
        self._stack = QStackedWidget()
        outer.addWidget(self._stack, stretch=1)

        # Build screens up-front so signal wiring is straightforward.
        self._welcome = self._build_welcome()
        self._probing = self._build_probing()
        self._recommend = self._build_recommend()
        self._done = self._build_done()
        for w in (self._welcome, self._probing, self._recommend, self._done):
            self._stack.addWidget(w)

        # State that the probe + recommend screens populate.
        self._profile: HardwareProfile | None = None
        self._recommendation: Recommendation | None = None

        # Footer with Back / Next
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        self._back_btn = QPushButton("Back")
        self._back_btn.clicked.connect(self._on_back)
        footer.addWidget(self._back_btn)
        footer.addStretch(1)
        self._next_btn = QPushButton("Get started")
        self._next_btn.setObjectName("primary")
        self._next_btn.clicked.connect(self._on_next)
        footer.addWidget(self._next_btn)
        outer.addLayout(footer)

        self._update_footer_for_index(0)

    # ----------------------------------------------------- screens

    def _build_welcome(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(18)

        title = QLabel("Welcome to Slumbr")
        tf = QFont()
        tf.setPointSize(28)
        tf.setBold(True)
        title.setFont(tf)
        layout.addWidget(title)

        body = QLabel(
            "Slumbr is a local, offline voice dictation app. Everything runs "
            "on your machine — no cloud, no accounts, no telemetry.\n\n"
            "In the next step Slumbr will scan your hardware and pick the "
            "fastest speech-to-text backend that works on it. You can change "
            "any of this later in Settings."
        )
        body.setWordWrap(True)
        body.setStyleSheet(f"color: {TEXT_SECONDARY}; line-height: 1.5;")
        layout.addWidget(body)

        layout.addStretch(1)
        return w

    def _build_probing(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(18)

        title = QLabel("Looking at your hardware…")
        tf = QFont()
        tf.setPointSize(22)
        tf.setBold(True)
        title.setFont(tf)
        layout.addWidget(title)

        self._probe_status = QLabel("Querying GPU + CPU info…")
        self._probe_status.setStyleSheet(f"color: {TEXT_SECONDARY};")
        layout.addWidget(self._probe_status)

        # A simple animated dot row — pure Qt timer, no fancy spinner widget.
        self._dots = QLabel("●  ○  ○")
        self._dots.setStyleSheet(
            f"color: {VIOLET_PRIMARY}; font-size: 22px; padding: 16px 0;"
        )
        layout.addWidget(self._dots)

        self._dot_timer = QTimer(self)
        self._dot_timer.setInterval(280)
        self._dot_timer.timeout.connect(self._tick_dots)
        self._dot_phase = 0

        layout.addStretch(1)
        return w

    def _build_recommend(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(18)

        title = QLabel("Recommended setup")
        tf = QFont()
        tf.setPointSize(22)
        tf.setBold(True)
        title.setFont(tf)
        layout.addWidget(title)

        # Recommendation card
        from PySide6.QtWidgets import QFrame  # noqa: PLC0415

        card = QFrame()
        card.setObjectName("card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(22, 20, 22, 20)
        cl.setSpacing(10)

        self._rec_backend_label = QLabel("…")
        bf = QFont()
        bf.setPointSize(15)
        bf.setBold(True)
        self._rec_backend_label.setFont(bf)
        cl.addWidget(self._rec_backend_label)

        self._rec_model_label = QLabel("…")
        self._rec_model_label.setStyleSheet(f"color: {TEXT_SECONDARY};")
        cl.addWidget(self._rec_model_label)

        self._rec_reason = QLabel("")
        self._rec_reason.setWordWrap(True)
        self._rec_reason.setStyleSheet(f"color: {TEXT_PRIMARY}; padding-top: 6px;")
        cl.addWidget(self._rec_reason)

        layout.addWidget(card)

        # Alternatives dropdown (single picker — most users won't touch it)
        alt_row = QVBoxLayout()
        alt_row.setSpacing(8)
        alt_label = QLabel("Or choose a different backend")
        alt_label.setStyleSheet(f"color: {TEXT_SECONDARY};")
        alt_row.addWidget(alt_label)

        self._alt_combo = QComboBox()
        for name, label in _BACKEND_LABELS.items():
            self._alt_combo.addItem(label, userData=name)
        self._alt_combo.currentIndexChanged.connect(self._on_alt_changed)
        alt_row.addWidget(self._alt_combo)
        layout.addLayout(alt_row)

        layout.addStretch(1)
        return w

    def _build_done(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(18)

        title = QLabel("All set")
        tf = QFont()
        tf.setPointSize(28)
        tf.setBold(True)
        title.setFont(tf)
        layout.addWidget(title)

        self._done_summary = QLabel("…")
        self._done_summary.setWordWrap(True)
        self._done_summary.setStyleSheet(f"color: {TEXT_PRIMARY}; line-height: 1.5;")
        layout.addWidget(self._done_summary)

        tip = QLabel(
            "Tap your hotkey (Caps Lock by default) to start dictating. "
            "Right-click the tray icon for Settings or to quit."
        )
        tip.setWordWrap(True)
        tip.setStyleSheet(f"color: {TEXT_SECONDARY}; padding-top: 10px;")
        layout.addWidget(tip)

        layout.addStretch(1)
        return w

    # ----------------------------------------------------- navigation

    def _on_next(self) -> None:
        idx = self._stack.currentIndex()
        if idx == 0:  # welcome -> probing
            self._stack.setCurrentIndex(1)
            self._update_footer_for_index(1)
            self._start_probe()
        elif idx == 2:  # recommend -> done
            self._commit_recommendation()
            self._stack.setCurrentIndex(3)
            self._update_footer_for_index(3)
        elif idx == 3:  # done -> close + signal app to continue
            if self._on_finished is not None:
                self._on_finished()
            self.accept()  # QDialog.exec() returns Accepted
        # probing index has its own forward path triggered by the worker.

    def _on_back(self) -> None:
        idx = self._stack.currentIndex()
        if idx == 2:
            # Back to welcome — let the user re-probe if they want
            self._stack.setCurrentIndex(0)
            self._update_footer_for_index(0)

    def _update_footer_for_index(self, idx: int) -> None:
        # Back is meaningful only on the recommendation screen.
        self._back_btn.setVisible(idx == 2)
        labels = {
            0: "Get started",
            1: "",  # hidden during probe
            2: "Continue",
            3: "Finish",
        }
        self._next_btn.setText(labels.get(idx, "Next"))
        self._next_btn.setVisible(idx != 1)

    # ----------------------------------------------------- probe lifecycle

    def _start_probe(self) -> None:
        self._dot_phase = 0
        self._dot_timer.start()
        self._thread = QThread(self)
        self._worker = _ProbeWorker()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_probe_finished)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _tick_dots(self) -> None:
        # Cycle three dots: ●○○ ○●○ ○○● → repeat
        patterns = ["●  ○  ○", "○  ●  ○", "○  ○  ●"]
        self._dot_phase = (self._dot_phase + 1) % len(patterns)
        self._dots.setText(patterns[self._dot_phase])

    def _on_probe_finished(self, profile: HardwareProfile) -> None:
        self._dot_timer.stop()
        self._profile = profile
        self._recommendation = recommend(profile, phase1_only=True)
        self._populate_recommendation_screen()
        self._stack.setCurrentIndex(2)
        self._update_footer_for_index(2)

    # ----------------------------------------------------- recommend screen

    def _populate_recommendation_screen(self) -> None:
        rec = self._recommendation
        if rec is None:
            return
        self._rec_backend_label.setText(_BACKEND_LABELS.get(rec.primary.name, rec.primary.name))
        self._rec_model_label.setText(f"Model: {rec.primary.model}")
        self._rec_reason.setText(rec.reason)

        idx = self._alt_combo.findData(rec.primary.name)
        if idx >= 0:
            self._alt_combo.blockSignals(True)
            self._alt_combo.setCurrentIndex(idx)
            self._alt_combo.blockSignals(False)

    def _on_alt_changed(self, *_args) -> None:
        if self._recommendation is None:
            return
        new_name = self._alt_combo.currentData()
        if not new_name:
            return
        # Build a sensible default for the chosen backend by re-using
        # recommend()'s helpers indirectly via a synthetic Recommendation.
        primary = BackendConfig(
            name=new_name,
            model=_default_model_for(new_name),
            compute_type="int8_float16" if new_name == "cuda_ct2" else None,
            threads=4 if new_name in {"moonshine", "whispercpp_cpu"} else None,
            extra=dict(self._recommendation.primary.extra),
        )
        self._recommendation = Recommendation(
            primary=primary,
            runner_up=self._recommendation.runner_up,
            reason=f"Manually selected {_BACKEND_LABELS.get(new_name, new_name)}.",
        )
        self._rec_backend_label.setText(_BACKEND_LABELS.get(new_name, new_name))
        self._rec_model_label.setText(f"Model: {primary.model}")
        self._rec_reason.setText(self._recommendation.reason)

    def _commit_recommendation(self) -> None:
        if self._recommendation is None:
            return
        self._config.backend = self._recommendation.primary
        try:
            self._config.save()
            log.info(
                "wizard committed backend=%s model=%s",
                self._recommendation.primary.name,
                self._recommendation.primary.model,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("config save failed in wizard: %s", e)
        self._done_summary.setText(
            f"Slumbr will run on <b>"
            f"{_BACKEND_LABELS.get(self._recommendation.primary.name, self._recommendation.primary.name)}"
            f"</b> with the <b>{self._recommendation.primary.model}</b> model. "
            "You can change this any time from Settings → Engine."
        )


def _default_model_for(backend_name: str) -> str:
    return {
        "cuda_ct2": "large-v3-turbo",
        "moonshine": "moonshine-base-en-int8",
        "directml": "small",
        "whispercpp_sycl": "small.en-q5_k_m",
        "whispercpp_cpu": "small.en-q5_k_m",
    }.get(backend_name, "small")
