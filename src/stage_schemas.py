"""Small model-facing stage contracts; adapters preserve stored case interfaces."""
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict

from src.models import (AdoptedContribution, CouncilDecision, CustomerConcern,
    LearningResponse, ManagedPlan, ManagerReview, ReviewFinding, ReviewResult)
from src.policy import blocks_review


class StageContract(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class ProjectPlan(StageContract):
    theme: str
    summary: str
    schedule: list[str] = Field(min_length=1)
    basis: list[str] = Field(min_length=1, max_length=4)
    completion_conditions: list[str] = Field(min_length=1, max_length=5)
    pending_resources: list[str] = Field(default_factory=list)
    customer_questions: list[str] = Field(default_factory=list)
    customer_notice: list[str] = Field(default_factory=list)
    adopted_contributions: list[AdoptedContribution] = Field(default_factory=list)
    learning_responses: list[LearningResponse] = Field(default_factory=list)
    customer_concerns: list[CustomerConcern] = Field(default_factory=list)


class ProjectIssue(StageContract):
    domain: Literal["operations", "risk"]
    severity: Literal["low", "medium", "high"]
    description: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    release_condition: str = Field(min_length=1)
    decision_owner: Literal["owner", "customer"] | None = None
    can_auto_fix: bool = False
    blocking: bool | None = None


class ProjectReview(StageContract):
    findings: list[ProjectIssue] = Field(default_factory=list)
    completion_conditions: list[str] = Field(min_length=1, max_length=5)


class ProjectDraft(StageContract):
    plan: ProjectPlan
    findings: list[ProjectIssue] = Field(default_factory=list)


WIRE_MODELS = {ManagedPlan: ProjectDraft, ManagerReview: ProjectReview, CouncilDecision: ProjectPlan}


def wire_model(response_model):
    return WIRE_MODELS.get(response_model, response_model)


def _plan(value):
    return CouncilDecision(theme=value.theme, summary=value.summary, schedule=value.schedule,
        merged_ideas=[], rationale=value.basis, items_to_verify=value.pending_resources,
        unresolved_customer_decisions=value.customer_questions, customer_notice=value.customer_notice,
        adopted_contributions=value.adopted_contributions, learning_responses=value.learning_responses,
        customer_concerns=value.customer_concerns, completion_conditions=value.completion_conditions)


def _checks(findings, conditions):
    reviews = []
    for domain in ("operations", "risk"):
        items = [ReviewFinding(**f.model_dump(exclude={"domain"})) for f in findings if f.domain == domain]
        review = ReviewResult(reviewer_name="运营检查" if domain == "operations" else "风险检查",
            passed=True, findings=items)
        review.passed = not any(blocks_review(review, f) for f in items)
        reviews.append(review)
    return ManagerReview(operations=reviews[0], risk=reviews[1], completion_conditions=conditions)


def decode_response(response_model, payload):
    # Old valid payloads can still be returned by older models/prompts. No repair call.
    legacy = ((response_model is ManagedPlan and "checks" in payload and "findings" not in payload)
        or (response_model is ManagerReview and "operations" in payload and "findings" not in payload)
        or (response_model is CouncilDecision and "rationale" in payload and "basis" not in payload))
    if legacy or response_model not in WIRE_MODELS:
        return response_model.model_validate(payload)
    plan = payload.get("plan") if response_model is ManagedPlan else payload
    if response_model in {ManagedPlan, CouncilDecision} and isinstance(plan, dict):
        basis = plan.get("basis")
        if isinstance(basis, list) and len(basis) > 4 and all(isinstance(item, str) for item in basis):
            plan = {**plan, "basis": basis[:4]}
            payload = {**payload, "plan": plan} if response_model is ManagedPlan else plan
    value = wire_model(response_model).model_validate(payload)
    if response_model is CouncilDecision:
        return _plan(value)
    if response_model is ManagerReview:
        return _checks(value.findings, value.completion_conditions)
    return ManagedPlan(plan=_plan(value.plan),
        checks=_checks(value.findings, value.plan.completion_conditions), resolutions=value.plan.basis)


def compact_schema(value):
    """Drop display-only titles/defaults, never field types or validation rules."""
    if isinstance(value, dict):
        return {k: ({name: compact_schema(node) for name, node in v.items()}
                    if k in {"properties", "$defs"} else compact_schema(v))
                for k, v in value.items() if k not in {"title", "default"}}
    if isinstance(value, list):
        return [compact_schema(x) for x in value]
    return value
