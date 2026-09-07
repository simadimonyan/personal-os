#!/usr/bin/env python3
"""
cronctl — управление автоматизированными задачами Personal OS.

Примеры:
  ./cronctl.py list
  ./cronctl.py add morning-brief --schedule "0 9 * * *" \
      --prompt "Составь короткий утренний фокус-лист на сегодня" --model sonnet \
      --obsidian "10 — Claude/Рабочее пространство/personal-os/Брифинги.md" \
      --desc "Утренний брифинг"
  ./cronctl.py add disk-check --schedule "@every 30m" --shell "df -h /" --file ~/pos-disk.log
  ./cronctl.py run morning-brief        # запустить сейчас, мимо расписания
  ./cronctl.py disable morning-brief
  ./cronctl.py enable  morning-brief
  ./cronctl.py rm morning-brief
  ./cronctl.py status                   # последние срабатывания
  ./cronctl.py tick                     # один проход планировщика вручную

Расписание:
  cron «мин час день месяц день_недели» — "0 9 * * *", "*/15 * * * *", "0 10 * * 1-5"
  @every <N><s|m|h|d>                   — "@every 30m", "@every 2h"
  @hourly @daily @weekly @monthly
"""
import argparse
import sys

import core
from client import SOCKET_PATH, server_alive


def _build_action(args) -> dict:
    if args.prompt and args.shell:
        sys.exit("укажи либо --prompt, либо --shell, но не оба")
    if args.prompt:
        action = {"type": "prompt", "prompt": args.prompt}
        if args.model:
            action["model"] = args.model
        return action
    if args.shell:
        return {"type": "shell", "command": args.shell}
    sys.exit("нужно действие: --prompt \"...\" или --shell \"...\"")


def _build_output(args) -> list[dict]:
    sinks: list[dict] = []
    if args.obsidian:
        sinks.append({"to": "obsidian", "path": args.obsidian})
    if args.file:
        sinks.append({"to": "file", "path": args.file})
    if args.telegram:
        sinks.append({"to": "telegram", "chat": args.telegram})
    if not sinks:
        sinks.append({"to": "log"})
    return sinks


def cmd_add(args) -> None:
    jobs = core.load_jobs()
    if core.find_job(jobs, args.id):
        sys.exit(f"задача '{args.id}' уже есть (удали через rm или выбери другой id)")
    # проверим расписание заранее
    try:
        core.is_due({"id": args.id, "schedule": args.schedule}, {}, core.dt.datetime.now())
    except Exception as e:  # noqa: BLE001
        sys.exit(f"плохое расписание: {e}")

    job = {
        "id": args.id,
        "schedule": args.schedule,
        "enabled": True,
        "description": args.desc or "",
        "action": _build_action(args),
        "output": _build_output(args),
    }
    if args.requires:
        job["requires"] = [r.strip() for r in args.requires.split(",") if r.strip()]
    if args.no_catchup:
        job["catchup"] = False
    jobs.append(job)
    core.save_jobs(jobs)
    print(f"добавлено: {args.id}  [{core.describe_schedule(args.schedule)}]")


def cmd_list(args) -> None:
    jobs = core.load_jobs()
    state = core.load_state()
    if not jobs:
        print("задач нет. добавь: ./cronctl.py add <id> --schedule ... --prompt|--shell ...")
        return
    print(f"{'ID':22} {'РАСПИСАНИЕ':18} {'ВКЛ':4} {'ПОСЛ.СТАТУС':12} ДЕЙСТВИЕ")
    print("-" * 90)
    for j in jobs:
        st = state.get(j["id"], {})
        status = st.get("last_status", "—")
        on = "да" if j.get("enabled", True) else "нет"
        act = j["action"]
        act_desc = (
            f"prompt:{act.get('model', 'haiku')}"
            if act["type"] == "prompt"
            else f"shell:{act['command'][:30]}"
        )
        print(f"{j['id']:22} {core.describe_schedule(j['schedule']):18} {on:4} {status:12} {act_desc}")


def cmd_status(args) -> None:
    state = core.load_state()
    alive = server_alive()
    print(f"интернет: {'🟢 есть' if core.internet_up() else '🔴 нет (сетевые задачи откладываются)'}")
    print(f"claude-local-api сокет: {'🟢 поднят' if alive else '🔴 не отвечает'}  ({SOCKET_PATH})")
    print(f"тик демона: {core.TICK_SECONDS}s · окно догонки: {core.CATCHUP_WINDOW_MIN}мин · jobs.json: {core.JOBS_FILE}")
    if not state:
        print("ещё ничего не запускалось.")
        return
    print(f"\n{'ID':22} {'СТАТУС':8} {'КОГДА':20} ДЕТАЛИ")
    print("-" * 80)
    for jid, st in state.items():
        last = st.get("last_run")
        when = core.dt.datetime.fromtimestamp(last).strftime("%Y-%m-%d %H:%M:%S") if last else "—"
        detail = st.get("last_error", f"{st.get('last_ms', '')}ms")
        print(f"{jid:22} {st.get('last_status', '—'):8} {when:20} {str(detail)[:40]}")


def _mutate(job_id: str, **changes) -> None:
    jobs = core.load_jobs()
    job = core.find_job(jobs, job_id)
    if not job:
        sys.exit(f"нет задачи '{job_id}'")
    job.update(changes)
    core.save_jobs(jobs)


def cmd_enable(args) -> None:
    _mutate(args.id, enabled=True)
    print(f"включено: {args.id}")


def cmd_disable(args) -> None:
    _mutate(args.id, enabled=False)
    print(f"выключено: {args.id}")


def cmd_rm(args) -> None:
    jobs = core.load_jobs()
    if not core.find_job(jobs, args.id):
        sys.exit(f"нет задачи '{args.id}'")
    core.save_jobs([j for j in jobs if j["id"] != args.id])
    print(f"удалено: {args.id}")


def cmd_run(args) -> None:
    jobs = core.load_jobs()
    job = core.find_job(jobs, args.id)
    if not job:
        sys.exit(f"нет задачи '{args.id}'")
    state = core.load_state()
    ok = core.run_job(job, state, reason="вручную")
    sys.exit(0 if ok else 1)


def cmd_tick(args) -> None:
    n = core.tick()
    print(f"тик выполнен: запущено задач — {n}")


def main() -> None:
    p = argparse.ArgumentParser(prog="cronctl", description="Задачи Personal OS по расписанию")
    sub = p.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("add", help="добавить задачу")
    pa.add_argument("id")
    pa.add_argument("--schedule", required=True, help='"0 9 * * *" | "@every 30m" | "@daily"')
    pa.add_argument("--prompt", help="промпт для claude-local-api")
    pa.add_argument("--model", help="haiku|sonnet|opus (для --prompt)")
    pa.add_argument("--shell", help="shell-команда вместо промпта")
    pa.add_argument("--obsidian", help="путь заметки в Obsidian для дозаписи результата")
    pa.add_argument("--file", help="путь файла для дозаписи результата")
    pa.add_argument("--telegram", help="@канал или chat_id — отправить результат сообщением")
    pa.add_argument("--requires", help="требуемые ресурсы через запятую: internet,claude (иначе задача откладывается, не сгорает)")
    pa.add_argument("--no-catchup", action="store_true", help="не догонять пропущенные запуски (по умолчанию догоняет)")
    pa.add_argument("--desc", help="описание")
    pa.set_defaults(func=cmd_add)

    sub.add_parser("list", help="список задач").set_defaults(func=cmd_list)
    sub.add_parser("status", help="последние срабатывания").set_defaults(func=cmd_status)
    sub.add_parser("tick", help="один проход планировщика").set_defaults(func=cmd_tick)

    for name, fn, help_ in (
        ("enable", cmd_enable, "включить"),
        ("disable", cmd_disable, "выключить"),
        ("rm", cmd_rm, "удалить"),
        ("run", cmd_run, "запустить сейчас"),
    ):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("id")
        sp.set_defaults(func=fn)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
