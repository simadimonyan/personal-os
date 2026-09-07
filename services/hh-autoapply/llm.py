#!/usr/bin/env python3
"""
Тонкий клиент к claude-local-api (unix-сокет) для hh-autoapply.

Отличие от reference-проекта (там Ollama/Llama 3): модель уже поднята сервисом
claude-local-api под супервизором pos, отдельного бэкенда не нужно.

Ключевое правило: LLM — необязательный слой. Если сокета нет, воркер упал или
ответ невалидный — вызывающий код должен продолжить работу на жёстких фильтрах
и статичном сопроводительном. Автоотклик не должен падать из-за модели.
"""
from __future__ import annotations

import json
import re
import socket

SOCKET_PATH = "/tmp/claude_api.sock"


class LLMUnavailable(RuntimeError):
    """Модель недоступна или ответила мусором — вызывающий переходит на fallback."""


def available() -> bool:
    """Быстрая проверка живости сокета — без прогона модели."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(2)
            s.connect(SOCKET_PATH)
        return True
    except OSError:
        return False


def ask(prompt: str, model: str | None = None, timeout: int = 120) -> str:
    """Один запрос к модели. Возвращает текст ответа или бросает LLMUnavailable."""
    req: dict = {"prompt": prompt}
    if model:
        req["model"] = model
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(SOCKET_PATH)
            s.sendall(json.dumps(req, ensure_ascii=False).encode() + b"\n")
            chunks = []
            while True:
                data = s.recv(8192)
                if not data:
                    break
                chunks.append(data)
                if b"\n" in data:
                    break
    except OSError as e:
        raise LLMUnavailable(f"сокет claude-local-api: {e}") from e

    raw = b"".join(chunks).decode(errors="replace").strip()
    if not raw:
        raise LLMUnavailable("пустой ответ сервера (воркер мог упасть)")
    try:
        resp = json.loads(raw)
    except json.JSONDecodeError as e:
        raise LLMUnavailable(f"невалидный JSON от сервера: {e}") from e
    if "error" in resp:
        raise LLMUnavailable(str(resp["error"])[:200])
    result = (resp.get("result") or "").strip()
    if not result:
        raise LLMUnavailable("модель вернула пустой текст")
    return result


def ask_json(prompt: str, model: str | None = None, timeout: int = 120) -> object:
    """Запрос, от которого ждём JSON. Вырезает markdown-обёртку ```json ... ```."""
    text = ask(prompt, model=model, timeout=timeout)
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE).strip()
    # модель иногда добавляет пояснение до/после — берём самый внешний JSON-блок
    start = min((i for i in (text.find("["), text.find("{")) if i != -1), default=-1)
    if start == -1:
        raise LLMUnavailable(f"в ответе нет JSON: {text[:120]}")
    end = max(text.rfind("]"), text.rfind("}"))
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise LLMUnavailable(f"JSON не разобрался: {e} — {text[:120]}") from e
