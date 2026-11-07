"""First-person public speech derived from actual completed proposals/reviews."""
from src.models import CreativeOpinion, PeerReview


def proposal_speech(opinion: CreativeOpinion) -> str:
    if opinion.customer_response:
        return opinion.customer_response[:240]
    message = opinion.discussion_message.strip()
    if message and "我" in message[:24]:
        speech = message
    else:
        activities = "、".join(opinion.recommended_activities[:2])
        speech = (f"我的提案是《{opinion.concept_name}》。我建议{activities}。" if activities
                  else f"我的提案是《{opinion.concept_name}》。我的主张：{opinion.one_sentence_pitch}")
    if opinion.sharp_remark and opinion.sharp_remark not in speech:
        speech = opinion.sharp_remark + " " + speech
    return speech[:160]


def peer_speech(review: PeerReview) -> str:
    message = review.discussion_message.strip()
    if message and "我" in message[:24]:
        return message[:160]
    # Old records/providers can omit the new field; rephrase only supplied findings.
    parts = []
    if review.challenges:
        challenge = review.challenges[0]
        parts.append(f"{challenge.target_agent}，我对这处安排有个顾虑：{challenge.issue[:55]}")
    elif review.supported_points:
        parts.append(f"我赞同：{review.supported_points[0][:55]}")
    if review.proposed_compromises:
        parts.append(f"我建议这样折中：{review.proposed_compromises[0][:65]}")
    if not parts:
        parts.append(f"我评审后的主张是：{review.updated_position[:110]}")
    return "。".join(parts)[:160]
