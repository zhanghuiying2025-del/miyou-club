"""Customer request wizard with explicit step state."""
import streamlit as st

from src.models import RequirementSpec
from src.multimodal import validate_attachment
from src.style_planning import STYLE_OPTIONS


TEAM_PROFILES = ["全部或主要是年轻人", "青年、中年等年龄分布较广", "全部或主要是中年人",
                 "包含较多老年人", "小学生或儿童团体", "亲子或家庭混合团体", "暂不确定"]
DURATIONS = ["半天", "1天", "2天1晚", "3天2晚", "其他或未确定"]
STYLE_DETAILS = {
    "城市游玩": ("城市活动偏好", ["同一区域慢逛", "加入城市探索任务", "多留自由活动时间"]),
    "户外活动": ("运动强度", ["轻松为主", "适度挑战", "为不同体力成员准备可选路线"]),
    "博物馆古迹参观": ("参观方式", ["短讲解加互动", "深度主题导览", "保留自主参观时间"]),
}


def request_style(draft, key):
    options = [style for style in STYLE_OPTIONS if style != "还没想好"]
    saved = draft.get("preferred_style") or ""
    modes = ["自己填写", "还没想好，选一个"]
    mode = st.radio("怎么确定活动风格？", modes, horizontal=True, key=f"{key}:mode",
                    index=modes.index(draft.get("style_mode", modes[0])))
    if mode == modes[1]:
        style = st.selectbox("选择一个活动风格", options, index=options.index(saved) if saved in options else None,
                             placeholder="请选择一个方向", key=f"{key}:choice")
    else:
        style = st.text_input("活动风格", value=saved if saved != "还没想好" else "", max_chars=300,
                              placeholder="例如，慢节奏、室内为主，多小组交流，避免竞技", key=f"{key}:text").strip()
    return style or "", mode


def show_request_summary(req):
    st.write(f"{req.city or '城市待确认'} · {req.event_date or '日期待确认'} · "
             f"{req.participant_count or '人数待确认'}人 · {req.duration or '时长待确认'}")
    st.write(f"活动风格　{req.preferred_style or '还没想好'}")
    if req.budget_yuan:
        st.write(f"总预算　¥{req.budget_yuan:,.0f}")
    if req.team_profile:
        st.write(f"团队情况　{req.team_profile}")
    for label, values in (("团建目标", req.desired_outcomes), ("必须遵守", req.hard_constraints),
                          ("偏好与说明", req.preferences)):
        if values:
            st.write(f"{label}　{'、'.join(values)}")


def request_wizard(goals, constraints, dietary):
    draft = st.session_state.setdefault("trip_wizard", {})
    step = draft.get("step", 1)
    st.caption(f"第 {step} 步 / 共 3 步")
    if step == 1:
        st.subheader("基本信息")
        with st.form("trip_basics"):
            city = st.text_input("活动城市 *", value=draft.get("city", ""), placeholder="例如，南京")
            col1, col2 = st.columns(2)
            day = col1.date_input("活动日期 *", value=draft.get("event_date"), format="YYYY-MM-DD")
            people = col2.number_input("参与人数 *", 1, 1000, draft.get("participant_count", 30))
            duration = col1.selectbox("活动时长", DURATIONS, index=DURATIONS.index(draft.get("duration", "1天")))
            budget = col2.number_input("总预算（元，选填）", min_value=0, max_value=10_000_000,
                                       value=draft.get("budget_yuan"), step=1000)
            if st.form_submit_button("下一步", type="primary"):
                if not city.strip() or day is None:
                    st.error("请填写活动城市并选择活动日期。")
                else:
                    draft.update(city=city.strip(), event_date=day, participant_count=int(people),
                                 duration=duration, budget_yuan=budget, step=2)
                    st.rerun()
    elif step == 2:
        st.subheader("你想要怎样的团建？")
        style, style_mode = request_style(draft, "trip_style")
        profile = st.selectbox("团队情况", TEAM_PROFILES,
                               index=TEAM_PROFILES.index(draft.get("team_profile", TEAM_PROFILES[1])))
        detail = None
        if style in STYLE_DETAILS:
            label, options = STYLE_DETAILS[style]
            saved = draft.get("style_details", {}).get(style)
            detail = st.selectbox(label, [None, *options], index=options.index(saved) + 1 if saved in options else 0,
                                  format_func=lambda value: value or "暂未确定", key=f"trip_detail_{style}")
        default_checks = ["不安排高风险运动", "不强迫成员参加竞技项目", "必须准备雨天室内备用方案"]
        with st.expander("目标、安全要求和参考附件（选填）"):
            desired = st.multiselect("团建目标", goals, default=draft.get("desired_outcomes", ["增强团队协作", "创造共同回忆"]))
            required = st.multiselect("必须遵守的要求", constraints, default=draft.get("constraints", default_checks))
            meals = st.multiselect("餐饮需求", dietary, default=draft.get("dietary", []))
            notes = st.text_area("其他说明", value=draft.get("notes", ""), max_chars=2000)
            files = st.file_uploader("参考资料（最多3份）", type=["jpg", "jpeg", "png", "gif", "webp", "pdf", "docx"],
                                     accept_multiple_files=True)
            saved_files = draft.get("attachments", [])
            clear = False
            if saved_files:
                st.caption("已保留附件 " + "、".join(item[0] for item in saved_files))
                clear = st.checkbox("移除已保留的附件")
        left, right = st.columns(2)
        back = left.button("上一步")
        forward = right.button("下一步", type="primary")
        if back or forward:
            if forward and style in {"", "还没想好"}:
                st.error("请描述你想要的风格，或选择「还没想好，选一个」。")
                return None
            attachments = [] if clear else saved_files
            if files:
                attachments = [(file.name, file.type or "application/octet-stream", file.getvalue()) for file in files]
            try:
                if len(attachments) > 3:
                    raise ValueError("一次最多上传3个附件。")
                for attachment in attachments:
                    validate_attachment(*attachment)
            except ValueError as exc:
                st.error(str(exc))
                return None
            draft.setdefault("style_details", {})[style] = detail
            draft.update(preferred_style=style, style_mode=style_mode, team_profile=profile, desired_outcomes=desired, constraints=required,
                         dietary=meals, notes=notes.strip(), attachments=attachments, step=1 if back else 3)
            st.rerun()
    else:
        st.subheader("确认需求")
        details = [x for x in (draft["style_details"].get(draft["preferred_style"]), draft["notes"]) if x]
        req = RequirementSpec(city=draft["city"], event_date=draft["event_date"].isoformat(),
            participant_count=draft["participant_count"], duration=draft["duration"], budget_yuan=draft["budget_yuan"] or None,
            team_profile=draft["team_profile"], preferred_style=draft["preferred_style"], desired_outcomes=draft["desired_outcomes"],
            hard_constraints=[*draft["constraints"], *draft["dietary"]], preferences=details)
        show_request_summary(req)
        attachments = draft.get("attachments", [])
        if attachments:
            st.caption("参考附件 " + "、".join(x[0] for x in attachments))
        left, right = st.columns(2)
        if left.button("上一步"):
            draft["step"] = 2
            st.rerun()
        if right.button("提交团建需求", type="primary"):
            return req, attachments
    return None
