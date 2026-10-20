// 1-click workspace freeze: save the window's tabs to chrome.storage.local, close them, restore later.
const $ = id => document.getElementById(id);
const getBundles = async () => (await chrome.storage.local.get({ bundles: [] })).bundles;
const setBundles = bundles => chrome.storage.local.set({ bundles });

async function freeze() {
  const all = await chrome.tabs.query({ currentWindow: true });
  const tabs = all.filter(t => /^https?:/.test(t.url) && !t.pinned);
  if (!tabs.length) return;
  const bundles = await getBundles();
  bundles.unshift({
    id: crypto.randomUUID(),
    name: $('bundle-name').value.trim() || tabs[0].title || 'Workspace',
    createdAt: Date.now(),
    tabs: tabs.map(t => ({ url: t.url, title: t.title }))
  });
  await setBundles(bundles); // saved before anything is closed
  // Keep the window alive; inactive so the popup isn't closed mid-way.
  if (all.length === tabs.length) await chrome.tabs.create({ active: false });
  await chrome.tabs.remove(tabs.map(t => t.id));
  render();
}

async function restore(id) {
  const bundle = (await getBundles()).find(b => b.id === id);
  if (!bundle) return;
  await chrome.windows.create({ url: bundle.tabs.map(t => t.url) });
  // Drop the bundle only after the window exists (popup may close once it takes focus).
  await setBundles((await getBundles()).filter(b => b.id !== id));
}

async function remove(id) {
  if (!confirm('Delete this frozen workspace?')) return;
  await setBundles((await getBundles()).filter(b => b.id !== id));
  render();
}

async function render() {
  const list = $('bundles');
  const bundles = await getBundles();
  list.replaceChildren();
  if (!bundles.length) {
    list.innerHTML = '<p class="muted">Nothing frozen yet.</p>';
    return;
  }
  for (const b of bundles) {
    const row = document.createElement('div');
    row.className = 'row';
    const name = document.createElement('div');
    name.className = 'name';
    name.title = b.tabs.map(t => t.title).join('\n');
    name.textContent = b.name;
    const meta = document.createElement('small');
    meta.textContent = `${b.tabs.length} tabs · ${new Date(b.createdAt).toLocaleDateString()}`;
    name.append(document.createElement('br'), meta);
    const open = document.createElement('button');
    open.textContent = 'Restore';
    open.addEventListener('click', () => restore(b.id));
    const del = document.createElement('button');
    del.textContent = '✕';
    del.title = 'Delete';
    del.addEventListener('click', () => remove(b.id));
    row.append(name, open, del);
    list.append(row);
  }
}

$('freeze').addEventListener('click', freeze);

chrome.storage.sync.get('pwaUrl').then(({ pwaUrl }) => { if (pwaUrl) $('pwa-url').value = pwaUrl; });
$('pwa-url').addEventListener('change', e => {
  const v = e.target.value.trim();
  try { new URL(v); chrome.storage.sync.set({ pwaUrl: v }); } catch { alert('Enter a full URL, e.g. https://your-project.web.app/'); }
});

render();
