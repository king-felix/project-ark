/* PROJECT ARK — where a downloaded map pack lives on the device.
 *
 * One small IndexedDB database, four stores:
 *
 *   packs   one row per downloaded (or half-downloaded) pack version
 *   files   one row per component: size, chunk count, fingerprint
 *   chunks  the bytes, in 1 MiB pieces, so a download can stop and resume and a
 *           crash can never leave a file that looks finished
 *   local   saved places, route history, the last view and the unit choice —
 *           this device only, never sent anywhere
 *
 * A pack is *installed* only after every component has been written and its
 * fingerprint checked; the `state` field is what the app trusts, not the
 * presence of a folder.  Deleting a pack removes its chunks first and its row
 * last, so a deletion interrupted halfway leaves an obviously incomplete pack
 * rather than a row pointing at nothing.
 */

export const DB_NAME = "ark-maps";
export const DB_VERSION = 1;
export const CHUNK_BYTES = 1024 * 1024;

let dbPromise = null;

function request(req) {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error || new Error("the device refused the request"));
  });
}

function finished(tx) {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve(true);
    tx.onerror = () => reject(tx.error || new Error("the device refused the write"));
    tx.onabort = () => reject(tx.error || new Error("the device stopped the write"));
  });
}

export function openDB() {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve, reject) => {
    if (!("indexedDB" in globalThis)) {
      reject(new Error("This browser does not offer the storage this page needs."));
      return;
    }
    const open = indexedDB.open(DB_NAME, DB_VERSION);
    open.onupgradeneeded = () => {
      const db = open.result;
      if (!db.objectStoreNames.contains("packs")) db.createObjectStore("packs", { keyPath: "key" });
      if (!db.objectStoreNames.contains("files")) db.createObjectStore("files", { keyPath: "key" });
      if (!db.objectStoreNames.contains("chunks")) db.createObjectStore("chunks", { keyPath: "key" });
      if (!db.objectStoreNames.contains("local")) db.createObjectStore("local", { keyPath: "key" });
    };
    open.onsuccess = () => {
      const db = open.result;
      db.onversionchange = () => db.close();
      resolve(db);
    };
    open.onerror = () => reject(open.error || new Error("This browser would not open its storage."));
    open.onblocked = () => reject(new Error("Another tab is holding this page's storage open."));
  });
  return dbPromise;
}

export function packKey(id, version) {
  return `${id}@${version}`;
}

export function fileKey(key, file) {
  return `${key}|${file}`;
}

export function chunkKey(key, file, index) {
  return `${key}|${file}|${index}`;
}

async function put(store, value) {
  const db = await openDB();
  const tx = db.transaction(store, "readwrite");
  tx.objectStore(store).put(value);
  await finished(tx);
  return value;
}

async function get(store, key) {
  const db = await openDB();
  const tx = db.transaction(store, "readonly");
  return request(tx.objectStore(store).get(key));
}

async function all(store) {
  const db = await openDB();
  const tx = db.transaction(store, "readonly");
  return request(tx.objectStore(store).getAll());
}

async function del(store, key) {
  const db = await openDB();
  const tx = db.transaction(store, "readwrite");
  tx.objectStore(store).delete(key);
  await finished(tx);
}

export async function putPack(pack) {
  return put("packs", pack);
}

export async function getPack(key) {
  return get("packs", key);
}

export async function listPacks() {
  const rows = await all("packs");
  return rows.sort((a, b) => (a.createdAt || "").localeCompare(b.createdAt || ""));
}

export async function activePackKey() {
  // `localGet` already answers the stored value, not the row.  Unwrapping it a
  // second time returned `undefined`, which the app read as "no active pack"
  // and hid a perfectly good installed map behind the catalogue.
  return (await localGet("active")) || "";
}

export async function setActivePackKey(key) {
  await localPut("active", key);
}

export async function putFileRecord(record) {
  return put("files", record);
}

export async function getFileRecord(key, file) {
  return get("files", fileKey(key, file));
}

export async function putChunk(key, file, index, buffer) {
  return put("chunks", {
    key: chunkKey(key, file, index),
    packKey: key,
    file,
    index,
    bytes: buffer,
  });
}

export async function getChunk(key, file, index) {
  const row = await get("chunks", chunkKey(key, file, index));
  return row ? row.bytes : null;
}

export async function countChunks(key, file) {
  const db = await openDB();
  const tx = db.transaction("chunks", "readonly");
  const range = IDBKeyRange.bound(`${key}|${file}|`, `${key}|${file}|\uffff`);
  return request(tx.objectStore("chunks").count(range));
}

export async function readFile(key, file) {
  const record = await getFileRecord(key, file);
  if (!record) throw new Error(`the ${file} component is not on this device`);
  const parts = [];
  let have = 0;
  for (let index = 0; index < record.chunks; index++) {
    const bytes = await getChunk(key, file, index);
    if (!bytes) throw new Error(`the ${file} component is incomplete on this device`);
    parts.push(new Uint8Array(bytes));
    have += bytes.byteLength;
  }
  if (record.size && have !== record.size) {
    throw new Error(`the ${file} component is ${have} bytes and should be ${record.size}`);
  }
  const out = new Uint8Array(have);
  let at = 0;
  for (const part of parts) {
    out.set(part, at);
    at += part.byteLength;
  }
  return out;
}

export async function deletePack(key) {
  const db = await openDB();
  // Collect the keys first, in read transactions, and delete them in one write.
  // Walking two cursors inside one write transaction looks tidier and is not: an
  // IndexedDB transaction commits the moment it has no request pending, so a
  // promise between the two cursors can close it halfway through a deletion.
  const chunkKeys = await new Promise((resolve, reject) => {
    const tx = db.transaction("chunks", "readonly");
    const found = [];
    const range = IDBKeyRange.bound(`${key}|`, `${key}|\uffff`);
    const cursor = tx.objectStore("chunks").openKeyCursor(range);
    cursor.onsuccess = () => {
      const row = cursor.result;
      if (!row) { resolve(found); return; }
      found.push(row.key);
      row.continue();
    };
    cursor.onerror = () => reject(cursor.error || new Error("could not list the stored bytes"));
  });
  const fileKeys = await new Promise((resolve, reject) => {
    const tx = db.transaction("files", "readonly");
    const found = [];
    const range = IDBKeyRange.bound(`${key}|`, `${key}|\uffff`);
    const cursor = tx.objectStore("files").openKeyCursor(range);
    cursor.onsuccess = () => {
      const row = cursor.result;
      if (!row) { resolve(found); return; }
      found.push(row.key);
      row.continue();
    };
    cursor.onerror = () => reject(cursor.error || new Error("could not list the files"));
  });
  const tx = db.transaction(["chunks", "files", "packs"], "readwrite");
  for (const name of chunkKeys) tx.objectStore("chunks").delete(name);
  for (const name of fileKeys) tx.objectStore("files").delete(name);
  tx.objectStore("packs").delete(key);
  await finished(tx);
}


export async function deleteChunks(key, file) {
  const db = await openDB();
  const names = await new Promise((resolve, reject) => {
    const tx = db.transaction("chunks", "readonly");
    const found = [];
    const range = IDBKeyRange.bound(`${key}|${file}|`, `${key}|${file}|\uffff`);
    const cursor = tx.objectStore("chunks").openKeyCursor(range);
    cursor.onsuccess = () => {
      const row = cursor.result;
      if (!row) { resolve(found); return; }
      found.push(row.key);
      row.continue();
    };
    cursor.onerror = () => reject(cursor.error || new Error("could not list the stored bytes"));
  });
  const tx = db.transaction("chunks", "readwrite");
  for (const name of names) tx.objectStore("chunks").delete(name);
  await finished(tx);
}

export async function deleteFileRecord(key, file) {
  return del("files", fileKey(key, file));
}

export async function localKeys(prefix = "") {
  const rows = await all("local");
  return rows.filter((row) => String(row.key).startsWith(prefix)).map((row) => row.key);
}

export async function localGet(key, fallback = null) {
  const row = await get("local", key);
  return row ? row.value : fallback;
}

export async function localPut(key, value) {
  return put("local", { key, value });
}

export async function localDelete(key) {
  return del("local", key);
}

export async function storageEstimate() {
  if (!navigator.storage || !navigator.storage.estimate) return null;
  try {
    const estimate = await navigator.storage.estimate();
    return {
      usage: Number(estimate.usage) || 0,
      quota: Number(estimate.quota) || 0,
    };
  } catch (_) {
    return null;
  }
}

export async function requestPersistence() {
  if (!navigator.storage || !navigator.storage.persist) return null;
  try {
    return await navigator.storage.persist();
  } catch (_) {
    return null;
  }
}

export async function persisted() {
  if (!navigator.storage || !navigator.storage.persisted) return null;
  try {
    return await navigator.storage.persisted();
  } catch (_) {
    return null;
  }
}

export function humanBytes(n) {
  const value = Number(n) || 0;
  if (value < 1000) return `${Math.round(value)} B`;
  const units = ["kB", "MB", "GB", "TB"];
  let v = value / 1000;
  let i = 0;
  while (v >= 1000 && i < units.length - 1) { v /= 1000; i++; }
  return `${v.toFixed(v >= 100 ? 0 : 1)} ${units[i]}`;
}
