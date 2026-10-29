"""A tiny OpenAI-compatible server for testing the Token Factory client.

Scripted: each /v1/chat/completions call pops the next canned response. It can
refuse `tools=` with a 400 to exercise the JSON-protocol fallback.
"""
from __future__ import annotations

import json
import socket
import threading
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class MockOpenAI:
    def __init__(self, models: list[str], script: list[dict], reject_tools: bool = False):
        self.models, self.script, self.reject_tools = models, list(script), reject_tools
        self.requests: list[dict] = []
        self.port = free_port()
        app = FastAPI()

        @app.get("/v1/models")
        def models_():
            return {"object": "list", "data": [{"id": m, "object": "model"} for m in self.models]}

        @app.post("/v1/chat/completions")
        async def chat(req: Request):
            body = await req.json()
            self.requests.append(body)
            if self.reject_tools and body.get("tools"):
                return JSONResponse({"error": {"message": "tools not supported"}}, status_code=400)
            nxt = self.script.pop(0) if self.script else {"content": "done"}
            msg = {"role": "assistant", "content": nxt.get("content")}
            if nxt.get("tool_calls"):
                msg["tool_calls"] = [{"id": f"call_{i}", "type": "function",
                                      "function": {"name": n, "arguments": json.dumps(a)}}
                                     for i, (n, a) in enumerate(nxt["tool_calls"])]
            return {"id": "x", "object": "chat.completion", "created": 0, "model": body["model"],
                    "choices": [{"index": 0, "message": msg,
                                 "finish_reason": "tool_calls" if nxt.get("tool_calls") else "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}

        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port,
                                                    log_level="error"))

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1/"

    def __enter__(self):
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                break
            time.sleep(0.05)
        return self

    def __exit__(self, *a):
        self.server.should_exit = True
        self.thread.join(timeout=5)
