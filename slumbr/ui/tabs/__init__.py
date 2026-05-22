"""Settings dialog tabs, refactored from the old hub_panels.

Each tab is a self-contained QWidget that emits config_changed or
hotkey_changed signals. The settings dialog wires those to the app's
config-save + engine-rebuild paths.
"""
