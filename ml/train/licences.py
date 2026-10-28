"""Licence allowlist for base models and the generator LLM. `check_model` is what the notebooks' licence cell calls: it
prints the model-card licence and raises AssertionError unless both the repo id and its declared licence are allowed."""
from __future__ import annotations

from typing import Callable

ALLOWED_LICENCES = frozenset({"mit", "apache-2.0"})
ALLOWED_MODELS: dict[str, str] = {
    "FacebookAI/xlm-roberta-base": "mit",
    "google/muril-base-cased": "apache-2.0",
    "microsoft/Multilingual-MiniLM-L12-H384": "mit",
    "Qwen/Qwen2.5-7B-Instruct": "apache-2.0",
    "mistralai/Mistral-7B-Instruct-v0.3": "apache-2.0",
}
# Never acceptable, whatever a model card says (non-commercial or restrictive terms, or data we must not use).
BLOCKED_SUBSTRINGS = ("ai4privacy", "piiranha", "llama", "gemma", "qwen2.5-3b", "qwen2-3b")


def normalise(lic: str | None) -> str:
    return (lic or "").strip().lower()


def check_model(repo_id: str, fetch_licence: Callable[[str], str | None], allow_table_fallback: bool = False) -> str:
    """Return the verified licence. `fetch_licence(repo_id)` reads the Hub model card (huggingface_hub). If it fails and
    allow_table_fallback is set, the pinned table is used with a printed warning; otherwise the error propagates."""
    low = repo_id.lower()
    for bad in BLOCKED_SUBSTRINGS:
        assert bad not in low, f"{repo_id} is blocked ({bad}): non-commercial/restrictive or disallowed data"
    assert repo_id in ALLOWED_MODELS, f"{repo_id} is not in the approved model list {sorted(ALLOWED_MODELS)}"
    try:
        lic = normalise(fetch_licence(repo_id))
        source = "model card"
    except Exception as e:  # noqa: BLE001 (network/hub failure: decided by the caller's flag, never silent)
        if not allow_table_fallback:
            raise
        lic, source = ALLOWED_MODELS[repo_id], f"pinned table (hub lookup failed: {type(e).__name__})"
    print(f"{repo_id}: licence = {lic!r} (from {source})")
    assert lic in ALLOWED_LICENCES, f"{repo_id} licence {lic!r} not in allowlist {sorted(ALLOWED_LICENCES)}"
    return lic


def hub_licence(repo_id: str) -> str | None:
    from huggingface_hub import model_info
    info = model_info(repo_id)
    lic = getattr(getattr(info, "card_data", None), "license", None)
    if lic is None:
        for t in getattr(info, "tags", []) or []:
            if t.startswith("license:"):
                lic = t.split(":", 1)[1]
    return lic
