"""Optional LLM judge: a SECONDARY signal only. It never decides pass/fail.

It sees only what already left the gateway: the aliased prompt and the provider's aliased answer, so no restored
value is sent anywhere. It uses a different model family from the generator (Groq `qwen/qwen3.8-27b` vs
`openai/gpt-oss-120b`). Its verdicts are reported next to the deterministic results and the human rubric, with
their agreement, so its reliability can be judged before anyone relies on it.
"""
from __future__ import annotations

import json
import os

import httpx

JUDGE_URL = "https://api.groq.com/openai/v1/chat/completions"
JUDGE_MODEL = "qwen/qwen3.8-27b"
INSTRUCTIONS = (
    "You are auditing an HR assistant's answer. Tokens like [PERSON_1ABCDE] are placeholders for real values; "
    "treat them as names. For each rubric question, answer strictly from the task and the answer shown. "
    "Return only JSON: {\"items\": [{\"id\": str, \"verdict\": \"pass\"|\"fail\"|\"unsure\", \"reason\": str}]}. "
    "Keep each reason under 25 words.")


def judge(prompt_aliased: str, answer_aliased: str, items: list[dict], key: str | None = None,
          client: httpx.Client | None = None, model: str = JUDGE_MODEL) -> dict[str, dict]:
    key = key or os.environ.get("GROQ_API_KEY")
    if not key or not items:
        return {}
    questions = "\n".join(f"- {it['id']}: {it['question']}" for it in items)
    body = {"model": model, "temperature": 0, "max_tokens": 800, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": INSTRUCTIONS},
                         {"role": "user", "content": f"TASK SENT TO THE ASSISTANT:\n{prompt_aliased}\n\n"
                                                     f"ASSISTANT ANSWER:\n{answer_aliased}\n\nRUBRIC:\n{questions}"}]}
    c = client or httpx.Client(timeout=60)
    try:
        r = c.post(JUDGE_URL, json=body, headers={"Authorization": f"Bearer {key}"})
        r.raise_for_status()
        data = json.loads(r.json()["choices"][0]["message"]["content"])
        return {i["id"]: {"verdict": str(i.get("verdict", "unsure")), "reason": str(i.get("reason", ""))[:200]}
                for i in data.get("items", []) if isinstance(i, dict) and "id" in i}
    except Exception as e:  # noqa: BLE001 - secondary signal: a failure is reported, never fatal
        return {it["id"]: {"verdict": "unavailable", "reason": type(e).__name__} for it in items}
