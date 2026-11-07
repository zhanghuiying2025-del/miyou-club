"""Style branches and reusable methods derived from accepted order records."""
from src.storage import saved_case_snapshots
from src.performance import company_lessons


STYLE_GUIDES = {
    "城市游玩": "优先设计同一区域内的城市探索，说明步行距离、休息点和小组任务。",
    "户外活动": "按已确认的强度安排，提供自愿参与、天气备选和低强度替代。",
    "博物馆古迹参观": "围绕一个主题设计短讲解与互动，说明开放和预约条件。",
    "还没想好": "按团队构成、活动目标和限制选一个方向，在提案中说明选择依据。",
}
STYLE_OPTIONS = list(STYLE_GUIDES)

# Methods describe how to plan, with no order-specific prices, dates or places.
METHODS = {
    "安排明确的休息点或休息时段": ("休息点", "休息", "中途休整"),
    "用分组任务带动参与": ("小组任务", "小组挑战", "分组探索", "分组任务"),
    "留出可自由选择的时段": ("自由活动", "自由探索", "自由选择"),
    "把长讲解改成短讲解与互动": ("互动讲解", "短讲解", "短时讲解", "故事导览"),
    "提供低强度或自愿参与选项": ("低强度", "自愿参与", "可自愿", "低体力"),
    "准备同主题的雨天室内备选": ("雨天室内", "室内备用", "室内备选", "雨天切换"),
}


def style_for(value):
    text = value or ""
    if text in STYLE_GUIDES:
        return text
    if "不挑" in text or "不限" in text or "不确定" in text:
        return "还没想好"
    for style, aliases in (
        ("城市游玩", ("城市",)),
        ("户外活动", ("户外",)),
        ("博物馆古迹参观", ("文化", "博物馆", "古迹")),
    ):
        if any(alias in text for alias in aliases):
            return style
    return "还没想好"


def matching_style_template(case):
    req = case.requirements
    style = (req.preferred_style or "").strip()
    if style in {"", "还没想好"} or req.team_profile in {None, "", "暂不确定"}:
        return None
    methods = {}
    examples = []
    seen = set()
    disabled = {lesson.id for lesson in company_lessons() if not lesson.active}
    for entry in saved_case_snapshots():
        saved = entry.get("case") or {}
        spec = saved.get("requirements") or {}
        feedback = saved.get("plan_acceptance_feedback") or {}
        goals = set(spec.get("desired_outcomes") or [])
        shared_goals = set(req.desired_outcomes) & goals
        if (not saved.get("id") or saved["id"] in seen or saved["id"] == case.id or saved.get("service_mode") != "travel_planning"
            or saved.get("status") != "completed" or feedback.get("outcome") != "satisfied"
            or (spec.get("preferred_style") or "").strip() != style or spec.get("team_profile") != req.team_profile
            or (req.desired_outcomes and goals and not shared_goals)):
            continue
        version = saved.get("plan_version", 1)
        if any(lesson.get("id") in disabled for record in saved.get("feedback_history", [])
               if record.get("version") == version for lesson in record.get("lessons", [])):
            continue
        plan = saved.get("council_decision") or {}
        schedule = [line.strip() for line in plan.get("schedule", []) if isinstance(line, str) and line.strip()]
        if not schedule:
            continue
        seen.add(saved["id"])
        examples.append(((len(shared_goals), spec.get("duration") == req.duration, entry.get("updated_at", "")),
            {"case_id": saved["id"], "version": version,
             "schedule": [line[:160] for line in schedule[:6]]}))
        schedule_text = " ".join(schedule)
        for method, terms in METHODS.items():
            if any(term in schedule_text for term in terms):
                methods.setdefault(method, set()).add(saved["id"])
    if not examples:
        return None
    examples.sort(key=lambda item: item[0], reverse=True)
    return {"style": style, "team_profile": req.team_profile,
            "methods": {name: sorted(ids) for name, ids in methods.items()},
            "examples": [example for _, example in examples[:2]]}
