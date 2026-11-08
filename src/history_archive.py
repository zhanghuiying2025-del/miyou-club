"""Read full business archives and derive traceable, rebuildable search fragments.

This module never changes an order, its financial ledger, or its resume state.
"""
import hashlib
import json
import sqlite3
from contextlib import closing

from src import storage, company_store
from src.models import TravelCase
from src.order_labels import case_number, supplier_number


KIND_LABELS = {"requirements": "需求", "plan": "方案", "decision": "决策与取舍",
               "execution": "执行", "feedback": "客户反馈", "lesson": "反馈经验", "finance": "财务"}
STATUS_LABELS = {"in_progress": "处理中", "plan_confirmed": "方案已确认（未完单）",
                 "delivered": "已履约待结算", "completed": "已履约并结算"}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _supplier_records():
    # Read-only: opening the history page must not create a company DB or cash event.
    path = company_store.COMPANY_DB
    if not path.exists():
        return []
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE name='records'").fetchone()
        if not exists:
            return []
        return [{"payload": json.loads(row[0]), "revision": row[1]} for row in connection.execute(
            "SELECT payload,revision FROM records WHERE kind='supplier_order' ORDER BY rowid DESC")]


def documents():
    suppliers = _supplier_records()
    by_case = {}
    for record in suppliers:
        order = record["payload"]
        if order.get("case_id"):
            by_case.setdefault(order["case_id"], []).append(order)
    result = []
    for record in storage.saved_case_snapshots():
        raw = record["case"]
        related = by_case.get(raw["id"], [])
        req = raw.get("requirements") or {}
        old = raw.get("procurement_order") or {}
        settled = bool(old.get("settled")) or any(o.get("status") == "completed" for o in related)
        delivered = any(o.get("delivered") for o in related)
        stage = "completed" if settled else "delivered" if delivered else (
            "plan_confirmed" if raw.get("status") == "completed" else "in_progress")
        result.append({"id": "case:" + raw["id"], "source_id": raw["id"], "kind": "case",
            "display_number": case_number(raw),
            "title": req.get("event_name") or raw.get("customer_request", "")[:60],
            "city": req.get("city") or "", "style": req.get("preferred_style") or "",
            "stage": stage, "status": raw.get("status", "new"),
            "service": raw.get("service_mode", "travel_planning"),
            "updated_at": record.get("updated_at", ""), "revision": raw.get("storage_revision", 0),
            "snapshot": raw, "related_orders": related})
    for record in suppliers:
        raw = record["payload"]
        need = raw.get("need") or {}
        stage = "completed" if raw.get("status") == "completed" else (
            "delivered" if raw.get("delivered") else "in_progress")
        result.append({"id": "supplier:" + raw["id"], "source_id": raw["id"], "kind": "supplier",
            "display_number": supplier_number(raw["id"], need.get("check_in"), need.get("check_out")),
            "title": f"{need.get('city', '')}酒店餐饮 · {raw.get('customer_id', '')}",
            "city": need.get("city", ""), "style": "", "stage": stage,
            "status": raw.get("status", "inquiring"), "service": "supplier_booking",
            "updated_at": "", "revision": record["revision"], "snapshot": raw, "related_orders": []})
    return result


def get_document(document_id):
    return next((item for item in documents() if item["id"] == document_id), None)


def case_outcome(document):
    if document["status"] in {"failed", "cancelled"}:
        return "失败"
    if document["stage"] == "completed":
        return "成功"
    if document["service"] == "activity_review" and document["status"] == "completed":
        journey = document["snapshot"].get("journey_demo")
        if not journey or journey.get("status") == "finished":
            return "成功"
    return "未完成"


def hidden_documents(docs, hidden_ids):
    return {doc["id"] for doc in docs if doc["id"] in hidden_ids
            or (doc["kind"] == "supplier" and f"case:{doc['snapshot'].get('case_id')}" in hidden_ids)}


def fragments(document):
    """Split by business meaning, with exact field paths retained for verification."""
    raw = document["snapshot"]
    result = []

    def add(kind, path, value, version=0, ordinal=0):
        if value is None or value == [] or value == {} or value == "":
            return
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        key = digest([document["id"], path, version, ordinal])[:24]
        result.append({"id": "memory_" + key, "document_id": document["id"],
            "source_id": document["source_id"], "kind": kind, "path": path,
            "version": version, "city": document["city"], "style": document["style"],
            "service": document["service"], "stage": document["stage"],
            "title": document["title"], "text": text, "active": True,
            "scope": raw.get("requirements") or {}, "fingerprint": digest(value)})

    if document["kind"] == "supplier":
        add("requirements", "need", raw.get("need"))
        for i, item in enumerate(raw.get("contacts", [])):
            add("execution", f"contacts[{i}]", item)
        for i, item in enumerate(raw.get("confirmations", [])):
            add("execution", f"confirmations[{i}]", item)
        add("finance", "payment_snapshot", {name: raw.get(name) for name in
            ("service_fee_cents", "discount_cents", "customer_paid_cents", "supplier_paid_cents")})
        return result

    version = raw.get("plan_version", 1)
    add("requirements", "requirements", raw.get("requirements"), version)
    plan = raw.get("council_decision")
    if plan:
        # A draft is searchable by the owner, but cannot silently guide future cases.
        add("plan", "council_decision.summary", plan.get("summary"), version)
        for i, line in enumerate(plan.get("schedule", [])):
            add("plan", f"council_decision.schedule[{i}]", line, version)
        for name in ("rationale", "rejected_ideas", "items_to_verify"):
            for i, line in enumerate(plan.get(name, [])):
                add("decision", f"council_decision.{name}[{i}]", line, version)
    for i, line in enumerate(raw.get("manager_resolutions", [])):
        add("decision", f"manager_resolutions[{i}]", line, version)
    for i, record in enumerate(raw.get("feedback_history", [])):
        add("feedback", f"feedback_history[{i}].feedback", record.get("feedback"), record.get("version", 1))
        for j, lesson in enumerate(record.get("lessons", [])):
            if lesson.get("active", True):
                add("lesson", f"feedback_history[{i}].lessons[{j}]", lesson, record.get("version", 1))
                result[-1]["lesson_id"] = lesson.get("id", "")
    add("feedback", "plan_acceptance_feedback", raw.get("plan_acceptance_feedback"), version)
    add("feedback", "outcome_feedback", raw.get("outcome_feedback"), version)
    add("feedback", "delivery_feedback", raw.get("delivery_feedback"), version)
    review = raw.get("activity_review")
    if review:
        add("plan", "activity_review.execution", review.get("execution"), version)
    order = raw.get("procurement_order") or {}
    for i, item in enumerate(order.get("discussion", [])):
        add("decision", f"procurement_order.discussion[{i}]", item, version)
    for i, item in enumerate(order.get("reservations", [])):
        add("execution", f"procurement_order.reservations[{i}]", item, version)
    add("finance", "procurement_order.payments", order.get("payments"), version)
    journeys = [*raw.get("journey_history", []), *([raw["journey_demo"]] if raw.get("journey_demo") else [])]
    for i, journey in enumerate(journeys):
        for j, event in enumerate(journey.get("events", [])):
            path = f"journey_history[{i}]" if i < len(raw.get("journey_history", [])) else "journey_demo"
            add("execution", f"{path}.events[{j}]", event, journey.get("plan_version", version))
    return result


def current_case(case_id):
    raw = storage.load_case_from_disk(case_id)
    return raw if raw is None or isinstance(raw, TravelCase) else TravelCase.model_validate(raw)
