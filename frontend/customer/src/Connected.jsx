import React, { useState } from 'react';
import { money, useResource } from '../../shared/api.js';
import { activityPath, customerApi, planUrl, workspaceUrl } from './api.js';
import { DeliveryFeedback, Documents } from './Features.jsx';

const stages = {
  new: '准备策划', intake: '整理需求', waiting_requirements: '待确认需求',
  waiting_draft: '待确认草案', waiting_customer: '请补充信息', supplementing: '核对补充信息',
  alignment: '核对需求', creative_council: '正在策划', waiting_style_feedback: '请确认活动方向',
  cross_review: '核对执行条件', deliberation: '整理草案', reviewing: '最终检查',
  waiting_owner: '等待老板处理', ready_for_customer: '待确认最终方案',
  needs_rework: '待重新策划', completed: '方案已确认', failed: '处理未完成',
};
const bookingStages = {
  inquiring: '待选择报价', waiting_customer: '待确认预订条款',
  customer_confirmed: '待付模拟预付款', paid: '等待老板处理预订',
  confirmed: '已取得模拟回执', delivered: '等待老板结算', completed: '订单已结清',
};
const buttonLabels = {
  start: '确认需求并开始策划', retry: '继续处理', confirm_direction: '确认方向，查看草案',
  confirm_draft: '确认草案，进入最终检查', answer: '提交补充信息',
  question: '提交质疑', revise_direction: '提交修改', accept_plan: '提交方案确认',
  rework: '继续生成新方案', request_quotes: '查询本团建的报价',
  refresh_quotes: '重新查询报价', select_quotes: '确认所选报价',
  confirm_contract: '确认预订条款', pay_deposit: '支付模拟预付款',
  confirm_schedule: '确认更新后的行程', confirm_delivery: '确认交付并支付模拟尾款',
  supplement: '提交补充需求', start_travel_demo: '开始团建现场演示',
};

export function ActivityList({ draft, onStart }) {
  const { data, error, refresh } = useResource('/customer/activities', customerApi);
  return <main className="content-shell activities-shell">
    <div className="section-title-row"><div><p className="eyebrow">每一次相聚，都有迹可循</p>
      <h1 className="display-heading">我的活动</h1></div>
      <button className="button" onClick={onStart}>{draft ? '继续填写或创建活动' : '创建活动'}</button></div>
    {error && <p role="alert">{error.message} <button onClick={refresh}>重试</button></p>}
    {!data && !error && <p role="status">正在读取活动…</p>}
    {data?.length === 0 && <p>还没有提交过活动。</p>}
    <div className="connected-grid">{data?.map(item =>
      <a className="connected-panel activity-link" key={item.id} href={workspaceUrl(item.id)}>
        <h2>{item.title}</h2><p>{bookingStages[item.booking_status] || stages[item.status] || item.status}</p><small>{item.id}</small>
      </a>)}</div>
  </main>;
}

export function ActivityWorkspace({ id, planId, planList }) {
  const path = activityPath(id);
  const { data: activity, error, refresh, replace } = useResource(path, customerApi, 2200);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState('');

  async function submit(action, input = {}) {
    if (busy) return;
    setBusy(true);
    setActionError('');
    try {
      replace(await customerApi(path + '/actions', {
        body: { action, revision: activity.revision, order_revision: activity.booking?.revision,
          signature: activity.booking?.contract?.signature, ...input },
      }));
      refresh();
    } catch (problem) {
      setActionError(problem.message);
      refresh();
    } finally {
      setBusy(false);
    }
  }

  if (!activity) return <main className="content-shell dashboard-shell">
    <p role={error ? 'alert' : 'status'}>{error?.message || '正在读取活动…'}</p>
    {error && <button className="button" onClick={refresh}>重试</button>}
    <a href="/activities">返回我的活动</a>
  </main>;

  const plan = activity.plans.find(item => item.id === planId);
  const workspace = workspaceUrl(id);
  const currentPlanHref = activity.plans.length === 1 ? planUrl(id, activity.plans[0].id) : workspace;
  const isWorkspace = !planId && !planList;
  const booking = activity.booking;
  const allowed = new Set(activity.actions);
  const button = (action, input) => <button className="button" disabled={busy || activity.running}
    onClick={() => submit(action, input)}>{buttonLabels[action]}</button>;

  return <main className="content-shell dashboard-shell">
    <div className="breadcrumb"><a href="/activities">我的活动</a><span> / </span><a href={workspace}>AI 团队工作台</a></div>
    <p className="eyebrow">{booking ? bookingStages[booking.status] || booking.status :
      activity.service_mode === 'activity_review' && activity.status === 'completed' ? '审核清单已收到' : stages[activity.status]}</p>
    <h1 className="display-heading">{plan?.title || activity.title}</h1>
    {activity.requirements && <p>{activity.requirements.city} · {activity.requirements.event_date} ·
      {' '}{activity.requirements.participant_count} 人 · {activity.requirements.duration} ·
      {' '}总预算 {money(activity.requirements.budget_yuan * 100)}</p>}
    <p className="connected-case-id">案件编号 {activity.id}</p>
    {(actionError || error || activity.error) && <p className="connected-error" role="alert">{actionError || error?.message || activity.error}</p>}
    {activity.running && <p role="status">AI 团队正在处理。进度会自动更新。</p>}
    {activity.status === 'waiting_style_feedback' && activity.message && <p>{activity.message}</p>}
    {!isWorkspace && (allowed.has('request_quotes') || booking) && <div className="connected-actions">
      <a className="button" href={workspace}>{booking ? '查看预订和付款进度' : '方案已确认，查询报价'}</a>
    </div>}
    {booking?.status === 'paid' && <p className="connected-panel">模拟预付款已收到。接下来由老板授权供方定金并提交预订。</p>}
    {planId && !plan && <div className="connected-panel">
      <p>{activity.plans.length ? '该方案版本已更新。' : '当前暂无可查看的方案。'}</p>
      <a className="button button-outline plan-detail-link" href={currentPlanHref}>
        {activity.plans.length ? '查看当前方案' : '返回 AI 团队工作台'} <span aria-hidden="true">→</span>
      </a>
    </div>}
    {plan && <Plan plan={plan} />}
    {!planId && <div className="connected-grid">
      {activity.plans.map(item => <article className="connected-panel" key={item.id}>
        <h2>{item.title}</h2><p>{item.summary}</p>
        <a className="button button-outline plan-detail-link" href={planUrl(id, item.id)}
          aria-label={'查看方案详情：' + item.title}>查看方案详情 <span aria-hidden="true">→</span></a>
      </article>)}
    </div>}
    {activity.questions.length > 0 && <ul>{activity.questions.map(question => <li key={question}>{question}</li>)}</ul>}
    <div className="connected-actions">
      {['start', 'retry', 'confirm_direction', 'confirm_draft', 'rework'].filter(action => allowed.has(action))
        .map(action => <React.Fragment key={action}>{button(action)}</React.Fragment>)}
    </div>
    {['answer', 'question', 'revise_direction', 'supplement'].filter(action => allowed.has(action))
      .map(action => <NoteAction key={action} action={action} busy={busy} submit={submit} />)}
    {allowed.has('accept_plan') && <PlanConfirmation busy={busy} submit={submit} options={activity.feedback_options} />}
    {isWorkspace && allowed.has('request_quotes') && <BookingRequest key={activity.id} activity={activity} busy={busy} submit={submit} />}
    {isWorkspace && booking && <Booking key={booking.id + ':' + booking.revision}
      caseId={activity.id} booking={booking} allowed={allowed} schedule={activity.updated_schedule} button={button} busy={busy} submit={submit} />}
    {isWorkspace && <Documents activity={activity} busy={busy} submit={submit} />}
    {isWorkspace && activity.attachments?.length > 0 && <details className="connected-panel">
      <summary>查看参考附件读取结果</summary>
      {activity.attachments.map((item, index) => <p key={index}>{item.file_name} · {item.summary}</p>)}
    </details>}
    {isWorkspace && allowed.has('delivery_feedback') && <DeliveryFeedback busy={busy} submit={submit} />}
    {isWorkspace && activity.delivery_feedback && <p className="connected-panel">履约评价已提交。谢谢你的反馈。</p>}
    {isWorkspace && <section className="connected-panel"><h2>团队动态</h2>
      {activity.updates.length === 0 && <p>团队动态将在策划开始后显示。</p>}
      <ul>{activity.updates.map((update, index) => <li key={update.timestamp + index}>
        <strong>{update.actor} · {update.stage}</strong> {update.message}
      </li>)}</ul>
    </section>}
  </main>;
}

function Plan({ plan }) {
  return <section className="connected-panel"><p>{plan.summary}</p>
    {plan.highlights.length > 0 && <ul>{plan.highlights.map((item, index) => <li key={index}>{item}</li>)}</ul>}
    {plan.schedule.length > 0 && <><h2>行程安排</h2><ol>{plan.schedule.map((line, index) => <li key={index}>{line}</li>)}</ol></>}
  </section>;
}

function NoteAction({ action, busy, submit }) {
  const [note, setNote] = useState('');
  const label = buttonLabels[action];
  return <form className="connected-panel" onSubmit={event => {
    event.preventDefault();
    submit(action, { note });
  }}>
    <label>{label}<textarea required value={note} maxLength={2000} onChange={event => setNote(event.target.value)} /></label>
    <button className="button button-outline" disabled={busy || !note.trim()}>{label}</button>
  </form>;
}

function PlanConfirmation({ busy, submit, options }) {
  const [outcome, setOutcome] = useState('satisfied');
  const [note, setNote] = useState('');
  return <form className="connected-panel" onSubmit={event => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    submit('accept_plan', { outcome, note, options: form.getAll('reason') });
  }}>
    <h2>确认最终方案</h2>
    <label>你的评价<select value={outcome} onChange={event => setOutcome(event.target.value)}>
      <option value="satisfied">满意，确认方案</option><option value="acceptable">可以接受</option>
      <option value="unacceptable">需要重新策划</option>
    </select></label>
    <div key={outcome}>{options?.[outcome]?.map(item => <label key={item.id}>
      <input type="checkbox" name="reason" value={item.id} /> {item.label}</label>)}</div>
    <label>补充意见<textarea maxLength={2000} value={note} onChange={event => setNote(event.target.value)} /></label>
    <button className="button" disabled={busy}>{buttonLabels.accept_plan}</button>
  </form>;
}

function BookingRequest({ activity, busy, submit }) {
  const [meals, setMeals] = useState(['午餐']);
  const [hotel, setHotel] = useState(activity.requirements?.duration?.includes('晚') || false);
  const [men, setMen] = useState('');
  const [women, setWomen] = useState('');
  const [grade, setGrade] = useState('2');
  const [dietary, setDietary] = useState('');
  const people = activity.requirements?.participant_count;
  return <form className="connected-panel" onSubmit={event => {
    event.preventDefault();
    submit('request_quotes', { meal_names: meals, include_hotel: hotel, men: Number(men),
      women: Number(women), grade: Number(grade), dietary });
  }}>
    <h2>酒店餐饮预订</h2>
    <p>沿用方案的日期和人数。报价覆盖所选餐饮与住宿，场地和交通另计。</p>
    <div className="connected-actions">{['早餐', '午餐', '晚餐'].map(meal => <label key={meal}>
      <input type="checkbox" checked={meals.includes(meal)} onChange={event => setMeals(event.target.checked ? [...meals, meal] : meals.filter(value => value !== meal))} /> {meal}
    </label>)}</div>
    <label><input type="checkbox" checked={hotel} onChange={event => setHotel(event.target.checked)} /> 同时预订住宿</label>
    {hotel && <div className="connected-grid">
      <label>男性住宿人数<input required type="number" min="0" max={people} value={men} onChange={event => setMen(event.target.value)} /></label>
      <label>女性住宿人数<input required type="number" min="0" max={people} value={women} onChange={event => setWomen(event.target.value)} /></label>
      <label>住宿标准<select value={grade} onChange={event => setGrade(event.target.value)}>
        <option value="1">经济型双床</option><option value="2">舒适型双床</option><option value="3">高品质双床</option>
      </select></label>
    </div>}
    <label>口味与忌口<textarea maxLength={1000} value={dietary} onChange={event => setDietary(event.target.value)} /></label>
    <button className="button" disabled={busy || !meals.length || (hotel && Number(men) + Number(women) !== people)}>{buttonLabels.request_quotes}</button>
  </form>;
}

function Booking({ caseId, booking, allowed, schedule, button, busy, submit }) {
  const [selected, setSelected] = useState(booking.selected_quote_ids);
  const [conditions, setConditions] = useState([]);
  const [coupon, setCoupon] = useState('');
  const { data: offers } = useResource(
    allowed.has('pay_deposit') ? '/customer/marketing?case_id=' + encodeURIComponent(caseId) : null, customerApi);
  const coupons = (offers?.coupons || []).filter(item => item.status === 'issued' &&
    offers.policies.some(policy => policy.id === item.policy_id));
  const contract = booking.contract;
  const toggle = (values, key) => values.includes(key) ? values.filter(value => value !== key) : [...values, key];
  return <section className="connected-panel"><h2>预订与交付</h2>
    <p>{bookingStages[booking.status] || booking.status} · {booking.id}</p>
    {allowed.has('refresh_quotes') && button('refresh_quotes')}
    {allowed.has('select_quotes') && <fieldset><legend>选择可用报价，需覆盖全部餐次</legend>
      {booking.quotes.map(quote => <label className="connected-option" key={quote.id}>
        <input type="checkbox" disabled={!quote.available} checked={selected.includes(quote.id)} onChange={() => setSelected(toggle(selected, quote.id))} />
        <span><strong>{quote.supplier_name} · {money(quote.total_cents)}</strong>
          <small>{[...quote.meal_keys, ...quote.details, ...quote.blockers].join(' · ')}</small></span>
      </label>)}
      <button className="button button-outline" disabled={busy || !selected.length}
        onClick={() => submit('select_quotes', { options: selected })}>{buttonLabels.select_quotes}</button>
    </fieldset>}
    {contract && <p>合同总额 {money(contract.total_cents)} · 已模拟支付 {money(booking.customer_paid_cents)}</p>}
    {allowed.has('confirm_contract') && <fieldset><legend>确认所选合同与条件</legend>
      {Object.entries(contract.conditions).map(([key, text]) => <label className="connected-option" key={key}>
        <input type="checkbox" checked={conditions.includes(key)} onChange={() => setConditions(toggle(conditions, key))} />{text}
      </label>)}
      <button className="button" disabled={busy || conditions.length !== Object.keys(contract.conditions).length}
        onClick={() => submit('confirm_contract', { options: conditions })}>{buttonLabels.confirm_contract}</button>
    </fieldset>}
    {allowed.has('pay_deposit') && <div className="connected-actions">
      {coupons.length > 0 && <label>模拟优惠券<select value={coupon} onChange={event => setCoupon(event.target.value)}>
        <option value="">不使用</option>
        {coupons.map(item => <option key={item.id} value={item.id}>
          {offers.policies.find(policy => policy.id === item.policy_id)?.title}</option>)}
      </select></label>}
      <span>{coupon ? '优惠由系统核验并重新计算预付款' : '模拟预付款 ' + money(contract.deposit_cents)}</span>
      {button('pay_deposit', { coupon_id: coupon || null })}
    </div>}
    {booking.receipts.length > 0 && <><h3>模拟预订回执</h3><ul>{booking.receipts.map(receipt =>
      <li key={receipt.id}>{receipt.supplier} · {receipt.id} · {money(receipt.amount_cents)}</li>)}</ul></>}
    {allowed.has('confirm_schedule') && <><h3>请确认更新后的执行行程</h3><ol>{schedule.map((line, index) => <li key={index}>{line}</li>)}</ol>{button('confirm_schedule')}</>}
    {allowed.has('start_travel_demo') && <div className="connected-actions">
      <button className="button button-outline" disabled={busy} onClick={() => submit('start_travel_demo')}>
        {buttonLabels.start_travel_demo}
      </button>
    </div>}
    {allowed.has('confirm_delivery') && <div className="connected-actions"><span>模拟尾款 {money(contract.balance_cents)}</span>{button('confirm_delivery')}</div>}
  </section>;
}
