"""Bundled-model resolution — slumbr/_bundled.py + the loader fast-paths.

A frozen build ships model weights under ``<_MEIPASS>/models`` so first run is
fully offline. These tests verify the resolver and the Whisper fast-path pick
the bundled copy when present, and fall through to the normal download path
otherwise (so source / dev runs are unchanged).
"""

import slumbr._bundled as bundled
import slumbr.stt.engine as engine


def test_bundled_root_none_when_not_frozen(monkeypatch):
    monkeypatch.delattr(bundled.sys, "_MEIPASS", raising=False)
    assert bundled.bundled_models_root() is None


def test_bundled_root_none_when_models_dir_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(bundled.sys, "_MEIPASS", str(tmp_path), raising=False)
    assert bundled.bundled_models_root() is None  # no models/ subdir


def test_bundled_root_found(monkeypatch, tmp_path):
    (tmp_path / "models").mkdir()
    monkeypatch.setattr(bundled.sys, "_MEIPASS", str(tmp_path), raising=False)
    assert bundled.bundled_models_root() == tmp_path / "models"


def test_resolve_model_prefers_bundled_dir(monkeypatch, tmp_path):
    models = tmp_path / "models"
    wdir = models / "whisper-large-v3-turbo"
    wdir.mkdir(parents=True)
    (wdir / "model.bin").write_bytes(b"\x00")  # presence marker only
    monkeypatch.setattr(engine, "bundled_models_root", lambda: models)
    assert engine._resolve_model("large-v3-turbo") == str(wdir)


def test_resolve_model_falls_through_when_not_bundled(monkeypatch, tmp_path):
    (tmp_path / "models").mkdir()
    monkeypatch.setattr(engine, "bundled_models_root", lambda: tmp_path / "models")
    # No whisper-<size>/model.bin → returns the size name (HF download path).
    assert engine._resolve_model("large-v3-turbo") == "large-v3-turbo"


def test_resolve_model_falls_through_when_not_frozen(monkeypatch):
    monkeypatch.setattr(engine, "bundled_models_root", lambda: None)
    assert engine._resolve_model("small") == "small"
