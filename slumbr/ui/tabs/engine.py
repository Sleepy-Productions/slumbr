"""Engine tab — hardware-aware, per-dimension tier picker.

Replaces the old three dropdowns (Backend / Model / Compute precision)
with three *independent* Recommended / Balanced / Light selectors. Each
dimension is picked on its own, so the user can mix a high model with a
light backend and a medium precision — every option shows the exact
value it maps to. A probe at build time drives the recommended tier +
the hardware summary at the top.

Model + precision tiers depend on the chosen backend, so changing the
backend rebuilds those two rows (preserving the selected tier *slot*
where the new backend offers it). Precision only applies to the CUDA
backend; the row hides itself otherwise.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ...config import BackendConfig, SlumbrConfig
from ...hardware.probe import probe
from ...hardware.recommend import (
    TIER_LABELS,
    Option,
    backend_options,
    compute_options,
    hardware_rows,
    model_options,
    thread_budget,
)
from ...theme import BG_PANEL_HI, BORDER, RADIUS_MD, TEXT_PRIMARY, TEXT_SECONDARY, VIOLET_PRIMARY
from ._widgets import field_label, heading, scrollable, subheading

log = logging.getLogger(__name__)

# Friendly display names for backend values (shown on the backend cards).
_BACKEND_LABELS: dict[str, str] = {
    "cuda_ct2": "Faster-Whisper (CUDA)",
    "cpu_ct2": "Faster-Whisper (CPU)",
    "moonshine": "Moonshine (CPU)",
    "directml": "DirectML (AMD/Intel)",
    "whispercpp_sycl": "whisper.cpp SYCL",
    "whispercpp_cpu": "whisper.cpp CPU",
}

# Friendly model names so every tier reads as a real model — not a bare
# size word. "medium" / "small" alone look like sizes, not models.
_MODEL_DISPLAY: dict[str, str] = {
    "large-v3-turbo": "Whisper large-v3-turbo",
    "large-v3": "Whisper large-v3",
    "distil-large-v3": "Whisper distil-large-v3",
    "medium": "Whisper medium",
    "small": "Whisper small",
    "moonshine-base-en-int8": "Moonshine base",
    "moonshine-tiny-en-int8": "Moonshine tiny",
    "small.en-q5_k_m": "whisper.cpp small",
}


def _model_display(value: str) -> str:
    return _MODEL_DISPLAY.get(value, value)


class _OptionCard(QFrame):
    """A clickable card: tier label (Recommended/Balanced/Light) + the exact
    value it sets + a one-line note. ``set_active`` toggles the highlight.
    """

    clicked = Signal()

    def __init__(
        self, tier_key: str, value_text: str, note: str, accent: str = VIOLET_PRIMARY
    ) -> None:
        super().__init__()
        self.setObjectName("optCard")
        self.setCursor(Qt.PointingHandCursor)
        self._active = False
        self._accent = accent

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(2)

        row = QHBoxLayout()
        row.setSpacing(6)
        chip = QLabel(TIER_LABELS.get(tier_key, tier_key.title()))
        chip.setStyleSheet(f"color: {accent}; font-weight: 700; font-size: 9pt;")
        self._check = QLabel("")
        self._check.setStyleSheet(f"color: {accent}; font-weight: 700; font-size: 9pt;")
        row.addWidget(chip)
        row.addStretch(1)
        row.addWidget(self._check)
        lay.addLayout(row)

        value = QLabel(value_text)
        vf = QFont()
        vf.setPointSize(11)
        vf.setBold(True)
        value.setFont(vf)
        value.setStyleSheet(f"color: {TEXT_PRIMARY};")
        value.setWordWrap(True)
        lay.addWidget(value)

        note_lbl = QLabel(note)
        note_lbl.setWordWrap(True)
        note_lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9pt;")
        lay.addWidget(note_lbl)

        self._apply_style()

    def _apply_style(self) -> None:
        border = self._accent if self._active else BORDER
        width = 2 if self._active else 1
        self.setStyleSheet(
            f"QFrame#optCard {{ background: {BG_PANEL_HI}; border: {width}px solid {border}; "
            f"border-radius: {RADIUS_MD}px; }}"
        )
        self._check.setText("✓" if self._active else "")

    def set_active(self, active: bool) -> None:
        if active == self._active:
            return
        self._active = active
        self._apply_style()

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001, N802
        self.clicked.emit()
        super().mouseReleaseEvent(event)


class EngineTab(QWidget):
    """Hardware summary + three independent tier rows (backend / model /
    precision). Emits ``config_changed`` whenever a pick changes.
    """

    config_changed = Signal()

    def __init__(self, config: SlumbrConfig) -> None:
        super().__init__()
        self._config = config
        self._accent = config.accent_color  # tier-card chips/checks/active border
        self._hw_chips: list[QLabel] = []   # GPU/CPU row labels, restyled on accent change
        self._notice_dot: QLabel | None = None
        if self._config.backend is None:
            self._config.backend = BackendConfig(
                name="moonshine", model="moonshine-base-en-int8", threads=4
            )

        # Probe once at build time (best-effort).
        try:
            self._profile = probe()
        except Exception as e:  # noqa: BLE001
            log.debug("hardware probe failed in EngineTab: %s", e)
            self._profile = None

        # Per-dimension card registries: list[(card, Option)].
        self._backend_cards: list[tuple[_OptionCard, Option]] = []
        self._model_cards: list[tuple[_OptionCard, Option]] = []
        self._compute_cards: list[tuple[_OptionCard, Option]] = []

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(48, 40, 48, 40)
        layout.setSpacing(20)

        layout.addWidget(heading("Engine", size=28))
        layout.addWidget(
            subheading(
                "Pick each setting independently — Recommended, Balanced, or Light. "
                "Mix and match freely (e.g. a high model with a lighter backend). "
                "Changes apply on the next restart."
            )
        )

        # ----- Hardware summary header: GPU and CPU as explicit rows so
        # both detections are unmistakable (and you can see what each does).
        if self._profile is not None:
            layout.addWidget(field_label("Detected hardware"))
            for dim, value, role in hardware_rows(self._profile):
                layout.addWidget(self._hw_row(dim, value, role))

        # ----- Backend row (static for the detected hardware)
        layout.addWidget(field_label("Backend"))
        self._backend_row = QHBoxLayout()
        self._backend_row.setSpacing(8)
        layout.addLayout(self._backend_row)

        # ----- Model row (depends on backend)
        layout.addWidget(field_label("Model"))
        self._model_row = QHBoxLayout()
        self._model_row.setSpacing(8)
        layout.addLayout(self._model_row)

        # ----- Compute precision (CUDA only; hidden otherwise)
        self._compute_section = QWidget()
        cs = QVBoxLayout(self._compute_section)
        cs.setContentsMargins(0, 0, 0, 0)
        cs.setSpacing(8)
        cs.addWidget(field_label("Compute precision"))
        self._compute_row = QHBoxLayout()
        self._compute_row.setSpacing(8)
        cs.addLayout(self._compute_row)
        layout.addWidget(self._compute_section)

        # ----- Restart notice
        notice = QFrame()
        notice_l = QHBoxLayout(notice)
        notice_l.setContentsMargins(14, 12, 14, 12)
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {self._accent}; font-size: 16px;")
        self._notice_dot = dot
        msg = QLabel(
            "Engine / model changes apply on the next Slumbr launch. "
            "Hit Restart (in the tray menu or the About tab) to apply them now."
        )
        msg.setWordWrap(True)
        msg.setStyleSheet(f"color: {TEXT_SECONDARY};")
        notice_l.addWidget(dot)
        notice_l.addWidget(msg, stretch=1)
        layout.addWidget(notice)

        layout.addStretch(1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scrollable(body))

        # Populate all three rows from the current config.
        self._populate_backend_row()
        self._rebuild_model_row()
        self._rebuild_compute_row()

    # ------------------------------------------------------- hardware rows

    def _hw_row(self, dim: str, value: str, role: str) -> QFrame:
        """One detected-device line: [GPU/CPU] · value · what it does."""
        row = QFrame()
        row.setObjectName("hwRow")
        row.setStyleSheet(
            f"QFrame#hwRow {{ background: {BG_PANEL_HI}; border: 1px solid {BORDER}; "
            f"border-radius: {RADIUS_MD}px; }}"
        )
        lay = QHBoxLayout(row)
        lay.setContentsMargins(14, 9, 14, 9)
        lay.setSpacing(12)

        chip = QLabel(dim)
        chip.setFixedWidth(38)
        chip.setStyleSheet(f"color: {self._accent}; font-weight: 700; font-size: 10pt;")
        self._hw_chips.append(chip)
        lay.addWidget(chip)

        val = QLabel(value)
        vf = QFont()
        vf.setPointSize(11)
        vf.setBold(True)
        val.setFont(vf)
        val.setStyleSheet(f"color: {TEXT_PRIMARY};")
        lay.addWidget(val)

        lay.addStretch(1)

        role_lbl = QLabel(role)
        role_lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 9pt;")
        lay.addWidget(role_lbl)
        return row

    # ------------------------------------------------------- row population

    @staticmethod
    def _clear_row(layout: QHBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def reflect_accent(self, primary: str) -> None:
        """Recolor the tier cards (chips, checks, active border), the GPU/CPU
        detected-hardware rows, and the restart-notice dot when the user picks
        a new accent."""
        self._accent = primary
        for chip in self._hw_chips:
            chip.setStyleSheet(f"color: {primary}; font-weight: 700; font-size: 10pt;")
        if self._notice_dot is not None:
            self._notice_dot.setStyleSheet(f"color: {primary}; font-size: 16px;")
        self._populate_backend_row()
        self._rebuild_model_row()
        self._rebuild_compute_row()

    def _populate_backend_row(self) -> None:
        self._backend_cards.clear()
        self._clear_row(self._backend_row)
        if self._profile is None:
            return
        cur = self._config.backend.name if self._config.backend else None
        for opt in backend_options(self._profile):
            label = _BACKEND_LABELS.get(opt.value, opt.value)
            card = _OptionCard(opt.key, label, opt.note, self._accent)
            card.set_active(opt.value == cur)
            card.clicked.connect(lambda v=opt.value: self._on_pick_backend(v))
            self._backend_cards.append((card, opt))
            self._backend_row.addWidget(card, stretch=1)

    def _rebuild_model_row(self) -> None:
        self._model_cards.clear()
        self._clear_row(self._model_row)
        if self._profile is None or self._config.backend is None:
            return
        cur = self._config.backend.model
        for opt in model_options(self._config.backend.name, self._profile):
            card = _OptionCard(opt.key, _model_display(opt.value), opt.note, self._accent)
            card.set_active(opt.value == cur)
            card.clicked.connect(lambda v=opt.value: self._on_pick_model(v))
            self._model_cards.append((card, opt))
            self._model_row.addWidget(card, stretch=1)

    def _rebuild_compute_row(self) -> None:
        self._compute_cards.clear()
        self._clear_row(self._compute_row)
        if self._config.backend is None:
            self._compute_section.setVisible(False)
            return
        opts = compute_options(self._config.backend.name)
        self._compute_section.setVisible(bool(opts))
        cur = self._config.backend.compute_type
        for opt in opts:
            card = _OptionCard(opt.key, opt.value, opt.note, self._accent)
            card.set_active(opt.value == cur)
            card.clicked.connect(lambda v=opt.value: self._on_pick_compute(v))
            self._compute_cards.append((card, opt))
            self._compute_row.addWidget(card, stretch=1)

    # ------------------------------------------------------- pick handlers

    def _selected_key(self, cards: list[tuple[_OptionCard, Option]], value: str | None) -> str | None:
        for _card, opt in cards:
            if opt.value == value:
                return opt.key
        return None

    def _on_pick_backend(self, value: str) -> None:
        cur = self._config.backend
        if cur is None or value == cur.name:
            # Re-affirm highlight even on a no-op click.
            self._refresh_highlights()
            return

        # Remember which tier slots the user had, to carry them across the
        # backend switch where the new backend offers the same slot.
        prev_model_key = self._selected_key(self._model_cards, cur.model)
        prev_compute_key = self._selected_key(self._compute_cards, cur.compute_type)

        # Resolve the new model: same tier slot if available, else recommended.
        new_models = model_options(value, self._profile) if self._profile else []
        model = self._pick_by_key(new_models, prev_model_key)

        # Resolve precision (CUDA only).
        new_compute = compute_options(value)
        compute = self._pick_by_key(new_compute, prev_compute_key) if new_compute else None

        threads = None
        if value == "moonshine" and self._profile is not None:
            threads = thread_budget(self._profile)

        self._config.backend = BackendConfig(
            name=value,
            model=model or cur.model,
            compute_type=compute,
            threads=threads if threads is not None else cur.threads,
            device_index=0,
            extra=dict(cur.extra),
        )
        self._populate_backend_row()
        self._rebuild_model_row()
        self._rebuild_compute_row()
        self.config_changed.emit()

    def _on_pick_model(self, value: str) -> None:
        if self._config.backend is None:
            return
        self._config.backend.model = value
        self._refresh_highlights()
        self.config_changed.emit()

    def _on_pick_compute(self, value: str) -> None:
        if self._config.backend is None:
            return
        self._config.backend.compute_type = value
        self._refresh_highlights()
        self.config_changed.emit()

    # ------------------------------------------------------- helpers

    @staticmethod
    def _pick_by_key(options: list[Option], key: str | None) -> str | None:
        """Value for the option matching ``key``, else the first (recommended)."""
        if not options:
            return None
        if key is not None:
            for opt in options:
                if opt.key == key:
                    return opt.value
        return options[0].value

    def _refresh_highlights(self) -> None:
        cur = self._config.backend
        if cur is None:
            return
        for card, opt in self._backend_cards:
            card.set_active(opt.value == cur.name)
        for card, opt in self._model_cards:
            card.set_active(opt.value == cur.model)
        for card, opt in self._compute_cards:
            card.set_active(opt.value == cur.compute_type)
