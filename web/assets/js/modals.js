// Shared dialogs: Telegram login (modal or inline full-screen), payment and tariff chooser.

import * as api from "./api.js";
import { h, icons, toast, plural } from "./utils.js";

/* ---------------- dialog shell ---------------- */

export function createDialog({ title, subtitle, className = "", onClose, labelledBy } = {}) {
  const titleId = "dlg-" + Math.random().toString(36).slice(2, 8);
  const closeBtn = h("button", { type: "button", class: "icon-btn plain", "aria-label": "Закрыть", html: icons.x });
  const titleEl = h("h2", { id: titleId }, title || "");
  const subEl = h("p", {}, subtitle || "");
  if (!subtitle) subEl.hidden = true;
  const head = h("div", { class: "modal-head" }, h("div", {}, titleEl, subEl), closeBtn);
  const body = h("div", { class: "modal-body" });
  const dialog = h("dialog", { class: "modal " + className, "aria-labelledby": labelledBy || titleId }, head, body);
  document.body.append(dialog);

  let closing = false;
  const close = () => {
    if (closing || !dialog.open) return;
    closing = true;
    dialog.classList.add("is-closing");
    setTimeout(() => dialog.close(), 180);
  };
  closeBtn.addEventListener("click", close);
  dialog.addEventListener("cancel", (e) => {
    e.preventDefault();
    close();
  });
  dialog.addEventListener("click", (e) => {
    if (e.target === dialog) close();
  });
  dialog.addEventListener("close", () => {
    if (onClose) onClose();
    dialog.remove();
  });

  dialog.showModal();
  return {
    dialog,
    body,
    close,
    setTitle(t, s) {
      titleEl.textContent = t;
      subEl.textContent = s || "";
      subEl.hidden = !s;
    },
  };
}

/* ---------------- Telegram login flow ---------------- */

function renderQr(box, url) {
  box.replaceChildren();
  box.classList.remove("is-loading", "is-expired");
  if (!window.qrcode) {
    box.hidden = true;
    return;
  }
  try {
    const qr = window.qrcode(0, "M");
    qr.addData(url);
    qr.make();
    const img = h("img", { src: qr.createDataURL(8, 0), alt: "QR-код для входа через Telegram", width: 172, height: 172 });
    box.append(img);
    box.hidden = false;
  } catch {
    box.hidden = true;
  }
}

/**
 * Mounts the login flow into `container`. Polls the backend every 2 s.
 * @returns {{destroy: Function, restart: Function}}
 */
export function mountLoginFlow(container, { onConfirmed } = {}) {
  let token = null;
  let timer = 0;
  let expireTimer = 0;
  let ctrl = null;
  let destroyed = false;

  const qrBox = h("div", { class: "qr-box is-loading", "aria-hidden": "false" });
  const openBtn = h(
    "a",
    { class: "btn btn-accent btn-lg btn-block", href: "#", target: "_blank", rel: "noopener noreferrer", "aria-disabled": "true" },
    h("span", { html: icons.telegram, style: { display: "inline-flex", width: "20px", height: "20px" } }),
    "Открыть Telegram",
  );
  const status = h("div", { class: "login-status", role: "status", "aria-live": "polite" }, h("span", { class: "spinner" }), "Готовим ссылку для входа…");
  const retryBtn = h("button", { type: "button", class: "btn btn-ghost btn-block", hidden: true }, h("span", { html: icons.refresh, style: { display: "inline-flex" } }), "Получить новую ссылку");
  const steps = h(
    "ol",
    { class: "login-steps" },
    h("li", {}, "Нажмите «Открыть Telegram» или отсканируйте QR-код телефоном."),
    h("li", {}, "В боте нажмите «Запустить» и подтвердите вход."),
    h("li", {}, "Вернитесь на эту страницу: вход произойдёт автоматически."),
  );
  const flow = h("div", { class: "login-flow" }, qrBox, openBtn, status, retryBtn, steps);
  container.append(flow);

  const setStatus = (text, kind = "wait") => {
    status.className = "login-status" + (kind === "ok" ? " ok" : kind === "err" ? " err" : "");
    status.replaceChildren();
    if (kind === "wait") status.append(h("span", { class: "spinner" }));
    if (kind === "ok") status.append(h("span", { html: icons.check, style: { display: "inline-flex", width: "18px" } }));
    if (kind === "err") status.append(h("span", { html: icons.alert, style: { display: "inline-flex", width: "18px" } }));
    status.append(text);
  };

  const stopPolling = () => {
    clearTimeout(timer);
    clearTimeout(expireTimer);
    if (ctrl) ctrl.abort();
    ctrl = null;
  };

  const markExpired = () => {
    stopPolling();
    token = null;
    qrBox.classList.add("is-expired");
    openBtn.setAttribute("aria-disabled", "true");
    openBtn.removeAttribute("href");
    setStatus("Ссылка устарела", "err");
    retryBtn.hidden = false;
  };

  async function poll() {
    if (destroyed || !token) return;
    ctrl = new AbortController();
    try {
      const res = await api.getLoginStatus(token, { signal: ctrl.signal });
      if (destroyed) return;
      if (res && res.status === "confirmed") {
        stopPolling();
        setStatus("Вход подтверждён", "ok");
        if (onConfirmed) onConfirmed(res.user || null);
        return;
      }
      if (res && res.status === "expired") {
        markExpired();
        return;
      }
    } catch (e) {
      if (destroyed || (e && e.code === "aborted")) return;
      if (e && (e.status === 404 || e.status === 410)) {
        markExpired();
        return;
      }
      // transient error: keep polling
    }
    timer = setTimeout(poll, 2000);
  }

  async function start() {
    stopPolling();
    retryBtn.hidden = true;
    qrBox.classList.add("is-loading");
    qrBox.classList.remove("is-expired");
    openBtn.setAttribute("aria-disabled", "true");
    setStatus("Готовим ссылку для входа…");
    try {
      const res = await api.startLogin();
      if (destroyed) return;
      token = res.token;
      const url = String(res.url || "");
      if (/^https?:\/\//i.test(url || "") || /^tg:\/\//i.test(url || "")) {
        openBtn.href = url;
        openBtn.removeAttribute("aria-disabled");
        renderQr(qrBox, url);
      }
      setStatus("Ждём подтверждения в Telegram…");
      const ttl = Number(res.expires_in) > 0 ? Number(res.expires_in) * 1000 : 0;
      if (ttl) expireTimer = setTimeout(markExpired, ttl + 1500);
      timer = setTimeout(poll, 2000);
    } catch (e) {
      if (destroyed) return;
      qrBox.classList.remove("is-loading");
      qrBox.classList.add("is-expired");
      setStatus(e && e.code === "network" ? "Сервер недоступен. Проверьте соединение." : "Не удалось начать вход. Попробуйте ещё раз.", "err");
      retryBtn.hidden = false;
      retryBtn.lastChild.textContent = "Попробовать снова";
    }
  }

  const onVisible = () => {
    if (!document.hidden && token && !destroyed) {
      clearTimeout(timer);
      if (ctrl) ctrl.abort();
      poll();
    }
  };
  document.addEventListener("visibilitychange", onVisible);
  retryBtn.addEventListener("click", () => {
    retryBtn.lastChild.textContent = "Получить новую ссылку";
    start();
  });
  openBtn.addEventListener("click", (e) => {
    if (openBtn.getAttribute("aria-disabled") === "true") e.preventDefault();
  });

  start();

  return {
    restart: start,
    destroy() {
      destroyed = true;
      stopPolling();
      document.removeEventListener("visibilitychange", onVisible);
      flow.remove();
    },
  };
}

/** Opens «Вход через Telegram» modal. Resolves the callback after the session cookie is set. */
export function openLoginModal({ onSuccess, subtitle } = {}) {
  let flow = null;
  const dlg = createDialog({
    title: "Вход через Telegram",
    subtitle: subtitle || "Без паролей: подтвердите вход в нашем боте.",
    className: "login-modal",
    onClose: () => flow && flow.destroy(),
  });
  flow = mountLoginFlow(dlg.body, {
    onConfirmed: (user) => {
      setTimeout(() => {
        dlg.close();
        toast("Вы вошли" + (user && user.first_name ? ", " + user.first_name : ""), { type: "ok" });
        if (onSuccess) onSuccess(user);
      }, 500);
    },
  });
  return dlg;
}

/* ---------------- payment ---------------- */

const daysLabel = (n) => n + " " + plural(n, ["день", "дня", "дней"]);

/**
 * Payment modal for one tariff: crypto invoice (polled every 4 s) or Telegram Stars via the bot.
 */
export function openPaymentModal({ tariff, catalog, onPaid, onUnauthorized }) {
  let pollTimer = 0;
  let ctrl = null;
  let closed = false;
  const days = catalog.subscription_days || 30;

  const stop = () => {
    clearTimeout(pollTimer);
    if (ctrl) ctrl.abort();
    ctrl = null;
  };

  const dlg = createDialog({
    title: "Оплата тарифа " + tariff.title,
    subtitle: "Подписка на " + daysLabel(days) + ", без автопродления.",
    className: "pay-modal",
    onClose: () => {
      closed = true;
      stop();
    },
  });

  const summary = () =>
    h(
      "div",
      { class: "pay-summary" },
      h("div", {}, h("span", {}, (tariff.emoji ? tariff.emoji + " " : "") + tariff.title), h("div", { style: { color: "var(--muted)", fontSize: "13px", marginTop: "4px" } }, tariff.tagline || "")),
      h("b", {}, "$" + tariff.price_usd),
    );

  function viewChoose() {
    stop();
    const crypto = h(
      "button",
      { type: "button", class: "pay-option" },
      h("span", { class: "po-ico", html: icons.crypto, style: { color: "var(--accent)" } }),
      h("span", {}, h("b", {}, "Криптой"), h("small", {}, "Счёт на $" + tariff.price_usd + " откроется в новой вкладке")),
      h("span", { html: icons.arrowRight, style: { display: "inline-flex" } }),
    );
    crypto.addEventListener("click", createInvoice);

    const starsHref = api.botLink(catalog, "buy_" + tariff.code);
    const stars = h(
      "a",
      { class: "pay-option", href: starsHref, target: "_blank", rel: "noopener noreferrer" },
      h("span", { class: "po-ico", html: icons.star, style: { color: "var(--gold)" } }),
      h(
        "span",
        {},
        h("b", {}, "Telegram Stars"),
        h("small", {}, tariff.stars ? tariff.stars.toLocaleString("ru-RU") + " ⭐ · оплата в нашем боте" : "Оплата в нашем Telegram-боте"),
      ),
      h("span", { html: icons.external, style: { display: "inline-flex" } }),
    );
    stars.addEventListener("click", () => setTimeout(viewStarsHint, 400));

    dlg.body.replaceChildren(summary(), h("div", { class: "pay-options" }, crypto, stars));
  }

  function viewStarsHint() {
    if (closed) return;
    const refresh = h("button", { type: "button", class: "btn btn-light btn-block" }, "Я оплатил, обновить подписку");
    refresh.addEventListener("click", async () => {
      refresh.disabled = true;
      if (onPaid) await onPaid();
      dlg.close();
    });
    const back = h("button", { type: "button", class: "btn btn-quiet btn-block" }, "Выбрать другой способ");
    back.addEventListener("click", viewChoose);
    dlg.body.replaceChildren(
      h(
        "div",
        { class: "pay-wait" },
        h("div", { class: "po-ico", html: icons.star, style: { width: "56px", height: "56px", display: "grid", placeItems: "center", borderRadius: "16px", background: "var(--elev)", color: "var(--gold)" } }),
        h("p", {}, "Бот откроет страницу тарифа. После оплаты Stars вернитесь сюда, и мы обновим подписку."),
      ),
      refresh,
      back,
    );
  }

  async function createInvoice() {
    stop();
    dlg.body.replaceChildren(summary(), h("div", { class: "pay-wait" }, h("span", { class: "spinner lg" }), h("p", {}, "Создаём счёт…")));
    let inv;
    try {
      inv = await api.createCryptoInvoice(tariff.code);
    } catch (e) {
      if (closed) return;
      if (e && e.code === "unauthorized") {
        dlg.close();
        if (onUnauthorized) onUnauthorized();
        return;
      }
      viewError(e && e.code === "network" ? "Сервер недоступен. Проверьте соединение." : (e && e.message) || "Не удалось создать счёт.");
      return;
    }
    if (closed) return;
    const payUrl = /^https?:\/\//i.test(inv.pay_url || "") ? inv.pay_url : "";
    if (payUrl) {
      const w = window.open(payUrl, "_blank", "noopener");
      if (w) {
        try {
          w.opener = null;
        } catch {
          /* cross-origin */
        }
      }
    }
    viewWaiting(inv, payUrl);
    pollTimer = setTimeout(() => poll(inv), 4000);
  }

  function viewWaiting(inv, payUrl) {
    const back = h("button", { type: "button", class: "btn btn-quiet btn-block" }, "Назад к способам оплаты");
    back.addEventListener("click", viewChoose);
    dlg.body.replaceChildren(
      h(
        "div",
        { class: "pay-wait" },
        h("div", { class: "pulse-ring", "aria-hidden": "true" }),
        h("b", { style: { fontSize: "18px" } }, "Ожидаем оплату…"),
        h("p", {}, "Сумма к оплате: $" + (inv.amount_usd ?? tariff.price_usd) + ". Статус обновится автоматически, окно можно не закрывать."),
      ),
      payUrl
        ? h("a", { class: "btn btn-light btn-block", href: payUrl, target: "_blank", rel: "noopener noreferrer" }, "Открыть страницу оплаты", h("span", { html: icons.external, style: { display: "inline-flex" } }))
        : null,
      back,
    );
  }

  async function poll(inv) {
    if (closed) return;
    ctrl = new AbortController();
    try {
      const res = await api.getCryptoInvoice(inv.order_id, { signal: ctrl.signal });
      if (closed) return;
      if (res && res.status === "paid") {
        viewPaid();
        return;
      }
      if (res && res.status === "expired") {
        viewExpired();
        return;
      }
    } catch (e) {
      if (closed || (e && e.code === "aborted")) return;
    }
    pollTimer = setTimeout(() => poll(inv), 4000);
  }

  async function viewPaid() {
    stop();
    const done = h("button", { type: "button", class: "btn btn-accent btn-block" }, "Готово");
    done.addEventListener("click", () => dlg.close());
    dlg.setTitle("Оплата получена", "Тариф " + tariff.title + " активирован.");
    dlg.body.replaceChildren(
      h("div", { class: "pay-wait" }, h("div", { class: "pay-done", html: icons.check }), h("p", {}, "Спасибо! Подписка уже работает: можно открывать чат или подключать API.")),
      done,
    );
    if (onPaid) {
      try {
        await onPaid();
      } catch {
        /* refresh is best-effort */
      }
    }
  }

  function viewExpired() {
    stop();
    const again = h("button", { type: "button", class: "btn btn-light btn-block" }, "Создать новый счёт");
    again.addEventListener("click", createInvoice);
    const back = h("button", { type: "button", class: "btn btn-quiet btn-block" }, "Другой способ оплаты");
    back.addEventListener("click", viewChoose);
    dlg.body.replaceChildren(h("div", { class: "pay-wait" }, h("span", { html: icons.clock, style: { width: "40px", color: "var(--muted)" } }), h("b", {}, "Счёт истёк"), h("p", {}, "Оплата не поступила вовремя. Создайте новый счёт, это бесплатно.")), again, back);
  }

  function viewError(msg) {
    const again = h("button", { type: "button", class: "btn btn-light btn-block" }, "Попробовать снова");
    again.addEventListener("click", createInvoice);
    const back = h("button", { type: "button", class: "btn btn-quiet btn-block" }, "Назад");
    back.addEventListener("click", viewChoose);
    dlg.body.replaceChildren(h("div", { class: "pay-wait" }, h("span", { html: icons.alert, style: { width: "36px", color: "var(--danger)" } }), h("p", {}, msg)), again, back);
  }

  viewChoose();
  return dlg;
}

/** Tariff chooser (used in the app when there is no active subscription). */
export function openTariffsModal({ catalog, me, onPaid, onUnauthorized, reason }) {
  const dlg = createDialog({
    title: reason || "Выберите тариф",
    subtitle: "Все 49 моделей и API в каждом тарифе. Подписка на " + daysLabel(catalog.subscription_days || 30) + ".",
    className: "tariffs-modal",
  });
  const current = me && me.subscription && me.subscription.active ? me.subscription.tariff : null;
  const list = h("div", { class: "pay-options" });
  catalog.tariffs.forEach((t) => {
    const b = h(
      "button",
      { type: "button", class: "pay-option" },
      h("span", { class: "po-ico" }, t.emoji || "💎"),
      h("span", {}, h("b", {}, t.title + (t.popular ? " · популярный" : "")), h("small", {}, t.tagline || (t.features || [])[0] || "")),
      h("b", { style: { fontFamily: "var(--font-display)" } }, "$" + t.price_usd),
    );
    if (current === t.code) b.querySelector("small").textContent = "Ваш тариф · продлить ещё на " + daysLabel(catalog.subscription_days || 30);
    b.addEventListener("click", () => {
      dlg.close();
      setTimeout(() => openPaymentModal({ tariff: t, catalog, onPaid, onUnauthorized }), 200);
    });
    list.append(b);
  });
  dlg.body.append(list);
  return dlg;
}
