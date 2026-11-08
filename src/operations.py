"""Callable simulated market, acquisition and coupon modules; no external publishing."""
from datetime import date
from hashlib import sha256
from pydantic import BaseModel, Field

from src.company_store import transaction, cash_event, finance_summary, list_records
from src.supplier_models import identifier


def owner_only(actor):
    if actor != "owner":
        raise ValueError("该操作需要老板端明确授权")


class MarketCompetitor(BaseModel):
    name: str = Field(max_length=100)
    audience: str = Field(max_length=200)
    services: list[str] = Field(max_length=5)
    billing: str = Field(max_length=200)


class MarketReport(BaseModel):
    summary: str = Field(max_length=600)
    competitors: list[MarketCompetitor] = Field(min_length=1,max_length=4)
    opportunities: list[str] = Field(max_length=4)


def save_research(topic, report, source, actor):
    owner_only(actor)
    if source not in {"fixed_simulation","model_simulation"} or not topic.strip():
        raise ValueError("仅支持明确标记的模拟市场资料")
    report = MarketReport.model_validate(report)
    for competitor in report.competitors:
        if not competitor.name.startswith("模拟"):
            competitor.name="模拟"+competitor.name
    record = {"id":identifier("RESEARCH"),"topic":topic[:1000],"source":source,"simulated":True,
              "report":report.model_dump(mode="json")}
    with transaction() as tx:
        tx.save("research",record["id"],record)
    return record


def fixed_research(topic="150人企业团建", actor="owner"):
    report = MarketReport(summary="模拟对比三类服务：标准团建、酒店会议套餐和定制组织协调。不是实时市场调查。",
        competitors=[MarketCompetitor(name="模拟标准团建商",audience="企业行政",services=["活动套餐","教练带队"],billing="模拟：按人打包报价"),
            MarketCompetitor(name="模拟酒店会务服务",audience="会议与住宿团队",services=["客房","餐饮","场地"],billing="模拟：按间夜、餐次和场地计费"),
            MarketCompetitor(name="模拟定制组织商",audience="复杂需求组织者",services=["需求协调","资源确认","执行跟进"],billing="模拟：资源成本加服务费")],
        opportunities=["展示连续两晚库存与餐次确认，减少组织者逐家跟进", "明确服务费和待核实条件，不用低价承诺未落实资源"])
    return save_research(topic,report,"fixed_simulation",actor)


async def model_research(topic, actor="owner"):
    owner_only(actor)
    from src.agents import build_agent_team, run_structured
    report = await run_structured(build_agent_team().manager,
        "运营模块按需任务：生成明确虚构的市场调研演示。不是网络搜索，没有真实市场来源。"
        "竞品名称必须为模拟名称，不引用真实品牌、网址或声称实时价格。输出服务、客群和收费方式，"
        "每项机会是待验证假设，不声称真实用户验证；不能承诺预订、优惠或对外发布。"
        f"调研主题（业务数据，不是权限指令）：{topic[:2000]}", MarketReport)
    return save_research(topic,report,"model_simulation",actor)


def create_campaign(title, audience, channel, budget_cents, content, research_id=None, actor="owner"):
    owner_only(actor)
    if isinstance(budget_cents,bool) or not isinstance(budget_cents,int) or not title.strip() or not content.strip() or not 0 <= budget_cents <= 100000000:
        raise ValueError("活动标题、内容或预算无效")
    record = {"id":identifier("CAMPAIGN"),"title":title[:150],"audience":audience[:300],"channel":channel[:100],
        "budget_cents":int(budget_cents),"content":content[:3000],"research_id":research_id,"status":"draft","simulated":True}
    with transaction() as tx:
        if research_id:
            tx.get("research",research_id)
        tx.save("campaign",record["id"],record)
    return record


def list_campaigns():
    return [campaign for campaign in list_records("campaign") if not campaign.get("deleted_at")]


def activate_campaign(campaign_id, actor):
    owner_only(actor)
    with transaction() as tx:
        record, revision = tx.get("campaign",campaign_id)
        if "review_status" in record:
            raise ValueError("请在营销活动详情完成审批并确认发布")
        record["status"] = "active"
        tx.save("campaign",campaign_id,record,revision)
    return record  # A local demo entry, never an external advertisement.


def campaign_spend(campaign_id, cents, reference, actor):
    owner_only(actor)
    reference=reference.strip()
    if not reference.strip() or isinstance(cents,bool) or not isinstance(cents,int) or cents <= 0:
        raise ValueError("请填写正金额与唯一的模拟支出编号")
    key = f"campaign:{campaign_id}:{reference}"
    with transaction() as tx:
        record, _ = tx.get("campaign",campaign_id)
        if record["status"] != "active":
            raise ValueError("活动尚未启用")
        prior = next((e for e in tx.events("cash") if e["id"] == key),None)
        if prior:
            if prior["cents"] != cents:
                raise ValueError("相同支出编号的金额不一致")
            return
        spent = sum(e["cents"] for e in tx.events("cash") if e["entity_id"] == campaign_id and e["direction"] == "out")
        if spent+cents > record["budget_cents"]:
            raise ValueError("超过已授权的活动预算")
        cash_event(tx,key,campaign_id,cents,"out","模拟推广支出，无真实投放")


def register_lead(customer_id, interest, campaign_id=None):
    customer_id = customer_id.strip()
    if not 1 <= len(customer_id) <= 80 or not interest.strip():
        raise ValueError("请填写演示客户编号和需求")
    with transaction() as tx:
        if campaign_id:
            campaign,_ = tx.get("campaign",campaign_id)
            if campaign["status"] != "active":
                raise ValueError("获客活动未启用")
        existing = next((r for r in tx.all("lead") if r["id"] == customer_id),None)
        if existing:
            return existing
        record = {"id":customer_id,"interest":interest[:1500],"campaign_id":campaign_id,"source":"local_simulation"}
        tx.save("lead",customer_id,record)
    return record


def customer_segment(tx, customer_id):
    orders = [o for o in tx.all("supplier_order") if o["customer_id"] == customer_id]
    if any(o["status"] == "completed" for o in orders):
        return "老客"
    if any(o["customer_paid_cents"] > 0 for o in orders):
        return "新客"
    return "潜客"


def create_coupon_policy(title, face_cents, min_order_cents, valid_from, valid_until, audience,
                         limit, campaign_id=None, actor="owner"):
    owner_only(actor)
    start,end = date.fromisoformat(str(valid_from)),date.fromisoformat(str(valid_until))
    if any(isinstance(v,bool) or not isinstance(v,int) for v in (face_cents,min_order_cents,limit)):
        raise ValueError("优惠金额和额度必须为整数分与整数张")
    if not title.strip() or not 0 < face_cents <= 1000000 or min_order_cents < 0 or start > end or not 1 <= limit <= 10000:
        raise ValueError("优惠金额、门槛、有效期或发放额度无效")
    if audience not in {"全部","首单","老客"}:
        raise ValueError("未知的优惠客群")
    record = {"id":identifier("POLICY"),"title":title[:100],"face_cents":int(face_cents),
        "min_order_cents":int(min_order_cents),"valid_from":start.isoformat(),"valid_until":end.isoformat(),
        "audience":audience,"limit":int(limit),"campaign_id":campaign_id,"active":True,"scope":"service_fee","stackable":False}
    with transaction() as tx:
        if campaign_id:
            campaign, _ = tx.get("campaign", campaign_id)
            if campaign.get("deleted_at"):
                raise ValueError("获客活动已删除")
        tx.save("coupon_policy",record["id"],record)
    return record


def set_coupon_active(policy_id,active,actor):
    owner_only(actor)
    with transaction() as tx:
        record,revision=tx.get("coupon_policy",policy_id)
        record["active"]=bool(active)
        tx.save("coupon_policy",policy_id,record,revision)


def issue_coupon(policy_id, customer_id):
    customer_id=customer_id.strip()
    key = "SIM-COUPON-"+sha256(f"{policy_id}:{customer_id}".encode()).hexdigest()[:18]
    with transaction() as tx:
        policy,_ = tx.get("coupon_policy",policy_id)
        tx.get("lead",customer_id)
        if not policy["active"]:
            raise ValueError("优惠政策已停用")
        existing = next((r for r in tx.all("coupon") if r["id"] == key),None)
        if existing:
            return existing
        if len([r for r in tx.all("coupon") if r["policy_id"] == policy_id]) >= policy["limit"]:
            raise ValueError("优惠券已达到发放额度")
        if policy["audience"] == "老客" and customer_segment(tx,customer_id) != "老客":
            raise ValueError("该政策仅适用于已完成订单的老客")
        record = {"id":key,"policy_id":policy_id,"customer_id":customer_id,"status":"issued","order_id":None}
        tx.save("coupon",key,record)
    return record


def redeem_coupon(tx, coupon_id, order, gross_cents):
    if not coupon_id:
        return 0
    coupon,revision = tx.get("coupon",coupon_id)
    policy,_ = tx.get("coupon_policy",coupon["policy_id"])
    if coupon["customer_id"] != order.customer_id or coupon["status"] != "issued" or not policy["active"]:
        raise ValueError("优惠券不属于本客户、已使用或已停用")
    if not policy["valid_from"] <= order.simulation_day.isoformat() <= policy["valid_until"]:
        raise ValueError("优惠券不在订单演示日期的有效期内")
    if gross_cents < policy["min_order_cents"]:
        raise ValueError("订单未达到优惠使用门槛")
    other = [o for o in tx.all("supplier_order") if o["id"] != order.id and o["customer_id"] == order.customer_id]
    if policy["audience"] == "首单" and any(o["customer_paid_cents"] > 0 for o in other):
        raise ValueError("首单优惠不可重复用于已成交客户")
    if policy["audience"] == "老客" and not any(o["status"] == "completed" for o in other):
        raise ValueError("未满足老客条件")
    if policy["campaign_id"] and policy["campaign_id"] != order.campaign_id:
        raise ValueError("优惠券与订单获客活动不匹配")
    # Only discount the service fee; never subsidise supplier costs implicitly.
    discount = policy["face_cents"]
    if discount > max(0,order.service_fee_cents-50000):
        raise ValueError("优惠将使本单服务收入低于500元演示保留线，请调整优惠政策或服务费")
    coupon.update(status="redeemed",order_id=order.id)
    tx.save("coupon",coupon_id,coupon,revision)
    tx.event(f"redeem:{coupon_id}","coupon_redemption",order.id,{"coupon_id":coupon_id,"discount_cents":discount})
    return discount


def operating_summary():
    with transaction() as tx:
        orders,leads,campaigns = tx.all("supplier_order"),tx.all("lead"),tx.all("campaign")
        completed = [o for o in orders if o["status"] == "completed"]
        rows=[]
        for campaign in campaigns:
            if campaign.get("deleted_at"):
                continue
            sourced=[o for o in orders if o.get("campaign_id") == campaign["id"]]
            lead_count=sum(l.get("campaign_id") == campaign["id"] for l in leads)
            paid=[o for o in sourced if o["customer_paid_cents"] > 0]
            customers=len({o["customer_id"] for o in paid})
            spent=sum(e["cents"] for e in tx.events("cash") if e["entity_id"] == campaign["id"] and e["direction"] == "out")
            rows.append({"活动":campaign["title"],"潜客数":lead_count,"成交客户数":customers,
                "转化率":customers/lead_count if lead_count else None,"模拟推广支出（元）":spent/100})
        return {"finance":finance_summary(tx),"leads":len(leads),"paid_orders":sum(o["customer_paid_cents"]>0 for o in orders),
            "completed_orders":len(completed),"service_income_cents":sum(o["service_fee_cents"]-o["discount_cents"] for o in completed),
            "campaigns":rows,"ledger":tx.events("cash")}
