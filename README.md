# 🚀 Personalizer

Personalizer is a personal, secure, installable all-in-one Progressive Web App (PWA) dashboard designed to manage academic tasks, track shopping lists, securely view sensitive documents, and log media watchlists. 

Engineered with a **network-first service worker architecture** and **Firestore IndexedDB persistence**, this application is built to load instantly and remain fully operational even when completely offline.

---

## 🛠️ Tech Stack

*   **Frontend:** HTML5, Vanilla CSS3 (Custom Properties, Flexbox, Grid), Modern JavaScript (ES6+ Modules).
*   **Backend & Database:** Firebase v10 SDK (Firestore Ecosystem)[cite: 9, 12].
*   **Third-Party APIs:** OMDB API (Media Metadata Engine)[cite: 12], Clearbit & Google Favicon APIs (Dynamic Logo Resolvers)[cite: 12].
*   **PWA Shell:** Web App Manifest standard, Service Workers API, client-side Canvas Compression engine[cite: 11, 12, 14].

---

## ✨ Core Features & Technical Engineering

### 1. 📶 Hybrid Offline-First Architecture
*   **UI Caching:** A custom service worker (`sw.js`) intercepts network requests to cache and immediately serve the global app shell (HTML, CSS, JS, FontAwesome webfonts) locally[cite: 14].
*   **Firestore Persistent Cache:** Utilizes `enableIndexedDbPersistence` to mirror your database state locally[cite: 9]. If network drops, reads and writes map transparently to IndexedDB, syncing seamlessly back to the cloud once a connection is re-established[cite: 9].

### 2. 🗄️ The Secure Vault & Biometric Mock Layer
*   **PIN-Locked Partition:** The vault layer is blocked by a secondary 4-digit session-locked PIN, isolated locally in client memory to protect data from active browser snooping[cite: 11, 12].
*   **CORS Bypass Image Streaming:** To completely avoid Cross-Origin Resource Sharing (CORS) complications and manage database resource usage, user-uploaded document scans are compressed client-side via the HTML5 Canvas API and written directly into Firestore as optimized Base64 strings[cite: 12].

### 3. 🎓 Academic Planner & Truncation Hacks
*   **Categorized Tracking:** Dynamically streams and filters Quizzes, Assignments, and Lab Tests based on structural categorization[cite: 11, 12].
*   **Unbreakable Layouts:** Dynamic event cards use strict inline-flexbox rules (`flex-shrink: 0`, `min-width: 0`) paired with elegant CSS text-overflow rules (`ellipsis`), ensuring massive string headers never deform action buttons or rupture mobile views[cite: 11].

### 4. 🔑 Intelligent Credential Manager
*   **Automated Brand Ingestion:** Automatically extracts domain hostnames from target profile fields to stream high-resolution logos via Clearbit's API, with an integrated fallback chain targeting Google's structural favicon service[cite: 12].
*   **One-Click Copy:** Leverages the asynchronous Clipboard API to let users rapidly copy masked strings securely without explicit exposures[cite: 12].

### 5. 🎬 Media Tracker & State-Safe Routing
*   **Tab-State Resolution:** Features real-time state tracking that maps OMDB movie queries automatically to whichever list container (`Watched` vs. `Plan to Watch`) is actively toggled on the user interface[cite: 11, 12].
*   **Asynchronous Serialization:** Sanitizes strings dynamically using precise escape mapping so that movie synopsis vectors containing single/double quotes or slashes never crash the modal rendering pipelines[cite: 12].

### 6. 🛒 Integrated Shopping List
*   **Transactional Strikethrough UI:** Tracks product metadata, notes, target values, and links[cite: 11, 12].
*   **Bit-Toggled Filtering:** Efficiently structures view tabs by reading element data attributes to filter pending vs bought items dynamically without triggering redundant database reads[cite: 11, 12].

---

## 📁 Repository Structure

```text
├── .firebase/             # Firebase build target distributions
├── .firebaserc            # Active environment binding targets
├── firebase.json          # Deployment routing declarations
├── firebase.js            # Database engine initialization and persistence hooks
├── index.html             # Structural dashboard core layouts and modals
├── manifest.json          # Native PWA installer operational manifest
├── script.js              # Central operational state and transaction script
├── style.css              # Cascading style specifications (Light/Dark variants)
└── sw.js                  # Cache-first app shell lifecycle manager

Here is a banger, professional, and production-ready `README.md` that is perfectly safe for a public GitHub repository. It strips away your live database keys and outlines your entire project architecture, the technical workarounds you engineered, and the new offline features you just deployed.

---

```markdown
# 🚀 Personalizer

Personalizer is a personal, secure, installable all-in-one Progressive Web App (PWA) dashboard designed to manage academic tasks, track shopping lists, securely view sensitive documents, and log media watchlists. 

Engineered with a **network-first service worker architecture** and **Firestore IndexedDB persistence**, this application is built to load instantly and remain fully operational even when completely offline.

---

## 🛠️ Tech Stack

*   **Frontend:** HTML5, Vanilla CSS3 (Custom Properties, Flexbox, Grid), Modern JavaScript (ES6+ Modules).
*   **Backend & Database:** Firebase v10 SDK (Firestore Ecosystem)[cite: 9, 12].
*   **Third-Party APIs:** OMDB API (Media Metadata Engine)[cite: 12], Clearbit & Google Favicon APIs (Dynamic Logo Resolvers)[cite: 12].
*   **PWA Shell:** Web App Manifest standard, Service Workers API, client-side Canvas Compression engine[cite: 11, 12, 14].

---

## ✨ Core Features & Technical Engineering

### 1. 📶 Hybrid Offline-First Architecture
*   **UI Caching:** A custom service worker (`sw.js`) intercepts network requests to cache and immediately serve the global app shell (HTML, CSS, JS, FontAwesome webfonts) locally[cite: 14].
*   **Firestore Persistent Cache:** Utilizes `enableIndexedDbPersistence` to mirror your database state locally[cite: 9]. If network drops, reads and writes map transparently to IndexedDB, syncing seamlessly back to the cloud once a connection is re-established[cite: 9].

### 2. 🗄️ The Secure Vault & Biometric Mock Layer
*   **PIN-Locked Partition:** The vault layer is blocked by a secondary 4-digit session-locked PIN, isolated locally in client memory to protect data from active browser snooping[cite: 11, 12].
*   **CORS Bypass Image Streaming:** To completely avoid Cross-Origin Resource Sharing (CORS) complications and manage database resource usage, user-uploaded document scans are compressed client-side via the HTML5 Canvas API and written directly into Firestore as optimized Base64 strings[cite: 12].

### 3. 🎓 Academic Planner & Truncation Hacks
*   **Categorized Tracking:** Dynamically streams and filters Quizzes, Assignments, and Lab Tests based on structural categorization[cite: 11, 12].
*   **Unbreakable Layouts:** Dynamic event cards use strict inline-flexbox rules (`flex-shrink: 0`, `min-width: 0`) paired with elegant CSS text-overflow rules (`ellipsis`), ensuring massive string headers never deform action buttons or rupture mobile views[cite: 11].

### 4. 🔑 Intelligent Credential Manager
*   **Automated Brand Ingestion:** Automatically extracts domain hostnames from target profile fields to stream high-resolution logos via Clearbit's API, with an integrated fallback chain targeting Google's structural favicon service[cite: 12].
*   **One-Click Copy:** Leverages the asynchronous Clipboard API to let users rapidly copy masked strings securely without explicit exposures[cite: 12].

### 5. 🎬 Media Tracker & State-Safe Routing
*   **Tab-State Resolution:** Features real-time state tracking that maps OMDB movie queries automatically to whichever list container (`Watched` vs. `Plan to Watch`) is actively toggled on the user interface[cite: 11, 12].
*   **Asynchronous Serialization:** Sanitizes strings dynamically using precise escape mapping so that movie synopsis vectors containing single/double quotes or slashes never crash the modal rendering pipelines[cite: 12].

### 6. 🛒 Integrated Shopping List
*   **Transactional Strikethrough UI:** Tracks product metadata, notes, target values, and links[cite: 11, 12].
*   **Bit-Toggled Filtering:** Efficiently structures view tabs by reading element data attributes to filter pending vs bought items dynamically without triggering redundant database reads[cite: 11, 12].

---

## 📁 Repository Structure

```text
├── .firebase/             # Firebase build target distributions
├── .firebaserc            # Active environment binding targets
├── firebase.json          # Deployment routing declarations
├── firebase.js            # Database engine initialization and persistence hooks
├── index.html             # Structural dashboard core layouts and modals
├── manifest.json          # Native PWA installer operational manifest
├── script.js              # Central operational state and transaction script
├── style.css              # Cascading style specifications (Light/Dark variants)
└── sw.js                  # Cache-first app shell lifecycle manager

```

---

## ⚙️ Local Configuration & Setup

To replicate this project environment or deploy your own instances, configure your workspace credentials as follows:

### 1. Database Shell

Initialize a **Firestore Database** in your Firebase Console and specify appropriate access rules for your collection paradigms (`users`, `vault`, `academic`, `credentials`, `movies`, `shopping`).

### 2. Environment Variables

Replace the skeleton tokens inside `firebase.js` with your active web configuration profile:

```javascript
const firebaseConfig = {
  apiKey: "YOUR_FIREBASE_API_KEY",
  authDomain: "YOUR_PROJECT_ID.firebaseapp.com",
  projectId: "YOUR_PROJECT_ID",
  storageBucket: "YOUR_PROJECT_ID.firebasestorage.app",
  messagingSenderId: "YOUR_MESSAGING_SENDER_ID",
  appId: "YOUR_APP_ID"
};

```

Provide an operational tracking token in `script.js` for movie lookups:

```javascript
const OMDB_API_KEY = "YOUR_OMDB_API_KEY";

```

### 3. CLI Deployment

Ensure the global Firebase tools package is available in your workspace:

```bash
npm install -g firebase-tools
firebase login
firebase deploy --only hosting

```

```

```
