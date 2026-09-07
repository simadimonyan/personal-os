#!/usr/bin/env python3
"""
Sources index — постоянный забор ссылок в единую базу источников (origin-tagged),
фоновый сервис под супервизором pos (по образцу zotero-index).

Каждые interval_sec для каждого чата из config.json запускает
``tools/sources/ingest.py ingest-tg <chat_id> --origin X --limit N`` подпроцессом
(модель эмбеддингов грузится и освобождается за цикл — демон остаётся крохотным).
add_url идемпотентен, поэтому повторный проход тянет только новые/изменённые
ссылки. В конце цикла перестраивает граф базы (раскраска по origin) и пишет
status.json — его читает mission-control.

Конфиг: services/sources-index/config.json
  interval_sec       — период цикла (сек), по умолчанию 6ч
  per_cycle_limit    — сколько последних сообщений тянуть из чата за цикл
  chats[]            — {chat_id, origin, limit, name}
"""
import os
import sys
import json
import time
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
INGEST = REPO / "tools" / "sources" / "ingest.py"
CONFIG = HERE / "config.json"
STATUS = HERE / "status.json"


def load_config() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[sources-index] config error: {e}", flush=True)
        return {"interval_sec": 6 * 3600, "chats": []}


def run_ingest(chat: dict, default_limit: int) -> str:
    """Один заход в чат: ссылки → база. Возвращает последнюю строку-итог."""
    limit = str(chat.get("limit") or default_limit)
    args = [sys.executable, str(INGEST), "ingest-tg", str(chat["chat_id"]),
            "--origin", chat.get("origin", "community"), "--limit", limit]
    r = subprocess.run(args, capture_output=True, text=True)
    tail = (r.stdout or r.stderr or "").strip().splitlines()
    return tail[-1] if tail else f"exit {r.returncode}"


def rebuild_graph() -> int:
    return subprocess.run([sys.executable, str(INGEST), "graph"]).returncode


def cycle(cfg: dict) -> dict:
    results = []
    for chat in cfg.get("chats", []):
        try:
            line = run_ingest(chat, cfg.get("per_cycle_limit", 300))
        except Exception as e:                       # один чат не должен ронять цикл
            line = f"error: {e}"
        results.append({"name": chat.get("name") or str(chat.get("chat_id")),
                        "origin": chat.get("origin", "community"), "result": line})
        print(f"[sources-index] {chat.get('name')}: {line}", flush=True)
    graph_code = rebuild_graph()
    st = {
        "updated": time.strftime("%d.%m %H:%M"),
        "updated_ts": time.time(),
        "chats": results,
        "graph_ok": graph_code == 0,
    }
    STATUS.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    return st


if __name__ == "__main__":
    cfg = load_config()
    interval = int(os.environ.get("SOURCES_REFRESH_SEC",
                                  str(cfg.get("interval_sec", 6 * 3600))))
    print(f"[sources-index] start · interval={interval}s · chats={len(cfg.get('chats', []))}",
          flush=True)
    while True:
        t = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            cfg = load_config()                      # перечитываем — правки без рестарта
            cycle(cfg)
            print(f"[sources-index] {t} cycle done", flush=True)
        except Exception as e:                       # цикл не должен умирать
            print(f"[sources-index] {t} error: {e}", flush=True)
        time.sleep(interval)
