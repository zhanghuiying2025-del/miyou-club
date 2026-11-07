"""Streamlit fragment keeps the mini-simulation moving without blocking the page."""
import asyncio
import time

import streamlit as st
from src.agents import build_agent_team

from src.journey import (start_demo, replay_demo, tick_demo, current_index, pending_event,
                         recommend, coordinate_response, report_event, apply_response, confirm_event, TOTAL_TICKS)
from src.journey import fallback_response
from src.visuals import journey_frame
from src.runtime import submit_once, consume, progress_events
from src.presentation import short_text
from src.procurement import confirm_activity_cost


def request_response(snapshot, captured_event):
    async def execute():
        try:
            # Each worker owns its client/loop; reusing a client across asyncio.run
            # calls can otherwise produce 'Event loop is closed' on the second alert.
            fresh_team = build_agent_team()
            return await coordinate_response(fresh_team, snapshot, captured_event)
        except Exception as exc:
            response = fallback_response(snapshot, captured_event)
            response.requires_owner = True
            return response, "rule_fallback", str(exc)[:300]
    return asyncio.run(execute())


@st.fragment(run_every="1s")
def render_journey_demo(case, team, save, sync=None):
    if sync is not None and sync() is not case:
        st.rerun()
    st.subheader("活动进度")
    demo = case.journey_demo
    if demo is None:
        st.caption("按已确认的行程跟进活动，遇到现场变化时可确认调整。")
        label = "开始活动" if case.procurement_order or case.supplier_order_id else "确认订单并开始活动"
        if st.button(label, type="primary", key=f"journey_start_{case.id}"):
            start_demo(case)
            save(case)
            st.rerun()
        return

    if tick_demo(demo):
        save(case)
    event = pending_event(demo)
    job = None
    if demo.status == "awaiting_agent" and event and not demo.runtime_error:
        key = ("journey", case.id, demo.order_id, event.id, demo.task_attempt)
        job = submit_once(key, case.id, request_response, demo.model_copy(deep=True), event.model_copy(deep=True))
    if job and job.future.done() and not demo.runtime_error:
        event_id = job.key[3]
        def commit(result):
            response, source, error = result
            if demo.status == "awaiting_agent" and event and event.id == event_id:
                apply_response(demo, event_id, response, source, error)
                save(case)
        try:
            consume(job, commit)
            st.rerun()
        except Exception as exc:
            demo.runtime_error = str(exc)[:300]
            save(case)
    if demo.runtime_error:
        st.warning("活动响应未完成，行程保持暂停。可以重试本次变化，已确认安排不会丢失。")
        if st.button("重试本次活动响应", key=f"journey_retry_{demo.order_id}"):
            demo.runtime_error = ""
            demo.task_attempt += 1
            save(case)
            st.rerun()

    index = current_index(demo)
    rain = any(x.kind == "weather" for x in demo.events)
    member = any(x.kind == "member" for x in demo.events)
    st.image(journey_frame(demo.active_schedule[index], index, len(demo.active_schedule), demo.tick, rain, member), use_container_width=True)
    st.progress(demo.tick / TOTAL_TICKS, text=f"行程进度 {demo.tick / TOTAL_TICKS:.0%}")

    if demo.status == "running":
        st.info(f"组织者正在带队体验：{demo.active_schedule[index]}")
        if st.button("暂停活动进度", key=f"journey_pause_{demo.order_id}"):
            demo.status = "paused"
            save(case)
            st.rerun()
    elif demo.status == "paused":
        if st.button("继续活动", key=f"journey_continue_{demo.order_id}"):
            start_demo(case)
            save(case)
            st.rerun()
    if demo.status in {"running", "paused"}:
        with st.expander("现场负责人上报变化"):
            choice = st.selectbox("发生了什么", ["当前环节延迟15分钟", "有成员希望休息", "现场下雨"], key=f"report_kind_{demo.order_id}")
            if st.button("提交现场变化", key=f"report_event_{demo.order_id}"):
                report_event(demo, {"当前环节延迟15分钟": "delay", "有成员希望休息": "member", "现场下雨": "weather"}[choice])
                save(case)
                st.rerun()
    elif demo.status == "awaiting_agent" and not demo.runtime_error:
        st.warning(event.description)
        st.status("履约诊断 → 策划局部调整 → 经理核查……", state="running", expanded=False)
        st.caption(f"已等待{int(time.monotonic() - job.started)}秒；每步最多8秒，失败转规则建议并交老板核查")
        for item in progress_events(job)[-3:]:
            st.write(f"{item['actor']}：{item['message']}")
    elif demo.status == "awaiting_organizer":
        st.warning("组织者：请确认是否接受以下演示调整，行程已暂停。")
        response = event.response
        st.write(response.summary)
        for adjustment in response.adjustments:
            st.write(f"- 第{adjustment.schedule_index + 1}项：{adjustment.replacement}")
        st.caption(response.organizer_notice + " · " + response.cost_note)
        left, right = st.columns(2)
        if left.button("同意演示调整并继续", type="primary", key=f"journey_yes_{event.id}"):
            confirm_event(demo, True)
            save(case)
            st.rerun()
        if right.button("暂不接受，交公司处理", key=f"journey_no_{event.id}"):
            confirm_event(demo, False)
            save(case)
            st.rerun()
    elif demo.status == "awaiting_owner":
        st.warning("事件等待公司老板处理，行程保持暂停。请切换到老板端的案件与审批。")
    elif demo.status == "held":
        st.warning("公司未批准继续，当前模拟行程停止推进。")
    elif demo.status == "finished":
        st.success("模拟游玩结束：天气预警与人员状态变化均已留档，可对照原方案与客户反馈复盘。")

    if demo.events:
        st.markdown("**动态提醒与响应**")
        for item in list(reversed(demo.events))[:3]:
            with st.container(border=True):
                st.write(item.description)
                if item.response:
                    who = "项目经理汇总" if item.response_source == "agent" else "演示规则响应（非模型输出）"
                    st.write(f"{who}：{short_text(item.response.summary)}")
                    st.caption(item.response.organizer_notice)
                    st.caption("来源：" + ("现场负责人上报" if item.source == "organizer_report" else "固定模拟事件"))
                    st.caption({"resolved": "已记录 / 调整已获确认", "waiting_organizer": "等待组织者确认", "waiting_owner": "等待老板确认", "held": "暂停处理"}.get(item.status, "处理中"))
                else:
                    st.caption("提醒已展示，等待响应。")
    if demo.status in {"finished", "held"}:
        if st.button("再演示一次", key=f"journey_replay_{demo.order_id}"):
            replay_demo(case)
            save(case)
            st.rerun()


def render_journey_owner(case, save):
    if not case or not case.journey_demo:
        return
    demo = case.journey_demo
    with st.expander("模拟订单履约与异常", expanded=demo.status == "awaiting_owner"):
        st.write(f"{demo.order_id} · 进度{demo.tick}/{TOTAL_TICKS} · {demo.status}")
        if demo.runtime_error:
            st.error("活动后台任务异常，等待重试。")
            st.caption(demo.runtime_error)
        event = pending_event(demo)
        if demo.status == "awaiting_owner" and event and event.response:
            st.warning(event.description)
            st.write(event.response.summary)
            st.caption(event.response.cost_note)
            if st.button("批准建议，交组织者确认", key=f"journey_owner_yes_{event.id}"):
                confirm_event(demo, True, owner=True)
                save(case)
                st.rerun()
            if st.button("暂不批准，停止演示推进", key=f"journey_owner_no_{event.id}"):
                confirm_event(demo, False, owner=True)
                save(case)
                st.rerun()
        if case.procurement_order and not case.procurement_order.settled:
            for resolved in demo.events:
                if resolved.status == 'resolved' and resolved.response and (resolved.cost_confirmation_required or resolved.response.needs_change) and resolved.confirmed_cost_yuan is None:
                    st.warning("这次活动调整的费用尚未核对，结算前需要明确金额；确认无新增支出可填0。")
                    value = st.number_input("本次调整确认费用（元）", min_value=0.0,
                        value=float(resolved.response.additional_cost_yuan or 0), step=100.0, key=f"event_cost_{resolved.id}")
                    if st.button("确认费用并记录模拟支出", key=f"confirm_cost_{resolved.id}"):
                        try:
                            confirm_activity_cost(case, resolved.id, value)
                            save(case)
                            st.rerun()
                        except ValueError as exc:
                            st.error(str(exc))
        st.caption("所有金额与支付均为模拟；新增或未知费用需要核对后才能完单。")
