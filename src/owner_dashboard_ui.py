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
    "已确认净服务费收入", "已结清供方支出", "模拟推广支出",
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


def _overview(snapshot, open_order):
    st.subheader(PAGES[0])
    st.caption("当前累计 · 数据按业务与结算口径分列")
    travel, supplier = snapshot["travel"], snapshot["supplier"]
    metrics = (
        ("已登记线索", f"{supplier['leads']} 条", "酒店餐饮 · 模拟登记"),
        ("已付订单", f"{travel['paid_orders']} 笔", f"团建 · 含预付，已结算 {travel['settled_orders']} 笔"),
        ("已确认合同收入", _money(travel["revenue_cents"]), "团建 · 已结算订单"),
        ("已结算单直接成本", _money(travel["direct_cost_cents"]), "团建 · 供方、AI 运营及活动支出"),
    )
    for column, (label, value, scope) in zip(st.columns(4), metrics):
        with column.container(border=True):
            st.metric(label, value)
            st.caption(scope)
    st.caption(
        f"酒店餐饮模拟 · 去重付费客户 {supplier['paying_customers']} 位 · "
        f"已付 {supplier['paid_orders']} 笔 · 已结算 {supplier['settled_orders']} 笔 · "
        f"已确认净服务费 {_money(supplier['service_income_cents'])}"
    )

    pending = [order for order in snapshot["orders"] if order["needs_owner"]]
    tasks = [
        task for task in snapshot["tasks"]
        if task["status"] == "running" or task["status"] in EXCEPTION_STATES
    ]
    pending_col, task_col = st.columns([1.2, 1])
    with pending_col.container(border=True):
        st.subheader(f"需要你处理 · {len(pending)} 项")
        if pending:
            for order in pending[:3]:
                detail = order["blocker"] or order["stage"]
                _item(order["title"], "待老板", detail, urgent=True)
                if order.get("after_approval"):
                    st.caption(f"批准后 · {order['after_approval']}")
                st.button(
                    "查看审批", key=f"owner_dashboard_pending:{order['key']}",
                    on_click=open_order, args=(order,),
                )
        else:
            st.caption("当前没有需要老板处理的事项。")
        st.button(
            "查看订单与风险", key="owner_dashboard_go_orders",
            on_click=_switch_page, args=(PAGES[1],),
        )
    with task_col.container(border=True):
        st.subheader("Agent 动态")
        if tasks:
            for task in tasks[:4]:
                title = f"{task['actor']} · {task['title']}"
                detail = " · ".join(filter(None, (task["order_title"], task["detail"])))
                _item(title, TASK_STATES[task["status"]], detail, urgent=task["status"] in EXCEPTION_STATES)
        else:
            st.caption("当前没有执行中、失败或中断的任务记录。")
        st.button(
            "查看任务", key="owner_dashboard_go_tasks",
            on_click=_switch_page, args=(PAGES[3],),
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
            st.caption(f"批准后 · {order['after_approval']}")
        if order.get("alternatives"):
            st.markdown("**资源方案对比**")
            _table(order["alternatives"])
            st.caption("商业方案获批后，涉及已约定条件变化时由客户确认。供方支出另行授权。")
        st.button(
            "进入现有审批" if order["needs_owner"] else "打开订单详情",
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
    st.caption("项目经理 · 多角色策划 · 履约执行")
    tasks_col, policy_col = st.columns([1.45, 1])
    with tasks_col.container(border=True):
        st.subheader("任务与交接")
        st.caption("任务结果按模型输出、程序计算、规则降级和缓存复用区分。")
        filters = {
            "全部": tuple(TASK_STATES),
            "执行中": ("running",),
            "失败与中断": ("failed", "interrupted"),
            "已完成": ("completed",),
        }
        task_filter = st.selectbox("任务状态", list(filters), key="owner_dashboard_task_filter")
        tasks = [task for task in snapshot["tasks"] if task["status"] in filters[task_filter]]
        if tasks:
            _table([
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
            ])
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
    with policy_col.container(border=True):
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
            _overview(snapshot, open_order)
