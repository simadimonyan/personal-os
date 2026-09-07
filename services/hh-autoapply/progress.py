#!/usr/bin/env python3
"""
Живой статус текущего прогона — канал между autoapply.py и ботом.

Скрипт пишет `run_state.json` на каждом шаге, бот его читает и рисует. Файл, а
не сокет: прогон запускается и кроном, и ботом, и руками из терминала — файл
одинаково виден всем троим и переживает перезапуск бота.

Признак «прогон идёт» — живой pid плюс свежая отметка времени. Одного pid мало:
номер переиспользуется системой после падения процесса.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

STATE_FILE = Path(__file__).resolve().parent / "run_state.json"
STALE_SEC = 900  # прогон без обновлений 15 мин считаем оборванным


def _write(data: dict) -> None:
    """Атомарная запись: бот может читать файл в любой момент."""
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, STATE_FILE)


def read() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


class Run:
    """Состояние одного прогона. Все методы — быстрые и молча глотают ошибки записи:
    сорванный статус-файл не должен ронять отправку откликов."""

    def __init__(self, mode: str) -> None:
        self.data = {
            "pid": os.getpid(),
            "mode": mode,                # боевой / dry / без LLM
            "started_at": time.time(),
            "updated_at": time.time(),
            "phase": "старт",
            "done": False,
            "counters": {},
            "current": "",
            "log": [],
            "result": "",
        }
        self.flush()

    def flush(self) -> None:
        self.data["updated_at"] = time.time()
        try:
            _write(self.data)
        except OSError:
            pass

    def phase(self, name: str, **counters) -> None:
        self.data["phase"] = name
        self.data["current"] = ""
        self.data["counters"].update(counters)
        self.flush()

    def count(self, **counters) -> None:
        self.data["counters"].update(counters)
        self.flush()

    def current(self, text: str) -> None:
        self.data["current"] = text[:120]
        self.flush()

    def log(self, line: str) -> None:
        self.data["log"] = (self.data["log"] + [line[:160]])[-12:]
        self.flush()

    def finish(self, result: str) -> None:
        self.data["done"] = True
        self.data["phase"] = "готово"
        self.data["current"] = ""
        self.data["result"] = result[:400]
        self.flush()

    def fail(self, error: str) -> None:
        self.data["done"] = True
        self.data["phase"] = "ошибка"
        self.data["current"] = ""
        self.data["result"] = f"❌ {error[:400]}"
        self.flush()


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)   # сигнал 0 — только проверка существования
        return True
    except (OSError, TypeError):
        return False


def status() -> dict:
    """Состояние для бота: {running, stale, ...данные прогона}."""
    data = read()
    if not data:
        return {"running": False, "empty": True}
    fresh = (time.time() - data.get("updated_at", 0)) < STALE_SEC
    running = not data.get("done") and _alive(data.get("pid", -1)) and fresh
    # прогон, оборвавшийся вместе с процессом (краш, kill, ребут)
    stale = not data.get("done") and not running
    return {**data, "running": running, "stale": stale, "empty": False}
