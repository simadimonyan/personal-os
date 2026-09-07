#!/usr/bin/env python3
"""Конвейер update-memory целиком, без человека: L0 → L1→L3 (+ADR) → L4.

Раньше это была команда `/update-memory`, которую надо было вспомнить и
запустить руками. Здесь тот же протокол разложен на два слоя:

  детерминированный (этот скрипт) — экспорт L0, индексация, граф, поиск
      новых кросс-секционных связей, запись файла-дня лога L4;
  агентский (безголовый `claude --agent memory-extractor`) — то, что
      требует чтения смысла: факты L1, сцены L2, персона и идиолект L3,
      журнал архитектурных решений.

Шаг 5 протокола memory-extractor (граф) агенту не отдаётся: он и так
делается здесь, детерминированно и дешевле. Агенту остаются L1–L4c.

Запуск: из SessionEnd-хука отсоединённым процессом (конвейер живёт минуты,
а хуку CLI даёт максимум 60 с) либо руками:

    python3 memory_update.py --force      # игнорируя интервал
    python3 memory_update.py --status     # что было в прошлый раз
    python3 memory_update.py --dry        # что бы сделал, без запуска

Грабли, заложенные в конструкцию:
- Рекурсия. Безголовый `claude` поднимается в этом же проекте, значит с
  теми же хуками: его SessionEnd экспортировал бы служебный прогон в L0 и
  запускал новый конвейер. Маркер POS_MEMORY_CHILD рвёт цикл.
- Двойная обработка. У memory-extractor нет своего журнала обработанного,
  он берёт «свежие» сессии по дате. При автозапуске это значит жевать одно
  и то же по десять раз. Здесь список новых файлов L0 считается по mtime
  против state-файла и передаётся агенту явным перечнем.
- «Новые» связи. `brainstorm` на всей базе каждый раз выдаёт одни и те же
  верхние пары. Новизна считается против журнала уже записанных пар.
- Параллельные прогоны. Два быстрых /clear подряд — два конвейера на одной
  sqlite и одних и тех же файлах. Лок-файл с проверкой живости pid.
"""

import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HOOKS = Path(__file__).resolve().parent
ROOT = HOOKS.parent.parent
VAULT = Path("/Users/dimitrisimonyan/Yandex.Disk.localized/"
             "Self-Education/Knowledge base/Obsidian/Органон")
VSEARCH = ROOT / "tools" / "obsidian" / "tools" / "obsidian_vsearch.py"
QUEUE = ROOT / "tools" / "obsidian" / "tools" / ".reindex-queue"
EXPORTER = Path.home() / ".claude" / "skills" / "sync-claude-sessions" / "scripts" / "claude-sessions"

CONFIG_FILE = HOOKS / "memory_update.json"
STATE_FILE = HOOKS / ".memory-state.json"
STATUS_FILE = HOOKS / "memory_status.json"
LOG_FILE = HOOKS / ".memory-update.log"
LOCK_FILE = HOOKS / ".memory-update.lock"

L0_DIRS = ["10 — Claude/Контекст и Сессии", "10 — Claude/Аватар"]
L4_LOG_DIR = "10 — Claude/Memory/L4 Semantic Graph/Log"
L4_INDEX = "10 — Claude/Memory/L4 Semantic Graph/Semantic Graph Log.md"

DEFAULTS = {
    "enabled": True,
    # Два /clear подряд не должны запускать конвейер дважды: сессии никуда
    # не денутся, их подберёт следующий прогон.
    "min_interval_min": 20,
    # Сессия короче — это «открыл и закрыл», памяти там нет.
    "min_session_bytes": 3000,
    "model": "sonnet",
    "agent_timeout_min": 30,
    # Экспорт упомянутых в сессии Telegram-диалогов (Шаг 0.5 протокола).
    "telegram_export": True,
    "telegram_max_chats": 2,
    "graph_threshold": 0.55,
    "graph_k": 8,
    "brainstorm_threshold": 0.72,
    "brainstorm_significant": 0.75,
    "brainstorm_limit": 40,
    # Сколько уже записанных пар помнить, чтобы не логировать их повторно.
    "seen_pairs_keep": 800,
    "log_keep": 300,
    # Потолок на прогон: остаток подберёт следующий. Иначе первый же запуск
    # после долгого перерыва отдаёт агенту сотню сессий разом.
    "max_sessions_per_run": 12,
    # Если журнала обработанного ещё нет, считаем обработанным всё, что
    # старше последнего файла-дня L1 (память до этой даты уже собрана
    # ручными прогонами). Нет и его — отступаем на столько дней.
    "bootstrap_fallback_days": 14,
}

L1_DIR = "10 — Claude/Memory/L1 Atomic Memory"


def cfg() -> dict:
    out = dict(DEFAULTS)
    try:
        out.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass
    return out


def load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def log(line: str, keep: int = 300) -> None:
    stamp = f"{datetime.now():%Y-%m-%d %H:%M:%S}\t{line}"
    try:
        old = LOG_FILE.read_text(encoding="utf-8").splitlines() if LOG_FILE.exists() else []
        LOG_FILE.write_text("\n".join((old + [stamp])[-keep:]) + "\n", encoding="utf-8")
    except Exception:
        pass


# --- лок ---

def acquire_lock() -> bool:
    """Один конвейер за раз. Мёртвый лок (процесс умер) снимается сам."""
    if LOCK_FILE.exists():
        info = load(LOCK_FILE, {})
        pid, started = info.get("pid"), info.get("started", 0)
        alive = False
        if isinstance(pid, int):
            try:
                os.kill(pid, 0)
                alive = True
            except OSError:
                alive = False
        # Даже живой процесс не должен держать лок вечно: если он завис на
        # сетевом шаге, через полтора часа лок отбирается.
        if alive and time.time() - started < 90 * 60:
            return False
        LOCK_FILE.unlink(missing_ok=True)
    save(LOCK_FILE, {"pid": os.getpid(), "started": time.time()})
    return True


def release_lock() -> None:
    LOCK_FILE.unlink(missing_ok=True)


# --- шаги ---

def run(cmd: list[str], timeout: int, cwd: Path | None = None,
        env: dict | None = None) -> tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env,
                           capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout or "", r.stderr or ""
    except subprocess.TimeoutExpired:
        return -9, "", f"таймаут {timeout} с"
    except Exception as e:
        return -1, "", f"{type(e).__name__}: {e}"


def last_json(text: str):
    """Последний JSON-объект в выводе.

    Инструменты печатают прогресс до результата (vsearch — строку
    `{"status":"indexing"}`, onnxruntime — предупреждения), поэтому берём
    не первую строку, а последнее, что разбирается как JSON.
    """
    dec = json.JSONDecoder()
    best, i = None, 0
    while True:
        m = re.search(r"[{\[]", text[i:])
        if not m:
            return best
        start = i + m.start()
        try:
            obj, end = dec.raw_decode(text[start:])
            best, i = obj, start + end
        except ValueError:
            i = start + 1


def export_l0() -> str:
    """Шаг 0 — сессии сегодняшнего дня в L0.

    Строго с cwd=VAULT: экспортёр разрешает путь «10 — Claude/…» от текущего
    каталога и из папки проекта пишет мимо vault.
    """
    if not EXPORTER.is_file():
        return "экспортёр не найден"
    env = dict(os.environ, VAULT_DIR=str(VAULT))
    rc, _, err = run([sys.executable, str(EXPORTER), "-q", "export", "--today"],
                     timeout=120, cwd=VAULT, env=env)
    return "ок" if rc == 0 else f"rc={rc} {err.strip()[:120]}"


def bootstrap_cutoff(c: dict) -> float:
    """Дата, до которой память уже собрана ручными прогонами /update-memory."""
    days = [f.stem for f in (VAULT / L1_DIR).glob("????-??-??.md")]
    if days:
        try:
            return datetime.strptime(max(days), "%Y-%m-%d").timestamp()
        except ValueError:
            pass
    return time.time() - c["bootstrap_fallback_days"] * 86400


def new_l0_sessions(state: dict, min_bytes: int) -> list[Path]:
    """Файлы L0, которых не было при прошлом прогоне или которые подросли."""
    seen = state.get("processed", {})
    out = []
    for rel in L0_DIRS:
        base = VAULT / rel
        if not base.is_dir():
            continue
        for f in base.rglob("*.md"):
            try:
                st = f.stat()
            except OSError:
                continue
            if st.st_size < min_bytes:
                continue
            key = str(f.relative_to(VAULT))
            was = seen.get(key)
            if was is None or st.st_mtime > was.get("mtime", 0) + 1 or st.st_size != was.get("size"):
                out.append(f)
    out.sort(key=lambda f: f.stat().st_mtime)
    return out


def index() -> dict:
    rc, out, err = run([sys.executable, str(VSEARCH), "index"], timeout=3600)
    data = last_json(out) or {}
    if rc != 0:
        data = {"status": "error", "rc": rc, "err": err.strip()[-200:]}
    return data


AGENT_PROMPT = """Ты запущен автоматически после завершения сессии — конвейером памяти Personal OS (хук memory_update.py). Пользователя рядом нет, вопросов задавать некому: делай работу и верни отчёт.

Обработай эти новые сессии L0 (пути от корня vault):
{sessions}

Выполни свой протокол — шаги 1–4c:
- L1: атомарные факты (включая [INSIGHT]/[BEHAVIOR]/[VOICE]) → файл-день L1 Atomic Memory;
- L2: сцены → файлы-дни + ссылка в тонкое ядро;
- L3: персона (точечно) и файл-день идиолекта — включая рубрику «Как общается с людьми» ([SOCIAL]) по Telegram-экспортам, если они были;
- 4c: состоявшиеся решения по коду/архитектуре/инцидентам → Журнал архитектурных решений.

Шаг 5 (индексация, граф, brainstorm, лог L4) НЕ делай — конвейер выполняет его сам после тебя, детерминированно. Не запускай obsidian_vsearch.py.

Соблюдай своё правило бюджета токенов: читать только тонкие ядра и файлы-дни за 1–2 дня, писать новыми файлами-днями. Каждой созданной заметке — тег ai-generated в tags.
{telegram}
Верни последней строкой ровно такую сводку и ничего после неё:
ИТОГ: сессий={{N}} фактов={{K}} сцен={{M}} adr={{A}} tg={{T}}"""

TELEGRAM_BLOCK = """
Шаг 0.5: если в этих сессиях явно упомянут конкретный Telegram-диалог или чат, выгрузи его целиком командой `export_chat` драйвера telegram в «11 — Архив/Телеграм чаты/{{имя чата}}» (драйвер сам пагинирует и качает медиа). Чат ищи сначала в базе фермы (`db_chats`), в живой Telegram (`get_dialogs`) — только если в базе нет. Не больше {max_chats} чатов за прогон, только явно названные. Если чат по имени не находится — пропусти и напиши об этом в отчёте, не угадывай.

Каждый выгруженный экспорт разбери как источник тега [SOCIAL] — как Димитри общается с этим человеком. Смотри только его собственные сообщения (out: true), реплики собеседника бери как контекст. Снимай: с кем и какие отношения (близкий/знакомый/деловой/продавец), регистр и дистанцию, кто инициирует и кто закрывает разговор, чем открывает и заканчивает, как просит, отказывает, извиняется, благодарит, спорит, как реагирует на просьбу и на давление, сколько о себе открывает, подстраивается ли под манеру собеседника. Пиши это рубрикой «Как общается с людьми» в файл-день идиолекта (подрубрика на человека, каждый пункт с дословной цитатой), накопитель — «L3 Persona/Идиолект/Идиолект — как общается с людьми.md» (создай, если его ещё нет, и поставь ссылку в ядро «Идиолект и повадки.md»). В память идут паттерны общения Димитри с цитатой-примером, а не пересказ чужой личной жизни и не то, что собеседник доверил лично.
"""


def run_agent(sessions: list[Path], c: dict) -> dict:
    rel = [str(p.relative_to(VAULT)) for p in sessions]
    tg = (TELEGRAM_BLOCK.format(max_chats=c["telegram_max_chats"])
          if c["telegram_export"] else "")
    prompt = AGENT_PROMPT.format(
        sessions="\n".join(f"- {r}" for r in rel), telegram=tg)
    claude = os.environ.get("POS_CLAUDE_BIN") or str(Path.home() / ".local" / "bin" / "claude")
    if not Path(claude).is_file():
        found = subprocess.run(["which", "claude"], capture_output=True, text=True)
        claude = found.stdout.strip() or "claude"
    env = dict(os.environ, POS_MEMORY_CHILD="1")
    # Пути к драйверам и vault агент берёт из CLAUDE.md проекта — отсюда cwd.
    rc, out, err = run(
        [claude, "-p", prompt,
         "--agent", "memory-extractor",
         "--model", c["model"],
         "--output-format", "json",
         "--dangerously-skip-permissions"],
        timeout=int(c["agent_timeout_min"] * 60), cwd=ROOT, env=env)
    res = {"rc": rc}
    data = last_json(out)
    if isinstance(data, dict):
        text = data.get("result") or ""
        res["cost_usd"] = data.get("total_cost_usd")
        res["turns"] = data.get("num_turns")
        res["duration_s"] = round((data.get("duration_ms") or 0) / 1000)
        res["is_error"] = bool(data.get("is_error"))
        m = re.search(r"ИТОГ:.*", text)
        res["summary"] = (m.group(0) if m else text.strip()[-300:]) or "(пусто)"
    else:
        res["summary"] = (err or out).strip()[-300:] or "(нет вывода)"
        res["is_error"] = rc != 0
    return res


def build_graph(c: dict) -> str:
    rc, out, err = run([sys.executable, str(VSEARCH), "build_graph",
                        str(c["graph_threshold"]), str(c["graph_k"])], timeout=1800)
    if rc != 0:
        return f"ошибка rc={rc} {err.strip()[-120:]}"
    d = last_json(out) or {}
    notes, edges = d.get("notes"), d.get("edges")
    return f"{notes} заметок / {edges} рёбер" if notes else "перестроен"


def stem(path: str) -> str:
    return Path(path).stem


def brainstorm(state: dict, c: dict) -> tuple[list[dict], str]:
    """Новые кросс-секционные связи: те, которых ещё нет в журнале пар."""
    rc, out, err = run([sys.executable, str(VSEARCH), "brainstorm", "",
                        str(c["brainstorm_limit"]), str(c["brainstorm_threshold"])],
                       timeout=1800)
    if rc != 0:
        return [], f"ошибка rc={rc} {err.strip()[-120:]}"
    d = last_json(out) or {}
    # Список, а не множество: при переполнении журнала выпадать должны самые
    # старые пары, иначе из памяти случайно вылетает недавно записанное и
    # связь уходит в лог второй раз.
    order = list(state.get("seen_pairs", []))
    seen = set(order)
    fresh = []
    for h in d.get("hidden_connections", []):
        key = "|".join(sorted([h["note1"], h["note2"]]))
        if key in seen:
            continue
        seen.add(key)
        order.append(key)
        if h["score"] >= c["brainstorm_significant"]:
            fresh.append(h)
    state["seen_pairs"] = order[-int(c["seen_pairs_keep"]):]
    return fresh, f"всего {d.get('total_found', 0)}, новых значимых {len(fresh)}"


def write_l4_log(links: list[dict], day: str) -> str:
    """Файл-день лога L4 + ссылка в тонкий индекс."""
    if not links:
        return "нет новых связей"
    d = VAULT / L4_LOG_DIR
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"Graph Log {day}.md"
    body = [f"\n## {day} (конвейер памяти)"]
    for h in links:
        body.append(f"- [[{stem(h['note1'])}]] ↔ [[{stem(h['note2'])}]] "
                    f"({h['bridge']}, {h['score']})")
    if f.exists():
        f.write_text(f.read_text(encoding="utf-8") + "\n".join(body) + "\n",
                     encoding="utf-8")
    else:
        head = ("---\ntype: L4_graph_log\ndate: " + day +
                "\ntags:\n  - ai-generated\n---\n")
        f.write_text(head + "\n".join(body) + "\n", encoding="utf-8")
    idx = VAULT / L4_INDEX
    link = f"- [[Graph Log {day}]]"
    try:
        text = idx.read_text(encoding="utf-8")
        if link not in text:
            idx.write_text(text.rstrip("\n") + f"\n{link} — {len(links)} связей (автопрогон)\n",
                           encoding="utf-8")
    except Exception:
        pass
    return f"{len(links)} связей → Graph Log {day}"


def drain_queue() -> None:
    QUEUE.unlink(missing_ok=True)


# --- конвейер ---

def pipeline(reason: str, force: bool, dry: bool) -> dict:
    c = cfg()
    started = time.time()
    day = f"{datetime.now():%Y-%m-%d}"
    state = load(STATE_FILE, {})
    steps: dict[str, str] = {}

    if not c["enabled"] and not force:
        return {"skipped": "выключен в memory_update.json"}
    ago_min = (time.time() - state.get("last_run", 0)) / 60
    if not force and ago_min < c["min_interval_min"]:
        return {"skipped": f"прошлый прогон {ago_min:.0f} мин назад "
                           f"(интервал {c['min_interval_min']} мин)"}

    steps["L0"] = export_l0()
    if "processed" not in state:
        cut = bootstrap_cutoff(c)
        state["processed"] = {
            str(p.relative_to(VAULT)): {"mtime": p.stat().st_mtime, "size": p.stat().st_size}
            for p in new_l0_sessions({}, 0) if p.stat().st_mtime < cut}
        steps["первый запуск"] = (f"{len(state['processed'])} сессий до "
                                  f"{datetime.fromtimestamp(cut):%d.%m} считаю обработанными")
        save(STATE_FILE, state)
    sessions = new_l0_sessions(state, c["min_session_bytes"])
    total_new = len(sessions)
    sessions = sessions[:int(c["max_sessions_per_run"])]
    steps["новых сессий"] = (f"{total_new}" if total_new == len(sessions)
                             else f"{total_new}, беру {len(sessions)} (остальные — следующим прогоном)")
    if dry:
        return {"dry": True, "sessions": [str(p.relative_to(VAULT)) for p in sessions],
                "steps": steps}
    if not sessions:
        # Заметки всё равно могли меняться руками — индекс подтянуть стоит,
        # но агента и граф гонять не за чем.
        idx = index()
        steps["индекс"] = json.dumps(idx, ensure_ascii=False)[:120]
        drain_queue()
        state["last_run"] = time.time()
        save(STATE_FILE, state)
        return {"ok": True, "steps": steps, "note": "новых сессий нет",
                "seconds": round(time.time() - started)}

    idx = index()
    steps["индекс до"] = f"{idx.get('status')} +{idx.get('indexed', 0)}"

    agent = run_agent(sessions, c)
    steps["memory-extractor"] = agent.get("summary", "")[:200]
    if agent.get("is_error") or agent.get("rc") != 0:
        steps["memory-extractor"] = "СБОЙ: " + steps["memory-extractor"]

    idx2 = index()
    steps["индекс после"] = f"{idx2.get('status')} +{idx2.get('indexed', 0)}"
    steps["граф"] = build_graph(c)
    links, bs = brainstorm(state, c)
    steps["связи"] = bs
    steps["лог L4"] = write_l4_log(links, day)
    drain_queue()

    # Сессии считаем обработанными только если агент отработал: иначе на
    # следующем прогоне они выпадут из списка и никогда не попадут в память.
    if not agent.get("is_error") and agent.get("rc") == 0:
        proc = state.setdefault("processed", {})
        for p in sessions:
            try:
                st = p.stat()
            except OSError:
                continue
            proc[str(p.relative_to(VAULT))] = {"mtime": st.st_mtime, "size": st.st_size}
    state["last_run"] = time.time()
    state["last_cost_usd"] = agent.get("cost_usd")
    save(STATE_FILE, state)
    return {"ok": not agent.get("is_error"), "steps": steps,
            "cost_usd": agent.get("cost_usd"),
            "seconds": round(time.time() - started)}


def main() -> None:
    args = sys.argv[1:]
    if "--status" in args:
        print(json.dumps(load(STATUS_FILE, {"нет": "прогонов не было"}),
                         ensure_ascii=False, indent=2))
        return
    reason = "manual"
    if "--reason" in args:
        i = args.index("--reason")
        reason = args[i + 1] if len(args) > i + 1 else "?"
    force, dry = "--force" in args, "--dry" in args

    if dry:
        print(json.dumps(pipeline(reason, force, True), ensure_ascii=False, indent=2))
        return
    if not acquire_lock():
        log(f"{reason}\tпропуск: конвейер уже идёт")
        return
    try:
        res = pipeline(reason, force, False)
    except Exception as e:
        res = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        release_lock()

    res["reason"] = reason
    res["finished"] = f"{datetime.now():%Y-%m-%d %H:%M:%S}"
    save(STATUS_FILE, res)
    if "skipped" in res:
        log(f"{reason}\tпропуск: {res['skipped']}", cfg()["log_keep"])
    else:
        short = " · ".join(f"{k}: {v}" for k, v in (res.get("steps") or {}).items())
        log(f"{reason}\t{'ок' if res.get('ok') else 'СБОЙ'} "
            f"[{res.get('seconds', '?')}с] {short}"[:1200], cfg()["log_keep"])
    if "--verbose" in args or "--force" in args:
        print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
