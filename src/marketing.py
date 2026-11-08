"""Owner-managed campaigns and attribution to existing team-building cases."""

from asyncio import CancelledError
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import json
from typing import Annotated, Literal
from urllib.parse import urlencode
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, UUID4

from src import storage
from src.agents import AgentConfigurationError, AgentOutputError, build_marketing_agent, run_structured
from src.company_store import SimulationConflict, transaction


CHANNELS = {"小红书": "xiaohongshu", "微信朋友圈": "wechat_moments", "企业社群": "enterprise_group"}
RECOMMEND_CHANNEL = "由 Agent 推荐"
STAGES = {"inquiries": "有效咨询", "plans": "确认方案", "bookings": "创建预订", "paid": "模拟已付"}
PLATFORM_METRICS = {"views": "累计浏览", "likes": "累计点赞", "comments": "累计评论"}
SHANGHAI = timezone(timedelta(hours=8))
TASK_TITLES = {"generate": "生成营销文案", "analyze": "生成营销复盘"}
TASK_LEASE = timedelta(minutes=5)

ShortText = Annotated[str, Field(min_length=1, max_length=150)]
MetricCount = Annotated[int, Field(strict=True, ge=0, le=2**53 - 1)]


class MarketingNotFound(LookupError):
    pass


class GenerateCampaign(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    request_id: UUID4
    goal: ShortText
    audience: ShortText
    channel: ShortText


class CampaignAction(BaseModel):
    action: Literal["approve", "reject", "publish"]
    publication_mode: Literal["manual", "simulation"] = "manual"


class CampaignMetrics(BaseModel):
    date: date
    views: MetricCount | None = None
    likes: MetricCount | None = None
    comments: MetricCount | None = None


class AnalyzeCampaign(BaseModel):
    request_id: UUID4 | None = None


class CampaignDraft(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    channel: ShortText
    title: ShortText
    reason: Annotated[str, Field(min_length=1, max_length=500)]
    content: Annotated[str, Field(min_length=1, max_length=3000)]


class ReviewDraft(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: ShortText
    body: Annotated[str, Field(min_length=1, max_length=1200)]
    limitations: Annotated[str, Field(min_length=1, max_length=500)]
    next_goal: ShortText
    next_channel: ShortText


def _campaign(tx, campaign_id, *, include_deleted=False):
    result = tx.find("campaign", campaign_id)
    if result is None or "review_status" not in result[0]:
        raise MarketingNotFound("未找到这项营销活动。")
    if result[0].get("deleted_at") and not include_deleted:
        raise MarketingNotFound("这项营销活动已删除。")
    return result


def _published(campaign):
    return campaign.get("review_status") == "published" and campaign["status"] == "active"


def _source(channel):
    return CHANNELS.get(channel, "custom")


def resolve_attribution(campaign_id, source):
    if not campaign_id or not source:
        return None
    with transaction() as tx:
        result = tx.find("campaign", campaign_id)
    if result:
        campaign, _ = result
        if _published(campaign) and _source(campaign["channel"]) == source.casefold():
            return campaign["id"]
    return None


def _funnels(cases, orders, campaign_ids):
    counts = {key: dict.fromkeys(STAGES, 0) for key in campaign_ids}
    bookings = set()
    paid = set()
    for order in orders:
        case_id = order.get("case_id")
        if case_id and order["status"] != "cancelled":
            bookings.add(case_id)
            if order.get("customer_paid_cents", 0) > 0:
                paid.add(case_id)

    for snapshot in cases:
        case = snapshot["case"]
        stages = counts.get(case.get("marketing_campaign_id"))
        if stages is None:
            continue
        stages["inquiries"] += 1
        stages["plans"] += any(event["action"] in {"plan_accepted", "plan_evaluated"}
                               for event in case.get("audit_log", []))
        stages["bookings"] += case["id"] in bookings
        stages["paid"] += case["id"] in paid
    return counts


def _view(campaign, stages, metrics):
    status = campaign["review_status"]
    link = urlencode({"utm_source": _source(campaign["channel"]), "utm_campaign": campaign["id"]})
    return {
        **{name: campaign[name] for name in ("id", "title", "goal", "audience", "channel", "reason", "content")},
        "status": status,
        "stages": stages,
        "metrics": metrics,
        "review": campaign.get("review"),
        "publishedAt": campaign.get("publishedAt"),
        "publicationMode": campaign.get("publicationMode"),
        "tracking_path": f"/?{link}" if status in {"approved", "published"} else None,
    }


def _campaign_views(campaign_id=None):
    with transaction() as tx:
        if campaign_id is None:
            campaigns = [item for item in tx.all("campaign") if "review_status" in item and not item.get("deleted_at")]
        else:
            campaigns = [_campaign(tx, campaign_id)[0]]
        campaign_ids = {item["id"] for item in campaigns if _published(item)}
        orders = tx.all("supplier_order") if campaign_ids else []
        snapshots = tx.all("campaign_metric") if campaign_ids else []
    cases = storage.saved_case_snapshots() if campaign_ids else []
    funnels = _funnels(cases, orders, campaign_ids)
    metrics = defaultdict(list)
    for entry in snapshots:
        if entry["campaign_id"] in campaign_ids:
            metrics[entry["campaign_id"]].append({key: value for key, value in entry.items() if key != "campaign_id"})
    return [_view(item, funnels.get(item["id"], dict.fromkeys(STAGES, 0)), metrics[item["id"]]) for item in campaigns]


def summary():
    return {"campaigns": _campaign_views()}


def _set_task_result(tx, task, status, detail="", **campaign):
    _, revision = tx.get("marketing_task", task["id"])
    task.update(status=status, detail=detail, **campaign)
    tx.save("marketing_task", task["id"], task, revision)


def delete_campaign(campaign_id):
    with transaction() as tx:
        campaign, revision = _campaign(tx, campaign_id, include_deleted=True)
        if not campaign.get("deleted_at"):
            campaign.update(status="archived", deleted_at=datetime.now(timezone.utc).isoformat())
            tx.save("campaign", campaign_id, campaign, revision)
            for task in tx.all("marketing_task"):
                if task["campaign_id"] == campaign_id and task["status"] == "running":
                    _set_task_result(tx, task, "failed", "营销活动已删除，复盘任务已结束。")
    return {"id": campaign_id}


def _expire_task(tx, task, now):
    if task["status"] == "running" and datetime.fromisoformat(task["created_at"]) + TASK_LEASE <= now:
        _set_task_result(tx, task, "failed", "任务超时或中断，请重新执行。")


def _begin_task(tx, operation, request_key, campaign_title, *, campaign_id=None, request_fields=None):
    now = datetime.now(timezone.utc)
    previous = next((row for row in tx.all("marketing_task") if row["request_key"] == request_key), None)
    if previous:
        if previous.get("request_fields") != request_fields:
            raise SimulationConflict("这个请求已用于其他营销内容。请重新提交。")
        _expire_task(tx, previous, now)
        if previous["status"] == "completed":
            return None
        if previous["status"] == "running":
            raise SimulationConflict("这项营销任务正在执行，请查看团队动态。")

    task_id = f"marketing-{uuid4().hex}"
    tx.save("marketing_task", task_id, {
        "id": task_id, "kind": "marketing", "actor": "营销 Agent", "title": TASK_TITLES[operation],
        "status": "running", "created_at": now.isoformat(), "detail": "", "campaign_id": campaign_id,
        "campaign_title": campaign_title, "request_key": request_key, "request_fields": request_fields,
    })
    return task_id


def _complete_task(tx, task_id, campaign_id, campaign_title):
    task, _ = tx.get("marketing_task", task_id)
    if task["status"] != "running":
        raise SimulationConflict("这次营销任务已结束，请查看最新结果。")
    _set_task_result(tx, task, "completed", campaign_id=campaign_id, campaign_title=campaign_title)


def _task_error(error):
    if isinstance(error, AgentConfigurationError):
        return "模型配置不可用，请检查配置后重试。"
    if isinstance(error, TimeoutError) or isinstance(error.__cause__, TimeoutError):
        return "模型调用超时，请重试。"
    if isinstance(error, AgentOutputError):
        return "模型调用失败或返回的结果不符合要求，请重试。"
    if isinstance(error, CancelledError):
        return "任务中断，请重试。"
    return "处理营销任务时遇到问题，请重试。"


@contextmanager
def _record_task_failure(task_id):
    try:
        yield
    except (Exception, CancelledError) as error:
        with transaction() as tx:
            task, _ = tx.get("marketing_task", task_id)
            if task["status"] == "running":
                _set_task_result(tx, task, "failed", _task_error(error))
        raise


def list_tasks():
    now = datetime.now(timezone.utc)
    with transaction() as tx:
        tasks = tx.all("marketing_task")
        for task in tasks:
            _expire_task(tx, task, now)
    public = ("id", "kind", "actor", "title", "status", "created_at", "detail", "campaign_id", "campaign_title")
    return [{key: task[key] for key in public} for task in tasks]


def _check_request(existing, payload):
    if (existing["goal"], existing["audience"], existing["requested_channel"]) != (payload.goal, payload.audience, payload.channel):
        raise SimulationConflict("这个生成请求已用于另一份营销目标或渠道。请重新提交。")


async def generate(payload):
    campaign_id = f"SIM-CAMPAIGN-{payload.request_id.hex}"
    with transaction() as tx:
        existing = tx.find("campaign", campaign_id)
        if existing:
            _check_request(existing[0], payload)
        task_id = None if existing else _begin_task(tx, "generate", f"generate:{payload.request_id.hex}", payload.goal,
                                                    request_fields=[payload.goal, payload.audience, payload.channel])
    if task_id is None:
        return _campaign_views(campaign_id)[0]

    with _record_task_failure(task_id):
        choices = list(CHANNELS) if payload.channel == RECOMMEND_CHANNEL else [payload.channel]
        task = {"goal": payload.goal, "audience": payload.audience, "allowed_channels": choices}
        draft = await run_structured(build_marketing_agent(),
            "根据目标和客群选择一个候选渠道，说明理由并生成一篇待审推广文案。"
            "引导读者提交团建需求。推广链接由系统生成，正文不要加入链接或未经核实的承诺。\n"
            + json.dumps(task, ensure_ascii=False), CampaignDraft)
        if draft.channel not in choices:
            raise AgentOutputError("模型返回了候选范围外的渠道，请重新生成。")

        with transaction() as tx:
            _complete_task(tx, task_id, campaign_id, draft.title)
            tx.save("campaign", campaign_id, {
                "id": campaign_id, **draft.model_dump(), "goal": payload.goal, "audience": payload.audience,
                "requested_channel": payload.channel, "review_status": "pending", "status": "draft",
                "budget_cents": 0, "research_id": None, "simulated": True,
            })
    return _campaign_views(campaign_id)[0]


def act(campaign_id, payload):
    before, after = {
        "approve": ("pending", "approved"),
        "reject": ("pending", "rejected"),
        "publish": ("approved", "published"),
    }[payload.action]
    with transaction() as tx:
        campaign, revision = _campaign(tx, campaign_id)
        status = campaign["review_status"]
        if status not in {before, after}:
            raise SimulationConflict("当前审批状态不能执行该操作，请刷新后重试。")
        if status == before:
            campaign["review_status"] = after
            if after == "published":
                campaign.update(status="active", publishedAt=datetime.now(timezone.utc).isoformat(),
                                publicationMode=payload.publication_mode)
            tx.save("campaign", campaign_id, campaign, revision)
        elif after == "published" and campaign["publicationMode"] != payload.publication_mode:
            raise SimulationConflict("该活动已使用另一种发布方式确认。")
    return _campaign_views(campaign_id)[0]


def save_metrics(campaign_id, payload):
    key = f"{campaign_id}:{payload.date.isoformat()}"
    with transaction() as tx:
        campaign, _ = _campaign(tx, campaign_id)
        if not _published(campaign):
            raise SimulationConflict("活动发布后可录入平台数据。")
        existing = tx.find("campaign_metric", key)
        tx.save("campaign_metric", key, {
            "campaign_id": campaign_id, **payload.model_dump(mode="json"),
            "recordedAt": int(datetime.now(timezone.utc).timestamp() * 1000),
        }, existing[1] if existing else None)
    return _campaign_views(campaign_id)[0]


async def analyze(campaign_id, payload=None):
    captured_at = datetime.now(timezone.utc)
    campaign = _campaign_views(campaign_id)[0]
    if campaign["status"] != "published":
        raise SimulationConflict("活动发布后可生成复盘。")
    request_id = payload.request_id if payload is not None and payload.request_id else uuid4()
    with transaction() as tx:
        _campaign(tx, campaign_id)
        task_id = _begin_task(tx, "analyze", f"analyze:{campaign_id}:{request_id.hex}", campaign["title"],
                              campaign_id=campaign_id)
    if task_id is None:
        return _campaign_views(campaign_id)[0]

    with _record_task_failure(task_id):
        review = await _build_review(campaign, captured_at)
        with transaction() as tx:
            current, revision = _campaign(tx, campaign_id)
            _complete_task(tx, task_id, campaign_id, campaign["title"])
            previous = current.get("review")
            if previous is None or previous["snapshot"]["asOf"] <= review["snapshot"]["asOf"]:
                current["review"] = review
                tx.save("campaign", campaign_id, current, revision)
    return _campaign_views(campaign_id)[0]


async def _build_review(campaign, captured_at):
    stages = campaign["stages"]
    latest = max(campaign["metrics"], key=lambda metric: metric["date"], default=None)
    rate = stages["paid"] / stages["inquiries"] if stages["inquiries"] else None
    snapshot = {
        "asOf": captured_at.isoformat(), "stages": stages, "conversionRate": rate, "platform": latest,
    }
    task = {"campaign": {key: campaign[key] for key in ("channel", "title", "goal")}, "snapshot": snapshot}
    draft = await run_structured(build_marketing_agent(),
        "复盘一项获客活动。站内数据按案件去重，付费为模拟付款。站外累计数据为老板手录，"
        "null表示未录入或无法计算，不是零。根据汇总数据提出一项下轮测试，沿用原渠道，写清比较什么指标。"
        "没有有效咨询时建议先收集数据。区分观察和推断，说明样本量和可比性。数字依据由系统单独展示。\n"
        + json.dumps(task, ensure_ascii=False), ReviewDraft)
    if draft.next_channel != campaign["channel"]:
        raise AgentOutputError("模型返回的复盘渠道与原活动不一致，请重新生成。")

    evidence = " · ".join(f"{label} {stages[key]}" for key, label in STAGES.items())
    if latest:
        platform = " · ".join(f"{label} {latest[key] if latest[key] is not None else '未录入'}"
                              for key, label in PLATFORM_METRICS.items())
        evidence += f"。站外数据统计至 {latest['date']}：{platform}。"
    else:
        evidence += "。站外数据未录入。"
    return {
        **draft.model_dump(), "evidence": evidence, "snapshot": snapshot,
        "createdAt": datetime.now(SHANGHAI).date().isoformat(),
    }
