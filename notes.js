// Study Notes tab: browse and read the lecture notes the PC agent generated (Markdown + LaTeX + slides).
import { api, agent } from './pc.js';

const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const agentReady = () => !!(agent().url && agent().token);
const CDN = 'https://cdnjs.cloudflare.com/ajax/libs';

let libs = null;
function loadLibs() {
  // Loaded on first use only; the service worker caches them for offline reading.
  const script = src => new Promise((res, rej) => {
    const s = Object.assign(document.createElement('script'), { src, onload: res, onerror: rej });
    document.head.append(s);
  });
  if (!libs) {
    document.head.append(Object.assign(document.createElement('link'), { rel: 'stylesheet', href: `${CDN}/KaTeX/0.16.9/katex.min.css` }));
    libs = Promise.all([script(`${CDN}/marked/12.0.2/marked.min.js`), script(`${CDN}/dompurify/3.1.6/purify.min.js`),
      script(`${CDN}/KaTeX/0.16.9/katex.min.js`)]);
  }
  return libs;
}

let allNotes = [];
let current = null;

async function notesInit() {
  if (!agentReady()) {
    $('notes-list').innerHTML = '<p class="pc-muted">Connect the PC agent (PC Control &rarr; Agent) to see your notes.</p>';
    return;
  }
  await loadList();
}

async function loadList(openId) {
  try {
    const r = await api('/api/notes/list');
    if (!r.available) {
      $('notes-list').innerHTML = `<p class="pc-muted">Notes folder <code>${esc(r.folder)}</code> isn't available. Is that drive connected?</p>`;
      return;
    }
    allNotes = r.notes;
    renderList();
    const target = openId || current?.id || allNotes[0]?.id;
    if (target && allNotes.some(n => n.id === target)) openNote(target);
    else if (!allNotes.length) $('notes-reader').innerHTML = emptyReader();
  } catch (e) {
    $('notes-list').innerHTML = `<p class="pc-muted">${esc(e.message)}</p>`;
  }
}

function emptyReader() {
  return `<div class="notes-empty"><i class="fa-solid fa-book-open"></i><p>No study notes yet.</p>
    <p class="pc-muted">Paste a lecture URL above, use the Study Notes button under YouTube videos, or right-click a video file &rarr; Jarvis &rarr; Transcribe.</p></div>`;
}

function renderList() {
  const q = ($('notes-search').value || '').toLowerCase();
  const items = allNotes.filter(n => n.title.toLowerCase().includes(q));
  $('notes-list').innerHTML = items.map(n => `
    <button class="note-item ${current?.id === n.id ? 'active' : ''}" onclick="notesOpen(${esc(JSON.stringify(n.id))})">
      <span class="note-title">${esc(n.title)}</span>
      <span class="pc-muted"><i class="fa-${/youtu/.test(n.source) ? 'brands fa-youtube' : 'solid fa-file-video'}"></i>
        ${new Date(n.created * 1000).toLocaleDateString()}${n.slides ? ` · ${n.slides} slides` : ''}</span>
    </button>`).join('') || `<p class="pc-muted">${allNotes.length ? 'No matches.' : 'No notes yet.'}</p>`;
}

function renderMarkdown(md) {
  // Protect LaTeX from the Markdown parser (underscores, backslashes), then render it with KaTeX.
  const math = [];
  const keep = (tex, display) => { math.push([tex, display]); return `@@MATH${math.length - 1}@@`; };
  md = md.replace(/\$\$([\s\S]+?)\$\$/g, (_, t) => keep(t, true))
         .replace(/(^|[^\\$])\$([^\n$]+?)\$/g, (_, pre, t) => pre + keep(t, false));
  let html = DOMPurify.sanitize(marked.parse(md));
  html = html.replace(/@@MATH(\d+)@@/g, (_, i) =>
    katex.renderToString(math[i][0], { displayMode: math[i][1], throwOnError: false }));
  return html;
}

async function openNote(id) {
  current = allNotes.find(n => n.id === id) || { id };
  renderList();
  const reader = $('notes-reader');
  reader.innerHTML = '<p class="pc-muted">Loading...</p>';
  try {
    const [n] = await Promise.all([api(`/api/notes/get?id=${encodeURIComponent(id)}`), loadLibs()]);
    current = n;
    const body = n.markdown
      .replace(/^> Source:.*$/m, '')
      .replaceAll('](/api/notes/frame', `](${agent().url}/api/notes/frame`);
    const source = /^https?:/.test(n.source)
      ? `<a href="${esc(n.source)}" target="_blank" rel="noopener"><i class="fa-brands fa-youtube"></i> Watch</a>` : '';
    reader.innerHTML = `
      <div class="note-toolbar">
        <span class="pc-muted">${new Date(n.created * 1000).toLocaleString()}</span>${source}
        <span class="spacer"></span>
        <button onclick="notesAsk()" title="Ask Farvis about this lecture"><i class="fa-solid fa-wand-magic-sparkles"></i> Ask Farvis</button>
        <button onclick="notesReveal()" title="Show in Explorer"><i class="fa-solid fa-folder-open"></i></button>
        <button onclick="notesDownload()" title="Download .md"><i class="fa-solid fa-download"></i></button>
        <button onclick="notesDelete()" title="Move to Recycle Bin"><i class="fa-solid fa-trash"></i></button>
      </div>
      <div class="md">${renderMarkdown(body)}</div>`;
    reader.querySelectorAll('.md a').forEach(a => { a.target = '_blank'; a.rel = 'noopener'; });
    reader.scrollTop = 0;
  } catch (e) {
    reader.innerHTML = `<p class="pc-muted">${esc(e.message)}</p>`;
  }
}

function notesAsk() {
  if (!current?.title) return;
  window.farvisPrefill?.(`About my study notes "${current.title}": `);
}

async function notesReveal() {
  try { await api('/api/reveal', { method: 'POST', json: { path: current.path } }); window.showToast?.('Opened in Explorer'); }
  catch (e) { alert(e.message); }
}

function notesDownload() {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([current.markdown], { type: 'text/markdown' }));
  a.download = `${current.title || current.id}.md`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

async function notesDelete() {
  if (!confirm(`Move "${current.title}" to the Recycle Bin?`)) return;
  try {
    await api('/api/notes/delete', { method: 'POST', json: { id: current.id } });
    current = null;
    window.showToast?.('Moved to the Recycle Bin');
    loadList();
  } catch (e) { alert(e.message); }
}

async function notesGenerate() {
  const url = $('notes-url').value.trim();
  if (!url) return;
  if (!agentReady()) return alert('Connect the PC agent first (PC Control → Agent).');
  try {
    const job = await api('/api/notes', { method: 'POST', json: { url } });
    $('notes-url').value = '';
    watchJob(job.id);
  } catch (e) { alert(e.message); }
}

async function watchJob(id) {
  const row = document.createElement('div');
  row.className = 'note-job';
  $('notes-jobs').append(row);
  for (;;) {
    const j = await api(`/api/jobs/${id}`).catch(() => null);
    if (!j) { row.textContent = 'Lost track of the job.'; return; }
    if (j.status === 'done') {
      row.innerHTML = `<i class="fa-solid fa-check"></i> ${esc(j.result.title)} is ready`;
      setTimeout(() => row.remove(), 8000);
      const folder = j.result.file.split(/[\\/]/).slice(-2, -1)[0];
      return loadList(folder);
    }
    if (j.status === 'error') { row.innerHTML = `<i class="fa-solid fa-triangle-exclamation"></i> ${esc(j.error)}`; return; }
    row.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> ${esc(j.progress || 'working...')}`;
    await new Promise(r => setTimeout(r, 3000));
  }
}

Object.assign(window, {
  notesInit, notesOpen: openNote, notesFilter: renderList, notesAsk, notesReveal, notesDownload,
  notesDelete, notesGenerate
});
