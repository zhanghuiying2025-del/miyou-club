import React, { useState } from 'react';
import { money, useResource } from '../../shared/api.js';
import { customerApi, workspaceUrl } from './api.js';

export function UploadDocuments({ onCreated }) {
  const [requestId] = useState(() => crypto.randomUUID());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function submit(event) {
    event.preventDefault();
    const body = new FormData(event.currentTarget);
    body.set('request_id', requestId);
    setBusy(true);
    setError('');
    try {
      const activity = await customerApi('/customer/documents', { body });
      onCreated(workspaceUrl(activity.id));
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  }
  return <main className="content-shell dashboard-shell"><h1 className="display-heading">上传已有活动资料</h1>
    <form className="connected-panel" onSubmit={submit}>
      <label>选择 Word 或 PDF（最多 3 份）<input name="files" type="file" multiple required accept=".docx,.pdf" /></label>
      <p>先读取文字并请你核对。你可以用这些资料继续策划团建，也可以审核已有方案。</p>
      <button className="button" disabled={busy}>{busy ? '正在上传…' : '读取活动资料'}</button>
      {error && <p className="connected-error" role="alert">{error}</p>}
    </form>
  </main>;
}

function FieldEditor({ activity }) {
  const req = activity.requirements || {};
  return <div className="connected-grid">
    <label>活动城市<input name="city" maxLength={100} defaultValue={req.city || ''} /></label>
    <label>活动场地<input name="venue" maxLength={200} defaultValue={activity.venue || ''} /></label>
    <label>活动日期<input name="event_date" type="date" defaultValue={req.event_date || ''} /></label>
    <label>参与人数<input name="participant_count" type="number" min="1" max="100000" defaultValue={req.participant_count || ''} /></label>
  </div>;
}

function readFields(form) {
  return Object.fromEntries(['city', 'venue', 'event_date', 'participant_count']
    .map(key => [key, form.get(key)?.trim()]).filter(([, value]) => value));
}

function DocumentReview({ report }) {
  const findings = [...report.program_checks, ...report.audit.findings];
  const execution = report.execution;
  return <section className="connected-panel"><h2>审核结果和执行清单</h2>
    <p>{report.audit.summary}</p>
    <h3>原文确认的信息</h3>
    <ul>{report.audit.facts.map((fact, index) => <li key={index}>{fact.label} · {fact.value}<small>（原文：{fact.source_quote}）</small></li>)}</ul>
    <h3>需要核对</h3>
    <ul>{findings.map((item, index) => <li key={index}><strong>{item.issue}</strong> {item.suggestion}</li>)}</ul>
    <h3>现场顺序</h3>
    <ol>{execution.run_sheet.map((line, index) => <li key={index}>{line}</li>)}</ol>
    <h3>建议安排</h3>
    <ul>{execution.tasks.map((task, index) => <li key={index}>{task.phase} · {task.task} · {task.suggested_owner} · {task.timing} · {task.deliverable}</li>)}</ul>
    {execution.organizer_questions.length > 0 && <><h3>需要你决定</h3><ul>{execution.organizer_questions.map(q => <li key={q}>{q}</li>)}</ul></>}
  </section>;
}

export function Documents({ activity, busy, submit }) {
  const allowed = new Set(activity.actions);
  if (!activity.documents?.length && !activity.tracking) return null;
  return <>
    {activity.documents?.length > 0 && <details className="connected-panel"><summary>查看已上传资料</summary>
      {activity.documents.map((item, index) => <article key={index}><h3>{item.file_name}</h3>
        <p>{item.warnings.join(' · ')}</p><pre className="connected-text">{item.text}</pre></article>)}
    </details>}
    {allowed.has('confirm_document') && <form className="connected-panel" onSubmit={event => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      submit('confirm_document', { fields: readFields(form), purpose: form.get('purpose') });
    }}>
      <h2>核对资料并选择怎么处理</h2><p>{activity.intake?.summary}</p>
      <FieldEditor activity={activity} />
      <label>处理方式<select name="purpose" defaultValue="activity">
        <option value="activity">审核已有活动方案</option><option value="travel">按资料继续策划团建</option>
      </select></label>
      <button className="button" disabled={busy}>确认并继续</button>
    </form>}
    {allowed.has('retry_document') && <button className="button" disabled={busy} onClick={() => submit('retry_document')}>重新读取资料</button>}
    {activity.review && <><DocumentReview report={activity.review} />
      <a className="button button-outline" href={'/api/customer/activities/' + encodeURIComponent(activity.id) + '/report'}>下载审核与执行清单</a>
    </>}
    {activity.review_history?.length > 0 && <details className="connected-panel"><summary>查看旧版审核记录</summary>
      {activity.review_history.map((report, index) => <DocumentReview report={report} key={index} />)}
    </details>}
    {allowed.has('review_document') && <form className="connected-panel" onSubmit={event => {
      event.preventDefault();
      submit('review_document', { note: new FormData(event.currentTarget).get('note') });
    }}>
      <label>补充给审核团队的信息<textarea name="note" maxLength={2000} /></label>
      <button className="button button-outline" disabled={busy}>{activity.review ? '补充后重新审核' : '重试审核'}</button>
    </form>}
    {allowed.has('receive_review') && <button className="button" disabled={busy} onClick={() => submit('receive_review')}>确认收到审核与执行清单</button>}
    {allowed.has('create_activity_order') && <form className="connected-panel" onSubmit={event => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      submit('create_activity_order', { fields: readFields(form), options: form.getAll('step') });
    }}>
      <h2>纳入活动现场管理</h2><FieldEditor activity={activity} />
      {Array.from(new Set(activity.review.execution.run_sheet)).map(step => <label key={step}>
        <input type="checkbox" name="step" value={step} defaultChecked /> {step}
      </label>)}
      <button className="button" disabled={busy}>确认这些安排并创建管理单</button>
    </form>}
    {activity.tracking && <Tracking activity={activity} allowed={allowed} busy={busy} submit={submit} />}
  </>;
}

function Tracking({ activity, allowed, busy, submit }) {
  const tracking = activity.tracking;
  const steps = tracking.active_schedule;
  const event = tracking.events.find(item => item.id === tracking.pending_id);
  const labels = { ready: '准备开始', running: '活动进行中', awaiting_agent: '团队正在处理',
    awaiting_owner: '等待老板决定', awaiting_organizer: '等待组织者确认', held: '活动暂停', finished: '活动已结束' };
  const buttons = {
    start_activity: '开始活动', next_activity_step: tracking.context_kind === 'travel' ? '推进模拟活动' :
      tracking.index + 1 === steps.length ? '标记活动结束' : '完成当前环节',
    retry_incident: '继续处理现场问题', confirm_incident: '确认调整并继续', refer_incident: '请公司重新核对',
  };
  return <section className="connected-panel"><h2>{tracking.context_kind === 'travel' ? '团建现场演示' : '活动现场'}</h2>
    <p>{labels[tracking.status] || tracking.status} · {tracking.order_id}</p>
    {tracking.status !== 'finished' && <p><strong>当前环节</strong> {steps[tracking.index]}</p>}
    {event && <article><h3>正在处理的问题</h3><p>{event.description}</p>
      {event.response && <><p>{event.response.summary}</p><p>{event.response.organizer_notice}</p><p>{event.response.cost_note}</p>
        <ul>{event.response.adjustments.map(item => <li key={item.schedule_index}>第 {item.schedule_index + 1} 项 · {item.replacement}</li>)}</ul></>}
    </article>}
    <div className="connected-actions">{Object.entries(buttons).filter(([action]) => allowed.has(action)).map(([action, label]) =>
      <button key={action} className="button button-outline" disabled={busy} onClick={() => submit(action)}>{label}</button>)}</div>
    {allowed.has('report_incident') && <form onSubmit={e => {
      e.preventDefault();
      const form = new FormData(e.currentTarget);
      submit('report_incident', { incident: form.get('incident'), note: form.get('note') });
    }}>
      <label>现场发生了什么<select name="incident">
        <option value="equipment">设备故障</option><option value="delay">环节延迟</option><option value="absence">关键人员缺席</option>
        <option value="member">成员需要休息</option><option value="weather">天气变化</option><option value="other">其他</option>
      </select></label>
      <label>补充说明<textarea name="note" maxLength={1000} /></label>
      <div className="connected-actions"><button className="button" disabled={busy}>上报现场变化</button>
        <button type="button" className="button button-outline" disabled={busy} onClick={() => submit('report_incident', { incident: 'equipment', simulated: true, note: '演示设备故障' })}>演示一次设备故障</button></div>
    </form>}
    <details><summary>完整活动顺序与现场记录</summary>
      <ol>{steps.map((line, index) => <li key={index}>{line}</li>)}</ol>
      <ul>{tracking.events.map(item => <li key={item.id}>{item.description} {item.response?.summary}</li>)}</ul>
    </details>
  </section>;
}

export function Offers() {
  const path = '/customer/marketing';
  const { data, error, replace } = useResource(path, customerApi);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  async function save(body, success) {
    setBusy(true);
    try {
      replace(await customerApi(path, { body }));
      setMessage(success);
    } catch (failure) {
      setMessage(failure.message);
    } finally {
      setBusy(false);
    }
  }
  return <main className="content-shell dashboard-shell"><h1 className="display-heading">咨询与模拟优惠</h1>
    {error && <p role="alert">{error.message}</p>}
    {data && <>{data.campaigns.map(item => <section className="connected-panel" key={item.id}>
      <h2>{item.title}</h2><p>{item.content}</p>
    </section>)}<form className="connected-panel" onSubmit={e => {
      e.preventDefault();
      const form = new FormData(e.currentTarget);
      save({ interest: form.get('interest'), campaign_id: form.get('campaign_id') || null }, '咨询已登记。');
    }}>
      <h2>登记团建咨询</h2>
      <label>从哪里知道咪游社<select name="campaign_id"><option value="">直接来咨询</option>
        {data.campaigns.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}
      </select></label>
      <label>想办什么活动<textarea name="interest" maxLength={1500} required /></label>
      <button className="button" disabled={busy}>登记咨询</button>
    </form>
      <section className="connected-panel"><h2>可领取的模拟优惠券</h2>
        <p>先登记咨询，领取后可在付模拟预付款时选择。优惠以订单条件核验结果为准。</p>
        {!data.policies.length && <p>当前没有启用的优惠券。</p>}
        {data.policies.map(policy => <div className="connected-actions" key={policy.id}>
          <strong>{policy.title} · {money(policy.face_cents)}</strong>
          <span>满 {money(policy.min_order_cents)} · {policy.audience} · 至 {policy.valid_until}</span>
          <button className="button button-outline" disabled={busy || data.coupons.some(item => item.policy_id === policy.id)}
            onClick={() => save({ policy_id: policy.id }, '模拟优惠券已领取。')}>领取</button>
        </div>)}
        <p>当前已领取 {data.coupons.filter(item => item.status === 'issued').length} 张可用券。</p>
      </section></>}
    {message && <p role="status">{message}</p>}
  </main>;
}

const ratings = {
  overall_rating: '总体满意度', novelty_rating: '新鲜感', culture_rating: '文化内容',
  detail_rating: '安排细节', engagement_rating: '团队参与感', feasibility_rating: '现场可执行性',
};

export function DeliveryFeedback({ submit, busy }) {
  return <form className="connected-panel" onSubmit={e => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const feedback = Object.fromEntries(Object.keys(ratings).map(key => [key, Number(form.get(key))]));
    submit('delivery_feedback', { feedback: { ...feedback, comment: form.get('comment'),
      incidents: form.get('incidents').split('\n').map(text => text.trim()).filter(Boolean) } });
  }}>
    <h2>评价实际体验</h2>
    <div className="connected-grid">{Object.entries(ratings).map(([name, label]) => <label key={name}>{label}
      <select name={name} defaultValue="4">{[1, 2, 3, 4, 5].map(n => <option key={n} value={n}>{n} 分</option>)}</select>
    </label>)}</div>
    <label>遇到的问题（每行一项）<textarea name="incidents" maxLength={2000} /></label>
    <label>其他意见<textarea name="comment" maxLength={2000} /></label>
    <button className="button" disabled={busy}>提交履约评价</button>
  </form>;
}
