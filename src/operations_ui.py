"""Optional company modules; browsing and numeric operations consume no model calls."""
import asyncio
from datetime import date
import streamlit as st
from src import operations as ops
from src.company_store import list_records
from src.simulation_ui_helpers import run_action,launch_job,job_status


def _research(topic):
    return asyncio.run(ops.model_research(topic,"owner"))


def customer_growth():
    st.header("活动与优惠")
    customer=st.session_state.get("simulation_customer","demo-150")
    campaigns=[c for c in list_records("campaign") if c["status"]=="active"]
    for campaign in campaigns:
        with st.container(border=True):
            st.markdown(f"**{campaign['title']}**")
            st.write(campaign["content"])
            if st.button("查看预订",key=f"campaign_booking:{campaign['id']}"):
                try:
                    ops.register_lead(customer,campaign["title"],campaign["id"])
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.session_state["work_area"]="酒店餐饮模拟预订"
                    st.rerun()
    policies=[p for p in list_records("coupon_policy") if p["active"]]
    if not campaigns and not policies:
        st.info("近期暂无活动或优惠。")
    mine={c["policy_id"]:c for c in list_records("coupon") if c["customer_id"]==customer}
    for policy in policies:
        with st.container(border=True):
            st.write(f"**{policy['title']}** · 服务费减 ¥{policy['face_cents']/100:.2f}")
            st.caption(f"{policy['audience']} · 满 ¥{policy['min_order_cents']/100:.2f} 可用 · {policy['valid_from']} 至 {policy['valid_until']}")
            if policy["id"] in mine:
                st.caption("已使用" if mine[policy["id"]]["status"]=="redeemed" else "已领取")
            elif st.button("领取优惠券",key=f"claim:{policy['id']}:{customer}"):
                def claim():
                    ops.register_lead(customer,policy["title"],policy["campaign_id"])
                    ops.issue_coupon(policy["id"],customer)
                run_action(claim)


@st.fragment(run_every="1s")
def render_operations_workspace(owner=False):
    if st.session_state.get("simulation_action_error"):
        st.error(st.session_state["simulation_action_error"])
    if not owner:
        customer_growth()
        return
    st.header("公司运营")
    st.caption("市场资料、推广与优惠使用本地模拟数据。")
    overview,research,growth,coupons=st.tabs(["经营记录","市场调研","获客活动","优惠券"])
    with overview:
        summary=ops.operating_summary()
        cols=st.columns(4)
        for column,label,value in zip(cols,["登记客户","已付订单","已完成订单","已完成服务收入（元）"],
                [summary["leads"],summary["paid_orders"],summary["completed_orders"],f"{summary['service_income_cents']/100:,.2f}"]):
            column.metric(label,value)
        st.caption("统计仅覆盖新酒店餐饮模块；已完成服务收入尚未扣除推广等公司费用，不直接等于公司利润。")
        st.dataframe([{"指标":label,"模拟元":summary["finance"][key]/100} for label,key in
            [("公司期初现金","opening_cents"),("累计流入","incoming_cents"),("累计流出","outgoing_cents"),("当前现金","cash_cents"),("资金保留线","reserve_cents")]],hide_index=True)
        if summary["campaigns"]:
            st.dataframe(summary["campaigns"],hide_index=True)
        if summary["ledger"]:
            with st.expander("模拟收支流水（预付款与收入分别展示）"):
                st.dataframe(summary["ledger"],hide_index=True)
        st.caption("无数据时不编造转化率；获客记录与订单通过客户编号和活动编号关联。")
    with research:
        busy=job_status("company-research")
        topic=st.text_input("调研主题",value="150人企业团建，酒店餐饮协调与透明报价")
        if st.button("生成竞品对比",type="primary"):
            run_action(ops.fixed_research,topic,"owner")
        with st.expander("调研演示设置"):
            if st.button("由模型生成模拟市场报告",disabled=busy):
                launch_job("company-research","运营模块正在生成模拟市场报告",_research,topic)
                st.rerun()
        for record in list_records("research")[:5]:
            with st.expander(f"{record['topic']} · {record['source']}",expanded=True):
                st.write(record["report"]["summary"])
                st.dataframe(record["report"]["competitors"],hide_index=True)
                for suggestion in record["report"]["opportunities"]:
                    st.write(f"- 待验证机会：{suggestion}")
    with growth:
        researches=list_records("research")
        with st.form("campaign_create"):
            title=st.text_input("活动标题",value="企业团建酒店餐饮协调体验")
            audience=st.text_input("目标客群",value="企业行政与团建组织者")
            channel=st.selectbox("模拟获客渠道",["社群演示入口","老客转介绍","企业行政交流"])
            budget=st.number_input("授权推广预算（模拟元）",min_value=0,max_value=1000000,value=1000)
            source=st.selectbox("参考市场报告",[None]+[r["id"] for r in researches],format_func=lambda v:"无" if v is None else next(r["topic"] for r in researches if r["id"]==v))
            content=st.text_area("推广内容草案",value="提供酒店连续房量、餐次和费用条件对比，跟进模拟预订。具体资源与价格以确认结果为准。")
            submitted=st.form_submit_button("保存获客活动草案")
        if submitted:
            run_action(ops.create_campaign,title,audience,channel,int(budget)*100,content,source,"owner")
        for campaign in list_records("campaign"):
            with st.expander(f"{campaign['title']} · {campaign['status']}",expanded=True):
                st.write(campaign["content"])
                if campaign["status"]=="draft" and st.button("老板批准启用本地模拟活动",key=f"activate:{campaign['id']}"):
                    run_action(ops.activate_campaign,campaign["id"],"owner")
                if campaign["status"]=="active":
                    with st.form(f"spend_form:{campaign['id']}"):
                        amount=st.number_input("本次模拟推广支出（元）",min_value=1,max_value=1000000,value=100,key=f"amount:{campaign['id']}")
                        reference=st.text_input("唯一支出编号（重复提交同编号不重复扣款）",value="demo-spend-1",key=f"reference:{campaign['id']}")
                        pay=st.form_submit_button("老板授权记录模拟推广支出")
                    if pay:
                        run_action(ops.campaign_spend,campaign["id"],int(amount)*100,reference,"owner")
        st.caption("客户从“活动与优惠”进入预订时，系统会记录活动来源。")
    with coupons:
        campaigns=list_records("campaign")
        with st.form("coupon_policy_create"):
            title=st.text_input("优惠名称",value="首单服务费优惠")
            face=st.number_input("优惠面额（元）",min_value=1,max_value=10000,value=100)
            minimum=st.number_input("最低合同金额（元）",min_value=0,max_value=10000000,value=1000)
            audience=st.selectbox("适用客户",["全部","首单","老客"],index=1)
            start=st.date_input("有效开始日期（演示日期）",value=date(2026,11,1))
            end=st.date_input("有效截止日期（演示日期）",value=date(2026,12,31))
            limit=st.number_input("最多发放张数",min_value=1,max_value=10000,value=20)
            campaign=st.selectbox("限定来源活动",[None]+[c["id"] for c in campaigns],format_func=lambda v:"不限活动" if v is None else next(c["title"] for c in campaigns if c["id"]==v))
            agreed=st.checkbox("老板批准该模拟优惠；仅抵服务费，不叠加，保留至少500元本单服务收入")
            submitted=st.form_submit_button("创建并启用优惠政策")
        if submitted:
            if not agreed:
                st.error("请由老板明确批准优惠规则")
            else:
                run_action(ops.create_coupon_policy,title,int(face)*100,int(minimum)*100,start,end,audience,int(limit),campaign,"owner")
        policies=list_records("coupon_policy")
        if policies:
            st.dataframe(policies,hide_index=True)
            for entry in policies:
                if st.button(("停用" if entry["active"] else "重新启用")+entry["title"],key=f"toggle:{entry['id']}"):
                    run_action(ops.set_coupon_active,entry["id"],not entry["active"],"owner")
            with st.form("owner_issue_coupon"):
                policy=st.selectbox("优惠政策",[p["id"] for p in policies],format_func=lambda v:next(p["title"] for p in policies if p["id"]==v))
                customer=st.text_input("已登记的演示客户编号",value="demo-150")
                issue=st.form_submit_button("按政策发放模拟优惠券")
            if issue:
                run_action(ops.issue_coupon,policy,customer)
        tickets=list_records("coupon")
        if tickets:
            st.dataframe(tickets,hide_index=True)
