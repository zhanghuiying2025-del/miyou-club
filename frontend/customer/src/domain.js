export const blankBrief = () => ({ name: '', people: '', team: '团队同事', city: '', eventCity: '', destination: '', date: '', flexible: true, duration: '1天', budget: '', goals: [], notes: '' });
export const goals = ['放松解压', '增进默契', '亲近自然', '创意共创', '庆祝相聚', '认识新伙伴'];
export const stepTitles = ['先给活动起个名字吧。', '这次和谁一起出发？', '想去哪里聚一聚？', '准备什么时候出发？', '人均预算大概多少？', '希望这次活动是什么感觉？'];
export const examples = [
  { id: 'nature', number: '01', tag: '亲近自然', name: '把办公室，搬进山野里。', description: '轻徒步、草地游戏和一顿慢慢吃的午餐。让大家在自然里，重新找到相处的节奏。', city: '北京', destination: '北京近郊', people: 30, budget: 500, duration: '1天', goals: ['亲近自然', '放松解压'], label: '山野慢游', icon: 'mountain' },
  { id: 'creative', number: '02', tag: '创意共创', name: '一起做点，工作之外的事。', description: '用一场创意工作坊打开话匣子。新伙伴有了共同话题，老同事发现彼此的另一面。', city: '上海', destination: '上海市区', people: 20, budget: 300, duration: '半天', goals: ['创意共创', '增进默契'], label: '灵感发生所', icon: 'palette' },
  { id: 'relax', number: '03', tag: '放松解压', name: '留一个周末，给彼此。', description: '住进近郊、吃一顿好饭、围坐聊天。行程留白多一点，相聚也自在一点。', city: '杭州', destination: '杭州近郊', people: 40, budget: 1000, duration: '2天1晚', goals: ['放松解压', '庆祝相聚'], label: '周末充电计划', icon: 'coffee' },
];
export const money = (value) => new Intl.NumberFormat('zh-CN').format(value);
export const totalBudget = (brief) => Number(brief.people || 0) * Number(brief.budget || 0);
export function profileOf(brief) {
  const place = brief.destination.trim() || `${brief.city}周边`;
  const focus = brief.goals.join('、') || '轻松相聚';
  return { place, focus, summary: `${brief.people}位${brief.team}，从${brief.city}出发，在${place}度过${brief.duration}。以${focus}为主，人均预算控制在¥${money(brief.budget)}以内。` };
}
export function validateStep(step, brief) {
  if (step === 1) return brief.name.trim().length > 0;
  if (step === 2) return Number.isInteger(Number(brief.people)) && Number(brief.people) >= 2 && Number(brief.people) <= 1000;
  if (step === 3) return brief.city.trim().length > 0;
  if (step === 4) return /^\d{4}-\d{2}-\d{2}$/.test(brief.date);
  if (step === 5) return Number.isInteger(Number(brief.budget)) && Number(brief.budget) >= 50 && Number(brief.budget) <= 100000;
  if (step === 6) return brief.goals.length > 0;
  return true;
}
