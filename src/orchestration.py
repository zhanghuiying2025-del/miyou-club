"""Auditable assignments; authorization stays in deterministic workflows."""
import asyncio
import time
from src.agents import run_structured
from src.models import TaskHandoff
from src.runtime import emit_progress


async def assign(agent, prompt, response_model, records, case_id, sender, receiver,
                 task, input_summary, timeout=60, runner=None, on_change=None, checkpoint=None):
    record = TaskHandoff(case_id=case_id, sender=sender, receiver=receiver,
        task=task, input_summary=input_summary[:200], expected_result=response_model.__name__)
    records.append(record)
    emit_progress(receiver, task)
    if on_change:
        on_change(checkpoint)
    started = time.monotonic()
    try:
        call = (runner or run_structured)(agent, prompt, response_model)
        result = await call if timeout is None else await asyncio.wait_for(call, timeout=timeout)
        if timeout is not None and time.monotonic() - started > timeout:
            raise TimeoutError("任务在截止时间后返回，结果不予应用")
        record.status = "completed"
        emit_progress(receiver, task, finished=True)
        record.result_summary = (getattr(result, "summary", None)
            or getattr(result, "message", None) or f"已返回{response_model.__name__}")[:200]
        return result
    except BaseException as exc:
        record.status = "failed"
        record.error = (str(exc) or "处理取消或超时")[:300]
        raise
    finally:
        record.elapsed_seconds = round(time.monotonic() - started, 2)
        if on_change:
            on_change(checkpoint)


def handoffs_for(case):
    records = list(case.handoffs)
    if case.procurement_order:
        records.extend(case.procurement_order.handoffs)
    if case.journey_demo:
        records.extend(case.journey_demo.handoffs)
    return sorted(records, key=lambda r: r.created_at)


def lifecycle(case):
    if case.service_mode == 'document_intake':
        return '文档需求待客户补齐与确认'
    if case.service_mode == "activity_review":
        if case.journey_demo:
            return {'ready': '活动筹备中', 'running': '活动执行中', 'finished': '活动已结束',
                'awaiting_agent': '活动暂停，团队处理中', 'awaiting_owner': '活动暂停，待老板',
                'awaiting_organizer': '活动暂停，待组织者'}.get(case.journey_demo.status, '活动暂停')
        return "活动方案审核已交付" if case.status.value == "completed" else "已有活动方案审核与执行细化"
    if case.procurement_order and case.procurement_order.settled:
        return "订单完成并结算"
    if case.journey_demo:
        return {"finished": "活动完成，待结算", "running": "活动执行中",
            "awaiting_owner": "活动暂停，待老板决定", "awaiting_organizer": "活动暂停，待客户确认"}.get(case.journey_demo.status, "活动执行与异常处理")
    if case.supplier_order_id:
        from src.supplier_simulation import load_order
        try:
            order,_=load_order(case.supplier_order_id)
        except ValueError:
            return "酒店餐饮预订记录待核实"
        return {"inquiring":"正在询价","waiting_customer":"待客户确认报价",
            "customer_confirmed":"待付预付款","paid":"待确认供方预订",
            "confirmed":"资源已确认，待交付","delivered":"待结清供方",
            "completed":"订单完成并结算"}.get(order.status,"酒店餐饮预订处理中")
    if case.procurement_order:
        return {"ready": "资源已确认，待活动开始", "waiting_owner": "预订待老板审批",
            "waiting_customer": "预订待客户确认", "held": "预订暂停"}.get(case.procurement_order.status, "资源预订中")
    return "方案已确认，待模拟下单" if case.status.value == "completed" else "需求与方案处理中"
