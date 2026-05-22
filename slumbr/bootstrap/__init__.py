"""Runtime helpers for installing per-vendor backend wheels.

The first-launch wizard's Install screen drives ``install.InstallWorker``
to ``pip install slumbr[<vendor>]`` against the live venv, stream the
output, and force a relaunch so in-use DLLs (onnxruntime_providers_dml,
cublas, etc.) get picked up cleanly on the next process start.
"""
