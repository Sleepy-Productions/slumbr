"""Settings dialog — replaces the deleted MainWindow hub.

Tray menu's "Settings…" pops this. Tabs are independent widgets that
emit signals back to the app via callbacks passed at construction.

Order chosen so the most-changed knobs are leftmost:
    Engine | Voice | Behavior | Shortcuts | History | About

Engine first because it's the only tab whose value invalidates the
others (changing backend reshuffles the model dropdown). About last
per OS convention.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QDialog, QTabWidget, QVBoxLayout

from ..config import SlumbrConfig
from ..theme import (
    BG_DARK,
    BG_PANEL,
    BG_PANEL_HI,
    BORDER,
    FONT_BODY,
    RADIUS_CARD,
    RADIUS_MD,
    RADIUS_PILL,
    RADIUS_XS,
    TEXT_DISABLED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    derive_accent,
    text_on,
)
from .anim import fade_window_in
from .tabs.about import AboutTab
from .tabs.advanced import AdvancedTab
from .tabs.behavior import BehaviorTab
from .tabs.customization import CustomizationTab
from .tabs.engine import EngineTab
from .tabs.history import HistoryTab
from .tabs.shortcuts import ShortcutsTab
from .tabs.voice import VoiceTab


def _dialog_qss(primary: str, hover: str, deep: str, pill_bg: str) -> str:
    """Dialog stylesheet, recolored from the user's accent. ``primary`` is the
    chosen color; ``hover``/``deep`` are derived shades; ``pill_bg`` is the
    translucent hotkey-pill fill (see theme.derive_accent)."""
    # Contrasting text for elements FILLED with the accent (primary button,
    # combo selection): a light/white accent needs dark text, not white-on-white.
    on_primary = text_on(primary)
    # Spacing on the 8pt grid; one radius scale (RADIUS_*); disabled states on
    # every interactive control; Inter as the body face (Segoe UI fallback).
    return f"""
    QDialog {{ background-color: {BG_DARK}; }}
    QWidget {{ color: {TEXT_PRIMARY}; font-family: "{FONT_BODY}", "Segoe UI"; }}
    QLabel {{ color: {TEXT_PRIMARY}; }}

    QTabWidget::pane {{
        background-color: {BG_DARK};
        border: none;
    }}
    QTabBar::tab {{
        background-color: transparent;
        color: {TEXT_SECONDARY};
        padding: 12px 20px;
        border: none;
        margin-right: 4px;
    }}
    QTabBar::tab:hover {{
        color: {TEXT_PRIMARY};
    }}
    QTabBar::tab:selected {{
        color: {TEXT_PRIMARY};
        border-bottom: 2px solid {primary};
        font-weight: 700;
    }}

    QPushButton {{
        background-color: {BG_PANEL_HI};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER};
        border-radius: {RADIUS_MD}px;
        padding: 8px 16px;
        font-weight: 500;
    }}
    QPushButton:hover {{ border: 1px solid {primary}; }}
    QPushButton:pressed {{
        background-color: {deep};
        border: 1px solid {deep};
    }}
    QPushButton:focus {{ border: 1px solid {primary}; outline: none; }}
    QPushButton:disabled {{
        background-color: {BG_PANEL};
        color: {TEXT_DISABLED};
        border: 1px solid {BORDER};
    }}
    QPushButton#primary {{
        background-color: {primary};
        color: {on_primary};
        border: 1px solid {primary};
        font-weight: 700;
        padding: 12px 20px;
    }}
    QPushButton#primary:hover {{
        background-color: {hover};
        color: {on_primary};
        border: 1px solid {hover};
    }}
    QPushButton#primary:disabled {{
        background-color: {BG_PANEL_HI};
        color: {TEXT_DISABLED};
        border: 1px solid {BORDER};
    }}
    QPushButton#destructive:hover {{
        border: 1px solid #C97A7A;
        color: #FFCDCD;
    }}

    QComboBox {{
        background-color: {BG_PANEL_HI};
        border: 1px solid {BORDER};
        border-radius: {RADIUS_MD}px;
        padding: 8px 12px;
        min-height: 24px;
        selection-background-color: {primary};
        selection-color: {on_primary};
    }}
    QComboBox:hover {{ border: 1px solid {primary}; }}
    QComboBox:focus {{ border: 1px solid {primary}; }}
    QComboBox:disabled {{ color: {TEXT_DISABLED}; border: 1px solid {BORDER}; }}
    QComboBox QAbstractItemView {{
        background-color: {BG_PANEL};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER};
        selection-background-color: {primary};
        selection-color: {on_primary};
        outline: 0;
        padding: 8px;
    }}

    QCheckBox {{ color: {TEXT_PRIMARY}; spacing: 12px; padding: 8px 0; }}
    QCheckBox:disabled {{ color: {TEXT_DISABLED}; }}
    QCheckBox::indicator {{
        width: 20px;
        height: 20px;
        border: 1px solid {BORDER};
        border-radius: {RADIUS_XS}px;
        background: {BG_PANEL_HI};
    }}
    QCheckBox::indicator:hover {{ border: 1px solid {primary}; }}
    QCheckBox::indicator:checked {{
        background: {primary};
        border: 1px solid {primary};
    }}
    QCheckBox::indicator:disabled {{ border: 1px solid {BORDER}; background: {BG_PANEL}; }}

    QTextEdit, QPlainTextEdit {{
        background-color: {BG_PANEL};
        border: 1px solid {BORDER};
        border-radius: {RADIUS_CARD}px;
        padding: 16px;
        color: {TEXT_PRIMARY};
        selection-background-color: {primary};
        selection-color: {on_primary};
    }}
    QTextEdit:focus, QPlainTextEdit:focus {{
        border: 1px solid {primary};
    }}

    QLabel#hotkey-pill {{
        background-color: {pill_bg};
        color: {primary};
        border: 1px solid {deep};
        border-radius: {RADIUS_PILL}px;
        padding: 8px 16px;
        font-weight: 700;
    }}

    QScrollBar:vertical {{
        background: transparent;
        width: 12px;
        margin: 8px 2px 8px 0;
    }}
    QScrollBar::handle:vertical {{
        background: {BG_PANEL_HI};
        border-radius: {RADIUS_XS}px;
        min-height: 40px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {deep}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; background: transparent; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    """


class SettingsDialog(QDialog):
    """Tabbed Settings dialog. Owns no business state — all reads/writes
    go through the ``SlumbrConfig`` instance passed in, and signal-back
    via the callbacks the app supplies.
    """

    def __init__(
        self,
        *,
        config: SlumbrConfig,
        on_config_changed: Callable[[], None],
        on_hotkey_changed: Callable[[list[int]], None],
        on_quit: Callable[[], None],
        on_restart: Callable[[], None],
        app_icon: QIcon | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        self._on_config_changed = on_config_changed
        self._on_hotkey_changed = on_hotkey_changed
        self._on_quit = on_quit
        self._on_restart = on_restart

        self.setWindowTitle("Slumbr — Settings")
        # No "?" help button in the title bar; it doesn't do anything.
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        if app_icon is not None:
            self.setWindowIcon(app_icon)
        self.setStyleSheet(_dialog_qss(*derive_accent(config.accent_color)))
        self.setMinimumSize(820, 620)
        self.resize(960, 720)
        # Modeless — the tray hotkey + popup keep working while the
        # dialog is open. setModal(False) is default but explicit.
        self.setModal(False)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._tabs = QTabWidget()
        outer.addWidget(self._tabs)

        # Build tab instances
        self._engine_tab = EngineTab(config)
        self._voice_tab = VoiceTab(config)
        self._behavior_tab = BehaviorTab(config)
        self._customization_tab = CustomizationTab(config)
        self._shortcuts_tab = ShortcutsTab(config)
        self._history_tab = HistoryTab()
        self._advanced_tab = AdvancedTab(config)
        self._about_tab = AboutTab(config)

        self._tabs.addTab(self._engine_tab, "Engine")
        self._tabs.addTab(self._voice_tab, "Voice")
        self._tabs.addTab(self._behavior_tab, "Behavior")
        self._tabs.addTab(self._customization_tab, "Customization")
        self._tabs.addTab(self._shortcuts_tab, "Shortcuts")
        self._tabs.addTab(self._history_tab, "History")
        self._tabs.addTab(self._advanced_tab, "Advanced")
        self._tabs.addTab(self._about_tab, "About")

        # Wire signals → app callbacks
        self._engine_tab.config_changed.connect(self._handle_config_changed)
        self._voice_tab.config_changed.connect(self._handle_config_changed)
        self._behavior_tab.config_changed.connect(self._handle_config_changed)
        self._customization_tab.config_changed.connect(self._handle_config_changed)
        self._advanced_tab.config_changed.connect(self._handle_config_changed)
        self._shortcuts_tab.hotkey_changed.connect(self._handle_hotkey_changed)
        self._about_tab.quit_requested.connect(self._handle_quit)
        self._about_tab.restart_requested.connect(self._handle_restart)
        # Refresh history each time the user opens the History tab so
        # entries dictated between opens show up without reopening the
        # dialog.
        self._tabs.currentChanged.connect(self._on_tab_changed)

        # Propagate the accent to the tab widgets that style themselves
        # inline (engine cards, key picker) — the dialog QSS above already
        # covers the shared chrome.
        self._apply_accent()

    def show(self) -> None:  # noqa: N802
        # Fade the dialog in on open — seamless, ~150ms ease-out (see ui/anim).
        self.setWindowOpacity(0.0)
        super().show()
        self._fade_anim = fade_window_in(self)

    # ----------------------------------------------------- external API

    def jump_to_engine(self) -> None:
        self._tabs.setCurrentWidget(self._engine_tab)

    def reflect_hotkey(self, vks: list[int]) -> None:
        """Sync the picker when the hotkey was changed from elsewhere
        (e.g. the wizard, future scripting hooks).
        """
        self._shortcuts_tab.set_hotkey(vks)

    # ----------------------------------------------------- handlers

    def _handle_config_changed(self) -> None:
        # Re-apply the accent so picking a color in Appearance recolors the
        # whole dialog live (cheap + idempotent when the color is unchanged).
        self._apply_accent()
        self._on_config_changed()

    def _apply_accent(self) -> None:
        primary, hover, deep, pill_bg = derive_accent(self._config.accent_color)
        self.setStyleSheet(_dialog_qss(primary, hover, deep, pill_bg))
        self._engine_tab.reflect_accent(primary)
        self._shortcuts_tab.reflect_accent(primary, deep)
        self._advanced_tab.reflect_accent(primary)
        self._customization_tab.reflect_accent(primary)
        self._about_tab.reflect_accent(primary)

    def _handle_hotkey_changed(self, vks: list[int]) -> None:
        # The app callback owns persistence + the live rebind + tray label
        # (it writes both ``hotkey_vks`` and the legacy ``hotkey_vk``). A
        # hotkey change is independent of the device/mic reconcile that
        # ``_on_config_changed`` does, so we don't fire that here.
        self._on_hotkey_changed(vks)

    def _handle_quit(self) -> None:
        self.accept()
        self._on_quit()

    def _handle_restart(self) -> None:
        # The app callback spawns a fresh Slumbr then tears this one down;
        # close the dialog first so it doesn't flash during the handoff.
        self.accept()
        self._on_restart()

    def _on_tab_changed(self, index: int) -> None:
        if self._tabs.widget(index) is self._history_tab:
            self._history_tab.refresh()
