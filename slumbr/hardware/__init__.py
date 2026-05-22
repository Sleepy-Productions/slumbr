"""Hardware probe + backend recommendation.

`probe()` snapshots what the current Windows machine has (GPU vendor,
VRAM, CPU). `recommend()` is a pure function over that snapshot that
returns a `BackendConfig` per the decision matrix in
`references/research/slumbr-stt-backend-landscape-2026-05-22.md`.
"""
