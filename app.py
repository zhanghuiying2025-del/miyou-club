import asyncio
import json
import queue
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

import streamlit as st

from src.agents import AgentConfigurationError, AgentOutputError, build_agent_team
from src.models import (
    CaseStatus,
    CustomerStyleFeedback,
    OutcomeFeedback,
    PlanAcceptanceFeedback,
    TravelCase,
    CreativeOpinion,
)
from src.visuals import proposal_poster, itinerary_diagram, collaboration_diagram
from src.contributions import adoption_evidence
from src import activity_order
from src.feedback_ui import render_feedback_records
from src.journey_ui import render_journey_demo, render_journey_owner
from src.procurement_ui import render_procurement
from src.storage import CaseConflictError, delete_case_from_disk, load_case_from_disk, save_case_to_disk, list_saved_cases
from src.visits import initialize_visit,remember_view,bind_case,new_customer,resume_for_customer
from src.workflow import TravelAgencyWorkflow, WorkflowError
from src.orchestration import handoffs_for, lifecycle
from src.runtime import submit_once, consume, active_jobs, progress_events, pause_planning
from src.presentation import short_text
from src.request_ui import request_wizard, show_request_summary
from src.activity_review import review_activity
from src.activity_review_ui import render_activity_request, render_activity_case
from src.document_intake import intake_documents
from src.document_intake_ui import render_document_upload, render_document_confirmation
from src.supplier_ui import render_supplier_workspace
from src.history_ui import render_history_workspace, render_case_references
from src.organization import load_organization_state
from src.owner_dashboard import load_owner_dashboard
from src.owner_dashboard_ui import render_owner_dashboard
from src.policy import CompanyPolicy


st.set_page_config(
    page_title="OPC 团建旅行社",
    page_icon="🧭",
    layout="wide",
)


OWNER_STATUS_LABELS = {
    CaseStatus.WAITING_REQUIREMENTS: "等待客户确认需求",
    CaseStatus.WAITING_DRAFT: "等待客户确认组合草案",
    CaseStatus.NEW: "等待处理",
    CaseStatus.INTAKE: "需求整理中",
    CaseStatus.WAITING_CUSTOMER: "等待客户补充",
    CaseStatus.ALIGNMENT: "需求对齐中",
    CaseStatus.CREATIVE_COUNCIL: "策划委员会创作中",
    CaseStatus.WAITING_STYLE_FEEDBACK: "等待客户确认提案方向",
    CaseStatus.CROSS_REVIEW: "履约执行检查中",
    CaseStatus.DELIBERATION: "委员会协调中",
    CaseStatus.REVIEWING: "运营与风险审核中",
    CaseStatus.WAITING_OWNER: "等待老板审批",
    CaseStatus.READY_FOR_CUSTOMER: "等待客户确认方案",
    CaseStatus.NEEDS_REWORK: "客户拒绝，等待返工",
    CaseStatus.COMPLETED: "已完成",
    CaseStatus.FAILED: "执行失败",
}

OWNER_DASHBOARD_AREA = "经营驾驶舱"
TRAVEL_AREA = "团建策划与交付"
SUPPLIER_AREA = "酒店餐饮模拟预订"
HISTORY_AREA = "历史订单管理"

CUSTOMER_STATUS_LABELS = {
    CaseStatus.WAITING_REQUIREMENTS: "请确认需求，确认后开始策划",
    CaseStatus.WAITING_DRAFT: "组合草案已完成，可以微调后再审核",
    CaseStatus.NEW: "团队准备开始处理",
    CaseStatus.INTAKE: "正在理解你的需求",
    CaseStatus.WAITING_CUSTOMER: "需要你补充关键信息",
    CaseStatus.ALIGNMENT: "正在整理需求",
    CaseStatus.CREATIVE_COUNCIL: "策划团队正在设计方案",
    CaseStatus.WAITING_STYLE_FEEDBACK: "提案方向已准备好",
    CaseStatus.CROSS_REVIEW: "正在检查所选方案的执行条件",
    CaseStatus.DELIBERATION: "策划团队正在组合方案",
    CaseStatus.REVIEWING: "方案正在进行最终检查",
    CaseStatus.WAITING_OWNER: "方案正在进行公司内部确认",
    CaseStatus.READY_FOR_CUSTOMER: "方案已经完成",
    CaseStatus.NEEDS_REWORK: "已收到你的拒绝意见，正在等待重新策划",
    CaseStatus.COMPLETED: "方案已确认",
    CaseStatus.FAILED: "团队处理时遇到问题",
}

TEAM_GOAL_OPTIONS = [
    "增强团队协作",
    "帮助新成员融入",
    "促进跨部门交流",
    "放松减压",
    "体验城市文化",
    "创造共同回忆",
]

HARD_CONSTRAINT_OPTIONS = [
    "不安排高风险运动",
    "活动强度保持轻松或中等",
    "不强迫成员参加竞技项目",
    "控制步行距离并安排休息",
    "行动不便成员可以参与主要活动",
    "必须准备雨天室内备用方案",
    "避免频繁赶场",
    "照顾内向成员的参与方式",
]

DIETARY_OPTIONS = [
    "需要素食选择",
    "需要清真餐食",
    "存在食物过敏人员",
    "需要少辣或不辣选择",
]

def get_workflow() -> TravelAgencyWorkflow:
    return TravelAgencyWorkflow(build_agent_team())


def run_async(coroutine, timeout_seconds: int = 240):
    async def runner():
        return await asyncio.wait_for(coroutine, timeout=timeout_seconds)

    try:
        return asyncio.run(runner())
    except TimeoutError as exc:
        raise WorkflowError(
            f"Agent 团队超过 {timeout_seconds} 秒没有完成，请稍后继续处理。"
        ) from exc


def get_case():
    case = st.session_state.get("case")
    owner=globals().get("role","客户")=="公司老板"
    scope="owner_case_id" if owner else "customer_case_id"
    target=st.session_state.get(scope)
    if case is not None:
        try:
            if not isinstance(case, TravelCase) or not all(hasattr(case, field) for field in (
                "handoffs", "document_intake", "planning_protocol", "supplier_order_id",
                "supplier_execution_signature", "history_references", "pending_supplement",
            )):
                raw_case = case.model_dump(mode="python") if hasattr(case, "model_dump") else case
                case = TravelCase.model_validate(raw_case)
                st.session_state["case"] = case
        except Exception as exc:
            st.error(f"内存中的旧案件迁移失败：{exc}")
            return None
    # Tests and freshly created in-memory drafts can provide an unbound case.
    known={st.session_state.get("customer_case_id"),st.session_state.get("owner_case_id")}
    if case is not None and case.id not in known:
        target=case.id
        bind_case(target,owner)
    if target is None:
        st.session_state.pop("case",None)
        return None
    if case is not None and case.id!=target:
        case=None
    try:
        latest = load_case_from_disk(target)
    except Exception as exc:
        st.error(f"本地案件文件读取失败：{exc}")
        return case
    if latest is not None and (case is None or latest.storage_revision > case.storage_revision):
        case = latest
        st.session_state["case"] = case
    elif latest is None and (case is None or case.storage_revision > 0):
        st.session_state.pop("case", None)
        bind_case(None,owner)
        return None
    st.session_state["case_view_role"]="owner" if owner else "customer"
    return case


def save_case(case):
    try:
        save_case_to_disk(case)
    except CaseConflictError:
        st.session_state["case"] = load_case_from_disk(case.id)
        st.session_state["sync_notice"] = "另一页面已更新案件，已加载最新状态，请重新确认操作。"
        st.rerun()
    st.session_state["case"] = case
    bind_case(case.id,globals().get("role","客户")=="公司老板")


def sync_current_case():
    return get_case()


def proposal_image(opinion, case):
    req = case.requirements
    context = f"{req.city or '城市待定'} · {req.participant_count or '?'}人 · {req.duration or '时长待定'}" if req else "定制团建提案"
    return proposal_poster(
        opinion.agent_name, opinion.concept_name,
        opinion.sharp_remark or opinion.customer_response or opinion.client_facing_message or opinion.one_sentence_pitch,
        tuple(opinion.recommended_activities),
        opinion.style_strengths[0] if opinion.style_strengths else "",
        opinion.style_tradeoffs[0] if opinion.style_tradeoffs else "", context,
    )


def render_public_feed(items):
    if not items:
        st.caption("团队正在准备本轮任务。")
    identities = {
        "宋野": ("🏙️", "城市潮玩策划师"),
        "沈知行": ("🏛️", "历史文化策划师"),
        "夏橙": ("🌿", "户外团建策划师"),
        "协调者": ("🧩", "方案协调"),
        "项目经理": ("🧩", "项目调度与核查"),
        "多角色策划": ("🎨", "三个角色共用一个Agent"),
        "策划": ("🎨", "活动设计"),
        "履约执行": ("📋", "资源落实与活动跟进"),
        "运营审核员": ("📋", "运营审核"),
        "风险审核员": ("🛡️", "风险审核"),
    }
    for update in items[-4:]:
        actor = update["actor"]
        avatar, role = identities.get(actor, ("💬", "团队成员"))
        with st.chat_message(actor, avatar=avatar):
            st.markdown(f"**{actor}｜{role}**")
            st.write(short_text(update["message"], 110))
            st.caption(update["stage"])


def flow_step(status):
    if status == CaseStatus.CROSS_REVIEW:
        return 1
    if status in {CaseStatus.DELIBERATION, CaseStatus.WAITING_DRAFT}:
        return 2
    if status in {CaseStatus.REVIEWING, CaseStatus.READY_FOR_CUSTOMER, CaseStatus.COMPLETED}:
        return 3
    return 0


def process_case(workflow, case, title: str, attachments=None, action: str = "advance", feedback=None, adjustments=None, comment=""):
    attachments = attachments or []
    case = case.model_copy(deep=True)

    async def execute():
        if action == "document_intake":
            return await intake_documents(workflow, case, attachments)
        if action == "activity_review":
            return await review_activity(workflow, case, attachments, comment)
        if attachments:
            await workflow.analyze_attachments(case, attachments)
            if case.status == CaseStatus.WAITING_REQUIREMENTS and case.requirements:
                for insight in case.attachment_insights:
                    case.requirements.preferences.extend(insight.preferences_or_intent)
                    case.requirements.hard_constraints.extend(insight.constraints)
        if action == "style":
            return await workflow.submit_style_feedback(case, feedback)
        if action == "draft":
            return await workflow.submit_draft_feedback(case, adjustments or [], comment)
        if action == "rework":
            return await workflow.rework_rejected_plan(case)
        if action == "resume":
            return await workflow.resume_case(case)
        if action in {"new_request", "confirm", "document_travel"} and case.status == CaseStatus.WAITING_REQUIREMENTS:
            if case.planning_mode != "style_branch":
                case.lead_agent = workflow.STYLE_AGENT_MAP.get(case.requirements.preferred_style or "")
            workflow.confirm_requirements(case)
        return await workflow.advance(case)

    with st.status(title, expanded=True) as status:
        updates = queue.Queue()
        workflow.progress_callback = lambda message: updates.put({"kind": "progress", "message": message, "status": case.status})
        workflow.public_update_callback = updates.put
        workflow.checkpoint_callback = save_case_to_disk
        timer = st.empty()
        current_step = st.empty()
        current_step.caption(title)
        flow = st.empty()
        activity_mode = action in {"activity_review", "document_intake"}
        if activity_mode:
            flow.caption("文档读取 → 必要缺项确认 → 方案审核或团建策划" if action == "document_intake"
                else "原文读取 → 项目经理审核 → 策划细化执行 → 组织者确认")
            st.caption("以文档和客户确认的信息为准，展示真实任务交接与业务结论。")
        else:
            flow.image(collaboration_diagram(flow_step(case.status), case.planning_protocol == "compact_v1",
                                             case.planning_mode == "style_branch"), use_container_width=True)
            st.caption("团队讨论 · 根据本轮任务进度与交接结果整理")
            if case.planning_mode == "style_branch" and action in {"new_request", "document_travel"}:
                st.button("我想补充关键信息", key=f"customer_live_supplement:{case.id}",
                          on_click=open_supplement, args=(case.id,))
        feed = st.empty()
        preview = st.empty()
        messages = [item.model_dump(mode="json") for item in case.public_updates[-12:]]
        posters = {}
        with feed.container():
            render_public_feed(messages)

        def drain_updates():
            changed, image_changed = False, False
            while not updates.empty():
                event = updates.get_nowait()
                if event["kind"] == "progress":
                    current_step.caption(event["message"])
                    if not activity_mode:
                        flow.image(collaboration_diagram(flow_step(event["status"]), case.planning_protocol == "compact_v1",
                                                         case.planning_mode == "style_branch"), use_container_width=True)
                else:
                    messages.append(event)
                    changed = True
                    if "opinion" in event:
                        opinion = CreativeOpinion.model_validate(event["opinion"])
                        posters[opinion.agent_name] = opinion
                        image_changed = True
            if changed:
                with feed.container():
                    render_public_feed(messages)
            if image_changed:
                with preview.container():
                    st.caption("本轮策划提案" if case.planning_mode == "style_branch" else "本轮历史角色提案")
                    columns = st.columns(len(posters))
                    for i, (name, opinion) in enumerate(posters.items()):
                        with columns[i]:
                            st.caption(name)
                            st.image(proposal_image(opinion, case), caption=opinion.concept_name, use_container_width=True)
                            with st.expander("先看已完成的方案"):
                                if opinion.sharp_remark:
                                    st.write(opinion.sharp_remark)
                                st.write(opinion.client_facing_message or opinion.one_sentence_pitch)
                                for activity in opinion.recommended_activities:
                                    st.write(f"- {activity}")

        def work():
            try:
                return run_async(execute())
            except Exception as exc:
                if case.status not in {CaseStatus.FAILED, CaseStatus.NEEDS_REWORK}:
                    case.resume_status, case.status = case.status, CaseStatus.FAILED
                case.error_message = str(exc)
                raise
            finally:
                save_case_to_disk(case)
        payload = json.dumps({"feedback": feedback.model_dump() if feedback else None,
            "adjustments": adjustments, "comment": comment}, ensure_ascii=False, sort_keys=True)
        key = ("planning", case.id, case.plan_version, action, case.status.value,
            case.draft_revision_count, case.revision_count, case.storage_revision, payload)
        running = [j for j in active_jobs(case.id) if j.key[0] == "planning"]
        job = running[0] if running else submit_once(key, case.id, work)
        started = job.started
        try:
            while not job.future.done():
                drain_updates()
                events = progress_events(job)
                if events and not messages:
                    with feed.container():
                        render_public_feed(events)
                timer.caption(f"已等待 {int(time.monotonic() - started)} 秒 · 任务状态已保存，可刷新查看")
                if time.monotonic() - started > 245:
                    st.warning("本轮处理较久，后台任务仍在跟进，可稍后刷新查看。")
                    return get_case() or case
                time.sleep(0.2)
            case = job.future.result()
            consume(job, lambda result: None)
            drain_updates()
            timer.caption(f"本轮处理用时 {time.monotonic() - started:.1f} 秒")
        except WorkflowError:
            status.update(label="请根据提示调整后再提交", state="error")
            raise
        except Exception as exc:
            status.update(label=f"处理失败：{exc}", state="error")
            raise WorkflowError(f"处理失败：{exc}") from exc
        status.update(
            label=("已在当前交接点暂停" if job.pause.is_set() and case.status != CaseStatus.FAILED else
                   f"处理结束：{OWNER_STATUS_LABELS.get(case.status, case.status.value)}"),
            state="error" if case.status == CaseStatus.FAILED else "complete",
        )
    latest = load_case_from_disk(case.id)
    st.session_state['case'] = latest if latest and latest.id == case.id and latest.storage_revision >= case.storage_revision else case
    return case


def show_final_plan(case, show_internal_details: bool = False):
    plan = case.council_decision
    if plan is None:
        st.info("最终方案尚未生成。")
        return

    st.subheader(plan.theme)
    st.write(plan.summary)
    route = itinerary_diagram(plan.theme, tuple(plan.schedule))
    st.image(route, caption="活动顺序示意，不代表真实地理位置和导航路线", use_container_width=True)
    st.download_button("下载行程顺序图", route, file_name=f"行程图-{case.id}.png", mime="image/png", key=f"route_{case.id}_{show_internal_details}")

    st.markdown("#### 建议行程")
    for item in plan.schedule:
        st.write(f"- {item}")

    st.markdown("#### 为什么这样安排")
    for item in plan.rationale:
        st.write(f"- {item}")

    if plan.items_to_verify:
        st.markdown("#### 落地前待核实")
        for item in plan.items_to_verify:
            st.info(item)

    if plan.customer_notice:
        st.markdown("#### 重要提醒")
        for item in plan.customer_notice:
            st.warning(item)

    if show_internal_details:
        st.markdown("#### 内部组合原则")
        for item in plan.operating_principles:
            st.write(f"- {item}")
        if plan.rejected_ideas:
            st.markdown("#### 未采用的建议")
            for item in plan.rejected_ideas:
                st.caption(item)


def render_customer_feedback(workflow, case):
    if case.delivery_feedback is not None:
        st.success("履约评价已提交。")
        return

    st.subheader("评价本次实际体验")

    with st.form("customer_feedback_form"):
        overall = st.slider("总体满意度", 1, 5, 4)
        novelty = st.slider("新鲜感", 1, 5, 4)
        culture = st.slider("文化内容", 1, 5, 4)
        detail = st.slider("细节完整度", 1, 5, 4)
        engagement = st.slider("团队参与感", 1, 5, 4)
        feasibility = st.slider("方案可执行性", 1, 5, 4)
        issues_text = st.text_area("发现的问题（一行一个，没有可留空）")
        comment = st.text_area("其他意见")
        submitted = st.form_submit_button("提交履约评价", type="primary")

    if submitted:
        feedback = OutcomeFeedback(
            overall_rating=overall,
            novelty_rating=novelty,
            culture_rating=culture,
            detail_rating=detail,
            engagement_rating=engagement,
            feasibility_rating=feasibility,
            incidents=[line.strip() for line in issues_text.splitlines() if line.strip()],
            comment=comment,
        )
        try:
            case = workflow.submit_outcome_feedback(case, feedback, after_delivery=True)
            save_case(case)
            st.rerun()
        except WorkflowError as exc:
            st.error(str(exc))


def render_plan_acceptance(workflow, case):
    if case.plan_feedback_options is None:
        with st.spinner("正在根据这份方案准备评价选项……"):
            try:
                case = run_async(workflow.prepare_plan_feedback_options(case))
                save_case(case)
            except WorkflowError as exc:
                st.error(str(exc))
                return

    st.subheader("你对这份最终方案的态度")
    st.caption("选择最接近真实感受的一档，再勾选具体原因，帮助团队保留有效做法、检查问题。")

    outcome_labels = {
        "satisfied": "满意，符合我的期待",
        "acceptable": "能接受，但仍有保留",
        "unacceptable": "完全不能接受，需要重新策划",
    }
    outcome = st.radio(
        "方案结论",
        options=list(outcome_labels),
        format_func=lambda value: outcome_labels[value],
        horizontal=True,
        key=f"plan_outcome_{case.id}_{case.plan_version}",
    )

    options_by_outcome = {
        "satisfied": case.plan_feedback_options.satisfied if case.plan_feedback_options else [],
        "acceptable": case.plan_feedback_options.acceptable if case.plan_feedback_options else [],
        "unacceptable": case.plan_feedback_options.unacceptable if case.plan_feedback_options else [],
    }
    available = options_by_outcome[outcome]
    labels_by_id = {item.id: item.label for item in available}
    selected_ids = st.multiselect(
        "具体原因（可多选）",
        options=list(labels_by_id),
        format_func=lambda option_id: labels_by_id[option_id],
        key=f"plan_reasons_{case.id}_{case.plan_version}_{outcome}",
    )
    comment = st.text_area(
        "哪里不满意？" if outcome == "unacceptable" else "还想告诉我们的内容（选填）",
        placeholder="例如，转场太多，希望把活动集中在同一个场地。" if outcome == "unacceptable" else None,
        max_chars=2000,
        key=f"plan_comment_{case.id}_{case.plan_version}",
    )

    if outcome == "unacceptable":
        st.info("提交意见后，团队会在同一订单中生成新版本，原方案和反馈会留档。")

    if st.button("提交意见并重新生成" if outcome == "unacceptable" else "提交方案评价",
                 type="primary", key=f"submit_plan_outcome_{case.id}"):
        feedback = PlanAcceptanceFeedback(
            outcome=outcome,
            selected_option_ids=selected_ids,
            comment=comment.strip(),
        )
        try:
            case = workflow.submit_plan_acceptance(case, feedback)
            save_case(case)
            if case.status == CaseStatus.NEEDS_REWORK:
                process_case(workflow, case, "团队正在根据你的意见生成新方案……", action="rework")
            st.rerun()
        except WorkflowError as exc:
            st.error(str(exc))


def render_style_selection(workflow, case):
    if case.planning_mode == "style_branch":
        st.subheader("看看这个团建方向")
        if not case.creative_opinions:
            return
        opinion = case.creative_opinions[0]
        st.markdown(f"**{opinion.concept_name}**")
        st.write(opinion.one_sentence_pitch)
        for item in opinion.recommended_activities:
            st.write(f"- {item}")
        if case.style_template:
            with st.expander("本单参考的历史风格做法"):
                for method in case.style_template["methods"]:
                    st.write(f"- {method}")
                for example in case.style_template.get("examples", []):
                    st.caption(f"满意方案 {example['case_id']} · 第{example['version']}版")
        with st.form(f"style_branch_{case.id}_{case.plan_version}"):
            with st.expander("对提案有疑问或投诉？"):
                question = st.text_input("想问的问题")
                complaint = st.text_area("投诉内容")
            submitted = st.form_submit_button("确认方向，查看草案", type="primary")
        if submitted:
            try:
                process_case(workflow, case, "团队正在核查并整理草案……", action="style",
                             feedback=CustomerStyleFeedback(question=question, complaint=complaint))
                st.rerun()
            except WorkflowError as exc:
                st.error(str(exc))
        return
    st.subheader("选择你更喜欢的策划风格")
    st.caption(
        "同一个策划 Agent 提供三个角色的表达和取舍。单纯不喜欢某种风格属于偏好；"
        "如果方案违反明确要求或表达不专业，可以提出投诉。"
    )

    columns = st.columns(3)
    order = {name: i for i, name in enumerate(case.proposal_completion_order)}
    opinions = sorted(case.creative_opinions, key=lambda x: order.get(x.agent_name, 99))
    for index, opinion in enumerate(opinions):
        with columns[index % 3]:
            with st.container(border=True):
                lead_label = " · 当前主责" if case.lead_agent == opinion.agent_name else ""
                st.markdown(f"### {opinion.agent_name}{lead_label}")
                st.caption(opinion.persona_title)
                if opinion.agent_name in order:
                    st.caption(f"第{order[opinion.agent_name] + 1}位提交 · 完成顺序不影响贡献分")
                poster = proposal_image(opinion, case)
                st.image(poster, caption=opinion.concept_name, use_container_width=True)
                st.download_button("下载提案图片", poster, file_name=f"{opinion.agent_name}-{case.id}.png", mime="image/png", key=f"poster_{case.id}_{index}")
                with st.expander("查看提案文字与完整取舍"):
                    if opinion.customer_response:
                        st.write(opinion.customer_response)
                    if opinion.sharp_remark:
                        st.write(opinion.sharp_remark)
                    st.write(opinion.client_facing_message or opinion.one_sentence_pitch)
                    for item in opinion.recommended_activities:
                        st.write(f"- {item}")
                    st.caption("优势：" + "；".join(opinion.style_strengths))
                    st.caption("取舍：" + "；".join(opinion.style_tradeoffs))

    agent_options = ["暂不确定", "宋野", "沈知行", "夏橙"]
    preferred_index = (
        agent_options.index(case.lead_agent)
        if case.lead_agent in agent_options
        else 0
    )

    with st.form("style_feedback_form"):
        priorities = st.multiselect("组合时希望优先调整", ["节奏更轻松", "增加自由活动", "加强团队互动", "减少竞争环节", "增加文化体验", "更多室内活动"])
        preferred_agent = st.selectbox(
            "最终更偏好哪位策划师的风格？",
            agent_options,
            index=preferred_index,
        )
        disliked_agent = st.selectbox(
            "最不喜欢哪位策划师的风格？",
            ["没有特别不喜欢", "宋野", "沈知行", "夏橙"],
        )
        preference_reason = st.text_area(
            "选择原因（可选）",
            placeholder="例如：喜欢城市探索，但不希望一天安排太多地点。",
        )
        question = st.text_area(
            "对方案的质疑（可选）",
            placeholder="例如：为什么安排这么长时间的博物馆参观？",
        )
        complaint = st.text_area(
            "投诉内容（可选）",
            placeholder="例如：我明确要求照顾行动不便成员，但方案仍安排了长距离徒步。",
        )
        submitted = st.form_submit_button("提交选择，生成组合草案", type="primary")

    if submitted:
        feedback = CustomerStyleFeedback(
            preferred_agent=None if preferred_agent == "暂不确定" else preferred_agent,
            disliked_agent=(
                None if disliked_agent == "没有特别不喜欢" else disliked_agent
            ),
            preference_reason="；".join([*priorities, preference_reason.strip()]).strip("；"),
            question=question,
            complaint=complaint,
        )

        try:
            process_case(workflow, case, "团队正在组合你选中的方向……", action="style", feedback=feedback)
            st.rerun()
        except (WorkflowError, AgentOutputError) as exc:
            save_case(case)
            st.error(str(exc))


def render_requirements_confirmation(workflow, case):
    req = case.requirements
    if req is None:
        st.warning("需求尚未整理完成，请从老板端继续处理。")
        return
    if case.planning_mode == "style_branch":
        st.subheader("确认更新后的需求")
        show_request_summary(req)
        if st.button("确认需求并开始策划", type="primary"):
            try:
                process_case(workflow, case, "团队正在准备提案……", action="confirm")
                st.rerun()
            except WorkflowError as exc:
                st.error(str(exc))
        return
    st.subheader("先确认我们理解得对不对")
    st.write(f"{req.city} · {req.event_date} · {req.participant_count}人 · {req.duration or '时长待定'}")
    st.caption(f"团队：{req.team_profile or '未指定'} ｜ 风格：{req.preferred_style or '不限'}")
    st.write("目标：" + "、".join(req.desired_outcomes or ["未特别指定"]))
    with st.form(f"confirm_requirements_{case.id}"):
        budget = st.number_input("确认总预算（元，0表示待定）", min_value=0, value=int(req.budget_yuan or 0), step=100)
        constraints = st.text_area("必须遵守的要求（每行一项，可直接修改）", value="\n".join(req.hard_constraints), height=100)
        notes = st.text_input("再补充一句（选填）", placeholder="例如：上午不要太早集合")
        confirmed = st.form_submit_button("确认需求，让三位策划师提案", type="primary")
    if confirmed:
        req.budget_yuan = budget or None
        req.hard_constraints = [x.strip() for x in constraints.splitlines() if x.strip()]
        if notes.strip():
            req.preferences.append(notes.strip())
        case.customer_request += "\n客户确认的需求：" + req.model_dump_json()
        try:
            workflow.confirm_requirements(case)
            save_case(case)
            process_case(workflow, case, "策划 Agent 正在生成三角色精简提案……")
            st.rerun()
        except WorkflowError as exc:
            st.error(str(exc))


def render_draft_confirmation(workflow, case):
    plan = case.council_decision
    if plan is None:
        st.warning("草案尚未生成，请从老板端继续处理。")
        return
    st.subheader("组合草案：先看方向，再看完整安排")
    st.write(f"**{plan.theme}**")
    st.write(plan.summary[:200])
    st.image(itinerary_diagram(plan.theme, tuple(plan.schedule)), caption="草案行程顺序图 · 非导航地图", use_container_width=True)
    for notice in plan.customer_notice[:2]:
        st.caption(notice)
    with st.expander("查看本轮组合与取舍"):
        for item in plan.merged_ideas[:3]:
            st.write(f"- {item}")
        for item in plan.rejected_ideas[:2]:
            st.caption(f"未采用：{item}")
    if case.planning_mode == "style_branch":
        if st.button("确认草案，进入最终检查", type="primary"):
            try:
                process_case(workflow, case, "团队正在核查最终方案……", action="draft")
                st.rerun()
            except WorkflowError as exc:
                st.error(str(exc))
        return
    st.caption(f"可微调两次，已使用 {case.draft_revision_count} 次。确认后执行程序核验；方案有变化或存在阻塞时再复核。")
    with st.form(f"draft_feedback_{case.id}_{case.draft_revision_count}"):
        changes = st.multiselect("希望怎么调整（不选表示确认）", ["减少赶场，增加休息", "增加团队合作环节", "减少竞技与强制互动", "增加自由活动时间", "文化讲解更短、更有趣", "户外活动改为低强度"])
        comment = st.text_input("具体调整（选填）")
        submitted = st.form_submit_button("提交微调 / 无调整则确认并审核", type="primary")
    if submitted:
        try:
            process_case(workflow, case, "正在处理草案反馈……", action="draft", adjustments=changes, comment=comment)
            st.rerun()
        except WorkflowError as exc:
            st.error(str(exc))


@st.fragment(run_every="1s")
def render_shared_wait(case, waiting=False):
    latest = get_case()
    if latest is not case:
        st.rerun()
    jobs = active_jobs(case.id)
    if waiting and not any(j.key[0] == "planning" for j in jobs):
        st.rerun()
    st.caption("当前进度与最近动态会自动更新。")
    if jobs:
        st.status("团队正在处理你的安排……", state="running", expanded=False)
        st.caption(f"已运行 {int(max(time.monotonic() - j.started for j in jobs))} 秒")
    render_public_feed([x.model_dump(mode="json") for x in case.public_updates[-4:]])


def open_supplement(case_id):
    st.session_state[f"customer_supplement_open:{case_id}"] = True
    pause_planning(case_id)


def render_supplement(workflow, case, running=False):
    if case.pending_supplement:
        if not running and st.button("继续处理这次补充"):
            process_case(workflow, case, "团队正在核对补充信息……", action="resume")
            st.rerun()
        return False
    key = f"customer_supplement_open:{case.id}"
    if not st.session_state.get(key):
        st.button("我想补充关键信息", key=f"customer_supplement_button:{case.id}",
                  on_click=open_supplement, args=(case.id,))
        return False
    st.subheader("补充或更正关键信息")
    if case.customer_questions:
        st.info(case.customer_questions[0])
    if running:
        st.caption("当前任务结束后暂停，补充内容会从保留的步骤处理。")
    if case.procurement_order or case.supplier_order_id or case.journey_demo:
        st.caption("涉及已建订单的补充将交给公司确认，付款和预订记录继续保留。")
    note = st.text_area("本次补充", max_chars=2000, key=f"customer_supplement_text:{case.id}",
                        placeholder="例如，两位成员行动不便，需要改为有电梯的路线。")
    left, right = st.columns(2)
    if left.button("返回当前步骤"):
        st.session_state.pop(key, None)
        st.rerun()
    if right.button("提交补充", type="primary", disabled=running):
        try:
            workflow.record_supplement(case, note)
            save_case(case)
            st.session_state.pop(key, None)
            if case.pending_supplement:
                process_case(workflow, case, "团队正在按补充信息调整……")
            else:
                st.session_state["sync_notice"] = "已把补充转给公司核对。"
            st.rerun()
        except WorkflowError as exc:
            st.error(str(exc))
    return True


def render_customer_portal(workflow, case, request_type):
    if case is not None:
        st.button("保存进度并开始新需求",on_click=new_customer,key="customer_new_request")
        st.caption("已提交内容持续保存；开始新需求后，旧案件可在老板端历史记录中继续。")
        latest_learning = next((entry for entry in reversed(case.audit_log) if entry.action == "scenario_learned"), None)
        if latest_learning:
            st.success(latest_learning.detail)
    running = bool(case and any(j.key[0] == "planning" for j in active_jobs(case.id)))
    if (case and case.service_mode == "travel_planning" and case.planning_mode == "style_branch"
            and (case.status != CaseStatus.WAITING_CUSTOMER or running)):
        if render_supplement(workflow, case, running):
            if running:
                render_shared_wait(case, waiting=True)
            return
    if running:
        render_shared_wait(case, waiting=True)
        return

    if case is None:
        if request_type == "上传已有资料":
            render_document_upload(workflow, process_case, save_case)
            return

        result = request_wizard(TEAM_GOAL_OPTIONS, HARD_CONSTRAINT_OPTIONS, DIETARY_OPTIONS)
        if result:
            req, attachments = result
            try:
                case = workflow.create_case(req.model_dump_json())
                case.requirements = req
                case.status = CaseStatus.WAITING_REQUIREMENTS
                save_case(case)
                st.session_state.pop("trip_wizard", None)
                process_case(workflow, case, "团队正在准备提案……", attachments=attachments, action="new_request")
                st.rerun()
            except WorkflowError as exc:
                st.error(str(exc))
        return

    if case.service_mode == "document_intake":
        render_document_confirmation(workflow, case, process_case, save_case,
                                     tracking_enabled=activity_order.TRACKING_ENABLED)
        return
    if case.service_mode == "activity_review":
        render_activity_case(workflow, case, process_case, save_case, sync=sync_current_case,
                             tracking_enabled=activity_order.TRACKING_ENABLED)
        return
    if not case.procurement_order and not case.journey_demo:
        with st.expander("文档是现成活动方案？改用审核与执行细化"):
            st.caption("重新上传原文后切换本案服务。原有记录保留，不再继续生成旅行提案。")
            render_document_upload(workflow, process_case, save_case, existing_case=case)
    st.caption(f"{CUSTOMER_STATUS_LABELS.get(case.status, '正在处理')} · 服务编号 {case.id}")
    if case.customer_objections:
        st.subheader("策划师想直接和你确认")
        latest = {item.agent_name: item for item in case.customer_objections}
        for name, objection in latest.items():
            st.info(f"**{name} 对你说**\n\n{objection.message}")
        st.caption("这是策划师对具体要求的异议，仍待澄清。你可以在本页的反馈或补充信息中解释、选择取舍或提出质疑。")
    if case.public_updates:
        with st.expander("团队如何讨论出这份方案？"):
            st.image(collaboration_diagram(flow_step(case.status), case.planning_protocol == "compact_v1",
                                           case.planning_mode == "style_branch"), use_container_width=True)
            render_public_feed([item.model_dump(mode="json") for item in case.public_updates[-12:]])

    if case.attachment_insights:
        with st.expander(f"已理解 {len(case.attachment_insights)} 个参考附件"):
            for insight in case.attachment_insights:
                st.markdown(f"**{insight.file_name}**")
                st.write(insight.summary)
                if insight.uncertainties:
                    st.caption("仍需核实：" + "；".join(insight.uncertainties))

    if case.feedback_assessment:
        with st.expander("公司对质疑或投诉的处理结果"):
            st.write(case.feedback_assessment.customer_response)
            st.caption(case.feedback_assessment.rationale)

    if case.status == CaseStatus.WAITING_REQUIREMENTS:
        render_requirements_confirmation(workflow, case)
    elif case.status == CaseStatus.WAITING_DRAFT:
        render_draft_confirmation(workflow, case)
    elif case.status == CaseStatus.WAITING_STYLE_FEEDBACK:
        render_style_selection(workflow, case)

    elif case.status == CaseStatus.WAITING_CUSTOMER:
        st.subheader("请补充会影响方案的关键信息")
        for question in case.customer_questions:
            st.info(question)
        answer = st.text_area("你的答复", max_chars=2000,
                              key=f"customer_answer:{case.id}:{case.clarification_rounds}")
        if st.button("提交补充信息", type="primary"):
            try:
                case = workflow.record_customer_answer(case, answer)
                save_case(case)
                process_case(workflow, case, "团队正在根据补充信息重新策划……")
                st.rerun()
            except WorkflowError as exc:
                st.error(str(exc))

    elif case.status in {
        CaseStatus.READY_FOR_CUSTOMER,
        CaseStatus.NEEDS_REWORK,
        CaseStatus.COMPLETED,
    }:
        delivery_completed = bool(case.procurement_order and case.procurement_order.settled)
        if case.status == CaseStatus.COMPLETED:
            if case.supplier_order_id:
                render_supplier_workspace(owner=False,order_id=case.supplier_order_id)
                from src.supplier_simulation import load_order as load_supplier_order
                linked_order,_=load_supplier_order(case.supplier_order_id)
                delivery_completed = linked_order.status == "completed"
                if linked_order.status in {"confirmed","delivered","completed"}:
                    from src.supplier_simulation import linked_execution,confirm_linked_execution
                    proposed,key,changed=linked_execution(case,linked_order)
                    if changed and case.supplier_execution_signature!=key:
                        st.warning("酒店餐饮合同已排除原火源活动，请确认行程变化。")
                        for before,after in zip(case.council_decision.schedule,proposed):
                            if before!=after:
                                st.write(f"原安排：{before}")
                                st.info(f"建议执行：{after}")
                        agreed=st.checkbox("接受上述时段改为自愿休息，替代活动另行确认",key=f"linked_change:{case.id}")
                        if st.button("客户确认更新执行行程",disabled=not agreed):
                            confirm_linked_execution(case,"customer")
                            save_case(case)
                            st.rerun()
                    elif activity_order.TRACKING_ENABLED:
                        render_journey_demo(case,workflow.team,save_case,sync=sync_current_case)
            else:
                if case.council_decision and not (case.procurement_order or case.journey_demo):
                    st.button("为本方案补充酒店餐饮需求", on_click=switch_customer_area,
                              args=("酒店餐饮模拟预订",))
                render_procurement(case, save_case, sync=sync_current_case,
                                   allow_manual_delivery=not activity_order.TRACKING_ENABLED)
            if activity_order.TRACKING_ENABLED and case.procurement_order and case.procurement_order.status == "ready":
                render_journey_demo(case, workflow.team, save_case, sync=sync_current_case)
            with st.expander("查看已确认的完整方案"):
                show_final_plan(case)
        else:
            show_final_plan(case)
        if case.status == CaseStatus.READY_FOR_CUSTOMER:
            render_plan_acceptance(workflow, case)
        elif case.status == CaseStatus.NEEDS_REWORK:
            st.info("已保存你的意见，可以继续生成新版本。")
            if case.plan_acceptance_feedback:
                for label in case.plan_acceptance_feedback.selected_labels:
                    st.write(f"- {label}")
                if case.plan_acceptance_feedback.comment:
                    st.info(case.plan_acceptance_feedback.comment)
            if st.button("继续生成新方案", key=f"customer_rework:{case.id}"):
                try:
                    process_case(workflow, case, "团队正在根据你的意见生成新方案……", action="rework")
                    st.rerun()
                except WorkflowError as exc:
                    st.error(str(exc))
        else:
            outcome_text = {
                "satisfied": "你对方案表示满意，方案已经确认。",
                "acceptable": "你认为方案可以接受，方案已经确认。",
            }
            outcome = case.plan_acceptance_feedback.outcome if case.plan_acceptance_feedback else ""
            st.success(outcome_text.get(outcome, "方案已经确认。"))
            if case.plan_acceptance_feedback:
                for label in case.plan_acceptance_feedback.selected_labels:
                    st.write(f"- {label}")
            if delivery_completed:
                render_customer_feedback(workflow, case)

    elif case.status == CaseStatus.FAILED:
        st.warning("策划团队处理时遇到了问题，公司老板正在处理。你不需要重复提交需求。")
    else:
        st.write("处理记录已保存，刷新不会清空需求。")
        automatic = {CaseStatus.NEW, CaseStatus.INTAKE, CaseStatus.ALIGNMENT,
            CaseStatus.CREATIVE_COUNCIL, CaseStatus.CROSS_REVIEW, CaseStatus.DELIBERATION, CaseStatus.REVIEWING}
        if case.status in automatic and not active_jobs(case.id):
            st.info("当前没有正在运行的后台任务，可从保存的步骤继续。")
            if st.button("继续未完成处理", key=f"continue_case_{case.id}"):
                try:
                    process_case(workflow, case, "从已保存步骤继续……", action="resume")
                    st.rerun()
                except WorkflowError as exc:
                    st.error(str(exc))


def render_owner_overview(case):
    st.subheader("公司案件总览")
    if case is None:
        st.info("目前没有客户案件。")
        return

    left, middle, right = st.columns(3)
    left.metric("当前案件", case.id)
    middle.metric("组织状态", OWNER_STATUS_LABELS.get(case.status, case.status.value))
    right.metric("内部修订次数", case.revision_count)

    if case.requirements:
        st.markdown("#### 已确认需求")
        st.json(case.requirements.model_dump(mode="json"), expanded=False)

    if case.attachment_insights:
        st.markdown("#### 附件分析结果")
        for insight in case.attachment_insights:
            with st.expander(insight.file_name):
                st.write(insight.summary)
                if insight.observed_facts:
                    st.write("确认内容：")
                    for item in insight.observed_facts:
                        st.write(f"- {item}")
                if insight.preferences_or_intent:
                    st.write("偏好或意图：")
                    for item in insight.preferences_or_intent:
                        st.write(f"- {item}")
                if insight.constraints:
                    st.write("可能影响方案的限制：")
                    for item in insight.constraints:
                        st.write(f"- {item}")
                if insight.uncertainties:
                    st.warning("待核实：" + "；".join(insight.uncertainties))

    if case.error_message:
        st.error(case.error_message)

    if case.status in {
        CaseStatus.NEW, CaseStatus.INTAKE, CaseStatus.FAILED, CaseStatus.ALIGNMENT,
        CaseStatus.CREATIVE_COUNCIL, CaseStatus.CROSS_REVIEW, CaseStatus.DELIBERATION,
        CaseStatus.REVIEWING,
    }:
        if st.button("从中断步骤继续处理", type="primary"):
            try:
                process_case(get_workflow(), case, "Agent 团队正在从中断步骤继续……", action="resume")
                st.rerun()
            except (WorkflowError, AgentConfigurationError) as exc:
                st.error(str(exc))

    if case.status == CaseStatus.NEEDS_REWORK:
        st.warning("客户明确拒绝了当前方案，不能将案件标记为完成。")
        if case.plan_acceptance_feedback:
            st.markdown("#### 客户拒绝原因")
            for label in case.plan_acceptance_feedback.selected_labels:
                st.write(f"- {label}")
            if case.plan_acceptance_feedback.comment:
                st.info(case.plan_acceptance_feedback.comment)
        if st.button("根据客户拒绝原因重新策划", type="primary"):
            try:
                process_case(get_workflow(), case, "委员会正在根据客户拒绝意见返工……", action="rework")
                st.rerun()
            except (WorkflowError, AgentConfigurationError) as exc:
                st.error(str(exc))

    if case.council_decision:
        st.divider()
        show_final_plan(case, show_internal_details=True)

    st.divider()
    if st.button("删除当前案件", type="secondary"):
        try:
            delete_case_from_disk(case)
        except CaseConflictError:
            st.session_state["case"] = load_case_from_disk(case.id)
            st.session_state["sync_notice"] = "案件刚刚在另一页面更新，请检查最新内容后再决定是否删除。"
            st.rerun()
        st.session_state.pop("case", None)
        st.rerun()


def render_owner_council(case):
    st.subheader("策划与履约工作记录")
    if case is None or not case.creative_opinions:
        st.info("策划 Agent 尚未提交提案。")
        return

    st.markdown("### 策划提案")
    if case.planning_mode == "style_branch":
        if case.style_template:
            with st.expander("本单参考的历史风格模板"):
                for method, sources in case.style_template["methods"].items():
                    st.write(f"{method} · 来源订单 {'、'.join(sources)}")
                for example in case.style_template.get("examples", []):
                    st.caption(f"满意方案 {example['case_id']} · 第{example['version']}版")
    elif case.planning_mode == "single_agent_roles":
        st.caption("单个策划 Agent 提供宋野、沈知行、夏橙三种视角，保留贡献和反馈依据；由履约 Agent 检查，经理整合。")
    else:
        st.caption("历史案件：提案可能由旧版独立策划师生成；继续处理时使用单 Agent 三角色模式。")
    for opinion in case.creative_opinions:
        with st.expander(
            f"{opinion.agent_name} · {opinion.persona_title} · {opinion.concept_name}",
            expanded=False,
        ):
            st.write(opinion.one_sentence_pitch)
            st.progress(opinion.confidence, text=f"信心 {opinion.confidence:.0%}")
            st.markdown("**推荐**")
            for item in opinion.recommended_activities:
                st.write(f"- {item}")
            if opinion.rejected_activities:
                st.markdown("**明确反对**")
                for item in opinion.rejected_activities:
                    st.write(f"- {item}")
            if opinion.concerns:
                st.markdown("**担忧**")
                for item in opinion.concerns:
                    st.write(f"- {item}")
            if opinion.possible_concessions:
                st.markdown("**让步条件**")
                for item in opinion.possible_concessions:
                    st.write(f"- {item}")

    if case.style_feedback or case.feedback_assessment:
        st.divider()
        st.subheader("客户风格选择与投诉调查")
        if case.style_feedback:
            feedback = case.style_feedback
            if case.planning_mode == "style_branch":
                st.write(f"客户选定方向　{case.requirements.preferred_style if case.requirements else '还没想好'}")
            else:
                st.write(f"最终偏好：{feedback.preferred_agent or '暂未确定'}")
                st.write(f"最不喜欢：{feedback.disliked_agent or '没有特别不喜欢'}")
            if feedback.preference_reason:
                st.info(feedback.preference_reason)
            if feedback.question:
                st.write(f"质疑：{feedback.question}")
            if feedback.complaint:
                st.warning(f"投诉：{feedback.complaint}")

        if case.feedback_assessment:
            assessment = case.feedback_assessment
            st.markdown("**运营判断**")
            st.write(assessment.rationale)
            if assessment.complaint_present:
                st.write(
                    "投诉判断："
                    + ("合理" if assessment.complaint_reasonable else "未认定为合理投诉")
                )
            st.write(f"给客户的回复：{assessment.customer_response}")

        if case.applied_constraints:
            st.markdown("**已经生效的约束**")
            for item in case.applied_constraints:
                st.success(item)

    if case.feasibility_review:
        st.divider()
        st.subheader("第二轮：履约执行检查")
        st.write(case.feasibility_review.summary)
        for finding in case.feasibility_review.findings:
            with st.expander(f"[{finding.severity}] {finding.description}"):
                st.write(f"依据：{finding.evidence}")
                st.write(f"解除条件：{finding.release_condition}")
                for alternative in finding.alternatives:
                    st.write(f"- {alternative}")
        if case.feasibility_review.pending_resources:
            st.caption("待落实资源，不代表已经预订")
            for resource in case.feasibility_review.pending_resources:
                st.write(f"- {resource}")

    if case.manager_resolutions:
        st.markdown("**经理如何处理分歧**")
        for resolution in case.manager_resolutions:
            st.write(f"- {resolution}")

    if case.council_decision and case.council_decision.completion_conditions:
        st.markdown("**本轮交付完成条件**")
        for condition in case.council_decision.completion_conditions:
            st.write(f"- {condition}")
        st.caption("交付条件不代替资源回执、客户确认或老板授权。")

    if case.peer_reviews:
        st.divider()
        st.subheader("历史记录：策划角色交叉质询")
        st.caption("以下是三位策划师对同事方案的公开业务意见，不是隐藏思维过程。")
        for review in case.peer_reviews:
            with st.expander(
                f"{review.reviewer_name}的交叉评审"
                f" · {'调整了立场' if review.position_changed else '保持原立场'}",
                expanded=False,
            ):
                if review.supported_points:
                    st.markdown("**认可同事的部分**")
                    for item in review.supported_points:
                        st.success(item)

                if review.challenges:
                    st.markdown("**公开质疑**")
                    for challenge in review.challenges:
                        with st.container(border=True):
                            st.markdown(
                                f"**质疑对象：{challenge.target_agent} · "
                                f"严重度：{challenge.severity}**"
                            )
                            st.write(challenge.issue)
                            st.caption(f"为什么重要：{challenge.why_it_matters}")
                            st.info(f"需要的证据或修改：{challenge.evidence_or_change_needed}")

                if review.proposed_compromises:
                    st.markdown("**折中建议**")
                    for item in review.proposed_compromises:
                        st.write(f"- {item}")

                st.markdown("**更新后的立场**")
                st.write(review.updated_position)

    if case.council_decision:
        st.divider()
        st.subheader("经理整合结果" if case.planning_mode == "style_branch" else "第三轮：委员会协调结果")
        st.write(case.council_decision.summary)
        for item in case.council_decision.merged_ideas:
            st.success(item)
        for item in case.council_decision.rejected_ideas:
            st.caption(f"未采用：{item}")

    if case.reviews:
        st.divider()
        st.subheader("检查记录与程序规则")
        for review in case.reviews:
            with st.expander(
                f"{review.reviewer_name} · {'通过' if review.passed else '要求修订'}"
            ):
                if not review.findings:
                    st.success("没有发现阻塞性问题")
                for finding in review.findings:
                    st.write(f"- [{finding.severity}] {finding.description}")
                    if finding.evidence:
                        st.caption(f"依据：{finding.evidence}")
                    if finding.release_condition:
                        st.caption(f"解除条件：{finding.release_condition}")
                    if finding.decision_owner:
                        st.caption(f"需要决定：{finding.decision_owner}")


def render_owner_approval(case):
    st.subheader("老板审批中心")
    if case is None:
        st.info("目前没有案件。")
        return
    if case.status != CaseStatus.WAITING_OWNER or case.approval is None:
        st.info("普通创意分歧由 Agent 内部解决，当前没有需要老板审批的事项。")
        return

    st.warning(case.approval.title)
    if case.customer_answer_pending or any(
        finding.decision_owner == "customer"
        for review in case.reviews for finding in review.findings
    ):
        st.info("批准表示允许继续向客户确认；关键偏好仍由客户本人决定。")
    for reason in case.approval.reasons:
        st.write(f"- {reason}")
    comment = st.text_area("老板意见")
    left, right = st.columns(2)
    approved = left.button("批准", type="primary", use_container_width=True)
    rejected = right.button("拒绝", use_container_width=True)
    if not (approved or rejected):
        return
    try:
        workflow = get_workflow()
    except AgentConfigurationError as exc:
        st.error(str(exc))
        return
    try:
        case = run_async(workflow.submit_owner_decision(case, approved, comment))
        save_case(case)
        st.rerun()
    except (WorkflowError, AgentOutputError) as exc:
        save_case(case)
        st.error(str(exc))


def render_owner_audit(case):
    st.subheader("完整审计记录")
    if case is None:
        st.info("还没有协作记录。")
        return
    for event in reversed(case.audit_log):
        st.markdown(
            f"**{event.timestamp:%Y-%m-%d %H:%M:%S} · {event.actor}**  \n"
            f"{event.action}：{event.detail}"
        )
        st.divider()


def render_company_policy(policy, organization):
    st.subheader("公司治理制度")
    st.write("这些规则由程序执行，Agent 不能通过提示词绕过。")
    st.json(
        {
            "客户最多补充轮次": policy.max_clarification_rounds,
            "Agent内部最多修订次数": policy.max_revision_count,
            "不可自动修复的高风险": "必须由老板审批"
            if policy.high_risk_requires_owner
            else "允许自动通过",
            "反馈改进": "按版本保存证据；相似条件选入经验和检查项，不自动认定责任或发薪",
        }
    )

    st.markdown("#### 客户投诉形成的长期约束")
    active_constraints = [
        item for item in organization.constraints if item.active
    ]
    if not active_constraints:
        st.info("目前还没有经过认定的长期约束。")
    for constraint in active_constraints:
        with st.container(border=True):
            st.write(f"适用对象：{constraint.agent_name or '全体员工'}")
            st.write(constraint.rule)
            st.caption(
                f"来源案件：{constraint.source_case_id} · 已生效{constraint.times_applied}次"
            )
            st.caption(f"形成依据：{constraint.source_summary}")


@st.fragment(run_every="1s")
def render_live_tasks(case):
    if not case:
        return
    if get_case() is not case:
        st.rerun()
    jobs = active_jobs(case.id)
    if jobs:
        rows = []
        for job in jobs:
            updates = progress_events(job)
            rows.append({"任务类型": {"planning": "需求与策划", "booking": "资源预订", "journey": "活动响应"}.get(job.key[0], job.key[0]),
                "状态": "执行中", "已运行秒": int(time.monotonic() - job.started),
                "最新进展": updates[-1]['message'] if updates else "任务已登记，正在启动"})
        st.dataframe(rows, hide_index=True, use_container_width=True)
    else:
        st.caption("服务当前没有正在执行的后台任务；已完成、失败及中断记录见下方案件与协作记录。")


def render_owner_console(*, show_case_picker=True, linked_supplier_id=None):
    st.header("案件工作台" if show_case_picker else "订单详情")
    case = get_case()
    if not show_case_picker and case:
        st.button("在客户页面继续此案件", on_click=resume_for_customer, args=(case.id,), key="resume_customer_case")
    history=list_saved_cases() if show_case_picker else []
    ids=[r["id"] for r in history]
    labels={r["id"]:f"{r['title'][:35]} · {'方案已确认' if r['status']=='completed' else OWNER_STATUS_LABELS.get(r['status'],r['status'])} · {r['id']}" for r in history}
    if show_case_picker and case and case.id not in ids:
        ids.insert(0,case.id)
        labels[case.id]=case.customer_request[:35]+" · "+case.id
    if ids:
        def select_history():
            chosen=st.session_state.get("owner_history_select")
            bind_case(chosen,True)
            st.session_state.pop("case",None)
        if st.session_state.get("owner_history_select") not in ids:
            st.session_state.pop("owner_history_select",None)
        st.selectbox("历史案件与未完成进度",[None,*ids],index=ids.index(case.id)+1 if case else 0,
            format_func=lambda key:labels[key] if key else "请选择要查看的已保存案件",
            key="owner_history_select",on_change=select_history)
        if case:
            st.button("在客户页面继续此案件",on_click=resume_for_customer,args=(case.id,),key="resume_customer_case")
            st.caption("恢复已保存的需求、方案、审批、预订与活动步骤；不会重新执行已完成的模型步骤或重复收款。")
    if case is None:
        if show_case_picker:
            st.info("案件进度会自动保存。请选择一个案件查看，或到「历史订单管理」浏览完整档案。" if ids else "已保存的案件会出现在这里。")
        else:
            st.info("该案件当前不可用，请返回驾驶舱查看最新记录。")
        return
    render_live_tasks(case)
    if case and case.service_mode == "document_intake":
        st.subheader("文档需求整理")
        st.info("等待客户补齐必要信息并确认用途，不会自动生成旅行方案或创建订单。")
        if case.document_intake:
            st.write(case.document_intake.summary)
            st.json(case.requirements.model_dump(), expanded=False)
        if case.error_message:
            st.error(case.error_message)
        with st.expander("任务与审计记录"):
            render_owner_audit(case)
        if st.button("删除当前案件", type="secondary"):
            try:
                delete_case_from_disk(case)
                st.session_state.pop("case", None)
                st.rerun()
            except CaseConflictError as exc:
                st.error(str(exc))
        return
    if case and case.service_mode == "activity_review":
        render_activity_case(None, case, process_case, save_case, owner=True, sync=sync_current_case,
                             tracking_enabled=activity_order.TRACKING_ENABLED)
        with st.expander("审核与细化的任务交接"):
            st.dataframe([{"执行方": r.receiver, "任务": r.task, "状态": r.status,
                "耗时秒": r.elapsed_seconds, "结果": r.result_summary, "错误": r.error}
                for r in handoffs_for(case)], hide_index=True, use_container_width=True)
        with st.expander("审计记录"):
            render_owner_audit(case)
        st.download_button("保存本案记录", case.model_dump_json(indent=2),
            file_name=f"{case.id}.json", mime="application/json")
        if st.button("删除当前案件", type="secondary"):
            try:
                delete_case_from_disk(case)
                st.session_state.pop("case", None)
                st.rerun()
            except CaseConflictError as exc:
                st.error(str(exc))
        return
    overview, council, feedback, audit = st.tabs(
        ["案件与审批", "协作与分歧", "反馈与改进", "审计与规则"]
    )
    with overview:
        render_owner_overview(case)
        change_requests = [event.detail for event in case.audit_log if event.action == "booking_change_requested"]
        if change_requests:
            with st.expander("客户补充的订单变更请求", expanded=True):
                for message in change_requests:
                    st.write(message)
        render_owner_approval(case)
        supplier_id = case.supplier_order_id or linked_supplier_id
        if supplier_id:
            render_owner_supplier_order(supplier_id)
        else:
            render_procurement(case, save_case, owner=True, sync=sync_current_case)
        if activity_order.TRACKING_ENABLED:
            render_journey_owner(case, save_case)
    with council:
        if case:
            st.caption(f"订单全程状态：{lifecycle(case)}")
            records = handoffs_for(case)
            with st.expander(f"三个核心 Agent 的任务交接记录 · {len(records)}条", expanded=True):
                st.dataframe([{"发起方": r.sender, "执行方": r.receiver, "任务": r.task,
                    "状态": {"completed": "完成", "failed": "失败", "running": "处理中"}.get(r.status, r.status),
                    "耗时秒": r.elapsed_seconds, "输入依据": r.input_summary,
                    "预期输出": r.expected_result,
                    "结果": r.result_summary, "错误": r.error, "关联订单": r.case_id,
                    "来源": {"agent": "模型结果", "rule": "程序计算", "rule_fallback": "失败后规则建议",
                             "validated_cache": "已校验结果复用"}.get(r.source, r.source)}
                    for r in records[-30:]], hide_index=True, use_container_width=True)
        render_owner_council(case)
        evidence = adoption_evidence(case) if case else []
        if evidence:
            with st.expander("方案采纳依据"):
                st.dataframe(evidence, hide_index=True, width="stretch")
    with feedback:
        render_feedback_records(case)
        render_case_references(case)
    with audit:
        with st.expander("审计记录"):
            render_owner_audit(case)
        with st.expander("审批规则与反馈约束"):
            render_dashboard_policy()


def open_dashboard_order(order):
    st.session_state["owner_dashboard_order"] = (
        order["kind"], order["source_id"], order.get("supplier_order_id")
    )
    st.session_state["owner_dashboard_return_page"] = st.session_state.get("owner_dashboard_page", "经营总览")
    if order["kind"] == "case":
        bind_case(order["source_id"], owner=True)
        st.session_state.pop("case", None)


def return_to_dashboard():
    st.session_state.pop("owner_dashboard_order", None)
    st.session_state["owner_dashboard_page"] = st.session_state.pop("owner_dashboard_return_page", "经营总览")


def render_dashboard_policy():
    try:
        organization = load_organization_state()
    except (OSError, ValueError):
        st.error("公司制度记录暂时无法读取，请检查本地文件。")
        return
    render_company_policy(CompanyPolicy(), organization)


def render_owner_supplier_order(order_id):
    try:
        render_supplier_workspace(owner=True, order_id=order_id)
    except (OSError, ValueError, sqlite3.Error):
        st.error("预订记录暂时无法读取，请返回驾驶舱查看最新记录。")


def render_owner_dashboard_workspace():
    selected = st.session_state.get("owner_dashboard_order")
    if selected:
        st.button("返回经营驾驶舱", on_click=return_to_dashboard, key="owner_dashboard_return")
        kind, source_id, linked_supplier_id = selected
        if kind == "supplier":
            render_owner_supplier_order(source_id)
        else:
            render_owner_console(show_case_picker=False, linked_supplier_id=linked_supplier_id)
        return
    try:
        snapshot = load_owner_dashboard()
    except (OSError, ValueError, sqlite3.Error):
        st.error("经营记录暂时无法读取，请检查本地订单档案和模拟账本。")
        st.button("重新读取", key="owner_dashboard_retry")
        return
    render_owner_dashboard(snapshot, open_order=open_dashboard_order, render_policy=render_dashboard_policy)


def switch_identity():
    st.session_state.pop("owner_dashboard_order", None)
    st.session_state.pop("owner_dashboard_return_page", None)
    if st.session_state["identity"] != "公司老板":
        st.session_state["work_area"] = st.session_state.get("customer_work_area", TRAVEL_AREA)
        return
    area = st.session_state.get("work_area")
    if area in (TRAVEL_AREA, SUPPLIER_AREA):
        remember_view(customer_work_area=area)
    st.session_state["work_area"] = OWNER_DASHBOARD_AREA
    st.session_state["owner_dashboard_page"] = "经营总览"
    previous = st.session_state.get("owner_case_id")
    case = st.session_state.get("case")
    if case is not None and getattr(case, "id", None) == previous:
        st.session_state.pop("case", None)
    bind_case(None, owner=True)
    st.session_state.pop("owner_history_select", None)


def switch_customer_area(area):
    st.session_state["work_area"] = area
    remember_view(work_area=area)


def render_customer_entry(area):
    case = get_case()
    booking = area == SUPPLIER_AREA
    request_type = "团建策划"
    if case is None:
        active_booking = booking and (st.session_state.get("customer_supplier_order_id")
                                      or st.session_state.get("supplier_selected"))
        if not active_booking:
            st.header("安排一次团建")
            draft_key = "supplier_wizard" if booking else "trip_wizard"
            if st.session_state.get(draft_key, {}).get("step", 1) == 1:
                request_type = st.radio("需求类型", ["团建策划", "酒店餐饮预订", "上传已有资料"],
                                        index=1 if booking else 0, horizontal=True, key="customer_request_type")
                booking = request_type == "酒店餐饮预订"
                selected_area = SUPPLIER_AREA if booking else TRAVEL_AREA
                if selected_area != area:
                    switch_customer_area(selected_area)
    if booking:
        if case is not None:
            st.button("返回团建需求", on_click=switch_customer_area, args=(TRAVEL_AREA,))
        render_supplier_workspace(case=case, attach=save_case)
        return
    try:
        workflow = get_workflow()
    except AgentConfigurationError as exc:
        st.error(str(exc))
        return
    render_customer_portal(workflow, case, request_type)


initialize_visit()
st.sidebar.title("身份切换")
role = st.sidebar.radio("当前身份", ["客户", "公司老板"], key="identity", on_change=switch_identity)
work_areas = [TRAVEL_AREA, SUPPLIER_AREA, HISTORY_AREA]
if role == "公司老板":
    work_areas.insert(0, OWNER_DASHBOARD_AREA)
if st.session_state.get("work_area") not in work_areas:
    st.session_state["work_area"] = work_areas[0]
if role == "公司老板":
    business_area = st.sidebar.radio(
        "工作区", work_areas, key="work_area", on_change=return_to_dashboard
    )
else:
    # Retain the selection when Streamlit removes the sidebar widget.
    selected_area = st.session_state.get("work_area", TRAVEL_AREA)
    st.session_state["work_area"] = selected_area
    business_area = TRAVEL_AREA if selected_area == HISTORY_AREA else selected_area
remember_view(identity=role,work_area=st.session_state["work_area"])
st.sidebar.caption("演示模式：当前未启用账号登录，仅用于展示角色与权限边界。")

if business_area != OWNER_DASHBOARD_AREA:
    st.title("🧭 OPC 团建旅行社")
    st.caption("演示环境 · 供应商、预订与付款使用模拟数据。")
if "sync_notice" in st.session_state:
    st.info(st.session_state.pop("sync_notice"))

if role == "客户":
    render_customer_entry(business_area)
elif business_area == OWNER_DASHBOARD_AREA:
    render_owner_dashboard_workspace()
elif business_area == HISTORY_AREA:
    render_history_workspace(owner=True)
elif business_area == SUPPLIER_AREA:
    render_supplier_workspace(owner=True, case=get_case(), attach=save_case)
else:
    render_owner_console()

