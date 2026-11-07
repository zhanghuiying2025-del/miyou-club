"""Company learning records stored at the legacy path for existing installations."""
from pathlib import Path
from threading import RLock
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field

from src.models import FeedbackLesson


PERFORMANCE_FILE = Path(__file__).resolve().parent.parent / "data" / "performance.json"
_LOCK = RLock()


class LearningState(BaseModel):
    # Round-trip historical fields without using them to run the company.
    model_config = ConfigDict(extra="allow")
    applied_events: list[str] = Field(default_factory=list)
    lessons: list[FeedbackLesson] = Field(default_factory=list)


def _load() -> LearningState:
    if PERFORMANCE_FILE.exists():
        return LearningState.model_validate_json(PERFORMANCE_FILE.read_text(encoding="utf-8"))
    return LearningState()


def _save(state: LearningState) -> None:
    PERFORMANCE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = PERFORMANCE_FILE.with_name(f"performance_{uuid4().hex}.tmp")
    try:
        temporary.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(PERFORMANCE_FILE)
    finally:
        temporary.unlink(missing_ok=True)


def company_lessons() -> list[FeedbackLesson]:
    with _LOCK:
        return _load().lessons


def record_feedback(event_id, lessons):
    """Store evidence once per feedback event."""
    with _LOCK:
        state = _load()
        if event_id not in state.applied_events:
            known = {item.id for item in state.lessons}
            state.lessons.extend(item for item in lessons if item.id not in known)
            state.applied_events.append(event_id)
            _save(state)
        return [x for x in state.lessons if x.event_id == event_id]


def mark_lessons_supplied(case_id, lesson_ids):
    with _LOCK:
        state = _load()
        changed = False
        for lesson in state.lessons:
            event = f"{case_id}:supplied:{lesson.id}"
            if lesson.id in lesson_ids and event not in state.applied_events:
                lesson.supplied_cases += 1
                state.applied_events.append(event)
                changed = True
        if changed:
            _save(state)


def set_lesson_active(lesson_id, active):
    with _LOCK:
        state = _load()
        lesson = next((x for x in state.lessons if x.id == lesson_id), None)
        if lesson is None:
            raise ValueError("找不到这条反馈记录，请刷新后重试")
        lesson.active = bool(active)
        _save(state)
