import React, { useState } from 'react';
import { api, money, useResource } from '../../shared/api.js';
import { MarketingCampaigns } from './MarketingCampaigns.jsx';

const orderLink = key => '#/orders/' + encodeURIComponent(key);
const formatTime = time => time && new Date(time).toLocaleString('zh-CN', { hour12: false });
const stageNames = {
  in_progress: '处理中', plan_confirmed: '方案已确认', delivered: '已交付，待结算', completed: '已履约并结算',
};

export function taskStep(task) {
  return ({
    ProposalBrief: '准备活动方向', FeasibilityReview: '检查执行条件', ManagedPlan: '整理草案',
    CouncilDecision: '整理活动方案', IntakeResult: '整理客户信息',
    DocumentIntakeResult: '读取活动资料', ActivityAudit: '审核已有方案', ActivityExecutionPlan: '整理执行清单',
  })[task.expected_result || task.title.split('·').at(-1)?.trim()];
}

export function problemText(task) {
  const detail = task.detail || '';
  const step = taskStep(task) || '处理订单';
  if (task.status === 'interrupted') {
    return { title: step + '时中断了', reason: '这一步上次停在半途。打开订单可以查看已保存的进度。' };
  }
  const limit = detail.match(/超过\s*(\d+)\s*秒/);
  if (limit) {
    return { title: step + '等得太久', reason: '等了 ' + limit[1] + ' 秒，这一步仍没拿到结果。' };
  }
  if (/等待超时|处理取消或超时|处理超时/.test(detail)) {
    return { title: step + '等得太久', reason: '这一步在完成前已停止。打开订单可以从保存的步骤重试。' };
  }
  if (/Extra data|模型返回格式|没有可用的 JSON|多个不同的有效结果/.test(detail)) {
    return { title: step + '时没拿到可用结果', reason: '系统收到了回复，但没能把它整理成这一步所需的结果。' };
  }
  if (/缺少 LLM|模型配置缺失/.test(detail)) {
    return { title: step + '时连不上 AI', reason: 'AI 服务的连接信息还没配齐，请让技术同事核对。' };
  }
  return { title: step + '暂时没完成', reason: '现有记录暂时无法说明具体原因，可打开订单查看进度。' };
}

export function Exceptions({ tasks, orders }) {
  const [history, setHistory] = useState(false);
  const entries = tasks.map(task => {
    const order = orders.find(item => item.key === task.order_key);
    return { task, order, resumed: Boolean(task.recovered) };
  });
  const visible = entries.filter(entry => Boolean(entry.resumed) === history);
  return <>
    <div className="connected-actions">
      <button className={'btn ' + (history ? 'outline' : 'primary')} onClick={() => setHistory(false)}>待核对</button>
      <button className={'btn ' + (history ? 'primary' : 'outline')} onClick={() => setHistory(true)}>已继续的记录</button>
    </div>
    {!visible.length && <p className="muted">{history ? '暂无已继续的记录。' : '暂无待核对的异常。'}</p>}
    {visible.map(({ task, order, resumed }, index) => {
      const copy = problemText(task);
      return <article className="feature-card" key={task.order_key + ':' + index}>
        <h2>{copy.title}</h2>
        <p>{task.order_title} · {task.actor}{task.created_at && ' · ' + formatTime(task.created_at)}</p>
        <p>{copy.reason}</p>
        {order && <p><strong>{resumed ? '订单已继续' : '当前订单进度'}</strong> · {order.stage}</p>}
        {order && <a className="btn outline small" href={orderLink(order.key)}>查看这笔订单</a>}
        {task.detail && <details><summary>给技术同事看的记录</summary><p>{task.title} · {task.detail}</p></details>}
      </article>;
    })}
  </>;
}

export function Archive() {
  const { data, error, refresh } = useResource('/owner/archive');
  const [filter, setFilter] = useState('');
  const [stage, setStage] = useState('');
  const [kind, setKind] = useState('');
  const [hidden, setHidden] = useState(false);
  const [selected, setSelected] = useState('');
  const [search, setSearch] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState('');
  const path = '/owner/archive/' + encodeURIComponent(selected);
  const detail = useResource(selected ? path : null);
  const results = useResource(search ? '/owner/archive/search?' + search : null);
  const shown = (data || []).filter(item => item.hidden === hidden && (!stage || item.stage === stage) &&
    (!kind || item.kind === kind) && [item.title, item.id, item.city, item.display_number].join(' ').toLowerCase().includes(filter.trim().toLowerCase()));

  async function save(url, body, message) {
    setBusy(true);
    setNotice('');
    try {
      await api(url, { body });
      refresh();
      detail.refresh();
      results.refresh();
      setNotice(message);
      return true;
    } catch (failure) {
      setNotice(failure.message);
      return false;
    } finally { setBusy(false); }
  }

  function showDeleted(value) {
    setHidden(value);
    setSelected('');
    setConfirmRemove('');
    setNotice('');
  }

  async function changeArchive(item) {
    const restore = item.hidden;
    const saved = await save('/owner/archive/' + encodeURIComponent(item.id), { active: restore },
      restore ? '订单已恢复到历史订单。' : '订单已移到「已删除」，可随时恢复。');
    if (saved) {
      setSelected(current => current === item.id ? '' : current);
      setConfirmRemove('');
    }
  }

  return <>
    {(error || detail.error || results.error) && <p className="connected-alert" role="alert">{(error || detail.error || results.error).message}</p>}
    <div className="connected-actions">
      <button className={'btn ' + (hidden ? 'outline' : 'primary')} aria-pressed={!hidden} disabled={busy} onClick={() => showDeleted(false)}>历史订单</button>
      <button className={'btn ' + (hidden ? 'primary' : 'outline')} aria-pressed={hidden} disabled={busy} onClick={() => showDeleted(true)}>已删除</button>
    </div>
    <p className="muted">删除会把档案移到「已删除」，可以恢复。进行中的订单仍可在「订单与风险」处理。</p>
    {notice && <p role="status">{notice}</p>}
    <div className="feature-form">
      <label>查订单<input value={filter} placeholder="标题、编号或城市" onChange={e => setFilter(e.target.value)} /></label>
      <label>订单类型<select value={kind} onChange={e => setKind(e.target.value)}>
        <option value="">全部</option><option value="case">策划或活动</option><option value="supplier">酒店餐饮</option>
      </select></label>
      <label>履约阶段<select value={stage} onChange={e => setStage(e.target.value)}>
        <option value="">全部</option>{Object.entries(stageNames).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
      </select></label>
    </div>
    {!shown.length && <p className="muted">{data ? '没有符合条件的订单。' : '正在读取档案…'}</p>}
    <ul className="connected-tasks">{shown.map(item => <li key={item.id}>
      <div className="archive-row-actions">
        <button className="btn outline small" onClick={() => setSelected(item.id)}>{item.title}</button>
        {hidden ? (item.hidden_by_case ? <span className="muted">请先恢复关联的团建订单</span> :
          <button className="btn outline small" disabled={busy} onClick={() => changeArchive(item)}>恢复</button>) :
          confirmRemove !== item.id && <button className="btn outline small" disabled={busy}
            onClick={() => setConfirmRemove(item.id)}>删除</button>}
      </div>
      <p className="order-number">{item.display_number || item.source_id} · {item.city || '地点待确认'} · {stageNames[item.stage]} · {item.outcome}</p>
      {confirmRemove === item.id && <div className="archive-row-actions">
        <span>确定把这笔订单移到「已删除」？</span>
        <button className="btn primary small" disabled={busy} onClick={() => changeArchive(item)}>确认删除</button>
        <button className="btn outline small" disabled={busy} onClick={() => setConfirmRemove('')}>取消</button>
      </div>}
    </li>)}</ul>
    {detail.data && <section className="feature-card">
      <h2>{detail.data.title}</h2>
      {detail.data.display_number && <p className="order-number">{detail.data.display_number} · 原编号 {detail.data.source_id}</p>}
      <div className="connected-actions">
        <a className="btn primary" href={'/api' + path + '?download=true'}>下载完整档案</a>
        <a className="btn outline" href={orderLink(detail.data.kind === 'supplier' && detail.data.snapshot.case_id
          ? 'case:' + detail.data.snapshot.case_id : detail.data.id)}>在工作台查看</a>
      </div>
      <details><summary>查看完整档案内容</summary><pre className="feature-text">{JSON.stringify(detail.data.snapshot, null, 2)}</pre></details>
      <details><summary>管理检索用的记录片段</summary>
        {detail.data.fragments.map(item => <article className="feature-card" key={item.id}>
          <p>{item.text}</p><small>{item.path}</small>
          <button className="btn outline small" disabled={busy}
            onClick={() => save('/owner/archive/fragments/' + encodeURIComponent(item.id), { active: !item.active }, '检索设置已保存。')}>
            {item.active ? '停用片段' : '重新启用片段'}
          </button>
        </article>)}
      </details>
    </section>}
    <form className="feature-card feature-form" onSubmit={e => {
      e.preventDefault();
      const form = new FormData(e.currentTarget);
      const q = String(form.get('q')).trim();
      if (!q) return;
      setSearch(new URLSearchParams({ q, city: form.get('city') }).toString());
      results.refresh();
    }}>
      <h2>找历史经验</h2>
      <label>关键词<input name="q" required placeholder="例如：雨天备选" /></label>
      <label>城市（可选）<input name="city" /></label>
      <div className="connected-actions"><button className="btn primary">检索相关片段</button>
        <button className="btn outline" type="button" disabled={busy} onClick={() => save('/owner/archive/index', {}, '历史索引已同步。')}>重新同步索引</button></div>
    </form>
    {results.data && <><p>{results.data.notice || results.data.backend}</p>
      {!results.data.rows.length && <p>没有找到相关记录。</p>}
      <ul className="connected-tasks">{results.data.rows.map(item => <li key={item.id}>
        <p>{item.text}</p><button className="btn outline small" onClick={() => setSelected(item.document_id)}>打开来源档案</button>
      </li>)}</ul>
    </>}
  </>;
}

export function Lessons() {
  const { data, error, refresh } = useResource('/owner/lessons');
  const [message, setMessage] = useState('');
  return <>
    {error && <p role="alert">{error.message}</p>}
    {message && <p role="status">{message}</p>}
    {!data?.length && <p>暂无可复用的反馈经验。</p>}
    {data?.map(lesson => <article className="feature-card" key={lesson.id}>
      <h2>{lesson.title}</h2><p>{lesson.action}</p>
      {lesson.evidence?.length > 0 && <details><summary>客户反馈依据</summary><ul>
        {lesson.evidence.map((line, index) => <li key={index}>{line}</li>)}</ul></details>}
      <p>来源订单 {lesson.case_id || lesson.source_case_id || '—'} · 已参考 {lesson.supplied_cases || 0} 次</p>
      <button className="btn outline small" onClick={async () => {
        try {
          await api('/owner/lessons/' + encodeURIComponent(lesson.id), { body: { active: !lesson.active } });
          refresh();
          setMessage('经验设置已保存。');
        } catch (failure) { setMessage(failure.message); }
      }}>{lesson.active ? '停用经验' : '启用经验'}</button>
    </article>)}
  </>;
}

const yuanToCents = value => Math.round(Number(value) * 100);

function OperationForm({ title, fields, action, busy, save, button = '保存' }) {
  return <form className="feature-card feature-form" onSubmit={e => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const body = { action };
    for (const [name, , type] of fields) {
      const value = form.get(name);
      body[name] = type === 'money' ? yuanToCents(value) : type === 'number' ? Number(value) : value || (name.endsWith('_id') ? null : '');
    }
    save(body);
  }}>
    <h2>{title}</h2>
    {fields.map(([name, label, type, options]) => <label key={name}>{label}
      {options ? <select name={name} required={!name.endsWith('_id')}>{options.map(([value, caption]) =>
        <option key={value} value={value}>{caption}</option>)}</select>
        : type === 'textarea' ? <textarea name={name} maxLength={3000} required />
          : <input name={name} type={type === 'money' ? 'number' : type}
              step={type === 'money' ? '0.01' : type === 'number' ? '1' : undefined}
              min={type === 'money' ? 0 : type === 'number' ? 1 : undefined} required />}
    </label>)}
    <button className="btn primary" disabled={busy}>{button}</button>
  </form>;
}

const operationTabs = { ledger: '经营记录', research: '市场调研', campaigns: '获客活动', coupons: '优惠券' };

export function Operations({ activeTab, marketing, setMarketing }) {
  const { data, error, refresh } = useResource('/owner/operations');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const tab = Object.hasOwn(operationTabs, activeTab) ? activeTab : 'ledger';
  async function save(body) {
    setBusy(true);
    setMessage('');
    try {
      await api('/owner/operations', { body });
      refresh();
      setMessage('操作已保存。');
    } catch (failure) { setMessage(failure.message); }
    finally { setBusy(false); }
  }
  const options = (items, label) => items.map(item => [item.id, item[label] || item.id]);
  return <>
    <p>市场报告、推广支出和优惠均用于模拟经营。</p>
    <nav className="connected-actions operations-tabs" aria-label="公司运营">{Object.entries(operationTabs).map(([key, label]) =>
      <a className={'btn ' + (tab === key ? 'primary' : 'outline')} key={key} href={key === 'ledger' ? '#/operations' : '#/operations/' + key}
        aria-current={tab === key ? 'page' : undefined}>{label}</a>)}</nav>
    {tab === 'campaigns' && <MarketingCampaigns marketing={marketing} setMarketing={setMarketing} />}
    {!data && <p role={error ? 'alert' : 'status'}>{error?.message || '正在读取公司记录…'}</p>}
    {message && <p role="status">{message}</p>}
    {data && tab === 'ledger' && <><p>咨询 {data.summary.leads} 个 · 已付 {data.summary.paid_orders} 笔 · 已结清 {data.summary.completed_orders} 笔</p>
      <p>模拟余额 {money(data.summary.finance.cash_cents)} · 已确认净服务费 {money(data.summary.service_income_cents)}</p>
      {data.summary.campaigns.map((item, index) => <p key={index}>{item['活动']} · {item['潜客数']} 个咨询 · {item['成交客户数']} 位成交</p>)}
      <details><summary>查看模拟现金流水</summary><ul>{data.summary.ledger.map(row =>
        <li key={row.id}>{row.purpose} · {row.direction === 'in' ? '收入' : '支出'} {money(row.cents)}</li>)}</ul></details></>}
    {data && tab === 'research' && <>
      <OperationForm title="生成固定演示报告" fields={[[ 'title', '调研主题', 'text' ]]} action="fixed_research" busy={busy} save={save} button="生成报告" />
      <OperationForm title="让 AI 生成模拟调研报告" fields={[[ 'title', '调研主题', 'text' ]]} action="model_research" busy={busy} save={save} button={busy ? '正在处理…' : '生成 AI 报告'} />
      {data.research.map(item => <details className="feature-card" key={item.id}>
        <summary>{item.topic || item.id}</summary><p>{item.report?.summary}</p>
        <ul>{item.report?.competitors.map(entry => <li key={entry.name}>{entry.name} · {entry.audience} · {entry.services.join('、')} · {entry.billing}</li>)}</ul>
        <p>{item.report?.opportunities.join('；')}</p>
      </details>)}
    </>}
    {data && tab === 'campaigns' && <details className="feature-card">
      <summary>手动创建与历史获客活动</summary>
      <OperationForm title="创建获客活动草案" fields={[[ 'title', '活动名称', 'text' ], ['audience', '面向人群', 'text'],
        ['channel', '渠道', 'text'], ['amount', '模拟预算（元）', 'money'], ['content', '活动内容', 'textarea'],
        ['research_id', '参考报告', 'text', [['', '不指定'], ...options(data.research, 'topic')]]]} action="create_campaign" busy={busy} save={save} />
      {data.campaign.map(item => <article className="feature-card" key={item.id}><h2>{item.title}</h2>
        <p>{item.audience} · {item.channel} · {money(item.budget_cents)} · {item.status === 'active' ? '已启用' : '草案'}</p>
        <p>{item.content}</p>
        {item.status !== 'active' && <button className="btn primary small" disabled={busy} onClick={() => save({ action: 'activate_campaign', id: item.id })}>批准并启用</button>}
      </article>)}
      {data.campaign.some(item => item.status === 'active') && <OperationForm title="登记模拟推广支出"
        fields={[['id', '活动', 'text', options(data.campaign.filter(item => item.status === 'active'), 'title')],
          ['amount', '支出（元）', 'money'], ['reference', '唯一凭证编号', 'text']]}
        action="campaign_spend" busy={busy} save={save} />}
      <details><summary>查看已登记的咨询</summary><ul>{data.lead.map(item => <li key={item.id}>{item.id} · {item.interest}</li>)}</ul></details>
    </details>}
    {data && tab === 'coupons' && <>
      <OperationForm title="创建模拟优惠政策" fields={[[ 'title', '名称', 'text' ], ['amount', '优惠金额（元）', 'money'],
        ['minimum', '使用门槛（元）', 'money'], ['start', '开始日期', 'date'], ['end', '截止日期', 'date'],
        ['audience', '客群', 'text', ['全部', '首单', '老客'].map(v => [v, v])], ['limit', '发放上限', 'number'],
        ['campaign_id', '来源活动', 'text', [['', '不限'], ...options(data.campaign, 'title')]]]}
        action="create_policy" busy={busy} save={save} button="批准并启用优惠政策" />
      {data.coupon_policy.map(item => <article className="feature-card" key={item.id}><h2>{item.title}</h2>
        <p>{money(item.face_cents)} · {item.valid_from} 至 {item.valid_until} · {item.audience}</p>
        <button className="btn outline small" disabled={busy}
          onClick={() => save({ action: 'toggle_policy', id: item.id, active: !item.active })}>{item.active ? '停用优惠' : '启用优惠'}</button>
      </article>)}
      {!!data.lead.length && data.coupon_policy.some(item => item.active) &&
        <OperationForm title="给已登记客户发券" fields={[['id', '优惠政策', 'text', options(data.coupon_policy.filter(item => item.active), 'title')],
          ['reference', '客户', 'text', options(data.lead, 'interest')]]} action="issue_coupon" busy={busy} save={save} button="发放模拟优惠券" />}
      <details><summary>查看已发放的券</summary><ul>{data.coupon.map(item =>
        <li key={item.id}>{item.id} · {item.status === 'redeemed' ? '已使用' : '已领取'}</li>)}</ul></details>
    </>}
  </>;
}

export function OrderExtras({ order }) {
  const tracking = order.tracking;
  const event = tracking?.events.find(item => item.id === tracking.pending_id);
  return <>
    {order.attachments?.length > 0 && <details><summary>参考附件读取结果</summary>
      {order.attachments.map((item, index) => <p key={index}>{item.file_name} · {item.summary}</p>)}
    </details>}
    {order.documents?.length > 0 && <details><summary>客户上传的资料</summary>
      {order.documents.map((item, index) => <article key={index}><h3>{item.file_name}</h3>
        <pre className="feature-text">{item.text}</pre></article>)}</details>}
    {order.intake && <details><summary>资料提取与确认</summary><p>{order.intake.summary}</p></details>}
    {order.review && <details><summary>审核结果和执行清单</summary><p>{order.review.audit.summary}</p>
      <ol>{order.review.execution.run_sheet.map((step, index) => <li key={index}>{step}</li>)}</ol></details>}
    {tracking && <section className="feature-card"><h3>现场跟进</h3>
      <p>{tracking.status === 'finished' ? '活动已结束' : tracking.active_schedule[tracking.index]}</p>
      {event && <><p>{event.description}</p><p>{event.response?.summary}</p><p>{event.response?.cost_note}</p></>}
      <details><summary>现场记录</summary><ul>{tracking.events.map(item =>
        <li key={item.id}>{item.description} {item.response?.summary}</li>)}</ul></details>
    </section>}
    {order.plan_feedback && <p><strong>方案评价</strong> · {({
      satisfied: '满意', acceptable: '可以接受', unacceptable: '希望重做',
    })[order.plan_feedback.outcome]} {order.plan_feedback.comment}</p>}
    {order.delivery_feedback && <p><strong>履约评价</strong> · 总体 {order.delivery_feedback.overall_rating}/5 {order.delivery_feedback.comment}</p>}
  </>;
}
