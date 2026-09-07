"""FSM-состояния для всех сценариев чек-инов (§3 MASTER-PLAN)."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class MorningStates(StatesGroup):
    valence = State()
    arousal = State()
    q3 = State()            # ротируемый: sleep / emotion
    body_word = State()     # опциональный свободный ввод


class DayStates(StatesGroup):
    arousal = State()
    emotions = State()      # мультивыбор
    body_tension = State()  # мультивыбор
    trigger = State()


class EveningStates(StatesGroup):
    valence = State()
    anxiety = State()
    self_criticism = State()
    rumination = State()
    rumination_topics = State()   # мультивыбор, если руминация ≥1
    # deep dive
    felt_vs_analyzed = State()
    human_contact = State()
    resource = State()            # ресурс/радость или злость (ротация)
    resource_text = State()       # свободный ввод ресурса
    regulation = State()          # мультивыбор
    free_text = State()           # опциональное свободное поле


class SituationalStates(StatesGroup):
    body_tension = State()        # мультивыбор
    what = State()                # эмоция/триггер
    intensity = State()
    free_text = State()           # опц.


class AgencyStates(StatesGroup):
    """Ежедневная развилка + база (агентность, не состояние)."""
    fork = State()            # решил сам / подчинился / поспорил-назло / развилки не было
    addressee = State()       # кому относилось (если развилка была)
    note = State()            # «что именно» (опц. свободный текст)
    base = State()            # мультивыбор сон/тело/ходьба


class DeedStates(StatesGroup):
    """Событийный лог поступка A/B/C."""
    kind = State()            # A / B / C
    action = State()          # для A: выход / проглотил / сорвался
    addressee = State()       # для A/B: кому
    note = State()            # опц. свободный текст


class WeeklyStates(StatesGroup):
    """Обзор недели — 3 вопроса."""
    q1 = State()              # куда шла сила
    q1_text = State()
    q2 = State()              # позиция про дело
    q2_text = State()
    q3 = State()              # кому нёс важное
    q3_text = State()


class MonthlyStates(StatesGroup):
    """Обзор месяца — 7 фактов да/нет (один экран тумблеров)."""
    facts = State()


class HabitsStates(StatesGroup):
    """Привычки: ежедневный трекинг + управление списком на кнопках."""
    mark = State()      # экран тумблеров (что сделал / где сорвался)
    adding = State()    # ввод названия новой привычки (тип уже выбран кнопкой)


class NoteStates(StatesGroup):
    waiting_text = State()


class EditStates(StatesGroup):
    waiting_new_text = State()


class TaskStates(StatesGroup):
    waiting_text = State()        # ввод текста новой задачи
    editing_text = State()        # правка текста существующей задачи
    due_input = State()           # ввод срока строкой ("завтра 18:00")
    labels_input = State()        # ввод меток через запятую
