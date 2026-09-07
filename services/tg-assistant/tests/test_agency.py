"""Тесты метрик агентности/авторства (слоты agency, deed + обзоры).

Проверяем:
- рендер секций agency/deed телеграфно;
- «развилки не было» рендерится без адресата;
- пустая база рендерится честным «не было»;
- base_done объединяется (union) при двух записях за день, не перезаписывается;
- новые метрики есть в реестре и в порядке ключей frontmatter;
- недельная/месячная секции формируются.
"""

from __future__ import annotations

from app.domain import metrics as m
from app.keyboards import agency as akb
from app.obsidian import frontmatter as fm
from app.obsidian import note_body as nb


# --- рендер секций ---

def test_agency_section_full():
    a = {"agency_fork": "sam", "agency_addressee": "close", "base_done": ["sleep", "walk"]}
    out = nb.render_section("agency", "23:10", a, free_text="море вопреки")
    assert "## 🧭 Развилка · 23:10" in out
    assert "Развилка: решил сам (кому: близкие)." in out
    assert "База: сон, ходьба." in out
    assert "> море вопреки" in out


def test_agency_no_fork_hides_addressee():
    a = {"agency_fork": "none", "agency_addressee": None, "base_done": []}
    out = nb.render_section("agency", "22:00", a)
    assert "Развилка: развилки не было." in out
    assert "кому" not in out
    assert "База: не было." in out


def test_deed_section_a():
    d = {"deed_kind": "A", "deed_action": "out", "deed_addressee": "support"}
    out = nb.render_section("deed", "21:00", d, free_text="сказал близкому")
    assert "## 📌 Поступок · 21:00" in out
    assert "Столкнулся / накрыло." in out
    assert "Что сделал: нашёл выход." in out
    assert "Кому: тому, кто поддержит." in out


def test_deed_section_c_minimal():
    d = {"deed_kind": "C"}
    out = nb.render_section("deed", "12:00", d, free_text="подошёл первым")
    assert "Начал контакт первым." in out
    assert "> подошёл первым" in out


# --- frontmatter ---

def test_base_done_union_across_two_writes():
    existing = fm.parse_frontmatter("base_done: [sleep]\n")
    patch = {"base_done": ["walk"]}
    merged = fm.merge_frontmatter(existing, patch)
    assert list(merged["base_done"]) == ["sleep", "walk"]


def test_agency_patch_from_answers():
    a = {"agency_fork": "spite", "agency_addressee": "friends", "base_done": ["body"]}
    patch = fm.build_patch_from_answers(a, "agency", [])
    assert patch["agency_fork"] == "spite"
    assert patch["agency_addressee"] == "friends"
    assert patch["base_done"] == ["body"]


# --- реестр ---

def test_new_metrics_registered():
    for key in ("agency_fork", "agency_addressee", "base_done",
                "deed_kind", "deed_action", "deed_addressee"):
        assert key in m.REGISTRY, key
        assert key in m.FRONTMATTER_KEY_ORDER, key


def test_base_done_is_multi():
    assert m.REGISTRY["base_done"].type == m.MetricType.MULTI
    assert m.REGISTRY["agency_fork"].type == m.MetricType.ENUM


# --- обзоры ---

def test_weekly_section():
    from app.handlers import review as rv
    a = {"weekly_q1": "live", "weekly_q1_note": "разговор", "weekly_q2": "yes", "weekly_q3": "support"}
    sec = rv._weekly_section(a)
    assert sec.startswith("## Неделя ")
    assert "Где действовал: И с живыми людьми — разговор" in sec
    assert "Спорил по делу: Да, по делу" in sec


def test_monthly_facts_count():
    assert len(akb.MONTHLY_FACTS) == 7
    keys = [k for k, _ in akb.MONTHLY_FACTS]
    assert "boundary" in keys and "position" in keys and "base" in keys
