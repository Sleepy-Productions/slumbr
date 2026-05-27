"""CPU-fallback on engine-load failure — slumbr/ui/preparing.py.

The portability guarantee for "works on other people's PCs": if the configured
GPU backend fails to build/warm (bad driver, missing wheels in the wrong frozen
build, ONNX export failure, OOM), Slumbr must fall back to the always-bundled
Moonshine CPU engine instead of hard-exiting. These tests drive _EngineWorker
with a stubbed builder so no real models load.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import slumbr.ui.preparing as prep
from slumbr.config import BackendConfig, SlumbrConfig

_app = QApplication.instance() or QApplication([])


class _FakeTranscriber:
    def warm_up(self) -> None:
        pass


def test_falls_back_to_moonshine_when_primary_fails(monkeypatch):
    cfg = SlumbrConfig()
    cfg.backend = BackendConfig(name="cuda_ct2", model="large-v3-turbo")
    built: list[str] = []

    def fake_build(backend, **_kw):
        built.append(backend.name)
        if backend.name == "cuda_ct2":
            raise RuntimeError("CUDA boom")
        return _FakeTranscriber()

    monkeypatch.setattr(prep, "build_transcriber", fake_build)
    monkeypatch.setattr(prep, "StreamingASREngine", lambda **_kw: object())

    got: dict = {}
    w = prep._EngineWorker(cfg)
    w.ready.connect(lambda _t, _s: got.update(ready=True))
    w.failed.connect(lambda m: got.update(failed=m))
    w.fell_back.connect(lambda fb, reason: got.update(fb=fb, reason=reason))
    w.run()

    assert got.get("ready") is True
    assert "failed" not in got
    assert got["fb"].name == "moonshine"          # fell back to the bundled CPU engine
    assert built == ["cuda_ct2", "moonshine"]      # tried primary, then fallback


def test_no_fallback_loop_when_primary_is_already_moonshine(monkeypatch):
    cfg = SlumbrConfig()
    cfg.backend = BackendConfig(name="moonshine", model="moonshine-base-en-int8")

    def fake_build(backend, **_kw):
        raise RuntimeError("moonshine boom")

    monkeypatch.setattr(prep, "build_transcriber", fake_build)

    got: dict = {}
    w = prep._EngineWorker(cfg)
    w.ready.connect(lambda _t, _s: got.update(ready=True))
    w.failed.connect(lambda m: got.update(failed=m))
    w.fell_back.connect(lambda fb, _r: got.update(fb=fb))
    w.run()

    assert "moonshine boom" in got.get("failed", "")
    assert "ready" not in got and "fb" not in got   # no infinite fallback


def test_cpu_fallback_backend_is_moonshine():
    fb = prep._cpu_fallback_backend()
    assert fb.name == "moonshine"
    assert fb.model == "moonshine-base-en-int8"


def test_primary_model_bundled_is_model_specific(monkeypatch, tmp_path):
    """The first-run dialog's "no download" line must be per-model: on the NVIDIA
    build the Moonshine trio is bundled but the GPU Whisper model is NOT, so a
    cuda_ct2 first run still downloads and must be reported as such."""
    models = tmp_path / "models"
    (models / "moonshine-base-en").mkdir(parents=True)
    (models / "moonshine-base-en" / "tokens.txt").write_text("x")
    monkeypatch.setattr(prep, "bundled_models_root", lambda: models)

    moon = SlumbrConfig()
    moon.backend = BackendConfig(name="moonshine", model="moonshine-base-en-int8")
    assert prep._primary_model_bundled(moon) is True   # trio bundled

    cuda = SlumbrConfig()
    cuda.backend = BackendConfig(name="cuda_ct2", model="large-v3-turbo")
    assert prep._primary_model_bundled(cuda) is False   # GPU model not bundled → still downloads


def test_primary_model_bundled_false_when_not_frozen(monkeypatch):
    monkeypatch.setattr(prep, "bundled_models_root", lambda: None)
    cfg = SlumbrConfig()
    cfg.backend = BackendConfig(name="moonshine", model="moonshine-base-en-int8")
    assert prep._primary_model_bundled(cfg) is False
