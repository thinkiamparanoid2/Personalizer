import { db, auth } from './firebase.js';
import {
  collection, addDoc, serverTimestamp, setDoc, deleteField, writeBatch,
  onSnapshot, deleteDoc, doc, updateDoc, getDoc, getDocFromCache, getDocs, query, where
} from 'https://www.gstatic.com/firebasejs/10.8.1/firebase-firestore.js';
import {
  createUserWithEmailAndPassword, signInWithEmailAndPassword, signOut,
  onAuthStateChanged, sendPasswordResetEmail, sendEmailVerification
} from 'https://www.gstatic.com/firebasejs/10.8.1/firebase-auth.js';
import {
  PBKDF2_ITER, rand, toB64, fromB64, newSalt, deriveKek, kekFromBytes,
  newDek, wrapDek, unwrapDek, encryptJSON, decryptJSON
} from './vault-crypto.js';

// ==========================================
// --- GLOBAL STATE ---
// ==========================================
let currentUser = null;         // Firebase Auth user
let uid = null;                 // owner id stamped on every document
let currentMovieTab  = 'watched';
let deferredPrompt;
let unsubscribers = [];         // Firestore onSnapshot handles, released on logout

// Vault: the data key lives only in RAM while unlocked.
let dek = null;
let keyDoc;                     // undefined = loading, null = no vault yet, else keys/{uid}
let vaultDocs = [], credDocs = [], chipDocs = [];
const vaultItems = new Map(), credItems = new Map(), movieItems = new Map(), chipItems = new Map();
// Latest Firestore data for the home screen and the assistant (vault/credentials deliberately excluded).
const appData = { academic: [], shopping: [] };
const dataChanged = () => window.dispatchEvent(new CustomEvent('farvis-data'));
const OWNED_COLLECTIONS = ['vault', 'credentials', 'movies', 'academic', 'shopping'];
const AUTO_LOCK_MS = 5 * 60 * 1000;

const emptyState = (icon, text) => `<p class="empty-state"><i class="fa-solid ${icon}"></i> ${text}</p>`;

const escapeHTML = s => String(s ?? '').replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// Only http(s) links; blocks javascript:/data: hrefs from stored data.
function safeUrl(link) {
  try {
    const url = new URL(/^https?:\/\//i.test(link) ? link : 'https://' + link);
    return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
  } catch { return ''; }
}
// For url('...') inside a style attribute.
const cssUrl = u => escapeHTML(safeUrl(u).replace(/["'()\\]/g, c => '%' + c.charCodeAt(0).toString(16)));

// ==========================================
// --- PWA CAPTURED ENGINE CHANNELS ---
// ==========================================
// Install buttons (login screen + sidebar) appear only when the browser offers installation.
const installButtons = () => document.querySelectorAll('.install-app-btn');
window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  deferredPrompt = window.__installPrompt = e;
  installButtons().forEach(btn => { btn.style.display = 'flex'; });
});

// Already running as the installed app: no install buttons.
if (window.matchMedia('(display-mode: standalone)').matches) {
  document.addEventListener('DOMContentLoaded', () => installButtons().forEach(btn => { btn.style.display = 'none'; }));
}

async function installApp() {
  deferredPrompt = deferredPrompt || window.__installPrompt;
  if (!deferredPrompt) {
    return askConfirm([
      "If you see the ⧉ icon at the right of the address bar, Farvis is already installed: click it to open it.", "",
      "Otherwise install from the browser menu:", "",
      "Brave: menu (≡, top right) -> Save and share -> Install page as app",
      "Chrome/Edge: the install icon at the right of the address bar",
      "Android: menu -> Add to Home screen / Install app"
    ].join("\n"), { ok: 'Got it', cancel: null });
  }
  deferredPrompt.prompt();
  await deferredPrompt.userChoice;
  deferredPrompt = null;
  installButtons().forEach(btn => { btn.style.display = 'none'; });
}

window.addEventListener('appinstalled', () => {
  installButtons().forEach(btn => { btn.style.display = 'none'; });
});

// ==========================================
// --- INIT & THEME ENGINE ---
// ==========================================
document.addEventListener('DOMContentLoaded', () => {
  const themeToggle = document.getElementById('theme-toggle');
  const body = document.body;
  const icon = themeToggle.querySelector('i');

  if (localStorage.getItem('theme') !== 'light') {  // dark is the default look
    body.setAttribute('data-theme', 'dark');
    icon.classList.replace('fa-moon', 'fa-sun');
  }

  themeToggle.addEventListener('click', () => {
    if (body.getAttribute('data-theme') === 'dark') {
      body.removeAttribute('data-theme');
      icon.classList.replace('fa-sun', 'fa-moon');
      localStorage.setItem('theme', 'light');
    } else {
      body.setAttribute('data-theme', 'dark');
      icon.classList.replace('fa-moon', 'fa-sun');
      localStorage.setItem('theme', 'dark');
    }
  });

  ['vault-pin', 'vault-pin-confirm'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('keypress', e => { if (e.key === 'Enter') verifyPin(); });
  });

  ['click', 'keydown'].forEach(evt => document.addEventListener(evt, bumpAutoLock));
});

// Session restore + login/logout UI are driven by Firebase Auth.
onAuthStateChanged(auth, async (user) => {
  currentUser = user;
  uid = user ? user.uid : null;
  if (!user) return showAuthScreen();
  document.getElementById('auth-screen').style.display = 'none';
  document.getElementById('main-dashboard').style.display = 'flex';
  document.getElementById('verify-banner').style.display = user.emailVerified ? 'none' : 'flex';
  await migrateLegacyOwners(user);
  startListeners();
  restoreLastTab();
  importMovieFromUrl();
});

// ==========================================
// --- CUSTOM PASSTHROUGH AUTHENTICATION ---
// ==========================================
function switchAuthTab(tab) {
  document.querySelectorAll('.auth-form').forEach(f => f.classList.remove('active'));
  document.querySelectorAll('.auth-tab').forEach(t => t.classList.remove('active'));
  if (tab === 'login') {
    document.getElementById('login-form').classList.add('active');
    document.querySelectorAll('.auth-tab')[0].classList.add('active');
  } else if (tab === 'signup') {
    document.getElementById('signup-form').classList.add('active');
    document.querySelectorAll('.auth-tab')[1].classList.add('active');
  }
}

function showForgotPassword() {
  document.querySelectorAll('.auth-form').forEach(f => f.classList.remove('active'));
  document.getElementById('forgot-form').classList.add('active');
  document.getElementById('forgot-email').value = '';
}

const authErrorText = (e) => ({
  'auth/invalid-credential': 'Incorrect email or password.',
  'auth/email-already-in-use': 'Email already exists. Please login.',
  'auth/weak-password': 'Password must be at least 6 characters.',
  'auth/invalid-email': 'Invalid email address.',
  'auth/too-many-requests': 'Too many attempts. Try again later.',
  'auth/network-request-failed': 'Network error. Check your connection.'
}[e.code] || 'Authentication failed.');

async function handleSignup() {
  const email = document.getElementById('signup-email').value.trim().toLowerCase();
  const pass  = document.getElementById('signup-password').value;
  if (!email) return invalid('signup-email', "Enter your email address.");
  if (!pass) return invalid('signup-password', "Choose a password.");
  try {
    showToast("Creating account...");
    const { user } = await createUserWithEmailAndPassword(auth, email, pass);
    // Verified email is required to import data saved under the old login system.
    await sendEmailVerification(user).catch(() => {});
    showToast("Account created! Verification email sent.");
  } catch (e) { showToast(authErrorText(e), { error: true }); }
}

async function handleLogin() {
  const email = document.getElementById('login-email').value.trim().toLowerCase();
  const pass  = document.getElementById('login-password').value;
  if (!email) return invalid('login-email', "Enter your email address.");
  if (!pass) return invalid('login-password', "Enter your password.");
  try {
    showToast("Signing in...");
    await signInWithEmailAndPassword(auth, email, pass);
  } catch (e) { showToast(authErrorText(e), { error: true }); }
}

async function sendResetEmail() {
  const email = document.getElementById('forgot-email').value.trim().toLowerCase();
  if (!email) return invalid('forgot-email', "Enter your email address.");
  try {
    await sendPasswordResetEmail(auth, email);
    showToast("If that account exists, a reset link was sent.");
    switchAuthTab('login');
  } catch (e) { showToast(authErrorText(e), { error: true }); }
}

async function resendVerification() {
  try {
    await sendEmailVerification(currentUser);
    showToast("Verification email sent (check spam too).");
  } catch (e) { showToast(authErrorText(e), { error: true }); }
}

// After clicking the email link: refresh the token so the rules see email_verified, then import old data.
async function recheckVerification() {
  await currentUser.reload();
  await currentUser.getIdToken(true);
  if (!currentUser.emailVerified) return showToast("Not verified yet. Click the link in the email first.", { error: true });
  document.getElementById('verify-banner').style.display = 'none';
  showToast("Verified! Importing your old data...");
  await migrateLegacyOwners(currentUser);
  showToast("Old data imported.");
}

function handleLogout() {
  signOut(auth); // onAuthStateChanged -> showAuthScreen()
}

function showAuthScreen() {
  stopListeners();
  keyDoc = undefined;
  vaultDocs = []; credDocs = []; chipDocs = [];
  movieItems.clear();
  lockVault();
  ['vault-data','cred-data','watched-list','plan-to-watch-list','academic-list','shopping-list']
    .forEach(id => { const el = document.getElementById(id); if (el) el.innerHTML = ''; });
  document.getElementById('main-dashboard').style.display = 'none';
  document.getElementById('auth-screen').style.display = 'flex';
  switchAuthTab('login');
}

// Docs written before Firebase Auth have owner = email; re-stamp them with the uid.
async function migrateLegacyOwners(user) {
  if (!user.email || !user.emailVerified) return;
  try {
    for (const name of OWNED_COLLECTIONS) {
      const snap = await getDocs(query(collection(db, name), where("owner", "==", user.email)));
      if (snap.empty) continue;
      // ponytail: one batch per collection caps at 500 docs; chunk if a collection ever exceeds that.
      const batch = writeBatch(db);
      snap.forEach(d => batch.update(d.ref, { owner: user.uid }));
      await batch.commit();
    }
  } catch (e) { console.warn('Legacy owner migration skipped:', e.code || e); }
}

function startListeners() {
  stopListeners();
  unsubscribers = [
    listenToKeyDoc(),
    listenToVault(),
    listenToChips(),
    listenToMovies(),
    listenToCredentials(),
    listenToAcademic(),
    listenToShopping()
  ];
}

function stopListeners() {
  unsubscribers.forEach(unsub => unsub());
  unsubscribers = [];
}

// ==========================================
// --- NAVIGATION & DYNAMIC FILTERS ---
// ==========================================
const navItem = tabId => document.querySelector(`.nav-links li[onclick*="'${tabId}'"]`);
let fromHistory = false;  // switchTab called by the back button: don't touch history again

function switchTab(tabId, clickedElement) {
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active-tab'));
  document.querySelectorAll('.nav-links li').forEach(l => l.classList.remove('active'));
  document.getElementById(tabId).classList.add('active-tab');
  if (clickedElement) clickedElement.classList.add('active');
  document.getElementById('global-search').value = '';
  filterItems();
  // One history entry for "away from Home", so Back returns Home before it leaves the app.
  if (!fromHistory) {
    if (tabId === 'home') { if (history.state?.tab) history.back(); }
    else history[history.state?.tab ? 'replaceState' : 'pushState']({ tab: tabId }, '');
  }
  try { localStorage.setItem('lastTab', tabId); } catch {}
}

function restoreLastTab() {
  let tab = null;
  try { tab = localStorage.getItem('lastTab'); } catch {}
  if (tab && tab !== 'home') navItem(tab)?.click();  // click() also runs the tab's init (pcInit, notesInit)
}

function filterItems() {
  const q = document.getElementById('global-search').value.toLowerCase();
  const activeTabId = document.querySelector('.active-tab').id;
  const selector = activeTabId === 'credentials' ? '.cred-card' :
                   activeTabId === 'movies'      ? '.movie-card' :
                   activeTabId === 'academic'    ? '.academic-card' :
                   activeTabId === 'shopping'    ? '.shopping-card' : '';
  if (selector) {
    document.querySelectorAll(selector).forEach(card => {
      const visible = card.innerText.toLowerCase().includes(q);
      card.style.display = visible ? (activeTabId === 'academic' ? 'flex' : 'block') : 'none';
    });
  }
}

function copyText(textToCopy) {
  navigator.clipboard.writeText(textToCopy)
    .then(() => showToast("Copied to clipboard!"))
    .catch(() => {});
}

// A newer toast replaces the current one. Optional action button (Undo / Retry / Reload).
let toastTimer = null;
function showToast(message, { action, onAction, error = false, duration } = {}) {
  const toast = document.getElementById("toast");
  const hide = () => toast.classList.remove("show");
  toast.replaceChildren(message || '');
  if (action) {
    const btn = document.createElement('button');
    btn.textContent = action;
    btn.onclick = () => { hide(); onAction(); };
    toast.append(btn);
  }
  toast.classList.toggle('error', error);
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(hide, duration ?? (action ? 6000 : error ? 4500 : 2500));
}

// Marks a form field as the problem instead of a blocking alert.
function invalid(id, message) {
  const el = document.getElementById(id);
  el.classList.add('invalid');
  el.focus();
  el.addEventListener('input', () => el.classList.remove('invalid'), { once: true });
  showToast(message, { error: true });
}

const writeFailed = what => e => {
  console.warn(`Write failed (${what}):`, e.code || e);
  showToast(`Couldn't ${what}. Try again.`, { error: true });
};

// In-app replacement for confirm(): resolves true/false. cancel: null makes it a one-button notice.
let confirmResolve = null;
function askConfirm(text, { ok = 'OK', cancel = 'Cancel', danger = false } = {}) {
  confirmResolve?.(false);
  const okBtn = document.getElementById('confirm-ok'), cancelBtn = document.getElementById('confirm-cancel');
  document.getElementById('confirm-text').textContent = text;
  okBtn.textContent = ok;
  okBtn.classList.toggle('danger', danger);
  cancelBtn.textContent = cancel || '';
  cancelBtn.style.display = cancel ? '' : 'none';
  toggleModal('confirm-modal', true);
  okBtn.focus();
  return new Promise(resolve => { confirmResolve = resolve; });
}
function settleConfirm(value) {
  const resolve = confirmResolve;
  confirmResolve = null;
  toggleModal('confirm-modal', false);
  resolve?.(value);
}

function toggleModal(modalId, show) {
  document.getElementById(modalId).classList.toggle('show', show);
}

// ---------- Back button / Escape close the top overlay (modals, Farvis drawer) ----------
// Each overlay that opens gets a history entry; Back pops it instead of leaving the app.
const layerStack = [];  // ids of open overlays, oldest first
let skipPop = false;    // the next popstate is our own history.back() after a UI close
const openClass = el => el.id === 'farvis-drawer' ? 'open' : 'show';
const layerClosers = {
  'document-modal': () => closeDocumentModal(),
  'video-modal': () => window.pcCloseVideo(),   // minimizes; playback continues
  'farvis-drawer': () => window.farvisClose(),
  'confirm-modal': () => settleConfirm(false)
};
const closeLayer = id => (layerClosers[id] || (() => toggleModal(id, false)))();

const layerObserver = new MutationObserver(records => {
  for (const { target: el, oldValue } of records) {
    const isOpen = el.classList.contains(openClass(el));
    if ((oldValue || '').split(' ').includes(openClass(el)) === isOpen) continue;
    if (isOpen) {
      if (layerStack.includes(el.id)) continue;
      layerStack.push(el.id);
      history.pushState({ ...history.state, layer: el.id }, '');
    } else {
      const i = layerStack.lastIndexOf(el.id);
      if (i < 0) continue;  // already removed by the back button
      layerStack.splice(i, 1);
      if (i === layerStack.length && history.state?.layer === el.id) { skipPop = true; history.back(); }
      if (el.id === 'confirm-modal' && confirmResolve) settleConfirm(false);  // closed by clicking outside
    }
  }
});
document.querySelectorAll('.modal-overlay, #farvis-drawer').forEach(el =>
  layerObserver.observe(el, { attributes: true, attributeFilter: ['class'], attributeOldValue: true }));

window.addEventListener('popstate', () => {
  if (skipPop) { skipPop = false; return; }
  if (layerStack.length) return closeLayer(layerStack.pop());
  const tab = history.state?.tab || 'home';
  if (document.querySelector('.active-tab')?.id === tab) return;
  fromHistory = true;
  try { navItem(tab)?.click(); } finally { fromHistory = false; }
});

document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && layerStack.length) closeLayer(layerStack.at(-1));
});

// The service worker serves the cached app instantly and reports when a newer version was fetched.
let updateOffered = false;
navigator.serviceWorker?.addEventListener('message', e => {
  if (e.data?.type !== 'app-updated' || updateOffered) return;
  updateOffered = true;
  showToast('Farvis was updated.', { action: 'Reload', onAction: () => location.reload(), duration: 15000 });
});

window.addEventListener('click', (event) => {
  if (event.target.classList.contains('modal-overlay')) event.target.classList.remove('show');
  if (!event.target.closest('.cred-menu-btn') && !event.target.closest('.cred-dropdown') &&
      !event.target.closest('.movie-menu-btn') && !event.target.closest('.movie-dropdown')) {
    document.querySelectorAll('.movie-dropdown, .cred-dropdown').forEach(d => d.classList.remove('show'));
  }
});

// ==========================================
// --- CLIENT CANVAS GRAPHICS ENGINE ---
// ==========================================
async function compressImage(file, maxWidth = 900, quality = 0.75) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      let { width, height } = img;
      if (width > maxWidth) { height = Math.round(height * maxWidth / width); width = maxWidth; }
      const canvas = document.createElement('canvas');
      canvas.width = width; canvas.height = height;
      canvas.getContext('2d').drawImage(img, 0, 0, width, height);
      URL.revokeObjectURL(url);
      resolve(canvas.toDataURL('image/jpeg', quality));
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error(`Couldn't read "${file.name}". Use a JPG or PNG image.`)); };
    img.src = url;
  });
}

// ==========================================
// --- ACADEMIC MODULE CONTROLLER ---
// ==========================================
async function saveAcademicEvent() {
  const type  = document.getElementById('academicType').value;
  const title = document.getElementById('academicTitle').value.trim();
  const date  = document.getElementById('academicDate').value;
  if (!title) return invalid('academicTitle', "Give the event a title.");
  if (!date) return invalid('academicDate', "Pick a date.");
  // Not awaited: Firestore applies the write locally at once (and offline); awaiting waits for the server.
  addDoc(collection(db, "academic"), { type, title, date, owner: uid, createdAt: serverTimestamp() })
    .catch(writeFailed('save the event'));
  toggleModal('add-academic-modal', false);
  showToast("Event added");
  document.getElementById('academicTitle').value = '';
  document.getElementById('academicDate').value = '';
}

function listenToAcademic() {
  return onSnapshot(query(collection(db, "academic"), where("owner", "==", uid)), (snapshot) => {
    appData.academic = snapshot.docs.map(d => ({ id: d.id, ...d.data() }));
    dataChanged();
    const container = document.getElementById('academic-list');
    if (!container) return;
    container.innerHTML = snapshot.docs.map(docSnap => {
      const item = docSnap.data();
      const icon = item.type === 'Quiz' ? 'fa-pen-to-square' : item.type === 'Assignment' ? 'fa-file-lines' : 'fa-flask';
      return `
        <div class="academic-card" data-type="${escapeHTML(item.type)}">
          <i class="fa-solid ${icon}"></i>
          <div class="academic-info">
            <h4>${escapeHTML(item.title)}</h4>
            <p>${escapeHTML(item.type)} &bull; ${escapeHTML(item.date)}</p>
          </div>
          <button class="remove-btn" onclick="deleteDocItem('academic','${docSnap.id}')"><i class="fa-solid fa-trash"></i></button>
        </div>`;
    }).join('') || emptyState('fa-calendar-plus', 'No events yet. Add your next quiz, assignment or lab test.');
  });
}

function filterAcademic(filterType, btn) {
  document.querySelectorAll('#academic .m-tab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('.academic-card').forEach(card => {
    card.style.display = (filterType === 'All' || card.getAttribute('data-type') === filterType) ? 'flex' : 'none';
  });
}

// ==========================================
// --- VAULT SUBSYSTEM BLOCK ---
// ==========================================
// keys/{uid} = { salt, iter, wrapped } — data key wrapped by the passphrase-derived key.
function listenToKeyDoc() {
  return onSnapshot(doc(db, "keys", uid), (snap) => {
    keyDoc = snap.exists() ? snap.data() : null;
    updateLockScreen();
  });
}

function updateLockScreen() {
  const setup = keyDoc === null;
  document.getElementById('vault-lock-msg').innerText = setup
    ? 'Create a vault passphrase (8+ characters). It cannot be recovered — remember it.'
    : 'Enter vault passphrase or use biometrics';
  document.getElementById('vault-pin-confirm').style.display = setup ? 'block' : 'none';
  document.getElementById('vault-unlock-btn').innerText = setup ? 'Create' : 'Unlock';
}

function flashPinError() {
  const pinInput = document.getElementById('vault-pin');
  pinInput.style.borderColor = 'red';
  setTimeout(() => pinInput.style.borderColor = 'var(--border-color)', 500);
}

async function verifyPin() {
  const pinInput = document.getElementById('vault-pin');
  const confirmInput = document.getElementById('vault-pin-confirm');
  const pass = pinInput.value;
  if (!pass || keyDoc === undefined) return;

  if (keyDoc === null) {
    if (pass.length < 8) return invalid('vault-pin', "Passphrase must be at least 8 characters.");
    if (pass !== confirmInput.value) return invalid('vault-pin-confirm', "Passphrases don't match.");
    try {
      showToast("Creating vault key...");
      const salt = newSalt();
      const newKey = await newDek();
      // Awaited on purpose: the vault must not be used before its key is safely stored.
      await setDoc(doc(db, "keys", uid), { salt, iter: PBKDF2_ITER, wrapped: await wrapDek(newKey, await deriveKek(pass, salt)) });
      dek = newKey;
    } catch (e) { return showToast("Couldn't create the vault key. Check your connection and try again.", { error: true }); }
  } else {
    try {
      showToast("Unlocking...");
      dek = await unwrapDek(keyDoc.wrapped, await deriveKek(pass, keyDoc.salt, keyDoc.iter));
    } catch { return flashPinError(); }
  }
  pinInput.value = '';
  confirmInput.value = '';
  await onVaultUnlocked();
  offerBiometricEnrollment();
}

async function onVaultUnlocked() {
  document.getElementById('vault-lock').classList.add('hidden');
  bumpAutoLock();
  encryptLegacyDocs(); // not awaited: offline writes only resolve after sync
  renderVault();
  renderChips();
  renderCredentials();
}

let autoLockTimer = null;
function bumpAutoLock() {
  if (!dek) return;
  clearTimeout(autoLockTimer);
  autoLockTimer = setTimeout(lockVault, AUTO_LOCK_MS);
}

function lockVault() {
  dek = null;
  clearTimeout(autoLockTimer);
  vaultItems.clear();
  document.getElementById('vault-lock').classList.remove('hidden');
  renderVault();
  renderChips();
  renderCredentials();
}

// Plaintext docs from before encryption: encrypt in place on first unlock.
async function encryptLegacyDocs() {
  const key = dek;
  try {
    for (const d of vaultDocs.filter(d => !d.data().enc)) {
      const { type = '', title = '', number = '', frontImgUrl = null, backImgUrl = null } = d.data();
      await updateDoc(d.ref, {
        enc: await encryptJSON({ type, title, number, frontImgUrl, backImgUrl }, key),
        type: deleteField(), title: deleteField(), number: deleteField(),
        frontImgUrl: deleteField(), backImgUrl: deleteField()
      });
    }
    for (const d of credDocs.filter(d => !d.data().enc)) {
      const { username = '', password = '' } = d.data();
      await updateDoc(d.ref, {
        enc: await encryptJSON({ username, password }, key),
        username: deleteField(), password: deleteField()
      });
    }
  } catch (e) { console.warn('Legacy encryption incomplete:', e.code || e); }
}

// --- Biometric unlock: WebAuthn PRF yields a per-device secret that wraps the data key. ---
const PRF_SALT = new TextEncoder().encode('personalizer-vault-prf-v1');
const prfStorageKey = () => `vaultPrf_${uid}`;
const loadPrf = () => { try { return JSON.parse(localStorage.getItem(prfStorageKey())); } catch { return null; } };

async function prfSecret(credId) {
  const assertion = await navigator.credentials.get({ publicKey: {
    challenge: rand(32),
    allowCredentials: [{ type: 'public-key', id: credId }],
    userVerification: 'required',
    extensions: { prf: { eval: { first: PRF_SALT } } }
  }});
  const first = assertion.getClientExtensionResults().prf?.results?.first;
  if (!first) throw new Error('PRF not supported by this authenticator.');
  return first;
}

async function enrollBiometric() {
  const cred = await navigator.credentials.create({ publicKey: {
    challenge: rand(32),
    rp: { name: 'Farvis' },
    user: { id: new TextEncoder().encode(uid), name: currentUser.email, displayName: currentUser.email },
    pubKeyCredParams: [{ type: 'public-key', alg: -7 }, { type: 'public-key', alg: -257 }],
    authenticatorSelection: { authenticatorAttachment: 'platform', userVerification: 'required', residentKey: 'preferred' },
    extensions: { prf: {} }
  }});
  if (!cred.getClientExtensionResults().prf?.enabled) {
    throw new Error('This device does not support biometric vault keys (WebAuthn PRF).');
  }
  const secret = await prfSecret(cred.rawId);
  localStorage.setItem(prfStorageKey(), JSON.stringify({
    credId: toB64(cred.rawId),
    wrapped: await wrapDek(dek, await kekFromBytes(secret))
  }));
}

async function offerBiometricEnrollment() {
  if (!window.PublicKeyCredential || localStorage.getItem(prfStorageKey())) return;
  const enable = await askConfirm("Unlock the vault with fingerprint / Windows Hello on this device?",
    { ok: 'Enable', cancel: 'Not now' });
  if (!enable) {
    localStorage.setItem(prfStorageKey(), JSON.stringify({ declined: true }));
    return;
  }
  try { await enrollBiometric(); showToast("Biometric unlock enabled!"); }
  catch (e) { showToast(e.message || "Biometric setup failed.", { error: true }); }
}

async function unlockVault() {
  const saved = loadPrf();
  if (!window.PublicKeyCredential) return showToast("Biometrics aren't available in this browser.", { error: true });
  if (!saved || !saved.credId) {
    localStorage.removeItem(prfStorageKey()); // clear a previous "declined"
    return showToast("Unlock with your passphrase once to turn on biometrics here.");
  }
  try {
    const secret = await prfSecret(fromB64(saved.credId));
    dek = await unwrapDek(saved.wrapped, await kekFromBytes(secret));
  } catch (e) { return showToast("Biometric unlock didn't work. Use your passphrase.", { error: true }); }
  await onVaultUnlocked();
}

function forgotPassphrase() {
  askConfirm("The vault passphrase is never stored, so it can't be reset by email.\n\n" +
             "If biometrics are enabled on this device, unlock with them, then use \"Change Passphrase\". " +
             "Otherwise the encrypted vault data can't be recovered.", { ok: 'Got it', cancel: null });
}

async function changePassphrase() {
  if (!dek) return showToast("Unlock the vault first.", { error: true });
  const pass    = document.getElementById('new-vault-pin').value;
  const confirm = document.getElementById('new-vault-pin-confirm').value;
  if (pass.length < 8) return invalid('new-vault-pin', "Passphrase must be at least 8 characters.");
  if (pass !== confirm) return invalid('new-vault-pin-confirm', "Passphrases don't match.");
  try {
    showToast("Re-wrapping vault key...");
    const salt = newSalt();
    await setDoc(doc(db, "keys", uid), { salt, iter: PBKDF2_ITER, wrapped: await wrapDek(dek, await deriveKek(pass, salt)) });
    showToast("Passphrase changed!");
    toggleModal('reset-pin-modal', false);
    document.getElementById('new-vault-pin').value = '';
    document.getElementById('new-vault-pin-confirm').value = '';
  } catch (e) { showToast("Couldn't change the passphrase. Check your connection and try again.", { error: true }); }
}

async function saveNewVaultItem() {
  if (!dek) return showToast("Unlock the vault first.", { error: true });
  const type      = document.getElementById('vaultType').value;
  const title     = document.getElementById('vaultTitle').value.trim();
  const number    = document.getElementById('vaultNumber').value.trim();
  const frontFile = document.getElementById('vaultFrontImage').files[0];
  const backFile  = document.getElementById('vaultBackImage').files[0];
  if (!title) return invalid('vaultTitle', "Give the document a title.");
  try {
    showToast("Compressing & encrypting...");
    const frontImgUrl = frontFile ? await compressImage(frontFile) : null;
    const backImgUrl  = backFile  ? await compressImage(backFile)  : null;
    const enc = await encryptJSON({ type, title, number, frontImgUrl, backImgUrl }, dek);
    addDoc(collection(db, "vault"), { enc, owner: uid, createdAt: serverTimestamp() }).catch(writeFailed('save the document'));
    toggleModal('add-vault-modal', false);
    showToast("Saved to the vault");
    document.getElementById('vaultTitle').value  = '';
    document.getElementById('vaultNumber').value = '';
    document.getElementById('vaultFrontImage').value = '';
    document.getElementById('vaultBackImage').value  = '';
  } catch (e) { showToast(e.message || "Couldn't save the document. Try again.", { error: true }); }
}

function listenToVault() {
  return onSnapshot(query(collection(db, "vault"), where("owner", "==", uid)), (snapshot) => {
    vaultDocs = snapshot.docs;
    if (dek && vaultDocs.some(d => !d.data().enc)) encryptLegacyDocs();
    renderVault();
  });
}

// Decrypted data is rendered only while unlocked and wiped on lock.
async function renderVault() {
  const container = document.getElementById('vault-data');
  if (!container) return;
  const key = dek;
  if (!key) { container.innerHTML = ''; return; }
  const items = await Promise.all(vaultDocs.filter(d => d.data().enc).map(async d => {
    try { return { id: d.id, ...(await decryptJSON(d.data().enc, key)) }; }
    catch { return null; }
  }));
  if (dek !== key) return; // locked or re-keyed while decrypting
  vaultItems.clear();
  container.innerHTML = items.filter(Boolean).map(item => {
    vaultItems.set(item.id, item);
    const type = item.type || '';
    let iconClass = 'fa-file-shield';
    if (type.includes('NID'))      iconClass = 'fa-id-card';
    if (type.includes('Passport')) iconClass = 'fa-passport';
    if (type.includes('Bank'))     iconClass = 'fa-building-columns';
    if (type.includes('Student'))  iconClass = 'fa-id-badge';
    return `
        <div class="vault-card interactive-card" onclick="openDocumentModal('${item.id}')">
          <i class="fa-solid ${iconClass}"></i>
          <h5 style="color:var(--text-gray);font-size:0.8rem;text-transform:uppercase;margin-bottom:5px;">${escapeHTML(type)}</h5>
          <h4>${escapeHTML(item.title)}</h4>
          <p class="hidden-data"><i class="fa-solid fa-microchip" style="color:#d4af37;font-size:1.2rem;vertical-align:middle;margin-right:6px;"></i>${escapeHTML(item.number)}</p>
          <span class="view-hint">Click to view details</span>
        </div>`;
  }).join('') || emptyState('fa-shield-halved', 'Vault is empty. Add your NID, passport or bank details.');
}

// --- Quick-copy identity chips (label + value, both encrypted) ---
function listenToChips() {
  return onSnapshot(query(collection(db, "chips"), where("owner", "==", uid)), (snapshot) => {
    chipDocs = snapshot.docs;
    renderChips();
  });
}

async function renderChips() {
  const row = document.getElementById('chip-row');
  if (!row) return;
  const key = dek;
  if (!key) { row.innerHTML = ''; chipItems.clear(); return; }
  const items = await Promise.all(chipDocs.map(async d => {
    try { return { id: d.id, ...(await decryptJSON(d.data().enc, key)) }; }
    catch { return null; }
  }));
  if (dek !== key) return;
  chipItems.clear();
  row.innerHTML = items.filter(Boolean)
    .sort((a, b) => String(a.label).localeCompare(String(b.label)))
    .map(c => {
      chipItems.set(c.id, c);
      return `<div class="id-chip" onclick="copyChip('${c.id}')" title="Click to copy"><b>${escapeHTML(c.label)}</b><span>${escapeHTML(c.value)}</span><button onclick="event.stopPropagation();deleteDocItem('chips','${c.id}')" aria-label="Remove chip">&times;</button></div>`;
    }).join('');
}

function copyChip(id) {
  const chip = chipItems.get(id);
  if (chip) copyText(chip.value);
}

async function saveChip() {
  if (!dek) return showToast("Unlock the vault first.", { error: true });
  const label = document.getElementById('chipLabel').value.trim();
  const value = document.getElementById('chipValue').value.trim();
  if (!label) return invalid('chipLabel', "Give the chip a label.");
  if (!value) return invalid('chipValue', "Enter the value to copy.");
  try {
    const enc = await encryptJSON({ label, value }, dek);
    addDoc(collection(db, "chips"), { enc, owner: uid, createdAt: serverTimestamp() }) // not awaited: works offline
      .catch(writeFailed('save the chip'));
    toggleModal('add-chip-modal', false);
    showToast("Chip saved!");
    document.getElementById('chipLabel').value = '';
    document.getElementById('chipValue').value = '';
  } catch (e) { showToast("Couldn't save the chip. Try again.", { error: true }); }
}

function openDocumentModal(id) {
  const item = vaultItems.get(id);
  if (!item) return;
  document.getElementById('doc-title').innerText  = item.title || '';
  document.getElementById('doc-number').innerText = item.number || '';
  const fImg = document.getElementById('doc-front-image');
  const fPh  = document.getElementById('front-placeholder-icon');
  if (item.frontImgUrl) { fImg.src = item.frontImgUrl; fImg.style.display = 'block'; fPh.style.display = 'none'; }
  else                  { fImg.style.display = 'none'; fPh.style.display = 'flex'; }
  const bImg = document.getElementById('doc-back-image');
  const bPh  = document.getElementById('back-placeholder-icon');
  if (item.backImgUrl)  { bImg.src = item.backImgUrl; bImg.style.display = 'block'; bPh.style.display = 'none'; }
  else                  { bImg.style.display = 'none'; bPh.style.display = 'flex'; }
  document.getElementById('document-modal').classList.add('show');
}
function closeDocumentModal() {
  document.getElementById('document-modal').classList.remove('show');
  document.getElementById('doc-front-image').src = '';
  document.getElementById('doc-back-image').src = '';
}

// ==========================================
// --- PLATFORM CREDENTIALS CONTROLLER ---
// ==========================================
// Google S2 favicon service (Clearbit's logo API is dead).
function getLogoFromUrl(link) {
  const url = safeUrl(link);
  return url ? `https://www.google.com/s2/favicons?domain=${new URL(url).hostname}&sz=128` : null;
}

function previewCredLogo(val) {
  const preview = document.getElementById('cred-logo-preview');
  if (!val || !val.includes('.')) { preview.style.display = 'none'; return; }
  const logoUrl = getLogoFromUrl(val);
  if (logoUrl) {
    preview.src = logoUrl;
    preview.style.display = 'block';
    preview.onerror = () => { preview.style.display = 'none'; };
  }
}

async function saveNewCredential() {
  if (!dek) return showToast("Unlock the vault first: credentials are encrypted with the vault key.", { error: true });
  const platformName = document.getElementById('platformName').value.trim();
  const link         = document.getElementById('platformLink').value.trim();
  const username     = document.getElementById('username').value.trim();
  const password     = document.getElementById('password').value.trim();
  if (!platformName) return invalid('platformName', "Enter the platform name.");
  if (!username) return invalid('username', "Enter the username.");

  const data = { platformName, link, enc: await encryptJSON({ username, password }, dek), owner: uid, createdAt: serverTimestamp() };
  if (link) data.imageUrl = getLogoFromUrl(link);

  const fileInput = document.getElementById('platformImage');
  if (fileInput && fileInput.files[0]) {
    try { data.imageUrl = await compressImage(fileInput.files[0], 200, 0.8); }
    catch (e) { console.warn("Logo compression error context."); }
  }

  addDoc(collection(db, "credentials"), data).catch(writeFailed('save the credential'));
  toggleModal('add-cred-modal', false);
  showToast("Credential saved");
  ['platformName','platformLink','username','password'].forEach(id => document.getElementById(id).value = '');
  if (fileInput) fileInput.value = '';
  const preview = document.getElementById('cred-logo-preview');
  if (preview) { preview.src = ''; preview.style.display = 'none'; }
}

function listenToCredentials() {
  return onSnapshot(query(collection(db, "credentials"), where("owner", "==", uid)), (snapshot) => {
    credDocs = snapshot.docs;
    if (dek && credDocs.some(d => !d.data().enc)) encryptLegacyDocs();
    renderCredentials();
  });
}

async function renderCredentials() {
  const container = document.getElementById('cred-data');
  if (!container) return;
  const key = dek;
  const items = await Promise.all(credDocs.map(async d => {
    const data = d.data();
    let secret = null; // { username, password } when readable
    if (data.enc && key) { try { secret = await decryptJSON(data.enc, key); } catch {} }
    else if (!data.enc) secret = { username: data.username, password: data.password }; // legacy, until unlock
    return { id: d.id, ...data, secret };
  }));
  if (dek !== key) return;
  credItems.clear();
  container.innerHTML = items.map(item => {
    credItems.set(item.id, item);
    let imageUrl = item.imageUrl;
    if (imageUrl && imageUrl.includes('logo.clearbit.com')) imageUrl = item.link ? getLogoFromUrl(item.link) : null;
    const logoHTML = imageUrl
      ? `<img src="${escapeHTML(imageUrl)}" alt="logo" class="cred-logo" onerror="this.style.display='none';this.nextElementSibling.style.display='inline-block';"><i class="fa-solid fa-key" style="display:none;font-size:2rem;color:var(--maroon);"></i>`
      : `<i class="fa-solid fa-key" style="font-size:2rem;color:var(--maroon);"></i>`;

    const href = item.link ? safeUrl(item.link) : '';
    const linkHTML = href
      ? `<a href="${escapeHTML(href)}" target="_blank" rel="noopener" style="font-size:0.8rem;color:var(--text-gray);display:inline-block;margin-top:4px;word-break:break-all;"><i class="fa-solid fa-link"></i> ${escapeHTML(item.link.replace(/https?:\/\//,'').substring(0,40))}</a>`
      : '';

    const usernameHTML = item.secret
      ? escapeHTML(item.secret.username)
      : `<i class="fa-solid fa-lock"></i> Unlock vault to view`;
    const showCopy = item.secret ? !!item.secret.password : !!item.enc;

    return `
        <div class="cred-card">
          <div class="cred-logo-wrap">${logoHTML}</div>
          <div class="cred-info">
            <div class="cred-title-row">
              <h4>${escapeHTML(item.platformName)}</h4>
              <div class="cred-menu-wrap">
                <button class="movie-menu-btn cred-menu-container" onclick="toggleCredMenu(event,this)" style="width:44px;height:44px;padding:0;display:flex;align-items:center;justify-content:center;background:transparent;color:var(--text-gray);">
                  <i class="fa-solid fa-ellipsis-vertical" style="pointer-events:none;font-size:1.2rem;"></i>
                </button>
                <div class="cred-dropdown">
                  <button onclick="event.stopPropagation();deleteDocItem('credentials','${item.id}')"><i class="fa-solid fa-trash" style="color:#d32f2f;margin-right:6px;"></i>Remove</button>
                </div>
              </div>
            </div>
            ${linkHTML}
            <p style="margin-top:6px;font-family:monospace;font-size:0.9rem;">${usernameHTML}</p>
            ${showCopy ? `<button class="copy-btn" style="margin-top:4px;font-size:0.85rem;color:var(--maroon);background:none;border:none;cursor:pointer;padding:0;" onclick="copyCredPassword('${item.id}')"><i class="fa-regular fa-copy"></i> Copy Password</button>` : ''}
          </div>
        </div>`;
  }).join('') || emptyState('fa-key', 'No credentials yet. Click "Add New" to save one.');
}

function copyCredPassword(id) {
  const item = credItems.get(id);
  if (!item || !item.secret) return showToast("Unlock the vault first.", { error: true });
  copyText(item.secret.password);
}

function toggleCredMenu(event, btn) {
  event.stopPropagation();
  document.querySelectorAll('.cred-dropdown, .movie-dropdown').forEach(d => {
    if (d !== btn.nextElementSibling) d.classList.remove('show');
  });
  btn.nextElementSibling.classList.toggle('show');
}

// ==========================================
// --- MEDIA RETRIEVAL SERVICE ---
// ==========================================
// PUBLIC GITHUB REDACTED: Insert actual credential tokens before deployment pipelines execute.
const OMDB_API_KEY = "YOUR_OMDB_API_KEY"; 

function switchMovieTab(listId, btnElement) {
  currentMovieTab = listId; 
  document.querySelectorAll('#movie-status-tabs .m-tab').forEach(b => b.classList.remove('active'));
  btnElement.classList.add('active');
  document.getElementById('watched-list').style.display = 'none';
  document.getElementById('plan-to-watch-list').style.display = 'none';
  document.getElementById(listId + '-list').style.display = 'grid';
}

function filterMedia(type, btn) {
  document.querySelectorAll('#movies .movie-tabs:not(#movie-status-tabs) .m-tab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('.movie-card').forEach(card => {
    card.style.display = (type === 'All' || card.getAttribute('data-media-type') === type) ? 'block' : 'none';
  });
}

function openMovieModal(id) {
  const movie = movieItems.get(id);
  if (!movie) return;
  document.getElementById('modal-title').innerText = movie.title || '';
  document.getElementById('modal-duration').innerHTML = `<i class="fa-regular fa-clock"></i> ${escapeHTML(movie.duration)}`;
  document.getElementById('modal-genre').innerHTML   = `<i class="fa-solid fa-film"></i> ${escapeHTML(movie.genre)}`;
  document.getElementById('modal-plot-text').innerText = movie.plot || '';
  document.getElementById('modal-poster').src = safeUrl(movie.posterUrl || '');
  document.getElementById('modal-rating').innerHTML = `<i class="fa-solid fa-star"></i> ${escapeHTML(movie.rating)}`;
  document.getElementById('movie-modal').classList.add('show');
}
function closeMovieModal() { document.getElementById('movie-modal').classList.remove('show'); }

let currentRatingMovieId = null;

function toggleMovieMenu(event, btn) {
  event.stopPropagation();
  document.querySelectorAll('.movie-dropdown, .cred-dropdown').forEach(d => {
    if (d !== btn.nextElementSibling) d.classList.remove('show');
  });
  btn.nextElementSibling.classList.toggle('show');
}

function openRateModal(event, movieId) {
  event.stopPropagation();
  currentRatingMovieId = movieId;
  document.getElementById('new-rating-input').value = '';
  toggleModal('rate-movie-modal', true);
  event.target.closest('.movie-dropdown').classList.remove('show');
}

async function submitRating() {
  const newRating = document.getElementById('new-rating-input').value;
  if (!newRating || !currentRatingMovieId) return;
  updateDoc(doc(db, "movies", currentRatingMovieId), { rating: parseFloat(newRating).toFixed(1) })
    .catch(writeFailed('save the rating'));
  showToast("Rating saved");
  toggleModal('rate-movie-modal', false);
}

async function addNewMovie() {
  const inputEl   = document.getElementById('movie-search-input');
  const queryText = inputEl.value.trim();
  if (!queryText) return;

  if (OMDB_API_KEY.startsWith('YOUR_')) {
    return showToast("Title search needs an OMDB key in script.js. The IMDb buttons in Brave work without one.", { error: true });
  }
  const targetStatus = currentMovieTab;
  showToast("Fetching details...");
  const imdbId = queryText.match(/title\/(tt\d+)/)?.[1];
  const apiUrl = imdbId
    ? `https://www.omdbapi.com/?i=${imdbId}&apikey=${OMDB_API_KEY}`
    : `https://www.omdbapi.com/?t=${encodeURIComponent(queryText)}&apikey=${OMDB_API_KEY}`;

  try {
    const data = await (await fetch(apiUrl)).json();
    if (data.Response === "False") return showToast(`No match for "${queryText}". Check the spelling or paste the IMDb link.`, { error: true });
    addDoc(collection(db, "movies"), {
      title: data.Title, duration: data.Runtime, genre: data.Genre, plot: data.Plot,
      posterUrl: data.Poster !== "N/A" ? data.Poster : "https://via.placeholder.com/300x450?text=No+Poster",
      rating: data.imdbRating !== "N/A" ? data.imdbRating : "0.0",
      type: data.Type,
      status: targetStatus,
      owner: uid, createdAt: serverTimestamp()
    }).catch(writeFailed('add the movie'));
    inputEl.value = '';
    showToast(`Added to ${targetStatus === 'watched' ? 'Watched' : 'Plan to Watch'}`);
  } catch {
    showToast("Couldn't reach the movie database. Check your connection.", { error: true, action: 'Retry', onAction: addNewMovie });
  }
}

function listenToMovies() {
  return onSnapshot(query(collection(db, "movies"), where("owner", "==", uid)), (snapshot) => {
    const watchedList = document.getElementById('watched-list');
    const planList    = document.getElementById('plan-to-watch-list');
    if (!watchedList || !planList) return;
    const watchedHTML = [], planHTML = [];
    movieItems.clear();
    snapshot.forEach(docSnap => {
      const movie = docSnap.data();
      movieItems.set(docSnap.id, movie);
      const badgeColor = movie.type === 'series' ? '#0a66c2' : 'var(--maroon)';
      const badge      = movie.type === 'series' ? 'Series' : 'Movie';
      const html = `
        <div class="movie-card" data-media-type="${escapeHTML(movie.type)}" onclick="openMovieModal('${docSnap.id}')">
          <div class="movie-poster" style="background-image:url('${cssUrl(movie.posterUrl || '')}')">
            <div class="rating" style="left:10px;"><i class="fa-solid fa-star"></i> ${escapeHTML(movie.rating)}</div>
            <div class="rating" style="right:10px;left:auto;background:${badgeColor};font-size:0.75rem;">${badge}</div>
            <div class="movie-menu-container" style="top:40px;right:10px;">
              <button class="movie-menu-btn" onclick="toggleMovieMenu(event,this)"><i class="fa-solid fa-ellipsis-vertical"></i></button>
              <div class="movie-dropdown">
                <button onclick="openRateModal(event,'${docSnap.id}')">Rate</button>
                <button onclick="moveMovie(event,'${docSnap.id}','${movie.status==='watched'?'plan-to-watch':'watched'}')">Move to ${movie.status==='watched'?'Plan':'Watched'}</button>
                <button onclick="deleteDocItem('movies','${docSnap.id}')" class="danger-text">Remove</button>
              </div>
            </div>
          </div>
          <div class="movie-info"><h4>${escapeHTML(movie.title)}</h4><p>${escapeHTML((movie.genre||'').split(',')[0])}</p></div>
        </div>`;
      (movie.status === 'watched' ? watchedHTML : planHTML).push(html);
    });
    const noMedia = emptyState('fa-film', 'Nothing here yet. Search a title above, or use the IMDb buttons in Brave.');
    dataChanged();
    watchedList.innerHTML = watchedHTML.join('') || noMedia;
    planList.innerHTML    = planHTML.join('') || noMedia;
  });
}

// The browser extension hands IMDb JSON-LD over as ?addMovie=<json> (no OMDB key needed).
async function importMovieFromUrl() {
  const raw = new URLSearchParams(location.search).get('addMovie');
  if (!raw) return;
  history.replaceState(history.state, '', location.pathname);
  let m;
  try { m = JSON.parse(raw); } catch { return; }
  if (!/^tt\d+$/.test(m.imdbId || '')) return;
  const str = v => String(v ?? '').slice(0, 2000);
  const status = m.status === 'watched' ? 'watched' : 'plan-to-watch';
  let posterUrl = safeUrl(str(m.posterUrl));
  try { if (!new URL(posterUrl).hostname.endsWith('media-amazon.com')) posterUrl = ''; } catch { posterUrl = ''; }

  switchTab('movies', document.querySelector('.nav-links li[onclick*="movies"]'));
  switchMovieTab(status, document.querySelectorAll('#movie-status-tabs .m-tab')[status === 'watched' ? 0 : 1]);
  try {
    const existing = await getDocs(query(collection(db, "movies"), where("owner", "==", uid), where("imdbId", "==", m.imdbId)));
    if (!existing.empty) {
      updateDoc(existing.docs[0].ref, { status });
    } else {
      addDoc(collection(db, "movies"), {
        imdbId: m.imdbId, title: str(m.title), duration: str(m.duration), genre: str(m.genre),
        plot: str(m.plot), posterUrl, rating: str(m.rating) || '0.0',
        type: m.type === 'series' ? 'series' : 'movie',
        status, owner: uid, createdAt: serverTimestamp()
      });
    }
    showToast(`${str(m.title)} → ${status === 'watched' ? 'Watched' : 'Plan to Watch'}`);
    setTimeout(() => window.close(), 1500); // only closes tabs the extension opened
  } catch (e) { showToast("Couldn't add the movie. Try the button on IMDb again.", { error: true }); }
}

function moveMovie(event, movieId, newStatus) {
  event.stopPropagation();
  updateDoc(doc(db, "movies", movieId), { status: newStatus }).catch(writeFailed('move the movie'));
  showToast(`Moved to ${newStatus === 'watched' ? 'Watched' : 'Plan to Watch'}`);
}

// ==========================================
// --- SHOPPING MODULE SYSTEM ---
// ==========================================
async function saveShoppingItem() {
  const desc  = document.getElementById('shopping-desc').value.trim();
  const link  = document.getElementById('shopping-link').value.trim();
  const price = document.getElementById('shopping-price').value.trim();
  if (!desc) return invalid('shopping-desc', "What do you need to buy?");
  addDoc(collection(db, "shopping"), { desc, link, price, bought: false, owner: uid, createdAt: serverTimestamp() })
    .catch(writeFailed('add the item'));
  toggleModal('add-shopping-modal', false);
  showToast("Item added");
  document.getElementById('shopping-desc').value  = '';
  document.getElementById('shopping-link').value  = '';
  document.getElementById('shopping-price').value = '';
}

function listenToShopping() {
  return onSnapshot(query(collection(db, "shopping"), where("owner", "==", uid)), (snapshot) => {
    appData.shopping = snapshot.docs.map(d => ({ id: d.id, ...d.data() }));
    dataChanged();
    const container = document.getElementById('shopping-list');
    if (!container) return;
    container.innerHTML = snapshot.docs.map(docSnap => {
      const item = docSnap.data();
      const bought = item.bought === true;
      const href = item.link ? safeUrl(item.link) : '';
      const linkHTML = href
        ? `<a href="${escapeHTML(href)}" target="_blank" rel="noopener" class="shop-link"><i class="fa-solid fa-arrow-up-right-from-square"></i> View Product</a>`
        : '';
      const priceHTML = item.price ? `<span class="shop-price"><i class="fa-solid fa-tag"></i> ${escapeHTML(item.price)}</span>` : '';
      return `
        <div class="shopping-card ${bought ? 'bought' : ''}" data-bought="${bought}">
          <button class="shop-check-btn" onclick="toggleBought('${docSnap.id}',${bought})" title="${bought ? 'Mark pending' : 'Mark bought'}">
            <i class="fa-solid ${bought ? 'fa-check-circle' : 'fa-circle'}"></i>
          </button>
          <div class="shop-info">
            <p class="shop-desc">${escapeHTML(item.desc)}</p>
            <div class="shop-meta">${priceHTML}${linkHTML}</div>
          </div>
          <button class="remove-btn" onclick="deleteDocItem('shopping','${docSnap.id}')"><i class="fa-solid fa-trash"></i></button>
        </div>`;
    }).join('') || emptyState('fa-cart-plus', 'Shopping list is empty.');
  });
}

function toggleBought(docId, currentBought) {
  updateDoc(doc(db, "shopping", docId), { bought: !currentBought }).catch(writeFailed('update the item'));
}

function filterShopping(filter, btn) {
  document.querySelectorAll('#shopping .m-tab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('.shopping-card').forEach(card => {
    const isBought = card.getAttribute('data-bought') === 'true';
    card.style.display =
      filter === 'all'     ? 'flex' :
      filter === 'bought'  ? (isBought  ? 'flex' : 'none') :
      filter === 'pending' ? (!isBought ? 'flex' : 'none') : 'flex';
  });
}

// ==========================================
// --- ATOMIC DELETION PASSTHROUGH ---
// ==========================================
// Deletes at once and offers Undo (re-creates the same document) instead of asking first.
async function deleteDocItem(collectionName, docId) {
  if (event) event.stopPropagation();
  const ref = doc(db, collectionName, docId);
  const data = (await getDocFromCache(ref).catch(() => getDoc(ref)).catch(() => null))?.data();
  deleteDoc(ref).catch(writeFailed('delete that'));
  showToast("Deleted", data && {
    action: 'Undo',
    onAction: () => { setDoc(ref, data).catch(writeFailed('restore it')); showToast("Restored"); }
  });
}

// ==========================================
// --- FARVIS ASSISTANT: app-data tools ---
// ==========================================
const todayISO = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
const daysUntil = iso => Math.round((new Date(iso + 'T00:00:00') - new Date(todayISO() + 'T00:00:00')) / 86400000);

function upcomingEvents(daysAhead = 30) {
  return appData.academic
    .filter(e => e.date && daysUntil(e.date) >= 0 && daysUntil(e.date) <= daysAhead)
    .sort((a, b) => a.date.localeCompare(b.date));
}

const farvisTools = {
  list_academic_events({ days_ahead = 30 } = {}) {
    return { today: todayISO(), events: upcomingEvents(days_ahead).map(({ type, title, date }) =>
      ({ type, title, date, days_left: daysUntil(date) })) };
  },
  add_academic_event({ type, title, date }) {
    if (!['Quiz', 'Assignment', 'Lab Test'].includes(type)) type = 'Assignment';
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date || '') || !title) return { error: 'need title and YYYY-MM-DD date' };
    addDoc(collection(db, "academic"), { type, title, date, owner: uid, createdAt: serverTimestamp() }); // offline-safe
    return { ok: true, added: { type, title, date } };
  },
  list_shopping_items({ include_bought = false } = {}) {
    return { items: appData.shopping.filter(i => include_bought || !i.bought)
      .map(({ desc, price, bought }) => ({ description: desc, price: price || '', bought: !!bought })) };
  },
  add_shopping_item({ description, price = '', link = '' }) {
    if (!description) return { error: 'description required' };
    addDoc(collection(db, "shopping"), { desc: description, link, price, bought: false, owner: uid, createdAt: serverTimestamp() });
    return { ok: true, added: description };
  },
  mark_shopping_bought({ description }) {
    const q = (description || '').toLowerCase();
    const item = appData.shopping.find(i => !i.bought && i.desc.toLowerCase().includes(q))
      || appData.shopping.find(i => !i.bought && q.includes(i.desc.toLowerCase()));
    if (!item) return { error: `no pending item matching "${description}"` };
    updateDoc(doc(db, "shopping", item.id), { bought: true });
    return { ok: true, marked: item.desc };
  },
  list_movies({ status } = {}) {
    return { movies: [...movieItems.values()].filter(m => !status || m.status === status)
      .map(({ title, type, status: s, rating, genre }) => ({ title, type, status: s, rating, genre })) };
  }
};

window.farvis = {
  data: appData,
  tools: farvisTools,
  upcomingEvents,
  daysUntil,
  userName: () => {
    const raw = (currentUser?.displayName || currentUser?.email || '').split('@')[0].replace(/[._\d]+/g, ' ').trim();
    return raw ? raw.split(' ')[0].replace(/^./, c => c.toUpperCase()) : '';
  }
};

// ==========================================
// --- GLOBAL CORE RUNTIME ROUTING ---
// ==========================================
window.switchAuthTab       = switchAuthTab;
window.showForgotPassword  = showForgotPassword;
window.handleSignup        = handleSignup;
window.handleLogin         = handleLogin;
window.sendResetEmail      = sendResetEmail;
window.handleLogout        = handleLogout;
window.switchTab           = switchTab;
window.filterItems         = filterItems;
window.copyText            = copyText;
window.showToast           = showToast;
window.askConfirm          = askConfirm;
window.settleConfirm       = settleConfirm;
window.installApp          = installApp;
window.resendVerification  = resendVerification;
window.recheckVerification = recheckVerification;
window.toggleModal         = toggleModal;
window.unlockVault         = unlockVault;
window.verifyPin           = verifyPin;
window.changePassphrase    = changePassphrase;
window.forgotPassphrase    = forgotPassphrase;
window.saveNewVaultItem    = saveNewVaultItem;
window.openDocumentModal   = openDocumentModal;
window.closeDocumentModal  = closeDocumentModal;
window.previewCredLogo     = previewCredLogo;
window.saveNewCredential   = saveNewCredential;
window.toggleCredMenu      = toggleCredMenu;
window.copyCredPassword    = copyCredPassword;
window.copyChip            = copyChip;
window.saveChip            = saveChip;
window.switchMovieTab      = switchMovieTab;
window.filterMedia         = filterMedia;
window.openMovieModal      = openMovieModal;
window.closeMovieModal     = closeMovieModal;
window.toggleMovieMenu     = toggleMovieMenu;
window.openRateModal       = openRateModal;
window.submitRating        = submitRating;
window.addNewMovie         = addNewMovie;
window.moveMovie           = moveMovie;
window.deleteDocItem       = deleteDocItem;
window.saveAcademicEvent   = saveAcademicEvent;
window.filterAcademic      = filterAcademic;
window.saveShoppingItem    = saveShoppingItem;
window.filterShopping      = filterShopping;
window.toggleBought        = toggleBought;