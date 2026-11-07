from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}"


class CaseStatus(StrEnum):
    NEW = "new"
    INTAKE = "intake"
    WAITING_REQUIREMENTS = "waiting_requirements"
    WAITING_DRAFT = "waiting_draft"
    WAITING_CUSTOMER = "waiting_customer"
    SUPPLEMENTING = "supplementing"
    ALIGNMENT = "alignment"
    CREATIVE_COUNCIL = "creative_council"
    WAITING_STYLE_FEEDBACK = "waiting_style_feedback"
    CROSS_REVIEW = "cross_review"
    DELIBERATION = "deliberation"
    REVIEWING = "reviewing"
    WAITING_OWNER = "waiting_owner"
    READY_FOR_CUSTOMER = "ready_for_customer"
    NEEDS_REWORK = "needs_rework"
    COMPLETED = "completed"
    FAILED = "failed"


class RequirementSpec(BaseModel):
    event_name: str = "未命名团建"
    city: str | None = None
    event_date: str | None = None
    participant_count: int | None = None
    budget_yuan: float | None = None
    duration: str | None = None
    team_profile: str | None = None
    preferred_style: str | None = None
    desired_outcomes: list[str] = Field(default_factory=list)
    hard_constraints: list[str] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)
    special_needs: list[str] = Field(default_factory=list)


class IntakeResult(BaseModel):
    requirements: RequirementSpec
    summary: str
    blocking_questions: list[str] = Field(default_factory=list)


class AttachmentInsight(BaseModel):
    file_name: str = ""
    media_type: str = ""
    summary: str
    observed_facts: list[str] = Field(default_factory=list)
    preferences_or_intent: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    extracted_text: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)


class AlignmentResult(BaseModel):
    passed: bool
    auto_adjustments: list[str] = Field(default_factory=list)
    blocking_questions: list[str] = Field(default_factory=list)
    non_blocking_notes: list[str] = Field(default_factory=list)


class AgentPersonality(BaseModel):
    name: str
    age: int
    role: str
    background: str
    expertise: list[str]
    values: list[str]
    primary_goal: str
    worldview: str
    communication_style: str
    signature_behaviors: list[str]
    risk_tolerance: float = Field(ge=0, le=1)
    evidence_threshold: float = Field(ge=0, le=1)
    veto_conditions: list[str]
    known_biases: list[str]
    concession_rules: list[str]


class CustomerConcern(BaseModel):
    agent_name: str = ""
    kind: str
    evidence: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)


class CreativeOpinion(BaseModel):
    agent_name: str
    persona_title: str
    concept_name: str
    one_sentence_pitch: str
    recommended_activities: list[str]
    rejected_activities: list[str] = Field(default_factory=list)
    reasons: list[str]
    concerns: list[str] = Field(default_factory=list)
    suitable_participants: list[str] = Field(default_factory=list)
    required_verification: list[str] = Field(default_factory=list)
    possible_concessions: list[str] = Field(default_factory=list)
    client_facing_message: str = ""
    discussion_message: str = ""
    sharp_remark: str = ""
    customer_concerns: list[CustomerConcern] = Field(default_factory=list)
    customer_response: str = ""
    style_strengths: list[str] = Field(default_factory=list)
    style_tradeoffs: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class CreativeRound(BaseModel):
    song_ye: CreativeOpinion
    shen_zhixing: CreativeOpinion
    xia_cheng: CreativeOpinion


class ProposalBrief(BaseModel):
    concept_name: str
    one_sentence_pitch: str
    recommended_activities: list[str]
    reasons: list[str]
    concerns: list[str] = Field(default_factory=list)
    required_verification: list[str] = Field(default_factory=list)
    possible_concessions: list[str] = Field(default_factory=list)
    discussion_message: str = ""
    style_strengths: list[str] = Field(default_factory=list)
    style_tradeoffs: list[str] = Field(default_factory=list)
    customer_concerns: list[CustomerConcern] = Field(default_factory=list)
    confidence: float = Field(default=.7, ge=0, le=1)


class BriefCreativeRound(BaseModel):
    song_ye: ProposalBrief
    shen_zhixing: ProposalBrief
    xia_cheng: ProposalBrief


class CustomerStyleFeedback(BaseModel):
    preferred_agent: str | None = None
    disliked_agent: str | None = None
    preference_reason: str = ""
    question: str = ""
    complaint: str = ""


class FeedbackAssessment(BaseModel):
    feedback_kind: str
    complaint_present: bool = False
    complaint_reasonable: bool | None = None
    rationale: str
    evidence: list[str] = Field(default_factory=list)
    responsible_agents: list[str] = Field(default_factory=list)
    case_constraints: list[str] = Field(default_factory=list)
    organization_constraints: list[str] = Field(default_factory=list)
    customer_response: str
    requires_owner: bool = False


class OrganizationConstraint(BaseModel):
    id: str = Field(default_factory=lambda: new_id("constraint"))
    agent_name: str | None = None
    rule: str
    source_case_id: str
    source_summary: str
    active: bool = True
    times_applied: int = 0
    created_at: datetime = Field(default_factory=datetime.now)


class OrganizationState(BaseModel):
    constraints: list[OrganizationConstraint] = Field(default_factory=list)


class PeerChallenge(BaseModel):
    target_agent: str
    issue: str
    why_it_matters: str
    evidence_or_change_needed: str
    severity: str


class PeerReview(BaseModel):
    reviewer_name: str
    supported_points: list[str] = Field(default_factory=list)
    challenges: list[PeerChallenge] = Field(default_factory=list)
    proposed_compromises: list[str] = Field(default_factory=list)
    position_changed: bool = False
    updated_position: str
    discussion_message: str = ""
    customer_concerns: list[CustomerConcern] = Field(default_factory=list)


class PeerReviewRound(BaseModel):
    song_ye: PeerReview
    shen_zhixing: PeerReview
    xia_cheng: PeerReview


class AdoptedContribution(BaseModel):
    agent_name: str
    source_kind: str = "activity"
    source_index: int = Field(ge=0)
    schedule_index: int = Field(ge=0)
    reason: str


class ContributionShare(BaseModel):
    agent_name: str
    weight: float = Field(ge=0, le=1)
    evidence: list[str] = Field(default_factory=list)


class PayAdjustment(BaseModel):
    agent_name: str
    amount_yuan: float
    reason: str
    status: str = "pending_owner"


class LearningScope(BaseModel):
    city: str | None = None
    participant_count: int | None = None
    budget_yuan: float | None = None
    preferred_style: str | None = None
    team_profile: str | None = None
    duration: str | None = None
    hard_constraints: list[str] = Field(default_factory=list)
    special_needs: list[str] = Field(default_factory=list)


class FeedbackLesson(BaseModel):
    id: str
    event_id: str
    kind: Literal["success", "check"]
    source_case_id: str
    source_version: int
    source_stage: str
    title: str
    action: str
    evidence: list[str] = Field(default_factory=list)
    source_schedule: list[str] = Field(default_factory=list)
    agent_names: list[str] = Field(default_factory=list)
    scope: LearningScope = Field(default_factory=LearningScope)
    active: bool = True
    supplied_cases: int = 0
    created_at: datetime = Field(default_factory=datetime.now)


class AppliedLesson(BaseModel):
    lesson: FeedbackLesson
    match_reason: str
    supplied_to: list[str] = Field(default_factory=list)


class LearningResponse(BaseModel):
    lesson_id: str
    decision: Literal["adopted", "adapted", "not_applicable"]
    detail: str
    schedule_index: int | None = Field(default=None, ge=0)


class CouncilDecision(BaseModel):
    theme: str
    summary: str
    schedule: list[str]
    merged_ideas: list[str]
    rejected_ideas: list[str] = Field(default_factory=list)
    rationale: list[str]
    operating_principles: list[str] = Field(default_factory=list)
    items_to_verify: list[str] = Field(default_factory=list)
    customer_notice: list[str] = Field(default_factory=list)
    unresolved_customer_decisions: list[str] = Field(default_factory=list)
    adopted_contributions: list[AdoptedContribution] = Field(default_factory=list)
    customer_concerns: list[CustomerConcern] = Field(default_factory=list)
    learning_responses: list[LearningResponse] = Field(default_factory=list)
    completion_conditions: list[str] = Field(default_factory=list)


class ReviewFinding(BaseModel):
    severity: str
    description: str
    can_auto_fix: bool = False
    decision_owner: str | None = None
    blocking: bool | None = None
    evidence: str = ""
    release_condition: str = ""


class ReviewResult(BaseModel):
    reviewer_name: str
    passed: bool
    findings: list[ReviewFinding] = Field(default_factory=list)
    revision_instructions: list[str] = Field(default_factory=list)


class ManagerReview(BaseModel):
    operations: ReviewResult
    risk: ReviewResult
    completion_conditions: list[str] = Field(default_factory=list)


class FeasibilityFinding(ReviewFinding):
    evidence: str
    release_condition: str
    alternatives: list[str] = Field(default_factory=list)


class FeasibilityReview(BaseModel):
    summary: str
    findings: list[FeasibilityFinding] = Field(default_factory=list)
    pending_resources: list[str] = Field(default_factory=list)


class ManagedPlan(BaseModel):
    plan: CouncilDecision
    checks: ManagerReview
    resolutions: list[str] = Field(default_factory=list)


class ValidatedResult(BaseModel):
    key: str
    response_type: str
    payload: dict
    created_at: float
    expires_at: float


class TaskHandoff(BaseModel):
    id: str = Field(default_factory=lambda: new_id("task"))
    case_id: str
    sender: str
    receiver: str
    task: str
    input_summary: str
    expected_result: str
    status: str = "running"
    result_summary: str = ""
    error: str = ""
    elapsed_seconds: float = 0
    source: str = "agent"
    created_at: datetime = Field(default_factory=datetime.now)


class ApprovalRequest(BaseModel):
    id: str = Field(default_factory=lambda: new_id("approval"))
    title: str
    reasons: list[str]
    approved: bool | None = None
    comment: str | None = None


class PerformanceMetric(BaseModel):
    name: str
    weight: float = Field(ge=0, le=1)
    description: str
    score: float = Field(default=70, ge=0, le=100)


class PersonalityChange(BaseModel):
    event_id: str
    signal: str
    reason: str
    correction_probability: float | None = None
    draw: float | None = None
    corrected: bool = False
    before: dict[str, int] = Field(default_factory=dict)
    after: dict[str, int] = Field(default_factory=dict)
    response: str
    timestamp: datetime = Field(default_factory=datetime.now)


class PersonalityState(BaseModel):
    style_intensity: int = Field(default=85, ge=40, le=100)
    flaw_visibility: int = Field(default=75, ge=0, le=100)
    self_correction: int = Field(default=15, ge=0, le=100)
    frustration: int = Field(default=0, ge=0, le=100)
    history: list[PersonalityChange] = Field(default_factory=list)


class ToneDecision(BaseModel):
    probability: float = Field(ge=0, le=1)
    draw: float = Field(ge=0, le=1)
    triggered: bool
    reason: str
    remark: str = ""


class CustomerObjection(BaseModel):
    event_id: str
    kind: str = "requirement_conflict"
    agent_name: str
    stage: str
    evidence: list[str]
    alternatives: list[str]
    message: str
    tone: ToneDecision
    source: str


class AgentScorecard(BaseModel):
    agent_name: str
    role: str
    trust_score: float = Field(default=70, ge=0, le=100)
    completed_cases: int = 0
    metrics: list[PerformanceMetric]
    strengths: list[str] = Field(default_factory=list)
    improvement_notes: list[str] = Field(default_factory=list)
    pending_pay_adjustment_yuan: float = 0
    personality_state: PersonalityState | None = None

    @field_validator("agent_name", mode="before")
    @classmethod
    def normalize_project_manager_name(cls, value):
        # Read legacy profiles as the same business role, without a duplicate card.
        return "项目经理" if value == "运营经理" else value


class OutcomeFeedback(BaseModel):
    overall_rating: int = Field(ge=1, le=5)
    novelty_rating: int = Field(ge=1, le=5)
    culture_rating: int = Field(ge=1, le=5)
    detail_rating: int = Field(ge=1, le=5)
    engagement_rating: int = Field(ge=1, le=5)
    feasibility_rating: int = Field(ge=1, le=5)
    incidents: list[str] = Field(default_factory=list)
    comment: str = ""


class PlanFeedbackOption(BaseModel):
    id: str
    label: str
    metric: str


class PlanFeedbackOptions(BaseModel):
    satisfied: list[PlanFeedbackOption] = Field(default_factory=list)
    acceptable: list[PlanFeedbackOption] = Field(default_factory=list)
    unacceptable: list[PlanFeedbackOption] = Field(default_factory=list)


class PlanAcceptanceFeedback(BaseModel):
    outcome: str
    selected_option_ids: list[str] = Field(default_factory=list)
    selected_labels: list[str] = Field(default_factory=list)
    comment: str = ""


class AuditEvent(BaseModel):
    timestamp: datetime = Field(default_factory=datetime.now)
    actor: str
    action: str
    detail: str


class PlanFeedbackRecord(BaseModel):
    version: int
    plan: CouncilDecision | None = None
    feedback: PlanAcceptanceFeedback
    ratings: OutcomeFeedback | None = None  # Historical summaries before feedback stored the original choice.
    contributions: list[ContributionShare] = Field(default_factory=list)
    pay_adjustments: list[PayAdjustment] = Field(default_factory=list)
    personality_after: dict[str, PersonalityState] = Field(default_factory=dict)
    lessons: list[FeedbackLesson] = Field(default_factory=list)


class PublicUpdate(BaseModel):
    actor: str
    stage: str
    message: str
    source: str = "agent_business_summary"
    timestamp: datetime = Field(default_factory=datetime.now)


class JourneyAdjustment(BaseModel):
    schedule_index: int = Field(ge=0)
    replacement: str


class JourneyResponse(BaseModel):
    summary: str
    needs_change: bool
    adjustments: list[JourneyAdjustment] = Field(default_factory=list)
    organizer_notice: str
    requires_owner: bool = False
    cost_note: str = "演示调整，费用及场地尚未核实"
    handoffs: list[TaskHandoff] = Field(default_factory=list)
    additional_cost_yuan: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class ExecutionTriage(BaseModel):
    summary: str
    needs_replan: bool
    affected_indices: list[int] = Field(default_factory=list)
    requires_owner: bool = False


class JourneyEvent(BaseModel):
    id: str = Field(default_factory=lambda: new_id("event"))
    kind: str
    tick: int
    description: str
    status: str = "detected"
    response: JourneyResponse | None = None
    response_source: str = ""
    error: str = ""
    source: str = "simulated_event"
    confirmed_cost_yuan: float | None = Field(default=None, ge=0)
    cost_confirmation_required: bool = False


class JourneyDemo(BaseModel):
    context_kind: str = "travel"
    manual_index: int | None = None
    activity_context: str = ""
    order_id: str = Field(default_factory=lambda: new_id("demo_order"))
    status: str = "ready"
    tick: int = 0
    last_step_at: float = 0
    plan_version: int
    original_schedule: list[str]
    active_schedule: list[str]
    events: list[JourneyEvent] = Field(default_factory=list)
    handoffs: list[TaskHandoff] = Field(default_factory=list)
    runtime_error: str = ""
    task_attempt: int = 0


class ResourceReservation(BaseModel):
    kind: str
    supplier: str
    status: str = "pending"
    requested_quantity: int = 1
    available_quantity: int = 0
    note: str = ""
    confirmation_id: str = ""


class ResourcePackage(BaseModel):
    id: str
    label: str
    hotel: str
    hotel_grade: int
    same_hotel: bool = True
    available_rooms: int
    transfer_minutes: int
    venue_cost: float
    vehicle_cost: float
    hotel_cost: float
    other_cost: float
    note: str = ""


class BookingFinancialReview(BaseModel):
    """Financial commentary has no resource-selection or itinerary-edit authority."""
    model_config = {"extra": "forbid"}
    message: str


class BookingSpeech(BaseModel):
    message: str
    package_id: str | None = None
    adjustments: list[JourneyAdjustment] = Field(default_factory=list)
    handoffs: list[TaskHandoff] = Field(default_factory=list)


class BookingDiscussion(BaseModel):
    actor: str
    stage: str
    public_message: str
    owner_message: str
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)


class SimulatedPayment(BaseModel):
    id: str
    kind: str
    amount_yuan: float
    source: str = "模拟支付，无真实交易"


class ProcurementOrder(BaseModel):
    id: str = Field(default_factory=lambda: new_id("mock_booking"))
    plan_version: int
    status: str = "processing"
    step: int = 0
    last_step_at: float = 0
    quote_yuan: float
    deposit_yuan: float
    baseline_cost: float
    room_count: int
    nights: int
    confirmed_hotel_grade: int
    confirmed_same_hotel: bool = True
    confirmed_max_transfer: int = 90
    auto_min_margin: float = 0.12
    auto_max_extra_cost_ratio: float = 0.05
    auto_max_added_transfer: int = 20
    reservations: list[ResourceReservation] = Field(default_factory=list)
    packages: list[ResourcePackage] = Field(default_factory=list)
    selected_package: str = "original"
    owner_approved_package: str | None = None
    customer_approved_package: str | None = None
    payments: list[SimulatedPayment] = Field(default_factory=list)
    discussion: list[BookingDiscussion] = Field(default_factory=list)
    case_id: str = ""
    original_schedule: list[str] = Field(default_factory=list)
    execution_schedule: list[str] = Field(default_factory=list)
    schedule_approved: bool = False
    handoffs: list[TaskHandoff] = Field(default_factory=list)
    opening_cash_yuan: float = 46000
    settled: bool = False
    runtime_error: str = ""
    task_attempt: int = 0


class ActivitySource(BaseModel):
    file_name: str
    text: str
    warnings: list[str] = Field(default_factory=list)


class ActivityFact(BaseModel):
    label: str
    value: str
    source_quote: str


class ActivityFinding(BaseModel):
    issue: str
    evidence: str
    suggestion: str


class ActivityAudit(BaseModel):
    event_name: str
    summary: str
    facts: list[ActivityFact] = Field(default_factory=list)
    findings: list[ActivityFinding] = Field(default_factory=list)


class ActivityTask(BaseModel):
    phase: str
    task: str
    suggested_owner: str
    timing: str
    deliverable: str


class ActivityExecutionPlan(BaseModel):
    summary: str
    tasks: list[ActivityTask] = Field(min_length=1)
    run_sheet: list[str] = Field(default_factory=list)
    organizer_questions: list[str] = Field(default_factory=list, max_length=2)


class ActivityReviewReport(BaseModel):
    audit: ActivityAudit
    execution: ActivityExecutionPlan
    program_checks: list[ActivityFinding] = Field(default_factory=list)


class DocumentIntakeResult(BaseModel):
    document_kind: str = Field(description="existing_plan、requirements或uncertain")
    summary: str
    requirements: RequirementSpec
    venue: str | None = None


class TravelCase(BaseModel):
    document_intake: DocumentIntakeResult | None = None
    document_venue: str = ""
    service_mode: str = "travel_planning"
    activity_sources: list[ActivitySource] = Field(default_factory=list)
    activity_review: ActivityReviewReport | None = None
    activity_review_history: list[ActivityReviewReport] = Field(default_factory=list)
    activity_review_notes: str = ""
    handoffs: list[TaskHandoff] = Field(default_factory=list)
    result_cache: list[ValidatedResult] = Field(default_factory=list)
    storage_revision: int = 0
    planning_mode: str = "legacy_independent"
    style_template: dict | None = None
    customer_supplements: list[str] = Field(default_factory=list)
    pending_supplement: str = ""
    supplement_origin: CaseStatus | None = None
    id: str = Field(default_factory=lambda: new_id("case"))
    web_customer_id: str | None = None
    status: CaseStatus = CaseStatus.NEW
    customer_request: str
    requirements: RequirementSpec | None = None
    lead_agent: str | None = None
    attachment_insights: list[AttachmentInsight] = Field(default_factory=list)
    customer_questions: list[str] = Field(default_factory=list)
    clarification_rounds: int = 0
    customer_answer_pending: bool = False
    resume_status: CaseStatus | None = None
    pending_customer_step: CaseStatus | None = None
    pending_owner_step: CaseStatus | None = None
    draft_revision_count: int = 0

    creative_opinions: list[CreativeOpinion] = Field(default_factory=list)
    proposal_completion_order: list[str] = Field(default_factory=list)
    contribution_shares: list[ContributionShare] = Field(default_factory=list)
    pay_adjustments: list[PayAdjustment] = Field(default_factory=list)
    style_feedback: CustomerStyleFeedback | None = None
    personality_snapshots: dict[str, PersonalityState] = Field(default_factory=dict)
    tone_decisions: dict[str, ToneDecision] = Field(default_factory=dict)
    customer_objections: list[CustomerObjection] = Field(default_factory=list)
    customer_frustration: dict[str, int] = Field(default_factory=dict)
    draft_change_requests: list[str] = Field(default_factory=list)
    feedback_assessment: FeedbackAssessment | None = None
    applied_constraints: list[str] = Field(default_factory=list)
    learning_loaded: bool = False
    learning_scope_key: str = ""
    learning_applications: list[AppliedLesson] = Field(default_factory=list)
    history_references: list[dict] = Field(default_factory=list)
    history_search_key: str = ""
    history_search_backend: str = ""
    history_search_notice: str = ""
    peer_reviews: list[PeerReview] = Field(default_factory=list)
    planning_protocol: str = "legacy"
    feasibility_review: FeasibilityReview | None = None
    feasibility_fingerprint: str = ""
    feasibility_cached_at: float = 0
    manager_checks: ManagerReview | None = None
    manager_checks_source: str = "manager"
    manager_check_fingerprint: str = ""
    manager_checks_cached_at: float = 0
    cache_policy_fingerprint: str = ""
    manager_resolutions: list[str] = Field(default_factory=list)
    final_recheck_required: bool = False
    council_decision: CouncilDecision | None = None
    reviews: list[ReviewResult] = Field(default_factory=list)
    revision_count: int = 0

    approval: ApprovalRequest | None = None
    scorecards: list[AgentScorecard] = Field(default_factory=list)
    outcome_feedback: OutcomeFeedback | None = None
    delivery_feedback: OutcomeFeedback | None = None
    plan_feedback_options: PlanFeedbackOptions | None = None
    plan_acceptance_feedback: PlanAcceptanceFeedback | None = None
    plan_version: int = 1
    feedback_history: list[PlanFeedbackRecord] = Field(default_factory=list)

    audit_log: list[AuditEvent] = Field(default_factory=list)
    public_updates: list[PublicUpdate] = Field(default_factory=list)
    error_message: str | None = None
    journey_demo: JourneyDemo | None = None
    journey_history: list[JourneyDemo] = Field(default_factory=list)
    procurement_order: ProcurementOrder | None = None
    booking_route: Literal["", "supplier"] = ""
    supplier_order_id: str | None = None
    supplier_execution_schedule: list[str] = Field(default_factory=list)
    supplier_execution_signature: str = ""

