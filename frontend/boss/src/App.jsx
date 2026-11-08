import React, { useEffect, useState } from 'react';
import {
  ArrowLeft, ArrowRight, Bell, ChartNoAxesCombined, ChevronRight, CircleX, ClipboardCheck,
  Coins, House, Layers, Menu, RefreshCw, Search, ShieldCheck, TriangleAlert, UserRound, UsersRound,
} from 'lucide-react';
import { api, money, useResource } from '../../shared/api.js';
import { Archive, Exceptions, Lessons, Operations, OrderExtras, problemText, taskStep } from './Features.jsx';
import './connected.css';

const navigation = [
  ['', '经营总览', House], ['approvals', '待审批', ClipboardCheck], ['orders', '订单与风险', Layers],
  ['team', 'AI 员工', UsersRound], ['review', '经营复盘', ChartNoAxesCombined],
  ['errors', '运行异常', CircleX], ['archive', '历史订单', Layers],
  ['lessons', '反馈经验', ClipboardCheck], ['operations', '公司运营', Coins],
  ['rules', '公司制度', ShieldCheck],
];
const actionLabels = {
  approve_plan: '批准方案', reject_plan: '退回方案', authorize_deposit: '授权模拟供方定金',
  submit_booking: '提交模拟预订并获取回执', settle: '授权结清供方并完成本单',
  retry: '从这里继续处理', retry_document: '重新读取资料', review_document: '继续审核活动方案',
  retry_incident: '继续处理现场问题', approve_incident: '批准调整，交组织者确认',
  hold_incident: '暂不批准，保持暂停', reopen_incident: '重新核查暂停事件',
};
const taskLabels = { running: '进行中', completed: '已完成', failed: '失败', interrupted: '中断' };
const exception = task => ['failed', 'interrupted'].includes(task.status);
const orderPath = key => '/owner/orders/' + encodeURIComponent(key);
const orderHref = key => '#/orders/' + encodeURIComponent(key);

function readPage() {
  try {
    const [section, key] = location.hash.replace(/^#\/?/, '').split('/').map(decodeURIComponent);
    return { section, key: section === 'orders' ? key : null };
  } catch {
    return { section: '', key: null };
  }
}

export function App() {
  const [page, setPage] = useState(readPage);
  const [session, setSession] = useState(0);
  const [mobile, setMobile] = useState(false);
  const dashboard = useResource('/owner/dashboard?session=' + session, api, 2500);
  useEffect(() => {
    function navigate() { setPage(readPage()); setMobile(false); }
    window.addEventListener('hashchange', navigate);
    return () => window.removeEventListener('hashchange', navigate);
  }, []);

  if (dashboard.error?.status === 401) return <Login onLogin={() => setSession(value => value + 1)} />;
  const data = dashboard.data;
  const pending = data?.orders.filter(order => order.needs_owner) || [];
  const problems = data?.tasks.filter(exception) || [];
  const title = page.key ? '订单详情' : navigation.find(([section]) => section === page.section)?.[1]
    || ({ errors: '运行异常', logs: '工作日志', notifications: '消息中心', settings: '工作台设置' })[page.section];

  return <div className="app-shell">
    <aside className={'sidebar ' + (mobile ? 'mobile-open' : '')}>
      <a className="brand" href="#/"><strong>咪游社</strong><span>AI 团建事务所</span></a>
      <nav aria-label="主导航">{navigation.map(([section, label, Icon]) =>
        <a key={section} href={'#/' + section} className={'nav-item ' + (page.section === section ? 'selected' : '')}
          aria-current={page.section === section ? 'page' : undefined}>
          <Icon size={23} strokeWidth={1.65} /><span>{label}</span>{section === 'approvals' && pending.length > 0 && <i>{pending.length}</i>}
        </a>)}</nav>
      <div className="sidebar-bottom"><span className="demo-label"><span className="demo-dot" />本地演示工作空间</span>
        <a className="profile" href="#/settings"><span className="avatar"><UserRound size={25} /></span><div><strong>老板</strong><small>工作台设置</small></div></a></div>
    </aside>
    {mobile && <button className="mobile-scrim" aria-label="关闭导航" onClick={() => setMobile(false)} />}
    <main className="workspace">
      <header className="topbar">
        <button className="icon-btn mobile-menu" onClick={() => setMobile(true)} aria-label="打开导航"><Menu /></button>
        <div className="topbar-right">
          <button className="icon-btn" onClick={dashboard.refresh} aria-label="刷新"><RefreshCw size={20} /></button>
          <a className="icon-btn" href="#/notifications" aria-label="消息中心"><Bell size={22} /></a>
          <a className="btn outline small" href="/">打开用户端</a>
        </div>
      </header>
      {dashboard.error && <p className="connected-alert" role="alert">{dashboard.error.message}</p>}
      {!data && !dashboard.error && <p role="status">正在读取经营记录…</p>}
      {data && (page.section === '' ? <Overview data={data} pending={pending} problems={problems} /> :
        <section className="panel connected-page">
          <div className="panel-heading"><h1>{title || '工作台'}</h1></div>
          {page.section === 'approvals' && <OrderList rows={pending} empty="当前没有待老板处理的订单。" />}
          {page.section === 'orders' && (page.key ? <OrderDetail key={page.key} orderKey={page.key} refreshDashboard={dashboard.refresh} />
            : <OrderList rows={data.orders} search empty="没有找到订单。" />)}
          {page.section === 'team' && <TaskList rows={data.tasks} empty="目前还没有 AI 团队任务。" />}
          {page.section === 'errors' && <Exceptions tasks={problems} orders={data.orders} />}
          {page.section === 'archive' && <Archive />}
          {page.section === 'lessons' && <Lessons />}
          {page.section === 'operations' && <Operations />}
          {page.section === 'review' && <Review data={data} />}
          {page.section === 'rules' && <div className="fact-grid">
            <Fact label="重大风险需要老板审批" value={data.policy.high_risk_requires_owner ? '启用' : '关闭'} />
            <Fact label="最多澄清轮次" value={data.policy.max_clarification_rounds} />
            <Fact label="最多修订次数" value={data.policy.max_revision_count} />
          </div>}
          {page.section === 'logs' && <TaskList rows={data.tasks} empty="目前还没有任务记录。" />}
          {page.section === 'notifications' && <OrderList rows={pending} empty="当前没有待办通知。" />}
          {page.section === 'settings' && <Settings onLogout={() => setSession(value => value + 1)} />}
        </section>)}
      <footer className="workspace-footer"><a href="#/logs">工作日志</a><span>咪游社 Miyou Club</span></footer>
    </main>
  </div>;
}

function Login({ onLogin }) {
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function submit(event) {
    event.preventDefault();
    if (busy || !password.trim()) return;
    setBusy(true);
    setError('');
    try {
      await api('/owner/session', { body: { password } });
      onLogin();
    } catch (problem) {
      setError(problem.message);
    } finally {
      setBusy(false);
    }
  }
  return <main className="owner-login"><form className="panel connected-page" onSubmit={submit}>
    <h1>咪游社 · 老板工作台</h1><p>请输入启动服务时设置的老板口令。</p>
    <label>老板口令<input type="password" autoComplete="current-password" value={password} required onChange={event => setPassword(event.target.value)} /></label>
    {error && <p className="connected-alert" role="alert">{error}</p>}
    <button className="btn primary" disabled={busy || !password.trim()}>登录</button><a href="/">返回用户端</a>
  </form></main>;
}

function Overview({ data, pending, problems }) {
  const metrics = [
    ['当前登记订单', data.orders.length, Layers, 'orders'],
    ['团建主单已付', data.main_orders.paid, Coins, 'review'],
    ['团建主单已结清', data.main_orders.settled, ClipboardCheck, 'review'],
  ];
  const groups = [
    ['待审批', pending.length, '需要你做出决策后才能继续', 'approvals', ClipboardCheck, 'approval-card'],
    ['订单与风险', data.orders.length, '查看从需求到交付的进度', 'orders', TriangleAlert, 'intervention-card'],
    ['待处理异常', problems.filter(task => !task.recovered).length, '查看失败或中断的工作', 'errors', CircleX, 'error-card'],
  ];
  return <>
    <section className="hero">
      <div className="greeting"><img className="mascot" src={import.meta.env.BASE_URL + 'assets/miyou-robot.png'} alt="咪游机器人" />
        <div className="greeting-copy"><h1>你好，老板。</h1><p>你有 <strong>{pending.length}</strong> 项待处理事项。</p></div></div>
      <div className="metrics">{metrics.map(([label, value, Icon, section]) => <a className="metric" href={'#/' + section} key={label}>
        <Icon /><div><span>{label}</span><strong>{value}</strong></div></a>)}</div>
    </section>
    <section className="triage" aria-label="待处理事项">{groups.map(([label, count, description, section, Icon, theme]) =>
      <a className={'triage-card ' + theme} href={'#/' + section} key={section}>
        <Icon className="triage-icon" /><div><h2>{label} <span className="count green">{count}</span></h2><p>{description}</p></div>
        <ArrowRight className="triage-arrow" />
      </a>)}</section>
    <div className="dashboard-grid">
      <section className="panel connected-page"><h2>待处理订单</h2><OrderList rows={pending.slice(0, 5)} empty="当前没有待办。" /></section>
      <section className="panel connected-page"><h2>团队最近动态</h2><TaskList rows={data.tasks.slice(0, 5)} empty="暂无任务动态。" /></section>
    </div>
  </>;
}

function OrderList({ rows, search = false, empty }) {
  const [query, setQuery] = useState('');
  const visible = rows.filter(row => [row.title, row.display_number, row.source_id].join(' ').toLowerCase().includes(query.trim().toLowerCase()));
  return <>
    {search && <label className="search-field"><Search size={18} /><input placeholder="搜索活动名称或订单号" value={query}
      onChange={event => setQuery(event.target.value)} /></label>}
    {!visible.length && <p className="muted">{empty}</p>}
    <div className="order-list">{visible.map(order => <a className="order-card" href={orderHref(order.key)} key={order.key}>
      <div className="order-card-top"><div><h3>{order.title}</h3><p className="order-number">{order.display_number || order.source_id}</p></div><ChevronRight size={18} /></div>
      <div className="order-card-bottom"><span className="tag green">{order.stage}</span><span>{order.next_actor}</span></div>
    </a>)}</div>
  </>;
}

function TaskList({ rows, empty }) {
  return rows.length ? <ul className="connected-tasks">{rows.map((task, index) => {
    const problem = exception(task);
    return <li key={task.order_key + ':' + index}>
      <a href={orderHref(task.order_key)}><strong>{task.actor} · {taskStep(task) || task.title}</strong></a>
      <span className="tag neutral">{task.recovered ? '已恢复 · 历史异常' : taskLabels[task.status] || task.status}</span>
      <p>{task.order_title}{task.created_at && ' · ' + new Date(task.created_at).toLocaleString('zh-CN', { hour12: false })}</p>
      {problem ? <>
        <p>{problemText(task).reason}</p>
        {task.detail && <details><summary>查看技术记录</summary><p>{task.title} · {task.detail}</p></details>}
      </> : task.detail && <p>{task.detail}</p>}
    </li>;
  })}</ul> : <p className="muted">{empty}</p>;
}

function OrderDetail({ orderKey, refreshDashboard }) {
  const path = orderPath(orderKey);
  const { data, error, refresh, replace } = useResource(path, api, 2200);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  const [feedback, setFeedback] = useState(null);
  async function act(action) {
    if (busy) return;
    setBusy(true);
    setFeedback(null);
    try {
      replace(await api(path + '/actions', { body: {
        action, note, revision: data.revision, order_revision: data.booking?.revision,
        signature: data.booking?.contract?.signature,
      } }));
      setFeedback({ message: '操作已提交。订单状态会自动更新。' });
      setNote('');
    } catch (problem) {
      setFeedback({ message: problem.message, error: true });
    } finally {
      refresh();
      refreshDashboard();
      setBusy(false);
    }
  }
  if (!data) return <p role={error ? 'alert' : 'status'}>{error?.message || '正在读取订单…'}</p>;
  const booking = data.booking;
  const approval = data.approval;
  return <>
    <a className="text-link" href="#/orders"><ArrowLeft size={16} />返回订单与风险</a>
    <div className="order-detail-title"><span className="tag green">{data.stage}</span><h2>{data.title}</h2>
      <p className="order-number">{data.display_number || data.source_id}</p>
      {data.display_number && <p>原编号 {data.source_id}{booking && booking.id !== data.source_id && ' · 预订 ' + booking.id}</p>}</div>
    <div className="fact-grid"><Fact label="下一处理人" value={data.next_actor} />
      <Fact label="当前状态" value={data.blocker || '—'} />
      {data.after_approval && <Fact label="处理后" value={data.after_approval} />}</div>
    {error && <p className="connected-alert" role="alert">{error.message}</p>}
    {data.running && <p role="status">团队正在处理，状态会自动更新。</p>}
    {approval && <section><h3>{approval.title}</h3><ul>{approval.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul></section>}
    {data.actions.some(action => ['approve_plan', 'reject_plan'].includes(action)) &&
      <label>老板意见<textarea value={note} maxLength={2000} onChange={event => setNote(event.target.value)} /></label>}
    {booking?.contract && <div className="fact-grid">
      <Fact label="合同总额" value={money(booking.contract.total_cents)} />
      <Fact label="已收模拟款" value={money(booking.customer_paid_cents)} />
      <Fact label="模拟供方定金" value={money(booking.contract.supplier_deposit_cents)} />
      <Fact label="模拟供方总支出" value={money(booking.contract.supplier_total_cents)} />
    </div>}
    <div className="connected-actions">{data.actions.map(action =>
      <button className={'btn ' + (action === 'reject_plan' ? 'outline' : 'primary')} key={action}
        disabled={busy || (action === 'reject_plan' && !note.trim())} onClick={() => act(action)}>{actionLabels[action]}</button>)}</div>
    {feedback && <p className={feedback.error ? 'connected-alert' : ''} role={feedback.error ? 'alert' : 'status'}>{feedback.message}</p>}
    {booking?.receipts.length > 0 && <section><h3>模拟预订回执</h3><ul>{booking.receipts.map(receipt =>
      <li key={receipt.id}>{receipt.supplier} · {receipt.id} · {money(receipt.amount_cents)}</li>)}</ul></section>}
    <OrderExtras order={data} />
    {data.plans.map(plan => <details key={plan.id}><summary>{plan.title}</summary><p>{plan.summary}</p>
      <ol>{plan.schedule.map((line, index) => <li key={index}>{line}</li>)}</ol></details>)}
    {data.audit.length > 0 && <details><summary>案件审计</summary><ul>{data.audit.map((event, index) =>
      <li key={index}>{event.actor} · {event.detail}</li>)}</ul></details>}
  </>;
}

function Review({ data }) {
  const cash = data.supplier_cash;
  return <>
    <div className="fact-grid"><Fact label="团建主单已结清" value={data.main_orders.settled + ' 笔'} />
      <Fact label="累计模拟收款" value={money(cash.incoming_cents)} />
      <Fact label="累计模拟支出" value={money(cash.outgoing_cents)} />
      <Fact label="模拟现金余额" value={money(cash.cash_cents)} /></div>
    <h2>已结算订单</h2>
    {!data.settled.length && <p>当前没有已结算订单。</p>}
    <div className="order-list">{data.settled.map((row, index) => {
      const order = data.orders.find(item => [item.supplier_order_id, item.procurement_order_id].includes(row['订单编号']));
      return <section className="order-card" key={index}>
        <h3>{order ? <a href={orderHref(order.key)}>{order.title}</a> : row['订单']}</h3>
        <dl>{Object.entries(row).filter(([key]) => key !== '订单').map(([key, value]) =>
          <div key={key}><dt>{key}</dt><dd>{typeof value === 'number' ? money(Math.round(value * 100)) : value}</dd></div>)}</dl>
      </section>;
    })}</div>
  </>;
}

function Settings({ onLogout }) {
  const [error, setError] = useState('');
  return <><p>当前使用本地团建演示工作空间。</p>
    <button className="btn outline" onClick={async () => {
      try { await api('/owner/logout', { body: {} }); onLogout(); }
      catch (problem) { setError(problem.message); }
    }}>退出老板工作台</button>{error && <p role="alert">{error}</p>}</>;
}

function Fact({ label, value }) {
  return <div className="fact"><span>{label}</span><strong>{value}</strong></div>;
}
