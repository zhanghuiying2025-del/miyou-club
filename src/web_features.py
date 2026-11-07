"""React endpoints for existing archives, feedback lessons and company operations."""
import asyncio
from typing import Literal

from fastapi import Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src import company_store, history_archive, history_search, operations, performance, storage
from src.agents import AgentConfigurationError, AgentOutputError


class Switch(BaseModel):
    active: bool


class Operation(BaseModel):
    action: Literal["fixed_research", "model_research", "create_campaign", "activate_campaign",
                    "campaign_spend", "create_policy", "toggle_policy", "issue_coupon"]
    id: str = Field(default="", max_length=100)
    title: str = Field(default="", max_length=150)
    audience: str = Field(default="全部", max_length=300)
    channel: str = Field(default="", max_length=100)
    content: str = Field(default="", max_length=3000)
    amount: int = Field(default=0, ge=0, le=100000000)
    minimum: int = Field(default=0, ge=0, le=100000000)
    start: str = Field(default="", max_length=10)
    end: str = Field(default="", max_length=10)
    limit: int = Field(default=20, ge=1, le=10000)
    campaign_id: str | None = Field(default=None, max_length=100)
    research_id: str | None = Field(default=None, max_length=100)
    active: bool = False
    reference: str = Field(default="", max_length=100)


def marketing_view(customer_id):
    campaigns = company_store.list_records("campaign")
    policies = company_store.list_records("coupon_policy")
    return {
        "campaigns": [{"id": item["id"], "title": item["title"], "content": item["content"]}
                      for item in campaigns if item["status"] == "active"],
        "policies": [item for item in policies if item["active"]],
        "coupons": [item for item in company_store.list_records("coupon") if item["customer_id"] == customer_id],
    }


def register_owner_features(app, require_owner):
    protected = [Depends(require_owner)]

    @app.get("/api/owner/archive", dependencies=protected)
    def archive():
        docs = history_archive.documents()
        hidden = history_archive.hidden_documents(docs, storage.hidden_archive_ids())
        return [{**{k: v for k, v in doc.items() if k not in {"snapshot", "related_orders"}},
                 "outcome": history_archive.case_outcome(doc), "hidden": doc["id"] in hidden}
                for doc in docs]

    @app.get("/api/owner/archive/search", dependencies=protected)
    def search(q: str, city: str = "", kind: str = ""):
        docs = history_archive.documents()
        hidden = history_archive.hidden_documents(docs, storage.hidden_archive_ids())
        return history_search.search(q, city=city, kind=kind, exclude_document_ids=hidden)

    @app.post("/api/owner/archive/index", dependencies=protected)
    def sync_index():
        docs, fragments = history_search.sync_index()
        return {"documents": len(docs), "fragments": len(fragments)}

    @app.post("/api/owner/archive/fragments/{fragment_id}", dependencies=protected)
    def switch_fragment(fragment_id: str, payload: Switch):
        history_search.set_active(fragment_id, payload.active)
        return {"ok": True}

    @app.get("/api/owner/archive/{document_id}", dependencies=protected)
    def archive_detail(document_id: str, download: bool = False):
        doc = history_archive.get_document(document_id)
        if doc is None:
            raise HTTPException(404, "找不到这份档案。")
        doc["snapshot"].pop("web_customer_id", None)
        if download:
            return JSONResponse(doc, headers={"Content-Disposition": 'attachment; filename="order-archive.json"'})
        _, fragments = history_search.sync_index()
        return {**doc, "fragments": [item for item in fragments if item["document_id"] == document_id]}

    @app.post("/api/owner/archive/{document_id}", dependencies=protected)
    def switch_archive(document_id: str, payload: Switch):
        if not history_archive.get_document(document_id):
            raise HTTPException(404, "找不到这份档案。")
        storage.set_archive_hidden(document_id, not payload.active)
        return {"ok": True}

    @app.get("/api/owner/lessons", dependencies=protected)
    def lessons():
        return [item.model_dump(mode="json") for item in performance.company_lessons()]

    @app.post("/api/owner/lessons/{lesson_id}", dependencies=protected)
    def switch_lesson(lesson_id: str, payload: Switch):
        performance.set_lesson_active(lesson_id, payload.active)
        return {"ok": True}

    @app.get("/api/owner/operations", dependencies=protected)
    def operations_view():
        return {
            **{kind: company_store.list_records(kind) for kind in
               ("research", "campaign", "lead", "coupon_policy", "coupon")},
            "summary": operations.operating_summary(),
        }

    @app.post("/api/owner/operations", dependencies=protected)
    def operation(payload: Operation):
        action = payload.action
        if action == "fixed_research":
            operations.fixed_research(payload.title or "团建市场")
        elif action == "model_research":
            try:
                asyncio.run(operations.model_research(payload.title or "团建市场"))
            except (AgentConfigurationError, AgentOutputError) as exc:
                raise HTTPException(502, "AI 调研未完成。请检查模型服务后重试，或先生成固定演示报告。") from exc
        elif action == "create_campaign":
            operations.create_campaign(payload.title, payload.audience, payload.channel,
                                       payload.amount, payload.content, payload.research_id)
        elif action == "activate_campaign":
            operations.activate_campaign(payload.id, "owner")
        elif action == "campaign_spend":
            operations.campaign_spend(payload.id, payload.amount, payload.reference, "owner")
        elif action == "create_policy":
            operations.create_coupon_policy(payload.title, payload.amount, payload.minimum, payload.start,
                                            payload.end, payload.audience, payload.limit, payload.campaign_id)
        elif action == "toggle_policy":
            operations.set_coupon_active(payload.id, payload.active, "owner")
        else:
            operations.issue_coupon(payload.id, payload.reference)
        return {"ok": True}
