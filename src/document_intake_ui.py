from datetime import date
import streamlit as st

from src.activity_review import read_activity_documents
from src.document_intake import FIELD_LABELS, missing_fields, update_document_fields, confirm_document_route
from src.request_ui import show_request_summary
from src.style_planning import STYLE_OPTIONS, style_for


def render_document_upload(workflow, process_case, save_case, existing_case=None):
    st.caption('上传 Word 或 PDF，我们会整理已有信息并提示需要补充的内容。')
    with st.form(f'document_upload_{existing_case.id if existing_case else "new"}'):
        files = st.file_uploader('上传需求或已有活动方案', type=['docx', 'pdf'], accept_multiple_files=True,
            help='最多3份，单份不超过12 MB；扫描PDF和Word内嵌图片需补充文字。')
        notes = st.text_area('特别说明（选填）', placeholder='例如，希望保留原活动并检查人员分工。')
        submitted = st.form_submit_button('读取文档，整理已有信息', type='primary')
    if submitted:
        try:
            sources = read_activity_documents([(f.name, f.type or 'application/octet-stream', f.getvalue()) for f in files])
            case = existing_case.model_copy(deep=True) if existing_case else workflow.create_case('根据客户上传文档整理需求')
            if case.procurement_order or case.journey_demo:
                raise ValueError('已有订单正在执行，请新建服务后再上传文档')
            case.service_mode = 'document_intake'
            case.activity_sources = sources
            case.activity_review_notes = notes.strip()
            case.document_intake = None
            if case.activity_review:
                case.activity_review_history = [*case.activity_review_history, case.activity_review][-3:]
                case.activity_review = None
            save_case(case)
            process_case(workflow, case, '正在读取文档中的信息……', action='document_intake')
            st.rerun()
        except (ValueError, RuntimeError) as exc:
            st.error(str(exc))


def render_document_confirmation(workflow, case, process_case, save_case, *, tracking_enabled=False):
    if case.error_message:
        st.error(case.error_message)
    if not case.document_intake:
        if st.button('重试读取文档'):
            try:
                process_case(workflow, case, '重新读取已保存的原文……', action='document_intake')
                st.rerun()
            except RuntimeError as exc:
                st.error(str(exc))
        return
    result, req = case.document_intake, case.requirements
    st.subheader('文档已读取，先确认已有信息')
    st.write(result.summary)
    known = {'活动': req.event_name, '日期': req.event_date, '场地': case.document_venue,
        '城市': req.city, '参与总人数': req.participant_count,
        '文档预算': f'{req.budget_yuan:g}元' if req.budget_yuan is not None else None}
    st.write(' ｜ '.join(f'{k}：{v}' for k, v in known.items() if v))
    if req.event_date:
        try:
            if date.fromisoformat(req.event_date) < date.today():
                st.info('文档中的活动日期已经过去。如用于新活动，可以补充新的日期。')
        except ValueError:
            pass
    with st.expander('查看原文'):
        for source in case.activity_sources:
            st.write(source.file_name)
            for warning in source.warnings:
                st.warning(warning)
            st.text(source.text)
    purpose = st.radio('接下来希望我们做什么', ['activity', 'travel'],
        format_func={'activity': '按已有方案跟进活动' if tracking_enabled else '审核已有方案',
                     'travel': '根据资料策划团建'}.__getitem__,
        index=1 if result.document_kind == 'requirements' else 0, key=f'document_purpose_{case.id}')
    missing = missing_fields(case, purpose)
    edit = st.checkbox('我想补充关键信息', key=f'document_edit_{case.id}')
    fields = (['event_date', 'venue', 'participant_count'] if purpose == 'activity' else
        ['city', 'event_date', 'participant_count']) if edit else missing[:2]
    if missing:
        st.info('当前只需补充：' + '、'.join(FIELD_LABELS[x] for x in missing[:2]) + '。已读取的信息不会重复询问。')
    else:
        st.caption('基础信息齐全。预算、参赛细分人数等仅在影响具体任务时再确认，不强制补满所有字段。')
    with st.form(f'document_missing_{case.id}'):
        values = {}
        for field in fields:
            label = FIELD_LABELS[field]
            if field == 'event_date':
                try:
                    initial = date.fromisoformat(req.event_date) if req.event_date else None
                except ValueError:
                    initial = None
                values[field] = st.date_input(label, value=initial, format='YYYY-MM-DD')
            elif field == 'participant_count':
                values[field] = st.number_input(label, min_value=1, max_value=100000,
                    value=req.participant_count, step=1)
            else:
                values[field] = st.text_input(label, value=case.document_venue if field == 'venue' else req.city or '')
        style = None
        if purpose == 'travel' and not missing and case.planning_mode == "style_branch":
            st.subheader("确认需求")
            show_request_summary(req)
            style = st.selectbox("活动风格", STYLE_OPTIONS, index=STYLE_OPTIONS.index(style_for(req.preferred_style)))
        notes = st.text_input('补充说明（选填）', placeholder='例如，说明参赛与观众人数。') if edit else ""
        label = '保存补充信息' if missing else '保存并确认继续' if edit else '确认信息并继续'
        submitted = st.form_submit_button(label, type='primary')
    if submitted:
        try:
            if any(v is None or v == '' for v in values.values()):
                raise ValueError('请填写本轮必要信息；未确定时请先与组织者核实')
            update_document_fields(case, values)
            if style is not None:
                case.requirements.preferred_style = style
            if notes.strip():
                case.activity_review_notes += '\n' + notes.strip()
            case.error_message = None
            if missing:
                save_case(case)
                st.rerun()
            action = confirm_document_route(case, purpose)
            save_case(case)
            process_case(workflow, case, '依据已确认信息进入下一步……', action=action)
            st.rerun()
        except (ValueError, RuntimeError) as exc:
            st.error(str(exc))
