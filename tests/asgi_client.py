"""Минимальный ASGI-клиент для тестов API без сетевых зависимостей."""

from __future__ import annotations

import json
from urllib.parse import urlsplit

import anyio


def call(app, method: str, url: str, body: dict | None = None):
    parts = urlsplit(url)
    payload = json.dumps(body).encode() if body is not None else b""
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "path": parts.path, "raw_path": parts.path.encode(),
        "query_string": parts.query.encode(), "root_path": "",
        "headers": [(b"content-type", b"application/json"), (b"host", b"test")],
        "client": ("127.0.0.1", 1), "server": ("test", 80),
    }
    out = {"status": None, "body": b"", "headers": {}}

    async def run():
        sent = False

        async def receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": payload, "more_body": False}
            return {"type": "http.disconnect"}

        async def send(msg):
            if msg["type"] == "http.response.start":
                out["status"] = msg["status"]
                out["headers"] = {k.decode(): v.decode() for k, v in msg["headers"]}
            elif msg["type"] == "http.response.body":
                out["body"] += msg.get("body", b"")

        await app(scope, receive, send)

    anyio.run(run)
    ctype = out["headers"].get("content-type", "")
    data = json.loads(out["body"]) if "json" in ctype else out["body"].decode()
    return out["status"], data
