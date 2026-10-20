// Self-check: node vault-crypto.test.mjs
import assert from 'node:assert/strict';
import { deriveKek, kekFromBytes, newDek, wrapDek, unwrapDek, encryptJSON, decryptJSON, newSalt, rand } from './vault-crypto.js';

const salt = newSalt();
const dek = await newDek();
const wrapped = await wrapDek(dek, await deriveKek('correct horse', salt));

// right passphrase round-trips a large record (image-sized payload exercises chunked base64)
const record = { title: 'Passport', number: 'A1234567', img: 'x'.repeat(300000) };
const box = await encryptJSON(record, dek);
const dek2 = await unwrapDek(wrapped, await deriveKek('correct horse', salt));
assert.deepEqual(await decryptJSON(box, dek2), record);

// wrong passphrase is rejected
await assert.rejects(unwrapDek(wrapped, await deriveKek('wrong horse', salt)));

// PRF-style 32-byte key can wrap the same DEK
const prf = rand(32);
const viaPrf = await unwrapDek(await wrapDek(dek, await kekFromBytes(prf)), await kekFromBytes(prf));
assert.deepEqual(await decryptJSON(box, viaPrf), record);

console.log('vault-crypto: all checks passed');
