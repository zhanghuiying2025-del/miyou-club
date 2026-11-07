"""Read-only dashboard with links to the existing owner workflow actions."""

from html import escape

import streamlit as st


PAGES = ("经营总览", "订单与风险", "经营复盘", "AI 员工与制度")
PAGE_KEY = "owner_dashboard_page"
TASK_STATES = {
    "running": "执行中",
    "failed": "失败",
    "interrupted": "中断",
    "completed": "已完成",
}
EXCEPTION_STATES = {"failed", "interrupted"}
MONEY_COLUMNS = {
    "预计成本", "预计利润", "已确认合同收入", "已记录直接成本", "已实现订单利润",
    "已确认净服务费收入", "已结清供方支出", "模拟推广支出", "客户已付金额",
}

DASHBOARD_STYLE = """
<style>
.st-key-owner_dashboard {
    --owner-accent: #258263;
    --owner-tint: color-mix(in srgb, var(--owner-accent) 8%, transparent);
}
.st-key-owner_dashboard .owner-heading {
    border-block-end: 1px solid color-mix(in srgb, currentColor 15%, transparent);
    padding-block-end: .6rem;
}
.st-key-owner_dashboard .owner-heading span {
    display: inline-block;
    background: var(--owner-tint);
    border-radius: .4rem;
    margin-inline-start: .5rem;
    padding: .1rem .4rem;
    font-size: .78rem;
    vertical-align: middle;
}
.st-key-owner_dashboard button[kind="segmented_controlActive"] {
    border-color: var(--owner-accent);
    background: var(--owner-tint);
    color: inherit;
}
.st-key-owner_dashboard .owner-item {
    border-block-start: 1px solid color-mix(in srgb, currentColor 13%, transparent);
    padding-block: .55rem .15rem;
    overflow-wrap: anywhere;
}
.st-key-owner_dashboard .owner-item p { margin: .25rem 0 0; font-size: .875rem; }
.st-key-owner_dashboard .owner-status {
    display: inline-block;
    background: var(--owner-tint);
    margin-inline-start: .4rem;
    padding: .05rem .4rem;
    border-radius: .35rem;
    font-size: .76rem;
}
.st-key-owner_dashboard .owner-status.owner-alert {
    background: color-mix(in srgb, #bc7c25 18%, transparent);
}
.st-key-owner_dashboard [data-testid="stMetric"] { font-variant-numeric: tabular-nums; }
</style>
"""


def _money(cents):
    return f"¥{cents / 100:,.2f}"


def _item(title, status, detail="", *, urgent=False):
    badge = "owner-status owner-alert" if urgent else "owner-status"
    st.html(
        f'<div class="owner-item"><strong>{escape(title)}</strong>'
        f'<span class="{badge}">{escape(status)}</span>'
        + (f"<p>{escape(detail)}</p>" if detail else "")
        + "</div>"
    )


def _table(rows):
    money = MONEY_COLUMNS.intersection(rows[0])
    st.dataframe(
        rows,
        hide_index=True,
        width="stretch",
        column_config={name: st.column_config.NumberColumn(format="¥%.2f") for name in money},
    )


def _switch_page(page):
    st.session_state[PAGE_KEY] = page


def _overview(snapshot):
    st.subheader(PAGES[0])
    st.caption("查看经营摘要，使用顶部导航处理具体事务。")
    pending = sum(order["needs_owner"] for order in snapshot["orders"])
    exceptions = sum(task["status"] in EXCEPTION_STATES for task in snapshot["tasks"])
    main_orders = snapshot["main_orders"]
    pending_col, settled_col, task_col = st.columns(3)
    with pending_col.container(border=True):
        st.metric("待老板处理", f"{pending} 项")
        st.button("查看订单与风险", key="owner_dashboard_go_orders",
                  on_click=_switch_page, args=(PAGES[1],))
    with settled_col.container(border=True):
        st.metric("已结清团建主单", f"{main_orders['settled']} 笔")
        st.caption(f"累计已付 {main_orders['paid']} 笔 · 按团建案件去重")
        st.button("查看经营复盘", key="owner_dashboard_go_review",
                  on_click=_switch_page, args=(PAGES[2],))
    with task_col.container(border=True):
        st.metric("失败与中断任务", f"{exceptions} 项")
        st.button("查看任务", key="owner_dashboard_go_tasks",
                  on_click=_switch_page, args=(PAGES[3],))


def _review_cash(snapshot):
    supplier = snapshot["supplier"]
    cash = snapshot["supplier_cash"]
    with st.container(border=True):
        st.markdown("**酒店餐饮与运营 · 模拟资金**")
        st.caption(
            f"去重付费客户 {supplier['paying_customers']} 位 · "
            f"酒店餐饮已付 {supplier['paid_orders']} 笔 · 已结算 {supplier['settled_orders']} 笔 · "
            f"已确认净服务费 {_money(supplier['service_income_cents'])}"
        )
        incoming, outgoing, balance = st.columns(3)
        incoming.metric("累计模拟收款", _money(cash["incoming_cents"]))
        outgoing.metric("累计模拟支出", _money(cash["outgoing_cents"]))
        balance.metric("模拟现金余额", _money(cash["cash_cents"]))
        st.caption(
            f"收款含客户预付款和尾款，支出含供方款和模拟推广费。"
            f"余额包含期初资金 {_money(cash['opening_cents'])}，服务费在结算后确认。"
        )


def _orders(orders, open_order):
    st.subheader(PAGES[1])
    st.caption("按主单跟进审批与交付，关联酒店餐饮合同归入主单。")
    if not orders:
        st.info("提交需求或创建酒店餐饮合同后，订单会出现在这里。")
        return

    filters = {
        "全部": lambda order: True,
        "待老板": lambda order: order["needs_owner"],
        "待客户": lambda order: order["next_actor"] == "客户",
        "待履约": lambda order: order["next_actor"] == "履约执行",
        "待团队": lambda order: order["next_actor"] in {"团队", "项目经理", "多角色策划"},
    }
    selected_filter = st.selectbox("筛选订单", list(filters), key="owner_dashboard_order_filter")
    candidates = {order["key"]: order for order in orders if filters[selected_filter](order)}
    if not candidates:
        st.info("这个筛选范围内暂无订单。")
        return

    _table([
        {
            "订单": order["title"],
            "编号": order["source_id"],
            "当前阶段": order["stage"],
            "接手人": order["next_actor"],
            "卡点": order["blocker"],
        }
        for order in candidates.values()
    ])
    selected = st.selectbox(
        "查看订单",
        list(candidates),
        format_func=lambda key: f"{candidates[key]['title']} · {candidates[key]['source_id']}",
        key="owner_dashboard_order_selection",
    )
    order = candidates[selected]
    details, history = st.columns([1.2, 1])
    with details.container(border=True):
        st.subheader(order["title"])
        _item("当前阶段", order["stage"], order["blocker"], urgent=order["needs_owner"])
        st.caption(f"当前接手人 · {order['next_actor']}")
        if order.get("after_approval"):
            st.caption(f"处理后 · {order['after_approval']}")
        if order.get("alternatives"):
            st.markdown("**资源方案对比**")
            _table(order["alternatives"])
            st.caption("商业方案获批后，涉及已约定条件变化时由客户确认。供方支出另行授权。")
        st.button(
            ("提交预订" if order["stage"] == "待提交模拟预订" else
             "进入现有审批" if order["needs_owner"] else "打开订单详情"),
            key=f"owner_dashboard_open:{order['key']}", type="primary",
            on_click=open_order, args=(order,),
        )
    with history.container(border=True):
        st.subheader("订单轨迹")
        if order.get("events"):
            _table(order["events"])
        else:
            st.caption("该订单暂无可展示的交接记录。")


def _review(snapshot, open_order):
    st.subheader(PAGES[2])
    st.caption("按业务核对已结算结果，回到原订单查看反馈与决策记录。")
    st.caption("原团建资源合同按合同收入和直接成本展示。酒店餐饮按客户已付、供方支出和净服务费展示。")
    _review_cash(snapshot)
    settled_col, growth_col = st.columns([1.2, 1])
    with settled_col.container(border=True):
        st.subheader("已结算订单")
        rows = snapshot["settled"]
        if rows:
            for service in ("团建", "酒店餐饮"):
                group = [row for row in rows if row["业务"] == service]
                if group:
                    st.markdown(f"**{service}**")
                    _table(group)
            ids = {row["订单编号"] for row in rows}
            orders = {
                order["key"]: order for order in snapshot["orders"]
                if ids.intersection((order["source_id"], order["supplier_order_id"], order.get("procurement_order_id")))
            }
            if orders:
                selected = st.selectbox(
                    "回看结算与客户反馈", list(orders),
                    format_func=lambda key: f"{orders[key]['title']} · {orders[key]['source_id']}",
                    key="owner_dashboard_review_order",
                )
                st.button(
                    "查看订单与反馈", key="owner_dashboard_review_open",
                    on_click=open_order, args=(orders[selected],),
                )
        else:
            st.caption("订单完成结算后，这里会显示收入、成本和已记录的结果。")
    with growth_col.container(border=True):
        supplier = snapshot["supplier"]
        st.subheader("来源与成交")
        st.caption("酒店餐饮 · 本地模拟记录")
        st.write(
            f"已登记 {supplier['leads']} 条线索 · 去重付费客户 {supplier['paying_customers']} 位 · "
            f"已付款 {supplier['paid_orders']} 笔 · 已结算 {supplier['settled_orders']} 笔"
        )
        st.metric("已确认净服务费收入", _money(supplier["service_income_cents"]))
        if snapshot["campaigns"]:
            _table(snapshot["campaigns"])
        else:
            st.caption("暂无可归因的活动记录。")


def _team(snapshot, open_order, render_policy):
    st.subheader(PAGES[3])
    st.caption("项目经理 · 策划 · 履约执行")
    with st.container(border=True):
        st.subheader("任务与交接")
        st.caption("任务结果按模型输出、程序计算、规则降级和缓存复用区分。")
        filters = {
            "失败与中断": ("failed", "interrupted"),
            "全部": tuple(TASK_STATES),
            "执行中": ("running",),
            "已完成": ("completed",),
        }
        task_filter = st.selectbox("任务状态", list(filters), key="owner_dashboard_task_filter")
        tasks = [task for task in snapshot["tasks"] if task["status"] in filters[task_filter]]
        if tasks:
            details = [
                {
                    "订单": task["order_title"],
                    "任务": task["title"],
                    "发起方": task.get("sender", ""),
                    "执行方": task["actor"],
                    "状态": TASK_STATES[task["status"]],
                    "耗时（秒）": task["elapsed_seconds"],
                    "来源": task["source"],
                    "输入依据": task.get("input_summary", ""),
                    "预期结果": task.get("expected_result", ""),
                    "结果或失败原因": task["detail"],
                }
                for task in tasks
            ]
            columns = ("订单", "任务", "执行方", "状态", "结果或失败原因")
            _table([{column: row[column] for column in columns} for row in details])
            with st.expander("查看完整任务交接"):
                _table(details)
            task_orders = {task["order_key"] for task in tasks}
            orders = {
                order["key"]: order for order in snapshot["orders"] if order["key"] in task_orders
            }
            if orders:
                selected = st.selectbox(
                    "定位任务所在订单", list(orders),
                    format_func=lambda key: f"{orders[key]['title']} · {orders[key]['source_id']}",
                    key="owner_dashboard_task_order",
                )
                st.button(
                    "打开关联订单", key="owner_dashboard_task_open",
                    on_click=open_order, args=(orders[selected],),
                )
                st.caption("可在订单详情按原步骤处理失败或中断的任务。")
        else:
            st.info("这个筛选范围内暂无任务记录。")
    with st.expander("查看审批规则与反馈约束"):
        render_policy()


def render_owner_dashboard(snapshot, *, open_order, render_policy):
    """Render live owner data, dispatching write actions to the order detail UI."""
    with st.container(key="owner_dashboard"):
        st.html(DASHBOARD_STYLE)
        st.html('<div class="owner-heading"><strong>团建旅行社 · 经营驾驶舱</strong><span>模拟业务</span></div>')
        nav, actions = st.columns([4, 1], vertical_alignment="center")
        with nav:
            page = st.segmented_control(
                "老板端导航", PAGES, default=None if PAGE_KEY in st.session_state else PAGES[0], required=True,
                key=PAGE_KEY, label_visibility="collapsed",
            )
        with actions:
            st.button("刷新数据", key="owner_dashboard_refresh")
        if page == PAGES[1]:
            _orders(snapshot["orders"], open_order)
        elif page == PAGES[2]:
            _review(snapshot, open_order)
        elif page == PAGES[3]:
            _team(snapshot, open_order, render_policy)
        else:
            _overview(snapshot)
