"""Core business hotel and meal simulation, operated by the fulfillment role."""
import asyncio
from datetime import date,timedelta
import streamlit as st

from src.company_store import list_records
from src.supplier_models import BookingNeed,MealSlot
from src import supplier_simulation as business
from src.operations import register_lead
from src.simulation_ui_helpers import launch_job,job_status,run_action
from src.visits import remember_view,resume_supplier,new_supplier_request
from src.request_ui import TEAM_PROFILES, request_style

LABELS={"inquiring":"查询报价","waiting_customer":"待确认报价","customer_confirmed":"待付预付款",
    "paid":"预付款已收，正在安排预订","confirmed":"预订已确认","delivered":"服务已完成","completed":"订单已结清"}


def _generate(order_id):
    return asyncio.run(business.generate_world(order_id))


def _contact(order_id,instruction):
    return asyncio.run(business.agent_contact(order_id,instruction))


def create_form(case=None,attach=None):
    st.caption("填写团建、住宿和用餐需求，我们会核对每晚房量和每餐餐位。")
    customer=st.session_state.get("simulation_customer","demo-150")
    can_link=bool(attach and case and case.council_decision and case.status.value=="completed"
                  and not (case.procurement_order or case.supplier_order_id or case.journey_demo))
    default_arrival=date.today()+timedelta(days=7)
    req = case.requirements if case else None
    draft = st.session_state.setdefault("supplier_wizard", {
        "step": 1, "city": req.city if req and req.city else "南京",
        "check_in": default_arrival, "check_out": default_arrival + timedelta(days=2),
        "men": 100, "women": 50, "preferred_style": req.preferred_style if req else "",
        "team_profile": req.team_profile if req and req.team_profile in TEAM_PROFILES else "暂不确定",
        "grade": 2, "meal_names": ["午餐", "晚餐"],
    })
    step = draft["step"]
    st.caption(f"第 {step} 步 / 共 4 步")
    if step == 1:
        st.subheader("基本信息")
        left, right = st.columns(2)
        values = {
            "city": st.text_input("目的地", value=draft["city"]).strip(),
            "check_in": left.date_input("入住日期", value=draft["check_in"]),
            "check_out": right.date_input("退房日期", value=draft["check_out"]),
            "men": left.number_input("男员工人数", min_value=0, max_value=2000, value=draft["men"]),
            "women": right.number_input("女员工人数", min_value=0, max_value=2000, value=draft["women"]),
        }
    elif step == 2:
        st.subheader("你想要怎样的团建？")
        style, mode = request_style(draft, "supplier_style")
        values = {"preferred_style": style, "style_mode": mode,
            "team_profile": st.selectbox("团队情况", TEAM_PROFILES, index=TEAM_PROFILES.index(draft["team_profile"])),
            "desired_outcomes": st.multiselect("团建目标", ["放松减压", "增强团队协作", "促进跨部门交流", "体验城市文化"],
                                               default=draft.get("desired_outcomes", [])),
            "activity_notes": st.text_area("活动或参与偏好（选填）", value=draft.get("activity_notes", ""),
                                          max_chars=1000, placeholder="例如，晚餐后需要能容纳全员交流的场地"),
        }
    elif step == 3:
        st.subheader("住宿与活动场地")
        st.caption("按男女分别两人一间双床房核算，单数人员各占一间。")
        values = {
            "grade": st.selectbox("住宿标准", [1, 2, 3], index=draft["grade"] - 1,
                                  format_func={1: "经济型双床", 2: "舒适型双床", 3: "高品质双床"}.get),
            "bonfire": st.checkbox("需要篝火场地", value=draft.get("bonfire", False)),
            "fireworks": st.checkbox("需要烟花场地", value=draft.get("fireworks", False)),
            "link": can_link and st.checkbox("把这笔预订加入已确认的团建方案", value=draft.get("link", False)),
        }
    else:
        st.subheader("餐饮需求与确认")
        st.caption(f"{draft['city']} · {draft['check_in']} 至 {draft['check_out']} · "
                   f"{draft['men'] + draft['women']}人 · {draft['preferred_style']}")
        meals = ["早餐", "午餐", "晚餐"]
        values = {
            "meal_names": st.multiselect("每天预订的餐次", meals, default=draft["meal_names"]),
            "skip_first": st.multiselect("入住当天跳过的餐次", meals, default=draft.get("skip_first", [])),
            "skip_last": st.multiselect("退房当天跳过的餐次", meals, default=draft.get("skip_last", [])),
            "daily_budget": st.number_input("每人每天餐饮预算（元）", min_value=0, max_value=10000,
                                            value=draft.get("daily_budget")),
            "dietary": st.text_input("口味与忌口", value=draft.get("dietary", ""), max_chars=1000,
                                     placeholder="例如，需要清淡餐，有成员对花生过敏"),
        }
    left, right = st.columns(2)
    if step > 1 and left.button("上一步", key="supplier_back"):
        draft.update(values, step=step - 1)
        st.rerun()
    if not right.button("查询报价" if step == 4 else "下一步", key="supplier_next", type="primary"):
        return
    draft.update(values)
    if step == 1 and (not draft["city"] or not 1 <= (draft["check_out"] - draft["check_in"]).days <= 14
                      or not 1 <= draft["men"] + draft["women"] <= 2000):
        st.error("请填写目的地，选择1至14晚的住宿日期，并填写1至2000名参与者。")
        return
    if step == 2 and draft["preferred_style"] in {"", "还没想好"}:
        st.error("请描述你想要的风格，或选择「还没想好，选一个」。")
        return
    if step < 4:
        draft["step"] += 1
        st.rerun()
    check_in, check_out = draft["check_in"], draft["check_out"]
    try:
        meal_slots = []
        for offset in range((check_out - check_in).days + 1):
            day = check_in + timedelta(days=offset)
            for name in draft["meal_names"]:
                if ((day == check_in and name in draft["skip_first"])
                        or (day == check_out and name in draft["skip_last"])):
                    continue
                meal_slots.append(MealSlot(day=day, meal=name))
        need = BookingNeed(city=draft["city"], check_in=check_in, check_out=check_out, men=draft["men"],
            women=draft["women"], meals=meal_slots, hotel_grade=draft["grade"],
            require_bonfire=draft["bonfire"], require_fireworks=draft["fireworks"],
            dietary=draft["dietary"].strip() or "提供清淡选项，具体过敏与忌口待确认",
            daily_meal_budget_cents=draft["daily_budget"] * 100 if draft["daily_budget"] else None,
            preferred_style=draft["preferred_style"], team_profile=draft["team_profile"],
            desired_outcomes=draft["desired_outcomes"], activity_notes=draft["activity_notes"].strip())
        lead = register_lead(customer, f"{need.city} · {need.people}人酒店与餐饮")
        link = can_link and draft["link"]
        order = business.create_order(need, customer, campaign_id=lead.get("campaign_id"), case_id=case.id if link else None)
        remember_view(customer_supplier_order_id=order.id)
        st.session_state["simulation_customer"] = customer
        st.session_state.pop("supplier_wizard", None)
        if link:
            case.supplier_order_id = order.id
            attach(case)
    except ValueError as exc:
        st.error(str(exc))
        return
    try:
        business.contact_all(order.id)
        st.session_state.pop("simulation_action_error", None)
    except (ValueError, RuntimeError) as exc:
        st.session_state["simulation_action_error"] = str(exc)
    st.rerun()


@st.fragment(run_every="1s")
def render_supplier_workspace(owner=False,case=None,attach=None,order_id=None):
    st.subheader("酒店餐饮预订")
    if not order_id and owner:
        records={r["id"]:r for r in list_records("supplier_order")}
        selected=st.session_state.get("owner_supplier_order_id") or st.session_state.get("customer_supplier_order_id") or st.session_state.get("supplier_selected")
        options=[None,*records]
        order_id=st.selectbox("选择预订订单",options,index=options.index(selected) if selected in records else 0,
            format_func=lambda v:"请选择订单" if v is None else f"{v} · {records[v]['need']['city']}",
            key="supplier_order_select_owner")
        if order_id:
            remember_view(owner_supplier_order_id=order_id)
    elif not order_id:
        selected=st.session_state.get("customer_supplier_order_id") or st.session_state.get("supplier_selected")
        if selected and any(r["id"]==selected for r in list_records("supplier_order")):
            order_id=selected
            remember_view(customer_supplier_order_id=order_id)
    if order_id is None:
        if owner:
            st.info("客户端可创建预订需求；老板端处理供方支出和结算。")
        else:
            create_form(case,attach)
        return
    busy=job_status(order_id)
    order,_=business.load_order(order_id)
    if owner:
        st.button("在客户页面继续此预订",on_click=resume_supplier,args=(order.id,),key=f"resume_supplier:{order.id}")
    elif case is not None or attach is not None:
        st.button("新建预订",on_click=new_supplier_request,key=f"new_supplier:{order.id}")
    if not owner:
        st.session_state["simulation_customer"]=order.customer_id
    st.caption(f"{LABELS.get(order.status,order.status)} · {order.id}")
    st.write(f"{order.need.city} · {order.need.check_in:%m月%d日}至{order.need.check_out:%m月%d日} · "
             f"{order.need.people}人 · {order.need.rooms}间双床房 · {len(order.need.meals)}餐")
    if order.need.preferred_style:
        st.write(f"团建风格　{order.need.preferred_style}")
    if order.need.desired_outcomes:
        st.caption("团建目标　" + "、".join(order.need.desired_outcomes))
    if order.need.activity_notes:
        st.write(order.need.activity_notes)
    if owner:
        st.caption(f"客户 {order.customer_id} · 业务日期 {order.simulation_day} · 报价检查点 {order.tick}")
    if not order.world:
        if st.button("查询报价",type="primary",disabled=busy,key=f"fixed:{order.id}"):
            def prepare_quotes(order_id):
                business.install_fixed_world(order_id)
                business.contact_all(order_id)
            run_action(prepare_quotes,order.id)
        if owner:
            with st.expander("供应商调试"):
                if st.button("生成模拟供方场景",disabled=busy,key=f"world:{order.id}"):
                    launch_job(order.id,"执行 Agent 正在准备供方",_generate,order.id)
                    st.rerun()
        return
    editable=not order.confirmations and not order.supplier_paid_cents
    summary=None
    if order.selected_quote_ids:
        try:
            summary=business.contract(order,check_valid=not bool(order.confirmations))
        except ValueError as exc:
            st.warning(str(exc))
    if editable:
        available=[q for q in order.quotes if q.capacity_ok and business.quote_current(order,q)]
        hotels=[q.id for q in available if q.kind=="hotel"]
        restaurants=[q.id for q in available if q.kind=="restaurant"]
        if not hotels or not restaurants:
            if order.quotes:
                st.info("当前没有满足需求的完整报价，可以重新查询。")
            if st.button("重新查询报价" if order.quotes else "查询报价",type="primary",disabled=busy,key=f"contact_rule:{order.id}"):
                run_action(business.contact_all,order.id)
        elif not owner:
            quote_labels={q.id:f"{q.supplier_name} · ¥{q.total_cents/100:,.2f}"
                          + (f" · {len(q.meal_keys)}餐" if q.kind=="restaurant" else "") for q in available}
            with st.expander("更换酒店或餐饮" if summary else "选择酒店和餐饮",expanded=summary is None):
                if not summary:
                    st.caption("以下报价已通过房量或餐位核查。")
                with st.form(f"select_quotes:{order.id}"):
                    hotel=st.selectbox("酒店",hotels,format_func=quote_labels.__getitem__)
                    meals=st.multiselect(f"餐饮（须覆盖 {len(order.need.meals)} 餐）",restaurants,
                        default=restaurants if len(restaurants)==1 else [],format_func=quote_labels.__getitem__)
                    select=st.form_submit_button("查看预订明细",type="primary")
                if select:
                    run_action(business.select_quotes,order.id,[hotel,*meals])
    if summary:
        st.markdown("**预订明细**")
        st.write(" · ".join(q.supplier_name for q in order.quotes if q.id in order.selected_quote_ids))
        st.write(f"总价 ¥{summary['net_cents']/100:,.2f}")
        st.caption(f"资源费用 ¥{summary['resource_cents']/100:,.2f} · 服务费 ¥{order.service_fee_cents/100:,.2f}"
                   + (f" · 已减优惠 ¥{order.discount_cents/100:,.2f}" if order.discount_cents else ""))
        if order.customer_paid_cents:
            st.caption(f"已付 ¥{order.customer_paid_cents/100:,.2f}")
    if not owner and summary:
        if order.status=="waiting_customer":
            with st.form(f"booking_conditions:{order.id}:{summary['signature']}"):
                accepted=[key for key,condition in summary["conditions"].items()
                          if st.checkbox(condition,key=f"condition:{order.id}:{key}")]
                confirm=st.form_submit_button("确认报价及预订条件",type="primary")
            if confirm:
                run_action(business.confirm_contract,order.id,accepted,"customer",summary["signature"])
        elif order.status in {"customer_confirmed","paid"}:
            remaining=max(0,(summary["net_cents"]*60+50)//100-order.customer_paid_cents)
            if remaining:
                if st.button(f"支付预付款 ¥{remaining/100:,.2f}",type="primary",key=f"customer_pay:{order.id}"):
                    run_action(business.pay_customer_deposit,order.id,order.coupon_id,"customer",summary["signature"])
            elif order.status=="customer_confirmed":
                if st.button("确认预付款",type="primary",key=f"customer_pay:{order.id}"):
                    run_action(business.pay_customer_deposit,order.id,order.coupon_id,"customer",summary["signature"])
            else:
                st.info("我们正在确认酒店和餐饮，预订回执会更新在这里。")
    if owner and summary and order.status=="paid":
        st.caption("供方模拟定金为资源费用30%，老板明确授权；程序保留15000元公司模拟现金。")
        if st.button("老板授权模拟支付酒店餐饮定金",key=f"owner_pay:{order.id}"):
            run_action(business.pay_supplier_deposit,order.id,"owner",summary["signature"])
    if owner and order.status=="paid" and order.supplier_paid_cents:
        if st.button("执行工具提交模拟预订并获取回执",key=f"fulfill:{order.id}"):
            run_action(business.fulfill_bookings,order.id)
    if order.confirmations:
        with st.expander("预订回执"):
            st.dataframe(order.confirmations,hide_index=True)
        if not owner and order.status=="confirmed":
            delivery=st.checkbox("酒店和餐饮服务已完成",key=f"delivery:{order.id}")
            if st.button("确认完成并支付尾款",type="primary",disabled=not delivery,key=f"tail:{order.id}"):
                run_action(business.finish_service,order.id,"customer")
        if owner and order.status=="delivered" and st.button("老板授权结清供方并完成本单",key=f"settle:{order.id}"):
            run_action(business.settle_suppliers,order.id,"owner")
    if owner:
        with st.expander("供应商报价、库存与协作记录"):
            st.caption(f"供方来源 {order.world_source} · 供方已付 ¥{order.supplier_paid_cents/100:,.2f}")
            st.dataframe([{"供方":q.supplier_name,"金额":q.total_cents/100,"容量满足":q.capacity_ok,
                "报价有效":business.quote_current(order,q),"阻塞":"；".join(q.blockers),
                "确认条件":"；".join(q.conditions.values())} for q in order.quotes[-30:]],hide_index=True)
            st.json(order.world.model_dump(mode="json"),expanded=False)
            for contact in order.contacts[-10:]:
                st.markdown(f"**{contact['actor']} · {contact['source']}**")
                st.write(contact.get("request",""))
                st.info(contact.get("reply",""))
            st.json(order.handoffs,expanded=False)
    if owner and editable:
        with st.expander("供应商调试"):
            instruction=st.text_input("给执行 Agent 的联系任务",value="比较可用房量和餐位，取得可执行报价",key=f"contact_instruction:{order.id}")
            if st.button("由执行 Agent 模拟联系",disabled=busy,key=f"contact_agent:{order.id}"):
                launch_job(order.id,"执行 Agent 正在联系供方",_contact,order.id,instruction)
                st.rerun()
            if st.button("模拟报价过期",key=f"expire:{order.id}"):
                run_action(business.simulate_change,order.id,"expire")
            if st.button("模拟酒店B第二晚房量减少",key=f"shortage:{order.id}"):
                run_action(business.simulate_change,order.id,"second_night_shortage")
