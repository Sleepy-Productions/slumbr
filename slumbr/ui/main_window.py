"""Slumbr main window — the full-app control hub.

A sidebar-nav layout: left rail with sections (Home, Shortcuts, Voice,
Behavior, About), right content area is a QStackedWidget swapping the
panel that corresponds to the selected nav item.

The hub is the persistent control surface. The popup is for the
moment-of-dictation; the tray is for "Slumbr is running, click to act."
The hub is what the user opens to *manage* Slumbr.

Close behavior
--------------
Slumbr is designed to live in the tray. Closing the main window does NOT
exit the app by default — it minimizes to tray. The first time the user
closes the window we show a one-time dialog so they can pick the policy
explicitly (close to tray vs. quit on close); the choice is persisted to
`SlumbrConfig.close_to_tray` and the dialog never shows again.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QCloseEvent, QFont, QIcon
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..config import SlumbrConfig
from ..input.keymap import vk_label
from ..state import State
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
from .hub_panels import (
    AboutPanel,
    BehaviorPanel,
    HomePanel,
    ShortcutsPanel,
    VoicePanel,
)

# Index of each nav entry — matches insertion order in `_build_sidebar`.
NAV_HOME = 0
NAV_SHORTCUTS = 1
NAV_VOICE = 2
NAV_BEHAVIOR = 3
NAV_ABOUT = 4


def _root_qss() -> str:
    """Single source of QSS for the hub. Driven by theme.py constants."""
    return f"""
    QMainWindow {{ background-color: {BG_DARK}; }}
    QWidget {{ color: {TEXT_PRIMARY}; font-family: "Segoe UI"; }}
    QLabel {{ color: {TEXT_PRIMARY}; }}

    /* ---- Sidebar ---- */
    QFrame#sidebar {{
        background-color: {BG_PANEL};
        border: none;
        border-right: 1px solid {BORDER};
    }}
    QFrame#sidebar-divider {{
        background-color: {BORDER};
        max-height: 1px;
        min-height: 1px;
        border: none;
    }}
    QListWidget#nav {{
        background-color: transparent;
        border: none;
        padding: 14px 10px;
        outline: 0;
    }}
    QListWidget#nav::item {{
        color: {TEXT_SECONDARY};
        padding: 12px 16px 12px 22px;
        border-radius: 10px;
        margin: 4px 4px;
        border-left: 3px solid transparent;
    }}
    QListWidget#nav::item:hover {{
        background-color: {BG_PANEL_HI};
        color: {TEXT_PRIMARY};
    }}
    QListWidget#nav::item:selected {{
        background-color: rgba(155, 111, 224, 40);
        color: {TEXT_PRIMARY};
        font-weight: 700;
        border-left: 3px solid {VIOLET_PRIMARY};
    }}

    /* ---- Cards ---- */
    QFrame#card {{
        background-color: {BG_PANEL};
        border: 1px solid {BORDER};
        border-radius: 14px;
    }}
    QFrame#hero-card {{
        background-color: {BG_PANEL};
        border: 1px solid {VIOLET_DEEP};
        border-radius: 16px;
    }}

    /* ---- Buttons ---- */
    QPushButton {{
        background-color: {BG_PANEL_HI};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER};
        border-radius: 10px;
        padding: 9px 18px;
        font-weight: 500;
    }}
    QPushButton:hover {{ border: 1px solid {VIOLET_PRIMARY}; }}
    QPushButton:pressed {{
        background-color: {VIOLET_DEEP};
        border: 1px solid {VIOLET_DEEP};
    }}
    QPushButton:focus {{ border: 1px solid {VIOLET_PRIMARY}; outline: none; }}
    QPushButton#primary {{
        background-color: {VIOLET_PRIMARY};
        border: 1px solid {VIOLET_PRIMARY};
        color: {TEXT_PRIMARY};
        font-weight: 700;
        padding: 11px 22px;
    }}
    QPushButton#primary:hover {{
        background-color: {VIOLET_PRIMARY_HOVER};
        border: 1px solid {VIOLET_PRIMARY_HOVER};
    }}
    QPushButton#primary:pressed {{
        background-color: {VIOLET_DEEP};
        border: 1px solid {VIOLET_DEEP};
    }}
    QPushButton#destructive {{
        background-color: {BG_PANEL_HI};
        border: 1px solid {BORDER};
        color: {TEXT_PRIMARY};
    }}
    QPushButton#destructive:hover {{
        border: 1px solid #C97A7A;
        color: #FFCDCD;
    }}

    /* ---- Form controls ---- */
    QComboBox {{
        background-color: {BG_PANEL_HI};
        border: 1px solid {BORDER};
        border-radius: 10px;
        padding: 10px 14px;
        min-height: 24px;
        selection-background-color: {VIOLET_PRIMARY};
    }}
    QComboBox:hover {{ border: 1px solid {VIOLET_PRIMARY}; }}
    QComboBox:focus {{ border: 1px solid {VIOLET_PRIMARY}; }}
    QComboBox::drop-down {{
        border: none;
        padding-right: 6px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {BG_PANEL};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER};
        selection-background-color: {VIOLET_PRIMARY};
        selection-color: {TEXT_PRIMARY};
        outline: 0;
        padding: 6px;
    }}

    QCheckBox {{ color: {TEXT_PRIMARY}; spacing: 12px; padding: 6px 0; }}
    QCheckBox:focus {{ outline: none; }}
    QCheckBox::indicator {{
        width: 20px;
        height: 20px;
        border: 1px solid {BORDER};
        border-radius: 5px;
        background: {BG_PANEL_HI};
    }}
    QCheckBox::indicator:hover {{ border: 1px solid {VIOLET_PRIMARY}; }}
    QCheckBox::indicator:checked {{
        background: {VIOLET_PRIMARY};
        border: 1px solid {VIOLET_PRIMARY};
    }}

    QTextEdit, QPlainTextEdit {{
        background-color: {BG_PANEL};
        border: 1px solid {BORDER};
        border-radius: 12px;
        padding: 14px;
        color: {TEXT_PRIMARY};
        selection-background-color: {VIOLET_PRIMARY};
    }}
    QTextEdit:focus, QPlainTextEdit:focus {{ border: 1px solid {VIOLET_PRIMARY}; }}

    /* ---- Hotkey pill ---- */
    QLabel#hotkey-pill {{
        background-color: rgba(155, 111, 224, 40);
        color: {VIOLET_PRIMARY};
        border: 1px solid {VIOLET_DEEP};
        border-radius: 10px;
        padding: 6px 14px;
        font-weight: 700;
    }}

    /* ---- Scrollbars ---- */
    QScrollBar:vertical {{
        background: transparent;
        width: 12px;
        margin: 6px 2px 6px 0;
    }}
    QScrollBar::handle:vertical {{
        background: {BG_PANEL_HI};
        border-radius: 5px;
        min-height: 40px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {VIOLET_DEEP}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; background: transparent; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    """


class MainWindow(QMainWindow):
    def __init__(
        self,
        *,
        config: SlumbrConfig,
        on_toggle: Callable[[], None],
        on_quit: Callable[[], None],
        on_config_changed: Callable[[], None],
        on_hotkey_changed: Callable[[int], None],
        app_icon: QIcon | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        self._on_toggle = on_toggle
        self._on_quit = on_quit
        self._on_config_changed = on_config_changed
        self._on_hotkey_changed = on_hotkey_changed
        self._quitting = False

        self.setWindowTitle("Slumbr")
        if app_icon is not None:
            self.setWindowIcon(app_icon)
        self.setMinimumSize(1000, 640)
        self.resize(1240, 800)
        self.setStyleSheet(_root_qss())

        # ----- root layout: sidebar | content
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._sidebar = self._build_sidebar()
        root.addWidget(self._sidebar)

        # ----- content stack
        self._stack = QStackedWidget()
        root.addWidget(self._stack, stretch=1)

        # ----- panels
        self.home = HomePanel(config=config, on_toggle=on_toggle)
        self.shortcuts = ShortcutsPanel(config=config)
        self.voice = VoicePanel(config=config)
        self.behavior = BehaviorPanel(config=config)
        self.about = AboutPanel()

        self._stack.addWidget(self.home)        # NAV_HOME
        self._stack.addWidget(self.shortcuts)   # NAV_SHORTCUTS
        self._stack.addWidget(self.voice)       # NAV_VOICE
        self._stack.addWidget(self.behavior)    # NAV_BEHAVIOR
        self._stack.addWidget(self.about)       # NAV_ABOUT

        # ----- signals
        self.shortcuts.hotkey_changed.connect(self._handle_hotkey_changed)
        self.voice.config_changed.connect(self._handle_config_changed)
        self.behavior.config_changed.connect(self._handle_config_changed)
        self.about.quit_requested.connect(self._handle_quit_button)

        # Select Home by default
        self._nav.setCurrentRow(NAV_HOME)

    # ----------------------------------------------------- sidebar

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(260)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Brand block — bigger and more breathing room
        brand = QFrame()
        brand_layout = QVBoxLayout(brand)
        brand_layout.setContentsMargins(28, 30, 28, 22)
        brand_layout.setSpacing(4)
        title = QLabel("Slumbr")
        tf = QFont()
        tf.setPointSize(26)
        tf.setBold(True)
        title.setFont(tf)
        title.setStyleSheet(f"color: {VIOLET_PRIMARY}; letter-spacing: -0.5px;")
        subtitle = QLabel("Sleepy Productions")
        sf = QFont()
        sf.setPointSize(9)
        subtitle.setFont(sf)
        subtitle.setStyleSheet(f"color: {TEXT_SECONDARY}; letter-spacing: 1.5px;")
        subtitle.setText("SLEEPY PRODUCTIONS")
        brand_layout.addWidget(title)
        brand_layout.addWidget(subtitle)
        layout.addWidget(brand)

        # Divider
        divider = QFrame()
        divider.setObjectName("sidebar-divider")
        layout.addWidget(divider)

        # Nav list
        self._nav = QListWidget()
        self._nav.setObjectName("nav")
        self._nav.setIconSize(QSize(18, 18))
        for label in ("Home", "Shortcuts", "Voice", "Behavior", "About"):
            item = QListWidgetItem(label)
            item.setFont(_nav_font())
            self._nav.addItem(item)
        self._nav.currentRowChanged.connect(self._on_nav_changed)
        layout.addWidget(self._nav, stretch=1)

        # Footer with version
        footer = QLabel(f"v{__version__}")
        ff = QFont()
        ff.setPointSize(9)
        footer.setFont(ff)
        footer.setStyleSheet(f"color: {TEXT_SECONDARY}; padding: 20px 28px;")
        footer.setAlignment(Qt.AlignLeft)
        layout.addWidget(footer)

        return sidebar

    def _on_nav_changed(self, index: int) -> None:
        if index < 0:
            return
        self._stack.setCurrentIndex(index)

    # ----------------------------------------------------- external API
    def set_state(self, state: State) -> None:
        self.home.set_state(state)

    def set_last_transcript(self, text: str) -> None:
        self.home.set_last_transcript(text)

    def reflect_hotkey(self, vk: int) -> None:
        """Sync the picker + status caption to a hotkey changed elsewhere."""
        self.shortcuts.set_hotkey(vk)
        self.home.set_hotkey_label(vk_label(vk))

    def jump_to_voice(self) -> None:
        """Open the Voice panel (used by tray menu's "Settings…" item)."""
        self._nav.setCurrentRow(NAV_VOICE)

    # --------------------------------------------------- signal handlers
    def _handle_hotkey_changed(self, vk: int) -> None:
        self._config.hotkey_vk = vk
        self.home.set_hotkey_label(vk_label(vk))
        self._on_hotkey_changed(vk)
        self._on_config_changed()

    def _handle_config_changed(self) -> None:
        self._on_config_changed()

    def _handle_quit_button(self) -> None:
        self._quitting = True
        self._on_quit()

    # ----------------------------------------------------- close event
    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._quitting:
            event.accept()
            return

        if not self._config.close_choice_made:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Question)
            box.setWindowTitle("Close Slumbr")
            box.setText("When you close this window, what should Slumbr do?")
            box.setInformativeText(
                "You can change this later from the Behavior tab."
            )
            tray_btn = box.addButton("Keep running in tray", QMessageBox.AcceptRole)
            quit_btn = box.addButton("Quit Slumbr", QMessageBox.DestructiveRole)
            box.addButton("Cancel", QMessageBox.RejectRole)
            box.setDefaultButton(tray_btn)
            box.exec()
            clicked = box.clickedButton()
            if clicked is tray_btn:
                self._config.close_to_tray = True
                self._config.close_choice_made = True
                self._on_config_changed()
                event.ignore()
                self.hide()
                return
            if clicked is quit_btn:
                self._config.close_to_tray = False
                self._config.close_choice_made = True
                self._on_config_changed()
                self._quitting = True
                event.accept()
                self._on_quit()
                return
            event.ignore()
            return

        if self._config.close_to_tray:
            event.ignore()
            self.hide()
            return

        self._quitting = True
        event.accept()
        self._on_quit()


def _nav_font() -> QFont:
    f = QFont()
    f.setPointSize(10)
    return f
