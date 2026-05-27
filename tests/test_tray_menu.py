"""Tray menu construction — slumbr/ui/tray.py.

Regression guard for the Mode submenu: pystray's ``_assert_action`` only accepts
actions of arity 0/1/2, so a per-item ``lambda ..., mid=m.id`` (arity 3) makes
``pystray.Menu(...)`` raise ``ValueError`` at construction — which crashed the
frozen app on startup. Building the menu here validates every item's action up
front, no system tray / display required.
"""

from slumbr.config import SlumbrConfig
from slumbr.ui.tray import SlumbrTray


def _tray(cfg: SlumbrConfig) -> SlumbrTray:
    return SlumbrTray(
        on_toggle=lambda: None,
        on_settings=lambda: None,
        on_quit=lambda: None,
        on_restart=lambda: None,
        config=cfg,
        on_quick_toggle=lambda _f: None,
        on_mode_selected=lambda _m: None,
    )


def test_build_menu_with_mode_submenu_does_not_raise():
    # Pre-fix this raised ValueError inside pystray for the 3-arg lambdas.
    menu = _tray(SlumbrConfig())._build_menu()
    assert menu is not None


def test_mode_action_and_checked_are_wired():
    cfg = SlumbrConfig()
    tray = SlumbrTray(
        on_toggle=lambda: None,
        on_settings=lambda: None,
        on_quit=lambda: None,
        on_restart=lambda: None,
        config=cfg,
        on_quick_toggle=lambda _f: None,
        on_mode_selected=lambda m: setattr(cfg, "active_mode", m),
    )
    # 2-arg action (icon, item) — pystray's accepted signature.
    tray._make_mode_action("code")(None, None)
    assert cfg.active_mode == "code"
    # 1-arg checked (item) reflects the active mode.
    assert tray._make_mode_checked("code")(None) is True
    assert tray._make_mode_checked("notes")(None) is False
