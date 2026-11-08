import React, { useRef, useState } from 'react';

const channels = new Map([
  ['小红书', 'xiaohongshu'], ['微信朋友圈', 'wechat_moments'], ['企业社群', 'enterprise_group'],
]);
const draftFields = [
  {
    name: 'goal', label: '营销目标', customLabel: '自定义目标', example: '例如：吸引北京中小企业提交团建需求',
    options: ['增加团建需求提交', '推广指定城市的团建服务', '推广某类团建活动', '提高方案到预订的转化'],
  },
  {
    name: 'audience', label: '目标客户', customLabel: '自定义客群', example: '例如：北京 50 人以下科技公司的 HR',
    options: ['企业 HR、行政、团建负责人', '中小企业负责人', '部门负责人、团队主管', '工会、员工活动组织者'],
  },
  {
    name: 'channel', label: '渠道偏好', customLabel: '自定义渠道', example: '例如：微信公众号',
    options: ['由 Agent 推荐', ...channels.keys()],
  },
];
const statusLabels = { pending: '待审批', approved: '已批准，待发布', rejected: '已驳回', published: '已模拟发布' };
const stageFields = [['inquiries', '有效咨询'], ['plans', '确认方案'], ['bookings', '创建预订'], ['paid', '模拟已付']];
const metricFields = [['views', '累计浏览'], ['likes', '累计点赞'], ['comments', '累计评论']];
const dateInShanghai = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', dateStyle: 'short' });
const percent = new Intl.NumberFormat('zh-CN', { style: 'percent', maximumFractionDigits: 1 });
const demoTitle = '30 人团建怎么安排？先把这三笔预算算清楚';
const demoContent = '在准备团队活动？先把交通、餐饮和场地分开估算，再决定行程。打开咪游社，填好人数、预算和偏好，就可以开始规划。';

export function formatMarketingDate(date) {
  const [, month, day] = date.split('-');
  return `${Number(month)} 月 ${Number(day)} 日`;
}

export function marketingRate(paid, inquiries) {
  return inquiries ? percent.format(paid / inquiries) : '暂无转化数据';
}

function latestSnapshot(metrics) {
  return metrics.reduce((latest, entry) => !latest || entry.date > latest.date ? entry : latest, null);
}

function buildReview(campaign, createdAt) {
  const { inquiries, plans, bookings } = campaign.stages;
  const latest = latestSnapshot(campaign.metrics);
  const stageEvidence = stageFields.map(([key, label]) => `${label} ${campaign.stages[key]}`).join(' · ');
  const outsideEvidence = latest
    ? `站外数据统计至 ${formatMarketingDate(latest.date)}，${metricFields.map(([key, label]) => `${label} ${latest[key] ?? '未录入'}`).join(' · ')}。`
    : '站外数据未录入。';
  const evidence = `${stageEvidence}。${outsideEvidence}`;
  if (!inquiries) {
    return {
      title: `先收集${campaign.channel}的有效咨询`,
      body: '用专属链接发布一条内容，观察有效咨询量。收到首批需求后，再决定调整哪个内容。',
      evidence, createdAt,
    };
  }
  return {
    title: `在${campaign.channel}测试更清晰的文末引导`,
    body: `${plans} 条需求确认了方案，${bookings} 条创建了预订。保留原主题和客群，只调整文末提示，明确「提交后可看方案，再按需查餐饮报价」。下轮观察有效咨询量和确认方案后创建预订的比例。当前仅有 ${inquiries} 条咨询，建议先做小规模测试。`,
    evidence, createdAt,
  };
}

const seedDate = '2026-11-08';
const seedCampaign = {
  id: 'demo-001', title: demoTitle, goal: '团建预算攻略', audience: draftFields[1].options[0],
  channel: '小红书', reason: '适合用预算攻略展示团建规划服务', content: demoContent,
  status: 'published', stages: { inquiries: 20, plans: 8, bookings: 5, paid: 3 },
  metrics: [{ date: seedDate, views: 800, likes: 35, comments: 6, recordedAt: Date.parse(`${seedDate}T09:00:00+08:00`) }],
};
export const initialMarketing = [{ ...seedCampaign, review: buildReview(seedCampaign, seedDate) }];

function draftForm(values = {}) {
  return Object.fromEntries(draftFields.map(({ name, options }) => {
    const value = values[name] ?? options[0];
    return [name, options.includes(value) ? { choice: value, custom: '' } : { choice: 'custom', custom: value }];
  }));
}

function CampaignForm({ values, setValues, formRef, onCreate }) {
  function update(name, change) {
    setValues(current => ({ ...current, [name]: { ...current[name], ...change } }));
  }
  function submit(event) {
    event.preventDefault();
    const fields = Object.fromEntries(Object.entries(values).map(([name, { choice, custom }]) =>
      [name, choice === 'custom' ? custom.trim() : choice]));
    if (Object.values(fields).every(Boolean)) onCreate(fields);
  }
  return <section className="feature-card marketing-panel">
    <h2>创建营销任务</h2>
    <form ref={formRef} className="feature-form" onSubmit={submit}>
      {draftFields.map(({ name, label, customLabel, example, options }) => <React.Fragment key={name}>
        <label>{label}<select name={name} value={values[name].choice} onChange={event => update(name, { choice: event.target.value })}>
          {options.map(option => <option key={option}>{option}</option>)}
          <option value="custom">{customLabel}</option>
        </select></label>
        {values[name].choice === 'custom' && <label>{customLabel}<input required pattern={String.raw`.*\S.*`} maxLength={150}
          placeholder={example} value={values[name].custom} onChange={event => update(name, { custom: event.target.value })} /></label>}
      </React.Fragment>)}
      <button className="btn primary">生成营销方案</button>
    </form>
  </section>;
}

function MetricsForm({ snapshots, onSave }) {
  const [date, setDate] = useState(() => latestSnapshot(snapshots)?.date ?? dateInShanghai.format(new Date()));
  const snapshot = snapshots.find(item => item.date === date);
  function submit(event) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const counts = Object.fromEntries(metricFields.map(([key]) => [key, form.get(key) === '' ? null : Number(form.get(key))]));
    onSave({ date, ...counts, recordedAt: Date.now() });
  }
  return <form className="feature-form" onSubmit={submit}>
    <div className="marketing-fields">
      <label>统计日期<input type="date" value={date} onChange={event => setDate(event.target.value)} required /></label>
      {metricFields.map(([key, label]) => <label key={key}>{label}<input key={date} name={key} type="number" min="0"
        max={Number.MAX_SAFE_INTEGER} step="1" placeholder="未录入" defaultValue={snapshot?.[key] ?? ''} /></label>)}
    </div>
    <button className="btn primary">保存平台数据</button>
  </form>;
}

function MetricsList({ fields, values }) {
  return <dl className="marketing-metrics">{fields.map(([key, label]) =>
    <div key={key}><dt>{label}</dt><dd>{values[key]?.toLocaleString('zh-CN') ?? '未录入'}</dd></div>)}</dl>;
}

function CampaignDetails({ campaign, update, onAdopt }) {
  const [editingMetrics, setEditingMetrics] = useState(false);
  const [feedback, setFeedback] = useState('');
  const linkRef = useRef(null);
  const { id, title, goal, audience, channel, reason, content, status, metrics, stages, review } = campaign;
  const latest = latestSnapshot(metrics);
  const materials = status === 'approved' || status === 'published';
  const link = `${window.location.origin}/?${new URLSearchParams({ utm_source: channels.get(channel) ?? 'custom', utm_campaign: id })}`;

  async function copy(value, name) {
    try {
      await navigator.clipboard.writeText(value);
      setFeedback(`${name}已复制。`);
    } catch {
      if (name === '链接') linkRef.current?.select();
      setFeedback(`可选中${name}手动复制。`);
    }
  }

  function saveMetrics(snapshot) {
    update(current => ({ ...current, metrics: [...current.metrics.filter(entry => entry.date !== snapshot.date), snapshot] }));
    setEditingMetrics(false);
    setFeedback('已保存平台数据。');
  }

  return <section className="feature-card marketing-panel" aria-label="活动详情">
    <div className="marketing-heading"><p className="marketing-muted">{goal}</p><span className="tag green">{statusLabels[status]}</span></div>
    <h2>{title}</h2>
    <p className="marketing-muted">推荐{channel} · {reason}</p>
    <p className="marketing-muted">目标客户 · {audience}</p>
    <p className="marketing-draft">{content}</p>
    {status === 'pending' && <div className="connected-actions marketing-actions">
      <button className="btn primary" onClick={() => update(current => ({ ...current, status: 'approved' }))}>批准文案</button>
      <button className="btn outline" onClick={() => update(current => ({ ...current, status: 'rejected' }))}>驳回</button>
    </div>}
    {materials && <section className="marketing-section" aria-label="推广素材">
      <div className="feature-form"><label>推广链接示例<input className="marketing-link" ref={linkRef} value={link} readOnly /></label></div>
      <div className="connected-actions marketing-actions">
        <button className="btn outline" onClick={() => copy(`${title}\n\n${content}`, '文案')}>复制文案</button>
        <button className="btn outline" onClick={() => copy(link, '链接')}>复制链接</button>
        {status === 'approved' && <button className="btn primary" onClick={() => update(current => ({ ...current, status: 'published' }))}>确认模拟发布</button>}
      </div>
    </section>}
    {status === 'published' && <>
      <section className="marketing-section" aria-label="站内转化">
        <div className="marketing-heading"><h3>站内转化</h3><span className="marketing-muted">演示数据</span></div>
        <MetricsList fields={stageFields} values={stages} />
        <p className="marketing-muted">需求付费转化率 · {marketingRate(stages.paid, stages.inquiries)}</p>
      </section>
      <section className="marketing-section" aria-label="站外表现">
        <div className="marketing-heading"><h3>站外表现</h3><span className="marketing-muted">{latest ? `统计至 ${formatMarketingDate(latest.date)}` : '暂无手动录入'}</span></div>
        <MetricsList fields={metricFields} values={latest ?? {}} />
        <div className="connected-actions marketing-actions"><button className="btn outline" aria-expanded={editingMetrics}
          onClick={() => setEditingMetrics(open => !open)}>{editingMetrics ? '收起录入' : '录入平台数据'}</button></div>
        {editingMetrics && <MetricsForm snapshots={metrics} onSave={saveMetrics} />}
      </section>
      <section className="marketing-section" aria-label="营销复盘">
        <div className="marketing-heading"><h3>营销复盘</h3><span className="marketing-muted">{review ? `生成于 ${formatMarketingDate(review.createdAt)}` : '待生成'}</span></div>
        {review && <div className="marketing-review"><strong>{review.title}</strong><p>{review.body}</p>
          <details><summary>查看数据依据</summary><p className="marketing-muted">{review.evidence}</p></details>
        </div>}
        <div className="connected-actions marketing-actions">
          <button className={`btn ${review ? 'outline' : 'primary'}`} onClick={() => {
            const createdAt = dateInShanghai.format(new Date());
            update(current => ({ ...current, review: buildReview(current, createdAt) }));
            setFeedback('已生成演示复盘。');
          }}>{review ? '重新生成复盘' : '生成复盘'}</button>
          {review && <button className="btn primary" onClick={() => onAdopt(campaign)}>用此建议创建活动</button>}
        </div>
      </section>
    </>}
    {feedback && <p className="marketing-muted" role="status">{feedback}</p>}
  </section>;
}

export function MarketingCampaigns({ marketing, setMarketing }) {
  const [values, setValues] = useState(draftForm);
  const [selected, setSelected] = useState(null);
  const formRef = useRef(null);
  const active = marketing.find(campaign => campaign.id === selected) ?? marketing[0];

  function create({ goal, audience, channel }) {
    const recommendation = channel === '由 Agent 推荐';
    const id = `demo-${Date.now()}-${marketing.length + 1}`;
    const campaign = {
      id, title: demoTitle, goal, audience, channel: recommendation ? '小红书' : channel,
      reason: recommendation ? '适合用预算攻略展示团建规划服务' : '按你选择的渠道准备演示文案',
      content: goal.startsWith('在') && goal.includes('测试更清晰的文末引导') ? `${demoContent} 提交后可看方案，再按需查餐饮报价。` : demoContent,
      status: 'pending', stages: { inquiries: 0, plans: 0, bookings: 0, paid: 0 }, metrics: [], review: null,
    };
    setMarketing(current => [campaign, ...current]);
    setSelected(id);
  }

  function adopt(campaign) {
    setValues(draftForm({ goal: campaign.review.title, audience: campaign.audience, channel: campaign.channel }));
    formRef.current?.elements.goal.focus();
  }

  return <div className="marketing-page">
    <p className="marketing-muted">演示模式 · 文案、归因数据和复盘用于试用。新活动和录入数据保存在本次页面会话中。</p>
    <div className="marketing-grid">
      <div className="marketing-sidebar">
        <CampaignForm values={values} setValues={setValues} formRef={formRef} onCreate={create} />
        <section className="feature-card marketing-panel" aria-label="活动列表">
          <h2>活动列表</h2>
          <ul className="marketing-campaign-list">{marketing.map(campaign => <li key={campaign.id}>
            <button className="marketing-campaign-option" type="button" aria-pressed={campaign.id === active?.id}
              onClick={() => setSelected(campaign.id)}><strong>{campaign.goal}</strong><span className="marketing-muted">{campaign.channel} · {statusLabels[campaign.status]}</span></button>
          </li>)}</ul>
        </section>
      </div>
      {active && <CampaignDetails key={active.id} campaign={active} onAdopt={adopt}
        update={change => setMarketing(current => current.map(campaign => campaign.id === active.id ? change(campaign) : campaign))} />}
    </div>
  </div>;
}
