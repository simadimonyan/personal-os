#!/usr/bin/env python3
"""
Демон cron-сервиса Personal OS.

Раз в TICK_SECONDS проверяет jobs.json и запускает все готовые задачи.
Состояние срабатываний — в state.json, логи — в logs/.

Запуск вручную:   python3 cron.py
Как сервис:        deploy/install.sh  (launchd, авто-старт при логине)
"""
import signal
import time

import core

_running = True


def _stop(*_):
    global _running
    _running = False


def main() -> None:
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _stop)

    core.log(f"[cron] старт демона · тик={core.TICK_SECONDS}s · задач={len(core.load_jobs())}")
    while _running:
        try:
            core.tick()
        except Exception as e:  # noqa: BLE001 — демон не должен умирать от одного тика
            core.log(f"[cron] ошибка тика: {e}")
        # короткий сон порциями, чтобы быстро реагировать на сигнал остановки
        for _ in range(core.TICK_SECONDS):
            if not _running:
                break
            time.sleep(1)

    core.log("[cron] остановлен")


if __name__ == "__main__":
    main()
