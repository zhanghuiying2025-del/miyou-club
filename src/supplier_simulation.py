"""Executor-owned simulated hotel/meal contacts; program-owned stock and money."""
from contextlib import closing
from datetime import date
from hashlib import sha256
import json
import sqlite3
import time

from src import company_store, storage
from src.company_store import transaction, read_record, SimulationConflict, cash_event
from src.models import CaseStatus
from src.supplier_models import (BookingNeed, SupplierWorld, Hotel, Restaurant, SupplierOrder,
    SupplierQuote, ContactPlan, ContactAction, identifier)
from src.operations import redeem_coupon, owner_only


def signature(value):
    return sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest()


def fixed_world(need):
    rooms=need.rooms
    hotels=[Hotel(id="hotel-a",name="模拟湖畔酒店A",rooms_by_night={d:max(0,rooms-15) if i==1 else rooms+5 for i,d in enumerate(need.nights)},
        room_rate_cents=32000,grade=2,plaza_capacity=need.people,bonfire="不支持",fireworks="不支持"),
        Hotel(id="hotel-b",name="模拟山居酒店B",rooms_by_night={d:rooms+10 for d in need.nights},
        room_rate_cents=40000,grade=2,plaza_capacity=need.people+30,bonfire="待核实",fireworks="不支持",transfer_minutes=40),
        Hotel(id="hotel-c",name="模拟会务酒店C",rooms_by_night={d:rooms+5 for d in need.nights},
        room_rate_cents=46000,grade=3,plaza_capacity=need.people+20,bonfire="支持",fireworks="待核实",transfer_minutes=25)]
    restaurants=[Restaurant(id="meal-a",name="模拟餐厅A",seats_by_meal={m.key:max(0,need.people-30) for m in need.meals},
        price_per_person_cents=8000,menu=["荤菜搭配","时蔬","主食与汤"],dietary_note="偏辣，清淡餐与过敏需事先确认"),
        Restaurant(id="meal-b",name="模拟餐厅B",seats_by_meal={m.key:need.people+20 for m in need.meals},
        price_per_person_cents=10000,menu=["荤菜三款","时蔬两款","主食与汤","清淡备选"],dietary_note=need.dietary)]
    return SupplierWorld(hotels=hotels if need.include_hotel else [],restaurants=restaurants)


def validate_world(world,need):
    world=SupplierWorld.model_validate(world)
    if need.include_hotel and len(world.hotels)<2:
        raise ValueError("住宿询价至少需要两家模拟酒店")
    if not need.include_hotel and world.hotels:
        raise ValueError("仅餐饮需求不需要生成酒店")
    suppliers=[*world.hotels,*world.restaurants]
    if len({s.id for s in suppliers}) != len(suppliers):
        raise ValueError("模拟供方编号不能重复")
    for hotel in world.hotels:
        if set(hotel.rooms_by_night) != set(need.nights) or any(isinstance(v,bool) or not 0<=v<=10000 for v in hotel.rooms_by_night.values()):
            raise ValueError("酒店必须逐晚给出有效房量")
        if not hotel.name.startswith("模拟"):
            hotel.name="模拟"+hotel.name
    for restaurant in world.restaurants:
        if set(restaurant.seats_by_meal) != {m.key for m in need.meals} or any(isinstance(v,bool) or not 0<=v<=10000 for v in restaurant.seats_by_meal.values()):
            raise ValueError("餐厅必须逐餐给出有效接待量")
        if not restaurant.name.startswith("模拟"):
            restaurant.name="模拟"+restaurant.name
    return world


def load_order(order_id):
    record,revision=read_record("supplier_order",order_id)
    return SupplierOrder.model_validate(record),revision


def order_for_case(case_id):
    path = company_store.COMPANY_DB
    if not path.exists():
        return None
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='records'").fetchone():
            return None
        orders = company_store.Records(connection).all("supplier_order")
    return next((order["id"] for order in orders
                 if order.get("case_id") == case_id and order["status"] != "cancelled"), None)


def find_case_booking(case):
    if case.supplier_order_id:
        return case.supplier_order_id
    if (case.service_mode == "travel_planning" and case.status == CaseStatus.COMPLETED
            and not (case.procurement_order or case.journey_demo)):
        return order_for_case(case.id)
    return None


def linked_case_for_order(case_id, order_id):
    case = storage.load_case_from_disk(case_id) if case_id else None
    return case if case and find_case_booking(case) == order_id else None


def _load(tx,order_id,expected_revision=None):
    record,revision=tx.get("supplier_order",order_id)
    if expected_revision is not None and revision!=expected_revision:
        raise SimulationConflict("订单已变化，旧的Agent结果不能覆盖新状态，请刷新重试")
    return SupplierOrder.model_validate(record),revision


def _save(tx,order,revision):
    tx.save("supplier_order",order.id,order.model_dump(mode="json"),revision)


def create_order(need,customer_id,service_fee_cents=500000,campaign_id=None,case_id=None,use_fixed=True):
    need=BookingNeed.model_validate(need)
    customer_id=customer_id.strip()
    if not customer_id.strip() or not isinstance(service_fee_cents,int) or not 0<=service_fee_cents<=10000000:
        raise ValueError("客户编号或服务费无效")
    order=SupplierOrder(customer_id=customer_id,need=need,service_fee_cents=service_fee_cents,
        campaign_id=campaign_id,case_id=case_id,simulation_day=need.check_in)
    if use_fixed:
        order.world=fixed_world(need)
        order.world_source="fixed_simulation"
    with transaction() as tx:
        lead,_=tx.get("lead",customer_id)
        if campaign_id != lead.get("campaign_id"):
            raise ValueError("订单来源应与已登记潜客一致")
        if case_id and any(o.get("case_id")==case_id and o["status"]!="cancelled" for o in tx.all("supplier_order")):
            raise ValueError("该案件已经关联酒店餐饮订单，请打开原订单")
        tx.save("supplier_order",order.id,order.model_dump(mode="json"))
        tx.event(f"create:{order.id}","business",order.id,{"action":"创建模拟酒店餐饮需求","case_id":case_id})
    return order


async def generate_world(order_id):
    from src.agents import build_agent_team,run_structured
    order,revision=load_order(order_id)
    if order.world is not None:
        return order
    started=time.monotonic()
    world=await run_structured(build_agent_team().fulfillment,
        "这是执行Agent调用的模拟供方场景生成工具。一次生成2至3家虚构餐厅；需要住宿时再生成2至3家虚构酒店。"
        "名称以模拟开头，编号唯一；每家覆盖全部所需夜晚或餐次。金额单位是分，不是元。"
        "需要住宿时至少一家酒店容量满足需求，有第二晚时至少一家酒店第二晚房量不足。"
        "至少一家餐厅容量满足需求。火源活动条件分别列支持/不支持/待核实；"
        "不得声称取得真实许可或真实预订。菜单简短。该场景随后保存，后续库存和金额均由程序维护。"
        f"已确认需求：{order.need.model_dump_json()}；每晚需要双床房{order.need.rooms}间。"
        "若include_hotel为false，hotels必须为空。"
        f"酒店夜晚键：{json.dumps(order.need.nights)}；餐次键：{json.dumps([m.key for m in order.need.meals],ensure_ascii=False)}",SupplierWorld)
    world=validate_world(world,order.need)
    with transaction() as tx:
        current,rev=_load(tx,order_id,revision)
        current.world,current.world_source=world,"model_simulation"
        current.handoffs.append({"actor":"履约执行","task":"生成并保存模拟供应商场景","source":"model_simulation",
            "seconds":round(time.monotonic()-started,2),"status":"completed"})
        _save(tx,current,rev)
    return current


def install_fixed_world(order_id):
    with transaction() as tx:
        order,revision=_load(tx,order_id)
        if order.world is not None:
            return order
        order.world,order.world_source=fixed_world(order.need),"fixed_simulation"
        _save(tx,order,revision)
    return order


def _supplier(order,kind,supplier_id):
    if order.world is None:
        raise ValueError("请先生成或载入模拟供应商场景")
    values=order.world.hotels if kind=="hotel" else order.world.restaurants
    found=next((v for v in values if v.id==supplier_id),None)
    if not found:
        raise ValueError("执行Agent选择了不存在或类型不匹配的供应商")
    return found


def _quote(order,action,source):
    provider=_supplier(order,action.kind,action.supplier_id)
    blockers,conditions,details=[],{},[]
    meal_keys=[]
    if action.kind=="hotel":
        for night in order.need.nights:
            available=provider.rooms_by_night[night]
            details.append(f"{night}可用{available}间，需要{order.need.rooms}间")
            if available<order.need.rooms:
                blockers.append(f"{night}房量不足")
        total=provider.room_rate_cents*order.need.rooms*len(order.need.nights)
        if provider.grade<order.need.hotel_grade:
            conditions["hotel_grade"]="客户确认接受较低住宿等级"
        for requested,field,title in [(order.need.require_bonfire,"bonfire","篝火"),(order.need.require_fireworks,"fireworks","烟花")]:
            if requested:
                conditions[field]=f"{title}能力为{getattr(provider,field)}，本酒店客房合同不含该活动；客户明确接受取消本单该项，替代活动另行确认"
        details += [f"双床房{provider.room_rate_cents/100:.2f}元/间夜；早餐{'包含' if provider.breakfast_included else '不包含'}",
            f"广场{provider.plaza_capacity}人；篝火{provider.bonfire}，烟花{provider.fireworks}；模拟接驳{provider.transfer_minutes}分钟",
            "报价仅含客房，不含活动场地及火源活动费用",provider.cancellation_terms]
    else:
        meal_keys=action.meal_keys or [m.key for m in order.need.meals]
        if len(set(meal_keys))!=len(meal_keys) or not set(meal_keys)<={m.key for m in order.need.meals}:
            raise ValueError("询价引用了不存在或重复的餐次")
        for key in meal_keys:
            capacity=provider.seats_by_meal[key]
            details.append(f"{key}可接待{capacity}人，需要{order.need.people}人")
            if capacity<order.need.people:
                blockers.append(f"{key}餐位不足")
        total=provider.price_per_person_cents*order.need.people*len(meal_keys)
        details += [f"每餐{provider.price_per_person_cents/100:.2f}元/人", "菜单："+"、".join(provider.menu),provider.dietary_note,provider.cancellation_terms]
    quote=SupplierQuote(id=identifier("QUOTE"),kind=action.kind,supplier_id=provider.id,supplier_name=provider.name,
        meal_keys=meal_keys,total_cents=total,capacity_ok=not blockers,blockers=blockers,conditions=conditions,details=details,
        inventory_signature=signature(provider.model_dump(mode="json")),valid_until_tick=order.tick+20)
    order.quotes.append(quote)
    order.contacts.append({"actor":"履约执行","source":source,"supplier_id":provider.id,"request":action.message,
        "reply":"模拟供方工具回执："+"；".join(details+blockers),"quote_id":quote.id,"tick":order.tick})
    return quote


def contact_with_plan(order_id,plan,source="rule",expected_revision=None):
    if source not in {"agent","rule"}:
        raise ValueError("未知联系来源")
    plan=ContactPlan.model_validate(plan)
    with transaction() as tx:
        order,revision=_load(tx,order_id,expected_revision)
        if order.confirmations:
            raise ValueError("已确认的预订不可重新询价覆盖")
        order.tick+=1
        for action in plan.actions:
            _quote(order,action,source)
        order.handoffs.append({"actor":"履约执行","task":"模拟联系与询价","source":source,
            "summary":plan.summary,"status":"completed","contacts":len(plan.actions)})
        _save(tx,order,revision)
    return order


def contact_all(order_id):
    order,revision=load_order(order_id)
    if not order.world:
        raise ValueError("请先准备模拟供应商")
    actions=[ContactAction(kind="hotel",supplier_id=h.id,message=f"模拟询价：{len(order.need.nights)}晚，每晚{order.need.rooms}间同性双床安排，请核对活动场地。") for h in order.world.hotels]
    actions += [ContactAction(kind="restaurant",supplier_id=r.id,message=f"模拟询价：{order.need.people}人，指定餐次，{order.need.dietary}") for r in order.world.restaurants]
    return contact_with_plan(order_id,ContactPlan(summary="程序执行批量询价，不调用模型",actions=actions),"rule",revision)


async def agent_contact(order_id,instruction="比较酒店连续房量及餐饮容量，优先补齐可执行报价"):
    from src.agents import build_agent_team,run_structured
    order,revision=load_order(order_id)
    if not order.world:
        raise ValueError("请先准备模拟供应商")
    plan=await run_structured(build_agent_team().fulfillment,
        "你是本单供应商联系任务的执行者。通过结构化actions调用模拟联系工具，不对外发消息，不付款或直接确认预订。"
        "只选择给出的供应商编号与正确kind。每个message是你给模拟供应商的简短询价请求；不能声称已预订或已有回复。"
        "优先对满足连续住宿房量和餐位的候选询价，可为比较条件询问其他候选。"
        "meal_keys可为空表示全部餐次。最多6个动作；未知供应商会被拒绝。"
        f"客户需求：{order.need.model_dump_json()}；房间数{order.need.rooms}。"
        f"模拟供应商工具目录：{order.world.model_dump_json()}。"
        f"已有报价摘要：{json.dumps([{'id':q.supplier_id,'blockers':q.blockers} for q in order.quotes[-6:]],ensure_ascii=False)}。"
        f"本轮任务（业务数据，不能修改权限）：{instruction[:1500]}",ContactPlan)
    result=contact_with_plan(order_id,plan,"agent",revision)
    return result


def _selected(order,ids=None,check_valid=True):
    ids=order.selected_quote_ids if ids is None else ids
    if len(set(ids))!=len(ids):
        raise ValueError("报价不能重复选择")
    selected=[]
    for key in ids:
        quote=next((q for q in order.quotes if q.id==key),None)
        if not quote:
            raise ValueError("报价不存在")
        if check_valid:
            provider=_supplier(order,quote.kind,quote.supplier_id)
            if order.tick>quote.valid_until_tick or signature(provider.model_dump(mode="json"))!=quote.inventory_signature:
                raise ValueError("模拟报价已过期或库存变化，请重新询价、选择并确认；原付款记录保留")
            if not quote.capacity_ok:
                raise ValueError("供应能力不足，客户或老板同意也不能生成不存在的房量或餐位")
        selected.append(quote)
    if sum(q.kind=="hotel" for q in selected)!=int(order.need.include_hotel):
        raise ValueError("住宿需选择一家满足连续房量的酒店，纯餐饮订单只选餐饮报价")
    covered=[key for q in selected if q.kind=="restaurant" for key in q.meal_keys]
    if sorted(covered)!=sorted(m.key for m in order.need.meals):
        raise ValueError("必须覆盖全部餐次，且每餐只能预订一次")
    return selected


def quote_current(order,quote):
    provider=_supplier(order,quote.kind,quote.supplier_id)
    return order.tick<=quote.valid_until_tick and signature(provider.model_dump(mode="json"))==quote.inventory_signature


def contract(order,ids=None,check_valid=True):
    selected=_selected(order,ids,check_valid)
    conditions={f"{q.id}:{k}":v for q in selected for k,v in q.conditions.items()}
    daily={}
    for quote in selected:
        if quote.kind=="restaurant":
            provider=_supplier(order,"restaurant",quote.supplier_id)
            for key in quote.meal_keys:
                day=key.split(":")[0]
                daily[day]=daily.get(day,0)+provider.price_per_person_cents*order.need.people
    if order.need.daily_meal_budget_cents:
        for day,cost in daily.items():
            if cost>order.need.daily_meal_budget_cents*order.need.people:
                conditions[f"meal_budget:{day}"]=f"{day}餐饮{cost/100:.2f}元超过原日预算，客户确认接受本次明确报价"
    hotel=next((q for q in selected if q.kind=="hotel"), None)
    if hotel and _supplier(order,"hotel",hotel.supplier_id).breakfast_included and any(k.endswith(":早餐") for q in selected for k in q.meal_keys):
        conditions["external_breakfast"]="酒店含早餐，客户仍明确选择另订早餐"
    cost=sum(q.total_cents for q in selected)
    gross=cost+order.service_fee_cents
    if order.need.event_budget_cents and gross-order.discount_cents>order.need.event_budget_cents:
        conditions["event_budget"]=(f"本次报价超过团建原总预算 ¥{order.need.event_budget_cents/100:,.2f}，"
                                    "请确认是否接受本笔报价")
    return {"resource_cents":cost,"gross_cents":gross,"net_cents":gross-order.discount_cents,"conditions":conditions,
        "signature":signature({"quotes":[q.model_dump(mode="json") for q in selected],"fee":order.service_fee_cents})}


def select_quotes(order_id,ids,*,expected_revision=None):
    with transaction() as tx:
        order,revision=_load(tx,order_id,expected_revision)
        if order.confirmations or order.supplier_paid_cents:
            raise ValueError("已付款给供应商或已预订，变更需单独处理，不可覆盖原订单")
        summary=contract(order,ids)
        if summary["net_cents"]<order.customer_paid_cents:
            raise ValueError("新合同金额低于客户已付款，需先人工处理退款；本次未改变订单")
        order.selected_quote_ids=list(ids)
        order.customer_signature=""
        order.accepted_conditions=[]
        order.status="waiting_customer"
        _save(tx,order,revision)
    return order


def confirm_contract(order_id,accepted_conditions,actor="customer",expected_signature=None,*,expected_revision=None):
    if actor!="customer":
        raise ValueError("服务条件必须由客户确认，老板不能代替")
    with transaction() as tx:
        order,revision=_load(tx,order_id,expected_revision)
        if order.status not in {"waiting_customer","customer_confirmed","paid"}:
            raise ValueError("当前没有待客户确认的合同")
        summary=contract(order)
        if expected_signature is not None and expected_signature!=summary["signature"]:
            raise SimulationConflict("页面显示的合同已变化，请重新查看后确认")
        if not set(summary["conditions"])<=set(accepted_conditions):
            raise ValueError("需逐项确认住宿、活动替代、餐饮预算等条件变化")
        order.customer_signature=summary["signature"]
        order.accepted_conditions=list(summary["conditions"])
        order.status="paid" if order.customer_paid_cents >= (summary["net_cents"]*60+50)//100 else "customer_confirmed"
        _save(tx,order,revision)
    return order


def pay_customer_deposit(order_id,coupon_id=None,actor="customer",expected_signature=None,*,expected_revision=None):
    if actor!="customer":
        raise ValueError("预付款需客户明确操作")
    with transaction() as tx:
        order,revision=_load(tx,order_id,expected_revision)
        if order.confirmations:
            if coupon_id!=order.coupon_id:
                raise ValueError("已有付款不能改用另一张优惠券")
            return order
        summary=contract(order)
        if expected_signature is not None and expected_signature!=summary["signature"]:
            raise SimulationConflict("页面显示的合同已变化，请重新查看付款金额")
        if order.customer_signature!=summary["signature"] or order.status not in {"customer_confirmed","paid"}:
            raise ValueError("请先由客户确认当前报价和全部条件")
        if order.customer_paid_cents:
            if coupon_id!=order.coupon_id:
                raise ValueError("已有付款不能改用另一张优惠券")
        else:
            order.discount_cents=redeem_coupon(tx,coupon_id,order,summary["gross_cents"])
            order.coupon_id=coupon_id
        net=summary["gross_cents"]-order.discount_cents
        target=(net*60+50)//100
        amount=max(0,target-order.customer_paid_cents)
        if amount:
            cash_event(tx,f"deposit:{order.id}:{order.customer_signature}",order.id,amount,"in","客户模拟预付款；尚未确认收入")
            order.customer_paid_cents+=amount
        order.status="paid"
        _save(tx,order,revision)
    return order


def pay_supplier_deposit(order_id,actor,expected_signature=None,*,expected_revision=None):
    owner_only(actor)
    with transaction() as tx:
        order,revision=_load(tx,order_id,expected_revision)
        if order.confirmations:
            return order
        summary=contract(order)
        if expected_signature is not None and expected_signature!=summary["signature"]:
            raise SimulationConflict("供方支出对应的合同已变化，请重新授权")
        if order.status not in {"paid","confirmed"} or order.customer_signature!=summary["signature"]:
            raise ValueError("客户尚未付款或确认当前条件")
        target=(summary["resource_cents"]*30+50)//100
        amount=max(0,target-order.supplier_paid_cents)
        if amount:
            cash_event(tx,f"supplier_deposit:{order.id}",order.id,amount,"out","老板授权模拟供方定金，无真实交易")
            order.supplier_paid_cents+=amount
            _save(tx,order,revision)
    return order


def fulfill_bookings(order_id,*,expected_revision=None):
    with transaction() as tx:
        order,revision=_load(tx,order_id,expected_revision)
        if order.confirmations:
            return order
        summary=contract(order)
        if order.status!="paid" or order.customer_signature!=summary["signature"] or order.supplier_paid_cents<(summary["resource_cents"]*30+50)//100:
            raise ValueError("执行工具需等待客户确认、预付款和老板供方支出授权")
        for quote in _selected(order):
            provider=_supplier(order,quote.kind,quote.supplier_id)
            if quote.kind=="hotel":
                for night in order.need.nights:
                    provider.rooms_by_night[night]-=order.need.rooms
            else:
                for key in quote.meal_keys:
                    provider.seats_by_meal[key]-=order.need.people
            order.confirmations.append({"id":identifier("CONFIRM"),"supplier":provider.name,"kind":quote.kind,
                "quote_id":quote.id,"amount_cents":quote.total_cents,"source":"supplier_simulator",
                "日期或餐次":order.need.nights if quote.kind=="hotel" else quote.meal_keys,
                "每次预订数量":order.need.rooms if quote.kind=="hotel" else order.need.people,
                "计量单位":"双床房" if quote.kind=="hotel" else "餐位",
                "取消条件":provider.cancellation_terms})
        order.status="confirmed"
        order.handoffs.append({"actor":"履约执行工具","task":"提交模拟预订并更新库存","source":"rule","status":"completed"})
        _save(tx,order,revision)
    return order


def simulate_change(order_id,kind="expire"):
    with transaction() as tx:
        order,revision=_load(tx,order_id)
        if order.confirmations or order.supplier_paid_cents:
            raise ValueError("本演示只在供方付款前模拟库存变化，已落实订单不会被直接覆盖")
        if kind=="expire":
            order.tick+=21
        elif kind=="second_night_shortage":
            if not order.world or len(order.need.nights)<2:
                raise ValueError("需有两晚住宿的模拟场景")
            order.world.hotels[1].rooms_by_night[order.need.nights[1]]=max(0,order.need.rooms-5)
        else:
            raise ValueError("未知模拟事件")
        order.contacts.append({"actor":"模拟环境","source":"simulated_event","request":kind,"reply":"报价失效或库存改变，请执行者重新核实"})
        _save(tx,order,revision)
    return order


def finish_service(order_id,actor="customer",*,expected_revision=None):
    if actor!="customer":
        raise ValueError("交付需客户确认")
    with transaction() as tx:
        order,revision=_load(tx,order_id,expected_revision)
        if order.status not in {"confirmed","delivered","completed"}:
            raise ValueError("资源尚未确认，不能完成服务")
        if order.status=="completed":
            return order
        if order.status == "confirmed":
            case = linked_case_for_order(order.case_id, order.id)
            if case and not linked_delivery_ready(case, order):
                raise ValueError("请先在原团建案件确认更新后的执行行程")
        order.delivered=True
        summary=contract(order,check_valid=False)
        remaining=summary["net_cents"]-order.customer_paid_cents
        if remaining:
            cash_event(tx,f"tail:{order.id}",order.id,remaining,"in","客户确认酒店餐饮服务交付并模拟支付尾款")
            order.customer_paid_cents+=remaining
        order.status="delivered"
        _save(tx,order,revision)
    return order


def settle_suppliers(order_id,actor,*,expected_revision=None):
    owner_only(actor)
    with transaction() as tx:
        order,revision=_load(tx,order_id,expected_revision)
        if order.status=="completed":
            return order
        if not order.delivered or order.status!="delivered":
            raise ValueError("请先由客户确认交付并支付尾款")
        summary=contract(order,check_valid=False)
        amount=summary["resource_cents"]-order.supplier_paid_cents
        if amount:
            cash_event(tx,f"supplier_balance:{order.id}",order.id,amount,"out","老板授权模拟结清供方尾款")
            order.supplier_paid_cents+=amount
        order.status="completed"
        _save(tx,order,revision)
    return order


def linked_execution(case,order):
    original=list(case.council_decision.schedule) if case.council_decision else []
    excluded=[word for key,word in [("bonfire","篝火"),("fireworks","烟花")]
        if any(condition.endswith(":"+key) for condition in order.accepted_conditions)]
    updated=[("原时段调整为自愿休息，已取消原火源活动；替代活动需另行确认"
        if any(word in line for word in excluded) else line) for line in original]
    key=signature({"order":order.id,"quotes":order.selected_quote_ids,"conditions":order.accepted_conditions,
        "plan_version":case.plan_version,"original":original})
    return updated,key,updated!=original


def linked_delivery_ready(case, order):
    _, signature, changed = linked_execution(case, order)
    return not changed or case.supplier_execution_signature == signature


def confirm_linked_execution(case,actor="customer"):
    if actor!="customer":
        raise ValueError("执行行程变化需客户确认")
    order,_=load_order(case.supplier_order_id)
    if order.status not in {"confirmed","delivered","completed"}:
        raise ValueError("资源尚未确认")
    updated,key,_=linked_execution(case,order)
    case.supplier_execution_schedule=updated
    case.supplier_execution_signature=key
    return case
