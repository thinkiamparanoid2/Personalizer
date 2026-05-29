import { db } from './firebase.js';
import {
  collection, addDoc, serverTimestamp,
  onSnapshot, deleteDoc, doc, updateDoc, getDocs, query, where
} from 'https://www.gstatic.com/firebasejs/10.8.1/firebase-firestore.js';

// ==========================================
// --- GLOBAL STATE ---
// ==========================================
let currentUserEmail = null;
let currentMovieTab  = 'watched'; 
let deferredPrompt;

// ==========================================
// --- PWA CAPTURED ENGINE CHANNELS ---
// ==========================================
window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  deferredPrompt = e;
  const installBtn = document.getElementById('install-app-btn');
  if (installBtn) {
    installBtn.style.display = 'block';
    installBtn.addEventListener('click', async () => {
      deferredPrompt.prompt();
      const { outcome } = await deferredPrompt.userChoice;
      if (outcome === 'accepted') console.log('User installed PWA application shell container.');
      deferredPrompt = null;
      installBtn.style.display = 'none';
    });
  }
});

window.addEventListener('appinstalled', () => {
  const installBtn = document.getElementById('install-app-btn');
  if (installBtn) installBtn.style.display = 'none';
});

// ==========================================
// --- INIT & THEME ENGINE ---
// ==========================================
document.addEventListener('DOMContentLoaded', () => {
  const themeToggle = document.getElementById('theme-toggle');
  const body = document.body;
  const icon = themeToggle.querySelector('i');

  if (localStorage.getItem('theme') === 'dark') {
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

  const pinInput = document.getElementById('vault-pin');
  if (pinInput) pinInput.addEventListener('keypress', e => { if (e.key === 'Enter') verifyPin(); });

  const savedUser = localStorage.getItem('loggedInUser');
  if (savedUser) {
    currentUserEmail = savedUser;
    document.getElementById('auth-screen').style.display = 'none';
    document.getElementById('main-dashboard').style.display = 'flex';
    startListeners();
  }
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
  document.getElementById('recover-section').style.display = 'none';
  document.getElementById('revealed-password').innerText = '';
  document.getElementById('forgot-email').value = '';
  document.getElementById('forgot-answer').value = '';
}

async function handleSignup() {
  try {
    const email = document.getElementById('signup-email').value.trim().toLowerCase();
    const pass  = document.getElementById('signup-password').value;
    const q     = document.getElementById('signup-question').value;
    const ans   = document.getElementById('signup-answer').value.trim().toLowerCase();
    if (!email || !pass || !ans) return alert("Please fill all fields.");
    showToast("Checking database...");
    const snapshot = await getDocs(query(collection(db, "users"), where("email", "==", email)));
    if (!snapshot.empty) return alert("Email already exists. Please login.");
    await addDoc(collection(db, "users"), { email, password: btoa(pass), question: q, answer: ans });
    showToast("Account Created! Please login.");
    switchAuthTab('login');
  } catch (error) {
    console.error(error);
  }
}

async function handleLogin() {
  try {
    const email = document.getElementById('login-email').value.trim().toLowerCase();
    const pass  = document.getElementById('login-password').value;
    if (!email || !pass) return alert("Enter email and password.");
    showToast("Authenticating...");
    const snapshot = await getDocs(query(collection(db, "users"), where("email", "==", email)));
    if (snapshot.empty) return alert("User not found.");
    const user = snapshot.docs[0].data();
    if (atob(user.password) === pass) {
      currentUserEmail = email;
      localStorage.setItem('loggedInUser', email);
      document.getElementById('auth-screen').style.display = 'none';
      document.getElementById('main-dashboard').style.display = 'flex';
      startListeners();
    } else {
      alert("Incorrect Password.");
    }
  } catch (error) {
    alert("Database Connection Error.");
  }
}

let tempForgotDoc = null;
async function fetchSecurityQuestion() {
  try {
    const email = document.getElementById('forgot-email').value.trim().toLowerCase();
    if (!email) return alert("Enter your email address.");
    showToast("Looking up account...");
    const snapshot = await getDocs(query(collection(db, "users"), where("email", "==", email)));
    if (snapshot.empty) return alert("No account found with that email.");
    tempForgotDoc = snapshot.docs[0].data();
    document.getElementById('recovered-question').innerText = tempForgotDoc.question;
    document.getElementById('recover-section').style.display = 'block';
    showToast("Security question loaded!");
  } catch (err) {
    alert("Could not reach database.");
  }
}

function recoverPassword() {
  const ans = document.getElementById('forgot-answer').value.trim().toLowerCase();
  if (!tempForgotDoc) return alert("Please fetch your security question first.");
  if (ans === tempForgotDoc.answer) {
    document.getElementById('revealed-password').innerText = "Your password is: " + atob(tempForgotDoc.password);
  } else {
    alert("Incorrect answer. Try again.");
  }
}

function handleLogout() {
  currentUserEmail = null;
  localStorage.removeItem('loggedInUser');
  ['vault-data','cred-data','watched-list','plan-to-watch-list','academic-list','shopping-list']
    .forEach(id => { const el = document.getElementById(id); if (el) el.innerHTML = ''; });
  const vaultLock = document.getElementById('vault-lock');
  if (vaultLock) vaultLock.classList.remove('hidden');
  document.getElementById('main-dashboard').style.display = 'none';
  document.getElementById('auth-screen').style.display = 'flex';
  switchAuthTab('login');
}

function startListeners() {
  listenToVault();
  listenToMovies();
  listenToCredentials();
  listenToAcademic();
  listenToShopping();
}

// ==========================================
// --- NAVIGATION & DYNAMIC FILTERS ---
// ==========================================
function switchTab(tabId, clickedElement) {
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active-tab'));
  document.querySelectorAll('.nav-links li').forEach(l => l.classList.remove('active'));
  document.getElementById(tabId).classList.add('active-tab');
  if (clickedElement) clickedElement.classList.add('active');
  if (tabId !== 'vault') {
    document.getElementById('vault-lock').classList.remove('hidden');
    const pin = document.getElementById('vault-pin');
    if (pin) pin.value = '';
  }
  document.getElementById('global-search').value = '';
  filterItems();
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

function showToast(message) {
  const toast = document.getElementById("toast");
  if (message) toast.innerText = message;
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 2500);
}

function toggleModal(modalId, show) {
  document.getElementById(modalId).classList.toggle('show', show);
}

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
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('Image processing exception.')); };
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
  if (!title || !date) return alert("Enter title and date");
  await addDoc(collection(db, "academic"), { type, title, date, owner: currentUserEmail, createdAt: serverTimestamp() });
  toggleModal('add-academic-modal', false);
  showToast("Event Added!");
  document.getElementById('academicTitle').value = '';
  document.getElementById('academicDate').value = '';
}

function listenToAcademic() {
  onSnapshot(query(collection(db, "academic"), where("owner", "==", currentUserEmail)), (snapshot) => {
    const container = document.getElementById('academic-list');
    if (!container) return;
    container.innerHTML = '';
    snapshot.forEach(docSnap => {
      const item = docSnap.data();
      const icon = item.type === 'Quiz' ? 'fa-pen-to-square' : item.type === 'Assignment' ? 'fa-file-lines' : 'fa-flask';
      container.innerHTML += `
        <div class="academic-card" data-type="${item.type}">
          <i class="fa-solid ${icon}"></i>
          <div class="academic-info">
            <h4>${item.title}</h4>
            <p>${item.type} &bull; ${item.date}</p>
          </div>
          <button class="remove-btn" onclick="deleteDocItem('academic','${docSnap.id}')"><i class="fa-solid fa-trash"></i></button>
        </div>`;
    });
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
function unlockVault() { alert("Biometrics initialization failed. Utilize security structural access PIN."); }

function verifyPin() {
  const pinInput  = document.getElementById('vault-pin');
  const lockScreen = document.getElementById('vault-lock');
  const currentPin = localStorage.getItem(`${currentUserEmail}_vaultPin`) || '1234';
  if (pinInput.value === currentPin) {
    lockScreen.classList.add('hidden');
    pinInput.value = '';
    pinInput.style.borderColor = 'var(--border-color)';
  } else {
    pinInput.style.borderColor = 'red';
    setTimeout(() => pinInput.style.borderColor = 'var(--border-color)', 500);
  }
}

async function handlePinReset() {
  const pass   = document.getElementById('reset-pin-password').value;
  const newPin = document.getElementById('new-vault-pin').value;
  const email  = currentUserEmail || localStorage.getItem('loggedInUser');
  if (!pass || !newPin) return alert("Please fill all fields.");
  if (newPin.length !== 4 || isNaN(newPin)) return alert("PIN must be exactly 4 digits.");
  showToast("Verifying...");
  try {
    const snapshot = await getDocs(query(collection(db, "users"), where("email", "==", email)));
    if (snapshot.empty) return alert("Session execution error.");
    if (atob(snapshot.docs[0].data().password) === pass) {
      localStorage.setItem(`${email}_vaultPin`, newPin);
      showToast("PIN Reset Successfully!");
      toggleModal('reset-pin-modal', false);
      document.getElementById('reset-pin-password').value = '';
      document.getElementById('new-vault-pin').value = '';
    } else { alert("Incorrect Login Password."); }
  } catch (e) { alert("Error mapping authentication variables."); }
}

async function saveNewVaultItem() {
  const type      = document.getElementById('vaultType').value;
  const title     = document.getElementById('vaultTitle').value.trim();
  const number    = document.getElementById('vaultNumber').value.trim();
  const frontFile = document.getElementById('vaultFrontImage').files[0];
  const backFile  = document.getElementById('vaultBackImage').files[0];
  if (!title) return alert("Please enter a document title.");
  try {
    showToast("Compressing & securing...");
    const frontBase64 = frontFile ? await compressImage(frontFile) : null;
    const backBase64  = backFile  ? await compressImage(backFile)  : null;
    await addDoc(collection(db, "vault"), { type, title, number, frontImgUrl: frontBase64, backImgUrl: backBase64, owner: currentUserEmail, createdAt: serverTimestamp() });
    toggleModal('add-vault-modal', false);
    showToast("Item Secured!");
    document.getElementById('vaultTitle').value  = '';
    document.getElementById('vaultNumber').value = '';
    document.getElementById('vaultFrontImage').value = '';
    document.getElementById('vaultBackImage').value  = '';
  } catch (e) { alert("Write access operation failed."); }
}

function listenToVault() {
  onSnapshot(query(collection(db, "vault"), where("owner", "==", currentUserEmail)), (snapshot) => {
    const container = document.getElementById('vault-data');
    if (!container) return;
    container.innerHTML = '';
    snapshot.forEach(docSnap => {
      const item = docSnap.data();
      let iconClass = 'fa-file-shield';
      if (item.type.includes('NID'))     iconClass = 'fa-id-card';
      if (item.type.includes('Passport')) iconClass = 'fa-passport';
      if (item.type.includes('Bank'))    iconClass = 'fa-building-columns';
      if (item.type.includes('Student')) iconClass = 'fa-id-badge';
      const fUrl = item.frontImgUrl ? `'${item.frontImgUrl}'` : 'null';
      const bUrl = item.backImgUrl  ? `'${item.backImgUrl}'`  : 'null';
      const safeTitle  = item.title.replace(/\\/g,'\\\\').replace(/'/g,"\\'");
      const safeNumber = (item.number||'').replace(/\\/g,'\\\\').replace(/'/g,"\\'");
      container.innerHTML += `
        <div class="vault-card interactive-card" onclick="openDocumentModal('${safeTitle}','${safeNumber}',${fUrl},${bUrl})">
          <i class="fa-solid ${iconClass}"></i>
          <h5 style="color:var(--text-gray);font-size:0.8rem;text-transform:uppercase;margin-bottom:5px;">${item.type}</h5>
          <h4>${item.title}</h4>
          <p class="hidden-data"><i class="fa-solid fa-microchip" style="color:#d4af37;font-size:1.2rem;vertical-align:middle;margin-right:6px;"></i>${item.number||''}</p>
          <span class="view-hint">Click to view details</span>
        </div>`;
    });
  });
}

function openDocumentModal(title, number, frontImgUrl = null, backImgUrl = null) {
  document.getElementById('doc-title').innerText  = title;
  document.getElementById('doc-number').innerText = number;
  const fImg = document.getElementById('doc-front-image');
  const fPh  = document.getElementById('front-placeholder-icon');
  if (frontImgUrl) { fImg.src = frontImgUrl; fImg.style.display = 'block'; fPh.style.display = 'none'; }
  else             { fImg.style.display = 'none'; fPh.style.display = 'flex'; }
  const bImg = document.getElementById('doc-back-image');
  const bPh  = document.getElementById('back-placeholder-icon');
  if (backImgUrl)  { bImg.src = backImgUrl; bImg.style.display = 'block'; bPh.style.display = 'none'; }
  else             { bImg.style.display = 'none'; bPh.style.display = 'flex'; }
  document.getElementById('document-modal').classList.add('show');
}
function closeDocumentModal() { document.getElementById('document-modal').classList.remove('show'); }

// ==========================================
// --- PLATFORM CREDENTIALS CONTROLLER ---
// ==========================================
function getLogoFromUrl(link) {
  try {
    const url = new URL(link.startsWith('http') ? link : 'https://' + link);
    return `https://logo.clearbit.com/${url.hostname}`;
  } catch { return null; }
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
  const platformName = document.getElementById('platformName').value.trim();
  const link         = document.getElementById('platformLink').value.trim();
  const username     = document.getElementById('username').value.trim();
  const password     = document.getElementById('password').value.trim();
  if (!platformName || !username) return alert("Please fill Platform Name and Username");

  const data = { platformName, link, username, password, owner: currentUserEmail, createdAt: serverTimestamp() };
  if (link) data.imageUrl = getLogoFromUrl(link);

  const fileInput = document.getElementById('platformImage');
  if (fileInput && fileInput.files[0]) {
    try { data.imageUrl = await compressImage(fileInput.files[0], 200, 0.8); }
    catch (e) { console.warn("Logo compression error context."); }
  }

  await addDoc(collection(db, "credentials"), data);
  toggleModal('add-cred-modal', false);
  showToast("Credential Saved!");
  ['platformName','platformLink','username','password'].forEach(id => document.getElementById(id).value = '');
  if (fileInput) fileInput.value = '';
  const preview = document.getElementById('cred-logo-preview');
  if (preview) { preview.src = ''; preview.style.display = 'none'; }
}

function listenToCredentials() {
  onSnapshot(query(collection(db, "credentials"), where("owner", "==", currentUserEmail)), (snapshot) => {
    const container = document.getElementById('cred-data');
    if (!container) return;
    container.innerHTML = '';
    snapshot.forEach(docSnap => {
      const item = docSnap.data();

      let logoHTML;
      if (item.imageUrl) {
        const fallbackSrc = item.link
          ? (() => { try { return `https://www.google.com/s2/favicons?domain=${new URL(item.link.startsWith('http')?item.link:'https://'+item.link).hostname}&sz=64`; } catch { return ''; } })()
          : '';
        const fallbackHandler = fallbackSrc
          ? `this.src='${fallbackSrc}';this.onerror=function(){this.style.display='none';this.nextElementSibling.style.display='inline-block';};`
          : `this.style.display='none';this.nextElementSibling.style.display='inline-block';`;
        logoHTML = `<img src="${item.imageUrl}" alt="logo" class="cred-logo" onerror="${fallbackHandler}"><i class="fa-solid fa-key" style="display:none;font-size:2rem;color:var(--maroon);"></i>`;
      } else {
        logoHTML = `<i class="fa-solid fa-key" style="font-size:2rem;color:var(--maroon);"></i>`;
      }

      const linkHTML = item.link
        ? `<a href="${item.link}" target="_blank" rel="noopener" style="font-size:0.8rem;color:var(--text-gray);display:inline-block;margin-top:4px;word-break:break-all;"><i class="fa-solid fa-link"></i> ${item.link.replace(/https?:\/\//,'').substring(0,40)}</a>`
        : '';

      container.innerHTML += `
        <div class="cred-card">
          <div class="cred-logo-wrap">${logoHTML}</div>
          <div class="cred-info">
            <div class="cred-title-row">
              <h4>${item.platformName}</h4>
              <div class="cred-menu-wrap">
                <button class="movie-menu-btn cred-menu-container" onclick="toggleCredMenu(event,this)" style="width:44px;height:44px;padding:0;display:flex;align-items:center;justify-content:center;background:transparent;color:var(--text-gray);">
                  <i class="fa-solid fa-ellipsis-vertical" style="pointer-events:none;font-size:1.2rem;"></i>
                </button>
                <div class="cred-dropdown">
                  <button onclick="event.stopPropagation();deleteDocItem('credentials','${docSnap.id}')"><i class="fa-solid fa-trash" style="color:#d32f2f;margin-right:6px;"></i>Remove</button>
                </div>
              </div>
            </div>
            ${linkHTML}
            <p style="margin-top:6px;font-family:monospace;font-size:0.9rem;">${item.username}</p>
            ${item.password ? `<button class="copy-btn" style="margin-top:4px;font-size:0.85rem;color:var(--maroon);background:none;border:none;cursor:pointer;padding:0;" onclick="copyText('${item.password.replace(/'/g,"\\'")}')"><i class="fa-regular fa-copy"></i> Copy Password</button>` : ''}
          </div>
        </div>`;
    });
  });
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

function openMovieModal(title, duration, genre, plot, posterUrl, rating) {
  document.getElementById('modal-title').innerText = title;
  document.getElementById('modal-duration').innerHTML = `<i class="fa-regular fa-clock"></i> ${duration}`;
  document.getElementById('modal-genre').innerHTML   = `<i class="fa-solid fa-film"></i> ${genre}`;
  document.getElementById('modal-plot-text').innerText = plot;
  document.getElementById('modal-poster').src = posterUrl;
  document.getElementById('modal-rating').innerHTML = `<i class="fa-solid fa-star"></i> ${rating}`;
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
  try {
    await updateDoc(doc(db, "movies", currentRatingMovieId), { rating: parseFloat(newRating).toFixed(1) });
    showToast("Rating saved!");
    toggleModal('rate-movie-modal', false);
  } catch { alert("Failed to commit tracking modifications."); }
}

async function addNewMovie() {
  const inputEl   = document.getElementById('movie-search-input');
  const queryText = inputEl.value.trim();
  if (!queryText) return;

  const targetStatus = currentMovieTab;
  showToast("Fetching details...");
  const apiUrl = queryText.includes('imdb.com/title/')
    ? `https://www.omdbapi.com/?i=${queryText.match(/title\/(tt\d+)/)[1]}&apikey=${OMDB_API_KEY}`
    : `https://www.omdbapi.com/?t=${encodeURIComponent(queryText)}&apikey=${OMDB_API_KEY}`;

  try {
    const data = await (await fetch(apiUrl)).json();
    if (data.Response === "False") return alert("Not found: " + data.Error);
    await addDoc(collection(db, "movies"), {
      title: data.Title, duration: data.Runtime, genre: data.Genre, plot: data.Plot,
      posterUrl: data.Poster !== "N/A" ? data.Poster : "https://via.placeholder.com/300x450?text=No+Poster",
      rating: data.imdbRating !== "N/A" ? data.imdbRating : "0.0",
      type: data.Type,
      status: targetStatus,   
      owner: currentUserEmail, createdAt: serverTimestamp()
    });
    inputEl.value = '';
    showToast(`Added to ${targetStatus === 'watched' ? 'Watched' : 'Plan to Watch'}!`);
  } catch { alert("API endpoint synchronization crash."); }
}

function listenToMovies() {
  onSnapshot(query(collection(db, "movies"), where("owner", "==", currentUserEmail)), (snapshot) => {
    const watchedList = document.getElementById('watched-list');
    const planList    = document.getElementById('plan-to-watch-list');
    if (!watchedList || !planList) return;
    watchedList.innerHTML = ''; planList.innerHTML = '';
    snapshot.forEach(docSnap => {
      const movie = docSnap.data();
      const safeTitle = movie.title.replace(/\\/g,'\\\\').replace(/'/g,"\\'");
      const safePlot  = (movie.plot||'').replace(/\\/g,'\\\\').replace(/'/g,"\\'");
      const badgeColor = movie.type === 'series' ? '#0a66c2' : 'var(--maroon)';
      const badge      = movie.type === 'series' ? 'Series' : 'Movie';
      const html = `
        <div class="movie-card" data-media-type="${movie.type}" onclick="openMovieModal('${safeTitle}','${movie.duration}','${movie.genre}','${safePlot}','${movie.posterUrl}','${movie.rating}')">
          <div class="movie-poster" style="background-image:url('${movie.posterUrl}')">
            <div class="rating" style="left:10px;"><i class="fa-solid fa-star"></i> ${movie.rating}</div>
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
          <div class="movie-info"><h4>${movie.title}</h4><p>${(movie.genre||'').split(',')[0]}</p></div>
        </div>`;
      if (movie.status === 'watched') watchedList.innerHTML += html;
      else planList.innerHTML += html;
    });
  });
}

async function moveMovie(event, movieId, newStatus) {
  event.stopPropagation();
  await updateDoc(doc(db, "movies", movieId), { status: newStatus });
  showToast("Moved!");
}

// ==========================================
// --- SHOPPING MODULE SYSTEM ---
// ==========================================
async function saveShoppingItem() {
  const desc  = document.getElementById('shopping-desc').value.trim();
  const link  = document.getElementById('shopping-link').value.trim();
  const price = document.getElementById('shopping-price').value.trim();
  if (!desc) return alert("Please enter an item description.");
  await addDoc(collection(db, "shopping"), { desc, link, price, bought: false, owner: currentUserEmail, createdAt: serverTimestamp() });
  toggleModal('add-shopping-modal', false);
  showToast("Item Added!");
  document.getElementById('shopping-desc').value  = '';
  document.getElementById('shopping-link').value  = '';
  document.getElementById('shopping-price').value = '';
}

function listenToShopping() {
  onSnapshot(query(collection(db, "shopping"), where("owner", "==", currentUserEmail)), (snapshot) => {
    const container = document.getElementById('shopping-list');
    if (!container) return;
    container.innerHTML = '';
    snapshot.forEach(docSnap => {
      const item = docSnap.data();
      const linkHTML = item.link
        ? `<a href="${item.link}" target="_blank" rel="noopener" class="shop-link"><i class="fa-solid fa-arrow-up-right-from-square"></i> View Product</a>`
        : '';
      const priceHTML = item.price ? `<span class="shop-price"><i class="fa-solid fa-tag"></i> ${item.price}</span>` : '';
      container.innerHTML += `
        <div class="shopping-card ${item.bought ? 'bought' : ''}" data-bought="${item.bought}">
          <button class="shop-check-btn" onclick="toggleBought('${docSnap.id}',${item.bought})" title="${item.bought ? 'Mark pending' : 'Mark bought'}">
            <i class="fa-solid ${item.bought ? 'fa-check-circle' : 'fa-circle'}"></i>
          </button>
          <div class="shop-info">
            <p class="shop-desc">${item.desc}</p>
            <div class="shop-meta">${priceHTML}${linkHTML}</div>
          </div>
          <button class="remove-btn" onclick="deleteDocItem('shopping','${docSnap.id}')"><i class="fa-solid fa-trash"></i></button>
        </div>`;
    });
  });
}

async function toggleBought(docId, currentBought) {
  await updateDoc(doc(db, "shopping", docId), { bought: !currentBought });
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
async function deleteDocItem(collectionName, docId) {
  if (event) event.stopPropagation();
  if (confirm("Delete this item forever?")) {
    await deleteDoc(doc(db, collectionName, docId));
    showToast("Deleted!");
  }
}

// ==========================================
// --- GLOBAL CORE RUNTIME ROUTING ---
// ==========================================
window.switchAuthTab       = switchAuthTab;
window.showForgotPassword  = showForgotPassword;
window.handleSignup        = handleSignup;
window.handleLogin         = handleLogin;
window.fetchSecurityQuestion = fetchSecurityQuestion;
window.recoverPassword     = recoverPassword;
window.handleLogout        = handleLogout;
window.switchTab           = switchTab;
window.filterItems         = filterItems;
window.copyText            = copyText;
window.toggleModal         = toggleModal;
window.unlockVault         = unlockVault;
window.verifyPin           = verifyPin;
window.handlePinReset      = handlePinReset;
window.saveNewVaultItem    = saveNewVaultItem;
window.openDocumentModal   = openDocumentModal;
window.closeDocumentModal  = closeDocumentModal;
window.previewCredLogo     = previewCredLogo;
window.saveNewCredential   = saveNewCredential;
window.toggleCredMenu      = toggleCredMenu;
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