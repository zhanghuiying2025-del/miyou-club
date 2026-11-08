export const blankBrief = () => ({ name: '', people: '', team: '团队同事', city: '', destination: '', eventCity: '', date: '', endDate: '', flexible: true, budget: '', goals: [], customStyle: '', showCustomStyle: false, attractions: [], notes: '' });
export const activityStyles = ['城市游玩', '户外活动', '文化参观', '休闲社交', '美食体验', '创意手作', '主题挑战'];
export const goals = activityStyles;
export const attractionSuggestions = {
  '北京': ['故宫', '天坛', '颐和园', '八达岭长城', '南锣鼓巷'],
  '上海': ['外滩', '豫园', '上海博物馆', '武康路', '朱家角古镇'],
  '杭州': ['西湖', '灵隐寺', '良渚古城遗址', '河坊街', '西溪湿地'],
  '深圳': ['深圳湾公园', '世界之窗', '大芬油画村', '莲花山公园', '甘坑客家小镇'],
  '广州': ['广州塔', '陈家祠', '沙面', '白云山', '永庆坊'],
  '成都': ['宽窄巷子', '锦里', '武侯祠', '青城山', '都江堰'],
  '南京': ['中山陵', '夫子庙', '明孝陵', '玄武湖', '老门东'],
  '天津': ['五大道', '意式风情区', '古文化街', '瓷房子', '海河'],
  '苏州': ['拙政园', '虎丘', '平江路', '周庄', '金鸡湖'],
  '厦门': ['鼓浪屿', '南普陀寺', '曾厝垵', '环岛路', '厦门大学'],
  '重庆': ['解放碑', '洪崖洞', '磁器口', '李子坝', '长江索道'],
  '武汉': ['黄鹤楼', '东湖', '户部巷', '武汉大学', '江汉路'],
  '西安': ['兵马俑', '大雁塔', '回民街', '西安城墙', '华清宫'],
  '长沙': ['岳麓山', '橘子洲', '太平街', '湖南博物院', 'IFS国金中心'],
  '青岛': ['栈桥', '八大关', '崂山', '五四广场', '青岛啤酒博物馆'],
  '昆明': ['滇池', '石林', '翠湖', '西山', '官渡古镇'],
};
export function attractionsFor(place = '') {
  const normalized = place.trim().replace(/(近郊|周边|市区|市内|城区)$/, '');
  return attractionSuggestions[normalized] || ['当地代表性地标', '代表性自然景区', '历史文化场馆', '特色街区', '美食聚集地'];
}
export const stepTitles = ['先给活动起个名字吧。', '这次和谁一起出发？', '想去哪里聚一聚？', '准备什么时候出发和回来？', '人均预算大概多少？', '希望这次活动是什么感觉？'];
export const examples = [
  { id: 'nature', number: '01', tag: '亲近自然', name: '把办公室，搬进山野里。', description: '轻徒步、草地游戏和一顿慢慢吃的午餐。让大家在自然里，重新找到相处的节奏。', city: '北京', destination: '北京近郊', people: 30, budget: 500, duration: '1天', goals: ['户外活动', '休闲社交'], label: '山野慢游', icon: 'mountain' },
  { id: 'creative', number: '02', tag: '创意共创', name: '一起做点，工作之外的事。', description: '用一场创意工作坊打开话匣子。新伙伴有了共同话题，老同事发现彼此的另一面。', city: '上海', destination: '上海市区', people: 20, budget: 300, duration: '半天', goals: ['创意手作', '主题挑战'], label: '灵感发生所', icon: 'palette' },
  { id: 'relax', number: '03', tag: '放松解压', name: '留一个周末，给彼此。', description: '住进近郊、吃一顿好饭、围坐聊天。行程留白多一点，相聚也自在一点。', city: '杭州', destination: '杭州近郊', people: 40, budget: 1000, duration: '2天1晚', goals: ['休闲社交', '美食体验'], label: '周末充电计划', icon: 'coffee' },
];
export const money = (value) => new Intl.NumberFormat('zh-CN').format(value);
export const totalBudget = (brief) => Number(brief.people || 0) * Number(brief.budget || 0);
export function endDateFromRequirement(requirements) {
  if (requirements?.end_date) return requirements.end_date;
  if (!requirements?.event_date) return '';
  const matched = String(requirements.duration || '').match(/(\d+)天/);
  const days = matched ? Number(matched[1]) : 1;
  const end = new Date(requirements.event_date + 'T00:00:00Z');
  if (Number.isNaN(end.getTime())) return '';
  end.setUTCDate(end.getUTCDate() + Math.max(days - 1, 0));
  return end.toISOString().slice(0, 10);
}
export function durationOf(brief) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(brief.date) || !/^\d{4}-\d{2}-\d{2}$/.test(brief.endDate || '')) return '';
  const days = Math.round((new Date(brief.endDate + 'T00:00:00') - new Date(brief.date + 'T00:00:00')) / 86400000) + 1;
  const nights = Math.max(days - 1, 0);
  return `${days}天${nights ? `${nights}晚` : ''}`;
}
export function profileOf(brief) {
  const place = brief.destination.trim() || `${brief.eventCity.trim() || brief.city.trim()}周边`;
  const custom = (brief.customStyle || '').trim();
  const focus = [...(brief.goals || []), ...(custom ? [custom] : [])].join('、') || '轻松相聚';
  return { place, focus, summary: `${brief.people}位${brief.team}，从${brief.city}出发，在${place}度过${durationOf(brief)}。以${focus}为主，人均预算控制在¥${money(brief.budget)}以内。` };
}
export function validateStep(step, brief) {
  if (step === 1) return brief.name.trim().length > 0;
  if (step === 2) return Number.isInteger(Number(brief.people)) && Number(brief.people) >= 2 && Number(brief.people) <= 1000;
  if (step === 3) return brief.city.trim().length > 0;
  if (step === 4) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(brief.date) || !/^\d{4}-\d{2}-\d{2}$/.test(brief.endDate || '')) return false;
    const days = Math.round((new Date(brief.endDate + 'T00:00:00') - new Date(brief.date + 'T00:00:00')) / 86400000) + 1;
    return days >= 1 && days <= 16;
  }
  if (step === 5) return Number.isInteger(Number(brief.budget)) && Number(brief.budget) >= 50 && Number(brief.budget) <= 100000;
  if (step === 6) return (brief.goals?.length || 0) + ((brief.customStyle || '').trim() ? 1 : 0) > 0;
  return true;
}
