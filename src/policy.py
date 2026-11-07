from pydantic import BaseModel

from src.models import ReviewResult, ReviewFinding


class CompanyPolicy(BaseModel):
    max_clarification_rounds: int = 2
    max_revision_count: int = 2
    high_risk_requires_owner: bool = True


def owner_approval_reasons(
    reviews: list[ReviewResult],
    policy: CompanyPolicy,
) -> list[str]:
    reasons: list[str] = []
    for review in reviews:
        for finding in review.findings:
            if finding.decision_owner == "owner" or (
                policy.high_risk_requires_owner
                and finding.decision_owner != "customer"
                and finding.severity == "high" and not finding.can_auto_fix
            ):
                reasons.append(f"{review.reviewer_name}：{finding.description}")
    return list(dict.fromkeys(reasons))


def blocks_review(review, finding):
    if finding.decision_owner in {"customer", "owner"} or finding.severity == "high":
        return True
    if finding.blocking is not None:
        return finding.blocking
    return not (review.passed and finding.severity == "low")


def program_review(case):
    findings = []
    if not case.council_decision or not case.council_decision.schedule:
        findings.append(ReviewFinding(severity="high", description="程序检查：缺少可执行行程",
            decision_owner="owner", can_auto_fix=False))
    if case.council_decision and case.council_decision.unresolved_customer_decisions:
        findings.extend(ReviewFinding(severity="medium", description=q, decision_owner="customer")
            for q in case.council_decision.unresolved_customer_decisions)
    if case.requirements and (case.requirements.participant_count is not None and case.requirements.participant_count <= 0):
        findings.append(ReviewFinding(severity="high", description="程序检查：参与人数必须为正数", decision_owner="customer"))
    return ReviewResult(reviewer_name="程序硬性检查", passed=not findings, findings=findings)

