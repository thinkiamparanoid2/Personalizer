// Farvis: the Home "Today" screen and the chat assistant (drawer, Ctrl+K, voice).
// The conversation runs on the PC agent (Gemini + PC tools); app-data tools run here via window.farvis.tools.
import { api, agent } from './pc.js';

const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const agentReady = () => !!(agent().url && agent().token);
const isoToday = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);

const SUGGESTIONS = ["What's due this week?", 'Brief me on today', 'Remind me in 20 minutes to stretch', 'Add milk to my shopping list',
  'Play lofi on YouTube', 'Add a CSE330 quiz next Monday', 'What should I watch next?'];

// ---------- Home ----------
function greeting() {
  const h = new Date().getHours();
  return h < 5 ? 'Up late' : h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening';
}

function whenLabel(days) {
  return days === 0 ? 'Today' : days === 1 ? 'Tomorrow' : `in ${days} days`;
}

function renderHome() {
  const f = window.farvis;
  if (!f || !$('home-greeting')) return;
  const name = f.userName();
  $('home-greeting').textContent = `${greeting()}${name ? `, ${name}` : ''}`;
  $('home-date').textContent = new Date().toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' });

  const events = f.upcomingEvents(14);
  const thisWeek = events.filter(e => f.daysUntil(e.date) <= 7).length;
  const pending = f.data.shopping.filter(i => !i.bought);
  $('home-summary').textContent = thisWeek
    ? `${thisWeek} deadline${thisWeek > 1 ? 's' : ''} this week${pending.length ? ` · ${pending.length} thing${pending.length > 1 ? 's' : ''} to buy` : ''}.`
    : `Nothing due this week${pending.length ? ` · ${pending.length} thing${pending.length > 1 ? 's' : ''} to buy` : ''}.`;

  // Empty cards stay hidden: the summary line already says when there's nothing.
  $('card-deadlines').hidden = !events.length;
  $('home-deadlines').innerHTML = events.slice(0, 6).map(e => {
    const d = f.daysUntil(e.date);
    return `<div class="home-item"><span class="badge ${d <= 1 ? 'urgent' : d <= 3 ? 'soon' : ''}">${whenLabel(d)}</span>
      <span class="home-item-title">${esc(e.title)}</span><span class="pc-muted">${esc(e.type)}</span></div>`;
  }).join('');

  $('card-shopping').hidden = !pending.length;
  $('home-shopping').innerHTML = pending.slice(0, 5).map(i =>
    `<div class="home-item"><i class="fa-regular fa-circle"></i><span class="home-item-title">${esc(i.desc)}</span>
      <span class="pc-muted">${esc(i.price || '')}</span></div>`).join('')
    + (pending.length > 5 ? `<p class="pc-muted">+${pending.length - 5} more</p>` : '');

  const watch = f.tools.list_movies({ status: 'plan-to-watch' }).movies;
  $('card-watch').hidden = !watch.length;
  $('home-watch').innerHTML = watch.slice(0, 4).map(m =>
    `<div class="home-item"><i class="fa-solid fa-film"></i><span class="home-item-title">${esc(m.title)}</span>
      <span class="pc-muted">${esc(m.rating || '')}</span></div>`).join('');
  renderNow();
}

// ---------- "Now": the one thing that matters most right now (hidden when nothing does) ----------
let homeJobs = [], homeReminders = [];
function renderNow() {
  const f = window.farvis, box = $('home-now');
  if (!f || !box) return;
  const mins = r => Math.round((new Date(r.at) - Date.now()) / 60000);
  const reminder = homeReminders.find(r => mins(r) >= -1 && mins(r) <= 60);
  const due = f.upcomingEvents(3)[0];
  const job = homeJobs.find(j => j.status === 'running');
  const now =
    reminder ? { icon: 'fa-bell', label: mins(reminder) <= 0 ? 'Reminder now' : `Reminder in ${mins(reminder)} min`,
                 title: reminder.text } :
    due && f.daysUntil(due.date) === 0 ? { icon: 'fa-calendar-day', label: 'Due today', title: due.title, sub: due.type, tab: 'academic', urgent: true } :
    job ? { icon: job.kind === 'notes' ? 'fa-wand-magic-sparkles' : 'fa-download',
            label: job.kind === 'notes' ? 'Making study notes' : 'Downloading on your PC', title: job.progress || 'Working...', tab: 'pc' } :
    due ? { icon: 'fa-calendar-day', label: `Due ${whenLabel(f.daysUntil(due.date)).toLowerCase()}`, title: due.title, sub: due.type, tab: 'academic' } :
    null;
  box.hidden = !now;
  if (!now) return;
  box.classList.toggle('urgent', !!now.urgent);
  box.onclick = now.tab ? () => window.openTab(now.tab) : null;
  box.innerHTML = `<i class="fa-solid ${now.icon}"></i>
    <span class="now-text"><span class="now-label">${esc(now.label)}</span><span class="now-title">${esc(now.title)}</span></span>
    ${now.sub ? `<span class="pc-muted">${esc(now.sub)}</span>` : ''}`;
}

// ---------- Reminders, brief, weather (the agent delivers them even when this app is closed) ----------
let syncTimer = null;
function syncPlanner() {
  clearTimeout(syncTimer);
  syncTimer = setTimeout(async () => {
    const f = window.farvis;
    if (!agentReady() || !f) return;
    try {
      await api('/api/sync', { method: 'POST', json: {
        academic: f.data.academic.map(({ type, title, date }) => ({ type, title, date })),
        shopping_pending: f.data.shopping.filter(i => !i.bought).length,
        name: f.userName(),
        app_url: location.origin + location.pathname
      } });
    } catch { /* agent offline: next change syncs */ }
  }, 3000);
}

async function renderReminders() {
  const box = $('home-reminders');
  if (!box) return;
  const hide = () => { $('card-reminders').hidden = true; homeReminders = []; renderNow(); };
  if (!agentReady()) return hide();  // the PC status pill already says "Not connected"
  try {
    const list = await api('/api/reminders');
    homeReminders = list;
    $('card-reminders').hidden = !list.length;
    renderNow();
    box.innerHTML = list.slice(0, 6).map(r => {
      const at = new Date(r.at);
      const when = at.toDateString() === new Date().toDateString() ? at.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
        : at.toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' });
      return `<div class="home-item"><span class="badge">${esc(when)}</span><span class="home-item-title">${esc(r.text)}</span>
        <button class="icon-btn" onclick="farvisCancelReminder('${esc(r.id)}')" aria-label="Cancel reminder"><i class="fa-solid fa-xmark"></i></button></div>`;
    }).join('');
  } catch { hide(); }
}

async function cancelReminder(id) {
  try { await api('/api/reminders/cancel', { method: 'POST', json: { id } }); }
  catch (e) { return window.showToast?.(e.message, { error: true }); }
  renderReminders();
}

async function briefMe() {
  try {
    const r = await api('/api/brief/send', { method: 'POST' });
    openDrawer();
    addMsg('farvis', `<b>${esc(r.title)}</b><br>${esc(r.body).replace(/\n/g, '<br>')}`);
  } catch (e) { window.showToast?.(e.message, { error: true }); }
}

async function renderWeather() {
  if (!agentReady() || !$('home-weather')) return;
  try {
    const { weather } = await api('/api/weather');
    $('home-weather').textContent = weather ? `Today: ${weather}` : '';
  } catch { /* offline */ }
}

let jobsTimer = null;
async function refreshPcStatus() {
  const el = $('home-pc-status');
  if (!el) return;
  if (!agentReady()) {
    el.innerHTML = '<span class="dot"></span> Not connected <a href="#" onclick="openTab(\'pc\');return false;">Set up</a>';
    $('card-controls').hidden = true;
    return;
  }
  clearTimeout(jobsTimer);
  try {
    homeJobs = await api('/api/jobs');
    el.innerHTML = '<span class="dot ok"></span> PC online';
    // Follow a running download/notes job closely so the Now card's progress stays live.
    if (homeJobs.some(j => j.status === 'running')) jobsTimer = setTimeout(refreshPcStatus, 5000);
  } catch {
    homeJobs = [];
    el.innerHTML = '<span class="dot"></span> PC offline';
  }
  $('card-controls').hidden = !el.querySelector('.dot.ok');
  renderNow();
}
// pc.js polls jobs while one runs (PC Control downloads); keep the Now card in step with it.
window.addEventListener('pc-jobs', e => { homeJobs = e.detail; renderNow(); });

window.addEventListener('farvis-data', () => { renderHome(); syncPlanner(); });
document.addEventListener('DOMContentLoaded', () => {
  renderHome();
  refreshPcStatus();
  renderReminders();
  renderWeather();
  $('home-suggestions').innerHTML = SUGGESTIONS.map(s =>
    `<button class="suggestion" type="button">${esc(s)}</button>`).join('');
  $('home-suggestions').addEventListener('click', e => {
    if (e.target.classList.contains('suggestion')) ask(e.target.textContent);
  });
  renderWelcome();
});
setInterval(() => { renderHome(); refreshPcStatus(); renderReminders(); }, 60000);
setInterval(renderWeather, 3600000);

// ---------- Assistant ----------
let session = sessionStorage.getItem('farvisSession');
if (!session) { session = crypto.randomUUID(); sessionStorage.setItem('farvisSession', session); }

function renderWelcome() {
  $('chat-log').innerHTML = `<div class="msg farvis"><div class="bubble">Hi${window.farvis?.userName() ? ` ${esc(window.farvis.userName())}` : ''}! I can manage your planner and shopping list, control your PC, play music, download videos and make study notes. What do you need?</div></div>`;
}

function md(text) {
  // Minimal, safe Markdown: escape first, then bold/code/bullets/line breaks.
  return esc(text)
    .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/^\s*[*-]\s+(.*)$/gm, '<li>$1</li>')
    .replace(/(<li>.*<\/li>\n?)+/g, m => `<ul>${m.replace(/\n/g, '')}</ul>`)
    .replace(/\n/g, '<br>');
}

function chip(tool, result) {
  if (!result || result.error) return result?.error ? `<span class="action-chip error">${esc(tool)}: ${esc(result.error)}</span>` : '';
  const a = result.added;
  const text = {
    add_academic_event: a && `Added ${a.type}: ${a.title} · ${a.date}`,
    add_shopping_item: a && `Added to shopping: ${a}`,
    mark_shopping_bought: result.marked && `Bought: ${result.marked}`,
    power_action: result.done && `PC: ${result.done}`,
    media_control: result.action && `Media: ${result.action.replace('_', ' ')}`,
    play_youtube: (result.title || result.url) && `Playing: ${result.title || result.url}`,
    set_audio_output: result.switched_to && `Audio → ${result.switched_to}`,
    download_video: result.started && 'Download started',
    make_study_notes: result.started && 'Study notes started',
    set_reminder: result.reminder && `Reminder: ${result.reminder.text} · ${new Date(result.reminder.at).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' })}`,
    cancel_reminder: result.cancelled && 'Reminder cancelled',
    remember: result.remembered && !result.already_known && `Remembered: ${result.remembered.text}`,
    forget: result.forgot && `Forgot: ${result.forgot}`,
    add_routine: result.routine && `Routine: ${result.routine.name} · ${result.routine.at}${
      result.routine.days.length < 7 ? ` ${result.routine.days.join(', ')}` : ' daily'}`,
    cancel_routine: result.cancelled && 'Routine cancelled'
  }[tool];
  return text ? `<span class="action-chip"><i class="fa-solid fa-check"></i> ${esc(text)}</span>` : '';
}

function addMsg(role, html) {
  const div = document.createElement('div');
  div.className = `msg ${role}`;
  div.innerHTML = `<div class="bubble">${html}</div>`;
  $('chat-log').append(div);
  $('chat-log').scrollTop = $('chat-log').scrollHeight;
  return div.firstElementChild;
}

async function runClientTool(call) {
  const args = call.args || {};
  if (call.name === 'power_action') {
    const delay = Math.max(0, args.delay_minutes || 0);
    const verb = args.action === 'sleep' ? 'Sleep' : 'Shut down';
    if (['sleep', 'shutdown'].includes(args.action) &&
        !await window.askConfirm(`${verb} the PC${delay ? ` in ${delay} min` : ' now'}?`,
          { ok: verb, danger: args.action === 'shutdown' })) {
      return { cancelled_by_user: true };
    }
    await api('/api/power', { method: 'POST', json: { action: args.action, delay_s: delay * 60 } });
    return { ok: true, done: `${args.action}${delay ? ` in ${delay} min` : ''}` };
  }
  if (call.name === 'uninstall_app') {
    if (!await window.askConfirm(`Uninstall ${args.name} from your PC?`, { ok: 'Uninstall', danger: true })) {
      return { cancelled_by_user: true };
    }
    const r = await api('/api/command', { method: 'POST',
      json: { command: { action: 'uninstall_app', query: args.name }, confirmed: true } });
    return { ok: true, done: r.did };
  }
  const tool = window.farvis?.tools?.[call.name];
  return tool ? tool(args) : { error: `unknown tool ${call.name}` };
}

function context() {
  return {
    name: window.farvis?.userName() || '',
    now: `${new Date().toLocaleString(undefined, { weekday: 'long', hour: '2-digit', minute: '2-digit' })}, date ${isoToday()}`,
    tz: Intl.DateTimeFormat().resolvedOptions().timeZone
  };
}

let busy = false;
async function ask(text) {
  text = (text || '').trim();
  if (!text || busy) return;
  openDrawer();
  addMsg('user', esc(text));
  const bubble = addMsg('farvis', '<span class="typing"><i></i><i></i><i></i></span>');
  if (!agentReady()) {
    bubble.innerHTML = 'Connect your PC agent first: <b>PC Control → Agent</b>. I run on your PC.';
    return;
  }
  busy = true;
  try {
    let r = await api('/api/assistant', { method: 'POST', json: { session, message: text, context: context() } });
    while (r.calls) {
      const results = [];
      for (const call of r.calls) results.push({ name: call.name, response: await runClientTool(call) });
      r = await api('/api/assistant', { method: 'POST', json: { session, tool_results: results, context: context() } });
    }
    const chips = (r.actions || []).map(a => chip(a.tool, a.result)).join('');
    if ((r.actions || []).some(a => /reminder/.test(a.tool))) renderReminders();
    if ((r.actions || []).some(a => /download_video|make_study_notes/.test(a.tool))) refreshPcStatus();
    bubble.innerHTML = md(r.reply || 'Done.') + (chips ? `<div class="chips">${chips}</div>` : '');
  } catch (e) {
    bubble.textContent = e.offline ? "I can't reach your PC right now. It may be asleep, or the agent isn't running."
      : /quota/i.test(e.message) ? "Gemini's free daily quota is used up (it resets around 1 PM). Direct commands in PC Control still work."
      : /Gemini/i.test(e.message) ? 'Gemini is busy right now. Try again in a moment.' : e.message;
  } finally {
    busy = false;
    $('chat-log').scrollTop = $('chat-log').scrollHeight;
  }
}

// ---------- Drawer, shortcuts, voice ----------
function openDrawer() {
  $('farvis-drawer').classList.add('open');
  setTimeout(() => $('chat-input').focus(), 50);
}
function closeDrawer() { $('farvis-drawer').classList.remove('open'); }
function toggleDrawer() { $('farvis-drawer').classList.contains('open') ? closeDrawer() : openDrawer(); }

async function newChat() {
  if (agentReady()) api('/api/assistant/reset', { method: 'POST', json: { session } }).catch(() => {});
  session = crypto.randomUUID();
  sessionStorage.setItem('farvisSession', session);
  renderWelcome();
}

function sendFrom(inputId) {
  const input = $(inputId);
  const text = input.value;
  input.value = '';
  ask(text);
}

let recorder = null;
async function voice(btn) {
  if (recorder) return recorder.stop();
  if (!agentReady()) return ask('(voice)');
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const chunks = [];
  recorder = new MediaRecorder(stream);
  recorder.ondataavailable = e => chunks.push(e.data);
  recorder.onstop = async () => {
    stream.getTracks().forEach(t => t.stop());
    recorder = null;
    document.querySelectorAll('.mic-btn').forEach(b => b.classList.remove('recording'));
    const type = chunks[0]?.type || 'audio/webm';
    const form = new FormData();
    form.append('audio', new Blob(chunks, { type }), type.includes('mp4') ? 'voice.m4a' : 'voice.webm');
    try {
      const { text } = await api('/api/transcribe', { method: 'POST', form });
      text ? ask(text) : window.showToast?.("Didn't catch that");
    } catch (e) { window.showToast?.(e.message, { error: true }); }
  };
  recorder.start();
  document.querySelectorAll('.mic-btn').forEach(b => b.classList.add('recording'));
  window.showToast?.('Listening... tap the mic again to stop');
  setTimeout(() => { if (recorder?.state === 'recording') recorder.stop(); }, 12000);
}

document.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); toggleDrawer(); }
});

Object.assign(window, {
  farvisAsk: ask, farvisSend: sendFrom, farvisVoice: voice, farvisToggle: toggleDrawer,
  farvisClose: closeDrawer, farvisNewChat: newChat,
  farvisPrefill: text => { openDrawer(); $('chat-input').value = text; $('chat-input').focus(); }, farvisCancelReminder: cancelReminder, farvisBrief: briefMe
});
