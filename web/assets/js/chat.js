// Chat thread: rendering, sending (text stream / images / video jobs), errors, regenerate, video polling.

import * as api from "./api.js";
import * as store from "./store.js";
import { h, $, icons, safeUrl, copyWithFeedback, toast, uid, TYPE_LABELS, REASONING_LABELS } from "./utils.js";
import { renderMarkdown, enhanceCode } from "./markdown.js";
import { initOrb } from "./orb.js";

const FINAL = new Set(["completed", "failed"]);
const isFinal = (s) => FINAL.has(s);

const ERRORS = {
  unauthorized: { title: "Сессия истекла", text: "Войдите снова через Telegram, история чата сохранится.", action: "login" },
  subscription_required: { title: "Нужна активная подписка", text: "Оформите тариф, чтобы отправлять запросы к моделям.", action: "buy", sub: true },
  no_api_key: { title: "Нужна активная подписка", text: "Ключ доступа выдаётся после оплаты тарифа. Оформите подписку, и чат заработает сразу.", action: "buy", sub: true },
  invalid_api_key: { title: "Ключ доступа недействителен", text: "Проверьте подписку в разделе «Подписка и ключ» или продлите тариф.", action: "buy", sub: true },
  plan_limit_reached: { title: "Дневной лимит тарифа исчерпан", text: "Доступ вернётся после сброса лимита. На тарифе Unlimited ограничений нет.", action: "upgrade", sub: true },
  model_not_found: { title: "Модель недоступна", text: "Такой модели сейчас нет. Выберите другую в списке моделей.", action: "retry" },
  upstream_error: { title: "Модель не ответила", text: "Провайдер временно недоступен. Повторите запрос или выберите другую модель.", action: "retry" },
  network: { title: "Нет соединения", text: "Не удалось связаться с сервером. Проверьте интернет и повторите запрос.", action: "retry" },
  invalid_request: { title: "Неверные параметры запроса", text: "Модель не приняла эти параметры. Попробуйте другие настройки.", action: "retry" },
  video_failed: { title: "Видео не получилось", text: "Генерация завершилась ошибкой. Попробуйте изменить описание или модель.", action: "retry" },
  empty: { title: "Пустой ответ", text: "Модель ничего не вернула. Попробуйте ещё раз.", action: "retry" },
  bad_response: { title: "Неожиданный ответ сервера", text: "Попробуйте повторить запрос чуть позже.", action: "retry" },
};
const DEFAULT_ERROR = { title: "Что-то пошло не так", text: "Запрос не выполнен. Попробуйте ещё раз.", action: "retry" };

const SUGGESTIONS = [
  { icon: "💻", title: "Помоги с кодом", text: "Найди ошибку в этом коде и объясни, как её исправить:\n\n", model: "claude-opus-5-5" },
  { icon: "✍️", title: "Напиши текст", text: "Напиши пост для Telegram-канала о запуске нового продукта: коротко, живо и с призывом к действию.", model: "gpt-6-sol" },
  { icon: "🎨", title: "Нарисуй картинку", text: "Минималистичная иконка приложения: оранжевая сфера из светящихся частиц на чёрном фоне", model: "nano-banana-pro" },
  { icon: "🎬", title: "Сделай видео", text: "Медленный пролёт камеры над ночным мегаполисом, неоновые огни и лёгкий туман", model: "kling-3.0" },
];

const VIDEO_STATUS = {
  starting: "Отправляем задачу…",
  queued: "В очереди",
  pending: "В очереди",
  processing: "Генерируется",
  in_progress: "Генерируется",
  running: "Генерируется",
};

function aspectCss(ar) {
  const m = /^(\d+):(\d+)$/.exec(ar || "");
  return m ? m[1] + " / " + m[2] : "1 / 1";
}

function makeTitle(text, attachments) {
  const line = (text || "").split("\n").find((l) => l.trim()) || "";
  if (line) return line.trim().slice(0, 60) + (line.trim().length > 60 ? "…" : "");
  if (attachments && attachments.length) return attachments[0].kind === "image" ? "Изображение" : attachments[0].name || "Файл";
  return "Новый чат";
}

export function initChat(ctx, { composer, sidebar }) {
  const thread = $("#thread");
  const inner = $("#thread-inner");
  const main = $("#main");
  const titleEl = $("#chat-title");
  const toBottom = $("#to-bottom");
  const live = $("#live-status");

  let chat = null;
  let job = null; // {chatId, msgId, controller}
  let orb = null;
  let pinned = true;
  let warnedSlim = false;
  const deleted = new Set();
  const polls = new Map();

  const modelName = (id) => (ctx.models.get(id) || {}).name || id || "Модель";
  const msgEl = (id) => inner.querySelector(`[data-id="${CSS.escape(id)}"]`);

  /* ---------- persistence ---------- */
  function persist(c = chat) {
    if (!c || deleted.has(c.id)) return;
    const list = store.loadChats();
    const idx = list.findIndex((x) => x.id === c.id);
    if (idx < 0) list.unshift(c);
    else if (list[idx] !== c) list[idx] = c;
    const res = store.saveChats(list, c.id);
    if (!res.ok) toast("Память браузера переполнена: удалите старые чаты, иначе история не сохранится", { type: "err", duration: 6000 });
    else if (res.slimmed && !warnedSlim) {
      warnedSlim = true;
      toast("Память браузера почти заполнена: вложения в старых чатах удалены из истории", { duration: 5000 });
    }
  }

  /* ---------- scrolling ---------- */
  const nearBottom = () => thread.scrollHeight - thread.scrollTop - thread.clientHeight < 120;
  const scrollToBottom = (smooth = false) => {
    thread.scrollTo({ top: thread.scrollHeight, behavior: smooth ? "smooth" : "auto" });
  };
  thread.addEventListener(
    "scroll",
    () => {
      pinned = nearBottom();
      main.classList.toggle("is-scrolled", thread.scrollTop > 4);
      toBottom.hidden = pinned || !chat;
    },
    { passive: true },
  );
  new ResizeObserver(() => {
    if (pinned) scrollToBottom();
  }).observe(inner);
  toBottom.addEventListener("click", () => {
    pinned = true;
    scrollToBottom(true);
  });

  /* ---------- title ---------- */
  function setTitle() {
    titleEl.textContent = chat ? chat.title : "Новый чат";
    document.title = (chat ? chat.title + " — " : "Чат — ") + "unknown shop";
  }

  /* ---------- empty state ---------- */
  function destroyOrb() {
    if (orb) orb.destroy();
    orb = null;
  }

  function renderEmpty() {
    const name = ctx.me && ctx.me.user && ctx.me.user.first_name;
    const canvas = h("canvas", { "aria-hidden": "true" });
    const chips = SUGGESTIONS.filter((s) => ctx.models.has(s.model)).map((s) => {
      const m = ctx.models.get(s.model);
      const b = h(
        "button",
        { type: "button", class: "empty-chip" },
        h("b", {}, h("span", { "aria-hidden": "true" }, s.icon), s.title),
        h("span", {}, TYPE_LABELS[m.type] + " · " + m.name),
      );
      b.addEventListener("click", () => {
        composer.setModel(s.model);
        composer.setText(s.text);
      });
      return b;
    });
    const sub = ctx.me && ctx.me.subscription;
    const hint =
      sub && sub.active
        ? "Выберите модель внизу, опишите задачу или прикрепите файл. Скриншот можно вставить через Ctrl+V."
        : "Чтобы отправлять запросы, нужна активная подписка. Посмотреть тарифы можно в меню профиля.";
    inner.append(
      h(
        "div",
        { class: "empty" },
        h("div", { class: "empty-orb" }, canvas),
        h("h2", {}, name ? "Привет, " + name + "! Чем займёмся?" : "Чем займёмся сегодня?"),
        h("p", {}, hint),
        h("div", { class: "empty-chips" }, chips),
      ),
    );
    orb = initOrb(canvas);
  }

  /* ---------- rendering ---------- */
  function renderThread() {
    destroyOrb();
    inner.replaceChildren();
    if (!chat || !chat.messages.length) {
      renderEmpty();
    } else {
      chat.messages.forEach((m) => inner.append(renderMsg(m)));
    }
    refreshActions();
    pinned = true;
    scrollToBottom();
    toBottom.hidden = true;
  }

  function renderMsg(m) {
    return m.role === "user" ? renderUser(m) : renderAi(m);
  }

  function renderUser(m) {
    const el = h("article", { class: "msg msg-user", "data-id": m.id, "aria-label": "Ваше сообщение" });
    const atts = m.attachments || [];
    if (atts.length) {
      const box = h("div", { class: "msg-atts" });
      atts.forEach((a) => {
        if (a.kind === "image") {
          const src = safeUrl(a.dataUrl || "");
          box.append(
            src
              ? h("div", { class: "att-img", title: a.name || "" }, h("img", { src, alt: a.name || "Вложенное изображение", loading: "lazy" }))
              : h("div", { class: "att-img dropped" }, "Изображение не сохранено"),
          );
        } else {
          box.append(h("div", { class: "att-file-chip", title: a.name || "" }, h("span", { html: icons.file, style: { display: "inline-flex" } }), h("span", {}, a.name || "Файл")));
        }
      });
      el.append(box);
    }
    if (m.content) el.append(h("div", { class: "bubble" }, m.content));
    return el;
  }

  function paramsLabel(m) {
    const p = m.params || {};
    if (m.kind === "image") return [p.aspect_ratio === "auto" ? "авто" : p.aspect_ratio, p.resolution, p.n > 1 ? "×" + p.n : ""].filter(Boolean).join(" · ");
    if (m.kind === "video") return [p.duration ? p.duration + " с" : "", p.aspect_ratio, p.resolution, p.generate_audio ? "со звуком" : ""].filter(Boolean).join(" · ");
    if (m.kind === "text") return [p.reasoning_effort ? REASONING_LABELS[p.reasoning_effort] || p.reasoning_effort : "", p.web_search ? "веб-поиск" : ""].filter(Boolean).join(" · ");
    return "";
  }

  function renderAi(m) {
    const label = paramsLabel(m);
    const el = h(
      "article",
      { class: "msg msg-ai", "data-id": m.id, "aria-label": "Ответ модели " + modelName(m.model) },
      h("div", { class: "ai-avatar", "aria-hidden": "true" }),
      h(
        "div",
        { class: "msg-body" },
        h("div", { class: "msg-head" }, h("span", { class: "msg-model" }, modelName(m.model)), label ? h("span", {}, "· " + label) : null),
        h("div", { class: "msg-content" }),
        h("div", { class: "msg-actions" }),
      ),
    );
    fillAi(el, m);
    return el;
  }

  function thinkingBlock(text, open) {
    return h(
      "details",
      { class: "thinking", open: open ? true : null },
      h("summary", {}, h("span", { html: icons.brain, style: { display: "inline-flex" } }), "Ход рассуждений"),
      h("div", { class: "thinking-body" }, text),
    );
  }

  function fillAi(el, m) {
    const content = el.querySelector(".msg-content");
    content.replaceChildren();
    const isLive = !!(job && job.msgId === m.id);
    el.classList.toggle("is-live", isLive);
    if (m.kind === "text") {
      if (m.reasoning) content.append(thinkingBlock(m.reasoning, isLive && !m.content));
      if (m.content) {
        const md = h("div", { class: "md" });
        md.innerHTML = renderMarkdown(m.content);
        enhanceCode(md, { highlight: !isLive });
        content.append(md);
      } else if (isLive) {
        content.append(h("div", { class: "typing", role: "img", "aria-label": "Модель отвечает" }, h("i"), h("i"), h("i")));
      }
      if (m.stopped) content.append(h("div", { class: "stopped-note" }, "Генерация остановлена"));
    } else if (m.kind === "image") {
      const blk = imageBlock(m);
      if (blk) content.append(blk);
      if (m.stopped) content.append(h("div", { class: "stopped-note" }, "Генерация остановлена"));
    } else if (m.kind === "video") {
      const blk = videoBlock(m);
      if (blk) content.append(blk);
      if (m.stopped) content.append(h("div", { class: "stopped-note" }, "Запуск видео отменён"));
    }
    if (m.error) content.append(errorCard(m));
    renderActions(el, m);
  }

  function rerender(m) {
    const el = msgEl(m.id);
    if (el) fillAi(el, m);
  }

  function imageBlock(m) {
    const p = m.params || {};
    if (m.pending) {
      const n = Math.max(1, Math.min(4, Number(p.n) || 1));
      const grid = h("div", { class: "img-grid" + (n === 1 ? " single" : "") });
      for (let i = 0; i < n; i++) grid.append(h("div", { class: "img-tile skeleton", style: { "--ar": aspectCss(p.aspect_ratio) } }));
      return h("div", { style: { display: "grid", gap: "10px" } }, grid, h("div", { class: "gen-note" }, h("span", { class: "spinner" }), "Генерируем изображение…"));
    }
    const urls = (m.images || []).map((u) => safeUrl(u)).filter(Boolean);
    if (!urls.length) return null;
    const grid = h("div", { class: "img-grid" + (urls.length === 1 ? " single" : "") });
    urls.forEach((url, i) => {
      const isData = url.startsWith("data:");
      grid.append(
        h(
          "figure",
          { class: "img-tile done", style: { margin: "0" } },
          h("img", { src: url, alt: "Сгенерированное изображение " + (i + 1), loading: "lazy" }),
          h(
            "div",
            { class: "img-actions" },
            isData ? null : h("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, h("span", { html: icons.external, style: { display: "inline-flex" } }), "Открыть"),
            h("a", { href: url, download: "unknown-shop-" + (i + 1) + ".png", target: isData ? null : "_blank", rel: "noopener noreferrer" }, h("span", { html: icons.download, style: { display: "inline-flex" } }), "Скачать"),
          ),
        ),
      );
    });
    return grid;
  }

  function videoBlock(m) {
    const v = m.video;
    if (!v) return null;
    const p = m.params || {};
    if (v.status === "completed" && safeUrl(v.url)) {
      const url = safeUrl(v.url);
      return h(
        "div",
        { class: "video-card" },
        h("video", { controls: true, playsinline: true, preload: "metadata", src: url }),
        h(
          "div",
          { class: "video-links" },
          h("a", { class: "btn btn-ghost btn-sm", href: url, target: "_blank", rel: "noopener noreferrer" }, h("span", { html: icons.external, style: { display: "inline-flex" } }), "Открыть"),
          h("a", { class: "btn btn-ghost btn-sm", href: url, download: "unknown-shop-video.mp4", target: "_blank", rel: "noopener noreferrer" }, h("span", { html: icons.download, style: { display: "inline-flex" } }), "Скачать"),
        ),
      );
    }
    if (v.status === "completed") return h("div", { class: "video-card" }, h("p", { class: "gen-note" }, "Видео готово, но сервер не прислал ссылку на файл."));
    if (v.status === "failed") return null;
    const bars = h("div", { class: "vw-bars", "aria-hidden": "true" }, Array.from({ length: 9 }, () => h("i")));
    return h(
      "div",
      { class: "video-card" },
      h("div", { class: "video-wait", style: { "--ar": aspectCss(p.aspect_ratio || "16:9") } }, h("div", { class: "vw-film", "aria-hidden": "true" }), bars),
      h(
        "div",
        { class: "video-status" },
        h("span", {}, h("b", {}, VIDEO_STATUS[v.status] || "Генерируется"), v.id ? " · задача " + v.id : ""),
        h("span", { class: "js-elapsed", "data-since": String(v.startedAt || m.createdAt || Date.now()) }, "0:00"),
      ),
      h("div", { class: "progress", role: "progressbar", "aria-label": "Генерация видео" }),
      h("p", { class: "gen-note" }, "Обычно это занимает несколько минут. Можно продолжать работу: видео появится здесь само."),
    );
  }

  function errorCard(m) {
    const code = (m.error && m.error.code) || "";
    const info = ERRORS[code] || DEFAULT_ERROR;
    const actions = h("div", { class: "err-actions" });
    if (info.action === "buy" || info.action === "upgrade") {
      const b = h("button", { type: "button", class: "btn btn-accent btn-sm" }, info.action === "upgrade" ? "Перейти на Unlimited" : "Купить подписку");
      b.addEventListener("click", () => {
        if (info.action === "upgrade") {
          const t = ctx.catalog.tariffs.find((x) => x.code === "unlimited");
          if (t) return ctx.account.buy(t);
        }
        ctx.openBuy(info.title);
      });
      actions.append(b);
    }
    if (info.action === "login") {
      const b = h("button", { type: "button", class: "btn btn-light btn-sm" }, "Войти");
      b.addEventListener("click", () => ctx.requireLogin());
      actions.append(b);
    }
    if (isLastAssistant(m) && !job && (info.action === "retry" || info.action === "upgrade" || info.action === "login" || info.sub)) {
      const r = h("button", { type: "button", class: "btn btn-ghost btn-sm" }, h("span", { html: icons.refresh, style: { display: "inline-flex" } }), "Повторить");
      r.addEventListener("click", () => regenerate(m));
      actions.append(r);
    }
    const detail = m.error && m.error.message && m.error.message !== info.title ? m.error.message : "";
    return h(
      "div",
      { class: "err-card" + (info.sub ? " is-sub" : ""), role: "alert" },
      h("div", { class: "err-title" }, h("span", { html: info.sub ? icons.key : icons.alert, style: { display: "inline-flex" } }), info.title),
      h("p", {}, info.text),
      detail ? h("small", {}, detail) : null,
      actions.childElementCount ? actions : null,
    );
  }

  function isLastAssistant(m) {
    if (!chat) return false;
    const last = chat.messages[chat.messages.length - 1];
    return last === m && m.role === "assistant";
  }

  function renderActions(el, m) {
    const box = el.querySelector(".msg-actions");
    if (!box) return;
    box.replaceChildren();
    el.classList.toggle("is-last", isLastAssistant(m));
    if (job && job.msgId === m.id) return;
    if (m.kind === "text" && m.content) {
      const copy = h("button", { type: "button", class: "act-btn", title: "Скопировать ответ" }, h("span", { html: icons.copy, style: { display: "inline-flex" } }), h("span", { "data-label": "Копировать" }, "Копировать"));
      copy.addEventListener("click", () => copyWithFeedback(copy, m.content, { label: "Копировать" }));
      box.append(copy);
    }
    const canRegen = isLastAssistant(m) && !job && !m.error && !(m.kind === "video" && m.video && !isFinal(m.video.status));
    if (canRegen) {
      const regen = h(
        "button",
        { type: "button", class: "act-btn", title: "Сгенерировать заново" },
        h("span", { html: icons.refresh, style: { display: "inline-flex" } }),
        m.kind === "text" ? "Ещё вариант" : "Повторить",
      );
      regen.addEventListener("click", () => regenerate(m));
      box.append(regen);
    }
  }

  function refreshActions() {
    if (!chat) return;
    chat.messages.forEach((m) => {
      if (m.role !== "assistant") return;
      const el = msgEl(m.id);
      if (!el) return;
      if (m.error) fillAi(el, m);
      else renderActions(el, m);
    });
  }

  /* ---------- live streaming paint ---------- */
  let liveTimer = 0;
  let liveLast = 0;
  function scheduleLive(m) {
    if (liveTimer) return;
    const wait = Math.max(0, 60 - (performance.now() - liveLast));
    liveTimer = setTimeout(() => {
      liveTimer = 0;
      liveLast = performance.now();
      paintLive(m);
    }, wait);
  }
  function cancelLive() {
    clearTimeout(liveTimer);
    liveTimer = 0;
  }
  function paintLive(m) {
    const el = msgEl(m.id);
    if (!el) return;
    const content = el.querySelector(".msg-content");
    if (!m.content) {
      if (m.reasoning) {
        let th = content.querySelector(".thinking");
        if (!th) {
          th = thinkingBlock(m.reasoning, true);
          content.prepend(th);
        } else th.querySelector(".thinking-body").textContent = m.reasoning;
      }
      return;
    }
    let md = content.querySelector(".md");
    if (!md) {
      content.replaceChildren();
      if (m.reasoning) content.append(thinkingBlock(m.reasoning, false));
      md = h("div", { class: "md" });
      content.append(md);
    }
    md.innerHTML = renderMarkdown(m.content);
    enhanceCode(md, { highlight: false });
    let target = md.lastElementChild;
    while (target && (target.tagName === "UL" || target.tagName === "OL")) target = target.lastElementChild;
    const caret = h("span", { class: "live-caret", "aria-hidden": "true" });
    if (target && /^(P|LI|H[1-6]|BLOCKQUOTE)$/.test(target.tagName)) target.append(caret);
    else md.append(caret);
  }

  function announce(m) {
    let text = "";
    if (m.error) text = "Ошибка: " + (ERRORS[m.error.code] || DEFAULT_ERROR).title;
    else if (m.kind === "image") text = "Изображения готовы";
    else if (m.kind === "video") text = m.video && m.video.status === "completed" ? "Видео готово" : "Видео поставлено в очередь";
    else text = "Ответ готов";
    live.textContent = text;
  }

  /* ---------- jobs ---------- */
  function startJob(c, m) {
    const controller = new AbortController();
    job = { chatId: c.id, msgId: m.id, controller };
    composer.setBusy(true);
    return controller;
  }
  function endJob() {
    job = null;
    composer.setBusy(false);
    refreshActions();
  }

  function addAssistant(c, m) {
    c.messages.push(m);
    if (chat === c) {
      refreshActions();
      inner.append(renderMsg(m));
      pinned = true;
      scrollToBottom();
    }
  }

  function handleError(m, e) {
    if (e && e.code === "aborted") {
      m.stopped = true;
      return;
    }
    const code = (e && e.code) || "unknown";
    m.error = { code, message: (e && e.message) || "" };
    if (code === "unauthorized") ctx.onUnauthorized();
    else if (code === "subscription_required" || code === "no_api_key" || code === "invalid_api_key") ctx.refreshMe();
  }

  function userContent(m) {
    let text = m.content || "";
    (m.attachments || [])
      .filter((a) => a.kind === "file" && a.text)
      .forEach((f) => {
        text += (text ? "\n\n" : "") + "Файл «" + f.name + "»:\n```\n" + f.text + "\n```";
      });
    const imgs = imageUrls(m);
    if (!imgs.length) return text;
    const parts = [];
    if (text) parts.push({ type: "text", text });
    imgs.forEach((url) => parts.push({ type: "image_url", image_url: { url } }));
    return parts;
  }

  function imageUrls(m) {
    return (m.attachments || []).filter((a) => a.kind === "image" && a.dataUrl).map((a) => a.dataUrl);
  }

  function buildMessages(c, placeholder) {
    const msgs = c.messages;
    const out = [];
    for (let i = 0; i < msgs.length; i++) {
      const m = msgs[i];
      if (m === placeholder) break;
      if (m.kind && m.kind !== "text") continue;
      if (m.role === "user") {
        const next = msgs[i + 1];
        if (next && next !== placeholder && next.role === "assistant" && !next.content && next.error) continue;
        out.push({ role: "user", content: userContent(m) });
      } else if (m.content) {
        out.push({ role: "assistant", content: m.content });
      }
    }
    return out;
  }

  async function runText(c, um, model, options) {
    const params = { reasoning_effort: options.reasoning || null, web_search: !!options.webSearch };
    const m = { id: uid("m_"), role: "assistant", kind: "text", model: model.id, content: "", createdAt: Date.now(), params };
    const controller = startJob(c, m);
    addAssistant(c, m);
    const body = { model: model.id, messages: buildMessages(c, m), stream: true, web_search: params.web_search };
    if (params.reasoning_effort && model.reasoning) body.reasoning_effort = params.reasoning_effort;
    try {
      await api.streamChat(body, {
        signal: controller.signal,
        onDelta: ({ content, reasoning }) => {
          m.content = content;
          if (reasoning) m.reasoning = reasoning;
          scheduleLive(m);
        },
      });
      if (!m.content && !m.reasoning) m.error = { code: "empty", message: "" };
    } catch (e) {
      handleError(m, e);
    } finally {
      cancelLive();
      c.updatedAt = Date.now();
      endJob();
      persist(c);
      rerender(m);
      announce(m);
      if (chat === c) sidebar.render(c.id);
    }
  }

  async function runImage(c, um, model, options) {
    const params = {
      aspect_ratio: options.aspect_ratio || "auto",
      resolution: options.resolution || "2K",
      n: Math.max(1, Math.min(4, Number(options.n) || 1)),
    };
    const m = { id: uid("m_"), role: "assistant", kind: "image", model: model.id, params, pending: true, createdAt: Date.now() };
    const controller = startJob(c, m);
    addAssistant(c, m);
    const body = { model: model.id, prompt: um.content, aspect_ratio: params.aspect_ratio, resolution: params.resolution, n: params.n };
    const imgs = imageUrls(um);
    if (imgs.length) body.images = imgs;
    try {
      const res = await api.generateImages(body, { signal: controller.signal });
      const list = (res && Array.isArray(res.data) ? res.data : []).map((d) => (d && (d.url || (d.b64_json ? "data:image/png;base64," + d.b64_json : ""))) || "");
      m.images = list.filter((u) => safeUrl(u));
      if (!m.images.length) m.error = { code: "empty", message: "Сервис не вернул изображений" };
    } catch (e) {
      handleError(m, e);
    } finally {
      m.pending = false;
      c.updatedAt = Date.now();
      endJob();
      persist(c);
      rerender(m);
      announce(m);
      if (chat === c) sidebar.render(c.id);
    }
  }

  function applyVideoResult(m, res) {
    if (!res || !m.video) return;
    const raw = String(res.status || m.video.status || "").toLowerCase();
    let status = raw;
    if (["completed", "succeeded", "success", "done", "finished"].includes(raw)) status = "completed";
    else if (["failed", "error", "cancelled", "canceled", "expired"].includes(raw)) status = "failed";
    const url =
      res.url ||
      res.video_url ||
      res.output_url ||
      (Array.isArray(res.data) && res.data[0] && (res.data[0].url || res.data[0].video_url)) ||
      (res.output && (res.output.url || res.output.video_url)) ||
      (res.result && res.result.url) ||
      (res.video && res.video.url) ||
      "";
    if (url) m.video.url = String(url);
    m.video.status = status || "processing";
    if (status === "failed") {
      const em = res.error && (res.error.message || (typeof res.error === "string" ? res.error : ""));
      m.error = { code: "video_failed", message: em || "" };
    }
  }

  async function runVideo(c, um, model, options) {
    const params = {
      aspect_ratio: options.aspect_ratio || "16:9",
      duration: Number(options.duration) || 5,
      resolution: options.resolution || "1080p",
      generate_audio: options.generate_audio !== false,
    };
    const startedAt = Date.now();
    const m = { id: uid("m_"), role: "assistant", kind: "video", model: model.id, params, video: { status: "starting", startedAt }, createdAt: startedAt };
    const controller = startJob(c, m);
    addAssistant(c, m);
    const body = {
      model: model.id,
      prompt: um.content,
      aspect_ratio: params.aspect_ratio,
      duration: params.duration,
      resolution: params.resolution,
      generate_audio: params.generate_audio,
    };
    const imgs = imageUrls(um);
    if (imgs.length) body.images = imgs;
    let ok = false;
    try {
      const res = await api.createVideo(body, { signal: controller.signal });
      if (!res || !res.id) throw new api.ApiError(0, "bad_response", "Сервер не вернул ID задачи");
      m.video = { id: String(res.id), status: "queued", startedAt };
      applyVideoResult(m, res);
      ok = true;
    } catch (e) {
      handleError(m, e);
      m.video = null;
    } finally {
      c.updatedAt = Date.now();
      endJob();
      persist(c);
      rerender(m);
      announce(m);
      if (chat === c) sidebar.render(c.id);
    }
    if (ok && !isFinal(m.video.status)) pollVideo(c.id, m.id);
  }

  function pollVideo(chatId, msgId) {
    if (polls.has(msgId)) return;
    const st = { timer: 0, fails: 0, ctrl: null };
    polls.set(msgId, st);
    const tick = async () => {
      const c = store.getChat(chatId);
      const m = c && c.messages.find((x) => x.id === msgId);
      if (!c || !m || !m.video || !m.video.id || isFinal(m.video.status) || deleted.has(chatId)) {
        polls.delete(msgId);
        return;
      }
      st.ctrl = new AbortController();
      try {
        const res = await api.getVideo(m.video.id, { signal: st.ctrl.signal });
        st.fails = 0;
        const before = m.video.status + "|" + (m.video.url || "");
        applyVideoResult(m, res);
        if (before !== m.video.status + "|" + (m.video.url || "")) {
          persist(c);
          rerender(m);
          if (chat && chat.id === chatId) refreshActions();
          if (isFinal(m.video.status)) {
            announce(m);
            if (!chat || chat.id !== chatId) toast(m.video.status === "completed" ? "Видео готово: «" + c.title + "»" : "Видео не получилось: «" + c.title + "»", { type: m.video.status === "completed" ? "ok" : "err" });
          }
        }
      } catch (e) {
        if (e && e.code === "aborted") {
          polls.delete(msgId);
          return;
        }
        if (e && e.code === "unauthorized") {
          polls.delete(msgId);
          ctx.onUnauthorized();
          return;
        }
        if (e && e.status === 404) {
          m.video.status = "failed";
          m.error = { code: "video_failed", message: "Задача не найдена" };
          persist(c);
          rerender(m);
          polls.delete(msgId);
          return;
        }
        st.fails++;
      }
      if (isFinal(m.video.status)) {
        polls.delete(msgId);
        return;
      }
      st.timer = setTimeout(tick, st.fails > 3 ? 15000 : 5000);
    };
    st.timer = setTimeout(tick, 5000);
  }

  function stopPoll(msgId) {
    const st = polls.get(msgId);
    if (!st) return;
    clearTimeout(st.timer);
    if (st.ctrl) st.ctrl.abort();
    polls.delete(msgId);
  }

  // elapsed timers for running videos
  setInterval(() => {
    inner.querySelectorAll(".js-elapsed").forEach((el) => {
      const s = Math.max(0, Math.floor((Date.now() - Number(el.dataset.since)) / 1000));
      el.textContent = Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
    });
  }, 1000);

  function run(c, um, model, options) {
    if (model.type === "image") return runImage(c, um, model, options || {});
    if (model.type === "video") return runVideo(c, um, model, options || {});
    return runText(c, um, model, options || {});
  }

  function regenerate(m) {
    if (job || !chat) return;
    const idx = chat.messages.indexOf(m);
    if (idx < 0) return;
    let ui = idx - 1;
    while (ui >= 0 && chat.messages[ui].role !== "user") ui--;
    if (ui < 0) return;
    const um = chat.messages[ui];
    const model = ctx.models.get(m.model) || ctx.models.get(um.model) || composer.model;
    const p = m.params || {};
    const options =
      model.type === "text" ? { reasoning: p.reasoning_effort || (model.reasoning ? model.default_reasoning : null), webSearch: !!p.web_search } : { ...p };
    stopPoll(m.id);
    chat.messages.splice(idx, 1);
    const el = msgEl(m.id);
    if (el) el.remove();
    persist();
    run(chat, um, model, options);
  }

  /* ---------- public ---------- */
  return {
    send({ text, attachments, model, options }) {
      if (job) {
        toast("Дождитесь ответа или остановите генерацию");
        return false;
      }
      if (!ctx.me) {
        ctx.requireLogin();
        return false;
      }
      const now = Date.now();
      if (!chat) {
        chat = store.createChat({ type: model.type, model: model.id, title: makeTitle(text, attachments) });
        deleted.delete(chat.id);
      }
      chat.type = model.type;
      chat.model = model.id;
      chat.updatedAt = now;
      const um = { id: uid("m_"), role: "user", kind: model.type, model: model.id, content: text, createdAt: now };
      if (attachments && attachments.length) um.attachments = attachments.map((a) => ({ ...a }));
      if (inner.querySelector(".empty")) {
        destroyOrb();
        inner.replaceChildren();
      }
      chat.messages.push(um);
      inner.append(renderMsg(um));
      persist();
      sidebar.render(chat.id);
      setTitle();
      pinned = true;
      scrollToBottom();
      run(chat, um, model, options);
      return true;
    },
    stop() {
      if (job) job.controller.abort();
    },
    openChat(id) {
      const c = store.getChat(id);
      if (!c) return this.newChat();
      chat = c;
      store.setPrefs({ lastChat: id });
      renderThread();
      sidebar.setActive(id);
      setTitle();
      if (!job && ctx.models.has(c.model) && composer.model.id !== c.model) composer.setModel(c.model);
      composer.focus();
    },
    newChat() {
      chat = null;
      renderThread();
      sidebar.setActive(null);
      setTitle();
      composer.focus();
    },
    deleteChat(id) {
      if (job && job.chatId === id) job.controller.abort();
      const c = store.getChat(id);
      if (c) c.messages.forEach((m) => stopPoll(m.id));
      deleted.add(id);
      store.deleteChat(id);
      if (chat && chat.id === id) {
        chat = null;
        renderThread();
        setTitle();
      }
      sidebar.render(chat ? chat.id : null);
      toast("Чат удалён");
    },
    resumeVideos() {
      store.loadChats().forEach((c) =>
        c.messages.forEach((m) => {
          if (m.kind === "video" && m.video && m.video.id && !isFinal(m.video.status)) pollVideo(c.id, m.id);
        }),
      );
    },
    onRenamed(id) {
      if (chat && chat.id === id) setTitle();
    },
    refreshEmpty() {
      if (!chat) renderThread();
    },
  };
}
