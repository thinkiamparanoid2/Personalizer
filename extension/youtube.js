// 1.2 / 1.3: [Download 1080p] [Download MP3] [Study Notes] under YouTube videos -> local PC agent.
const agent = (method, path, body) => new Promise(resolve =>
  chrome.runtime.sendMessage({ type: 'agent', method, path, body }, resolve));

function videoUrl() {
  const id = new URLSearchParams(location.search).get('v');
  return id ? `https://www.youtube.com/watch?v=${id}` : location.href.split('&')[0]; // drop playlist params
}

async function watch(jobId, status) {
  for (;;) {
    await new Promise(r => setTimeout(r, 3000));
    const r = await agent('GET', `/api/jobs/${jobId}`);
    if (!r?.ok) return (status.textContent = r?.error || 'Lost track of job');
    const job = r.data;
    if (job.status === 'done') return (status.textContent = `✓ ${job.result?.file?.split(/[\\/]/).pop() || 'Done'}`);
    if (job.status === 'error') return (status.textContent = `✗ ${job.error}`);
    status.textContent = job.progress || 'working...';
  }
}

async function start(path, body, status) {
  status.textContent = 'Sending to PC...';
  const r = await agent('POST', path, body);
  if (!r?.ok) return (status.textContent = `✗ ${r?.error || 'Extension error'}`);
  watch(r.data.id, status);
}

function inject() {
  const onVideo = location.pathname === '/watch' || location.pathname.startsWith('/shorts/');
  const existing = document.getElementById('pz-yt');
  if (!onVideo) return existing?.remove();
  if (existing) return;
  const anchor = document.querySelector('ytd-watch-metadata #title') || document.querySelector('#above-the-fold #title');
  if (!anchor) return;
  const wrap = document.createElement('div');
  wrap.id = 'pz-yt';
  wrap.style.cssText = 'display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:8px 0;';
  const status = document.createElement('span');
  status.style.cssText = 'font:13px Roboto,sans-serif;color:var(--yt-spec-text-secondary,#aaa);';
  for (const [label, path, body] of [
    ['⬇ 1080p', '/api/ytdl', () => ({ url: videoUrl(), format: 'mp4' })],
    ['⬇ MP3', '/api/ytdl', () => ({ url: videoUrl(), format: 'mp3' })],
    ['📝 Study Notes', '/api/notes', () => ({ url: videoUrl() })]
  ]) {
    const b = document.createElement('button');
    b.textContent = label;
    b.style.cssText = 'background:#8b0000;color:#fff;border:none;border-radius:18px;padding:6px 14px;font:500 14px Roboto,sans-serif;cursor:pointer;';
    b.addEventListener('click', () => start(path, body(), status));
    wrap.append(b);
  }
  wrap.append(status);
  anchor.after(wrap);
}

// YouTube is a SPA: re-check after each navigation (the title renders a bit later).
function retryInject() {
  let tries = 0;
  const t = setInterval(() => {
    inject();
    if (++tries > 20 || document.getElementById('pz-yt') || !location.pathname.startsWith('/watch')) clearInterval(t);
  }, 500);
}
window.addEventListener('yt-navigate-finish', retryInject);
retryInject();
