"""Owner-facing customer feedback and learning history."""
import streamlit as st

from src.performance import company_lessons, set_lesson_active


def _scope_text(scope):
    values = [scope.city, scope.preferred_style, scope.team_profile, scope.duration,
              *scope.hard_constraints, *scope.special_needs]
    if scope.participant_count is not None:
        values.append(f"来源人数{scope.participant_count}人")
    if scope.budget_yuan is not None:
        values.append(f"来源预算{scope.budget_yuan:g}元；本单金额需重算")
    return "；".join(x for x in values if x) or "来源条件不足，暂不跨单自动引用"


def render_feedback_records(case):
    st.subheader("反馈与改进")
    st.caption("保留客户原话、方案版本和改进依据。经验用于后续策划，检查项需要结合新订单核对。")
    lessons = company_lessons()
    with st.expander("学习更新记录", expanded=True):
        if lessons:
            st.dataframe([{
                "更新时间": item.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                "学习情景": f"已学习情景：{item.title}",
                "来源订单": item.source_case_id, "方案版本": item.source_version,
                "阶段": item.source_stage, "状态": "启用" if item.active else "停用",
            } for item in reversed(lessons)], hide_index=True, use_container_width=True)
        else:
            st.caption("还没有已保存的学习情景。")
    if case is None:
        st.info("创建订单后，可查看反馈与后续改进。")
        return

    st.markdown("#### 本案参考了什么、具体怎样处理")
    if not case.learning_applications:
        st.info("尚无匹配的历史经验，或策划尚未开始；不会补造改进记录。")
    responses = {x.lesson_id: x for x in case.council_decision.learning_responses} if case.council_decision else {}
    active = {x.id for x in lessons if x.active}
    labels = {"adopted": "经理说明已采用", "adapted": "经理说明已调整后采用", "not_applicable": "本案不适用"}
    for item in case.learning_applications:
        lesson = item.lesson
        with st.container(border=True):
            st.write(f"**{'经验候选' if lesson.kind == 'success' else '问题检查项'}：{lesson.title}**")
            st.caption(f"来源：{lesson.source_case_id} · 第{lesson.source_version}版 · {lesson.source_stage}")
            st.caption(f"匹配依据：{item.match_reason}")
            st.write(lesson.action)
            st.caption("已提供给：" + ("、".join(item.supplied_to) or "尚未交给模型"))
            response = responses.get(lesson.id)
            if lesson.id not in active:
                st.warning("此记录已停用，后续调用不再提供；此前的引用保留作历史。")
            if response:
                st.write(f"**{labels[response.decision]}**：{response.detail}")
                if response.schedule_index is not None and case.council_decision and response.schedule_index < len(case.council_decision.schedule):
                    st.write(f"对应本版行程：{case.council_decision.schedule[response.schedule_index]}")
                st.caption("采纳说明来自经理；程序仅校验记录引用和行程下标，不等同于已成功执行。")
            else:
                st.caption("目前只有提供参考的记录，尚无最终方案的具体处理说明。")

    st.markdown("#### 跨订单经验与检查项")
    if not lessons:
        st.info("尚无已保存的满意方案模板或具体反馈。")
    for lesson in reversed(lessons[-30:]):
        kind = "经验候选" if lesson.kind == "success" else "问题检查项"
        with st.expander(f"{kind} · {lesson.title} · {'启用' if lesson.active else '停用'}"):
            st.write(lesson.action)
            st.caption(f"来源：{lesson.source_case_id} · 第{lesson.source_version}版 · {lesson.source_stage}")
            st.write("适用条件：" + _scope_text(lesson.scope))
            for evidence in lesson.evidence:
                st.write(f"客户反馈或证据：{evidence}")
            if lesson.source_schedule:
                st.write("来源方案：" + "；".join(lesson.source_schedule))
            st.caption(f"已选入{lesson.supplied_cases}个后续订单的参考资料；不表示已采纳或完成")
            if st.button("停用此记录" if lesson.active else "重新启用此记录", key=f"lesson_active_{lesson.id}"):
                try:
                    set_lesson_active(lesson.id, not lesson.active)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.rerun()

    if case.feedback_assessment:
        st.markdown("#### 本案质疑与投诉依据")
        assessment = case.feedback_assessment
        st.write(assessment.rationale)
        for evidence in assessment.evidence:
            st.write(f"核查引文：{evidence}")
        st.caption("责任建议：" + ("、".join(assessment.responsible_agents) or "未确认"))
    if case.customer_objections:
        with st.expander("本案需求澄清记录"):
            for objection in case.customer_objections:
                st.write(f"{objection.agent_name}：{objection.message}")
                st.caption(f"依据：{'；'.join(objection.evidence)}；来源：{objection.source}")

    st.markdown("#### 方案版本与客户评价")
    labels = {"satisfied": "满意", "acceptable": "能接受", "unacceptable": "完全不能接受"}
    if not case.feedback_history and not case.outcome_feedback:
        st.info("客户尚未评价，老板不能代替客户填写。")
    for record in reversed(case.feedback_history):
        with st.expander(f"第{record.version}版 · {labels.get(record.feedback.outcome, record.feedback.outcome)}"):
            if record.plan:
                st.write(record.plan.summary)
                st.write("；".join(record.plan.schedule))
            for text in record.feedback.selected_labels:
                st.write(text)
            if record.feedback.comment:
                st.info(record.feedback.comment)
            for lesson in record.lessons:
                st.write(f"形成{'经验候选' if lesson.kind == 'success' else '检查项'}：{lesson.title}")
    if case.outcome_feedback and not case.feedback_history:
        for text in [case.outcome_feedback.comment, *case.outcome_feedback.incidents]:
            if text:
                st.write(text)
    if case.delivery_feedback:
        st.markdown("#### 履约后评价")
        st.write(f"总体满意度 {case.delivery_feedback.overall_rating}/5")
        for text in [*case.delivery_feedback.incidents, case.delivery_feedback.comment]:
            if text:
                st.write(text)
