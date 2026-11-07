"""Customer/owner views for reviewing existing event plans."""
import streamlit as st

from src.activity_review import read_activity_documents
from src.models import AuditEvent, CaseStatus
from src.activity_order_ui import render_activity_order


def render_activity_request(workflow, process_case, save_case, existing_case=None):
    st.caption("上传已有方案，保留原主题，检查缺项并细化分工；无需填写旅行城市、风格或虚构人数。")
    with st.form(f"activity_review_request_{existing_case.id if existing_case else 'new'}"):
        files = st.file_uploader("上传已有活动方案", type=["docx", "pdf"],
            accept_multiple_files=True, help="最多3份，单份不超过12 MB。Word内嵌图片需补充文字。")
        notes = st.text_area("本次重点（选填）", placeholder="例如：保留原比赛规则，重点检查预算和现场人员分工。")
        submitted = st.form_submit_button("审核方案并细化执行安排", type="primary")
    if submitted:
        attachments = [(f.name, f.type or "application/octet-stream", f.getvalue()) for f in files]
        try:
            sources = read_activity_documents(attachments)
            case = existing_case.model_copy(deep=True) if existing_case else workflow.create_case("审核并细化已有活动方案")
            if case.procurement_order or case.journey_demo:
                raise ValueError("已有预订或执行记录，请新建审核案件")
            case.service_mode = "activity_review"
            case.activity_sources = sources
            case.activity_review_notes = notes.strip()
            save_case(case)
            process_case(workflow, case, "经理审核原方案，策划细化执行……", action="activity_review")
            st.rerun()
        except (ValueError, RuntimeError) as exc:
            st.error(str(exc))


def render_activity_case(workflow, case, process_case, save_case, owner=False, sync=None, *, tracking_enabled=False):
    if tracking_enabled and case.journey_demo and case.journey_demo.context_kind == 'activity':
        render_activity_order(case, save_case, sync, owner)
        with st.expander('查看接管前的审核清单'):
            st.write(case.activity_review.audit.summary)
            st.download_button('下载原审核与执行清单', report_markdown(case.activity_review),
                file_name='活动审核与执行清单.md', mime='text/markdown')
        return
    st.subheader("已有活动方案审核")
    st.caption(f"服务编号：{case.id}。审核与清单均为建议；不代表正式批准、已分派任务或活动已经执行。")
    if case.error_message:
        st.error(case.error_message)
    report = case.activity_review
    if report:
        if case.error_message:
            st.caption("以下保留上一次完整审核结果，本次失败没有覆盖历史清单。")
        st.markdown(f"### {report.audit.event_name}")
        st.write(report.audit.summary)
        if report.program_checks:
            st.markdown("#### 程序金额核对")
            for finding in report.program_checks:
                st.warning(f"{finding.issue}。{finding.suggestion}")
                st.caption("依据：" + finding.evidence)
        with st.expander("原文事实与出处"):
            for fact in report.audit.facts:
                st.write(f"**{fact.label}**：{fact.value}")
                st.caption("原文：" + fact.source_quote)
        st.markdown("#### 需要补齐或核实的安排")
        for finding in report.audit.findings:
            st.write(f"**{finding.issue}**")
            st.caption(f"依据：{finding.evidence}；建议：{finding.suggestion}")
        st.markdown("#### 建议执行清单")
        st.write(report.execution.summary)
        st.dataframe([{"阶段": t.phase, "任务": t.task, "建议负责岗位": t.suggested_owner,
            "时间或截止点": t.timing, "交付物": t.deliverable} for t in report.execution.tasks],
            hide_index=True, use_container_width=True)
        with st.expander("现场执行顺序"):
            for step in report.execution.run_sheet:
                st.write("- " + step)
        if report.execution.organizer_questions:
            st.markdown("#### 优先请组织者确认")
            for question in report.execution.organizer_questions:
                st.info(question)
        st.download_button("下载审核与执行清单", data=report_markdown(report),
            file_name="活动方案审核与执行清单.md", mime="text/markdown")
    else:
        st.info("当前尚无完整审核结果，可从保存的原文重试。")
    with st.expander("已读取的活动原文"):
        for source in case.activity_sources:
            st.write(source.file_name)
            for warning in source.warnings:
                st.warning(warning)
            st.text(source.text)
    if owner:
        if tracking_enabled:
            render_activity_order(case, save_case, sync, owner=True)
        return
    if case.journey_demo:
        return
    with st.form(f"activity_review_notes_{case.id}"):
        notes = st.text_area("回答待确认问题或补充修改要求", placeholder="例如：共12位选手，5位评委；300元用于证书制作。")
        retry = st.form_submit_button("补充后重新审核" if report else "重试审核")
    if retry:
        try:
            process_case(workflow, case, "依据原文和你的补充重新审核……", action="activity_review", comment=notes)
            st.rerun()
        except RuntimeError as exc:
            st.error(str(exc))
    if report and case.status != CaseStatus.COMPLETED and st.button("确认收到审核与执行清单"):
        case.status = CaseStatus.COMPLETED
        case.audit_log.append(AuditEvent(actor="客户", action="activity_review_received", detail="确认收到建议清单，不代表活动已完成或同意全部建议"))
        save_case(case)
        st.rerun()
    if tracking_enabled and report:
        render_activity_order(case, save_case, sync)


def report_markdown(report):
    lines = [f"# {report.audit.event_name}", report.audit.summary, "## 原文事实"]
    lines += [f"- {f.label}：{f.value}（原文：{f.source_quote}）" for f in report.audit.facts]
    lines.append("## 程序核对与待确认事项")
    lines += [f"- {f.issue}；依据：{f.evidence}；建议：{f.suggestion}" for f in [*report.program_checks, *report.audit.findings]]
    lines.append("## 建议执行清单 尚未派发")
    lines += [f"- {t.phase}｜{t.task}｜建议岗位：{t.suggested_owner}｜{t.timing}｜交付物：{t.deliverable}" for t in report.execution.tasks]
    lines.append("## 现场顺序")
    lines += [f"- {s}" for s in report.execution.run_sheet]
    lines.append("## 请组织者确认")
    lines += [f"- {q}" for q in report.execution.organizer_questions]
    return "\n\n".join(lines)
