"""Owner-only full order archive, source inspection, and search diagnostics."""
import json
import os

import streamlit as st

from src.history_archive import KIND_LABELS, STATUS_LABELS, case_outcome, get_document, hidden_documents
from src.history_search import sync_index, search, set_active
from src.storage import hidden_archive_ids, set_archive_hidden
from src.supplier_simulation import linked_case_for_order
from src.visits import resume_for_customer, resume_supplier, remember_view, new_customer


def _case_id_for_archive(doc):
    if doc["kind"] == "case":
        return doc["source_id"]
    linked = linked_case_for_order(doc["snapshot"].get("case_id"), doc["source_id"])
    return linked.id if linked else None


def _resume(document_id):
    doc = get_document(document_id)
    if not doc:
        return
    case_id = _case_id_for_archive(doc)
    if case_id:
        resume_for_customer(case_id)
    else:
        new_customer()
        resume_supplier(doc["source_id"])


def _owner_open(document_id):
    doc = get_document(document_id)
    if not doc:
        return
    case_id = _case_id_for_archive(doc)
    if case_id:
        area = "团建策划与交付"
        remember_view(owner_case_id=case_id, work_area=area)
        st.session_state["owner_history_select"] = case_id
    else:
        area = "酒店餐饮模拟预订"
        remember_view(owner_supplier_order_id=doc["source_id"], work_area=area)
        st.session_state["supplier_order_select_owner"] = doc["source_id"]
    st.session_state.pop("case", None)
    st.session_state["work_area"] = area


def _reference_cards(rows):
    for row in rows:
        with st.expander(f"{KIND_LABELS.get(row['kind'], row['kind'])} · {row['title'][:36]} · {row['id']}"):
            st.caption(f"来源：{row['source_id']} · 第{row['version']}版 · {row['path']} · {STATUS_LABELS[row['stage']]}")
            st.text(row["text"])
            if row.get("match_reason"):
                st.caption(row["match_reason"])
            st.caption("检索相关不代表已采纳；旧价格、库存、档期和交易状态不能直接沿用。")


def render_case_references(case):
    if not case:
        return
    with st.expander("本案检索到的历史订单参考", expanded=bool(case.history_references)):
        st.caption(case.history_search_backend or "尚未进入需要历史参考的策划步骤")
        if case.history_search_notice:
            st.info(case.history_search_notice)
        if not case.history_references:
            st.write("暂无相关历史片段；系统不会为演示编造过往订单。")
        else:
            _reference_cards(case.history_references)
            supplied = sorted({name for row in case.history_references for name in row.get("supplied_to", [])})
            st.caption("已提供给：" + ("、".join(supplied) or "尚未提供") + "。此记录仅证明资料已提供，不证明已执行或产生效果。")


def _render_case_categories(docs, hidden_ids):
    st.caption("团建和预订以履约结算为成功；活动审核以客户确认收到清单为准，有执行订单的还需完成执行。失败和未完成都归入问题案例。")
    st.caption("删除仅从老板端的案例与历史档案列表移除。公司原始档案、订单和流水仍保存在本地，当前演示尚未接入外部公司数据库。")
    st.caption("进行中的订单仍可在「订单与风险」处理。可在「已移除」恢复显示。")
    case_ids = {doc["source_id"] for doc in docs if doc["kind"] == "case"}
    groups = {"成功案例": [], "问题案例": [], "已移除": []}
    for doc in docs:
        if doc["kind"] == "supplier" and doc["snapshot"].get("case_id") in case_ids:
            continue
        outcome = case_outcome(doc)
        group = "已移除" if doc["id"] in hidden_ids else "成功案例" if outcome == "成功" else "问题案例"
        groups[group].append((doc, outcome))
    group = st.radio("案例分类", list(groups), horizontal=True, key="archive_case_group",
                     format_func=lambda name: f"{name}（{len(groups[name])}）")
    cases = groups[group]
    if not cases:
        st.info(f"暂无{group}。")
        return
    st.dataframe([{"案例": doc["title"], "城市": doc["city"], "结果": outcome,
                   "编号": doc["source_id"]} for doc, outcome in cases],
                 hide_index=True, use_container_width=True)
    options = {doc["id"]: doc for doc, _ in cases}
    key = "archive_case_selection"
    if st.session_state.get(key) not in options:
        st.session_state.pop(key, None)
    chosen = st.selectbox("选择案例", list(options), key=key,
                          format_func=lambda value: options[value]["title"][:40] + " · " + options[value]["source_id"])
    restoring = group == "已移除"
    if restoring:
        submitted = st.button("恢复到列表", key="archive_restore")
    else:
        first, second = st.columns(2)
        first.button("打开案例", on_click=_owner_open, args=(chosen,), key="archive_case_open")
        with second.popover("删除案例"):
            st.write("确认将此案例从列表移除？")
            submitted = st.button("确认移除", key="archive_remove:" + chosen)
    if submitted:
        try:
            set_archive_hidden(chosen, not restoring)
        except OSError:
            st.error("未能保存列表设置，请检查本地档案目录权限后重试。")
        else:
            st.rerun()


def render_history_workspace(owner=False):
    st.header("历史订单管理")
    if not owner:
        st.info("请切换到公司老板查看公司历史订单。当前演示尚未启用账号鉴权。")
        return
    st.caption("所有订单保留完整档案；另建可重建的片段索引。浏览、搜索和恢复不会重新付款或调用策划模型。")
    try:
        docs, rows = sync_index()
        hidden_ids = hidden_documents(docs, hidden_archive_ids())
    except Exception:
        st.error("历史档案或索引读取失败，请检查文件是否完整及目录权限；原订单未被修改。")
        return
    categories_tab, archive_tab, search_tab = st.tabs(["案例归类", "完整订单档案", "经验检索与索引"])
    with categories_tab:
        _render_case_categories(docs, hidden_ids)
    docs = [d for d in docs if d["id"] not in hidden_ids]
    with archive_tab:
        a, b, c = st.columns(3)
        a.metric("策划/活动案件", sum(d["kind"] == "case" for d in docs))
        b.metric("酒店餐饮订单", sum(d["kind"] == "supplier" for d in docs))
        c.metric("可见检索片段", sum(r["active"] and r["document_id"] not in hidden_ids for r in rows))
        st.caption("同一业务的策划案与预订合同分别计数。")
        left, right = st.columns(2)
        category = left.selectbox("订单类型", ["全部", "策划/活动案件", "酒店餐饮订单"], key="history_category")
        stage = right.selectbox("履约阶段", ["全部", *STATUS_LABELS.values()], key="history_stage")
        keyword = st.text_input("按标题、编号或城市筛选", key="history_filter").strip().casefold()
        visible = [d for d in docs if (category == "全部" or d["kind"] == ("case" if category == "策划/活动案件" else "supplier"))
            and (stage == "全部" or STATUS_LABELS[d["stage"]] == stage)
            and (not keyword or keyword in (d["id"] + d["title"] + d["city"]).casefold())]
        if not visible:
            st.info("暂无符合条件的历史订单。")
        else:
            st.dataframe([{"编号": d["source_id"], "标题": d["title"], "城市": d["city"],
                "阶段": STATUS_LABELS[d["stage"]], "原始状态": d["status"], "保存版本": d["revision"]} for d in visible],
                hide_index=True, use_container_width=True)
            mapping = {d["id"]: d for d in visible}
            if st.session_state.get("history_document") not in mapping:
                st.session_state.pop("history_document", None)
            chosen = st.selectbox("查看完整档案", list(mapping),
                format_func=lambda key: mapping[key]["title"][:40] + " · " + mapping[key]["source_id"], key="history_document")
            doc = mapping[chosen]
            st.info(STATUS_LABELS[doc["stage"]] + "；原始状态：" + doc["status"])
            first, second = st.columns(2)
            first.button("在老板工作台打开", on_click=_owner_open, args=(chosen,), key="history_open_owner")
            second.button("恢复到客户页面", on_click=_resume, args=(chosen,), key="history_resume")
            st.download_button("下载完整档案 JSON", json.dumps(doc, ensure_ascii=False, indent=2),
                file_name=doc["source_id"] + "_full_archive.json", mime="application/json", key="history_download")
            with st.expander("完整原始记录（含需求、方案、反馈及执行信息）"):
                st.json(doc["snapshot"], expanded=False)
                if doc["related_orders"]:
                    st.caption("关联的酒店餐饮订单；财务金额以各合同及原流水为准。")
                    st.json(doc["related_orders"], expanded=False)
            own_rows = [r for r in rows if r["document_id"] == chosen]
            st.write(f"本档案提取了 {len(own_rows)} 个业务片段；停用片段只影响后续检索，完整档案保留。")
            if own_rows:
                options = {r["id"]: r for r in own_rows}
                key = "history_fragment:" + chosen
                if st.session_state.get(key) not in options:
                    st.session_state.pop(key, None)
                selected = st.selectbox("管理检索片段", list(options), key=key,
                    format_func=lambda key: ("启用" if options[key]["active"] else "停用") + " · "
                        + KIND_LABELS[options[key]["kind"]] + " · " + options[key]["path"])
                item = options[selected]
                st.text(item["text"])
                if st.button("停用该片段" if item["active"] else "重新启用该片段", key="history_toggle"):
                    set_active(selected, not item["active"])
                    st.rerun()
    with search_tab:
        st.caption("Redis：" + ("已配置，搜索时检测连接与 Search 能力" if os.getenv("HISTORY_REDIS_URL", "").strip()
            else "未配置，使用本地索引"))
        st.caption("向量模型：" + ("已配置本地目录，搜索时加载" if os.getenv("HISTORY_EMBEDDING_MODEL_PATH", "").strip()
            else "未配置，目前使用字段和关键词；不会自动下载模型"))
        with st.form("history_search_form"):
            query = st.text_input("检索历史经验", placeholder="例如：雨天备用、避免强迫成员参与、酒店房量不足")
            city = st.selectbox("限定城市", ["全部", *sorted({d["city"] for d in docs if d["city"]})])
            kind = st.selectbox("片段类型", ["全部", *KIND_LABELS.values()])
            submitted = st.form_submit_button("检索相关片段")
        if submitted:
            if not query.strip():
                st.info("请输入要查找的问题或关键词。")
            else:
                try:
                    with st.spinner("检索历史记录，不调用 MiMo…"):
                        found = search(query, city="" if city == "全部" else city,
                            kind=next((k for k,v in KIND_LABELS.items() if v == kind), ""), limit=8,
                            exclude_document_ids=hidden_ids)
                    st.caption(found["backend"] + f" · 返回 {len(found['rows'])} 条")
                    if found["notice"]:
                        st.info(found["notice"])
                    if not found["rows"]:
                        st.info("没有找到相关记录。关键词检索不能自动理解所有近义表达。")
                    _reference_cards(found["rows"])
                except Exception:
                    st.error("检索失败，请检查索引文件权限。订单档案未改变。")
        if st.button("从完整档案重新同步片段索引", key="history_sync"):
            sync_index()
            st.success("本地片段索引已同步，停用设置保留；Redis 在下次搜索时同步。未调用大模型。")
