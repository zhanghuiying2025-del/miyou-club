"""Manual activity orders share incident approvals, not travel booking or payments."""
from src.document_intake import missing_fields
from src.journey import current_index, pending_event, report_event
from src.models import JourneyDemo, AuditEvent, CaseStatus


TRACKING_ENABLED = False


def create_activity_order(case, schedule):
    if case.journey_demo:
        raise ValueError('本案已有执行订单，不能重复创建')
    if not case.activity_review or case.status != CaseStatus.COMPLETED:
        raise ValueError('请先确认收到审核清单，再核对执行信息并创建管理订单')
    if missing_fields(case, 'activity'):
        raise ValueError('请先确认活动日期、场地及参与总人数')
    allowed = case.activity_review.execution.run_sheet
    if not schedule or any(not s.strip() or s not in allowed for s in schedule) or len(set(schedule)) != len(schedule):
        raise ValueError('请从审核后的现场顺序选择至少一个有效环节')
    # Preserve source order even if the browser submits selected entries in another order.
    selected = [s for s in allowed if s in schedule]
    context = ('\n\n'.join(s.text for s in case.activity_sources)[:24000] +
        '\n组织者确认信息：' + case.requirements.model_dump_json() +
        '\n执行场地：' + case.document_venue + '\n补充：' + case.activity_review_notes[-6000:])
    demo = JourneyDemo(context_kind='activity', manual_index=0, plan_version=case.plan_version,
        original_schedule=selected, active_schedule=list(selected), activity_context=context)
    case.journey_demo = demo
    case.audit_log.append(AuditEvent(actor='客户', action='activity_order_created',
        detail=f'确认执行顺序，创建管理订单{demo.order_id}；没有创建旅行预订或记录支付'))
    return demo


def start_activity_order(case):
    demo = case.journey_demo
    if not demo or demo.context_kind != 'activity' or demo.status not in {'ready', 'paused'}:
        raise ValueError('当前没有可开始或恢复的活动订单')
    if pending_event(demo):
        raise ValueError('请先处理当前事件')
    demo.status = 'running'
    return demo


def advance_activity_order(case):
    demo = case.journey_demo
    if not demo or demo.context_kind != 'activity' or demo.status != 'running' or pending_event(demo):
        raise ValueError('活动暂停或有未处理事件，不能推进')
    index = current_index(demo)
    if index + 1 == len(demo.active_schedule):
        demo.status = 'finished'
        demo.tick = 12
    else:
        demo.manual_index = index + 1
    return demo


def report_activity_incident(case, kind, details='', simulated=False):
    demo = case.journey_demo
    if not demo or demo.context_kind != 'activity':
        raise ValueError('尚未创建活动管理订单')
    if kind == 'other' and not details.strip():
        raise ValueError('请描述具体发生了什么，不能仅提交“其他突发情况”')
    event = report_event(demo, kind, details)
    if simulated:
        event.source = 'simulated_event'
    case.audit_log.append(AuditEvent(actor='组织者', action='activity_incident', detail=event.description))
    return event
