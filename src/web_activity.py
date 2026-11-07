"""Document review and optional on-site management for React."""
from src import activity_order, document_intake, journey, storage
from src.activity_review import review_activity
from src.models import AuditEvent, CaseStatus


def tracking_actions(demo, *, owner=False):
    if demo.status == "awaiting_agent":
        return ["retry_incident"]
    if owner:
        return {
            "awaiting_owner": ["approve_incident", "hold_incident"],
            "held": ["reopen_incident"],
        }.get(demo.status, [])
    return {
        "ready": ["start_activity"],
        "paused": ["start_activity"],
        "running": ["next_activity_step", "report_incident"],
        "awaiting_organizer": ["confirm_incident", "refer_incident"],
    }.get(demo.status, [])


def document_actions(case, *, owner=False):
    if case.journey_demo:
        return tracking_actions(case.journey_demo, owner=owner)
    if case.service_mode == "document_intake":
        if case.document_intake and case.status == CaseStatus.WAITING_REQUIREMENTS:
            return [] if owner else ["confirm_document"]
        return ["retry_document"] if case.activity_sources else []
    if case.service_mode != "activity_review":
        return []
    if case.status in {CaseStatus.REVIEWING, CaseStatus.FAILED}:
        return ["review_document"]
    if owner:
        return []
    if case.status == CaseStatus.READY_FOR_CUSTOMER:
        return ["receive_review", "review_document"]
    if case.status == CaseStatus.COMPLETED and case.activity_review:
        return ["create_activity_order", "review_document"]
    return []


def document_view(case):
    if case.service_mode not in {"document_intake", "activity_review"} and not case.activity_sources and not case.journey_demo:
        return {}
    demo = case.journey_demo
    tracking = None
    if demo:
        tracking = demo.model_dump(mode="json", exclude={"activity_context", "handoffs", "runtime_error"})
        tracking["index"] = journey.current_index(demo)
        event = journey.pending_event(demo)
        tracking["pending_id"] = event.id if event else None
        for record in tracking["events"]:
            record.pop("error", None)
            if record.get("response"):
                record["response"].pop("handoffs", None)
    return {
        "documents": [item.model_dump(mode="json") for item in case.activity_sources],
        "intake": case.document_intake.model_dump(mode="json") if case.document_intake else None,
        "venue": case.document_venue,
        "review": case.activity_review.model_dump(mode="json") if case.activity_review else None,
        "review_history": [report.model_dump(mode="json") for report in case.activity_review_history],
        "tracking": tracking,
    }


def apply_document_action(case, payload, enqueue, start_planning, *, owner=False):
    action = payload.action
    if action == "retry_document":
        enqueue(case, action, document_intake.intake_documents)
        return
    if action == "review_document":
        enqueue(case, action, lambda workflow, item: review_activity(workflow, item, notes=payload.note))
        return
    if action == "confirm_document":
        document_intake.update_document_fields(case, payload.fields)
        mode = document_intake.confirm_document_route(case, payload.purpose)
        storage.save_case_to_disk(case)
        if mode == "activity_review":
            enqueue(case, mode, review_activity)
        else:
            start_planning(case)
        return
    if action == "receive_review":
        case.status = CaseStatus.COMPLETED
        case.audit_log.append(AuditEvent(actor="客户", action="activity_review_received", detail="确认收到审核与执行清单"))
    elif action == "create_activity_order":
        document_intake.update_document_fields(case, payload.fields)
        activity_order.create_activity_order(case, payload.options)
    else:
        apply_tracking_action(case, payload, enqueue, owner=owner)
        return
    storage.save_case_to_disk(case)


def apply_tracking_action(case, payload, enqueue, *, owner=False):
    action = payload.action
    demo = case.journey_demo
    manual = demo is not None and demo.context_kind == "activity"
    if action in {"start_travel_demo", "start_activity"}:
        if manual:
            activity_order.start_activity_order(case)
        else:
            journey.start_demo(case)
    elif action == "next_activity_step":
        if manual:
            activity_order.advance_activity_order(case)
        else:
            journey.tick_demo(demo, now=demo.last_step_at + journey.SECONDS_PER_TICK * 4 + .01)
    elif action in {"report_incident", "reopen_incident"}:
        kind, note, simulated = payload.incident, payload.note, payload.simulated
        if action == "reopen_incident":
            previous = demo.events[-1]
            demo.status = "paused"
            kind, note, simulated = previous.kind, previous.description, previous.source == "simulated_event"
        if simulated and manual and kind != "equipment":
            raise ValueError("演示事件仅支持设备故障。")
        if manual:
            activity_order.report_activity_incident(case, kind, note, simulated)
        else:
            event = journey.report_event(demo, kind, note)
            if simulated:
                event.source = "simulated_event"
    elif action in {"approve_incident", "hold_incident", "confirm_incident", "refer_incident"}:
        journey.confirm_event(demo, action in {"approve_incident", "confirm_incident"}, owner=owner)

    if demo and demo.status == "awaiting_agent":
        demo.runtime_error = ""
        case.status, case.resume_status, case.error_message = CaseStatus.COMPLETED, None, None
        storage.save_case_to_disk(case)

        async def handle(workflow, item):
            active = item.journey_demo
            event = journey.pending_event(active)
            response, source, error = await journey.coordinate_response(workflow.team, active, event)
            journey.apply_response(active, event.id, response, source, error)

        enqueue(case, action, handle)
    else:
        storage.save_case_to_disk(case)
