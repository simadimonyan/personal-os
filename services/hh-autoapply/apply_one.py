#!/usr/bin/env python3
"""
Отклик на одну вакансию — для ручного отклика из бота («лента пропущенных»).

Отдельный скрипт, а не флаг у autoapply.py: у того свой конвейер с поиском,
отбором и лимитами, а здесь нужно ровно одно действие над конкретным id.
Пишет в ту же history.db, поэтому вакансия перестаёт считаться пропущенной.

Запуск:  python3 apply_one.py <vacancy_id>
Печатает одну строку итога — её бот показывает в карточке.
"""
from __future__ import annotations

import sys

import analyzer
import history
from autoapply import COVER, _apply_outcome
from hhdriver import CONFIG, driver


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("нужен id вакансии")
    vid = str(sys.argv[1]).strip()

    resume_id = CONFIG.get("resume_id")
    if not resume_id:
        raise SystemExit("в конфиге не выбрано резюме")

    conn = history.connect()
    row = conn.execute("SELECT title, company, url, score FROM applied WHERE id=?", (vid,)).fetchone()
    vacancy = dict(row) if row else {}
    vacancy["id"] = vid

    # Описание нужно для персонального письма; без него уйдёт статичное из cover.txt.
    try:
        details = driver("hh_get_vacancy", {"vacancy_id": vid}, tries=2)
        if isinstance(details, dict):
            vacancy.update({k: v for k, v in details.items() if v})
    except RuntimeError:
        pass

    letter, generated = COVER, False
    if analyzer.enabled() and analyzer.llm_cfg().get("personal_letter", True):
        letter, generated = analyzer.cover_letter(vacancy, analyzer.profile_text(resume_id), COVER)

    try:
        res = driver("hh_apply", {"vacancy_id": vid, "resume_id": resume_id,
                                  "cover_letter": letter}, tries=2)
        outcome = _apply_outcome(str(res))
    except RuntimeError as e:
        outcome, res = "skip", str(e)[:120]

    history.record(conn, vacancy, outcome=outcome,
                   score=vacancy.get("score"), reason="ручной отклик из бота",
                   letter=letter if outcome == "applied" else "")
    history.sync_legacy_file(conn)

    mark = {"applied": "✅ отклик отправлен",
            "already": "↩︎ уже откликались раньше",
            "unconfirmed": "❓ hh не подтвердил — проверь вручную"}.get(outcome, f"⏭ не вышло: {res}")
    print(f"{mark}{' · письмо под вакансию' if generated and outcome == 'applied' else ''}")


if __name__ == "__main__":
    main()
