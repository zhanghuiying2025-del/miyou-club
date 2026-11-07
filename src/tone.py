"""Reproducible, bounded theatrical bluntness based on recorded dissatisfaction."""
import hashlib

from src.models import PersonalityState, ToneDecision


BASE_PROBABILITIES = {"宋野": 0.70, "沈知行": 0.15, "夏橙": 0.40}
REMARKS = {
    "宋野": (
        "我不想拿一份毫无记忆点的流水账来糊弄这次团建。",
        "我有点不服气：把所有棱角都磨平，最后只会剩下一份没劲的行程。",
        "我直说了，只求四平八稳、毫无特色的方案，我拿不出手。",
    ),
    "沈知行": (
        "我愿意改，但我不接受把认真设计当成多余；只有热闹、没有内容的行程，我拿不出手。",
        "我可以把讲解缩短，但不能把文化内容删成几句空话，那是在敷衍这份方案。",
    ),
    "夏橙": (
        "我会调整，但硬拉着所有人配合来制造气氛，我觉得那是自欺欺人。",
        "我直说了，靠喊口号和强行互动凑出来的热闹，根本不算参与感。",
    ),
}


def decide_tone(case_id: str, name: str, state: PersonalityState | None) -> ToneDecision:
    digest = hashlib.sha256(f"tone-v1:{case_id}:{name}".encode()).digest()
    draw = int.from_bytes(digest[:8], "big") / 2**64
    frustration = state.frustration if state else 0
    negative = next((x for x in reversed(state.history) if x.signal in {"penalty", "setback"}), None) if state else None
    # Compatibility for previously recorded feedback predating the frustration field.
    if state and state.history and "frustration" not in state.history[-1].after and state.history[-1].signal in {"penalty", "setback"}:
        frustration = max(frustration, 20 if state.history[-1].corrected else 30)
    probability = 0.0
    if frustration > 0 and name in BASE_PROBABILITIES and state:
        probability = BASE_PROBABILITIES[name] * (0.4 + 0.6 * frustration / 100) * (1 - state.self_correction / 150)
    probability = round(probability, 4)
    triggered = draw < probability
    choices = REMARKS.get(name, ("",))
    remark = choices[digest[8] % len(choices)] if triggered else ""
    reason = f"不满程度{frustration}/100；自我修正{state.self_correction if state else 0}/100"
    if negative:
        reason += f"；负面反馈来源：{negative.event_id}，{negative.reason[:120]}"
    return ToneDecision(probability=probability, draw=draw, triggered=triggered, reason=reason, remark=remark)


def tone_instructions(decision: ToneDecision) -> str:
    if not decision.triggered:
        return "本轮没有触发不满重话。保持原有人设，不刻意添加尖刻台词。"
    return f"""本轮触发了不满时的强硬表达，角色台词为：“{decision.remark}”
保留第一人称和你的专业立场，可以直率、有情绪，不必把所有话都改成客服套话。
程序会单独展示上述台词，client_facing_message 和 discussion_message 接续具体活动建议，不重复台词。
重话只针对方案取舍；不捏造本案经历或其他人的回复，不把前一单的不满归咎于当前客户。
这不改变任何客户硬性要求、权限或公司长期约束。互评时可保留锋芒，但不要反复重复同一句抱怨。
"""


def apply_tone_constraints(decision: ToneDecision, rules: list[str]) -> ToneDecision:
    """Conservative veto for an explicit request to restrain communication tone."""
    for rule in rules:
        explicit = any(word in rule for word in ("不要说重话", "禁止重话", "不得尖刻", "不接受尖刻", "禁止强硬表达", "不接受强硬"))
        restrained = any(word in rule for word in ("语气", "表达", "措辞", "沟通")) and any(
            word in rule for word in ("温和", "礼貌", "克制", "文明", "不得", "避免", "禁止", "不应", "不要"))
        if explicit or restrained:
            return decision.model_copy(update={
                "probability": 0.0, "triggered": False, "remark": "",
                "reason": decision.reason + f"；已被表达约束屏蔽（原概率{decision.probability:.1%}）：{rule[:100]}",
            })
    return decision
