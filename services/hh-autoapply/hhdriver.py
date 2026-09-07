#!/usr/bin/env python3
"""
Общая обвязка вокруг hh-драйвера и конфига — используется autoapply.py,
analyzer.py и responses.py, чтобы не дублировать вызов node-драйвера.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
DRIVER = Path.home() / "Desktop" / "personal os" / "tools" / "hh" / "driver.mjs"
CONFIG = json.loads((BASE / "config.json").read_text(encoding="utf-8"))

# Транзиентные сбои: VPN моргнул, hh не отдал страницу, браузер не успел стартовать.
# Их лечит повтор; всё остальное — реальная ошибка, повторять бессмысленно.
TRANSIENT = ("timeout", "goto", "net::", "econn", "socket", "target closed", "navigation")


def driver(tool: str, args: dict | None = None, tries: int = 1, timeout: int = 180) -> object:
    """Вызвать hh-драйвер. Успех → JSON из stdout; ошибка → RuntimeError из stderr."""
    cmd = ["node", str(DRIVER), tool]
    if args is not None:
        cmd.append(json.dumps(args, ensure_ascii=False))
    # HH_WINDOW_MODE читает только старый драйвер из ~/.claude/skills/hh;
    # tools/hh/browser.js понимает единственный флаг HH_HEADLESS=1. Без него
    # окно Chrome всплывало на каждый прогон, включая кроновские по ночам.
    env = {
        **os.environ,
        "HH_WINDOW_MODE": CONFIG.get("window_mode", "background"),
        "HH_HEADLESS": "1" if CONFIG.get("headless", True) else "0",
    }
    last = "пустой ответ драйвера"
    for attempt in range(tries):
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            last = f"процесс драйвера превысил {timeout}с"
            if attempt < tries - 1:
                time.sleep(10)
                continue
            break
        out = proc.stdout.strip()
        if out:
            try:
                return json.loads(out)
            except json.JSONDecodeError:
                pass
        err = proc.stderr.strip()
        try:
            last = json.loads(err).get("error", err)
        except json.JSONDecodeError:
            last = err or "пустой ответ драйвера"
        if attempt < tries - 1 and any(w in last.lower() for w in TRANSIENT):
            time.sleep(10)  # дать сети/браузеру прийти в себя
            continue
        break
    raise RuntimeError(f"{tool}: {last[:300]}")
