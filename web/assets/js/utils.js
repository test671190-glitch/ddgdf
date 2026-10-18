// Small shared helpers: safe DOM building, icons, toasts, clipboard, formatting.

/** Escape text for insertion into HTML strings. */
export function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/**
 * Element builder. Children that are strings become text nodes (never parsed as HTML).
 * Use attrs.html only with trusted static markup (icons).
 */
export function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, val] of Object.entries(attrs || {})) {
    if (val === undefined || val === null || val === false) continue;
    if (key === "class") node.className = val;
    else if (key === "html") node.innerHTML = val;
    else if (key === "text") node.textContent = val;
    else if (key === "dataset") Object.assign(node.dataset, val);
    else if (key === "style" && typeof val === "object") Object.assign(node.style, val);
    else if (key.startsWith("on") && typeof val === "function") node.addEventListener(key.slice(2).toLowerCase(), val);
    else if (val === true) node.setAttribute(key, "");
    else node.setAttribute(key, String(val));
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

/** Only allow http(s), blob and inline image/video data URLs into src/href attributes. */
export function safeUrl(url, { allowData = true } = {}) {
  if (typeof url !== "string") return "";
  const u = url.trim();
  if (/^https?:\/\//i.test(u) || u.startsWith("/") || u.startsWith("blob:")) return u;
  if (allowData && /^data:(image|video)\/[a-z0-9.+-]+;base64,/i.test(u)) return u;
  return "";
}

export const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
export const isCoarsePointer = () => window.matchMedia("(hover: none), (pointer: coarse)").matches;

export function uid(prefix = "") {
  const src = window.crypto && crypto.getRandomValues ? crypto.getRandomValues(new Uint32Array(2)) : [Math.random() * 1e9, Math.random() * 1e9];
  const rnd = Array.from(src, (x) => Math.floor(x).toString(36)).join("");
  return prefix + Date.now().toString(36) + rnd.slice(0, 8);
}

export function debounce(fn, ms = 200) {
  let t = 0;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

/** Russian plural: plural(5, ["день", "дня", "дней"]) → "дней" */
export function plural(n, forms) {
  const a = Math.abs(n) % 100;
  const b = a % 10;
  if (a > 10 && a < 20) return forms[2];
  if (b > 1 && b < 5) return forms[1];
  if (b === 1) return forms[0];
  return forms[2];
}

export function formatDate(value, withTime = false) {
  if (!value) return "—";
  const d = new Date(typeof value === "number" && value < 1e12 ? value * 1000 : value);
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleDateString("ru-RU", {
    day: "numeric",
    month: "long",
    year: "numeric",
    ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}),
  });
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = h("textarea", { style: { position: "fixed", opacity: "0", top: "0", left: "0" } });
    ta.value = text;
    document.body.append(ta);
    ta.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch {
      ok = false;
    }
    ta.remove();
    return ok;
  }
}

/** Copy and flash a "done" state on the button. */
export async function copyWithFeedback(btn, text, { label, doneLabel = "Скопировано" } = {}) {
  const ok = await copyText(text);
  if (!btn) return ok;
  const labelEl = btn.querySelector("[data-label]");
  btn.classList.add(ok ? "is-done" : "is-fail");
  if (labelEl) labelEl.textContent = ok ? doneLabel : "Не удалось";
  clearTimeout(btn._copyT);
  btn._copyT = setTimeout(() => {
    btn.classList.remove("is-done", "is-fail");
    if (labelEl) labelEl.textContent = label ?? labelEl.dataset.label ?? "";
  }, 1600);
  if (!labelEl) toast(ok ? "Скопировано" : "Не удалось скопировать", { type: ok ? "ok" : "err", duration: 1600 });
  return ok;
}

/* ---------------- toasts ---------------- */

let toastRoot = null;
export function toast(message, { type = "info", duration = 3200 } = {}) {
  if (!toastRoot) {
    toastRoot = h("div", { class: "toasts", role: "status", "aria-live": "polite" });
    document.body.append(toastRoot);
  }
  const icon = type === "ok" ? icons.check : type === "err" ? icons.alert : icons.info;
  const node = h("div", { class: "toast " + type }, h("span", { html: icon, "aria-hidden": "true" }), h("span", {}, message));
  toastRoot.append(node);
  setTimeout(() => {
    node.classList.add("is-leaving");
    setTimeout(() => node.remove(), 260);
  }, duration);
}

/* ---------------- icons (static, trusted markup) ---------------- */

const svg = (d, extra = "") =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" ${extra}>${d}</svg>`;

export const icons = {
  plus: svg('<path d="M12 5v14M5 12h14"/>'),
  send: svg('<path d="M12 19V5M5.5 11.5 12 5l6.5 6.5"/>', 'stroke-width="2.2"'),
  stop: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor"/></svg>',
  caret: svg('<path d="m6 9 6 6 6-6"/>', 'class="caret"'),
  check: svg('<path d="M20 6 9 17l-5-5"/>'),
  checkSmall: svg('<path d="M20 6 9 17l-5-5"/>', 'class="check"'),
  copy: svg('<rect x="9" y="9" width="12" height="12" rx="2.5"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>'),
  x: svg('<path d="M18 6 6 18M6 6l12 12"/>'),
  alert: svg('<path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/>'),
  info: svg('<circle cx="12" cy="12" r="9"/><path d="M12 16v-4M12 8h.01"/>'),
  telegram: '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M21.9 4.3 18.7 19.4c-.2 1-.9 1.3-1.8.8l-4.9-3.6-2.4 2.3c-.3.3-.5.5-1 .5l.3-5 9.1-8.2c.4-.4-.1-.6-.6-.2L6.2 13.1l-4.8-1.5c-1-.3-1.1-1 .2-1.5L20.5 2.9c.9-.3 1.6.2 1.4 1.4Z"/></svg>',
  sparkle: svg('<path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M5.6 18.4l2.8-2.8M15.6 8.4l2.8-2.8"/>'),
  brain: svg('<path d="M9 4a3 3 0 0 0-3 3v.2A3 3 0 0 0 4 10a3 3 0 0 0 1 2.2A3 3 0 0 0 6 17a3 3 0 0 0 3 3h0a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2Z"/><path d="M15 4a3 3 0 0 1 3 3v.2A3 3 0 0 1 20 10a3 3 0 0 1-1 2.2A3 3 0 0 1 18 17a3 3 0 0 1-3 3h0a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z"/>'),
  globe: svg('<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.7 3.8 5.7 3.8 9s-1.3 6.3-3.8 9c-2.5-2.7-3.8-5.7-3.8-9S9.5 5.7 12 3Z"/>'),
  image: svg('<rect x="3" y="4" width="18" height="16" rx="3"/><circle cx="9" cy="10" r="1.8"/><path d="m21 16-5-5-9 9"/>'),
  video: svg('<rect x="2.5" y="5" width="14" height="14" rx="3"/><path d="m16.5 10 5-3v10l-5-3"/>'),
  text: svg('<path d="M4 6h16M4 12h10M4 18h13"/>'),
  ratio: svg('<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M7 10v4M17 10v4"/>'),
  layers: svg('<path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 13 9 5 9-5"/>'),
  clock: svg('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'),
  sound: svg('<path d="M11 5 6 9H3v6h3l5 4V5Z"/><path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13"/>'),
  grid: svg('<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>'),
  menu: svg('<path d="M4 7h16M4 12h16M4 17h10"/>'),
  sidebar: svg('<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M9 4v16"/>'),
  edit: svg('<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>'),
  trash: svg('<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/>'),
  dots: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1.8" fill="currentColor"/><circle cx="12" cy="12" r="1.8" fill="currentColor"/><circle cx="19" cy="12" r="1.8" fill="currentColor"/></svg>',
  search: svg('<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>'),
  key: svg('<circle cx="8" cy="15" r="4"/><path d="m10.8 12.2 9.2-9.2M17 6l3 3M14 9l2 2"/>'),
  book: svg('<path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2Z"/><path d="M4 19a2 2 0 0 1 2-2h13v4H6a2 2 0 0 1-2-2Z"/>'),
  logout: svg('<path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3"/><path d="M10 17l-5-5 5-5M5 12h11"/>'),
  home: svg('<path d="m3 11 9-7 9 7"/><path d="M5 10v10h14V10"/>'),
  refresh: svg('<path d="M20 11a8 8 0 0 0-14.9-3.9L3 9.5"/><path d="M3 4v5.5h5.5"/><path d="M4 13a8 8 0 0 0 14.9 3.9L21 14.5"/><path d="M21 20v-5.5h-5.5"/>'),
  eye: svg('<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>'),
  eyeOff: svg('<path d="M3 3l18 18"/><path d="M10.6 5.1A10.4 10.4 0 0 1 12 5c6.4 0 10 7 10 7a17.6 17.6 0 0 1-3.2 4.2M6.6 6.6A17.4 17.4 0 0 0 2 12s3.6 7 10 7a9.8 9.8 0 0 0 5.4-1.6"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/>'),
  download: svg('<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>'),
  external: svg('<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>'),
  arrowRight: svg('<path d="M5 12h14M13 6l6 6-6 6"/>'),
  chat: svg('<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12Z"/>'),
  paperclip: svg('<path d="m21 11-8.5 8.5a5 5 0 0 1-7-7L14 4a3.5 3.5 0 0 1 5 5l-8.5 8.5a2 2 0 0 1-3-3L15 7"/>'),
  file: svg('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z"/><path d="M14 3v5h5"/>'),
  crypto: svg('<circle cx="12" cy="12" r="9"/><path d="M9.5 8h4a2 2 0 0 1 0 4h-4m0 0h4.5a2 2 0 0 1 0 4h-4.5m0-8v8m1.5-10v2m0 8v2"/>'),
  star: '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="m12 2.8 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 16.8l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9Z"/></svg>',
};

export function iconEl(name, cls = "") {
  return h("span", { class: "ico " + cls, html: icons[name] || "", "aria-hidden": "true" });
}

export const TYPE_LABELS = { text: "Текст", image: "Изображения", video: "Видео" };
export const TYPE_ICONS = { text: "text", image: "image", video: "video" };

export const REASONING_LABELS = {
  none: "Без рассуждений",
  minimal: "Минимальный",
  low: "Низкий",
  medium: "Средний",
  high: "Высокий",
  xhigh: "Очень высокий",
  max: "Максимальный",
};
