"""Тесты merge frontmatter и атомарной записи (КРИТИЧНО, §4.3 + ADR-3,4).

Проверяем:
- append второго слота не теряет данные первого;
- null для неспрошенных метрик (не 0);
- сохранение порядка ключей;
- union списочных метрик;
- атомарность записи (.tmp + os.replace), отсутствие .tmp после записи;
- конкурентные слоты (lock per-date).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.obsidian import frontmatter as fm
from app.obsidian.writer import ObsidianWriter

# --- merge_frontmatter ---

def test_merge_preserves_first_slot_when_adding_second():
    existing = fm.parse_frontmatter("valence_morning: 0\narousal_morning: 4\n")
    patch = {"valence_day": -2, "arousal_day": 7}
    merged = fm.merge_frontmatter(existing, patch)
    assert merged["valence_morning"] == 0
    assert merged["arousal_morning"] == 4
    assert merged["valence_day"] == -2
    assert merged["arousal_day"] == 7


def test_null_for_unasked_not_zero():
    existing = fm.parse_frontmatter("")
    patch = {"catastrophizing": None}
    merged = fm.merge_frontmatter(existing, patch)
    assert "catastrophizing" in merged
    assert merged["catastrophizing"] is None  # именно null, не 0


def test_none_does_not_overwrite_existing():
    existing = fm.parse_frontmatter("anxiety: 7\n")
    patch = {"anxiety": None}
    merged = fm.merge_frontmatter(existing, patch)
    assert merged["anxiety"] == 7  # не затёрли


def test_list_union_no_duplicates():
    existing = fm.parse_frontmatter("emotions: [anxiety, envy]\n")
    patch = {"emotions": ["envy", "apathy"]}
    merged = fm.merge_frontmatter(existing, patch)
    assert list(merged["emotions"]) == ["anxiety", "envy", "apathy"]


def test_key_order_stable():
    existing = fm.parse_frontmatter("")
    existing = fm.ensure_base_frontmatter(existing, "2026-06-09")
    patch = {"arousal_morning": 4, "valence_morning": 0, "anxiety": 7}
    merged = fm.merge_frontmatter(existing, patch)
    keys = list(merged.keys())
    # date раньше valence_morning, тот раньше arousal_morning (по FRONTMATTER_KEY_ORDER)
    assert keys.index("date") < keys.index("valence_morning")
    assert keys.index("valence_morning") < keys.index("arousal_morning")
    assert keys.index("arousal_morning") < keys.index("anxiety")


def test_build_patch_slot_suffix_and_triggers():
    answers = {
        "valence": -2,
        "arousal": 7,
        "emotions": ["anxiety"],
        "trigger_type": "сравнение_с_другими",
    }
    patch = fm.build_patch_from_answers(answers, "day", flags=["flag_jealousy"])
    assert patch["valence_day"] == -2       # слотовый суффикс
    assert patch["arousal_day"] == 7
    assert patch["emotions"] == ["anxiety"]
    assert patch["triggers"] == ["сравнение_с_другими"]  # trigger_type -> triggers
    assert patch["flags"] == ["flag_jealousy"]


def test_build_patch_skips_none():
    patch = fm.build_patch_from_answers({"valence": None, "arousal": 5}, "morning")
    assert "valence_morning" not in patch
    assert patch["arousal_morning"] == 5


def test_split_document_roundtrip():
    text = "---\ndate: 2026-06-09\n---\n\n# Состояние\n\n## ☀️ Утро · 10:00\nТон 0.\n"
    fm_block, body = fm.split_document(text)
    assert "date: 2026-06-09" in fm_block
    assert body.startswith("# Состояние")


def test_split_document_no_frontmatter():
    text = "# Просто заметка\n"
    fm_block, body = fm.split_document(text)
    assert fm_block == ""
    assert body == text


# --- атомарная запись через writer ---

class _StubPaths:
    def __init__(self, tmp: Path) -> None:
        self._tmp = tmp

    def diary_file(self, date: str) -> Path:
        return self._tmp / f"{date}.md"

    def ensure_dirs(self) -> None:
        self._tmp.mkdir(parents=True, exist_ok=True)


@pytest.mark.asyncio
async def test_writer_two_slots_no_data_loss(tmp_path):
    writer = ObsidianWriter(_StubPaths(tmp_path))
    date = "2026-06-09"

    await writer.write_slot(date, "morning", {"valence": 0, "arousal": 4, "emotions": ["anxiety"]}, hhmm="10:42")
    await writer.write_slot(date, "day", {"valence": -2, "arousal": 7, "emotions": ["envy"]}, hhmm="15:20")

    content = (tmp_path / f"{date}.md").read_text(encoding="utf-8")
    fm_block, body = fm.split_document(content)
    data = fm.parse_frontmatter(fm_block)

    # оба слота на месте
    assert data["valence_morning"] == 0
    assert data["valence_day"] == -2
    # эмоции объединены
    assert set(data["emotions"]) == {"anxiety", "envy"}
    # обе секции в теле
    assert "Утро · 10:42" in body
    assert "День · 15:20" in body


@pytest.mark.asyncio
async def test_writer_no_tmp_leftover(tmp_path):
    writer = ObsidianWriter(_StubPaths(tmp_path))
    await writer.write_slot("2026-06-09", "morning", {"valence": 1, "arousal": 5}, hhmm="10:00")
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []  # временные файлы подчищены


@pytest.mark.asyncio
async def test_writer_concurrent_slots_serialized(tmp_path):
    writer = ObsidianWriter(_StubPaths(tmp_path))
    date = "2026-06-09"
    await asyncio.gather(
        writer.write_slot(date, "morning", {"valence": 0, "arousal": 4}, hhmm="10:00"),
        writer.write_slot(date, "day", {"valence": -1, "arousal": 6}, hhmm="15:00"),
        writer.write_slot(date, "evening", {"valence": 2, "arousal": 5}, hhmm="22:40"),
    )
    content = (tmp_path / f"{date}.md").read_text(encoding="utf-8")
    data = fm.parse_frontmatter(fm.split_document(content)[0])
    # все три слота записаны, ничего не потеряно гонкой
    assert data["valence_morning"] == 0
    assert data["valence_day"] == -1
    assert data["valence_evening"] == 2


@pytest.mark.asyncio
async def test_replace_last_section(tmp_path):
    writer = ObsidianWriter(_StubPaths(tmp_path))
    date = "2026-06-09"
    await writer.write_slot(date, "morning", {"valence": 0, "arousal": 4}, hhmm="10:00")
    await writer.append_raw_section(date, "## 📝 Заметка · 12:00\nстарый текст")

    ok = await writer.replace_last_section(date, "## 📝 Заметка · 12:00\nновый текст")
    assert ok
    content = (tmp_path / f"{date}.md").read_text(encoding="utf-8")
    assert "новый текст" in content
    assert "старый текст" not in content
    # утренняя секция не пострадала
    assert "Утро · 10:00" in content
