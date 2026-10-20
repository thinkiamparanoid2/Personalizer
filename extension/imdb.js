// Injects "+ Plan to Watch" / "✓ Watched" under the IMDb title.
// Movie data comes from the page's own JSON-LD; the PWA (already logged in) saves it.
const DEFAULT_PWA_URL = 'https://YOUR_PROJECT_ID.web.app/';

function decodeEntities(s) {
  const t = document.createElement('textarea');
  t.innerHTML = s || '';
  return t.value;
}

// "PT2H22M" -> "142 min"
function isoToMinutes(d) {
  const m = /PT(?:(\d+)H)?(?:(\d+)M)?/.exec(d || '');
  const mins = m ? (+m[1] || 0) * 60 + (+m[2] || 0) : 0;
  return mins ? `${mins} min` : '';
}

function readMovie() {
  const id = location.pathname.match(/\/title\/(tt\d+)/)?.[1];
  if (!id) return null;
  for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
    let ld;
    try { ld = JSON.parse(s.textContent); } catch { continue; }
    // After in-app navigation the JSON-LD can be stale; only trust it if it matches the URL.
    if (!ld || !String(ld.url || '').includes(id)) continue;
    return {
      imdbId: id,
      title: decodeEntities(ld.name),
      posterUrl: ld.image || '',
      rating: ld.aggregateRating?.ratingValue != null ? String(ld.aggregateRating.ratingValue) : '',
      duration: isoToMinutes(ld.duration),
      genre: [].concat(ld.genre || []).join(', '),
      plot: decodeEntities(ld.description),
      type: /Series/.test(ld['@type']) ? 'series' : 'movie'
    };
  }
  return null;
}

async function send(status) {
  const movie = readMovie();
  if (!movie) return alert('Personalizer: could not read this title. Reload the page and try again.');
  const { pwaUrl = DEFAULT_PWA_URL } = await chrome.storage.sync.get('pwaUrl');
  const url = new URL(pwaUrl);
  url.searchParams.set('addMovie', JSON.stringify({ ...movie, status }));
  window.open(url.href, '_blank');
}

function inject() {
  if (document.getElementById('pz-buttons')) return;
  const h1 = document.querySelector('h1[data-testid="hero__pageTitle"]') || document.querySelector('h1');
  if (!h1) return;
  const wrap = document.createElement('div');
  wrap.id = 'pz-buttons';
  wrap.style.cssText = 'display:flex;gap:8px;margin:8px 0;';
  for (const [label, status, bg] of [['+ Plan to Watch', 'plan-to-watch', '#8b0000'], ['✓ Watched', 'watched', '#2e7d32']]) {
    const b = document.createElement('button');
    b.textContent = label;
    b.style.cssText = `background:${bg};color:#fff;border:none;border-radius:6px;padding:6px 12px;font:600 14px sans-serif;cursor:pointer;`;
    b.addEventListener('click', () => send(status));
    wrap.append(b);
  }
  h1.after(wrap);
}

inject();
// IMDb re-renders the hero on client-side navigation; put the buttons back.
new MutationObserver(inject).observe(document.body, { childList: true, subtree: true });
