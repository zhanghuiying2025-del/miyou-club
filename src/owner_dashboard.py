"""Read-only projections for the owner's cross-order cockpit."""

from contextlib import closing
import sqlite3
import time

from src import activity_order, company_store, procurement, storage, supplier_simulation
from src.journey import pending_event
from src.models import CaseStatus, TravelCase
from src.order_labels import case_number, supplier_number
from src.runtime import active_jobs, progress_events
from src.supplier_models import SupplierOrder
from src.workflow import TravelAgencyWorkflow


_CUSTOMER_STAGES = {
    CaseStatus.WAITING_REQUIREMENTS: "待客户确认需求",
    CaseStatus.WAITING_DRAFT: "待客户确认组合草案",
    CaseStatus.WAITING_CUSTOMER: "待客户补充信息",
    CaseStatus.WAITING_STYLE_FEEDBACK: "待客户确认提案方向",
    CaseStatus.READY_FOR_CUSTOMER: "待客户确认方案",
}
_TEAM_STAGES = {
    CaseStatus.NEW: ("待处理", "项目经理"),
    CaseStatus.INTAKE: ("整理需求", "项目经理"),
    CaseStatus.SUPPLEMENTING: ("整理补充信息", "项目经理"),
    CaseStatus.ALIGNMENT: ("对齐需求", "项目经理"),
    CaseStatus.CREATIVE_COUNCIL: ("生成提案", "多角色策划"),
    CaseStatus.CROSS_REVIEW: ("检查执行条件", "履约执行"),
    CaseStatus.DELIBERATION: ("组合方案", "项目经理"),
    CaseStatus.REVIEWING: ("审核方案", "项目经理"),
    CaseStatus.NEEDS_REWORK: ("待重新策划", "多角色策划"),
}
_TASK_SOURCES = {
    "agent": "模型结果", "rule": "程序执行", "rule_fallback": "规则降级",
    "validated_cache": "已校验结果复用", "model_simulation": "模型生成的模拟场景",
    "supplier_fixture": "固定模拟回执",
}
_JOB_LABELS = {
    "planning": ("团队", "需求与策划"),
    "booking": ("履约执行", "落实模拟资源"),
    "journey": ("团队", "处理活动响应"),
    "simulation": ("履约执行", "酒店餐饮模拟任务"),
}
_AUDIT_LABELS = {
    "approval_decision": "老板审批", "booking_change_requested": "客户申请变更",
    "interrupted": "执行中断", "agent_error": "Agent 失败", "unexpected_error": "执行异常",
}


def _company_snapshot():
    path = company_store.COMPANY_DB
    if not path.exists():
        return [], [], [], []
    # A single read transaction gives each record and its cash events the same snapshot.
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        records = company_store.Records(db)
        if "records" not in tables:
            return [], [], [], []
        return (
            [SupplierOrder.model_validate(row) for row in records.all("supplier_order")],
            records.all("lead"), records.all("campaign"),
            records.events("cash") if "events" in tables else [],
        )


def _activity_flow(case, *, tracking=False):
    demo = case.journey_demo
    if not ((tracking or activity_order.TRACKING_ENABLED) and demo):
        return None
    event = pending_event(demo)
    if demo.status == "awaiting_owner" and event and event.response:
        return "待批准活动调整", "老板", event.description, True
    if demo.status == "awaiting_organizer":
        return "待客户确认活动调整", "客户", event.description if event else "", False
    if demo.status == "awaiting_agent":
        return "活动异常处理中", "团队", event.description if event else "", False
    if tracking and demo.status in {"ready", "running", "held", "paused"}:
        stage = {"ready": "活动待开始", "running": "活动进行中", "held": "活动暂停", "paused": "活动暂停"}[demo.status]
        return stage, "客户", event.description if event else "", False
    return None


def _travel_flow(case):
    order = case.procurement_order
    if order.settled:
        return "已模拟结算", "—", "", False
    if order.status in {"waiting_owner", "waiting_customer"}:
        package = next((p for p in order.packages if p.id == order.selected_package), None)
        if order.status == "waiting_owner":
            reasons = procurement.owner_reasons(order, package) if package and order.quote_yuan > 0 else []
            return "待审批资源替代", "老板", "；".join(reasons) or "替代资源需要商业决定", True
        reasons = procurement.customer_reasons(order, package) if package else []
        return "待客户确认资源变更", "客户", "；".join(reasons) or "请客户确认替代资源", False
    if order.status == "held":
        return "资源安排暂停", "老板", "待决定是否恢复资源安排", False
    if order.status != "ready":
        if order.runtime_error:
            return "资源任务待重试", "履约执行", order.runtime_error, False
        return "落实资源中", "履约执行", "", False
    if not any(p.kind == "supplier_payment" for p in order.payments):
        if any(r.status not in {"confirmed", "not_required"} for r in order.reservations):
            return "待供方回执", "履约执行", "回执齐全后才可授权支出", False
        return "待授权模拟供方支出", "老板", "已确认资源，供方支出需要单独授权", True
    activity = _activity_flow(case)
    if activity:
        return activity
    demo = case.journey_demo
    if demo and demo.status != "finished":
        if not activity_order.TRACKING_ENABLED:
            return "活动记录未结束", "—", "当前版本的活动跟进已关闭", False
        return "待活动完成", "客户", demo.runtime_error, False
    if demo and any(
        e.status == "resolved" and e.response and (e.cost_confirmation_required or e.response.needs_change)
        and e.confirmed_cost_yuan is None for e in demo.events
    ):
        if activity_order.TRACKING_ENABLED:
            return "待核对活动费用", "老板", "结算前需要核对活动调整费用", True
        return "活动费用未核对", "—", "当前版本的活动跟进已关闭", False
    return "待客户确认交付并支付尾款", "客户", "", False


def _supplier_flow(order, case=None):
    if order.status == "completed":
        return "酒店餐饮已模拟结算", "—", "", False
    if order.status == "delivered":
        return "待结清供方款", "老板", "客户已确认交付并支付尾款", True
    if order.status == "paid":
        try:
            summary = supplier_simulation.contract(order)
        except ValueError as error:
            return "待重新核实供方报价", "履约执行", str(error), False
        if order.customer_signature != summary["signature"]:
            return "待客户确认当前报价", "客户", "报价已变更，需要客户重新确认", False
        if order.supplier_paid_cents < (summary["resource_cents"] * 30 + 50) // 100:
            return "待授权模拟供方定金", "老板", "客户已预付，需授权供方定金", True
        return "待提交模拟预订", "老板", "供方定金已授权，请点击执行工具提交预订并获取回执", True
    if order.status == "confirmed":
        if case:
            _, signature, changed = supplier_simulation.linked_execution(case, order)
            if changed and signature != case.supplier_execution_signature:
                return "待客户确认执行行程", "客户", "酒店餐饮条件改变了原行程", False
            activity = _activity_flow(case)
            if activity:
                return activity
        return "待客户确认交付", "客户", "", False
    if order.status == "waiting_customer":
        return "待客户确认报价", "客户", "等待确认报价及相关条件", False
    if order.status == "customer_confirmed":
        return "待客户支付预付款", "客户", "", False
    return "酒店餐饮询价中", "履约执行", "", False


def _case_flow(case, supplier, *, tracking=False):
    if tracking and (activity := _activity_flow(case, tracking=True)):
        return activity
    if case.service_mode == "document_intake":
        return "待客户补齐文档信息", "客户", case.error_message or "", False
    if case.service_mode == "activity_review":
        activity = _activity_flow(case)
        if activity:
            return activity
        if case.journey_demo and case.journey_demo.status == "finished":
            return "活动执行已结束", "—", "", False
        if case.status == CaseStatus.COMPLETED:
            return "审核清单已签收", "—", "", False
        if case.activity_review:
            return "待客户确认收到审核清单", "客户", case.error_message or "", False
        return "审核文档中", "项目经理", case.error_message or "", False
    if case.status == CaseStatus.WAITING_OWNER and case.approval:
        if TravelAgencyWorkflow.customer_decision_exhausted(case):
            return "审批暂缓，待客户补充", "客户", "；".join(case.approval.reasons), False
        return case.approval.title, "老板", "；".join(case.approval.reasons), True
    if supplier:
        return _supplier_flow(supplier, case)
    if case.procurement_order:
        return _travel_flow(case)
    if case.status in _CUSTOMER_STAGES:
        return _CUSTOMER_STAGES[case.status], "客户", "", False
    if case.status == CaseStatus.COMPLETED:
        if case.booking_route == "supplier":
            return "待继续酒店餐饮预订", "客户", "预订需求已开始，等待取得报价", False
        return "方案已确认，待下单", "客户", "", False
    if case.status == CaseStatus.FAILED:
        if case.approval and case.approval.approved is False and not case.resume_status:
            return "方案审批已拒绝", "—", case.approval.comment or "", False
        return "案件待检查", "团队", case.error_message or "", False
    stage, actor = _TEAM_STAGES.get(case.status, ("待核对案件", "项目经理"))
    return stage, actor, "", False


def _case_title(case):
    return (case.requirements.event_name if case.requirements else case.customer_request[:60]) or case.id


def _supplier_title(order):
    return f"{order.need.city}酒店餐饮 · {order.customer_id}"


def _order_row(kind, source_id, title, flow, case_id=None, supplier_order_id=None):
    stage, actor, blocker, needs_owner = flow
    return {
        "key": f"{kind}:{source_id}", "kind": kind, "source_id": source_id, "title": title,
        "stage": stage, "next_actor": actor, "blocker": blocker, "needs_owner": needs_owner,
        "case_id": case_id, "supplier_order_id": supplier_order_id,
    }


def _after_approval(case, supplier, stage):
    if case and case.status == CaseStatus.WAITING_OWNER:
        if case.pending_owner_step:
            stage, actor = _TEAM_STAGES.get(case.pending_owner_step, ("推进案件", "项目团队"))
            return f"{actor}继续{stage}"
        if case.customer_answer_pending or any(
            finding.decision_owner == "customer" for review in case.reviews for finding in review.findings
        ):
            return "客户补充信息或确认关键条件"
        return "客户确认最终方案"
    if supplier:
        if supplier.status == "paid":
            if stage == "待提交模拟预订":
                return "工具返回模拟预订回执，客户继续核对行程或确认交付"
            return "老板点击执行工具提交模拟预订并获取回执"
        if supplier.status == "delivered":
            return "程序登记供方支出并完成模拟结算"
        return "客户确认活动调整"
    if case.procurement_order and case.procurement_order.status == "waiting_owner":
        return "服务条件变化时由客户确认。条件满足后取得模拟回执，再单独授权供方支出"
    if case.journey_demo and case.journey_demo.status == "awaiting_owner":
        return "客户确认活动调整"
    return "客户确认交付并支付模拟尾款"


def _order_details(row, case, supplier):
    if row["needs_owner"]:
        row["after_approval"] = _after_approval(case, supplier, row["stage"])
    if supplier:
        row["events"] = [
            {"执行方": "履约执行", "动作": "取得模拟预订回执",
             "说明": f"{receipt.get('supplier', '')} · {receipt.get('id', '')}"}
            for receipt in reversed(supplier.confirmations[-5:])
        ]
    if case:
        order = case.procurement_order
        timeline = [(e.timestamp.isoformat(), e.actor, _AUDIT_LABELS.get(e.action, e.action), e.detail)
                    for e in case.audit_log[-5:]]
        if order and not supplier:
            timeline.extend((e.timestamp.isoformat(), e.actor, e.stage, e.owner_message)
                            for e in order.discussion[-5:])
        row.setdefault("events", []).extend(
            {"执行方": actor, "动作": action, "说明": detail}
            for _, actor, action, detail in sorted(timeline, reverse=True)[:5 - len(row.get("events", []))]
        )
        if order and not supplier and order.status == "waiting_owner" and order.quote_yuan > 0:
            alternatives = []
            for package in order.packages:
                if package.available_rooms < order.room_count:
                    continue
                figures = procurement.financials(order, package)
                alternatives.append({
                    "资源组合": package.label, "预计成本": figures["cost"], "预计利润": figures["profit"],
                    "利润率": f"{figures['margin']:.1%}", "接驳分钟": package.transfer_minutes,
                    "待客户确认": "；".join(procurement.customer_reasons(order, package)) or "—",
                })
            row["alternatives"] = alternatives


def _task_row(order, actor, title, status, detail, source="", elapsed=None, **context):
    return {
        "actor": actor, "title": title, "status": status, "order_key": order["key"],
        "order_title": order["title"], "detail": detail, "source": _TASK_SOURCES.get(source, source),
        "elapsed_seconds": elapsed, **context,
    }


def _tasks_for(row, entity_id, records):
    jobs = list(active_jobs(entity_id))
    tasks = []
    completed_steps = set()
    for record in reversed(records):
        status = record.get("status", "completed")
        detail = record.get("error") or record.get("result_summary") or record.get("summary") or record.get("detail") or ""
        elapsed = record.get("elapsed_seconds", record.get("seconds"))
        actor = record.get("receiver") or record.get("actor") or "团队"
        if status == "running":
            job = next((j for j in jobs if j.key[0] == "planning"), None)
            if job is None:
                status, detail = "interrupted", detail or "当前进程未发现对应的执行任务"
            else:
                jobs.remove(job)
                elapsed = round(max(0, time.monotonic() - job.started), 1)
        elif status == "failed" and "前次执行中断" in detail:
            status = "interrupted"
        title = record.get("task") or "业务任务"
        input_summary = record.get("input_summary", "")
        expected = record.get("expected_result", "")
        step = (actor, title, expected, input_summary)
        # Scan newest first. Earlier successes and other plan versions cannot resolve a later failure.
        recovered = status in {"failed", "interrupted"} and step in completed_steps
        if status == "completed" and expected and input_summary:
            completed_steps.add(step)
        tasks.append(_task_row(
            row, actor, title, status, detail, record.get("source", ""),
            elapsed, sender=record.get("sender", ""), expected_result=expected,
            input_summary=input_summary, created_at=record.get("created_at"), recovered=recovered,
        ))
    for job in jobs:
        default_actor, title = _JOB_LABELS.get(job.key[0], ("团队", "后台任务"))
        events = progress_events(job)
        last = events[-1] if events else {}
        tasks.append(_task_row(
            row, last.get("actor") or default_actor, title, "running",
            last.get("message") or "后台任务正在执行", "进程内任务",
            round(max(0, time.monotonic() - job.started), 1),
        ))
    return tasks


def _travel_results(cases):
    metrics = {"paid_orders": 0, "settled_orders": 0, "revenue_cents": 0, "direct_cost_cents": 0}
    rows = []
    for case in cases:
        order = case.procurement_order
        if order is None:
            continue
        metrics["paid_orders"] += int(any(
            p.kind in {"customer_deposit", "customer_balance"} and p.amount_yuan > 0 for p in order.payments
        ))
        if not order.settled:
            continue
        metrics["settled_orders"] += 1
        cost_cents = sum(round(p.amount_yuan * 100) for p in order.payments
                         if p.kind in {"supplier_payment", "ai_operations", "activity_expense"})
        revenue_cents = round(order.quote_yuan * 100)
        metrics["revenue_cents"] += revenue_cents
        metrics["direct_cost_cents"] += cost_cents
        package = next((p for p in order.packages if p.id == order.selected_package), None)
        estimate = procurement.financials(order, package)["profit"] if package and order.quote_yuan > 0 else None
        rows.append({
            "业务": "团建", "订单": _case_title(case), "订单编号": order.id,
            "已确认合同收入": revenue_cents / 100, "已记录直接成本": cost_cents / 100,
            "已实现订单利润": (revenue_cents - cost_cents) / 100, "预计利润": estimate,
        })
    return metrics, rows


def _case_tasks(row, case):
    order, demo = case.procurement_order, case.journey_demo
    handoffs = list(case.handoffs)
    if order:
        handoffs.extend(order.handoffs)
    if demo:
        handoffs.extend(demo.handoffs)
    handoffs.sort(key=lambda handoff: handoff.created_at.isoformat())
    tasks = _tasks_for(row, case.id, [handoff.model_dump() for handoff in handoffs])
    # Process-level failures can occur outside an individual Agent handoff.
    if case.status == CaseStatus.FAILED and case.resume_status and case.error_message:
        if not case.handoffs or case.handoffs[-1].status != "failed":
            stage, actor = _TEAM_STAGES.get(case.resume_status, ("案件处理", "团队"))
            tasks.append(_task_row(row, actor, stage, "failed", case.error_message, "业务执行记录"))
    if order and order.runtime_error:
        tasks.append(_task_row(row, "履约执行", "预订任务处理", "failed", order.runtime_error, "业务执行记录"))
    if demo and demo.runtime_error:
        tasks.append(_task_row(row, "团队", "活动响应处理", "failed", demo.runtime_error, "业务执行记录"))
    return tasks


def _supplier_results(orders, leads, campaigns, cash):
    paid = [order for order in orders if order.customer_paid_cents > 0]
    settled = [order for order in orders if order.status == "completed"]
    metrics = {
        "leads": len(leads), "paying_customers": len({order.customer_id for order in paid}),
        "paid_orders": len(paid), "settled_orders": len(settled),
        "service_income_cents": sum(order.service_fee_cents - order.discount_cents for order in settled),
    }
    campaign_rows = []
    for campaign in campaigns:
        campaign_id = campaign["id"]
        sourced_paid = [order for order in paid if order.campaign_id == campaign_id]
        campaign_rows.append({
            "活动": campaign["title"],
            "已登记线索": sum(lead.get("campaign_id") == campaign_id for lead in leads),
            "已付客户": len({order.customer_id for order in sourced_paid}),
            "已付订单": len(sourced_paid),
            "已结算订单": sum(order.campaign_id == campaign_id for order in settled),
            "模拟推广支出": sum(e["cents"] for e in cash
                             if e["entity_id"] == campaign_id and e["direction"] == "out") / 100,
        })
    rows = [{
        "业务": "酒店餐饮", "订单": _supplier_title(order), "订单编号": order.id,
        "客户已付金额": order.customer_paid_cents / 100,
        "已确认净服务费收入": (order.service_fee_cents - order.discount_cents) / 100,
        "已结清供方支出": order.supplier_paid_cents / 100,
    } for order in settled]
    return metrics, campaign_rows, rows


def load_owner_dashboard(*, activity_tracking=False):
    """Summarize real saved demo records and current jobs without changing business state."""
    saved = sorted(storage.saved_case_snapshots(), key=lambda row: row.get("updated_at", ""), reverse=True)
    cases = [TravelCase.model_validate(row["case"]) for row in saved]
    suppliers, leads, campaigns, cash = _company_snapshot()
    travel, travel_rows = _travel_results(cases)
    supplier_metrics, campaign_rows, supplier_rows = _supplier_results(suppliers, leads, campaigns, cash)

    orders, tasks = [], []
    paid_cases, settled_cases = set(), set()
    supplier_by_id = {order.id: order for order in suppliers}
    supplier_by_case = {}
    for supplier in suppliers:
        if supplier.case_id and supplier.status != "cancelled":
            supplier_by_case.setdefault(supplier.case_id, supplier)
    linked = set()
    for case, record in zip(cases, saved):
        supplier = supplier_by_id.get(case.supplier_order_id)
        if (not case.supplier_order_id and case.service_mode == "travel_planning" and case.status == CaseStatus.COMPLETED
                and not (case.procurement_order or case.journey_demo)):
            supplier = supplier_by_case.get(case.id)
        row = _order_row(
            "case", case.id, _case_title(case), _case_flow(case, supplier, tracking=activity_tracking),
            case_id=case.id, supplier_order_id=supplier.id if supplier else None,
        )
        row["case_status"] = case.status.value
        row["display_number"] = case_number(record["case"])
        if case.procurement_order:
            row["procurement_order_id"] = case.procurement_order.id
            if any(p.kind in {"customer_deposit", "customer_balance"} and p.amount_yuan > 0
                   for p in case.procurement_order.payments):
                paid_cases.add(case.id)
            if case.procurement_order.settled:
                settled_cases.add(case.id)
        _order_details(row, case, supplier)
        orders.append(row)
        tasks.extend(_case_tasks(row, case))
        if supplier:
            linked.add(supplier.id)
            if supplier.status != "cancelled" and supplier.customer_paid_cents > 0:
                paid_cases.add(case.id)
            if supplier.status == "completed":
                settled_cases.add(case.id)
            tasks.extend(_tasks_for(row, supplier.id, supplier.handoffs))
    for supplier in suppliers:
        if supplier.id in linked:
            continue
        row = _order_row(
            "supplier", supplier.id, _supplier_title(supplier), _supplier_flow(supplier),
            case_id=supplier.case_id, supplier_order_id=supplier.id,
        )
        row["display_number"] = supplier_number(supplier.id, supplier.need.check_in, supplier.need.check_out)
        _order_details(row, None, supplier)
        orders.append(row)
        tasks.extend(_tasks_for(row, supplier.id, supplier.handoffs))

    task_order = {"running": 0, "failed": 1, "interrupted": 2, "completed": 3}
    tasks.sort(key=lambda task: 4 if task.get("recovered") else task_order.get(task["status"], 4))
    return {
        "travel": travel, "supplier": supplier_metrics, "orders": orders, "tasks": tasks,
        "main_orders": {"paid": len(paid_cases), "settled": len(settled_cases)},
        "supplier_cash": company_store.summarize_cash_events(cash),
        "campaigns": campaign_rows, "settled": travel_rows + supplier_rows,
    }
