import { useEffect, useState } from 'react';

export async function api(path, { body, signal } = {}) {
  const response = await fetch('/api' + path, {
    signal,
    ...(body === undefined ? {} : {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload?.detail;
    const message = Array.isArray(detail)
      ? '有信息格式不正确，请检查人数、日期和预算后再试。'
      : detail || '暂时无法连接服务，请重试。';
    throw Object.assign(new Error(message), { status: response.status });
  }
  return payload;
}

export function useResource(path, load = api, interval = 0) {
  const [snapshot, setSnapshot] = useState({});
  const [version, setVersion] = useState(0);

  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    let timer;
    async function refresh() {
      try {
        const data = await load(path, { signal: controller.signal });
        if (!controller.signal.aborted) setSnapshot({ path, data });
      } catch (error) {
        if (!controller.signal.aborted) {
          setSnapshot(current => ({ path, data: current.path === path ? current.data : null, error }));
        }
        if (error.status === 401) return;
      }
      if (interval && !controller.signal.aborted) timer = setTimeout(refresh, interval);
    }
    refresh();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [path, load, interval, version]);

  return {
    data: snapshot.path === path ? snapshot.data : null,
    error: snapshot.path === path ? snapshot.error : null,
    replace: data => setSnapshot({ path, data }),
    refresh: () => setVersion(value => value + 1),
  };
}

export const money = cents => new Intl.NumberFormat('zh-CN', {
  style: 'currency', currency: 'CNY',
}).format(cents / 100);
