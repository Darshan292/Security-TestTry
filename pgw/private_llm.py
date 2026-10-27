"""The smallest in-boundary model endpoint for the PRIVATE (C4) route: OpenAI-compatible /v1/chat/completions.

It runs inside the trust boundary (loopback or an internal service), so it receives content the gateway cannot
protect reliably (e.g. non-Latin-script personal text) without aliasing.

Backend:
  * PGW_PRIVATE_GGUF=<path to a GGUF model> and `llama-cpp-python` installed -> a real local LLM.
  * otherwise a deterministic EXTRACTIVE STAND-IN ("extractive-standin"): it returns the leading sentences of the
    user's text. It is NOT a language model; it exists to prove routing, isolation and fail-closed behaviour in
    development. No model weights ship with this repository.

Identity (re-audit N2): GET /v1/models lists the backend, and the stand-in always reports the id
`extractive-standin` with `"pgw_backend": "standin"` (also on every chat reply). The gateway probes this at startup:
production refuses a stand-in on any contract that routes to PRIVATE; development allows it with a loud warning and
marks every in-boundary reply `X-PGW-Private-Model: standin`. The marking is fixed in code, not configuration, so a
stand-in cannot pass as a model by accident. A real in-house deployment uses an OpenAI-compatible server inside the
boundary (vLLM, Ollama, llama.cpp server) whose /v1/models lists the configured PGW_PRIVATE_MODEL (OPERATIONS.md).

    python -m uvicorn pgw.private_llm:app --host 127.0.0.1 --port 8090
"""
from __future__ import annotations

import os
import re
import time
import uuid

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .gateway.upstream import STANDIN_BACKEND, STANDIN_MODEL_ID

_SENT = re.compile(r"(?<=[.!?।])\s+")


class _Extractive:
    name = STANDIN_MODEL_ID
    backend = STANDIN_BACKEND

    def __call__(self, messages: list[dict], max_tokens: int) -> str:
        users = [m.get("content") for m in messages if m.get("role") == "user" and isinstance(m.get("content"), str)]
        text = " ".join((users[-1] if users else "").split())
        return " ".join(_SENT.split(text)[:3])[: max(64, min(max_tokens, 1024) * 4)]


class _LlamaCpp:
    backend = "llama-cpp"

    def __init__(self, path: str):
        from llama_cpp import Llama          # optional dependency, local weights only
        self.llm = Llama(model_path=path, n_ctx=4096, verbose=False)
        self.name = "llama-cpp:" + os.path.basename(path)

    def __call__(self, messages: list[dict], max_tokens: int) -> str:
        r = self.llm.create_chat_completion(messages=messages, max_tokens=max_tokens)
        return r["choices"][0]["message"]["content"]


def _backend():
    path = os.environ.get("PGW_PRIVATE_GGUF")
    return _LlamaCpp(path) if path else _Extractive()


def build_app(backend) -> Starlette:
    """The endpoint around one backend (an object with `name`, `backend` and `__call__(messages, max_tokens)`)."""
    async def chat(request: Request) -> JSONResponse:
        try:
            body = await request.json()
            messages = body["messages"]
            if not isinstance(messages, list):
                raise ValueError
        except (ValueError, KeyError, TypeError):   # never echo content
            return JSONResponse({"error": {"type": "invalid_request", "message": "invalid body"}}, status_code=400)
        text = backend(messages, int(body.get("max_tokens") or 512))
        return JSONResponse({"id": "local-" + uuid.uuid4().hex[:12], "object": "chat.completion",
                             "created": int(time.time()), "model": backend.name, "pgw_backend": backend.backend,
                             "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                                          "finish_reason": "stop"}]})

    async def models(request: Request) -> JSONResponse:
        return JSONResponse({"object": "list", "data": [{"id": backend.name, "object": "model",
                                                         "owned_by": "pgw.private_llm",
                                                         "pgw_backend": backend.backend}]})

    return Starlette(routes=[Route("/v1/chat/completions", chat, methods=["POST"]),
                             Route("/v1/models", models, methods=["GET"])])


BACKEND = _backend()
app = build_app(BACKEND)
