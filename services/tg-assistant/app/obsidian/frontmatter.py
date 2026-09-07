"""Read-modify-write YAML frontmatter через ruamel.yaml.

КРИТИЧЕСКИЕ ПРАВИЛА (§4.3 MASTER-PLAN, ADR-4):
- null для НЕспрошенных метрик — НЕ 0 (0 ≠ «нет данных»).
- сохранение порядка ключей между записями (стабильная история трендов).
- merge по слотам БЕЗ потери ранее записанных данных:
  второй чек-ин дня дополняет frontmatter, не затирая первый.
- списочные метрики (emotions, body_tension, triggers, regulation_used,
  rumination_topics, flags) объединяются как множество (union), сохраняя порядок.
- скалярные метрики перезаписываются только если пришло НЕ-None значение.

Чистые функции, без I/O. Работают со строками. Вызываются из writer
(который уже в потоке asyncio.to_thread).
"""

from __future__ import annotations

import io
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from app.domain.metrics import FRONTMATTER_KEY_ORDER, REGISTRY, MetricType

# метрики-списки, которые надо объединять (union), а не перезаписывать
_LIST_MERGE_KEYS = {
    "emotions",
    "body_tension",
    "rumination_topics",
    "regulation_used",
    "triggers",
    "flags",
    "base_done",
}

_FM_DELIM = "---"


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.default_flow_style = False
    y.allow_unicode = True
    y.indent(mapping=2, sequence=2, offset=0)
    y.width = 4096  # не переносить длинные строки
    return y


def split_document(text: str) -> tuple[str, str]:
    """Разделяет .md на (frontmatter_block, body).

    frontmatter_block — содержимое МЕЖДУ разделителями (без самих ---).
    Если frontmatter отсутствует — возвращает ('', text).
    """
    if not text.startswith(_FM_DELIM):
        return "", text
    # ищем закрывающий разделитель
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != _FM_DELIM:
        return "", text
    for i in range(1, len(lines)):
        if lines[i].strip() == _FM_DELIM:
            fm = "".join(lines[1:i])
            body = "".join(lines[i + 1 :])
            # убрать ведущий перевод строки у тела
            body = body.lstrip("\n")
            return fm, body
    return "", text


def parse_frontmatter(fm_block: str) -> CommentedMap:
    if not fm_block.strip():
        return CommentedMap()
    data = _yaml().load(fm_block)
    if data is None:
        return CommentedMap()
    if not isinstance(data, CommentedMap):
        # на всякий случай нормализуем
        cm = CommentedMap()
        for k, v in dict(data).items():
            cm[k] = v
        return cm
    return data


def dump_frontmatter(data: CommentedMap) -> str:
    buf = io.StringIO()
    _yaml().dump(data, buf)
    return buf.getvalue()


def _ordered(data: CommentedMap) -> CommentedMap:
    """Переупорядочивает ключи согласно FRONTMATTER_KEY_ORDER.
    Ключи вне списка идут в конце в исходном порядке появления."""
    result = CommentedMap()
    for key in FRONTMATTER_KEY_ORDER:
        if key in data:
            result[key] = data[key]
    for key in data:
        if key not in result:
            result[key] = data[key]
    return result


def _merge_list(existing: Any, incoming: Any) -> CommentedSeq:
    """Union с сохранением порядка: сначала старые, потом новые отсутствующие."""
    seq = CommentedSeq()
    seen: set = set()

    def add(items: Any) -> None:
        if items is None:
            return
        iterable = items if isinstance(items, (list, tuple, CommentedSeq)) else [items]
        for it in iterable:
            if it is None:
                continue
            if it not in seen:
                seen.add(it)
                seq.append(it)

    add(existing)
    add(incoming)
    return seq


def merge_frontmatter(existing: CommentedMap, patch: dict[str, Any]) -> CommentedMap:
    """Объединяет существующий frontmatter с патчем нового слота.

    Правила:
    - списочные ключи (_LIST_MERGE_KEYS) — union;
    - скалярные — перезапись только не-None значением; None НЕ затирает существующее
      и НЕ создаёт ключ (отсутствие = null по соглашению §4.3);
    - явный None для НЕсуществующего ключа допустим только если этот ключ
      присутствует в патче намеренно (мы создаём его со значением null,
      чтобы Dataview видел поле). См. _apply_scalar.
    """
    result = CommentedMap()
    # копируем существующее
    for k, v in existing.items():
        result[k] = v

    for key, value in patch.items():
        if key in _LIST_MERGE_KEYS:
            result[key] = _merge_list(result.get(key), value)
        else:
            _apply_scalar(result, key, value)

    return _ordered(result)


def _apply_scalar(target: CommentedMap, key: str, value: Any) -> None:
    """Скалярная запись с null-правилами.

    - value is not None -> записать.
    - value is None и ключа ещё нет -> создать со значением None (Dataview-friendly null).
    - value is None и ключ есть -> НЕ затирать существующее значение.
    """
    if value is not None:
        target[key] = value
    elif key not in target:
        target[key] = None
    # else: оставляем как есть


def build_patch_from_answers(
    answers: dict[str, Any],
    slot: str,
    flags: list[str] | None = None,
) -> dict[str, Any]:
    """Преобразует накопленные ответы чек-ина в патч frontmatter.

    - слотовые метрики (per_slot) получают суффикс слота: valence_morning и т.п.
    - trigger_type попадает в агрегирующий список `triggers`.
    - flags объединяются в `flags`.
    - значения None НЕ включаются в патч (merge сам решит про null).
    """
    patch: dict[str, Any] = {}

    for key, value in answers.items():
        if value is None:
            continue
        spec = REGISTRY.get(key)

        if key == "trigger_type":
            # пишем в агрегирующий список triggers (исключая служебные «не_было»)
            if value not in ("не_было",):
                patch.setdefault("triggers", [])
                patch["triggers"] = _as_list(patch.get("triggers")) + _as_list(value)
            continue

        if spec is None:
            patch[key] = value
            continue

        out_key = spec.slot_key(slot if spec.per_slot else None)

        if spec.type == MetricType.MULTI:
            patch[out_key] = _as_list(value)
        else:
            patch[out_key] = value

    if flags:
        patch.setdefault("flags", [])
        patch["flags"] = _as_list(patch.get("flags")) + list(flags)

    return patch


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def ensure_base_frontmatter(data: CommentedMap, date: str) -> CommentedMap:
    """Гарантирует обязательные базовые ключи (date, type, tags)."""
    if "date" not in data:
        data["date"] = date
    if "type" not in data:
        data["type"] = "state-log"
    if "tags" not in data:
        tags = CommentedSeq()
        tags.append("дневник-состояний")
        data["tags"] = tags
    return _ordered(data)
