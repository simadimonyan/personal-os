"""Клиент к claude-local-api (Unix-сокет). Stdlib-only."""
import json
import os
import socket

SOCKET_PATH = os.environ.get("CLAUDE_API_SOCK", "/tmp/claude_api.sock")


def ask_claude(prompt: str, model: str | None = None, timeout: int = 300) -> str:
    req: dict = {"prompt": prompt}
    if model:
        req["model"] = model
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(SOCKET_PATH)
        s.sendall(json.dumps(req).encode() + b"\n")
        chunks = []
        while True:
            data = s.recv(4096)
            if not data:
                break
            chunks.append(data)
            if b"\n" in data:
                break
    raw = b"".join(chunks).decode().strip()
    if not raw:
        raise RuntimeError("пустой ответ от claude-local-api")
    resp = json.loads(raw)
    if "error" in resp:
        raise RuntimeError(f"claude error: {resp['error']}")
    return resp["result"]


def server_alive() -> bool:
    if not os.path.exists(SOCKET_PATH):
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(2)
            s.connect(SOCKET_PATH)
        return True
    except OSError:
        return False
