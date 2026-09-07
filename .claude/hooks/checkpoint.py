#!/usr/bin/env python3
"""Чекпоинт: детерминированный слепок работы, переживающий сброс контекста.

Три входа:
  • хук PreCompact   — пишет чекпоинт до сжатия, чтобы сводка ничего не потеряла;
  • хук PostCompact  — дописывает сводку сжатия в журнал;
  • CLI из сессии    — `python3 .claude/hooks/checkpoint.py --note "..." --next "..."`.

Смысловую часть (что сделано, что дальше) даёт агент через --note/--next.
Механическую (ветка, диффы, тронутые файлы, коммиты) собирает скрипт сам.
"""
import argparse
import json
import re
import sys

import ctx_lib as C

MAX_KEEP = 6  # сколько чекпоинтов держать в файле


def build(note: str, nxt: str, files: list[str], tokens: int, source: str) -> str:
    branch = C.git("rev-parse", "--abbrev-ref", "HEAD") or "?"
    status = C.git("status", "--porcelain")
    changed = []
    for line in status.split("\n"):
        if not line.strip():
            continue
        # C.git() снимает пробелы по краям вывода, поэтому у первой строки
        # статус-префикс короче на пробел — режем префикс регуляркой, не срезом.
        m = re.match(r"^\s*\S{1,2}\s+(.*)$", line)
        path = m.group(1) if m else line.strip()
        if " -> " in path:          # переименования: интересует новое имя
            path = path.split(" -> ")[-1]
        changed.append(path)
    commits = C.git("log", "--oneline", "-3")
    stat = C.git("diff", "--stat", "HEAD")
    stat_tail = stat.split("\n")[-1] if stat else "нет незакоммиченных правок"

    body = [f"### {C.now()} · {source}", ""]
    if note:
        body.append(f"**Сделано:** {note}")
    if nxt:
        body.append(f"**Дальше:** {nxt}")
    if tokens:
        body.append(f"**Контекст на момент сброса:** ~{tokens // 1000}k токенов")
    body.append(f"**Ветка:** `{branch}` · {stat_tail}")
    if files:
        body.append("**Правлено в сессии:** " + ", ".join(
            f"`{f.replace(str(C.REPO) + '/', '')}`" for f in files[:12]))
    if changed:
        head = ", ".join(f"`{c}`" for c in changed[:5])
        more = f" и ещё {len(changed) - 5}" if len(changed) > 5 else ""
        body.append(f"**Не закоммичено:** {len(changed)} файлов — {head}{more}")
    if commits:
        body.append("**Последние коммиты:**")
        body += [f"- {c}" for c in commits.split("\n")]
    body.append("")
    return "\n".join(body)


def write(entry: str) -> None:
    C.state_dir()
    old = C.CHECKPOINT.read_text("utf-8") if C.CHECKPOINT.exists() else ""
    blocks = [b for b in old.split("\n### ") if b.strip()]
    # первый блок может нести заголовок файла — отбрасываем его целиком и пишем заново
    blocks = ["### " + b if not b.startswith("#") else b for b in blocks]
    blocks = [b for b in blocks if b.startswith("### ")][:MAX_KEEP - 1]
    header = (
        "# Чекпоинты Personal OS\n\n"
        "Что система делала до сброса контекста. Свежий сверху. "
        "Подмешивается в начало новой сессии хуком `session_start_status.py`.\n\n"
    )
    C.CHECKPOINT.write_text(header + entry + "\n" + "\n".join(blocks), "utf-8")


def log_feature(note: str) -> None:
    if not note:
        return
    C.state_dir()
    line = f"- {C.now()} — {note}\n"
    with open(C.FEATURE_LOG, "a", encoding="utf-8") as fh:
        fh.write(line)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--note", default="", help="что сделано (смысл, 1-3 строки)")
    ap.add_argument("--next", dest="nxt", default="", help="следующий шаг")
    ap.add_argument("--hook", default="", help="precompact | postcompact")
    args = ap.parse_args()

    # stdin читаем только в режиме хука: из сессии он не закрыт и чтение
    # заблокировало бы вызов навсегда.
    payload = {}
    if args.hook:
        try:
            payload = json.loads(sys.stdin.read() or "{}")
        except Exception:
            payload = {}

    transcript = payload.get("transcript_path", "")
    tokens, _side, files = C.read_transcript(transcript)
    source = {"precompact": "перед сжатием контекста",
              "postcompact": "после сжатия"}.get(args.hook, "чекпоинт фичи")

    if args.hook == "postcompact":
        summary = (payload.get("summary") or "").strip()
        if summary:
            C.state_dir()
            with open(C.STATE / "compact-summaries.md", "a", encoding="utf-8") as fh:
                fh.write(f"\n### {C.now()}\n{summary[:4000]}\n")
        C.out({"suppressOutput": True})
        return 0

    note = args.note
    if args.hook == "precompact" and not note:
        note = "автосохранение перед сжатием (смысловую часть агент не записал)"

    write(build(note, args.nxt, files, tokens, source))
    if args.hook != "precompact":
        log_feature(note)

    if args.hook:
        C.out({"suppressOutput": True,
               "systemMessage": "Чекпоинт записан: .claude/state/checkpoint.md"})
    else:
        print(f"Чекпоинт записан: .claude/state/checkpoint.md ({C.now()})")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # хук никогда не роняет сессию
        print(json.dumps({"suppressOutput": True,
                          "systemMessage": f"checkpoint: {e}"}, ensure_ascii=False))
        sys.exit(0)
