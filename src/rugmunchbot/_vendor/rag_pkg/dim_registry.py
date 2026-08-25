"""Dim → model identity registry (bot-side mirror of rugbrain dim_policy).

Answers 'what is this vector?' for any dim we serve — no confusion about
which space a vector belongs to.
"""

from __future__ import annotations

DIM_MODELS: dict[int, str] = {
    2560: "qwen3-embedding-4b (native, local :8004 / OpenRouter same-model)",
    2048: "nvidia/nemotron-3-embed-1b:free (OpenRouter API)",
    1024: "qwen3-embedding-0.6b (fast lane :8007) or qwen3-4b MRL-truncated legacy",
}


def model_for_dim(dim: int) -> str:
    return DIM_MODELS.get(dim, f"unknown-space-{dim}")
