// Envelope encryption for the vault: a random AES-256-GCM data key (DEK) encrypts records;
// the DEK is stored only wrapped by a passphrase-derived key (PBKDF2) or a WebAuthn PRF key.
// Firestore never sees plaintext or the unwrapped DEK.
const enc = new TextEncoder();
const dec = new TextDecoder();
export const PBKDF2_ITER = 600000; // OWASP 2023 recommendation for PBKDF2-SHA256

export const rand = n => crypto.getRandomValues(new Uint8Array(n));

export function toB64(buf) {
  const u = new Uint8Array(buf);
  let s = '';
  for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode.apply(null, u.subarray(i, i + 0x8000));
  return btoa(s);
}
export const fromB64 = s => Uint8Array.from(atob(s), c => c.charCodeAt(0));
export const newSalt = () => toB64(rand(16));

export async function deriveKek(passphrase, saltB64, iterations = PBKDF2_ITER) {
  const base = await crypto.subtle.importKey('raw', enc.encode(passphrase), 'PBKDF2', false, ['deriveKey']);
  return crypto.subtle.deriveKey(
    { name: 'PBKDF2', salt: fromB64(saltB64), iterations, hash: 'SHA-256' },
    base, { name: 'AES-GCM', length: 256 }, false, ['wrapKey', 'unwrapKey']);
}

// 32-byte WebAuthn PRF output -> wrapping key
export const kekFromBytes = bytes =>
  crypto.subtle.importKey('raw', bytes, 'AES-GCM', false, ['wrapKey', 'unwrapKey']);

export const newDek = () =>
  crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, true, ['encrypt', 'decrypt']);

export async function wrapDek(dek, kek) {
  const iv = rand(12);
  return { iv: toB64(iv), ct: toB64(await crypto.subtle.wrapKey('raw', dek, kek, { name: 'AES-GCM', iv })) };
}

// Throws on wrong passphrase / key (GCM auth tag mismatch).
export const unwrapDek = (w, kek) =>
  crypto.subtle.unwrapKey('raw', fromB64(w.ct), kek, { name: 'AES-GCM', iv: fromB64(w.iv) },
    { name: 'AES-GCM', length: 256 }, true, ['encrypt', 'decrypt']);

export async function encryptJSON(obj, dek) {
  const iv = rand(12);
  const ct = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, dek, enc.encode(JSON.stringify(obj)));
  return { iv: toB64(iv), ct: toB64(ct) };
}

export async function decryptJSON(box, dek) {
  const pt = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: fromB64(box.iv) }, dek, fromB64(box.ct));
  return JSON.parse(dec.decode(pt));
}
