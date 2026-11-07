"""Single-process local entry point for the customer and owner React apps."""
from datetime import date
from hashlib import sha256
import os
from pathlib import Path
import re
from secrets import compare_digest, token_urlsafe
from threading import RLock
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, UUID4, ValidationError
from pypdf.errors import PyPdfError

from src import document_intake, operations, storage, supplier_simulation as suppliers
from src.activity_review import read_activity_documents, report_markdown
from src import web_flow as flow
from src.company_store import SimulationConflict
from src.models import CaseStatus, TravelCase
from src.multimodal import MAX_DOCX_BYTES, validate_attachment
from src.owner_dashboard import load_owner_dashboard
from src.policy import CompanyPolicy
from src.workflow import WorkflowError
from src.web_activity import document_view
from src.web_features import marketing_view, register_owner_features

CUSTOMER_COOKIE = "miyou_customer"
OWNER_COOKIE = "miyou_owner"
BROWSER_ID = re.compile(r"[A-Za-z0-9_-]{43}")
API_LOCK = RLock()
FRONTEND = Path(__file__).resolve().parents[1] / "frontend"


class OwnerLogin(BaseModel):
    password: str = Field(max_length=512)


class MarketingAction(BaseModel):
    case_id: str | None = Field(default=None, max_length=100)
    policy_id: str | None = Field(default=None, max_length=100)
    campaign_id: str | None = Field(default=None, max_length=100)
    interest: str = Field(default="", max_length=1500)


def attachments_from(files):
    if not 1 <= len(files) <= 3:
        raise WorkflowError("请上传 1 到 3 份资料。")
    attachments = []
    for file in files:
        name = (file.filename or "附件").replace("\\", "/").split("/")[-1][:150]
        content = file.file.read(MAX_DOCX_BYTES + 1)
        media_type = file.content_type or ""
        validate_attachment(name, media_type, content)
        attachments.append((name, media_type, content))
    return attachments


def create_app(frontend=FRONTEND):
    app = FastAPI(title="咪游社 · 本地团建 API")
    owner_session = token_urlsafe(32)

    @app.middleware("http")
    async def api_headers(request, call_next):
        if request.url.path.startswith("/api/"):
            if request.method == "POST" and (origin := request.headers.get("origin")):
                expected = (request.url.scheme, request.headers.get("host"))
                supplied = urlsplit(origin)
                if (supplied.scheme, supplied.netloc) != expected:
                    return JSONResponse({"detail": "请从咪游社页面发起操作。"}, status_code=403)
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            return response
        return await call_next(request)

    @app.exception_handler(WorkflowError)
    @app.exception_handler(ValueError)
    async def business_error(_request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(storage.CaseConflictError)
    @app.exception_handler(SimulationConflict)
    async def stale(_request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    def current_customer(request: Request):
        value = request.cookies.get(CUSTOMER_COOKIE, "")
        if not BROWSER_ID.fullmatch(value):
            raise HTTPException(401, "请先打开用户端页面，建立本地会话。")
        return sha256(value.encode()).hexdigest()

    def require_owner(request: Request):
        value = request.cookies.get(OWNER_COOKIE, "")
        if not compare_digest(value.encode(), owner_session.encode()):
            raise HTTPException(401, "请先登录老板工作台。")

    def owned_case(case_id, customer):
        case = storage.load_case_from_disk(case_id)
        if case is None or case.web_customer_id != customer:
            raise HTTPException(404, "找不到该活动。")
        return case

    def check_case_revision(case, revision):
        if case.storage_revision != revision:
            raise storage.CaseConflictError("活动已经更新，请查看最新状态后再操作。")

    @app.get("/api/customer/session")
    def customer_session(request: Request, response: Response):
        if not BROWSER_ID.fullmatch(request.cookies.get(CUSTOMER_COOKIE, "")):
            response.set_cookie(CUSTOMER_COOKIE, token_urlsafe(32), httponly=True, samesite="strict",
                                max_age=30 * 24 * 60 * 60)
        return {"role": "customer"}

    @app.get("/api/customer/activities")
    def activities(customer=Depends(current_customer)):
        result = []
        for row in sorted(storage.saved_case_snapshots(), key=lambda item: item["updated_at"], reverse=True):
            case = row["case"]
            if case.get("web_customer_id") != customer:
                continue
            order_id = case.get("supplier_order_id")
            booking = suppliers.load_order(order_id)[0] if order_id else None
            status = case["status"]
            if status == CaseStatus.WAITING_OWNER:
                status = flow.customer_status(TravelCase.model_validate(case))
            result.append({
                "id": case["id"], "title": (case.get("requirements") or {}).get("event_name", "团建活动"),
                "status": status, "updated_at": row["updated_at"],
                "booking_status": booking.status if booking else None,
                "service_mode": case.get("service_mode", "travel_planning"),
            })
        return result

    def save_activity(payload, customer, attachments=()):
        case_id = "case_" + sha256(f"{customer}:{payload.request_id}".encode()).hexdigest()[:24]
        with API_LOCK:
            if case := storage.load_case_from_disk(case_id):
                return flow.activity_view(case)
            if payload.brief.date < date.today():
                raise WorkflowError("请选择今天或之后的活动日期。")
            req = payload.brief.requirements()
            case = flow.TravelAgencyWorkflow(None).create_case(req.model_dump_json())
            case.id, case.web_customer_id = case_id, customer
            case.status, case.requirements = CaseStatus.WAITING_REQUIREMENTS, req
            storage.save_case_to_disk(case)
            flow.start_planning(case, attachments)
            return flow.activity_view(storage.load_case_from_disk(case.id))

    @app.post("/api/customer/activities", status_code=202)
    def create_activity(payload: flow.CreateActivity, customer=Depends(current_customer)):
        return save_activity(payload, customer)

    @app.post("/api/customer/activities/with-files", status_code=202)
    def create_activity_with_files(payload: str = Form(), files: list[UploadFile] = File(),
                                   customer=Depends(current_customer)):
        try:
            request = flow.CreateActivity.model_validate_json(payload)
        except ValidationError as exc:
            raise WorkflowError("请检查活动表单后再提交。") from exc
        return save_activity(request, customer, attachments_from(files))

    @app.post("/api/customer/documents", status_code=202)
    def create_document(request_id: UUID4 = Form(), files: list[UploadFile] = File(),
                        customer=Depends(current_customer)):
        case_id = "case_" + sha256(f"{customer}:document:{request_id}".encode()).hexdigest()[:24]
        try:
            sources = read_activity_documents(attachments_from(files))
        except PyPdfError as exc:
            raise WorkflowError("PDF 无法读取，请检查文件是否损坏或加密。") from exc
        with API_LOCK:
            if case := storage.load_case_from_disk(case_id):
                return flow.activity_view(case)
            case = flow.TravelAgencyWorkflow(None).create_case("上传活动资料")
            case.id, case.web_customer_id = case_id, customer
            case.service_mode, case.status = "document_intake", CaseStatus.INTAKE
            case.activity_sources = sources
            storage.save_case_to_disk(case)
            flow.enqueue(case, "intake_document", document_intake.intake_documents)
            return flow.activity_view(storage.load_case_from_disk(case.id))

    def marketing_customer(case_id, customer):
        if case_id and (booking := flow.booking_for(owned_case(case_id, customer))):
            return booking[0].customer_id
        return customer

    @app.get("/api/customer/marketing")
    def marketing(case_id: str | None = None, customer=Depends(current_customer)):
        return marketing_view(marketing_customer(case_id, customer))

    @app.post("/api/customer/marketing")
    def marketing_action(payload: MarketingAction, customer=Depends(current_customer)):
        key = marketing_customer(payload.case_id, customer)
        if payload.policy_id:
            operations.issue_coupon(payload.policy_id, key)
        else:
            operations.register_lead(key, payload.interest, payload.campaign_id)
        return marketing_view(key)

    @app.get("/api/customer/activities/{case_id}")
    def activity(case_id: str, customer=Depends(current_customer)):
        return flow.activity_view(owned_case(case_id, customer))

    @app.get("/api/customer/activities/{case_id}/report")
    def activity_report(case_id: str, customer=Depends(current_customer)):
        report = owned_case(case_id, customer).activity_review
        if not report:
            raise HTTPException(404, "还没有可以下载的审核清单。")
        return Response(report_markdown(report), media_type="text/markdown",
                        headers={"Content-Disposition": 'attachment; filename="activity-review.md"'})

    @app.post("/api/customer/activities/{case_id}/actions")
    def activity_action(case_id: str, payload: flow.Action, customer=Depends(current_customer)):
        with API_LOCK:
            case = owned_case(case_id, customer)
            check_case_revision(case, payload.revision)
            flow.customer_action(case, payload)
            return flow.activity_view(owned_case(case_id, customer))

    @app.post("/api/owner/session")
    def owner_login(payload: OwnerLogin, response: Response):
        password = os.environ.get("MIYOU_OWNER_PASSWORD")
        if not password:
            raise HTTPException(503, "启动服务前请设置 MIYOU_OWNER_PASSWORD。")
        if not compare_digest(payload.password.encode(), password.encode()):
            raise HTTPException(401, "老板工作台口令不正确。")
        response.set_cookie(OWNER_COOKIE, owner_session, httponly=True, samesite="strict")
        return {"role": "owner"}

    @app.post("/api/owner/logout")
    def owner_logout(response: Response):
        response.delete_cookie(OWNER_COOKIE, httponly=True, samesite="strict")
        return {"ok": True}

    @app.get("/api/owner/dashboard", dependencies=[Depends(require_owner)])
    def dashboard():
        snapshot = load_owner_dashboard(activity_tracking=True)
        return {**snapshot, "policy": CompanyPolicy().model_dump(mode="json")}

    def owner_order(key):
        row = next((x for x in load_owner_dashboard(activity_tracking=True)["orders"] if x["key"] == key), None)
        if row is None:
            raise HTTPException(404, "找不到该订单。")
        case = storage.load_case_from_disk(row["case_id"]) if row["kind"] == "case" else None
        booking = suppliers.load_order(row["supplier_order_id"]) if row["supplier_order_id"] else None
        return row, case, booking

    def owner_view(row, case, booking):
        return {
            **row, "revision": case.storage_revision if case else 1,
            "actions": flow.owner_actions(case, booking), "booking": flow.booking_view(booking, owner=True),
            "running": flow.planning_active(case.id) if case else False,
            "plans": flow.plan_views(case) if case else [],
            "approval": case.approval.model_dump(mode="json") if case and case.approval else None,
            "plan_feedback": case.plan_acceptance_feedback.model_dump(mode="json") if case and case.plan_acceptance_feedback else None,
            "delivery_feedback": case.delivery_feedback.model_dump(mode="json") if case and case.delivery_feedback else None,
            "attachments": [item.model_dump(mode="json") for item in case.attachment_insights] if case else [],
            **(document_view(case) if case else {}),
            "audit": [item.model_dump(mode="json") for item in case.audit_log[-30:]] if case else [],
        }

    @app.get("/api/owner/orders/{key}", dependencies=[Depends(require_owner)])
    def order(key: str):
        return owner_view(*owner_order(key))

    @app.post("/api/owner/orders/{key}/actions", dependencies=[Depends(require_owner)])
    def order_action(key: str, payload: flow.Action):
        with API_LOCK:
            _, case, booking = owner_order(key)
            if case:
                check_case_revision(case, payload.revision)
            flow.owner_action(case, booking, payload)
            return owner_view(*owner_order(key))

    register_owner_features(app, require_owner)

    boss = frontend / "boss" / "dist" / "client"
    customer = frontend / "customer" / "dist" / "client"

    @app.get("/boss", include_in_schema=False)
    @app.get("/boss/", include_in_schema=False)
    def boss_page():
        return frontend_page(boss)

    if (boss / "assets").is_dir():
        app.mount("/boss/assets", StaticFiles(directory=boss / "assets"), name="boss-assets")
    if (customer / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=customer / "assets"), name="customer-assets")

    @app.get("/{path:path}", include_in_schema=False)
    def customer_page(path: str):
        if path.split("/")[0] not in {"", "cases", "about", "create", "activities", "workspace", "documents", "offers"}:
            raise HTTPException(404)
        return frontend_page(customer)

    return app


def frontend_page(directory):
    index = directory / "index.html"
    if not index.is_file():
        raise HTTPException(503, "请先构建对应的 React 前端。")
    return FileResponse(index, headers={"Cache-Control": "no-cache"})


app = create_app()
