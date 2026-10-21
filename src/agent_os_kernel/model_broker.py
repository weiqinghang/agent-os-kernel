"""Model broker: real provider if key present, else deterministic stub."""
from __future__ import annotations

import os
from typing import Any


def has_llm() -> bool:
    return bool(
        os.getenv("MODEL_API_KEY")
        or os.getenv("XAI_API_KEY")
        or os.getenv("OPENAI_API_KEY")
    )


def complete(system: str, user: str, evidence_bundle: list[dict[str, Any]]) -> str:
    """Return assistant markdown. Stub still must be fed real aggregates by caller."""
    if not has_llm():
        return ""  # caller builds stub report from evidence
    # Minimal OpenAI-compatible call when key exists
    import httpx

    key = os.getenv("MODEL_API_KEY") or os.getenv("XAI_API_KEY") or os.getenv("OPENAI_API_KEY")
    base = os.getenv("MODEL_BASE_URL") or (
        "https://api.x.ai/v1" if os.getenv("XAI_API_KEY") else "https://api.openai.com/v1"
    )
    model = os.getenv("MODEL_NAME") or "grok-2-latest"
    try:
        with httpx.Client(timeout=60.0) as client:
            r = client.post(
                f"{base.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system},
                        {
                            "role": "user",
                            "content": user
                            + "\n\n证据:\n"
                            + str(evidence_bundle)[:8000],
                        },
                    ],
                    "temperature": 0.2,
                },
            )
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001
        pass
    return ""
