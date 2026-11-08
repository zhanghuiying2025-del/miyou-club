import React, { useRef, useState } from 'react';
import { api } from '../../shared/api.js';

const marketingPath = '/owner/marketing';
const campaignPage = '#/operations/campaigns';
export const campaignHref = id => id ? campaignPage + '/' + encodeURIComponent(id) : campaignPage;
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
    options: ['由 Agent 推荐', '小红书', '微信朋友圈', '企业社群'],
  },
];
const statusLabels = { pending: '待审批', approved: '已批准，待发布', rejected: '已驳回', published: '已发布' };
const statusLabel = campaign => campaign.status === 'published' && campaign.publicationMode === 'simulation'
  ? '已模拟发布' : statusLabels[campaign.status];
const stageFields = [['inquiries', '有效咨询'], ['plans', '确认方案'], ['bookings', '创建预订'], ['paid', '模拟已付']];
const metricFields = [['views', '累计浏览'], ['likes', '累计点赞'], ['comments', '累计评论']];
const dateInShanghai = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', dateStyle: 'short' });
const shortDate = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: 'long', day: 'numeric' });
const percent = new Intl.NumberFormat('zh-CN', { style: 'percent', maximumFractionDigits: 1 });

export function formatMarketingDate(date) {
  return shortDate.format(new Date(date));
}

export function marketingRate(paid, inquiries) {
  return inquiries ? percent.format(paid / inquiries) : '暂无转化数据';
}

function latestSnapshot(metrics) {
  return metrics.reduce((latest, entry) => !latest || entry.date > latest.date ? entry : latest, null);
}

function draftForm(values = {}) {
  return Object.fromEntries(draftFields.map(({ name, options }) => {
    const value = values[name] ?? options[0];
    return [name, options.includes(value) ? { choice: value, custom: '' } : { choice: 'custom', custom: value }];
  }));
}

function CampaignForm({ values, setValues, formRef, onCreate, disabled, generating, adopted }) {
  function update(name, change) {
    setValues(current => ({ ...current, [name]: { ...current[name], ...change } }));
  }
  function submit(event) {
    event.preventDefault();
    const fields = Object.fromEntries(Object.entries(values).map(([name, { choice, custom }]) =>
      [name, choice === 'custom' ? custom.trim() : choice]));
    if (Object.values(fields).every(Boolean)) onCreate(fields);
  }
  return <section ref={formRef} tabIndex={-1} className="feature-card marketing-panel marketing-creation"
    aria-labelledby="marketing-form-title">
    <h2 id="marketing-form-title">创建营销任务</h2>
    {adopted && <p className="marketing-prefill" role="status">已带入复盘建议。确认后点击「生成营销方案」。</p>}
    <form className="feature-form" onSubmit={submit}>
      {draftFields.map(({ name, label, customLabel, example, options }) => <React.Fragment key={name}>
        <label>{label}<select name={name} value={values[name].choice} disabled={generating} onChange={event => update(name, { choice: event.target.value })}>
          {options.map(option => <option key={option}>{option}</option>)}
          <option value="custom">{customLabel}</option>
        </select></label>
        {values[name].choice === 'custom' && <label>{customLabel}<input required pattern={String.raw`.*\S.*`} maxLength={150} disabled={generating}
          placeholder={example} value={values[name].custom} onChange={event => update(name, { custom: event.target.value })} /></label>}
      </React.Fragment>)}
      <button className="btn primary" disabled={disabled}>{generating ? '正在生成营销方案…' : '生成营销方案'}</button>
    </form>
  </section>;
}

function MetricsForm({ snapshots, onSave, pending }) {
  const [date, setDate] = useState(() => latestSnapshot(snapshots)?.date ?? dateInShanghai.format(new Date()));
  const snapshot = snapshots.find(item => item.date === date);
  function submit(event) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const counts = Object.fromEntries(metricFields.map(([key]) => [key, form.get(key) === '' ? null : Number(form.get(key))]));
    onSave({ date, ...counts });
  }
  return <form className="feature-form" onSubmit={submit}>
    <div className="marketing-fields">
      <label>统计日期<input type="date" value={date} onChange={event => setDate(event.target.value)} disabled={Boolean(pending)} required /></label>
      {metricFields.map(([key, label]) => <label key={key}>{label}<input key={date} name={key} type="number" min="0"
        max={Number.MAX_SAFE_INTEGER} step="1" placeholder="未录入" defaultValue={snapshot?.[key] ?? ''} disabled={Boolean(pending)} /></label>)}
    </div>
    <button className="btn primary" disabled={Boolean(pending)}>{pending === 'metrics' ? '正在保存…' : '保存平台数据'}</button>
  </form>;
}

function MetricsList({ fields, values }) {
  return <dl className="marketing-metrics">{fields.map(([key, label]) =>
    <div key={key}><dt>{label}</dt><dd>{values[key]?.toLocaleString('zh-CN') ?? '未录入'}</dd></div>)}</dl>;
}

function CampaignDetails({ campaign, save, pending, onAdopt }) {
  const [editingMetrics, setEditingMetrics] = useState(false);
  const [feedback, setFeedback] = useState('');
  const linkRef = useRef(null);
  const { title, goal, audience, channel, reason, content, status, metrics, stages, review, tracking_path: trackingPath } = campaign;
  const latest = latestSnapshot(metrics);
  const materials = status === 'approved' || status === 'published';
  const link = trackingPath && new URL(trackingPath, window.location.origin).href;

  async function copy(value, name) {
    try {
      await navigator.clipboard.writeText(value);
      setFeedback(`${name}已复制。`);
    } catch {
      if (name === '链接') linkRef.current?.select();
      setFeedback(`可选中${name}手动复制。`);
    }
  }

  async function submit(operation, body, message) {
    setFeedback('');
    const saved = await save(operation, body);
    if (saved) setFeedback(message);
    return saved;
  }

  async function saveMetrics(snapshot) {
    if (await submit('metrics', snapshot, '已保存平台数据。')) setEditingMetrics(false);
  }

  return <section className="feature-card marketing-panel" aria-label="活动详情">
    <div className="marketing-heading"><p className="marketing-muted">{goal}</p><span className="tag green">{statusLabel(campaign)}</span></div>
    <h2>{title}</h2>
    <p className="marketing-muted">推荐{channel} · {reason}</p>
    <p className="marketing-muted">目标客户 · {audience}</p>
    <p className="marketing-draft">{content}</p>
    {status === 'pending' && <div className="connected-actions marketing-actions">
      <button className="btn primary" disabled={Boolean(pending)} onClick={() => submit('actions', { action: 'approve' }, '已批准文案。')}>批准文案</button>
      <button className="btn outline" disabled={Boolean(pending)} onClick={() => submit('actions', { action: 'reject' }, '已驳回文案。')}>驳回</button>
    </div>}
    {materials && <section className="marketing-section" aria-label="推广素材">
      {status === 'approved' && <p className="marketing-muted">在渠道手动发布后，请回来确认。确认发布后，推广链接开始归因。</p>}
      {campaign.publishedAt && <p className="marketing-muted">发布于 {formatMarketingDate(campaign.publishedAt)}</p>}
      {link && <div className="feature-form"><label>推广链接<input className="marketing-link" ref={linkRef} value={link} readOnly /></label></div>}
      <div className="connected-actions marketing-actions">
        <button className="btn outline" onClick={() => copy(`${title}\n\n${content}`, '文案')}>复制文案</button>
        {link && <button className="btn outline" onClick={() => copy(link, '链接')}>复制链接</button>}
        {status === 'approved' && <>
          <button className="btn primary" disabled={Boolean(pending)} onClick={() => submit('actions', {
            action: 'publish', publication_mode: 'manual',
          }, '已确认发布。')}>确认已手动发布</button>
          <button className="btn outline" disabled={Boolean(pending)} onClick={() => submit('actions', {
            action: 'publish', publication_mode: 'simulation',
          }, '已记录模拟发布。')}>模拟发布</button>
        </>}
      </div>
    </section>}
    {status === 'published' && <>
      <section className="marketing-section" aria-label="站内转化">
        <div className="marketing-heading"><h3>站内转化</h3><span className="marketing-muted">推广链接归因</span></div>
        <MetricsList fields={stageFields} values={stages} />
        <p className="marketing-muted">需求付费转化率 · {marketingRate(stages.paid, stages.inquiries)}</p>
      </section>
      <section className="marketing-section" aria-label="站外表现">
        <div className="marketing-heading"><h3>站外表现</h3><span className="marketing-muted">{latest ? `统计至 ${formatMarketingDate(latest.date)}` : '暂无手动录入'}</span></div>
        <MetricsList fields={metricFields} values={latest ?? {}} />
        <div className="connected-actions marketing-actions"><button className="btn outline" aria-expanded={editingMetrics} disabled={Boolean(pending)}
          onClick={() => setEditingMetrics(open => !open)}>{editingMetrics ? '收起录入' : '录入平台数据'}</button></div>
        {editingMetrics && <MetricsForm snapshots={metrics} onSave={saveMetrics} pending={pending} />}
      </section>
      <section className="marketing-section" aria-label="营销复盘">
        <div className="marketing-heading"><h3>营销复盘</h3><span className="marketing-muted">{review ? `生成于 ${formatMarketingDate(review.createdAt)}` : '待生成'}</span></div>
        {review && <div className="marketing-review"><strong>{review.title}</strong><p>{review.body}</p>
          {review.limitations && <p className="marketing-muted">判断边界 · {review.limitations}</p>}
          <details><summary>查看数据依据</summary><p className="marketing-muted">{review.evidence}</p></details>
        </div>}
        <div className="connected-actions marketing-actions">
          <button className={`btn ${review ? 'outline' : 'primary'}`} disabled={Boolean(pending)}
            onClick={() => submit('analyze', {}, '已生成营销复盘。')}>
            {pending === 'analyze' ? '正在生成复盘…' : review ? '重新生成复盘' : '生成复盘'}
          </button>
          {review && <button className="btn primary" disabled={Boolean(pending)} onClick={() => onAdopt(campaign)}>用此建议创建活动</button>}
        </div>
      </section>
    </>}
    {feedback && <p className="marketing-muted" role="status">{feedback}</p>}
  </section>;
}

export function MarketingCampaigns({ campaignId, marketing }) {
  const [values, setValues] = useState(draftForm);
  const [adopted, setAdopted] = useState(false);
  const [pending, setPending] = useState('');
  const [error, setError] = useState('');
  const [deletingId, setDeletingId] = useState(null);
  const [listMessage, setListMessage] = useState('');
  const formRef = useRef(null);
  const generation = useRef(null);
  const reviews = useRef(new Map());
  const campaigns = marketing.data?.campaigns ?? [];
  const active = campaignId ? campaigns.find(campaign => campaign.id === campaignId) : campaigns[0];
  const confirmingDeletion = campaigns.some(campaign => campaign.id === deletingId);

  async function save(operation, body, campaignId) {
    if (pending) return null;
    setPending(operation);
    setError('');
    try {
      const suffix = campaignId ? `/${encodeURIComponent(campaignId)}/${operation}` : `/${operation}`;
      const saved = await api(marketingPath + suffix, { body });
      let updated;
      if (operation === 'delete') updated = campaigns.filter(item => item.id !== saved.id);
      else if (campaigns.some(item => item.id === saved.id)) updated = campaigns.map(item => item.id === saved.id ? saved : item);
      else updated = [saved, ...campaigns];
      marketing.replace({ campaigns: updated });
      marketing.refresh();
      return saved;
    } catch (failure) {
      if (failure.status === 409) marketing.refresh();
      setError(failure.status ? failure.message : '暂时无法连接营销服务，请重试。');
      return null;
    } finally {
      setPending('');
    }
  }

  async function create(fields) {
    const origin = window.location.hash;
    const key = JSON.stringify(fields);
    if (generation.current?.key !== key) generation.current = { key, id: crypto.randomUUID() };
    const saved = await save('generate', { ...fields, request_id: generation.current.id });
    if (saved) {
      generation.current = null;
      setAdopted(false);
      if (window.location.hash === origin) window.location.hash = campaignHref(saved.id);
    }
  }

  async function saveCampaign(operation, body) {
    if (operation !== 'analyze') return save(operation, body, active.id);
    const id = active.id;
    const requestId = reviews.current.get(id) ?? crypto.randomUUID();
    reviews.current.set(id, requestId);
    const saved = await save(operation, { request_id: requestId }, id);
    if (saved) reviews.current.delete(id);
    return saved;
  }

  async function removeCampaign(id) {
    const origin = window.location.hash;
    const saved = await save('delete', {}, id);
    if (!saved) return;
    reviews.current.delete(id);
    setDeletingId(null);
    setListMessage('已删除活动。');
    if (campaignId === id && window.location.hash === origin) window.location.replace(campaignHref());
  }

  function adopt(campaign) {
    setValues(draftForm({
      goal: campaign.review.next_goal || campaign.review.title,
      audience: campaign.audience,
      channel: campaign.review.next_channel || campaign.channel,
    }));
    setAdopted(true);
    setError('');
    formRef.current?.focus({ preventScroll: true });
    formRef.current?.scrollIntoView({ block: 'start' });
  }

  return <div className="marketing-page">
    <p className="marketing-muted">文案由 AI 生成，老板审核并手动发布。付款指标来自团建订单的模拟付款记录。</p>
    {marketing.error && <div className="connected-alert" role="alert">
      <p>{marketing.error.message}</p><button className="btn outline" onClick={marketing.refresh}>重试读取获客活动</button>
    </div>}
    {error && !confirmingDeletion && <p className="connected-alert" role="alert">{error}</p>}
    {pending === 'actions' && <p className="marketing-muted" role="status">正在保存活动状态…</p>}
    <div className="marketing-grid">
      <div className="marketing-sidebar">
        <CampaignForm values={values} setValues={setValues} formRef={formRef} onCreate={create}
          disabled={!marketing.data || Boolean(pending)} generating={pending === 'generate'} adopted={adopted} />
        <section className="feature-card marketing-panel" aria-label="活动列表">
          <h2>活动列表</h2>
          {listMessage && <p className="marketing-muted" role="status">{listMessage}</p>}
          {!marketing.data && !marketing.error && <p className="marketing-muted" role="status">正在读取获客活动…</p>}
          {marketing.data && !campaigns.length && <p className="marketing-muted">暂无获客活动。生成营销方案后，草稿会出现在这里。</p>}
          {!!campaigns.length && <ul className="marketing-campaign-list">{campaigns.map(campaign => <li key={campaign.id}>
            <div className="marketing-campaign-row">
              <button className="marketing-campaign-option" type="button" aria-pressed={campaign.id === active?.id} disabled={Boolean(pending)}
                onClick={() => { window.location.hash = campaignHref(campaign.id); setDeletingId(null); setError(''); }}>
                <strong>{campaign.goal}</strong><span className="marketing-muted">{campaign.channel} · {statusLabel(campaign)}</span>
              </button>
              <button className="btn outline small marketing-delete" type="button" disabled={Boolean(pending)}
                aria-label={`删除活动 ${campaign.goal}（${campaign.channel}）`} aria-expanded={deletingId === campaign.id}
                onClick={() => { setDeletingId(campaign.id); setListMessage(''); setError(''); }}>删除</button>
            </div>
            {deletingId === campaign.id && <div className="marketing-delete-confirm">
              <p>{campaign.status === 'published' ? '删除后将停用推广归因，已有订单会保留。' : '确定删除这条活动？'}</p>
              {error && <p className="connected-alert" role="alert">{error}</p>}
              <div className="marketing-actions">
                <button className="btn outline small marketing-delete" type="button" disabled={Boolean(pending)}
                  onClick={() => removeCampaign(campaign.id)}>{pending === 'delete' ? '正在删除…' : '确认删除'}</button>
                <button className="btn outline small" type="button" disabled={Boolean(pending)}
                  onClick={() => { setDeletingId(null); setError(''); }}>取消</button>
              </div>
            </div>}
          </li>)}</ul>}
        </section>
      </div>
      {active && <CampaignDetails key={active.id} campaign={active} onAdopt={adopt} pending={pending}
        save={saveCampaign} />}
      {campaignId && marketing.data && !active && <p className="marketing-muted">未找到关联的获客活动。</p>}
    </div>
  </div>;
}
