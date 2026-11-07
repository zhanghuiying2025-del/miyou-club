"""Small prepayment/resource coordination panel, shared by client and owner views."""
import asyncio

import streamlit as st

from src.procurement import (GRADE_NAMES, start_procurement, advance_supplier, turn_context,
    request_booking_speech, financial_review, finish_turn, get_package, financials, owner_reasons,
    customer_reasons, owner_decide, customer_decide, resume_order, cash_summary, pay_suppliers, settle_order)
from src.orchestration import lifecycle
from src.runtime import submit_once, consume, progress_events
from src.presentation import short_text


def _request(order):
    return asyncio.run(request_booking_speech(order))


@st.fragment(run_every="1s")
def render_procurement(case, save, owner=False, sync=None, *, allow_manual_delivery=False):
    if sync is not None and sync() is not case:
        st.rerun()
    if case is None:
        return
    order = case.procurement_order
    if owner and order is None:
        return
    st.subheader("团建预订")
    if owner:
        st.caption(f"全程状态：{lifecycle(case)}")
    if order is None:
        budget = case.requirements.budget_yuan if case.requirements else None
        people = case.requirements.participant_count if case.requirements else None
        duration = case.requirements.duration if case.requirements else None
        default_nights = {"2天1晚": 1, "3天2晚": 2}.get(duration, 0)
        quote = float(budget or (people or 30) * 800)
        st.write(f"订单总价 ¥{quote:,.2f} · 预付款 ¥{quote*0.6:,.2f}")
        st.caption(f"包含场地和交通 · 默认{'住宿' + str(default_nights) + '晚' if default_nights else '当天往返'}")
        with st.form(f"procurement_pay_{case.id}"):
            with st.expander("调整住宿与接驳要求"):
                nights = st.number_input("住宿晚数（0 表示当天往返）", min_value=0, max_value=7, value=default_nights)
                grade = st.selectbox("住宿标准", list(GRADE_NAMES), index=1, format_func=lambda x: GRADE_NAMES[x])
                same_hotel = st.checkbox("全员同酒店入住", value=True)
                transfer = st.number_input("单程接驳上限（分钟）", min_value=20, max_value=180, value=90, step=10)
            submitted = st.form_submit_button("确认订单并支付预付款", type="primary")
        if submitted:
            try:
                start_procurement(case, quote, int(nights), grade, same_hotel, int(transfer))
                save(case)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
        return

    if advance_supplier(order):
        save(case)
        if order.status == "ready":
            st.rerun()
    job = None
    if order.status == "processing" and order.step == 5 and not order.runtime_error:
        finish_turn(order, 5, financial_review(order), "rule")
        save(case)
        st.rerun()
    if order.status == "processing" and order.step in {3, 4, 5, 6} and not order.runtime_error:
        key = ("booking", case.id, order.id, order.plan_version, order.step, order.task_attempt)
        job = submit_once(key, case.id, _request, order.model_copy(deep=True))
    if job and job.future.done() and not order.runtime_error:
        step = job.key[4]
        def commit(result):
            speech, source = result
            if order.status == "processing" and order.step == step:
                finish_turn(order, step, speech, source)
                save(case)
        try:
            consume(job, commit)
            st.rerun()
        except Exception as exc:
            order.runtime_error = str(exc)[:300]
            save(case)
    if order.runtime_error:
        st.warning("本步处理遇到问题，已保留预付款和供方结果。可重试当前步骤，无需重新下单。")
        if owner:
            st.caption(order.runtime_error)
        if st.button("重试当前预订步骤", key=f"booking_retry_{order.id}"):
            order.runtime_error = ""
            order.task_attempt += 1
            save(case)
            st.rerun()

    st.caption(f"订单 {order.id} · 已模拟收预付款{order.deposit_yuan:,.2f}元 · 合同价{order.quote_yuan:,.2f}元")
    st.dataframe([{"资源": {"venue": "场地", "vehicle": "车辆", "hotel": "住宿"}[x.kind],
        "供方（模拟）": x.supplier, "需求数量": x.requested_quantity, "可供数量": x.available_quantity,
        "状态": {"pending": "待确认", "confirmed": "已模拟确认", "partial_available": "房量不足，未完成预订", "not_required": "不需要"}.get(x.status, x.status)}
        for x in order.reservations], hide_index=True, use_container_width=True)

    if order.discussion:
        with st.expander("预订进展", expanded=order.status=="processing"):
            for item in (order.discussion if owner else order.discussion[-3:]):
                st.write(f"**{item.actor}** · {item.owner_message if owner else short_text(item.public_message)}")
                if owner:
                    source = {"agent": "模型业务意见＋工具计算", "rule_fallback": "演示规则响应，非模型输出", "supplier_fixture": "供应商固定模拟回执", "simulated_payment": "模拟支付记录", "owner": "老板操作", "customer": "客户操作", "rule": "程序规则与计算"}.get(item.source, item.source)
                    st.caption(f"{item.stage} · {source}")

    if order.status == "processing" and not order.runtime_error:
        label = "正在等待模拟供应商回执……" if order.step < 3 else f"{turn_context(order)[1]}正在处理……"
        st.status(label, state="running", expanded=False)
        if order.step >= 3:
            st.caption({3: "履约正在比对可用资源，下一步交策划检查行程影响。", 4: "策划正在检查替代资源会不会让行程太赶。", 5: "经理正在核对服务条件与经营规则。", 6: "经理正在确定需要客户或老板确认的事项。"}[order.step])
        if owner:
            from src.agents import task_timeout
            st.caption(f"财务由程序核查；其余资源任务每步最多等待{task_timeout(12)}秒。")
        if job:
            for item in progress_events(job)[-2:]:
                st.caption(f"{item['actor']}：{item['message']}")

    if owner:
        selected = get_package(order)
        numbers = financials(order, selected)
        st.markdown("**财务与授权（内部）**")
        st.dataframe([{"组合": x.label, "预计成本": financials(order, x)["cost"],
            "预计利润": financials(order, x)["profit"], "预计利润率": f"{financials(order, x)['margin']:.1%}",
            "单程接驳（分）": x.transfer_minutes, "住宿等级": GRADE_NAMES[x.hotel_grade],
            "需客户确认": "；".join(customer_reasons(order, x)) or "无条件变更"}
            for x in order.packages], hide_index=True, use_container_width=True)
        cash = cash_summary(order)
        st.dataframe([{"指标": name, "元": cash[key]} for name, key in (
            ("本单期初模拟现金", "opening_cash"), ("客户累计已付", "received"),
            ("累计模拟支出", "paid"), ("当前模拟现金", "cash"), ("未付供方款", "supplier_due"),
            ("待收尾款", "customer_due"), ("预收履约义务", "advance_obligation"),
            ("已确认收入", "revenue"), ("本单已实现利润", "profit"))], hide_index=True)
        st.caption("每单以46000元模拟期初现金演示，非公司跨订单总账。报价减资源成本为服务空间；预计利润另扣1% AI运营成本和5%风险预留。未动用风险预留在完单后不作为实际支出。")
        if order.status == "ready" and cash['supplier_due'] > 0:
            if st.button("授权模拟供应商支出", key=f"pay_supplier_{order.id}"):
                try:
                    pay_suppliers(order)
                    save(case)
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
        st.caption(f"内部授权：最低预计利润率{order.auto_min_margin:.0%}、额外成本不超过合同价{order.auto_max_extra_cost_ratio:.0%}、接驳增加不超过{order.auto_max_added_transfer}分钟。")
        if order.status == "waiting_owner":
            st.warning("团队已尝试优化，仍需老板作商业决定。")
            for reason in owner_reasons(order, selected):
                st.write(f"- {reason}")
            available = [x.id for x in order.packages if x.available_rooms >= order.room_count]
            labels = {x.id: x.label for x in order.packages}
            chosen = st.selectbox("老板选择资源组合", available,
                index=available.index(order.selected_package) if order.selected_package in available else 0,
                format_func=lambda x: labels[x], key=f"proc_choice_{order.id}")
            if st.button("批准所选商业方案", type="primary", key=f"proc_owner_yes_{order.id}"):
                owner_decide(order, True, chosen)
                save(case)
                st.rerun()
            if st.button("暂不批准，保留待处理", key=f"proc_owner_no_{order.id}"):
                owner_decide(order, False)
                save(case)
                st.rerun()
        elif order.status == "waiting_customer":
            st.info("商业建议已形成，正在等待客户确认已约定条件的变化；老板不能代替客户同意。")

    elif order.status == "waiting_owner":
        st.info("团队正在确认替代资源，有结果后会在这里更新。")
    elif order.status == "waiting_customer":
        selected = get_package(order)
        st.warning("替代资源影响你已确认的条件，尚未完成住宿预订。")
        st.write(f"替代酒店：{selected.hotel}；{GRADE_NAMES[selected.hotel_grade]}；单程接驳{selected.transfer_minutes}分钟。合同价保持{order.quote_yuan:,.2f}元。")
        for reason in customer_reasons(order, selected):
            st.write(f"- {reason}")
        if order.execution_schedule != order.original_schedule:
            for entry in order.execution_schedule:
                st.write(f"- {entry}")
        if st.button("我同意上述条件变更", type="primary", key=f"proc_customer_yes_{order.id}"):
            customer_decide(order, True)
            save(case)
            st.rerun()
        if st.button("不同意，请保留原标准重新安排", key=f"proc_customer_no_{order.id}"):
            customer_decide(order, False)
            save(case)
            st.rerun()

    if order.status == "ready":
        st.success("本单已完成结算。" if order.settled else "团建资源已确认。")
        demo = case.journey_demo
        delivery_ready = demo.status == "finished" if demo else allow_manual_delivery
        if not owner and not order.settled and delivery_ready:
            if not any(p.kind == "supplier_payment" for p in order.payments):
                st.info("待供方款项获批后，可确认交付并支付尾款。")
                return
            if st.button("确认活动完成并支付尾款", key=f"settle_{order.id}"):
                try:
                    settle_order(case, delivery_confirmed=allow_manual_delivery)
                    save(case)
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
        elif not owner and allow_manual_delivery and demo and not order.settled:
            st.info("此订单有未完成的活动记录，暂缓结算。")
    elif order.status == "held":
        st.warning("预订尚未完成，订单处于待处理；没有自动退款、降低标准或启动出游。")
        if owner and st.button("恢复处理并重新选择资源", key=f"proc_resume_{order.id}"):
            resume_order(order)
            save(case)
            st.rerun()
