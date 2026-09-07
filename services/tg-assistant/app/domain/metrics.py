"""Реестр ВСЕХ метрик (§1 MASTER-PLAN).

ЕДИНСТВЕННЫЙ источник истины для ключей YAML и аналитики.
Никакие строковые литералы ключей не должны появляться в keyboards/handlers/writer —
только ссылки на константы / реестр отсюда (§4.3, §10 ARCHITECTURE: «имена стабильны»).

Слотовые метрики (valence, arousal) пишутся в frontmatter с суффиксом слота:
    valence_morning, valence_day, valence_evening, arousal_morning, ...
Это задаётся флагом MetricSpec.per_slot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class MetricType(str, Enum):
    INT = "int"            # целочисленная шкала
    ENUM = "enum"          # одно значение из набора
    MULTI = "multi"        # мультивыбор -> список
    STRING = "string"      # свободное слово/фраза
    BOOL = "bool"          # булево


@dataclass(frozen=True)
class MetricSpec:
    key: str                              # машинный ключ (YAML)
    title: str                            # человекочитаемое имя
    type: MetricType
    scale: tuple[int, int] | None = None  # для INT: (min, max)
    options: tuple[str, ...] = field(default_factory=tuple)  # для ENUM/MULTI
    per_slot: bool = False                # пишется ли с суффиксом слота
    source: str = ""                      # C/E происхождение (для документации)

    def slot_key(self, slot: str | None) -> str:
        """Имя ключа во frontmatter с учётом слотовости."""
        if self.per_slot and slot:
            return f"{self.key}_{slot}"
        return self.key


# ============================================================================
# Палитры (значения = машинные ключи; человекочитаемые подписи — в keyboards)
# ============================================================================

EMOTIONS = (
    "anxiety",      # тревога
    "shame",        # стыд / «я хуже»
    "envy",         # зависть
    "loneliness",   # одиночество
    "anger",        # злость / раздражение (видимой кнопкой!)
    "sadness",      # грусть
    "apathy",       # апатия / пустота
    "joy",          # радость / тепло
    "calm",         # спокойствие / безопасность
    "hope",         # воодушевление / надежда
    "unreadable",   # «не считывается» (честный ответ при диссоциации)
)

BODY_ZONES = (
    "грудь",
    "горло",
    "челюсть",
    "плечи",
    "живот",
    "голова",
    "глаза",
    "спина",
    "руки",
    "ничего_не_замечаю",   # обязательная опция — маркер диссоциации, не пустота
)

TRIGGER_TYPES = (
    "сравнение_с_другими",
    "потеря_контроля",
    "ревность_сигнал",
    "насмешка_обесценивание",
    "соц_ненормальность",
    "усталость_тело",
    "отвержение",
    "неизвестно",
    "не_было",
)

REGULATION = (
    "люди_контакт",
    "работа_проект",
    "спорт_тело",
    "прогулка_природа",
    "сон_отдых",
    "дневник_анализ",
    "юмор",
    "ничего",
)

SLEEP_QUALITY = ("глубокий", "рваный", "поздний", "норма")

RUMINATION_TOPICS = (
    "отношения",
    "самообвинение",
    "работа",
    "сравнение",
    "будущее",
    "другое",
)

HUMAN_CONTACT = ("да_глубокий", "да_поверхностный", "нет")

# --- Агентность / авторство (метрики действий, не состояния) ---
# Ядро «решил сам · было страшно · сделал». Значения — латиницей (машинные ключи),
# человекочитаемые подписи живут в keyboards/agency.py и note_body.py.
AGENCY_FORK = ("sam", "obey", "spite", "none")        # решил сам / подчинился / поспорил-назло / развилки не было
ADDRESSEE = ("close", "friends", "work", "self")      # близкие / друзья / дело / сам с собой (обобщённые роли, без конкретных имён)
BASE_DONE = ("sleep", "body", "walk")                 # сон / тело / ходьба (Фаза 0)
DEED_KIND = ("A", "B", "C")                           # столкнулся→что сделал / сказал тяжёлое / начал контакт
DEED_ACTION = ("out", "swallow", "burst")             # выход / проглотил из страха / сорвался
DEED_ADDRESSEE = ("support", "judge")                 # тому, кто поддержит / тому, кто оценивает


# ============================================================================
# Реестр метрик
# ============================================================================

# --- Обязательные (§1.1) ---
VALENCE = MetricSpec("valence", "Тон", MetricType.INT, scale=(-5, 5), per_slot=True, source="E")
AROUSAL = MetricSpec("arousal", "Энергия", MetricType.INT, scale=(1, 10), per_slot=True, source="E")
EMOTIONS_M = MetricSpec("emotions", "Эмоции", MetricType.MULTI, options=EMOTIONS, source="E")
BODY_TENSION = MetricSpec("body_tension", "Зона напряжения", MetricType.MULTI, options=BODY_ZONES, source="E+C8")
ANXIETY = MetricSpec("anxiety", "Тревожные мысли", MetricType.INT, scale=(1, 10), source="C4")
SELF_CRITICISM = MetricSpec("self_criticism", "Внутренний критик", MetricType.INT, scale=(1, 10), source="C6")

# --- Опциональные / ротация / целевые слоты (§1.2) ---
BODY_WORD = MetricSpec("body_word", "Тело одним словом", MetricType.STRING, source="E+C")
FOCUS = MetricSpec("focus", "Фокус", MetricType.INT, scale=(1, 10), source="C1")
CLARITY = MetricSpec("clarity", "Ясность мышления", MetricType.INT, scale=(1, 10), source="C2")
PRODUCTIVITY = MetricSpec("productivity", "Продуктивность", MetricType.INT, scale=(1, 10), source="C3")
RUMINATION_LEVEL = MetricSpec("rumination_level", "Руминации", MetricType.INT, scale=(0, 2), source="C5")
RUMINATION_TOPICS_M = MetricSpec("rumination_topics", "Темы руминации", MetricType.MULTI, options=RUMINATION_TOPICS, source="C5")
FELT_VS_ANALYZED = MetricSpec("felt_vs_analyzed", "Прожил vs продумал", MetricType.INT, scale=(0, 10), source="E+C7")
HUMAN_CONTACT_M = MetricSpec("human_contact", "Живой контакт", MetricType.ENUM, options=HUMAN_CONTACT, source="E")
TRIGGER_TYPE = MetricSpec("trigger_type", "Триггер", MetricType.ENUM, options=TRIGGER_TYPES, source="E")
TRIGGER_NOTE = MetricSpec("trigger_note", "Контекст триггера", MetricType.STRING, source="E")
REGULATION_USED = MetricSpec("regulation_used", "Что помогло выдохнуть", MetricType.MULTI, options=REGULATION, source="E")
SLEEP_QUALITY_M = MetricSpec("sleep_quality", "Качество сна", MetricType.ENUM, options=SLEEP_QUALITY, source="E")
SENSE_OF_CONTROL = MetricSpec("sense_of_control", "Контроль над днём", MetricType.INT, scale=(1, 10), source="C10")
MENTAL_FATIGUE = MetricSpec("mental_fatigue", "Умственная усталость", MetricType.INT, scale=(1, 10), source="C9")
CATASTROPHIZING = MetricSpec("catastrophizing", "Катастрофизация", MetricType.INT, scale=(1, 10), source="C11")
SHOULD_PRESSURE = MetricSpec("should_pressure", "Давление «надо»", MetricType.INT, scale=(1, 10), source="C13")
DEVALUED_GOOD = MetricSpec("devalued_good", "Обесценил хорошее", MetricType.BOOL, source="C14")
BLACK_WHITE = MetricSpec("black_white", "Чёрно-белость", MetricType.INT, scale=(1, 10), source="C12")

# Интенсивность ситуативного чек-ина (§3.4). Отдельный ключ, не путать со шкалами выше.
INTENSITY = MetricSpec("intensity", "Интенсивность", MetricType.INT, scale=(0, 10), source="E")

# --- Агентность / авторство (слоты agency + deed) ---
# Метрики ДЕЙСТВИЙ, не состояния. Не числовые (enum/факт) — намеренно, чтобы не было балла.
AGENCY_FORK_M = MetricSpec("agency_fork", "Развилка дня", MetricType.ENUM, options=AGENCY_FORK, source="cog")
AGENCY_ADDRESSEE_M = MetricSpec("agency_addressee", "Кому относилось", MetricType.ENUM, options=ADDRESSEE, source="rel")
BASE_DONE_M = MetricSpec("base_done", "База (Фаза 0)", MetricType.MULTI, options=BASE_DONE, source="emo")
DEED_KIND_M = MetricSpec("deed_kind", "Тип поступка", MetricType.ENUM, options=DEED_KIND, source="rel")
DEED_ACTION_M = MetricSpec("deed_action", "Что сделал с эмоцией", MetricType.ENUM, options=DEED_ACTION, source="emo")
DEED_ADDRESSEE_M = MetricSpec("deed_addressee", "Кому отнёс", MetricType.ENUM, options=DEED_ADDRESSEE, source="rel")

# Триггеры пишутся во frontmatter под ключом `triggers` (список за день), а не trigger_type.
# Это согласовано с §4.1 шаблоном frontmatter: triggers: [...]
TRIGGERS_AGG_KEY = "triggers"


ALL_METRICS: tuple[MetricSpec, ...] = (
    VALENCE, AROUSAL, EMOTIONS_M, BODY_TENSION, ANXIETY, SELF_CRITICISM,
    BODY_WORD, FOCUS, CLARITY, PRODUCTIVITY, RUMINATION_LEVEL, RUMINATION_TOPICS_M,
    FELT_VS_ANALYZED, HUMAN_CONTACT_M, TRIGGER_TYPE, TRIGGER_NOTE, REGULATION_USED,
    SLEEP_QUALITY_M, SENSE_OF_CONTROL, MENTAL_FATIGUE, CATASTROPHIZING,
    SHOULD_PRESSURE, DEVALUED_GOOD, BLACK_WHITE, INTENSITY,
    AGENCY_FORK_M, AGENCY_ADDRESSEE_M, BASE_DONE_M,
    DEED_KIND_M, DEED_ACTION_M, DEED_ADDRESSEE_M,
)

REGISTRY: dict[str, MetricSpec] = {m.key: m for m in ALL_METRICS}


def get_metric(key: str) -> MetricSpec:
    return REGISTRY[key]


# Порядок ключей frontmatter (§4.1). Используется writer-ом для стабильного
# упорядочивания. Слотовые ключи разворачиваются в _morning/_day/_evening.
FRONTMATTER_KEY_ORDER: tuple[str, ...] = (
    "date",
    "type",
    # ядро аффекта по слотам
    "valence_morning", "valence_day", "valence_evening",
    "arousal_morning", "arousal_day", "arousal_evening",
    # эмоции
    "emotions",
    # тело
    "body_tension", "body_word",
    # когнитивные
    "anxiety", "self_criticism", "focus", "clarity", "productivity",
    "rumination_level", "rumination_topics", "mental_fatigue",
    "sense_of_control", "catastrophizing", "should_pressure",
    "devalued_good", "black_white",
    # триггеры / регуляция / контакт / диссоциация
    "triggers", "regulation_used", "human_contact", "felt_vs_analyzed",
    "sleep_quality", "intensity",
    # агентность / авторство (действия, не состояние)
    "agency_fork", "agency_addressee", "base_done",
    "deed_kind", "deed_action", "deed_addressee",
    # авто-флаги
    "flags",
    "tags",
)
