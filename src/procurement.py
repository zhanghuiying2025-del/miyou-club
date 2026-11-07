"""Explicit supplier fixtures + computed financial policy + human approval gates."""
import asyncio
import json
import math
import time

from src.agents import build_agent_team, run_structured, task_timeout
from src.orchestration import assign
from src.models import (BookingDiscussion, BookingSpeech, CaseStatus, ProcurementOrder, TaskHandoff,
                        ResourcePackage, ResourceReservation, SimulatedPayment, TravelCase)


GRADE_NAMES = {1: "经济型双人标准间", 2: "舒适型双人标准间", 3: "高品质双人标准间"}


def package_cost(package: ResourcePackage) -> float:
    return round(package.venue_cost + package.vehicle_cost + package.hotel_cost + package.other_cost, 2)


def get_package(order: ProcurementOrder, package_id: str | None = None) -> ResourcePackage:
    return next(x for x in order.packages if x.id == (package_id or order.selected_package))


def financials(order: ProcurementOrder, package: ResourcePackage) -> dict:
    resources = package_cost(package)
    ai_cost, reserve = round(order.quote_yuan * .01, 2), round(order.quote_yuan * .05, 2)
    execution_cost = sum(p.amount_yuan for p in order.payments if p.kind == "activity_expense")
    cost = round(resources + ai_cost + reserve + execution_cost, 2)
    profit = round(order.quote_yuan - cost, 2)
    return {"cost": cost, "resource_cost": resources, "ai_cost": ai_cost, "reserve": reserve,
            "service_fee": round(order.quote_yuan - resources, 2),
            "profit": profit, "margin": profit / order.quote_yuan,
            "extra_cost": round(resources - order.baseline_cost, 2),
            "extra_ratio": (resources - order.baseline_cost) / order.quote_yuan}


def owner_reasons(order: ProcurementOrder, package: ResourcePackage) -> list[str]:
    numbers = financials(order, package)
    reasons = []
    if numbers["margin"] < order.auto_min_margin - 1e-9:
        reasons.append(f"预计利润率{numbers['margin']:.1%}低于{order.auto_min_margin:.0%}授权线")
    if numbers["extra_ratio"] > order.auto_max_extra_cost_ratio + 1e-9:
        reasons.append(f"额外成本占合同价{numbers['extra_ratio']:.1%}，超过{order.auto_max_extra_cost_ratio:.0%}授权线")
    if package.transfer_minutes - 20 > order.auto_max_added_transfer:
        reasons.append(f"单程接驳增加{package.transfer_minutes - 20}分钟，超过内部调度授权")
    return reasons


def customer_reasons(order: ProcurementOrder, package: ResourcePackage) -> list[str]:
    reasons = []
    if order.room_count and package.hotel_grade < order.confirmed_hotel_grade:
        reasons.append(f"住宿由{GRADE_NAMES[order.confirmed_hotel_grade]}改为{GRADE_NAMES[package.hotel_grade]}")
    if order.confirmed_same_hotel and not package.same_hotel:
        reasons.append("无法满足全员同酒店入住")
    if package.transfer_minutes > order.confirmed_max_transfer:
        reasons.append(f"接驳{package.transfer_minutes}分钟超过客户确认的{order.confirmed_max_transfer}分钟上限")
    if order.execution_schedule and order.execution_schedule != order.original_schedule:
        reasons.append("资源变化导致行程调整，请确认更新后的执行行程")
    return reasons


def add_message(order, actor, stage, public, owner=None, source="rule"):
    order.discussion.append(BookingDiscussion(actor=actor, stage=stage,
        public_message=public, owner_message=owner or public, source=source))


def start_procurement(case: TravelCase, quote: float, nights: int, grade: int,
                      same_hotel: bool = True, max_transfer: int = 90) -> ProcurementOrder:
    if case.status != CaseStatus.COMPLETED or case.council_decision is None or not case.council_decision.schedule:
        raise ValueError("请先确认最终方案")
    if case.procurement_order is not None:
        return case.procurement_order  # Refresh/repeated click cannot collect another deposit.
    if case.supplier_order_id or case.booking_route == "supplier":
        raise ValueError("本案已进入酒店餐饮预订，请继续处理原订单")
    if not math.isfinite(quote) or quote <= 0 or nights not in range(8) or grade not in GRADE_NAMES or max_transfer < 0:
        raise ValueError("模拟报价或住宿条件无效")
    quote = round(quote, 2)
    if quote <= 0 or not math.isfinite(max_transfer):
        raise ValueError("金额至少为0.01元，接驳时间必须有效")
    count = case.requirements.participant_count if case.requirements and case.requirements.participant_count else 30
    if count <= 0:
        raise ValueError("参与人数必须为正数")
    rooms = math.ceil(count / 2) if nights else 0
    partial = max(0, rooms - max(1, math.ceil(rooms * 0.35)))
    venue, vehicle, hotel, other = [round(quote * ratio, 2) for ratio in (0.12, 0.18, 0.34 if nights else 0, 0.16)]

    def package(pid, label, hotel_name, hotel_grade, transfer, hotel_cost, vehicle_cost, capacity):
        return ResourcePackage(id=pid, label=label, hotel=hotel_name, hotel_grade=hotel_grade,
            available_rooms=capacity, transfer_minutes=transfer, venue_cost=venue,
            vehicle_cost=vehicle_cost, hotel_cost=hotel_cost, other_cost=other,
            note="固定演示供方报价，按模拟合同价生成；非市场实时价格")

    packages = [package("original", "原资源组合", "模拟原酒店", grade, 20, hotel, vehicle, partial)]
    if rooms:
        packages.extend([
            package("far", "同级远端酒店＋原调度", "模拟同级远端酒店", grade, 70, round(hotel + quote * .12, 2), round(vehicle + quote * .06, 2), rooms),
            package("far_optimized", "同级远端酒店＋优化车辆排班", "模拟同级远端酒店", grade, 70, round(hotel + quote * .12, 2), round(vehicle + quote * .03, 2), rooms),
            package("near_budget", "近端酒店＋保留原车辆", "模拟近端替代酒店", max(1, grade - 1), 20, round(max(0, hotel - quote * .02), 2), vehicle, rooms),
        ])
    order = ProcurementOrder(plan_version=case.plan_version, last_step_at=time.time(),
        case_id=case.id, original_schedule=list(case.council_decision.schedule),
        execution_schedule=list(case.council_decision.schedule),
        quote_yuan=round(quote, 2), deposit_yuan=round(quote * .6, 2), baseline_cost=package_cost(packages[0]),
        room_count=rooms, nights=nights, confirmed_hotel_grade=grade,
        confirmed_same_hotel=same_hotel, confirmed_max_transfer=max_transfer,
        packages=packages, reservations=[
            ResourceReservation(kind="venue", supplier="模拟活动场地", requested_quantity=1, available_quantity=1),
            ResourceReservation(kind="vehicle", supplier="模拟车辆供应商", requested_quantity=max(1, math.ceil(count / 45)), available_quantity=max(1, math.ceil(count / 45))),
            ResourceReservation(kind="hotel", supplier="模拟原酒店", requested_quantity=rooms, available_quantity=partial),
        ])
    order.payments.append(SimulatedPayment(id=f"{order.id}:deposit", kind="customer_deposit", amount_yuan=order.deposit_yuan))
    add_message(order, "财务核算（程序）", "模拟预付款", "已记录模拟预付款，开始确认场地、车辆和住宿资源。",
        f"模拟现金流入{order.deposit_yuan:.2f}元；同额记录预收履约义务。尚未履约，收入和已实现利润均为0。", "simulated_payment")
    case.procurement_order = order
    return order


def advance_supplier(order: ProcurementOrder, now: float | None = None) -> bool:
    if order.status != "processing" or order.step >= 3:
        return False
    now = time.time() if now is None else now
    if now - order.last_step_at < 1:
        return False
    item = order.reservations[order.step]
    if item.kind != "hotel":
        item.status = "confirmed"
        item.confirmation_id = f"SIM-{order.id}-{item.kind}"
        add_message(order, "供应商模拟回执", "资源确认", f"{item.supplier}确认成功，数量{item.requested_quantity}。", source="supplier_fixture")
    elif not order.room_count:
        item.status = "not_required"
        add_message(order, "供应商模拟回执", "住宿检查", "本订单无需住宿，场地和车辆已确认。", source="supplier_fixture")
        order.status = "ready"
    else:
        item.status = "partial_available"
        item.note = "只反馈部分房量，不代表已预订全部客房"
        add_message(order, "供应商模拟回执", "酒店房量不足", f"原酒店需要{order.room_count}间，只剩{item.available_quantity}间，无法确认全部住宿。", source="supplier_fixture")
    order.step += 1
    order.last_step_at = now
    return True


def turn_context(order: ProcurementOrder) -> tuple[str, str, str, str]:
    far, optimized = get_package(order, "far"), get_package(order, "far_optimized")
    before, after = financials(order, far), financials(order, optimized)
    if order.step == 3:
        return "fulfillment", "履约执行", "我查询了模拟候选资源，建议先核验同级替代酒店。", f"替代酒店房量{far.available_rooms}间，满足{order.room_count}间需求；住宿等级不变，待审核接驳和成本。"
    if order.step == 4:
        chosen = get_package(order)
        return "planner", "多角色策划", "我会检查替代资源对行程的影响，必要时局部调整，提交客户确认。", f"候选{chosen.id}单程接驳由20分钟变为{chosen.transfer_minutes}分钟；不得改变客户硬性条件，场地与时间待核实。"
    if order.step == 5:
        chosen = get_package(order)
        current = financials(order, chosen)
        return "manager", "财务核算（程序）", "我已完成报价与资源的程序核算，涉及服务变化或超出内部权限时会交相应人员确认。", f"当前候选{chosen.id}综合预计成本{current['cost']:.2f}元；预计利润{current['profit']:.2f}元、利润率{current['margin']:.1%}。预付款不是收入。"
    if order.step == 6:
        return "manager", "项目经理", "我建议比较车辆排班优化和近端备选；超出授权交老板，服务条件变化交客户。", f"远端车辆优化备选成本为{after['cost']:.2f}元，相对未优化远端组合{before['cost']:.2f}元有所下降；当前选择{order.selected_package}，请按候选实际计算比较。近端若降低等级须客户同意。"
    raise ValueError("当前没有等待讨论的资源步骤")


def financial_review(order: ProcurementOrder) -> BookingSpeech:
    if order.status != "processing" or order.step != 5:
        raise ValueError("当前不是财务核查步骤")
    _, actor, public, facts = turn_context(order)
    reasons = owner_reasons(order, get_package(order))
    return BookingSpeech(message=public, handoffs=[TaskHandoff(case_id=order.case_id or order.id,
        sender="项目经理", receiver=actor, task="资源落实步骤5 · 程序财务核查", input_summary=facts,
        expected_result="FinancialCalculation", status="completed", source="rule",
        result_summary="；".join(reasons) or "程序核算未超内部经营授权；客户条件另行检查")])


async def request_booking_speech(order: ProcurementOrder) -> tuple[BookingSpeech, str]:
    if order.step == 5:
        return financial_review(order), "rule"
    role, actor, public, facts = turn_context(order)
    async def execute():
        team = build_agent_team()
        agent = getattr(team, role)
        candidates = [{**p.model_dump(), "financials": financials(order, p),
            "owner_reasons": owner_reasons(order, p), "customer_reasons": customer_reasons(order, p)} for p in order.packages]
        records = []
        result = await assign(agent, f"你正在参与模拟预付款后的资源预订协同。你的身份是{actor}。\n"
            f"工具确认事实：{facts}\n客户已确认住宿等级：{GRADE_NAMES[order.confirmed_hotel_grade]}。\n"
            f"当前选择：{order.selected_package}；步骤：{order.step}。\n"
            f"模拟供方工具候选及程序财务计算：{json.dumps(candidates, ensure_ascii=False)}\n"
            f"原行程：{json.dumps(order.original_schedule, ensure_ascii=False)}\n"
            f"已收到的业务记录：{json.dumps([x.owner_message for x in order.discussion[-5:]], ensure_ascii=False)}\n"
            "步骤3由履约选择有效候选package_id；步骤4由策划仅为当前候选给出最多两项行程adjustments；"
            "步骤5经理核查，步骤6经理选择最终建议package_id；其他步骤package_id可为空。"
            "message是对客第一人称意见，不超过100字；只说服务影响或选择，不披露内部成本、利润和资金。"
            "步骤5已经由程序计算，不需要模型解释。不得改写工具金额、虚构已联系外界或声称已批准；不要输出隐藏思考过程。", BookingSpeech,
            records, order.case_id or order.id,
            "项目经理" if role != "manager" else "履约执行", actor,
            f"资源落实步骤{order.step}", facts, timeout=task_timeout(12), runner=run_structured)
        validate_booking_choice(order, result)
        result.handoffs = records
        return result
    try:
        return await execute(), "agent"
    except Exception as exc:
        from src.models import TaskHandoff
        return BookingSpeech(message=public, handoffs=[TaskHandoff(
            case_id=order.case_id or order.id, sender="项目经理", receiver=actor,
            task=f"资源落实步骤{order.step}", input_summary=facts,
            expected_result="BookingSpeech", status="failed", error=str(exc)[:300], source="rule_fallback")]), "rule_fallback"


def validate_booking_choice(order, speech):
    if speech.package_id is not None:
        if order.step not in {3, 6}:
            raise ValueError("当前步骤无权改变资源组合")
        if not any(p.id == speech.package_id and p.available_rooms >= order.room_count for p in order.packages):
            raise ValueError("Agent选择了不存在或房量不足的资源")
    elif order.step in {3, 6}:
        raise ValueError("资源选择缺少package_id")
    indices = [a.schedule_index for a in speech.adjustments]
    if speech.adjustments and order.step != 4:
        raise ValueError("只有策划步骤可以建议修改行程")
    if len(indices) > 2 or len(set(indices)) != len(indices) or any(i >= len(order.original_schedule) for i in indices):
        raise ValueError("策划修改超出原行程范围")
    if any(not a.replacement.strip() for a in speech.adjustments):
        raise ValueError("替代安排不能为空")


def finish_turn(order: ProcurementOrder, expected_step: int, speech: BookingSpeech, source: str) -> None:
    if order.status != "processing" or order.step != expected_step or expected_step not in {3, 4, 5, 6}:
        raise ValueError("讨论步骤已变化，不能重复应用")
    if source not in {"agent", "rule_fallback", "rule"} or (source == "rule" and expected_step != 5):
        raise ValueError("未知的业务结果来源")
    role, actor, public, facts = turn_context(order)
    if source in {"agent", "rule"}:
        validate_booking_choice(order, speech)
    order.handoffs.extend(speech.handoffs)
    public_message = speech.message if source != "rule_fallback" else public
    if any(word in public_message for word in ("利润", "成本", "现金", "毛利", "授权线")):
        public_message = (f"我建议考虑{get_package(order, speech.package_id).label}，仍需检查相关条件并完成确认。"
            if speech.package_id else public)
    add_message(order, actor, "资源协同", public_message,
        facts + "\n业务意见：" + speech.message, source)
    if order.step == 3:
        order.selected_package = speech.package_id if source == "agent" else "far"
    if order.step == 4 and speech.adjustments:
        for adjustment in speech.adjustments:
            order.execution_schedule[adjustment.schedule_index] = adjustment.replacement
        order.schedule_approved = False
    if order.step == 6:
        old = get_package(order)
        order.selected_package = speech.package_id if source == "agent" else "far_optimized"
        if get_package(order).transfer_minutes != old.transfer_minutes:
            order.execution_schedule = list(order.original_schedule)
            order.schedule_approved = False
    order.step += 1
    if order.step == 7:
        route_order(order)


def route_order(order: ProcurementOrder) -> None:
    if order.status == "ready":
        return
    if any(x.kind in {"venue", "vehicle"} and x.status != "confirmed" for x in order.reservations):
        raise ValueError("场地和车辆尚未确认")
    if order.room_count and order.step < 7:
        raise ValueError("团队尚未完成资源审核与优化，不能越过协同流程")
    selected = get_package(order)
    if selected.available_rooms < order.room_count:
        raise ValueError("房量不足，不能确认该资源组合")
    if owner_reasons(order, selected) and order.owner_approved_package != selected.id:
        order.status = "waiting_owner"
        add_message(order, "项目经理", "超出内部授权", "团队已尝试优化，当前方案仍超内部授权，交老板决定。", "；".join(owner_reasons(order, selected)))
    elif customer_reasons(order, selected) and order.customer_approved_package != selected.id:
        order.status = "waiting_customer"
        add_message(order, "项目经理", "服务条件变更", "替代组合影响已确认条件，请客户确认后再预订。", "；".join(customer_reasons(order, selected)))
    else:
        for item in order.reservations:
            if item.kind == "vehicle":
                item.note = "原车辆资源保留；若选择优化组合，则已模拟调整排班"
            if item.kind == "hotel" and order.room_count:
                item.supplier, item.status, item.available_quantity = selected.hotel, "confirmed", selected.available_rooms
                item.note = "模拟预订成功；所需商业与客户授权均已满足"
                item.confirmation_id = f"SIM-{order.id}-hotel-{selected.id}"
        order.status = "ready"
        add_message(order, "供应商模拟回执", "资源预订完成", "场地、车辆及所需住宿资源已模拟确认，可以进入游玩演示。", source="supplier_fixture")


def owner_decide(order: ProcurementOrder, approved: bool, package_id: str | None = None) -> None:
    if order.status != "waiting_owner":
        raise ValueError("当前没有等待老板处理的预订")
    if not approved:
        order.status = "held"
        add_message(order, "老板", "暂不批准", "公司暂未批准当前资源组合，订单保持待处理；未自动退款或继续预订。", source="owner")
        return
    selected = get_package(order, package_id)
    if selected.available_rooms < order.room_count:
        raise ValueError("老板批准也不能补足不存在的房量")
    if selected.transfer_minutes != get_package(order).transfer_minutes:
        order.execution_schedule = list(order.original_schedule)
    order.selected_package = selected.id
    order.owner_approved_package = selected.id
    order.customer_approved_package = None
    order.schedule_approved = False
    add_message(order, "老板", "商业决策", "老板已批准一套资源处理建议；涉及客户已确认条件的变化仍需客户同意。", selected.label, "owner")
    route_order(order)


def customer_decide(order: ProcurementOrder, approved: bool) -> None:
    if order.status != "waiting_customer":
        raise ValueError("当前没有等待客户确认的预订变更")
    if approved:
        order.customer_approved_package = order.selected_package
        order.schedule_approved = True
        add_message(order, "客户", "确认变更", "客户同意本次明确列出的条件变更。", source="customer")
        route_order(order)
    else:
        order.customer_approved_package = None
        order.owner_approved_package = None
        order.status = "waiting_owner"
        add_message(order, "客户", "拒绝变更", "客户不接受条件变化，请公司重新选择资源；未完成住宿预订。", source="customer")


def resume_order(order: ProcurementOrder) -> None:
    if order.status != "held":
        raise ValueError("只有暂停的预订可以恢复处理")
    order.status = "waiting_owner"
    order.owner_approved_package = None
    order.customer_approved_package = None
    add_message(order, "老板", "恢复处理", "公司恢复资源安排，原预付款与供方回执保留，涉及条件变化仍需重新确认。", source="owner")


def cash_summary(order):
    numbers = financials(order, get_package(order))
    received = sum(p.amount_yuan for p in order.payments if p.kind in {"customer_deposit", "customer_balance"})
    paid = sum(p.amount_yuan for p in order.payments if p.kind in {"supplier_payment", "ai_operations", "activity_expense"})
    supplier_paid = sum(p.amount_yuan for p in order.payments if p.kind == "supplier_payment")
    return {"opening_cash": order.opening_cash_yuan, "received": received, "paid": paid,
        "cash": round(order.opening_cash_yuan + received - paid, 2),
        "supplier_due": round(max(0, numbers['resource_cost'] - supplier_paid), 2),
        "customer_due": round(max(0, order.quote_yuan - received), 2),
        "revenue": order.quote_yuan if order.settled else 0,
        "profit": round(order.quote_yuan - paid, 2) if order.settled else 0,
        "advance_obligation": 0 if order.settled else received}


def pay_suppliers(order):
    if order.status != "ready" or order.settled:
        raise ValueError("请先完成资源确认，且订单尚未结算")
    if any(r.status not in {"confirmed", "not_required"} for r in order.reservations):
        raise ValueError("供方回执不完整，不能付款")
    if any(p.kind == "supplier_payment" for p in order.payments):
        return
    numbers = financials(order, get_package(order))
    total = round(numbers['resource_cost'] + numbers['ai_cost'], 2)
    if cash_summary(order)['cash'] - total < 15000:
        raise ValueError("支付后将低于15000元模拟生存准备金，请先安排资金")
    order.payments.extend([
        SimulatedPayment(id=f"{order.id}:supplier", kind="supplier_payment", amount_yuan=numbers['resource_cost']),
        SimulatedPayment(id=f"{order.id}:ai", kind="ai_operations", amount_yuan=numbers['ai_cost']),
    ])
    add_message(order, "老板", "模拟支付授权", "已按确认组合记录模拟供方支出，无真实付款。", source="simulated_payment")


def confirm_activity_cost(case, event_id, amount):
    order, demo = case.procurement_order, case.journey_demo
    if not order or order.settled or not demo:
        raise ValueError("当前没有可核对的活动费用")
    event = next((e for e in demo.events if e.id == event_id), None)
    if not event or event.status != "resolved" or not event.response:
        raise ValueError("请先完成本次活动调整的必要审批")
    if not math.isfinite(amount) or amount < 0:
        raise ValueError("费用必须为有效非负数")
    amount = round(amount, 2)
    pid = f"{order.id}:activity:{event_id}"
    existing = next((p for p in order.payments if p.id == pid), None)
    if existing:
        if existing.amount_yuan != amount:
            raise ValueError("已记录费用不能直接改写")
        return
    if cash_summary(order)['cash'] - amount < 15000:
        raise ValueError("该支出会突破生存准备金，不能直接支付")
    order.payments.append(SimulatedPayment(id=pid, kind="activity_expense", amount_yuan=amount))
    event.confirmed_cost_yuan = amount
    add_message(order, "老板", "活动费用核对", "本次活动变化的费用已由老板核对并记录模拟收支。",
        f"事件{event_id}，模拟支出{amount:.2f}元；无真实支付", "simulated_payment")


def settle_order(case, *, delivery_confirmed=False):
    order, demo = case.procurement_order, case.journey_demo
    if order is None or order.status != "ready":
        raise ValueError("必须完成预订与本次活动，才能结算")
    delivery_ready = demo.status == "finished" if demo else delivery_confirmed
    if not delivery_ready:
        raise ValueError("必须完成预订与本次活动，才能结算")
    if order.settled:
        return
    if not any(p.kind == "supplier_payment" for p in order.payments):
        raise ValueError("请先由老板确认模拟供应商支出")
    if demo and any(e.response and (e.cost_confirmation_required or e.response.needs_change) and e.confirmed_cost_yuan is None
                    for e in demo.events if e.status == "resolved"):
        raise ValueError("活动变化的新增或未知费用尚未核对，请老板先确认费用")
    order.payments.append(SimulatedPayment(id=f"{order.id}:balance", kind="customer_balance",
        amount_yuan=round(order.quote_yuan - order.deposit_yuan, 2)))
    order.settled = True
    add_message(order, "客户", "模拟尾款与完单", "活动完成，已模拟支付尾款；公司确认本单收入与结果。", source="simulated_payment")
