"""Trace the final schedule back to documented proposals or compromises."""
from src.models import TravelCase


def adoption_evidence(case: TravelCase) -> list[dict[str, str]]:
    plan = case.council_decision
    if plan is None:
        return []
    opinions = {x.agent_name: x for x in case.creative_opinions}
    reviews = {x.reviewer_name: x for x in case.peer_reviews}
    evidence = []
    seen = set()
    for item in plan.adopted_contributions:
        key = (item.agent_name, item.source_kind, item.source_index, item.schedule_index)
        if key in seen or not 0 <= item.schedule_index < len(plan.schedule):
            continue
        if item.source_kind == "activity" and item.agent_name in opinions:
            sources = opinions[item.agent_name].recommended_activities
        elif item.source_kind == "compromise" and item.agent_name in reviews:
            sources = reviews[item.agent_name].proposed_compromises
        else:
            continue
        if not 0 <= item.source_index < len(sources):
            continue
        seen.add(key)
        evidence.append({"来源角色": item.agent_name, "原建议": sources[item.source_index],
                         "对应行程": plan.schedule[item.schedule_index], "采纳原因": item.reason})
    return evidence


