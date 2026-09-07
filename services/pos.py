#!/usr/bin/env python3
"""
pos — единая точка входа Personal OS.

Один процесс-супервизор поднимает и стережёт все сервисы Personal OS, перезапускает
упавшие и работает автономно в фоне, пока включён ноутбук. Ставится как ОДИН
launchd-агент — больше ничего вручную запускать не нужно.

Что под управлением (см. services.json):
  - claude-local-api  — локальный Claude через Unix-сокет (для prompt-задач/синтеза)
  - cron              — планировщик задач (а он уже сам гоняет radar ingest и пр.)
  - (опц.) tg-psych-bot и любые другие сервисы

Команды (одна точка входа):
  ./pos.py up        — установить launchd-агент и запустить ВСЁ (автономно, при логине)
  ./pos.py down      — остановить всё и убрать из автозапуска
  ./pos.py status    — что сейчас живо (супервизор + дети)
  ./pos.py restart   — перезапустить ВЕСЬ супервизор (перечитает services.json)
  ./pos.py restart <имя> — точечно перезапустить ОДИН сервис (не трогая остальные)
  ./pos.py logs [имя]— показать хвост лога супервизора или сервиса
  ./pos.py run       — внутренняя команда: цикл супервизора (её запускает launchd)
  ./pos.py run-fg    — запустить супервизор на переднем плане (для отладки)
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
SERVICES_FILE = BASE / "services.json"
STATE_FILE = BASE / "pos_state.json"
LOG_DIR = BASE / "logs"
SUP_LOG = LOG_DIR / "pos.log"

LABEL = "com.dimitri.pos"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
PYTHON = "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3"

RESTART_BACKOFF_MAX = 60  # сек

# launchd даёт урезанный PATH. Задаём полноценный, чтобы дети и внуки находили
# claude (~/.local/bin), node (homebrew), jq/git (/usr/bin), bash (/bin).
BASE_PATH = (
    "/Users/dimitrisimonyan/.local/bin:/opt/homebrew/bin:/usr/local/bin:"
    "/usr/bin:/bin:/usr/sbin:/sbin"
)


def log(msg: str) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    with SUP_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def load_services() -> list[dict]:
    data = json.loads(SERVICES_FILE.read_text(encoding="utf-8"))
    return [s for s in data.get("services", []) if s.get("enabled", True)]


def _resolve_cmd(svc: dict) -> list[str]:
    """Относительный путь к исполняемому (со слешем) разрешаем от cwd сервиса."""
    cmd = list(svc["cmd"])
    exe = cmd[0]
    if not exe.startswith("/") and "/" in exe:
        cmd[0] = str(Path(svc["cwd"]) / exe)
    return cmd


# ── Супервизор ────────────────────────────────────────────────────────────────
class Child:
    def __init__(self, svc: dict):
        self.svc = svc
        self.name = svc["name"]
        self.proc: subprocess.Popen | None = None
        self.restarts = 0
        self.started_at = 0.0
        self.backoff = 1.0
        self.next_start = 0.0  # когда можно перезапускать (после падения)
        self.last_exit: int | None = None

    def start(self) -> None:
        out = (LOG_DIR / f"{self.name}.log").open("a", encoding="utf-8")
        merged_path = BASE_PATH + ":" + os.environ.get("PATH", "")
        env = {**os.environ, "PATH": merged_path, **self.svc.get("env", {})}
        self.proc = subprocess.Popen(
            _resolve_cmd(self.svc),
            cwd=self.svc["cwd"],
            stdout=out,
            stderr=subprocess.STDOUT,
            env=env,
        )
        self.started_at = time.time()
        log(f"▶ старт {self.name} (pid {self.proc.pid})")

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        if not self.alive():
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        log(f"■ остановлен {self.name}")


def write_state(children: list[Child]) -> None:
    state = {
        "supervisor_pid": os.getpid(),
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "services": {
            c.name: {
                "pid": c.proc.pid if c.alive() else None,
                "alive": c.alive(),
                "restarts": c.restarts,
                "uptime_s": int(time.time() - c.started_at) if c.alive() else 0,
                "last_exit": c.last_exit,
            }
            for c in children
        },
    }
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def supervise() -> None:
    children = [Child(s) for s in load_services()]
    log(f"[pos] супервизор стартовал · сервисов: {len(children)}")
    for c in children:
        c.start()

    stop = {"flag": False}

    def _sig(*_):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    while not stop["flag"]:
        now = time.time()
        for c in children:
            if c.alive():
                c.backoff = 1.0  # стабильно работает — сбросить задержку
                continue
            if c.proc is not None and c.last_exit is None:
                c.last_exit = c.proc.returncode
                log(f"✗ {c.name} упал (код {c.last_exit})")
                c.next_start = now + c.backoff
            if c.svc.get("autorestart", True) and now >= c.next_start:
                c.restarts += 1
                c.backoff = min(RESTART_BACKOFF_MAX, c.backoff * 2)
                c.last_exit = None
                c.start()
        write_state(children)
        for _ in range(5):  # реагировать на сигнал быстро
            if stop["flag"]:
                break
            time.sleep(1)

    log("[pos] остановка — гашу детей")
    for c in children:
        c.stop()
    write_state(children)
    log("[pos] супервизор остановлен")


# ── Управление (одна точка входа) ─────────────────────────────────────────────
def _plist_xml() -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>            <string>{LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{PYTHON}</string>
        <string>{BASE / 'pos.py'}</string>
        <string>run</string>
    </array>
    <key>WorkingDirectory</key> <string>{BASE}</string>
    <key>RunAtLoad</key>        <true/>
    <key>KeepAlive</key>        <true/>
    <key>StandardOutPath</key>  <string>{LOG_DIR / 'pos.launchd.log'}</string>
    <key>StandardErrorPath</key><string>{LOG_DIR / 'pos.launchd.log'}</string>
    <key>ProcessType</key>      <string>Background</string>
</dict>
</plist>
"""


def cmd_up(_args=None) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["launchctl", "unload", str(PLIST)], capture_output=True)
    PLIST.write_text(_plist_xml(), encoding="utf-8")
    r = subprocess.run(["launchctl", "load", str(PLIST)], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"launchctl load: {r.stderr.strip()}")
    print(f"Personal OS запущен. Один агент {LABEL} держит всё в фоне (и при логине).")
    print("Проверка: ./pos.py status")


def cmd_down(_args=None) -> None:
    r = subprocess.run(["launchctl", "unload", str(PLIST)], capture_output=True, text=True)
    print("Personal OS остановлен." if r.returncode == 0 else f"launchctl: {r.stderr.strip()}")


def cmd_status(_args=None) -> None:
    loaded = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout
    on = LABEL in loaded
    print(f"launchd-агент {LABEL}: {'🟢 загружен' if on else '🔴 не загружен (./pos.py up)'}")
    if not STATE_FILE.exists():
        print("супервизор ещё не писал состояние.")
        return
    st = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    print(f"супервизор pid {st['supervisor_pid']} · обновлено {st['updated']}\n")
    print(f"{'СЕРВИС':22} {'СТАТУС':10} {'PID':8} {'АПТАЙМ':10} ПЕРЕЗАПУСКОВ")
    print("-" * 70)
    for name, s in st["services"].items():
        status = "🟢 работает" if s["alive"] else "🔴 нет"
        up = f"{s['uptime_s']}s" if s["alive"] else "—"
        print(f"{name:22} {status:10} {str(s['pid'] or '—'):8} {up:10} {s['restarts']}")


def cmd_restart(args=None) -> None:
    """restart           — перезапустить ВЕСЬ супервизор (перечитает services.json).
    restart <name>    — точечно перезапустить ОДИН сервис, не трогая остальные.
    """
    name = args[0] if args else None
    if name:
        cmd_restart_one(name)
        return
    cmd_down()
    time.sleep(2)
    cmd_up()


def cmd_restart_one(name: str) -> None:
    """Точечный рестарт одного supervised-сервиса.

    Супервизор (cmd `run`) держит дочерние процессы в памяти и сам перезапускает
    упавших (autorestart). Мы не можем дотянуться до его in-memory Child из другого
    процесса, поэтому делаем честно и минимально-инвазивно: находим PID нужного
    сервиса в pos_state.json и шлём ему SIGTERM. Супервизор на следующем тике
    увидит, что ребёнок умер, и поднимет ТОЛЬКО его (backoff-логика уже есть).
    Остальные 4 сервиса не затрагиваются — их PID'ы не трогаем.
    """
    if not STATE_FILE.exists():
        sys.exit("супервизор не запущен (нет pos_state.json) — нечего перезапускать")
    st = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    svc = st.get("services", {}).get(name)
    if svc is None:
        known = ", ".join(st.get("services", {}).keys())
        sys.exit(f"нет такого сервиса: {name}. известные: {known}")
    # Честность: точечный рестарт работает только если супервизор поднимет ребёнка
    # обратно (autorestart). Если у сервиса autorestart=false — отказываем, чтобы
    # не «убить и бросить».
    defs = {s["name"]: s for s in json.loads(
        SERVICES_FILE.read_text(encoding="utf-8")).get("services", [])}
    if not defs.get(name, {}).get("autorestart", True):
        sys.exit(f"сервис {name} помечен autorestart=false — точечный рестарт его не "
                 f"поднимет; используйте './pos.py restart' (весь супервизор)")
    pid = svc.get("pid")
    if not svc.get("alive") or not pid:
        sys.exit(f"сервис {name} сейчас не запущен (alive=false) — "
                 f"супервизор поднимет его сам, точечный рестарт не нужен")
    try:
        os.kill(int(pid), signal.SIGTERM)
    except ProcessLookupError:
        sys.exit(f"процесс {pid} сервиса {name} уже не существует")
    except PermissionError:
        sys.exit(f"нет прав послать сигнал pid {pid} (сервиса {name})")
    log(f"↻ точечный рестарт {name}: послан SIGTERM pid {pid}; супервизор поднимет его сам")
    print(f"OK: {name} (pid {pid}) остановлен; супервизор перезапустит его в течение ~5с")


def cmd_logs(args) -> None:
    name = args[0] if args else "pos"
    f = LOG_DIR / (f"{name}.log" if name != "pos" else "pos.log")
    if not f.exists():
        sys.exit(f"нет лога: {f}")
    subprocess.run(["tail", "-n", "40", str(f)])


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    rest = sys.argv[2:]
    if cmd == "run":
        supervise()
    elif cmd == "run-fg":
        supervise()
    elif cmd == "up":
        cmd_up()
    elif cmd == "down":
        cmd_down()
    elif cmd == "status":
        cmd_status()
    elif cmd == "restart":
        cmd_restart(rest)
    elif cmd == "logs":
        cmd_logs(rest)
    else:
        print(__doc__)
        sys.exit(0 if cmd in ("", "-h", "--help") else 1)


if __name__ == "__main__":
    main()
