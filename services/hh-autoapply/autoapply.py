#!/usr/bin/env python3
"""
Авто-отклик на hh.ru — для планировщика Personal OS.

Конвейер: авторизация → резюме → поиск → жёсткий фильтр по названию →
загрузка описаний → смысловой отсев моделью (0–10) → персональное письмо →
отклик. Всё, что тронули, пишется в history.db, чтобы не долбить повторно.

Смысловой слой (analyzer.py + llm.py) необязателен: если claude-local-api
недоступен или llm.enabled=false, скрипт работает как раньше — по ключевым
словам в названии и статичному письму из cover.txt.

Запуск:  python3 autoapply.py              # боевой
         python3 autoapply.py --dry        # поиск + оценка, без откликов
         python3 autoapply.py --no-llm     # принудительно без модели
         python3 autoapply.py --explain    # показать оценки и письма целиком

Вызывается кроном 3×/день. Итог печатается в stdout (его cron шлёт в отчёт).
Требует рабочей сессии hh (~/.hh-mcp/auth-state.json); если её нет —
скрипт завершится с понятным сообщением, и в отчёт придёт «нужна авторизация».
"""
from __future__ import annotations

import sys
import time

import analyzer
import history
import llm
import progress
from hhdriver import BASE, CONFIG, driver

COVER = (BASE / "cover.txt").read_text(encoding="utf-8").strip()


def pick_resume() -> str:
    if CONFIG.get("resume_id"):
        return CONFIG["resume_id"]
    resumes = driver("hh_get_resumes")
    if not resumes:
        raise RuntimeError("на аккаунте нет резюме")
    # предпочесть резюме с Java/разработчик в названии, иначе первое
    for r in resumes:
        title = (r.get("title") or "").lower()
        if any(k in title for k in ("java", "backend", "разработчик", "developer")):
            return r["id"]
    return resumes[0]["id"]


def is_relevant(v: dict) -> bool:
    """Жёсткий фильтр по названию — дешёвый первый рубеж перед загрузкой описаний."""
    title = (v.get("title") or "").lower()
    keywords = [k.lower() for k in CONFIG.get("title_keywords", [])]
    exclude = [k.lower() for k in CONFIG.get("title_exclude", [])]
    if keywords and not any(k in title for k in keywords):
        return False
    if any(x in title for x in exclude):
        return False
    return True


def collect_candidates(touched: set[str], limit: int,
                       run: progress.Run | None = None) -> tuple[list[dict], int, int]:
    """Пагинировать поиск, собрать до limit релевантных НЕ отработанных вакансий.
    Возвращает (кандидаты, всего_просмотрено, отброшено_нерелевантных)."""
    fresh: list[dict] = []
    seen: set[str] = set()
    total = irrelevant = 0
    per_page = CONFIG.get("perPage", 50)
    pages_short: list[tuple[int, int]] = []   # (номер, сколько пришло) — для отчёта о недоборе
    for page in range(1, CONFIG.get("max_pages", 6) + 1):
        if run:
            run.current(f"страница поиска {page}")
        vacs = driver("hh_search_vacancies", {
            "query": CONFIG["query"],
            "search_field": CONFIG.get("search_field"),
            "order_by": CONFIG.get("order_by"),
            "area": CONFIG.get("area", "113"),
            "experience": CONFIG.get("experience"),
            "schedule": CONFIG.get("schedule"),
            "work_format": CONFIG.get("work_format"),
            "perPage": per_page,
            "page": page,
        }, tries=3)
        if not vacs:
            break  # выдача кончилась — единственный надёжный признак конца

        new_on_page = 0
        for v in vacs:
            vid = str(v.get("id"))
            if vid in seen:
                continue
            seen.add(vid)
            new_on_page += 1
            total += 1
            if vid in touched or v.get("hasResponse"):
                continue
            if not is_relevant(v):
                irrelevant += 1
                continue
            fresh.append(v)
            if run:
                run.count(просмотрено=total, кандидатов=len(fresh), отсеяно_по_названию=irrelevant)
            if len(fresh) >= limit:
                return fresh, total, irrelevant

        # Короткую страницу НЕ считаем последней: под нагрузкой hh отдаёт меньше
        # карточек, чем просили. Раньше здесь стоял `break` — одна такая страница
        # обрывала прогон, и из шести страниц просматривалась одна.
        # Ругаемся только на «дырявые» страницы в середине: если недобор случился
        # на последней странице выдачи, это норма, а не сбой.
        pages_short.append((page, len(vacs)))
        if new_on_page == 0:
            break  # hh повторяет ту же страницу — дальше листать некуда

    # Недобор на последней странице — норма (выдача кончилась). Недобор в
    # середине означает, что часть вакансий мы не увидели.
    gaps = [(p, n) for p, n in pages_short[:-1] if n < per_page]
    if gaps:
        lost = sum(per_page - n for _, n in gaps)
        print(f"    ⚠️ страницы {', '.join(str(p) for p, _ in gaps)} пришли неполными "
              f"— hh не дорисовал примерно {lost} вакансий, они не попали в отбор")
    return fresh, total, irrelevant


def load_descriptions(vacancies: list[dict], run: progress.Run | None = None,
                      chunk: int = 8) -> int:
    """Догрузить описания вакансий. → сколько удалось.

    Пачками, а не по одной: каждый вызов драйвера поднимает отдельный процесс
    node с новым Chrome, и поштучно выходило ~35с на вакансию — 15 минут на
    прогон вместо полутора. В пачке браузер стартует один раз.
    Пачка ограничена, чтобы прогресс в боте обновлялся по ходу, а сбой одной
    пачки не уносил весь список.
    """
    ok = 0
    by_id = {str(v.get("id")): v for v in vacancies}
    ids = list(by_id)
    for start in range(0, len(ids), chunk):
        part = ids[start:start + chunk]
        if run:
            run.current(f"описания {start + 1}–{min(start + chunk, len(ids))} из {len(ids)}")
        try:
            details = driver("hh_get_vacancies", {"vacancy_ids": part},
                             tries=2, timeout=300)
        except RuntimeError:
            details = []
        for d in details or []:
            if not isinstance(d, dict) or d.get("error"):
                continue
            target = by_id.get(str(d.get("id")))
            if target is None:
                continue
            target.update({k: val for k, val in d.items() if val})
            if d.get("description"):
                ok += 1
        if run:
            run.count(описаний=ok)
    return ok


def screen(vacancies: list[dict], profile: str,
           run: progress.Run | None = None) -> tuple[dict[str, dict], str]:
    """Прогнать кандидатов через модель батчами. → (оценки, примечание о сбое)."""
    size = analyzer.llm_cfg().get("batch_size", 8)
    scores: dict[str, dict] = {}
    batches = (len(vacancies) + size - 1) // size
    for i in range(0, len(vacancies), size):
        batch = vacancies[i:i + size]
        if run:
            run.current(f"оценка моделью, батч {i // size + 1}/{batches}")
        try:
            scores.update(analyzer.screen_batch(batch, profile))
            if run:
                run.count(оценено=len(scores))
        except llm.LLMUnavailable as e:
            return scores, f"модель отвалилась на батче {i // size + 1}: {e}"
    return scores, ""


def _apply_outcome(msg: str) -> str:
    """Классифицировать ответ драйвера: applied / already / unconfirmed / skip.

    Успехом считаем ТОЛЬКО явное «успешно» — драйвер отвечает так, когда модалка
    закрылась. Формулировка «статус не подтверждён» означает, что модалка осталась
    открытой: обычно это незаполненные обязательные вопросы работодателя, и отклик
    НЕ ушёл. Раньше она попадала в applied, вакансия уходила в историю как
    отработанная, и мы к ней уже не возвращались.
    """
    m = (msg or "").lower()
    if "уже откликал" in m or "already" in m:
        return "already"
    if "успешно" in m:
        return "applied"
    if "не подтверждён" in m or "не подтвержден" in m:
        return "unconfirmed"
    return "skip"


def reconcile_unconfirmed(conn, vacancies: list[dict]) -> int:
    """Перепроверить «неподтверждённые» отклики по списку откликов на hh.

    Возвращает, сколько из них на самом деле отправлено (и чинит их статус в БД).
    Сверяем по паре «название + компания»: id вакансии в списке откликов hh не
    отдаёт, а название с компанией пару различают достаточно надёжно.
    """
    rows = conn.execute(
        "SELECT id, title, company FROM applied WHERE outcome='unconfirmed'"
    ).fetchall()
    if not rows:
        return 0
    try:
        apps = driver("hh_get_applications", tries=2)
    except RuntimeError:
        return 0
    if not isinstance(apps, list) or not apps:
        return 0

    def key(title: str, company: str) -> str:
        return f"{(title or '').strip().lower()}|{(company or '').strip().lower()}"

    sent = {key(a.get("title"), a.get("company")) for a in apps}
    fixed = 0
    for r in rows:
        if key(r["title"], r["company"]) in sent:
            conn.execute("UPDATE applied SET outcome='applied' WHERE id=?", (r["id"],))
            fixed += 1
    conn.commit()
    return fixed


def main() -> None:
    dry = "--dry" in sys.argv
    explain = "--explain" in sys.argv
    use_llm = "--no-llm" not in sys.argv and analyzer.enabled()

    run = progress.Run(mode="просмотр" if dry else ("боевой" if use_llm else "боевой без LLM"))
    try:
        _run(dry, explain, use_llm, run)
    except SystemExit as e:
        run.fail(str(e))
        raise
    except BaseException as e:  # noqa: BLE001 — статус должен закрыться при любом исходе
        run.fail(f"{type(e).__name__}: {e}")
        raise


def _run(dry: bool, explain: bool, use_llm: bool, run: progress.Run) -> None:
    run.phase("проверка авторизации")
    if driver("hh_check_auth", tries=3) is not True:
        raise SystemExit("hh: нет авторизации — нужно войти (hh_login) и сохранить сессию")

    resume_id = pick_resume()
    conn = history.connect()
    moved = history.migrate_legacy(conn)
    touched = history.touched_ids(conn)
    target = CONFIG.get("target_applies", 10)
    cfg = analyzer.llm_cfg()
    min_score = cfg.get("min_score", 6)

    # С моделью берём запас кандидатов — часть отсеется по описанию.
    pool = min(cfg.get("max_screened", 25), target * 3) if use_llm else target
    run.phase("поиск вакансий", цель=target)
    fresh, total, irrelevant = collect_candidates(touched, pool, run)

    mode = f"LLM-отсев (порог {min_score}/10)" if use_llm else "без LLM (только фильтр названий)"
    print(f"hh: «{CONFIG['query']}» (поле {CONFIG.get('search_field','all')}) · {mode}")
    print(f"    просмотрено {total}, отсеяно по названию {irrelevant}, "
          f"кандидатов {len(fresh)}, цель {target}"
          + (f", мигрировано в БД {moved}" if moved else ""))

    if not fresh:
        msg = "новых релевантных вакансий нет — все уже отработаны или сужена выдача"
        print(f"hh-итог: {msg}")
        run.finish(msg)
        return

    profile = ""
    scores: dict[str, dict] = {}
    if use_llm:
        run.phase("загрузка описаний", кандидатов=len(fresh))
        loaded = load_descriptions(fresh, run)
        profile = analyzer.profile_text(resume_id)
        run.phase("оценка моделью", описаний=loaded)
        scores, note = screen(fresh, profile, run)
        if note:
            print(f"    ⚠️ {note} — остаток идёт по фильтру названий")
        # Вакансии без оценки (модель отвалилась) пропускаем вперёд, а не режем.
        passed = [v for v in fresh
                  if scores.get(str(v.get("id")), {}).get("score", min_score) >= min_score]
        cut = len(fresh) - len(passed)
        print(f"    описаний загружено {loaded}/{len(fresh)}, "
              f"оценено моделью {len(scores)}, отсеяно по смыслу {cut}")
        if explain:
            for v in fresh:
                s = scores.get(str(v.get("id")), {})
                mark = "✓" if s.get("score", min_score) >= min_score else "✗"
                print(f"      {mark} {s.get('score','—')}/10 {v.get('title')} — "
                      f"{v.get('company')}: {s.get('reason','нет оценки')}")
        passed_ids = {str(v.get("id")) for v in passed}
        for v in fresh:
            if str(v.get("id")) not in passed_ids:
                s = scores.get(str(v.get("id")), {})
                history.record(conn, v, outcome="rejected_by_llm",
                               score=s.get("score"), reason=s.get("reason", ""))
        fresh = sorted(passed, key=lambda v: -scores.get(str(v.get("id")), {}).get("score", 0))
        run.count(отсеяно_моделью=cut, прошли_отбор=len(passed))

    if dry:
        for v in fresh[:target]:
            s = scores.get(str(v.get("id")), {})
            print(f"  • [{s.get('score','—')}/10] {v.get('title')} — "
                  f"{v.get('company')} ({v.get('id')})")
        history.sync_legacy_file(conn)
        run.finish(f"просмотр: найдено {len(fresh)} подходящих вакансий, отклики не отправлялись")
        return

    ok = already = skip = personal = unconfirmed = 0
    # Знаменатель — сколько реально предстоит отправить, а не цель из конфига:
    # после отсева моделью кандидатов обычно меньше цели, и «1/20» при десяти
    # прошедших вводило в заблуждение.
    to_send = min(len(fresh), target)
    run.phase("отправка откликов", к_отправке=to_send)
    for v in fresh:
        if ok >= target:
            break
        vid = str(v.get("id"))
        s = scores.get(vid, {})
        run.current(f"{ok + 1}/{to_send}: {v.get('title')} — {v.get('company')}")

        letter, generated = (COVER, False)
        if use_llm and cfg.get("personal_letter", True):
            run.current(f"{ok + 1}/{to_send}: пишу письмо для «{v.get('title')}»")
            letter, generated = analyzer.cover_letter(v, profile, COVER)
            personal += int(generated)

        try:
            res = driver("hh_apply", {"vacancy_id": vid, "resume_id": resume_id,
                                      "cover_letter": letter})
            outcome = _apply_outcome(str(res))
        except RuntimeError as e:
            outcome = "skip"
            res = str(e)[:80]

        history.record(conn, v, outcome=outcome, score=s.get("score"),
                       reason=s.get("reason", ""), letter=letter if outcome == "applied" else "")

        badge = "✍️" if generated else ""
        if outcome == "applied":
            ok += 1
            print(f"  ✅ [{s.get('score','—')}/10] {v.get('title')} — {v.get('company')} {badge}")
            run.log(f"✅ {s.get('score','—')}/10 {v.get('title')} — {v.get('company')}")
            if explain and generated:
                print("     " + letter.replace("\n", "\n     "))
        elif outcome == "already":
            already += 1
            print(f"  ↩︎ уже откликались: {v.get('title')}")
            run.log(f"↩︎ уже было: {v.get('title')}")
        elif outcome == "unconfirmed":
            unconfirmed += 1
            print(f"  ❓ {v.get('title')} — {v.get('company')}: модалка не закрылась, "
                  f"скорее всего опрос работодателя. Отклик НЕ ушёл: {v.get('url') or vid}")
            run.log(f"❓ опрос работодателя: {v.get('title')}")
        else:
            skip += 1
            print(f"  ⏭ {v.get('title')}: {str(res)[:80]}")
            run.log(f"⏭ {v.get('title')}: {str(res)[:60]}")
        run.count(отправлено=ok, уже_было=already, пропущено=skip, опросы=unconfirmed)
        time.sleep(8)  # не частить браузером

    # Сверка по факту. Незакрывшаяся модалка НЕ означает, что отклик не ушёл:
    # у вакансий с опросом/тестом hh создаёт отклик и оставляет модалку с
    # предложением пройти опрос в чате. Единственный надёжный источник правды —
    # список откликов. Один заход на страницу на весь прогон.
    if unconfirmed:
        confirmed = reconcile_unconfirmed(conn, fresh)
        if confirmed:
            ok += confirmed
            unconfirmed -= confirmed
            print(f"    ✔︎ сверка со списком откликов: {confirmed} из «непонятных» "
                  f"на самом деле отправлены")
            run.count(отправлено=ok, опросы=unconfirmed)

    history.sync_legacy_file(conn)
    tail = f", требуют ручного отклика {unconfirmed}" if unconfirmed else ""
    summary = (f"откликов {ok}, из них с персональным письмом {personal}, "
               f"уже-было {already}, пропусков {skip}{tail}")
    print(f"hh-итог: {summary}, всего в истории {len(history.touched_ids(conn))}")
    run.finish(summary)


if __name__ == "__main__":
    main()
