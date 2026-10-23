// Chat app entry: auth gate, wiring of sidebar / composer / thread / account, draft hand-off from the landing.

import * as api from "./api.js";
import * as store from "./store.js";
import { $, toast } from "./utils.js";
import { mountLoginFlow, openTariffsModal } from "./modals.js";
import { initSidebar } from "./sidebar.js";
import { initComposer } from "./composer.js";
import { initChat } from "./chat.js";
import { initAccount } from "./account.js";

const ctx = {
  catalog: null,
  me: null,
  models: new Map(),
  started: false,
  account: null,
  composer: null,
  sidebar: null,
  chat: null,
};

const bootEl = $("#boot");
const authScreen = $("#auth-screen");
const appEl = $("#app");
let flow = null;

/* ---------------- context API used by modules ---------------- */

/** Chat history in this browser belongs to whoever signed in; another account wipes it. */
function adoptUser(me) {
  if (me && me.user && store.bindOwner(me.user.id) && ctx.started) window.location.reload();
}

ctx.requireLogin = () => showAuth({ overlay: ctx.started });
ctx.onUnauthorized = () => {
  ctx.me = null;
  if (ctx.account) ctx.account.renderBadge();
  showAuth({ overlay: ctx.started, expired: true });
};
ctx.refreshMe = async () => {
  try {
    const me = await api.getMe();
    if (me) {
      adoptUser(me);
      ctx.me = me;
      if (ctx.account) ctx.account.renderBadge();
    } else ctx.onUnauthorized();
  } catch {
    /* keep the previous state on network errors */
  }
  return ctx.me;
};
ctx.openBuy = (reason) =>
  openTariffsModal({
    catalog: ctx.catalog,
    me: ctx.me,
    reason,
    onPaid: ctx.refreshMe,
    onUnauthorized: ctx.requireLogin,
  });
ctx.onRenamed = (id) => ctx.chat && ctx.chat.onRenamed(id);

/* ---------------- boot ---------------- */

boot();

async function boot() {
  const catalogP = api.getCatalog();
  let me = null;
  let error = null;
  try {
    me = await api.getMe();
  } catch (e) {
    error = e;
  }
  ctx.catalog = await catalogP;
  ctx.models = new Map(ctx.catalog.models.map((m) => [m.id, m]));
  hideBoot();
  if (error) {
    showAuth({ error });
    return;
  }
  ctx.me = me;
  if (!me) {
    showAuth();
    return;
  }
  adoptUser(me);
  startApp();
}

function hideBoot() {
  if (!bootEl) return;
  bootEl.classList.add("is-done");
  setTimeout(() => bootEl.remove(), 450);
}

/* ---------------- auth screen ---------------- */

function showAuth({ error = null, overlay = false, expired = false } = {}) {
  authScreen.hidden = false;
  authScreen.classList.toggle("is-overlay", overlay);
  const errBox = $("#auth-error");
  const sub = $("#auth-sub");
  if (error) {
    if (flow) {
      flow.destroy();
      flow = null;
    }
    errBox.hidden = false;
    $("#auth-error-text").textContent =
      error.code === "network"
        ? "Сервер недоступен. Проверьте соединение и попробуйте снова."
        : "Не удалось проверить вход: " + (error.message || error.code) + ".";
    sub.textContent = "Чат откроется, как только сервер ответит.";
    return;
  }
  errBox.hidden = true;
  sub.textContent = expired
    ? "Сессия истекла. Войдите снова: чат и история останутся на месте."
    : overlay
      ? "Подтвердите вход в Telegram, чтобы продолжить."
      : "Войдите, чтобы открыть чат со всеми моделями. Ваш черновик запроса сохранится.";
  if (!flow) flow = mountLoginFlow($("#auth-flow"), { onConfirmed: onLoggedIn });
}

$("#auth-retry").addEventListener("click", async () => {
  const btn = $("#auth-retry");
  btn.disabled = true;
  try {
    const me = await api.getMe();
    adoptUser(me);
    ctx.me = me;
    if (me) {
      authScreen.hidden = true;
      if (!ctx.started) startApp();
      else if (ctx.account) ctx.account.renderBadge();
    } else showAuth({ overlay: ctx.started });
  } catch (e) {
    showAuth({ error: e });
  } finally {
    btn.disabled = false;
  }
});

async function onLoggedIn() {
  let me = null;
  try {
    me = await api.getMe();
  } catch {
    me = null;
  }
  if (!me) {
    toast("Не удалось подтвердить вход. Попробуйте ещё раз", { type: "err" });
    if (flow) flow.restart();
    return;
  }
  adoptUser(me);
  ctx.me = me;
  if (flow) {
    flow.destroy();
    flow = null;
  }
  authScreen.hidden = true;
  if (!ctx.started) {
    startApp();
    toast("Вы вошли" + (me.user && me.user.first_name ? ", " + me.user.first_name : ""), { type: "ok" });
  } else {
    if (ctx.account) ctx.account.renderBadge();
    ctx.chat.resumeVideos();
    ctx.chat.refreshEmpty();
    toast("С возвращением!", { type: "ok" });
  }
}

/* ---------------- app ---------------- */

function startApp() {
  ctx.started = true;
  appEl.hidden = false;

  ctx.account = initAccount(ctx);
  ctx.composer = initComposer(ctx, {
    onSubmit: (payload) => ctx.chat.send(payload),
    onStop: () => ctx.chat.stop(),
  });
  ctx.sidebar = initSidebar(ctx, {
    onSelect: (id) => ctx.chat.openChat(id),
    onNew: () => ctx.chat.newChat(),
    onDelete: (id) => ctx.chat.deleteChat(id),
  });
  ctx.chat = initChat(ctx, { composer: ctx.composer, sidebar: ctx.sidebar });

  ctx.sidebar.render(null);
  ctx.chat.newChat();

  // Draft from the landing page: preselect the model and put the text into the composer.
  const draft = store.takeDraft();
  if (draft) {
    if (draft.model) ctx.composer.setModel(draft.model, { reasoning: draft.reasoning });
    if (draft.text) ctx.composer.setText(String(draft.text));
    store.clearDraft();
  }

  ctx.chat.resumeVideos();
  ctx.composer.updateHeight();

  // Shortcut: Ctrl/Cmd+Shift+O — new chat
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === "O" || e.key === "o")) {
      e.preventDefault();
      ctx.chat.newChat();
    }
  });
}
