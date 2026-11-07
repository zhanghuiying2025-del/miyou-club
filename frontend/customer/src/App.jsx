import React, { useEffect, useRef, useState } from 'react';
import { ArrowRight, ArrowUpRight, ArrowLeft, X, ChatCircle, Sparkle, CalendarBlank, UsersThree, MapPin, Coins, Check, CheckCircle, Clock, Mountains as Mountain, Coffee, Palette, Briefcase, PencilSimple, List, ShieldCheck } from '@phosphor-icons/react';
import { blankBrief, goals, stepTitles, examples, money, totalBudget, profileOf, validateStep } from './domain.js';
import { readDraft, saveDraft } from './storage.js';
import { customerApi, workspaceUrl } from './api.js';
import { ActivityList, ActivityWorkspace } from './Connected.jsx';
import { Offers, UploadDocuments } from './Features.jsx';

const ROBOT = '/assets/miyo-robot.png';
const HERO_ROBOT = '/assets/miyo-hero-robot.png';
const icons = { mountain: Mountain, coffee: Coffee, palette: Palette };
const STEP_NAMES = ['活动名称', '参与团队', '活动地点', '出发时间', '活动预算', '活动偏好'];

function navigate(path, replace = false) {
  window.history[replace ? 'replaceState' : 'pushState']({}, '', path);
  window.dispatchEvent(new Event('miyo:navigate'));
}
function Link({ to, className = '', children, onClick, ...props }) {
  return <a href={to} className={className} {...props} onClick={(event) => {
    onClick?.(event);
    if (!event.defaultPrevented && !event.ctrlKey && !event.metaKey && !event.shiftKey && event.button === 0) { event.preventDefault(); navigate(to); }
  }}>{children}</a>;
}
function useRoute() {
  const [route, setRoute] = useState(window.location.pathname);
  useEffect(() => {
    const update = () => { setRoute(window.location.pathname); window.scrollTo(0, 0); };
    window.addEventListener('popstate', update); window.addEventListener('miyo:navigate', update);
    return () => { window.removeEventListener('popstate', update); window.removeEventListener('miyo:navigate', update); };
  }, []);
  return route;
}
function Brand() {
  return <Link to="/" className="brand" aria-label="咪游社首页"><span className="brand-name">咪游社</span><span className="brand-divider" /><span className="brand-description">AI 团建事务所</span></Link>;
}
function Header({ active, createMode, step, onExit, onStart }) {
  const [menuOpen, setMenuOpen] = useState(false);
  return <header className={`site-header ${createMode ? 'creating-header' : ''}`}>
    <Brand />
    {createMode ? <>
      <div className="wizard-progress" aria-label={`创建活动，第${Math.min(step, 6)}步，共6步`}>
        <div className="progress-segments">{STEP_NAMES.map((label, i) => <span key={label} className={i + 1 <= step ? 'complete' : ''} />)}</div>
        <span>创建活动 <span className="progress-dot">·</span> {Math.min(step, 6)} / 6</span>
      </div>
      <button className="text-button exit-button" onClick={onExit}>退出创建 <X size={19} /></button>
    </> : <>
      <button className="mobile-menu icon-button" aria-label={menuOpen ? '关闭导航' : '打开导航'} aria-expanded={menuOpen} onClick={() => setMenuOpen(!menuOpen)}>{menuOpen ? <X size={24} /> : <List size={24} />}</button>
      <nav aria-label="主导航" className={menuOpen ? 'nav-open' : ''}>
        {[['/', '首页', 'home'], ['/cases', '案例演示', 'cases'], ['/about', '关于我们', 'about']].map(([to, label, id]) => <Link key={id} to={to} className={`nav-link ${active === id ? 'active' : ''}`} aria-current={active === id ? 'page' : undefined} onClick={() => setMenuOpen(false)}>{label}</Link>)}
        <Link className="nav-link" to="/documents" onClick={() => setMenuOpen(false)}>上传资料</Link><Link className="nav-link" to="/offers" onClick={() => setMenuOpen(false)}>咨询与优惠</Link>
        <Link className="button button-small login-button" to="/activities" onClick={() => setMenuOpen(false)}>我的活动 <ArrowUpRight size={18} /></Link>
      </nav>
    </>}
  </header>;
}
function Footer() {
  return <footer className="page-footer"><span>咪游社 · 为每一次相聚，多想一步。</span><Link to="/activities">我的活动 <ArrowUpRight size={15} /></Link></footer>;
}
function Home({ onStart }) {
  return <div className="home-page"><Header active="home" onStart={onStart} />
    <main className="home-main">
      <p className="hero-eyebrow">好活动，从一句话开始</p>
      <h1 className="hero-title">把团建交给一支 AI 团队</h1>
      <p className="hero-subtitle">从灵感到落地，策划、酒店、用车与主持，一起安排。</p>
      <div className="hero-invitation">
        <img className="hero-robot" src={HERO_ROBOT} alt="歪头迈步、抬手邀请的咪游机器人" fetchPriority="high" />
        <div className="invitation-copy">
          <button className="speech-button" onClick={onStart}><span>嗨！开始创建活动吧！</span><span className="speech-arrow"><ArrowRight size={31} weight="regular" /></span></button>
        </div>
      </div>
    </main>
    <div className="home-bottom">
      <Link className="home-history" to="/activities"><CalendarBlank size={17} />我的活动</Link>
      <div className="home-process" aria-label="创建流程"><span><ChatCircle />聊聊需求</span><i>·</i><span><Sparkle />生成方案</span><i>·</i><span><CalendarBlank />确认安排</span></div>
      <Link className="case-shortcut" to="/cases">先看看演示案例 <ArrowUpRight size={19} /></Link>
    </div>
  </div>;
}
function Choice({ selected, children, onClick, className = '', ...props }) {
  return <button type="button" className={`choice ${selected ? 'selected' : ''} ${className}`} aria-pressed={selected} onClick={onClick} {...props}>{children}{selected && <Check size={17} weight="bold" />}</button>;
}
function Wizard({ step, brief, update, onExit, onReview }) {
  const ref = useRef(null);
  const valid = validateStep(step, brief);
  useEffect(() => { const timer = setTimeout(() => ref.current?.focus(), 120); return () => clearTimeout(timer); }, [step]);
  const next = () => { if (valid) step === 6 ? onReview() : navigate(`/create/${step + 1}`); };
  const help = ['一个简单的名字就好。', '人数大致确定就可以。', '有想去的地方，也可以直接告诉我。', '选择暂定的活动日期，后续可以沟通调整。', '我会帮你把预算花在合适的地方。', '选出最在意的，最多三项。'][step - 1];
  return <div className="wizard-page"><Header createMode step={step} onExit={onExit} />
    <main className="question-shell">
      <div className="question-intro"><img src={ROBOT} alt="正面咪游机器人" /><span>咪游陪你一起安排</span></div>
      <form className="question-form" onSubmit={(event) => { event.preventDefault(); next(); }}>
        <div className="question-heading" key={`heading-${step}`}><h1>{stepTitles[step - 1]}</h1><p>{help}</p></div>
        <div className={`answer-area answer-step-${step}`} key={step}>
          {step === 1 && <><label className="sr-only" htmlFor="event-name">活动名称</label><input ref={ref} id="event-name" className="large-input" placeholder="例如：秋日团建、团队周末出游" maxLength={64} value={brief.name} onChange={(e) => update('name', e.target.value)} autoComplete="off" /><span className="input-count">{brief.name.length} / 64</span><button type="button" className="skip-button" onClick={() => navigate('/create/2')}>还没想好？先跳过</button></>}
          {step === 2 && <><div className="number-field"><label htmlFor="people" className="sr-only">参与人数</label><input ref={ref} id="people" type="number" min="2" max="1000" placeholder="30" value={brief.people} onChange={(e) => update('people', e.target.value)} /><span>位伙伴</span></div><div className="quick-options">{[10, 20, 30, 50, 100].map((count) => <Choice key={count} selected={Number(brief.people) === count} onClick={() => update('people', String(count))}>{count} 人</Choice>)}</div><p className="field-label">这是一支怎样的团队？</p><div className="quick-options team-options">{['团队同事', '新组建团队', '社团伙伴', '朋友家人'].map((team) => <Choice key={team} selected={brief.team === team} onClick={() => update('team', team)}>{team}</Choice>)}</div><p className="field-hint">支持 2–1,000 人，后续可以调整。</p></>}
          {step === 3 && <><div className="location-fields"><label><span className="field-label">从哪里出发</span><div className="input-with-icon"><MapPin size={22} /><input ref={ref} placeholder="填写出发城市" maxLength={30} value={brief.city} onChange={(e) => update('city', e.target.value)} /></div></label><label><span className="field-label">想去哪里 <small>选填</small></span><div className="input-with-icon"><Mountain size={23} /><input placeholder="让咪游帮我推荐" maxLength={60} value={brief.destination} onChange={(e) => update('destination', e.target.value)} /></div></label></div><div className="quick-options city-options">{['北京', '上海', '杭州', '深圳', '广州', '成都'].map((city) => <Choice key={city} selected={brief.city === city} onClick={() => update('city', city)}>{city}</Choice>)}</div><label className="event-city"><span className="field-label">活动所在城市</span><input className="large-input" placeholder={brief.city || '填写活动城市'} maxLength={30} value={brief.eventCity || ''} onChange={(e) => update('eventCity', e.target.value)} /></label><p className="field-hint">默认按出发城市安排。跨城活动请填写实际活动城市。</p></>}
          {step === 4 && <><div className="date-field"><label htmlFor="event-date" className="field-label">出发日期</label><input ref={ref} id="event-date" type="date" className="large-input" min={new Date().toLocaleDateString('en-CA')} value={brief.date} onChange={(e) => update('date', e.target.value)} /><label className="checkbox-label"><input type="checkbox" checked={brief.flexible} onChange={(e) => update('flexible', e.target.checked)} /><span>日期可以协商</span></label></div><p className="field-label">想安排多久？</p><div className="quick-options duration-options">{['半天', '1天', '2天1晚', '3天2晚'].map((duration) => <Choice key={duration} selected={brief.duration === duration} onClick={() => update('duration', duration)}>{duration}</Choice>)}</div></>}
          {step === 5 && <><div className="number-field budget-field"><span className="currency">¥</span><label htmlFor="budget" className="sr-only">人均预算</label><input ref={ref} id="budget" type="number" min="50" max="100000" placeholder="500" value={brief.budget} onChange={(e) => update('budget', e.target.value)} /><span>/ 人</span></div><div className="quick-options">{[200, 500, 800, 1500].map((value) => <Choice key={value} selected={Number(brief.budget) === value} onClick={() => update('budget', String(value))}>¥{money(value)}</Choice>)}</div><div className="budget-note"><Coins size={20} /><span>{brief.budget ? `按 ${brief.people || 0} 人计算，总预算约 ` : '填写预算后，为你计算团队总预算'}{brief.budget && <strong>¥{money(totalBudget(brief))}</strong>}</span></div><p className="field-hint">含活动、餐饮、交通；住宿视活动时长安排。人均最低 ¥50。</p></>}
          {step === 6 && <><div className="goal-options">{goals.map((goal) => <Choice key={goal} selected={brief.goals.includes(goal)} disabled={!brief.goals.includes(goal) && brief.goals.length >= 3} onClick={() => update('goals', brief.goals.includes(goal) ? brief.goals.filter((item) => item !== goal) : [...brief.goals, goal])}>{goal}</Choice>)}</div><label className="notes-label" htmlFor="notes">还有什么想告诉咪游？ <span>选填</span></label><textarea id="notes" maxLength={300} value={brief.notes} onChange={(e) => update('notes', e.target.value)} placeholder="例如：不要太累、需要素食、有伙伴需要无障碍通行…" rows={3} /><span className="input-count">{brief.notes.length} / 300</span></>}
        </div>
        <div className="question-actions"><button type="submit" className="button continue-button" disabled={!valid}>{step === 6 ? '看看我的活动画像' : '下一步'} <ArrowRight size={23} /></button><span className="wizard-footnote">{step === 1 ? '约 2 分钟，开启一次好活动' : '你的回答会自动保存'}</span></div>
      </form>
    </main>
    <div className="wizard-bottom"><button className="text-button" onClick={() => step === 1 ? onExit() : navigate(`/create/${step - 1}`)}><ArrowLeft size={19} />{step === 1 ? '返回首页' : '上一步'}</button><span>{String(step).padStart(2, '0')} <span>/</span> 06</span></div>
  </div>;
}
function Review({ brief, onExit, onGenerate, onFiles, busy, error }) {
  const p = profileOf(brief);
  const eventCity = brief.eventCity.trim() || brief.city;
  const fields = [
    ['活动名称', brief.name.trim() || '还没有名字的好活动', 1, PencilSimple], ['参与团队', `${brief.people} 人 · ${brief.team}`, 2, UsersThree], ['活动地点', `${brief.city}出发 · ${eventCity} · ${p.place}`, 3, MapPin], ['时间安排', `${brief.flexible ? brief.date + '（可协商）' : brief.date} · ${brief.duration}`, 4, CalendarBlank], ['预算范围', `¥${money(brief.budget)} / 人 · 共 ¥${money(totalBudget(brief))}`, 5, Coins], ['活动偏好', p.focus, 6, Sparkle],
  ];
  return <div className="wizard-page review-page"><Header createMode step={6} onExit={onExit} /><main className="review-shell"><img className="review-robot" src={ROBOT} alt="正面咪游机器人" /><p className="eyebrow">很高兴，更了解你们了</p><h1 className="display-heading">这次相聚，我了解了。</h1><p className="section-description">确认一下，接下来交给我们的 AI 团队。</p><div className="profile-review">{fields.map(([label, value, step, Icon]) => <button key={label} className="review-field" onClick={() => navigate(`/create/${step}`)}><Icon size={23} /><span><small>{label}</small><strong>{value}</strong></span><PencilSimple size={17} className="review-edit" /></button>)}</div>{brief.notes && <p className="special-note"><strong>特别记下了：</strong>{brief.notes}</p>}<label className="connected-panel">参考附件（可选，最多 3 份）<input type="file" accept=".pdf,.docx,image/jpeg,image/png,image/gif,image/webp" multiple onChange={event => onFiles(Array.from(event.target.files))} /></label><button className="button generate-button" disabled={busy} onClick={onGenerate}>{busy ? "正在提交…" : "让 AI 团队开始策划"} <Sparkle size={23} /></button><p className="wizard-footnote">查看团队给出的方案，再确认安排。</p>{error && <p className="connected-error" role="alert">{error}</p>}</main><div className="wizard-bottom"><Link className="text-button" to="/create/6"><ArrowLeft size={19} />返回修改</Link><span>需求确认</span></div></div>;
}
function Cases({ onStart, onUseCase }) {
  return <div className="inner-page"><Header active="cases" onStart={onStart} /><main className="content-shell cases-shell"><p className="eyebrow">相聚的灵感，从这里开始</p><div className="section-title-row"><h1 className="display-heading">好活动，可以有很多种。</h1><span className="quiet-label">以下均为演示案例</span></div><p className="section-description">选一个喜欢的方向，把它变成你们自己的故事。</p><div className="case-list">{examples.map((item) => { const Icon = icons[item.icon]; return <article key={item.id} className={`case-row case-${item.id}`}><div className="case-number">{item.number}<Icon size={58} weight="thin" /></div><div className="case-content"><span className="small-tag">{item.tag}</span><h2>{item.name}</h2><p>{item.description}</p><div className="case-meta"><span><MapPin />{item.city}</span><span><UsersThree />{item.people}人</span><span><Clock />{item.duration}</span><span>人均 ¥{money(item.budget)}</span></div></div><button className="round-link" aria-label={`以${item.label}为灵感创建活动`} onClick={() => onUseCase(item)}><ArrowUpRight size={28} /></button><button className="case-use-link text-button" onClick={() => onUseCase(item)}>以此为灵感 <ArrowRight size={17} /></button></article>; })}</div><div className="bottom-callout"><span>还没有灵感？让咪游和你一起想。</span><button className="text-button green-text" onClick={onStart}>开始聊聊 <ArrowRight size={22} /></button></div></main><Footer /></div>;
}
function About({ onStart }) {
  const agents = [
    { name: '项目经理', role: '把事情，有条理地推进。', text: '整理需求、拆分任务，检查方案是否符合你的目标。', Icon: Briefcase, num: '01' },
    { name: '策划师', role: '让每次相聚，都有新意。', text: '根据团队画像，按团队偏好准备活动方向。', Icon: Sparkle, num: '02' },
    { name: '执行专员', role: '照顾好，计划里的细节。', text: '跟进场地、餐饮与交通候选，确认后再安排下一步。', Icon: CalendarBlank, num: '03' },
  ];
  return <div className="inner-page"><Header active="about" onStart={onStart} /><main className="content-shell about-shell"><section className="about-intro"><div><p className="eyebrow">认识咪游社</p><h1 className="display-heading">你负责期待，<br />我们负责多想一步。</h1><p className="section-description">我们想让组织一次团建，变得像约朋友出门一样简单。<br className="desktop-break" />从一句想法开始，让 AI 团队一起把它变成具体安排。</p><button className="button" onClick={onStart}>和咪游聊聊 <ArrowUpRight size={20} /></button></div><img src={ROBOT} alt="正面站立并挥手的咪游机器人" /></section><section className="team-section"><div className="section-title-row"><h2>一支团队，接住你的每个想法。</h2><span className="quiet-label">认识你的 AI 同事</span></div><div className="team-intro-grid">{agents.map(({ name, role, text, Icon, num }) => <article key={name}><span className="agent-index">{num}</span><Icon size={35} weight="light" /><h3>{name}</h3><strong>{role}</strong><p>{text}</p></article>)}</div></section><div className="about-principle"><ShieldCheck size={32} /><div><h3>好的安排，也需要你的参与。</h3><p>方案可以修改，重要安排由你确认。酒店、餐饮与交通的实际预订，会在信息核实后单独确认。</p></div></div></main><Footer /></div>;
}
function Dialog({ title, children, onClose, actions }) {
  const ref = useRef(null);
  useEffect(() => { const previous = document.activeElement; ref.current?.focus(); const onKey = (e) => { if (e.key === 'Escape') onClose(); if (e.key === 'Tab') { const nodes = ref.current?.querySelectorAll('button, a[href], input, [tabindex="0"]'); if (!nodes?.length) return; const first = nodes[0], last = nodes[nodes.length - 1]; if (e.shiftKey && (document.activeElement === first || document.activeElement === ref.current)) { e.preventDefault(); last.focus(); } else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); } } }; document.addEventListener('keydown', onKey); return () => { document.removeEventListener('keydown', onKey); previous?.focus(); }; }, []);
  return <div className="modal-backdrop" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}><section className="dialog" role="dialog" aria-modal="true" aria-labelledby="dialog-title" ref={ref} tabIndex={-1}><button className="icon-button dialog-close" onClick={onClose} aria-label="关闭弹窗"><X size={22} /></button><div className="dialog-icon"><ChatCircle size={29} /></div><h2 id="dialog-title">{title}</h2><div className="dialog-description">{children}</div><div className="dialog-actions">{actions}</div></section></div>;
}
function NotFound({ onStart }) {
  return <div className="inner-page"><Header onStart={onStart} /><main className="empty-state not-found"><img src={ROBOT} alt="咪游" /><h1>这个活动，好像不在这里。</h1><p>试试从“我的活动”找回来。</p><Link to="/activities" className="button">去我的活动 <ArrowRight size={19} /></Link></main></div>;
}
export function App() {
  const route = useRoute();
  const [draft, setDraft] = useState(readDraft);
  const [brief, setBrief] = useState(() => ({ ...blankBrief(), ...readDraft()?.brief }));
  const [requestId, setRequestId] = useState(() => readDraft()?.requestId || crypto.randomUUID());
  const [dialog, setDialog] = useState(null);
  const [storageError, setStorageError] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [files, setFiles] = useState([]);
  const [submitError, setSubmitError] = useState('');
  const stepMatch = route.match(/^\/create\/(\d+)$/);
  const step = stepMatch ? Number(stepMatch[1]) : null;
  const activityMatch = route.match(/^\/(workspace|activities)\/([^/]+)(?:\/plans(?:\/([^/]+))?)?$/);

  function persist(nextBrief, nextStep, id = requestId) {
    const next = { brief: nextBrief, step: nextStep, requestId: id };
    setDraft(next);
    setStorageError(!saveDraft(next));
  }

  useEffect(() => {
    if (route === '/create') {
      navigate('/create/1', true);
      return;
    }
    if (!(step >= 1 && step <= 6) && route !== '/create/review') return;
    const before = route === '/create/review' ? 7 : step;
    for (let i = 2; i < before; i += 1) {
      if (!validateStep(i, brief)) {
        navigate('/create/' + i, true);
        return;
      }
    }
    persist(brief, step || 6);
  }, [route]);

  function fresh(example) {
    const next = example ? { ...blankBrief(), name: example.label, city: example.city,
      destination: example.destination, people: String(example.people), budget: String(example.budget),
      duration: example.duration, goals: example.goals } : blankBrief();
    const id = crypto.randomUUID();
    setBrief(next);
    setFiles([]);
    setRequestId(id);
    persist(next, 1, id);
    setDialog(null);
    navigate('/create/1');
  }

  const start = example => draft ? setDialog({ type: 'resume', example }) : fresh(example);
  const onStart = () => start();
  const exit = () => setDialog({ type: 'exit' });

  function update(field, value) {
    const next = { ...brief, [field]: value };
    setBrief(next);
    persist(next, step || 1);
  }

  async function generate() {
    if (submitting) return;
    for (let i = 2; i <= 6; i += 1) {
      if (!validateStep(i, brief)) {
        navigate('/create/' + i);
        return;
      }
    }
    setSubmitting(true);
    setSubmitError('');
    try {
      const payload = { request_id: requestId, brief: { ...brief, name: brief.name.trim() || '未命名团建',
        event_city: brief.eventCity.trim() || brief.city.trim(), people: Number(brief.people), budget: Number(brief.budget) } };
      if (files.length > 3) throw new Error('最多上传 3 份参考附件。');
      let body = payload;
      if (files.length) {
        body = new FormData();
        body.set('payload', JSON.stringify(payload));
        files.forEach(file => body.append('files', file));
      }
      const activity = await customerApi('/customer/activities' + (files.length ? '/with-files' : ''), { body });
      setDraft(null);
      setStorageError(!saveDraft(null));
      setBrief(blankBrief());
      setFiles([]);
      setRequestId(crypto.randomUUID());
      navigate(workspaceUrl(activity.id));
    } catch (error) {
      setSubmitError(error.message);
    } finally {
      setSubmitting(false);
    }
  }

  let page;
  if (route === '/') page = <Home onStart={onStart} />;
  else if (route === '/cases') page = <Cases onStart={onStart} onUseCase={start} />;
  else if (route === '/about') page = <About onStart={onStart} />;
  else if (route === '/documents') page = <><Header onStart={onStart} /><UploadDocuments onCreated={navigate} /></>;
  else if (route === '/offers') page = <><Header onStart={onStart} /><Offers /></>;
  else if (route === '/activities') page = <><Header onStart={onStart} /><ActivityList draft={draft} onStart={onStart} /></>;
  else if (step >= 1 && step <= 6) page = <Wizard step={step} brief={brief} update={update} onExit={exit} onReview={() => navigate('/create/review')} />;
  else if (route === '/create/review') page = <Review brief={brief} onExit={exit} onGenerate={generate} onFiles={setFiles} busy={submitting} error={submitError} />;
  else if (activityMatch) page = <><Header onStart={onStart} /><ActivityWorkspace key={route} id={activityMatch[2]} planId={activityMatch[3]} planList={activityMatch[1] === 'activities'} /></>;
  else if (route === '/create') page = <div className="route-loading" />;
  else page = <NotFound onStart={onStart} />;

  return <>{page}
    {storageError && <p className="storage-warning" role="alert">浏览器暂时无法保存草稿。</p>}
    {dialog?.type === 'exit' && <Dialog title="先休息一下？" onClose={() => setDialog(null)} actions={<>
      <button className="button button-outline" onClick={() => setDialog(null)}>继续创建</button>
      <button className="button" onClick={() => { setDialog(null); navigate('/'); }}>保存并返回首页</button>
    </>}><p>草稿保存在当前浏览器。</p></Dialog>}
    {dialog?.type === 'resume' && <Dialog title="接着上次的想法聊？" onClose={() => setDialog(null)} actions={<>
      <button className="button button-outline" onClick={() => fresh(dialog.example)}>创建新活动</button>
      <button className="button" onClick={() => { setDialog(null); navigate('/create/' + draft.step); }}>继续上次的草稿</button>
    </>}><p>还有一份活动草稿。</p></Dialog>}
  </>;
}
