"""Business adapters and public projections for the React entry points."""
import asyncio
from datetime import date, timedelta
import logging
import re
from typing import Literal

from pydantic import BaseModel, Field, UUID4, field_validator, model_validator

from src import storage, supplier_simulation as suppliers
from src.agents import AgentConfigurationError, AgentOutputError, build_agent_team
from src.models import (CaseStatus, CustomerStyleFeedback, OutcomeFeedback, PlanAcceptanceFeedback,
                          RequirementSpec, WeatherForecast)
from src.operations import register_lead
from src.order_labels import case_number
from src.runtime import active_jobs, submit_once
from src.supplier_models import BookingNeed, MealSlot
from src.workflow import TravelAgencyWorkflow, WorkflowError
from src import weather

LOGGER = logging.getLogger(__name__)
WORKING = {
    CaseStatus.NEW, CaseStatus.INTAKE, CaseStatus.ALIGNMENT, CaseStatus.SUPPLEMENTING,
    CaseStatus.CREATIVE_COUNCIL, CaseStatus.CROSS_REVIEW, CaseStatus.DELIBERATION, CaseStatus.REVIEWING,
}


class Brief(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    people: int = Field(ge=1, le=2000)
    team: str = Field(default="", max_length=100)
    city: str = Field(min_length=1, max_length=100)
    destination: str = Field(default="", max_length=100)
    event_city: str = Field(default="", max_length=100)
    weather: WeatherForecast | None = None
    date: date
    end_date: date | None = None
    flexible: bool = False
    # Legacy input is accepted for old drafts, but the UI now derives duration from dates.
    duration: Literal["半天", "1天", "2天1晚", "3天2晚"] | None = None
    budget: int = Field(ge=50, le=100000)
    goals: list[str] = Field(default_factory=list, max_length=10)
    attractions: list[str] = Field(default_factory=list, max_length=5)
    notes: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def check_date_range(self):
        end = self.end_date or self.date
        if end < self.date:
            raise ValueError("结束日期不能早于出发日期")
        if (end - self.date).days > 15:
            raise ValueError("单次活动最多安排16天")
        return self

    @field_validator("name", "city", "event_city", mode="before")
    @classmethod
    def trim_required(cls, value):
        return value.strip() if isinstance(value, str) else value

    def requirements(self):
        start = self.date
        legacy_nights = {"2天1晚": 1, "3天2晚": 2}.get(self.duration, 0)
        end = self.end_date or start + timedelta(days=legacy_nights)
        days = (end - start).days + 1
        nights = max(days - 1, 0)
        derived_duration = f"{days}天" + (f"{nights}晚" if nights else "")
        details = [f"出发城市：{self.city}", self.notes]
        if self.destination:
            details.append(f"目的地偏好：{self.destination}")
        if self.attractions:
            details.append("偏好景点：" + "、".join(self.attractions))
        if self.flexible:
            details.append("活动日期可以协商，报价按本次确认日期计算")
        return RequirementSpec(
            event_name=self.name, city=self.event_city or self.city, destination=self.destination or None,
            event_date=self.date.isoformat(), end_date=end.isoformat(), weather=self.weather,
            participant_count=self.people, duration=(self.duration if self.end_date is None and self.duration else derived_duration),
            budget_yuan=self.people * self.budget,
            team_profile=self.team, desired_outcomes=self.goals, attractions=self.attractions,
            preferred_style="、".join(self.goals), preferences=[x for x in details if x],
        )


class CreateActivity(BaseModel):
    request_id: UUID4
    brief: Brief
    utm_campaign: str | None = None
    utm_source: str | None = None

    @field_validator("utm_campaign", "utm_source", mode="before")
    @classmethod
    def normalize_utm(cls, value):
        value = value.strip() if isinstance(value, str) else ""
        return value if re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value) else None


class Action(BaseModel):
    revision: int = Field(ge=0)
    action: str
    note: str = Field(default="", max_length=2000)
    outcome: Literal["satisfied", "acceptable", "unacceptable"] = "satisfied"
    options: list[str] = Field(default_factory=list, max_length=50)
    order_revision: int | None = Field(default=None, ge=1)
    signature: str | None = Field(default=None, max_length=200)
    meal_names: list[Literal["早餐", "午餐", "晚餐"]] = Field(default_factory=lambda: ["午餐"], max_length=3)
    include_hotel: bool = False
    men: int = Field(default=0, ge=0, le=2000)
    women: int = Field(default=0, ge=0, le=2000)
    grade: int = Field(default=2, ge=1, le=3)
    dietary: str = Field(default="", max_length=1000)
    feedback: OutcomeFeedback | None = None
    weather: WeatherForecast | None = None
    coupon_id: str | None = Field(default=None, max_length=100)
    campaign_id: str | None = Field(default=None, max_length=100)
    fields: dict[str, str | int | None] = Field(default_factory=dict, max_length=10)
    purpose: Literal["travel", "activity"] | None = None
    incident: str = Field(default="", max_length=50)
    simulated: bool = False


def workflow():
    return TravelAgencyWorkflow(build_agent_team())


def planning_active(case_id):
    return any(job.key[0] == "planning" for job in active_jobs(case_id))


PLANNING_TIMEOUT_SECONDS = 240


def enqueue(case, action, operation):
    def run():
        initial_status = case.status
        case.error_message = None
        try:
            team = workflow()
            team.checkpoint_callback = storage.save_case_to_disk
            asyncio.run(asyncio.wait_for(operation(team, case), timeout=PLANNING_TIMEOUT_SECONDS))
        except storage.CaseConflictError:
            return
        except Exception as exc:
            if not (isinstance(exc, WorkflowError) and case.status == initial_status):
                if case.status != CaseStatus.FAILED:
                    case.resume_status, case.status = case.status, CaseStatus.FAILED
            if isinstance(exc, AgentConfigurationError):
                case.error_message = "模型配置缺失，请检查服务端模型配置后重试。"
            elif isinstance(exc, AgentOutputError):
                case.error_message = str(exc)
            elif isinstance(exc, TimeoutError):
                case.error_message = f"团队处理超过{PLANNING_TIMEOUT_SECONDS}秒，可从已保存步骤重试。"
            elif isinstance(exc, WorkflowError):
                case.error_message = "操作未完成：" + str(exc)
            else:
                LOGGER.error("Planning job %s failed for %s (%s)", action, case.id, type(exc).__name__)
                case.error_message = "团队处理未完成，请稍后重试。"
        try:
            storage.save_case_to_disk(case)
        except storage.CaseConflictError:
            return

    return submit_once(("planning", case.id, case.storage_revision, action), case.id, run)


def start_planning(case, attachments=()):
    async def begin(team, item):
        if attachments:
            await team.analyze_attachments(item, attachments)
            for insight in item.attachment_insights:
                item.requirements.preferences.extend(insight.preferences_or_intent)
                item.requirements.hard_constraints.extend(insight.constraints)
        team.confirm_requirements(item)
        await team.advance(item)

    enqueue(case, "start", begin)


def pending_direction_action(case):
    if case.pending_direction_question:
        return "question"
    if case.pending_direction_revision:
        return "revise_direction"
    return None


async def process_direction_feedback(team, case):
    await team.resume_case(case)
    if case.pending_direction_question:
        await team.submit_style_feedback(case, CustomerStyleFeedback(question=case.pending_direction_question), question_only=True)
        case.pending_direction_question = ""
    else:
        await team.revise_direction(case, case.pending_direction_revision)
        case.pending_direction_revision = ""


async def process_plan_rework(team, case):
    await team.resume_case(case)
    await team.rework_rejected_plan(case)


def retry_planning(case):
    if action := pending_direction_action(case):
        enqueue(case, action, process_direction_feedback)
    elif case.resume_status == CaseStatus.NEEDS_REWORK:
        enqueue(case, "rework", process_plan_rework)
    elif case.resume_status == CaseStatus.WAITING_REQUIREMENTS:
        case.status, case.resume_status = CaseStatus.WAITING_REQUIREMENTS, None
        case.error_message = None
        storage.save_case_to_disk(case)
        start_planning(case)
    else:
        enqueue(case, "retry", lambda team, item: team.resume_case(item))


def booking_for(case):
    if order_id := suppliers.find_case_booking(case):
        return suppliers.load_order(order_id)
    return None


def create_booking(case, payload):
    if case.procurement_order or case.journey_demo:
        raise WorkflowError("请继续使用本案已选择的预订流程。")
    if existing := booking_for(case):
        return existing[0]
    req = case.requirements
    day = date.fromisoformat(req.event_date)
    days_match = re.search(r"(\d+)\s*天", req.duration or "")
    nights_match = re.search(r"(\d+)\s*晚", req.duration or "")
    nights = int(nights_match[1]) if nights_match else 0
    last_offset = max(0, int(days_match[1]) - 1) if days_match else nights
    stay = max(1, last_offset, nights) if payload.include_hotel else last_offset
    need = BookingNeed(
        city=req.city, check_in=day, check_out=day + timedelta(days=stay),
        men=payload.men if payload.include_hotel else 0, women=payload.women if payload.include_hotel else 0,
        include_hotel=payload.include_hotel, participant_count=req.participant_count,
        meals=[MealSlot(day=day + timedelta(days=i), meal=meal)
               for i in range(last_offset + 1) for meal in payload.meal_names],
        preferred_style=(req.preferred_style or "")[:300], team_profile=(req.team_profile or "")[:100],
        desired_outcomes=req.desired_outcomes, hotel_grade=payload.grade,
        dietary=payload.dietary.strip() or "具体过敏与忌口待确认",
        event_budget_cents=round(req.budget_yuan * 100) if req.budget_yuan else None,
    )
    case.booking_route = "supplier"
    storage.save_case_to_disk(case)
    lead = register_lead(case.web_customer_id or case.id, f"{need.city} · {need.people}人酒店与餐饮", payload.campaign_id)
    order = suppliers.create_order(need, lead["id"], campaign_id=lead.get("campaign_id"), case_id=case.id)
    case.supplier_order_id = order.id
    storage.save_case_to_disk(case)
    suppliers.contact_all(order.id)
    return order


def contract_view(order):
    if not order.selected_quote_ids:
        return None
    try:
        return suppliers.contract(order, check_valid=order.status not in {"confirmed", "delivered", "completed"})
    except ValueError:
        return None


def customer_status(case):
    if case.status == CaseStatus.WAITING_OWNER and TravelAgencyWorkflow.customer_decision_exhausted(case):
        return CaseStatus.WAITING_CUSTOMER
    return case.status


def customer_questions(case, status):
    if status != CaseStatus.WAITING_CUSTOMER:
        return []
    if case.status != CaseStatus.WAITING_OWNER:
        return case.customer_questions
    # The stored question list can contain only the first issue from the previous round.
    findings = [finding for review in case.reviews for finding in review.findings
                if finding.decision_owner == "customer"]
    findings.sort(key=lambda finding: finding.blocking is not True)
    return list(dict.fromkeys([*(finding.description for finding in findings), *case.customer_questions]))


def _customer_actions(case, booking):
    status = case.status
    if planning_active(case.id):
        return []
    if pending_direction_action(case):
        return ["retry"]
    if case.service_mode in {"document_intake", "activity_review"}:
        from src.web_activity import document_actions
        return document_actions(case)
    if case.journey_demo and case.journey_demo.status != "finished":
        from src.web_activity import tracking_actions
        return tracking_actions(case.journey_demo)
    if status == CaseStatus.WAITING_OWNER and customer_status(case) == CaseStatus.WAITING_CUSTOMER:
        return ["supplement"]
    if status == CaseStatus.WAITING_REQUIREMENTS:
        return ["start"]
    if status == CaseStatus.WAITING_STYLE_FEEDBACK:
        return ["confirm_direction", "question", "revise_direction"]
    if status == CaseStatus.WAITING_DRAFT:
        return ["confirm_draft", "supplement"]
    if status == CaseStatus.WAITING_CUSTOMER:
        return ["answer"]
    if status == CaseStatus.READY_FOR_CUSTOMER:
        return ["accept_plan", "supplement"]
    if status == CaseStatus.NEEDS_REWORK:
        return ["rework"]
    if status in WORKING or (status == CaseStatus.FAILED and case.resume_status):
        return ["retry"]
    if status != CaseStatus.COMPLETED:
        return []
    if booking is None:
        return [] if case.procurement_order or case.journey_demo else ["request_quotes"]
    order, _ = booking
    if order.status == "inquiring":
        if not order.quotes:
            return ["refresh_quotes"]
        usable = any(q.capacity_ok and suppliers.quote_current(order, q) for q in order.quotes)
        return ["select_quotes"] if usable else ["select_quotes", "refresh_quotes"]
    if order.status == "waiting_customer":
        return ["select_quotes", "confirm_contract"] if contract_view(order) else ["select_quotes", "refresh_quotes"]
    if order.status == "customer_confirmed":
        return ["pay_deposit"] if contract_view(order) else ["select_quotes", "refresh_quotes"]
    if order.status == "paid" and not order.supplier_paid_cents and contract_view(order) is None:
        return ["select_quotes", "refresh_quotes"]
    if order.status == "confirmed":
        if not suppliers.linked_delivery_ready(case, order):
            return ["confirm_schedule"]
        if not case.journey_demo and case.council_decision and case.council_decision.schedule:
            return ["confirm_delivery", "start_travel_demo"]
        return ["confirm_delivery"]
    if order.status == "completed" and not case.delivery_feedback:
        return ["delivery_feedback"]
    return []


def customer_actions(case, booking):
    actions = _customer_actions(case, booking)
    if (case.council_decision and not case.journey_demo
            and booking is None and not case.procurement_order
            and case.status in {CaseStatus.WAITING_DRAFT, CaseStatus.READY_FOR_CUSTOMER, CaseStatus.COMPLETED}
            and case.service_mode not in {"document_intake", "activity_review"}
            and not planning_active(case.id)):
        actions = [*actions, "weather_replan"]
    return actions


def owner_actions(case, booking):
    if case and planning_active(case.id):
        return []
    if case and pending_direction_action(case):
        return ["retry"]
    if case and case.service_mode in {"document_intake", "activity_review"}:
        from src.web_activity import document_actions
        return document_actions(case, owner=True)
    if case and case.journey_demo:
        from src.web_activity import tracking_actions
        if actions := tracking_actions(case.journey_demo, owner=True):
            return actions
    if case and case.status == CaseStatus.WAITING_OWNER and case.approval:
        actions = ["reject_plan"]
        if not TravelAgencyWorkflow.customer_decision_exhausted(case):
            actions.insert(0, "approve_plan")
        return actions
    if booking:
        order, _ = booking
        if order.status == "paid" and (contract := contract_view(order)):
            if order.customer_signature != contract["signature"]:
                return []
            deposit = (contract["resource_cents"] * 30 + 50) // 100
            return ["authorize_deposit"] if order.supplier_paid_cents < deposit else ["submit_booking"]
        if order.status == "delivered":
            return ["settle"]
    if case and (case.status in WORKING or (case.status == CaseStatus.FAILED and case.resume_status)):
        return ["retry"]
    return []


def booking_view(booking, *, owner=False):
    if not booking:
        return None
    order, revision = booking
    summary = contract_view(order)
    contract = None if summary is None else {
        "total_cents": summary["net_cents"], "conditions": summary["conditions"],
        "signature": summary["signature"],
        "deposit_cents": (summary["net_cents"] * 60 + 50) // 100,
        "balance_cents": max(0, summary["net_cents"] - order.customer_paid_cents),
    }
    if owner and contract:
        contract["supplier_deposit_cents"] = (summary["resource_cents"] * 30 + 50) // 100
        contract["supplier_total_cents"] = summary["resource_cents"]
    quotes = []
    for quote in order.quotes:
        current = suppliers.quote_current(order, quote)
        quotes.append({
            "id": quote.id, "kind": quote.kind, "supplier_name": quote.supplier_name, "meal_keys": quote.meal_keys,
            "total_cents": quote.total_cents, "available": quote.capacity_ok and current, "details": quote.details,
            "blockers": quote.blockers if current else ["报价已过期或库存已变化，请重新查询报价", *quote.blockers],
        })
    return {
        "id": order.id, "status": order.status, "revision": revision,
        "contract": contract, "customer_paid_cents": order.customer_paid_cents,
        "selected_quote_ids": order.selected_quote_ids,
        "quotes": quotes,
        "receipts": order.confirmations,
    }


def plan_views(case):
    if case.council_decision and case.status != CaseStatus.WAITING_STYLE_FEEDBACK:
        plan = case.council_decision
        return [{"id": f"plan-{case.plan_version}", "title": plan.theme, "summary": plan.summary,
                 "schedule": plan.schedule, "highlights": plan.customer_notice}]
    return [{"id": f"direction-{index}", "title": item.concept_name,
             "summary": item.client_facing_message or item.one_sentence_pitch,
             "schedule": [], "highlights": item.recommended_activities}
            for index, item in enumerate(case.creative_opinions, 1)]


def learning_view(case):
    plan = case.council_decision
    schedule = plan.schedule if plan else []
    responses = {item.lesson_id: item for item in plan.learning_responses} if plan else {}
    entries = []
    for application in case.learning_applications:
        lesson = application.lesson
        response = responses.get(lesson.id)
        index = response.schedule_index if response and response.decision != "not_applicable" else None
        entries.append({
            "id": lesson.id, "title": lesson.title, "source_case_id": lesson.source_case_id,
            "source_version": lesson.source_version, "source_stage": lesson.source_stage,
            "created_at": lesson.created_at, "evidence": lesson.evidence, "match_reason": application.match_reason,
            "supplied_to": application.supplied_to, "decision": response.decision if response else None,
            "detail": response.detail if response else "",
            "schedule_entry": schedule[index] if index is not None and 0 <= index < len(schedule) else "",
        })
    return entries


def activity_view(case):
    from src.web_activity import document_view
    booking = booking_for(case)
    req = case.requirements
    review = case.feedback_assessment
    audit = reversed(case.audit_log)
    latest_feedback = next((event for event in audit if event.action in {
        "direction_question_submitted", "direction_revised",
    }), None)
    assessment = next(audit, None)
    question_reply = None
    # Confirming a direction adds an audit entry but keeps the previous question's assessment.
    if (review and latest_feedback and latest_feedback.action == "direction_question_submitted"
            and assessment and assessment.action == "style_feedback_assessed" and assessment.detail == review.rationale):
        question_reply = {"question": latest_feedback.detail, "answer": review.customer_response,
                          "answered_at": latest_feedback.timestamp.isoformat()}
    jobs = active_jobs(case.id)
    actions = customer_actions(case, booking)
    status = customer_status(case)
    error = case.error_message or ""
    if not error.startswith("操作未完成："):
        error = "团队处理未完成，请查看可执行操作。" if case.status == CaseStatus.FAILED else ""
    return {
        "id": case.id, "revision": case.storage_revision, "status": status,
        "display_number": case_number(case.model_dump(mode="json", include={"id", "requirements", "audit_log"})),
        "service_mode": case.service_mode,
        "title": req.event_name if req else "团建活动",
        "requirements": req.model_dump(mode="json") if req else None,
        "running": any(job.key[0] == "planning" for job in jobs), "actions": actions, "plans": plan_views(case),
        "updates": [x.model_dump(mode="json") for x in case.public_updates[-12:]],
        "questions": customer_questions(case, status),
        "message": review.customer_response if review else "",
        "question_reply": question_reply,
        "pending_question": case.pending_direction_question,
        "pending_revision": case.pending_direction_revision,
        "question_pending": any(job.key[0] == "planning" and job.key[-1] == "question" for job in jobs),
        "error": error,
        "booking": booking_view(booking),
        "attachments": [item.model_dump(mode="json") for item in case.attachment_insights],
        "feedback_options": case.plan_feedback_options.model_dump(mode="json") if case.plan_feedback_options else None,
        "delivery_feedback": case.delivery_feedback.model_dump(mode="json") if case.delivery_feedback else None,
        **document_view(case),
        "updated_schedule": suppliers.linked_execution(case, booking[0])[0] if "confirm_schedule" in actions else [],
    }


def customer_action(case, payload):
    action = payload.action
    booking = booking_for(case)
    if action not in customer_actions(case, booking):
        raise WorkflowError("当前阶段无法执行该操作，请刷新后查看。")
    if action == "weather_replan":
        req = case.requirements
        if not req or not req.event_date:
            raise WorkflowError("当前活动缺少可查询的日期和目的地。")
        match = re.search(r"(\d+)天", req.duration or "")
        days = int(match.group(1)) if match else 1
        start = date.fromisoformat(req.event_date)
        end = date.fromisoformat(req.end_date) if req.end_date else start + timedelta(days=max(days - 1, 0))
        forecast = payload.weather
        if forecast is None:
            try:
                # Re-read the whole date range when no fresh UI snapshot is sent:
                # drafts must not keep planning from a stale one-day forecast.
                forecast = weather.fetch_weather(
                    req.destination or req.city or "", start, req.city, end,
                )
            except weather.WeatherError as exc:
                raise WorkflowError(str(exc)) from exc
        if forecast.event_date != start.isoformat() or (forecast.end_date or forecast.event_date) != end.isoformat():
            raise WorkflowError("天气预报与活动日期不一致，请刷新后再更新草案。")
        req.end_date = end.isoformat()
        req.weather = forecast
        enqueue(case, action, lambda team, item: team.replan_for_weather(item, forecast, payload.note.strip()))
        return
    if case.service_mode in {"document_intake", "activity_review"}:
        from src.web_activity import apply_document_action
        apply_document_action(case, payload, enqueue, start_planning)
        return
    from src.web_activity import apply_tracking_action, tracking_actions
    if action == "start_travel_demo" or (case.journey_demo and action in tracking_actions(case.journey_demo)):
        if action == "start_travel_demo" and not case.supplier_order_id:
            case.supplier_order_id = booking[0].id
        apply_tracking_action(case, payload, enqueue)
        return
    if action in {"question", "revise_direction", "answer"} and not payload.note.strip():
        raise WorkflowError("请填写内容后提交。")
    if action == "accept_plan" and payload.outcome == "unacceptable" and not (payload.note.strip() or payload.options):
        raise WorkflowError("请选择或填写不能接受的原因。")

    if action == "retry":
        retry_planning(case)
        return
    if action == "start":
        start_planning(case)
        return
    if action == "supplement":
        TravelAgencyWorkflow(None).record_supplement(case, payload.note)
        storage.save_case_to_disk(case)
        if case.pending_supplement:
            enqueue(case, action, lambda team, item: team.advance(item))
        return
    if action in {"question", "revise_direction"}:
        if action == "question":
            case.pending_direction_question = payload.note.strip()
        else:
            case.pending_direction_revision = payload.note.strip()
        storage.save_case_to_disk(case)
        enqueue(case, action, process_direction_feedback)
        return
    if action == "rework":
        enqueue(case, action, process_plan_rework)
        return
    if action in {"confirm_direction", "confirm_draft", "answer", "accept_plan"}:
        async def apply(team, item):
            if action == "confirm_direction":
                await team.submit_style_feedback(item, CustomerStyleFeedback())
            elif action == "confirm_draft":
                await team.submit_draft_feedback(item, [], "")
            elif action == "answer":
                team.record_customer_answer(item, payload.note)
                await team.advance(item)
            elif action == "accept_plan":
                team.submit_plan_acceptance(item, PlanAcceptanceFeedback(outcome=payload.outcome,
                                            selected_option_ids=payload.options, comment=payload.note))

        enqueue(case, action, apply)
        return

    if action == "request_quotes":
        create_booking(case, payload)
        return
    if action == "delivery_feedback":
        if not payload.feedback:
            raise WorkflowError("请填写履约评价。")
        if not case.supplier_order_id:
            case.supplier_order_id = booking[0].id
        TravelAgencyWorkflow(None).submit_outcome_feedback(case, payload.feedback, after_delivery=True)
        storage.save_case_to_disk(case)
        return

    order, revision = booking
    check_order_revision(payload, revision)
    if action == "refresh_quotes":
        suppliers.contact_all(order.id)
    elif action == "select_quotes":
        suppliers.select_quotes(order.id, payload.options, expected_revision=revision)
    elif action == "confirm_contract":
        suppliers.confirm_contract(order.id, payload.options, expected_signature=required_signature(payload),
                                   expected_revision=revision)
    elif action == "pay_deposit":
        suppliers.pay_customer_deposit(order.id, coupon_id=payload.coupon_id, expected_signature=required_signature(payload),
                                       expected_revision=revision)
    elif action == "confirm_schedule":
        if not case.supplier_order_id:
            case.supplier_order_id = order.id
        suppliers.confirm_linked_execution(case)
        storage.save_case_to_disk(case)
    elif action == "confirm_delivery":
        suppliers.finish_service(order.id, expected_revision=revision)


def check_order_revision(payload, revision):
    if payload.order_revision != revision:
        raise storage.CaseConflictError("预订已更新，请查看最新状态后再操作。")


def required_signature(payload):
    if not payload.signature:
        raise WorkflowError("请重新查看当前合同。")
    return payload.signature


def owner_action(case, booking, payload):
    if payload.action not in owner_actions(case, booking):
        raise WorkflowError("当前阶段没有该老板操作，请刷新后查看。")
    if case and case.service_mode in {"document_intake", "activity_review"}:
        from src.web_activity import apply_document_action
        apply_document_action(case, payload, enqueue, start_planning, owner=True)
        return
    if case and case.journey_demo:
        from src.web_activity import apply_tracking_action, tracking_actions
        if payload.action in tracking_actions(case.journey_demo, owner=True):
            apply_tracking_action(case, payload, enqueue, owner=True)
            return
    if payload.action == "retry":
        retry_planning(case)
        return
    if payload.action in {"approve_plan", "reject_plan"}:
        if payload.action == "reject_plan" and not payload.note.strip():
            raise WorkflowError("请填写退回原因。")

        async def decide(team, item):
            await team.submit_owner_decision(item, payload.action == "approve_plan", payload.note)

        enqueue(case, payload.action, decide)
        return
    order, revision = booking
    check_order_revision(payload, revision)
    if payload.action == "authorize_deposit":
        suppliers.pay_supplier_deposit(order.id, "owner", required_signature(payload), expected_revision=revision)
    elif payload.action == "submit_booking":
        suppliers.fulfill_bookings(order.id, expected_revision=revision)
    elif payload.action == "settle":
        suppliers.settle_suppliers(order.id, "owner", expected_revision=revision)
