"""A manual execution board for existing events, with shared incident handling."""
import time
import streamlit as st

from src.activity_order import create_activity_order, start_activity_order, advance_activity_order, report_activity_incident
from src.document_intake import missing_fields, update_document_fields
from src.models import RequirementSpec
from src.journey import current_index, pending_event, apply_response, confirm_event
from src.journey_ui import request_response
from src.runtime import submit_once, consume, progress_events


@st.fragment(run_every='1s')
def render_activity_order(case, save, sync=None, owner=False):
    if sync is not None and sync() is not case:
        st.rerun()
    st.subheader('活动跟进')
    st.caption('组织者可以推进活动环节，也可以上报现场变化。')
    demo = case.journey_demo
    if demo is None:
        if owner:
            st.caption('等待客户确认执行信息并创建管理订单。')
            return
        if case.status.value != 'completed':
            st.caption('先确认收到审核与执行清单，再选择要纳入在线管理的环节。')
            return
        if case.requirements is None:
            case.requirements = RequirementSpec()
        missing = missing_fields(case, 'activity')
        with st.form(f'activity_order_create_{case.id}'):
            values = {}
            if 'event_date' in missing:
                values['event_date'] = st.date_input('执行日期', value=None, format='YYYY-MM-DD')
            if 'venue' in missing:
                values['venue'] = st.text_input('执行场地')
            if 'participant_count' in missing:
                values['participant_count'] = st.number_input('参与总人数', min_value=1, max_value=100000, value=None, step=1)
            req = case.requirements
            st.caption(f'已知信息：{req.event_date or "日期待确认"} ｜ {case.document_venue or "场地待确认"} ｜ {req.participant_count or "人数待确认"}人')
            available = case.activity_review.execution.run_sheet
            selected = st.multiselect('确认纳入管理的执行环节（保持原文顺序）', available, default=available,
                help='建议环节也需你决定是否采用；未确认的设备、人员和规则不能视为已落实。')
            consent = st.checkbox('我已核对并确认以上执行信息与选中的环节')
            submitted = st.form_submit_button('确认执行信息并创建管理订单', type='primary')
        if submitted:
            try:
                if not consent:
                    raise ValueError('请先核对并确认执行信息')
                update_document_fields(case, values)
                create_activity_order(case, selected)
                save(case)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
        return
    if demo.context_kind != 'activity':
        st.warning('当前记录不属于活动管理订单，请检查服务类型。')
        return
    index = current_index(demo)
    st.write(f'管理订单：`{demo.order_id}` ｜ 第{index + 1}/{len(demo.active_schedule)}个环节')
    st.info('当前环节：' + demo.active_schedule[index])
    st.caption('状态：' + {'ready': '筹备中', 'running': '活动进行中', 'awaiting_agent': '团队处理中',
        'awaiting_owner': '暂停，等待老板', 'awaiting_organizer': '暂停，等待组织者',
        'held': '暂停，尚未批准', 'finished': '活动已结束'}.get(demo.status, demo.status))
    event = pending_event(demo)
    job = None
    if demo.status == 'awaiting_agent' and event and not demo.runtime_error:
        key = ('journey', case.id, demo.order_id, event.id, demo.task_attempt)
        job = submit_once(key, case.id, request_response, demo.model_copy(deep=True), event.model_copy(deep=True))
        if job.future.done():
            def commit(result):
                response, source, error = result
                if demo.status == 'awaiting_agent' and pending_event(demo) and pending_event(demo).id == event.id:
                    apply_response(demo, event.id, response, source, error)
                    save(case)
            try:
                consume(job, commit)
                st.rerun()
            except Exception as exc:
                demo.runtime_error = str(exc)[:300]
                save(case)
    if demo.runtime_error:
        st.error('事件处理未完成，活动保持暂停。')
        if st.button('重试活动事件处理', key=f'activity_retry_{demo.order_id}'):
            demo.runtime_error = ''
            demo.task_attempt += 1
            save(case)
            st.rerun()
    elif demo.status == 'awaiting_agent' and job:
        st.status('履约核查 → 必要时策划调整 → 项目经理审核', state='running')
        st.caption(f'已等待{int(time.monotonic() - job.started)}秒；每步最多20秒，失败明确转为规则建议并暂停等人工核查。')
        for update in progress_events(job)[-3:]:
            st.write(f'{update["actor"]}：{update["message"]}')
    if event and event.response:
        st.warning(event.description)
        st.write(event.response.summary)
        for change in event.response.adjustments:
            st.write(f'建议替换第{change.schedule_index + 1}项：{change.replacement}')
        st.caption(event.response.organizer_notice + '；' + event.response.cost_note)
        st.caption('来源：' + ('Agent协同建议' if event.response_source == 'agent' else '规则建议，非模型输出'))
        if owner and demo.status == 'awaiting_owner':
            if st.button('批准建议，交组织者确认', key=f'activity_owner_yes_{event.id}'):
                confirm_event(demo, True, owner=True)
                save(case)
                st.rerun()
            if st.button('暂不批准，保持暂停', key=f'activity_owner_no_{event.id}'):
                confirm_event(demo, False, owner=True)
                save(case)
                st.rerun()
        elif not owner and demo.status == 'awaiting_organizer':
            if st.button('确认本次处理并继续', key=f'activity_customer_yes_{event.id}'):
                confirm_event(demo, True)
                save(case)
                st.rerun()
            if st.button('暂不接受，交公司处理', key=f'activity_customer_no_{event.id}'):
                confirm_event(demo, False)
                save(case)
                st.rerun()
        elif not owner and demo.status == 'awaiting_owner':
            st.caption('涉及费用、重大责任或检查失败，等待公司老板处理。')
    if not owner and demo.status == 'ready':
        if st.button('开始活动', type='primary', key=f'activity_start_{demo.order_id}'):
            start_activity_order(case)
            save(case)
            st.rerun()
    if not owner and demo.status == 'running':
        label = '标记本次活动结束' if index + 1 == len(demo.active_schedule) else '完成当前环节，进入下一环节'
        if st.button(label, key=f'activity_next_{demo.order_id}'):
            advance_activity_order(case)
            save(case)
            st.rerun()
        with st.form(f'activity_incident_{demo.order_id}'):
            options = {'投影或音响故障': 'equipment', '环节延迟15分钟': 'delay',
                '关键人员缺席': 'absence', '成员需要休息': 'member', '其他突发情况': 'other'}
            choice = st.selectbox('现场发生了什么', list(options))
            details = st.text_area('现场补充', placeholder='例如：投影无法显示；备用电脑尚未测试；当前仍在开场环节。')
            report = st.form_submit_button('上报现场变化')
        if report:
            try:
                report_activity_incident(case, options[choice], details)
                save(case)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
        with st.expander('演示事件'):
            if st.button('演示一次投影故障', key=f'activity_mock_{demo.order_id}'):
                report_activity_incident(case, 'equipment', '演示：投影无法使用，备用设备状态未确认。', simulated=True)
                save(case)
                st.rerun()
    if demo.status == 'finished':
        st.success('本次活动已由组织者标记结束，执行与事件记录已保留；这不代表任何费用已支付或结算。')
    if owner and demo.status == 'held':
        if st.button('重新核查暂停事件', key=f'activity_reopen_{demo.order_id}'):
            previous = demo.events[-1]
            demo.status = 'paused'
            report_activity_incident(case, previous.kind, previous.description,
                simulated=previous.source == 'simulated_event')
            save(case)
            st.rerun()
    with st.expander('执行顺序与事件记录'):
        for i, step in enumerate(demo.active_schedule):
            st.write(f'{i + 1}. {step}')
        for item in reversed(demo.events):
            st.write(item.description)
            st.caption(f'{item.status} ｜ ' + ('演示事件' if item.source == 'simulated_event' else '现场负责人上报'))
            if item.response:
                st.write(item.response.summary)
