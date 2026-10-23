// Browser-only persistence. Chat history never leaves the device.
//   us_chats  — array of chats {id, title, type, model, createdAt, updatedAt, messages:[...]}
//   us_draft  — {model, text, reasoning?} handed from the landing page to the app
//   us_prefs  — UI preferences (last model, option values, sidebar state)
//   us_owner  — id of the account the chat history belongs to

import { uid } from "./utils.js";

const CHATS_KEY = "us_chats";
const DRAFT_KEY = "us_draft";
const PREFS_KEY = "us_prefs";
const OWNER_KEY = "us_owner";

function read(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    if (raw == null) return fallback;
    const val = JSON.parse(raw);
    return val ?? fallback;
  } catch {
    return fallback;
  }
}

function write(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch (e) {
    return e;
  }
}

function remove(key) {
  try {
    localStorage.removeItem(key);
  } catch {
    /* storage unavailable */
  }
}

/* ---------------- owner ---------------- */

/**
 * Chat history belongs to one account. When another account signs in in this browser, the
 * previous history is wiped: otherwise the next person saw someone else's chats and could
 * send them to a model with their own key. Returns true if history was wiped.
 */
export function bindOwner(userId) {
  const id = String(userId ?? "");
  if (!id) return false;
  const owner = read(OWNER_KEY, null);
  const wiped = owner !== null && String(owner) !== id;
  if (wiped) {
    remove(CHATS_KEY);
    cache = null;
  }
  write(OWNER_KEY, id);
  return wiped;
}

/** Logout: the account's chats and draft leave the browser with it. */
export function clearAccountData() {
  remove(CHATS_KEY);
  remove(DRAFT_KEY);
  remove(OWNER_KEY);
  cache = null;
}

/* ---------------- draft ---------------- */

export function setDraft(draft) {
  return write(DRAFT_KEY, { ...draft, createdAt: Date.now() }) === true;
}
export function takeDraft() {
  const d = read(DRAFT_KEY, null);
  return d && typeof d === "object" && (d.text || d.model) ? d : null;
}
export function clearDraft() {
  remove(DRAFT_KEY);
}

/* ---------------- prefs ---------------- */

export function getPrefs() {
  const p = read(PREFS_KEY, {});
  return p && typeof p === "object" ? p : {};
}
export function setPrefs(patch) {
  write(PREFS_KEY, { ...getPrefs(), ...patch });
}

/* ---------------- chats ---------------- */

function sanitizeChats(list) {
  if (!Array.isArray(list)) return [];
  return list
    .filter((c) => c && typeof c === "object" && c.id && Array.isArray(c.messages))
    .map((c) => ({
      id: String(c.id),
      title: String(c.title || "Новый чат"),
      type: c.type === "image" || c.type === "video" ? c.type : "text",
      model: String(c.model || ""),
      createdAt: Number(c.createdAt) || Date.now(),
      updatedAt: Number(c.updatedAt) || Number(c.createdAt) || Date.now(),
      messages: c.messages.filter((m) => m && typeof m === "object" && (m.role === "user" || m.role === "assistant")),
    }));
}

let cache = null;

export function loadChats() {
  if (!cache) cache = sanitizeChats(read(CHATS_KEY, []));
  return cache;
}

/** Strip heavy inline data (attachments, base64 images) from all chats except `keepId`. */
function slim(chats, keepId) {
  let changed = false;
  for (const chat of chats) {
    if (chat.id === keepId) continue;
    for (const msg of chat.messages) {
      if (Array.isArray(msg.attachments)) {
        for (const att of msg.attachments) {
          if (att.dataUrl) {
            delete att.dataUrl;
            att.dropped = true;
            changed = true;
          }
          if (att.text && att.text.length > 2000) {
            att.text = att.text.slice(0, 2000);
            changed = true;
          }
        }
      }
      if (Array.isArray(msg.images)) {
        const before = msg.images.length;
        msg.images = msg.images.filter((u) => !String(u).startsWith("data:"));
        if (msg.images.length !== before) changed = true;
      }
    }
  }
  return changed;
}

/**
 * Persist chats. On quota errors, drops inline attachment data from other chats and retries.
 * Returns {ok, slimmed}.
 */
export function saveChats(chats = cache, activeId = null) {
  cache = chats;
  let res = write(CHATS_KEY, chats);
  if (res === true) return { ok: true, slimmed: false };
  if (slim(chats, activeId)) {
    res = write(CHATS_KEY, chats);
    if (res === true) return { ok: true, slimmed: true };
  }
  // Last resort: drop the oldest chats until it fits.
  const sorted = [...chats].sort((a, b) => a.updatedAt - b.updatedAt);
  while (sorted.length > 1) {
    const victim = sorted.shift();
    if (victim.id === activeId) continue;
    const idx = chats.findIndex((c) => c.id === victim.id);
    if (idx >= 0) chats.splice(idx, 1);
    if (write(CHATS_KEY, chats) === true) return { ok: true, slimmed: true };
  }
  return { ok: false, slimmed: true };
}

export function createChat({ type, model, title }) {
  const now = Date.now();
  const chat = { id: uid("c_"), title: title || "Новый чат", type, model, createdAt: now, updatedAt: now, messages: [] };
  loadChats().unshift(chat);
  return chat;
}

export function getChat(id) {
  return loadChats().find((c) => c.id === id) || null;
}

export function deleteChat(id) {
  const list = loadChats();
  const idx = list.findIndex((c) => c.id === id);
  if (idx >= 0) list.splice(idx, 1);
  return saveChats(list);
}

export function renameChat(id, title) {
  const chat = getChat(id);
  if (!chat) return;
  chat.title = title.trim().slice(0, 120) || chat.title;
  chat.updatedAt = Date.now();
  saveChats(loadChats(), id);
}

/** Make sure the in-memory cache reflects writes from another tab. */
export function onExternalChange(cb) {
  window.addEventListener("storage", (e) => {
    if (e.key === CHATS_KEY) {
      cache = null;
      cb();
    }
  });
}
