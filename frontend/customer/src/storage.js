const KEY = 'miyo-studio-v1';

export function readDraft() {
  try {
    return JSON.parse(localStorage.getItem(KEY))?.draft || null;
  } catch {
    return null;
  }
}

export function saveDraft(draft) {
  try {
    const previous = JSON.parse(localStorage.getItem(KEY)) || {};
    localStorage.setItem(KEY, JSON.stringify({ ...previous, draft }));
    return true;
  } catch {
    return false;
  }
}
