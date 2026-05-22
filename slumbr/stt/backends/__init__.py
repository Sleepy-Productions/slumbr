"""Pluggable STT backends.

Each backend lives in its own module and implements the `Transcriber`
protocol from `slumbr.stt.protocol`. Adapters are kept thin — the real
engine work happens in vendor libraries (faster-whisper, pywhispercpp,
onnxruntime-directml, sherpa-onnx).

Construction goes through `slumbr.stt.factory.build_transcriber(cfg)` —
do not import backend modules directly from `app.py`.
"""
