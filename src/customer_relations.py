"""Evidence-linked, case-local reactions directed to the current customer."""
from src.models import CustomerConcern, CustomerObjection, PersonalityState, TravelCase
from src.tone import decide_tone, apply_tone_constraints


CONCERN_INSTRUCTIONS = """
customer_concerns 记录需要向当前客户直接质疑的具体要求矛盾、范围取舍或连续换方向引发的主观挫败，最多1项。
agent_name 标明持有该专业异议的策划师；协调者只能基于已有提案/互评归属，不得无依据替某人表达不满。
kind 只能是 requirement_conflict（要求冲突）、scope_change（新增范围与已确认条件冲突）
或 repeated_revision（至少两次实际修改记录中的方向变化，只表达员工主观挫败，不指责客户有错）。
evidence 给出两段不同的客户原话短引文，必须逐字来自客户需求/本次反馈；至少一段来自本次新要求。
alternatives 给出两个简短且不同的可选方向，不替客户决定。
不得因客户不喜欢你的风格、正常修改偏好、合理投诉或暂不决定而判定其不合理；连续改方向可以表达挫败，但不能说客户的要求必然矛盾。
不得将你自己的方案缺陷归咎于客户，不推测客户恶意，不以未核实价格宣称预算必然不足。
若缺少具体依据，返回空列表。这里只是待客户澄清的角色异议，不是公司对客户的过错裁定。
"""


def customer_corpus(case: TravelCase) -> str:
    return "\n".join([
        case.customer_request,
        case.requirements.model_dump_json() if case.requirements else "",
        *case.draft_change_requests,
        case.style_feedback.model_dump_json() if case.style_feedback else "",
    ])


def record_customer_objection(case: TravelCase, name: str, concern: CustomerConcern,
                              interaction: str, stage: str, current_text: str,
                              rules: list[str], source: str) -> CustomerObjection | None:
    if name not in {"宋野", "沈知行", "夏橙"} or concern.kind not in {"requirement_conflict", "scope_change", "repeated_revision"}:
        return None
    repeated = concern.kind == "repeated_revision"
    if repeated and len(case.draft_change_requests) < 2:
        return None
    evidence = list(dict.fromkeys(x.strip() for x in concern.evidence if x.strip()))
    alternatives = list(dict.fromkeys(x.strip() for x in concern.alternatives if x.strip()))
    corpus = customer_corpus(case) + "\n" + current_text
    if len(evidence) < 2 or len(alternatives) < 2:
        return None
    evidence = evidence[:2]
    if any(len(x) < 4 or x not in corpus for x in evidence) or not any(x in current_text for x in evidence):
        return None
    if concern.kind == "scope_change" and not any(x in customer_corpus(case) for x in evidence):
        return None
    # One exchange is one frustration event; redundant reports cannot inflate it.
    event_id = f"{interaction}:{name}"
    existing = next((x for x in case.customer_objections if x.event_id == event_id), None)
    if existing:
        return existing
    previous = sum(x.agent_name == name for x in case.customer_objections)
    level = min(100, case.customer_frustration.get(name, 0) + (15 if repeated else 25))
    frozen = case.personality_snapshots.get(name)
    state = frozen.model_copy(deep=True) if frozen else PersonalityState()
    # This customer's reaction excludes carry-over frustration from other orders.
    state.frustration, state.history = level, []
    tone = decide_tone(f"customer:{case.id}:{interaction}", name, state)
    tone = apply_tone_constraints(tone, rules)
    if repeated:
        opening = ({"宋野": "你当然可以改主意，但我得直说：反复换方向让我有些不满，我不想一直推翻重来。",
                    "沈知行": "我尊重你调整偏好，但反复推翻主线确实让我有些挫败，我希望先固定一个目标。",
                    "夏橙": "我愿意陪你调整，但方向一直换，我也会有些泄气。咱们先定下最重要的一件事。"}[name]
                   if tone.triggered else "我可以继续调整，但我想先和你固定一个优先目标，减少重复返工。")
    elif tone.triggered:
        if name == "宋野":
            opening = ("你可以改主意，但我得直说：这些要求不能同时成立，我不想一轮轮改完又回到原点。" if previous else
                       "我直说了，你这两项要求我没法照单全收，不能什么都要又不做取舍。")
        elif name == "沈知行":
            opening = "我理解你的期待，但我得直接指出：继续叠加矛盾要求，解决不了这个问题。"
        else:
            opening = "我愿意调整，但我也得直说：你提出的这些安排需要取舍，不能靠大家硬配合。"
    else:
        opening = {"宋野": "这里我不同意直接照办，我需要你先明确取舍。",
                   "沈知行": "我需要先向你确认，这两项要求之间有个矛盾。",
                   "夏橙": "我想先提醒你，这样安排会让两项要求互相冲突。"}[name]
    relation = "这次调整为" if repeated else "同时又要求"
    message = f"{opening}你提出了“{evidence[0][:25]}”，{relation}“{evidence[1][:25]}”。我建议二选一：{alternatives[0][:22]}；或{alternatives[1][:22]}。"
    tone.remark = opening if tone.triggered else ""
    tone.reason = f"本案客户要求引发的不满{level}/100，第{previous + 1}次有引文支持的角色异议；" + tone.reason
    objection = CustomerObjection(event_id=event_id, kind=concern.kind, agent_name=name, stage=stage,
        evidence=evidence, alternatives=alternatives[:2], message=message,
        tone=tone, source=source)
    case.customer_frustration[name] = level
    case.customer_objections.append(objection)
    return objection
