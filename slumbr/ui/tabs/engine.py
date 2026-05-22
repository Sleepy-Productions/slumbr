"""Engine tab — backend + model + tuning.

Replaces the old Voice panel's model_size/compute_type controls with
backend-aware selection: the model dropdown filters to whatever the
currently-selected backend can actually run.

Hot-tunable knobs (language, prompt) live in the Voice tab now —
they applied regardless of backend. Anything that needs a process
relaunch (the backend itself, the model size for some backends) is
flagged inline so the user knows what'll take effect immediately vs
on next launch.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ...config import BackendConfig, SlumbrConfig
from ...theme import TEXT_SECONDARY, VIOLET_PRIMARY
from ._widgets import field_hint, field_label, heading, scrollable, subheading

# Each backend declares which models it offers + the human-readable label.
_BACKEND_MODELS: dict[str, list[tuple[str, str]]] = {
    "cuda_ct2": [
        ("large-v3", "large-v3 — most accurate (~3 GB)"),
        ("large-v3-turbo", "large-v3-turbo — faster, slightly less accurate"),
        ("distil-large-v3", "distil-large-v3 — middle ground"),
        ("medium", "medium — small VRAM"),
        ("small", "small — tightest VRAM budget"),
    ],
    "moonshine": [
        ("moonshine-base-en-int8", "Moonshine Base (~180 MB, snappy)"),
    ],
    # Phase 2 placeholders — user can pick the backend ahead of the
    # wheel install but the factory rejects until the wheels land.
    "directml": [
        ("small", "small — recommended for most AMD GPUs"),
        ("medium", "medium — RX 6800+ class"),
    ],
    "whispercpp_sycl": [
        ("small.en-q5_k_m", "small (q5_k_m) — Intel Arc / iGPU sweet spot"),
    ],
    "whispercpp_cpu": [
        ("small.en-q5_k_m", "small (q5_k_m) — CPU fallback"),
    ],
}

_BACKEND_LABELS: dict[str, str] = {
    "cuda_ct2": "Faster-Whisper (NVIDIA CUDA)",
    "moonshine": "Moonshine (CPU — snappy)",
    "directml": "DirectML (AMD/Intel; Phase 2)",
    "whispercpp_sycl": "whisper.cpp SYCL (Intel; Phase 2)",
    "whispercpp_cpu": "whisper.cpp CPU (Phase 2 fallback)",
}

_CT2_COMPUTE_TYPES = [
    ("int8_float16", "int8_float16 — best accuracy/speed"),
    ("int8", "int8 — smallest VRAM"),
    ("float16", "float16 — most accurate, more VRAM"),
]


class EngineTab(QWidget):
    """Backend dropdown, model dropdown (filtered by backend), and
    backend-specific tuning. Emits ``config_changed`` whenever anything
    here is mutated.
    """

    config_changed = Signal()

    def __init__(self, config: SlumbrConfig) -> None:
        super().__init__()
        self._config = config
        # Phase 1 — we never want config.backend to be None here. If it
        # somehow is, default to the safest combo so the dialog doesn't
        # crash.
        if self._config.backend is None:
            self._config.backend = BackendConfig(
                name="moonshine",
                model="moonshine-base-en-int8",
                threads=4,
            )

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(56, 48, 56, 48)
        layout.setSpacing(22)

        layout.addWidget(heading("Engine", size=28))
        layout.addWidget(
            subheading(
                "The speech-to-text backend Slumbr uses, and the model it loads. "
                "Changes to the backend or model take effect after a restart."
            )
        )

        # ----- Backend
        layout.addWidget(field_label("Backend"))
        self._backend_combo = QComboBox()
        for name, label in _BACKEND_LABELS.items():
            self._backend_combo.addItem(label, userData=name)
        self._select_combo_by_data(self._backend_combo, self._config.backend.name)
        self._backend_combo.currentIndexChanged.connect(self._on_backend_changed)
        layout.addWidget(self._backend_combo)
        layout.addWidget(
            field_hint(
                "Slumbr's first-launch wizard already picked the best backend for "
                "your hardware. Only change this if you know why."
            )
        )

        # ----- Model
        layout.addWidget(field_label("Model"))
        self._model_combo = QComboBox()
        self._populate_models_for(self._config.backend.name)
        self._select_combo_by_data(self._model_combo, self._config.backend.model)
        self._model_combo.currentIndexChanged.connect(self._on_changed)
        layout.addWidget(self._model_combo)

        # ----- Compute type (CT2 only — hidden otherwise)
        self._compute_label = field_label("Compute precision")
        self._compute_combo = QComboBox()
        for value, label in _CT2_COMPUTE_TYPES:
            self._compute_combo.addItem(label, userData=value)
        self._select_combo_by_data(
            self._compute_combo, self._config.backend.compute_type or "int8_float16"
        )
        self._compute_combo.currentIndexChanged.connect(self._on_changed)
        layout.addWidget(self._compute_label)
        layout.addWidget(self._compute_combo)
        self._compute_hint = field_hint(
            "INT8 is the safest default. Bump to float16 only if you have spare "
            "VRAM and want maximum decoder fidelity."
        )
        layout.addWidget(self._compute_hint)

        self._update_compute_visibility(self._config.backend.name)

        # ----- Restart notice
        notice = QFrame()
        notice_l = QHBoxLayout(notice)
        notice_l.setContentsMargins(14, 12, 14, 12)
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {VIOLET_PRIMARY}; font-size: 16px;")
        msg = QLabel(
            "Engine / model changes apply on the next Slumbr launch. "
            "Quit from the tray and re-open to switch."
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

    # ----------------------------------------------------- handlers

    def _on_backend_changed(self, *_args) -> None:
        new_name = self._backend_combo.currentData()
        if not new_name:
            return
        self._populate_models_for(new_name)
        # If the current model isn't valid for this backend, pick the
        # first option for the new one.
        if self._model_combo.findData(self._config.backend.model) < 0 and self._model_combo.count() > 0:
            self._model_combo.setCurrentIndex(0)
        self._update_compute_visibility(new_name)
        self._on_changed()

    def _on_changed(self, *_args) -> None:
        cur = self._config.backend
        if cur is None:
            return
        backend_name = self._backend_combo.currentData() or cur.name
        model = self._model_combo.currentData() or cur.model
        compute = self._compute_combo.currentData() if backend_name == "cuda_ct2" else None
        self._config.backend = BackendConfig(
            name=backend_name,
            model=model,
            compute_type=compute,
            threads=cur.threads,
            device_index=cur.device_index,
            extra=cur.extra,
        )
        self.config_changed.emit()

    # ----------------------------------------------------- helpers

    def _populate_models_for(self, backend_name: str) -> None:
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        for value, label in _BACKEND_MODELS.get(backend_name, [("small", "small")]):
            self._model_combo.addItem(label, userData=value)
        self._model_combo.blockSignals(False)

    def _update_compute_visibility(self, backend_name: str) -> None:
        show = backend_name == "cuda_ct2"
        self._compute_label.setVisible(show)
        self._compute_combo.setVisible(show)
        self._compute_hint.setVisible(show)

    @staticmethod
    def _select_combo_by_data(combo: QComboBox, value: str | None) -> None:
        if value is None:
            return
        idx = combo.findData(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)
