// PC Control tab: talks to the local Python agent (agent/) over Tailscale HTTPS with a Bearer token.
// ponytail: token lives in localStorage on this device; move it into the encrypted vault if the phone is shared.
const $ = id => document.getElementById(id);
const toast = msg => window.showToast && window.showToast(msg);
const escapeHTML = s => String(s ?? '').replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmtSize = n => n == null ? '' : n < 1024 ? `${n} B` : n < 1 << 20 ? `${(n / 1024).toFixed(0)} KB`
  : n < 1 << 30 ? `${(n / (1 << 20)).toFixed(1)} MB` : `${(n / (1 << 30)).toFixed(2)} GB`;

function agent() {
  let url = '', token = '';
  try {
    url = (localStorage.getItem('agentUrl') || '').replace(/\/+$/, '');
    token = localStorage.getItem('agentToken') || '';
  } catch {}
  return { url, token };
}

async function api(path, { method = 'GET', json, form, raw } = {}) {
  const { url, token } = agent();
  if (!url || !token) {
    toggleModal('agent-settings-modal', true);
    throw new Error('Set the agent URL and token first.');
  }
  const headers = { Authorization: `Bearer ${token}` };
  let body;
  if (json) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(json); }
  else if (form) body = form;
  const res = await fetch(url + path, { method, headers, body });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch {}
    throw new Error(`${res.status}: ${typeof msg === 'string' ? msg : JSON.stringify(msg)}`);
  }
  return raw ? res : res.json();
}

// Wrap UI handlers: show errors instead of failing silently.
const ui = fn => async (...args) => {
  try { return await fn(...args); } catch (e) { alert(e.message); }
};

function setStatus(ok) {
  const el = $('pc-status');
  if (el) { el.classList.toggle('ok', ok); el.title = ok ? 'Agent connected' : 'Agent offline'; }
}

let initialized = false;
async function pcInit() {
  const { url, token } = agent();
  $('agent-url').value = url;
  $('agent-token').value = token;
  if (!url || !token) return toggleModal('agent-settings-modal', true);
  try {
    await api('/api/jobs');
    setStatus(true);
  } catch { setStatus(false); return; }
  if (initialized) return;
  initialized = true;
  pcLoadAudio().catch(() => {});
  pcLoadDrop().catch(() => {});
  pcBrowse(null).catch(() => {});
  renderJobs().catch(() => {});
}

async function pcSaveSettings() {
  try {
    localStorage.setItem('agentUrl', $('agent-url').value.trim());
    localStorage.setItem('agentToken', $('agent-token').value.trim());
  } catch { return alert('Could not save settings in this browser.'); }
  toggleModal('agent-settings-modal', false);
  initialized = false;
  await pcInit();
  toast(document.querySelector('#pc-status.ok') ? 'Agent connected!' : 'Agent not reachable');
}

// ---------- Remote ----------
const pcMedia = ui(async action => {
  const r = await api('/api/media', { method: 'POST', json: { action } });
  if (r.volume != null) toast(`Volume ${Math.round(r.volume * 100)}%${r.muted ? ' (muted)' : ''}`);
});

const pcPlayYt = ui(async () => {
  const query = $('pc-yt-query').value.trim();
  if (!query) return;
  const r = await api('/api/play-yt', { method: 'POST', json: { query } });
  toast(`Playing: ${r.title || r.url}`);
});

async function pcLoadAudio() {
  const devices = await api('/api/audio-devices');
  $('pc-audio').innerHTML = devices.map(d =>
    `<option value="${escapeHTML(d.id)}" ${d.default ? 'selected' : ''}>${escapeHTML(d.name)}</option>`).join('');
}

const pcSetAudio = ui(async id => {
  await api('/api/audio-device', { method: 'POST', json: { id } });
  toast('Audio output switched');
});

const pcPower = ui(async action => {
  const delay_s = Math.max(0, +$('pc-power-min').value || 0) * 60;
  if (action === 'shutdown' && !confirm(`Shut down the PC${delay_s ? ` in ${delay_s / 60} min` : ' now'}?`)) return;
  if (action === 'sleep' && !delay_s && !confirm('Put the PC to sleep now?')) return;
  await api('/api/power', { method: 'POST', json: { action, delay_s } });
  toast(action === 'cancel' ? 'Timers cancelled' : `${action}${delay_s ? ` in ${delay_s / 60} min` : ''}`);
});

// ---------- Voice / text command bar ----------
async function showCommandResult(r) {
  const out = $('pc-cmd-out');
  const heard = r.heard ? `"${r.heard}" \u2192 ` : '';
  if (r.confirm) {
    if (!confirm(r.confirm)) { out.textContent = `${heard}cancelled`; return; }
    r = { heard: r.heard, ...(await api('/api/command', { method: 'POST', json: { command: r.command, confirmed: true } })) };
  }
  out.textContent = `${heard}${r.did}`;
}

const pcCommand = ui(async () => {
  const text = $('pc-cmd').value.trim();
  if (!text) return;
  $('pc-cmd-out').textContent = 'Working...';
  await showCommandResult(await api('/api/command', { method: 'POST', json: { text } }));
  $('pc-cmd').value = '';
});

// Records in the browser and lets the PC transcribe (Whisper on the GPU): works in Brave,
// which disables the Web Speech API.
let recorder = null;
const pcVoice = ui(async () => {
  if (recorder) return recorder.stop();
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const chunks = [];
  recorder = new MediaRecorder(stream);
  recorder.ondataavailable = e => chunks.push(e.data);
  recorder.onstop = ui(async () => {
    stream.getTracks().forEach(t => t.stop());
    recorder = null;
    $('pc-mic').classList.remove('recording');
    const type = chunks[0]?.type || 'audio/webm';
    const form = new FormData();
    form.append('audio', new Blob(chunks, { type }), type.includes('mp4') ? 'voice.m4a' : 'voice.webm');
    $('pc-cmd-out').textContent = 'Transcribing...';
    await showCommandResult(await api('/api/command/voice', { method: 'POST', form }));
  });
  recorder.start();
  $('pc-mic').classList.add('recording');
  $('pc-cmd-out').textContent = 'Listening... tap the mic again to stop';
  setTimeout(() => { if (recorder?.state === 'recording') recorder.stop(); }, 10000);
});

// ---------- Clipboard ----------
const pcSendClip = ui(async () => {
  const text = $('pc-clip').value;
  if (!text) return;
  await api('/api/clipboard', { method: 'POST', json: { text } });
  toast('On PC clipboard — press Ctrl+V');
});

const pcSendClipImage = ui(async () => {
  const file = $('pc-clip-img').files[0];
  if (!file) return alert('Pick an image first.');
  const form = new FormData();
  form.append('image', file);
  await api('/api/clipboard/image', { method: 'POST', form });
  toast('Image on PC clipboard — press Ctrl+V');
});

// ---------- AirDrop ----------
const pcUpload = ui(async () => {
  const input = $('pc-drop-files');
  if (!input.files.length) return alert('Pick files first.');
  const form = new FormData();
  for (const f of input.files) form.append('items', f);
  toast(`Sending ${input.files.length} file(s)...`);
  await api('/api/drop', { method: 'POST', form });
  input.value = '';
  toast('Sent to PC!');
  pcLoadDrop();
});

let dropEntries = [];
async function pcLoadDrop() {
  const r = await api('/api/drop');
  dropEntries = r.entries.filter(e => !e.dir);
  $('pc-drop-list').innerHTML = dropEntries.length ? dropEntries.map((e, i) => `
    <div class="pc-item"><i class="fa-regular fa-file"></i><span class="pc-name">${escapeHTML(e.name)}</span>
      <span class="pc-muted">${fmtSize(e.size)}</span>
      <button onclick="pcDownload(dropEntries[${i}])" aria-label="Download"><i class="fa-solid fa-download"></i></button></div>`).join('')
    : '<p class="pc-muted">PhoneDrop folder is empty.</p>';
}

// ---------- Signed links: <video>, VLC and downloads can't send the Bearer header ----------
async function signedUrl(path) {
  const r = await api('/api/stream-link', { method: 'POST', json: { path } });
  return agent().url + r.url;
}

const pcDownload = ui(async entry => { window.open(await signedUrl(entry.path), '_blank'); });

const pcPlay = ui(async entry => {
  $('video-title').innerText = entry.name;
  $('pc-video').src = await signedUrl(entry.path);
  toggleModal('video-modal', true);
  $('pc-video').play().catch(() => {});
});

function pcCloseVideo() {
  const v = $('pc-video');
  v.pause();
  v.removeAttribute('src');
  v.load();
  toggleModal('video-modal', false);
}

const pcVlc = ui(async entry => { location.href = 'vlc://' + await signedUrl(entry.path); });

const pcShare = ui(async entry => {
  toast('Creating public link...');
  const r = await api('/api/share', { method: 'POST', json: { path: entry.path } });
  try { await navigator.clipboard.writeText(r.url); toast('Public link copied!'); }
  catch { prompt('Public link (valid for one download):', r.url); }
});

// ---------- Files ----------
let fileEntries = [], fileParent = null;
const pcBrowse = ui(async path => {
  const r = await api('/api/fs/list' + (path ? `?path=${encodeURIComponent(path)}` : ''));
  fileEntries = r.entries;
  $('pc-path').innerText = r.path || '';
  fileParent = r.parent;
  const up = r.path ? `<div class="pc-item pc-click" onclick="pcBrowse(fileParent)"><i class="fa-solid fa-arrow-up"></i><span class="pc-name">..</span></div>` : '';
  $('pc-files').innerHTML = up + (fileEntries.map((e, i) => e.dir
    ? `<div class="pc-item pc-click" onclick="pcBrowse(fileEntries[${i}].path)"><i class="fa-solid fa-folder"></i><span class="pc-name">${escapeHTML(e.name)}</span></div>`
    : `<div class="pc-item"><i class="fa-regular ${e.video ? 'fa-file-video' : 'fa-file'}"></i><span class="pc-name">${escapeHTML(e.name)}</span>
        <span class="pc-muted">${fmtSize(e.size)}</span>
        ${e.video ? `<button onclick="pcPlay(fileEntries[${i}])" aria-label="Play"><i class="fa-solid fa-play"></i></button>
        <button onclick="pcVlc(fileEntries[${i}])" title="Open in VLC">VLC</button>` : ''}
        <button onclick="pcDownload(fileEntries[${i}])" aria-label="Download"><i class="fa-solid fa-download"></i></button>
        <button onclick="pcShare(fileEntries[${i}])" aria-label="Share public link"><i class="fa-solid fa-share-nodes"></i></button></div>`
  ).join('') || '<p class="pc-muted">Empty folder.</p>');
});

// ---------- YouTube & notes jobs ----------
let pollTimer = null;
async function renderJobs() {
  const list = await api('/api/jobs');
  $('pc-jobs').innerHTML = list.slice(0, 8).map(j => `
    <div class="pc-item"><i class="fa-solid ${j.status === 'done' ? 'fa-check' : j.status === 'error' ? 'fa-triangle-exclamation' : 'fa-spinner fa-spin'}"></i>
      <span class="pc-name">${escapeHTML(j.kind)}: ${escapeHTML(j.result?.title || j.error || j.progress || j.status)}</span>
      ${j.result?.file ? `<span class="pc-muted" title="${escapeHTML(j.result.file)}">${escapeHTML(j.result.file.split(/[\\/]/).pop())}</span>` : ''}</div>`).join('');
  clearTimeout(pollTimer);
  if (list.some(j => j.status === 'running')) pollTimer = setTimeout(() => renderJobs().catch(() => {}), 3000);
}

const pcYtdl = ui(async format => {
  const url = $('pc-yt-url').value.trim();
  if (!url) return;
  await api('/api/ytdl', { method: 'POST', json: { url, format } });
  toast('Download started');
  renderJobs();
});

const pcNotes = ui(async () => {
  const url = $('pc-yt-url').value.trim();
  if (!url) return;
  await api('/api/notes', { method: 'POST', json: { url } });
  toast('Generating study notes...');
  renderJobs();
});

// ---------- PDF tools ----------
const pcPdf = ui(async kind => {
  const form = new FormData();
  if (kind === 'merge') {
    const files = $('pc-pdf-merge').files;
    if (files.length < 2) return alert('Pick at least 2 files.');
    for (const f of files) form.append('items', f);
  } else {
    const file = $(`pc-pdf-${kind}`).files[0];
    if (!file) return alert('Pick a file first.');
    form.append('item', file);
    if (kind === 'compress') form.append('target_kb', $('pc-pdf-target').value);
  }
  toast('Processing...');
  const res = await api(`/api/pdf/${kind}`, { method: 'POST', form, raw: true });
  const name = /filename\*?=(?:UTF-8'')?"?([^";]+)/i.exec(res.headers.get('Content-Disposition') || '')?.[1];
  const a = document.createElement('a');
  a.href = URL.createObjectURL(await res.blob());
  a.download = name ? decodeURIComponent(name) : `${kind}.pdf`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 10000);
});

Object.assign(window, {
  pcInit, pcSaveSettings, pcCommand, pcVoice, pcMedia, pcPlayYt, pcLoadAudio: ui(pcLoadAudio), pcSetAudio, pcPower,
  pcSendClip, pcSendClipImage, pcUpload, pcDownload, pcPlay, pcCloseVideo, pcVlc, pcShare, pcBrowse,
  pcYtdl, pcNotes, pcPdf
});
// Inline handlers index into the latest lists.
Object.defineProperties(window, {
  dropEntries: { get: () => dropEntries },
  fileEntries: { get: () => fileEntries },
  fileParent: { get: () => fileParent }
});
