// Relays YouTube button clicks to the local PC agent. The token stays in the extension, never in the page.
const AGENT = 'http://127.0.0.1:8765';
const ALLOWED = [['POST', /^\/api\/(ytdl|notes)$/], ['GET', /^\/api\/jobs\/[a-f0-9]+$/]];

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (sender.id !== chrome.runtime.id || msg?.type !== 'agent') return;
  if (!ALLOWED.some(([m, re]) => m === msg.method && re.test(msg.path))) {
    sendResponse({ ok: false, error: 'Not allowed' });
    return;
  }
  (async () => {
    const { agentToken } = await chrome.storage.local.get('agentToken');
    if (!agentToken) throw new Error('Set the agent token in the Personalizer extension popup (Settings).');
    const res = await fetch(AGENT + msg.path, {
      method: msg.method,
      headers: { Authorization: `Bearer ${agentToken}`, 'Content-Type': 'application/json' },
      body: msg.method === 'POST' ? JSON.stringify(msg.body || {}) : undefined
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `${res.status} ${res.statusText}`);
    return data;
  })().then(data => sendResponse({ ok: true, data }),
            e => sendResponse({ ok: false, error: e.message.includes('Failed to fetch') ? 'PC agent is not running.' : e.message }));
  return true; // async response
});
