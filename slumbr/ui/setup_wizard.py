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
import sys
from collections.abc import Callable

from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..bootstrap.install import InstallResult, InstallWorker
from ..config import BackendConfig, SlumbrConfig
from ..hardware.probe import HardwareProfile, probe
from ..hardware.recommend import Recommendation, recommend
from ..theme import (
    BG_DARK,
    BG_PANEL,
    BG_PANEL_HI,
    BORDER,
    FONT_BODY,
    FONT_DISPLAY,
    RADIUS_CARD,
    RADIUS_MD,
    TEXT_DISABLED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    VIOLET_DEEP,
    VIOLET_PRIMARY,
    VIOLET_PRIMARY_HOVER,
)
from .anim import fade_window_in
from .tabs.engine import _BACKEND_LABELS  # noqa: PLC2701 (intentional shared list)

log = logging.getLogger(__name__)


def _wizard_qss() -> str:
    return f"""
    QDialog {{ background-color: {BG_DARK}; }}
    QWidget {{ color: {TEXT_PRIMARY}; font-family: "{FONT_BODY}", "Segoe UI"; }}
    QLabel {{ color: {TEXT_PRIMARY}; }}

    QFrame#card {{
        background-color: {BG_PANEL};
        border: 1px solid {BORDER};
        border-radius: {RADIUS_CARD}px;
    }}

    QPushButton {{
        background-color: {BG_PANEL_HI};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER};
        border-radius: {RADIUS_MD}px;
        padding: 8px 20px;
        font-weight: 500;
    }}
    QPushButton:hover {{ border: 1px solid {VIOLET_PRIMARY}; }}
    QPushButton:pressed {{ background-color: {VIOLET_DEEP}; }}
    QPushButton:focus {{ border: 1px solid {VIOLET_PRIMARY}; outline: none; }}
    QPushButton:disabled {{
        background-color: {BG_PANEL};
        color: {TEXT_DISABLED};
        border: 1px solid {BORDER};
    }}
    QPushButton#primary {{
        background-color: {VIOLET_PRIMARY};
        color: #0A0A0B;
        border: 1px solid {VIOLET_PRIMARY};
        font-weight: 700;
        padding: 12px 24px;
    }}
    QPushButton#primary:hover {{
        background-color: {VIOLET_PRIMARY_HOVER};
        border: 1px solid {VIOLET_PRIMARY_HOVER};
    }}
    QPushButton#primary:disabled {{
        background-color: {BG_PANEL_HI};
        color: {TEXT_DISABLED};
        border: 1px solid {BORDER};
    }}

    QComboBox {{
        background-color: {BG_PANEL_HI};
        border: 1px solid {BORDER};
        border-radius: {RADIUS_MD}px;
        padding: 8px 12px;
        min-height: 24px;
    }}
    QComboBox:hover, QComboBox:focus {{ border: 1px solid {VIOLET_PRIMARY}; }}
    QComboBox QAbstractItemView {{
        background-color: {BG_PANEL};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER};
        selection-background-color: {VIOLET_PRIMARY};
        selection-color: #0A0A0B;
        outline: 0;
        padding: 8px;
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
        bf = QFont(FONT_DISPLAY)
        bf.setPointSize(20)
        bf.setBold(True)
        brand.setFont(bf)
        brand.setStyleSheet(f"color: {VIOLET_PRIMARY}; letter-spacing: -0.5px;")
        outer.addWidget(brand)

        # The screen stack
        self._stack = QStackedWidget()
        outer.addWidget(self._stack, stretch=1)

        # Build screens up-front so signal wiring is straightforward.
        # Screen indices: 0=Welcome  1=Probing  2=Recommend  3=Install  4=Done
        # Install auto-skips to Done if the chosen backend's wheels are
        # already present in the venv (e.g. when install.ps1 -Backend was used).
        self._welcome = self._build_welcome()
        self._probing = self._build_probing()
        self._recommend = self._build_recommend()
        self._install = self._build_install()
        self._done = self._build_done()
        for w in (self._welcome, self._probing, self._recommend, self._install, self._done):
            self._stack.addWidget(w)

        # State that the probe + recommend screens populate.
        self._profile: HardwareProfile | None = None
        self._recommendation: Recommendation | None = None

        # Install lifecycle handles (filled in when install runs).
        self._install_thread: QThread | None = None
        self._install_worker: InstallWorker | None = None
        self._install_succeeded = False

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

    def show(self) -> None:  # noqa: N802
        # Fade the wizard in on open — seamless, ~150ms ease-out (see ui/anim).
        self.setWindowOpacity(0.0)
        super().show()
        self._fade_anim = fade_window_in(self)

    # ----------------------------------------------------- screens

    def _build_welcome(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(18)

        title = QLabel("Welcome to Slumbr")
        tf = QFont(FONT_DISPLAY)
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
        tf = QFont(FONT_DISPLAY)
        tf.setPointSize(22)
        tf.setBold(True)
        title.setFont(tf)
        layout.addWidget(title)

        self._probe_status = QLabel("Querying GPU + CPU info…")
        self._probe_status.setStyleSheet(f"color: {TEXT_SECONDARY};")
        layout.addWidget(self._probe_status)

        # A simple animated dot row — pure Qt timer, no fancy spinner widget.
        self._dots = QLabel("●  ○  ○")
        self._dots.setStyleSheet(f"color: {VIOLET_PRIMARY}; font-size: 22px; padding: 16px 0;")
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
        tf = QFont(FONT_DISPLAY)
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
        bf = QFont(FONT_DISPLAY)
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

    def _build_install(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(14)

        self._install_title = QLabel("Installing backend…")
        tf = QFont(FONT_DISPLAY)
        tf.setPointSize(22)
        tf.setBold(True)
        self._install_title.setFont(tf)
        layout.addWidget(self._install_title)

        self._install_subtitle = QLabel(
            "Slumbr is fetching the wheels for the backend you picked. "
            "First time can take 1–3 minutes depending on your connection."
        )
        self._install_subtitle.setWordWrap(True)
        self._install_subtitle.setStyleSheet(f"color: {TEXT_SECONDARY};")
        layout.addWidget(self._install_subtitle)

        self._install_log = QPlainTextEdit()
        self._install_log.setReadOnly(True)
        self._install_log.setFont(_mono_font())
        self._install_log.setStyleSheet(
            f"QPlainTextEdit {{ background: {BG_PANEL}; border: 1px solid {BORDER}; "
            f"border-radius: 12px; padding: 10px; color: {TEXT_PRIMARY}; }}"
        )
        layout.addWidget(self._install_log, stretch=1)

        return w

    def _build_done(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(18)

        title = QLabel("All set")
        tf = QFont(FONT_DISPLAY)
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
        elif idx == 2:  # recommend -> install (or skip-to-done)
            self._advance_from_recommend()
        elif idx == 3:  # install screen — Next acts as Cancel-or-Skip
            # Only enabled in two states:
            #   - install in progress: button is Cancel (handled separately)
            #   - install failed:      button is Back to Recommend
            self._on_install_back()
        elif idx == 4:  # done -> close + signal app to continue
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
        elif idx == 3:
            # On the install screen, Back means "stop and pick something else"
            self._on_install_back()

    def _update_footer_for_index(self, idx: int) -> None:
        # Back is meaningful on Recommend and Install (failed/finished) screens.
        self._back_btn.setVisible(idx in (2, 3))
        labels = {
            0: "Get started",
            1: "",  # hidden during probe
            2: "Continue",
            3: "Cancel install",  # becomes "Try another backend" on failure
            4: "Finish",
        }
        self._next_btn.setText(labels.get(idx, "Next"))
        self._next_btn.setVisible(idx != 1)
        # Disable Next on the install screen until pip is done — pressing
        # it during a running install routes to cancel, not skip.
        if idx == 3:
            self._next_btn.setEnabled(True)

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
        # Phase 2: DirectML for AMD/Intel is live, so use the real
        # recommendation (no `phase1_only` fallback to Moonshine).
        self._recommendation = recommend(profile)
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

    # ----------------------------------------------------- install lifecycle

    def _advance_from_recommend(self) -> None:
        """Recommend → Install (or skip straight to Done if no install needed)."""
        if self._recommendation is None:
            return
        backend_name = self._recommendation.primary.name
        extras = _extras_for_backend(backend_name)
        if not extras or _is_backend_installed(backend_name):
            log.info(
                "wizard: backend %s needs no install (extras=%r, installed=%s) — skipping to Done",
                backend_name,
                extras,
                _is_backend_installed(backend_name),
            )
            self._commit_recommendation()
            self._stack.setCurrentIndex(4)
            self._update_footer_for_index(4)
            return
        # In a FROZEN build there is no pip and no editable repo — the backend
        # set is fixed at build time. If the recommended GPU backend isn't
        # bundled in THIS download, attempting pip would dead-end the user; fall
        # back to the always-bundled CPU engine (Moonshine) instead so they get
        # a working app, and point them at the build that has their GPU support.
        if getattr(sys, "frozen", False):
            self._fallback_to_bundled(backend_name)
            return
        # Source install — show the Install screen and kick off pip.
        self._stack.setCurrentIndex(3)
        self._update_footer_for_index(3)
        self._start_install(extras, backend_name)

    def _fallback_to_bundled(self, wanted: str) -> None:
        """Frozen build can't pip-install ``wanted`` — commit the bundled CPU
        engine (Moonshine, present in every build) so the user still gets a
        working app, and tell them which build to grab for GPU acceleration."""
        fallback = self._recommendation.runner_up if self._recommendation else None
        if fallback is None or _extras_for_backend(fallback.name):
            fallback = BackendConfig(name="moonshine", model="moonshine-base-en-int8")
        self._config.backend = fallback
        try:
            self._config.save()
        except Exception as e:  # noqa: BLE001
            log.warning("config save failed in frozen fallback: %s", e)
        log.info("frozen build lacks %s — falling back to bundled %s", wanted, fallback.name)
        label = _BACKEND_LABELS.get(wanted, wanted)
        self._done_summary.setText(
            f"This download doesn't include the <b>{label}</b> engine for your "
            "hardware, so Slumbr will run on the built-in <b>CPU engine "
            "(Moonshine)</b> — it works on any PC. For GPU-accelerated dictation, "
            "grab the build that matches your hardware from the Slumbr releases "
            "page. You can switch any time from Settings → Engine."
        )
        self._stack.setCurrentIndex(4)
        self._update_footer_for_index(4)

    def _start_install(self, extras: list[str], backend_name: str) -> None:
        self._install_log.clear()
        self._install_title.setText(
            f"Installing {_BACKEND_LABELS.get(backend_name, backend_name)}…"
        )
        self._install_subtitle.setText(
            f"Running `pip install slumbr[{','.join(extras)}]` in your venv. "
            "First time can take 1–3 minutes."
        )
        self._install_succeeded = False
        self._next_btn.setText("Cancel install")

        self._install_thread = QThread(self)
        self._install_worker = InstallWorker(extras)
        self._install_worker.moveToThread(self._install_thread)
        self._install_thread.started.connect(self._install_worker.run)
        self._install_worker.line.connect(self._on_install_line)
        self._install_worker.finished.connect(self._on_install_finished)
        self._install_worker.finished.connect(self._install_thread.quit)
        self._install_worker.finished.connect(self._install_worker.deleteLater)
        self._install_thread.finished.connect(self._install_thread.deleteLater)
        self._install_thread.start()

    def _on_install_line(self, line: str) -> None:
        self._install_log.appendPlainText(line)
        # Keep the cursor at the bottom so the user sees progress.
        bar = self._install_log.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _on_install_finished(self, result: InstallResult) -> None:
        if result.success:
            log.info("install succeeded: %s", result.summary)
            self._install_succeeded = True
            self._install_title.setText("Backend installed")
            self._install_subtitle.setText(
                "Slumbr will relaunch to pick up the new wheels. Click Finish to continue."
            )
            # Commit the recommendation now that the wheels exist.
            self._commit_recommendation()
            # Auto-advance after a short pause so the user sees the success
            # message before Done lands.
            QTimer.singleShot(800, self._jump_to_done)
        else:
            log.warning("install failed: %s", result.summary)
            self._install_title.setText("Install failed")
            self._install_subtitle.setText(
                f"{result.summary}\n\nClick Back to pick a different backend, "
                "or fix the problem and re-run Slumbr."
            )
            self._next_btn.setText("Try another backend")

    def _jump_to_done(self) -> None:
        self._stack.setCurrentIndex(4)
        self._update_footer_for_index(4)
        # If install succeeded and the wheels include in-use DLLs, the
        # safe thing is to relaunch slumbr — done_summary already calls
        # this out. We don't auto-relaunch here because the user might
        # have unsaved work in the wizard surface.

    def _on_install_back(self) -> None:
        """Cancel a running install (or just go back if it already finished)."""
        if self._install_worker is not None and not self._install_succeeded:
            try:
                self._install_worker.cancel()
            except Exception as e:  # noqa: BLE001
                log.warning("install cancel raised: %s", e)
        self._stack.setCurrentIndex(2)
        self._update_footer_for_index(2)


def _default_model_for(backend_name: str) -> str:
    return {
        "cuda_ct2": "large-v3-turbo",
        "moonshine": "moonshine-base-en-int8",
        "directml": "small",
        "whispercpp_sycl": "small.en-q5_k_m",
        "whispercpp_cpu": "small.en-q5_k_m",
    }.get(backend_name, "small")


# Map each backend to the pyproject extras the wizard pip-installs.
# Moonshine is intentionally empty: sherpa-onnx is in base deps and is
# the only thing Moonshine needs, so no install step fires.
_BACKEND_EXTRAS: dict[str, list[str]] = {
    "cuda_ct2": ["nvidia"],
    "moonshine": [],
    "directml": ["amd"],
    "whispercpp_sycl": ["intel"],
    "whispercpp_cpu": ["cpu"],
}


def _extras_for_backend(backend_name: str) -> list[str]:
    return list(_BACKEND_EXTRAS.get(backend_name, []))


def _is_backend_installed(backend_name: str) -> bool:
    """Check whether the backend's primary import is satisfiable.

    Probes by ``importlib.util.find_spec`` (no module is actually
    imported here) so we don't pay startup cost for backends we're
    not going to use.
    """
    import importlib.util  # noqa: PLC0415

    probes: dict[str, list[str]] = {
        # cuda_ct2 needs faster-whisper + ctranslate2 + the NVIDIA CUDA wheels.
        # We only check faster_whisper since it transitively pulls ctranslate2,
        # and the nvidia.* DLL search-path bootstrap in slumbr/__init__.py
        # makes the wheels effectively optional at import time (only failing
        # at first transcribe).
        "cuda_ct2": ["faster_whisper"],
        # moonshine uses sherpa-onnx which is always in base deps.
        "moonshine": ["sherpa_onnx"],
        # directml needs onnxruntime-directml + optimum.
        "directml": ["onnxruntime", "optimum"],
        "whispercpp_sycl": ["pywhispercpp"],
        "whispercpp_cpu": ["pywhispercpp"],
    }
    for mod in probes.get(backend_name, []):
        if importlib.util.find_spec(mod) is None:
            return False
    return True


def _mono_font() -> QFont:
    """Monospace font for the pip output log. Consolas is the
    Windows-default and looks right inside Slumbr's violet/dark theme.
    """
    f = QFont("Consolas")
    f.setStyleHint(QFont.Monospace)
    f.setPointSize(9)
    return f
