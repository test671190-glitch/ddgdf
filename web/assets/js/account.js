// Account: subscription badge, avatar menu and the «Подписка и ключ» panel.

import * as api from "./api.js";
import * as store from "./store.js";
import { h, $, icons, toast, plural, formatDate, copyWithFeedback } from "./utils.js";
import { createDialog, openPaymentModal, openTariffsModal } from "./modals.js";

const daysWord = (n) => plural(n, ["день", "дня", "дней"]);

function maskKey(key) {
  if (!key) return "";
  if (key.length <= 10) return "•".repeat(key.length);
  return key.slice(0, 5) + "•".repeat(Math.min(18, key.length - 9)) + key.slice(-4);
}

export function initAccount(ctx) {
  const badge = $("#sub-badge");
  const badgeText = $("#sub-badge-text");
  const avatarBtn = $("#avatar-btn");
  const menu = $("#avatar-menu");

  const tariffByCode = (code) => ctx.catalog.tariffs.find((t) => t.code === code) || null;

  function renderBadge() {
    const me = ctx.me;
    const sub = me && me.subscription;
    badge.hidden = !me;
    if (!me) return;
    badge.classList.remove("is-inactive", "is-warn");
    if (sub && sub.active) {
      const d = Number(sub.days_left) || 0;
      badgeText.textContent = (sub.title || sub.tariff || "Тариф") + " · " + d + " дн.";
      if (d <= 3) badge.classList.add("is-warn");
      badge.title = "Подписка активна до " + formatDate(sub.expires_at);
    } else {
      badgeText.textContent = "Нет подписки";
      badge.classList.add("is-inactive");
      badge.title = "Оформить подписку";
    }
    const u = me.user || {};
    const name = u.first_name || u.username || "U";
    avatarBtn.textContent = (name[0] || "U").toUpperCase();
  }

  /* ---------- avatar menu ---------- */
  function closeMenu(focus) {
    if (menu.hidden) return;
    menu.hidden = true;
    avatarBtn.setAttribute("aria-expanded", "false");
    if (focus) avatarBtn.focus();
  }
  function openMenu() {
    const u = (ctx.me && ctx.me.user) || {};
    const item = (icon, label, onClick, cls = "") => {
      const b = h("button", { type: "button", class: "menu-item " + cls, role: "menuitem" }, h("span", { html: icons[icon], style: { display: "inline-flex" } }), label);
      b.addEventListener("click", () => {
        closeMenu();
        onClick();
      });
      return b;
    };
    const link = (icon, label, href, blank) =>
      h(
        "a",
        { class: "menu-item", role: "menuitem", href, target: blank ? "_blank" : null, rel: blank ? "noopener" : null },
        h("span", { html: icons[icon], style: { display: "inline-flex" } }),
        label,
      );
    menu.replaceChildren(
      h("div", { class: "am-head" }, h("b", {}, u.first_name || "Профиль"), h("span", {}, u.username ? "@" + u.username : "ID " + (u.id ?? "—"))),
      h("div", { class: "menu-sep" }),
      item("key", "Подписка и ключ", openPanel),
      link("book", "Документация", "/docs", true),
      link("home", "Главная", "/"),
      h("div", { class: "menu-sep" }),
      item("logout", "Выйти", doLogout, "danger"),
    );
    menu.hidden = false;
    avatarBtn.setAttribute("aria-expanded", "true");
    const first = menu.querySelector(".menu-item");
    if (first) first.focus();
  }
  avatarBtn.addEventListener("click", () => (menu.hidden ? openMenu() : closeMenu()));
  document.addEventListener("pointerdown", (e) => {
    if (!menu.hidden && !menu.contains(e.target) && e.target !== avatarBtn) closeMenu();
  });
  menu.addEventListener("keydown", (e) => {
    const items = Array.from(menu.querySelectorAll(".menu-item"));
    const i = items.indexOf(document.activeElement);
    if (e.key === "Escape") closeMenu(true);
    else if (e.key === "ArrowDown") {
      e.preventDefault();
      items[(i + 1) % items.length].focus();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      items[(i - 1 + items.length) % items.length].focus();
    } else if (e.key === "Tab") closeMenu();
  });
  badge.addEventListener("click", openPanel);

  async function doLogout() {
    try {
      await api.logout();
    } catch (e) {
      if (e && e.code === "network") {
        toast("Нет соединения с сервером", { type: "err" });
        return;
      }
    }
    store.clearAccountData();
    window.location.href = "/";
  }

  /* ---------- panel ---------- */
  function buy(tariff) {
    openPaymentModal({
      tariff,
      catalog: ctx.catalog,
      onPaid: ctx.refreshMe,
      onUnauthorized: ctx.requireLogin,
    });
  }

  function openPanel() {
    const me = ctx.me;
    if (!me) {
      ctx.requireLogin();
      return;
    }
    const dlg = createDialog({ title: "Подписка и ключ", subtitle: "Тариф, срок действия и доступ к API", className: "account-modal" });
    const u = me.user || {};
    const sub = me.subscription;
    const active = !!(sub && sub.active);
    const tariff = sub ? tariffByCode(sub.tariff) : null;
    const days = ctx.catalog.subscription_days || 30;

    const userRow = h(
      "div",
      { class: "acc-user" },
      h("span", { class: "avatar-btn", "aria-hidden": "true" }, ((u.first_name || u.username || "U")[0] || "U").toUpperCase()),
      h("div", {}, h("b", {}, u.first_name || "Пользователь"), h("span", {}, u.username ? "@" + u.username : "Telegram ID " + (u.id ?? "—"))),
    );

    const planCard = h("section", { class: "acc-card", "aria-label": "Подписка" }, h("h3", {}, "Подписка"));
    if (sub) {
      const left = Math.max(0, Number(sub.days_left) || 0);
      planCard.append(
        h(
          "div",
          { class: "acc-plan" },
          h("b", {}, (tariff && tariff.emoji ? tariff.emoji + " " : "") + (sub.title || sub.tariff)),
          h("span", { class: "status-chip" + (active ? "" : " off") }, active ? "Активна" : "Не активна"),
        ),
        h(
          "div",
          { class: "acc-dates" },
          h("div", {}, h("span", {}, active ? "Действует до" : "Закончилась"), h("b", {}, formatDate(sub.expires_at))),
          h("div", {}, h("span", {}, "Осталось"), h("b", {}, left + " " + daysWord(left))),
        ),
        h("div", { class: "days-bar", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": String(days), "aria-valuenow": String(left), "aria-label": "Осталось дней" }, h("i", { style: { width: Math.min(100, (left / days) * 100) + "%" } })),
      );
    } else {
      planCard.append(h("p", { class: "acc-note" }, "Подписки пока нет. Выберите тариф, чтобы открыть все модели в чате и API."));
    }

    const actions = h("div", { class: "acc-actions" });
    if (active && tariff) {
      const renew = h("button", { type: "button", class: "btn btn-accent" }, "Продлить " + tariff.title);
      renew.addEventListener("click", () => {
        dlg.close();
        setTimeout(() => buy(tariff), 200);
      });
      const change = h("button", { type: "button", class: "btn btn-ghost" }, "Сменить тариф");
      change.addEventListener("click", () => {
        dlg.close();
        setTimeout(() => openTariffsModal({ catalog: ctx.catalog, me: ctx.me, onPaid: ctx.refreshMe, onUnauthorized: ctx.requireLogin }), 200);
      });
      actions.append(renew, change);
    } else {
      ctx.catalog.tariffs.forEach((t) => {
        const b = h("button", { type: "button", class: "btn " + (t.popular ? "btn-accent" : "btn-light") }, "Купить " + t.title + " · $" + t.price_usd);
        b.addEventListener("click", () => {
          dlg.close();
          setTimeout(() => buy(t), 200);
        });
        actions.append(b);
      });
    }
    planCard.append(actions);

    const keyCard = h("section", { class: "acc-card", "aria-label": "Доступ к API" }, h("h3", {}, "API"));
    if (me.api_key) {
      let shown = false;
      const value = h("code", { class: "field-value" }, maskKey(me.api_key));
      const toggle = h("button", { type: "button", class: "icon-btn sm plain", "aria-label": "Показать ключ", "aria-pressed": "false", html: icons.eye });
      toggle.addEventListener("click", () => {
        shown = !shown;
        value.textContent = shown ? me.api_key : maskKey(me.api_key);
        toggle.innerHTML = shown ? icons.eyeOff : icons.eye;
        toggle.setAttribute("aria-label", shown ? "Скрыть ключ" : "Показать ключ");
        toggle.setAttribute("aria-pressed", String(shown));
      });
      const copy = h("button", { type: "button", class: "btn btn-ghost btn-sm" }, h("span", { html: icons.copy, style: { display: "inline-flex" } }), h("span", { "data-label": "Копировать" }, "Копировать"));
      copy.addEventListener("click", () => copyWithFeedback(copy, me.api_key, { label: "Копировать" }));
      keyCard.append(h("div", { class: "acc-field" }, h("span", {}, "API-ключ"), h("div", { class: "field-row" }, value, toggle, copy)));
    } else {
      keyCard.append(h("p", { class: "acc-note" }, "Ключ появится здесь сразу после оплаты подписки."));
    }
    const baseCopy = h("button", { type: "button", class: "btn btn-ghost btn-sm" }, h("span", { html: icons.copy, style: { display: "inline-flex" } }), h("span", { "data-label": "Копировать" }, "Копировать"));
    baseCopy.addEventListener("click", () => copyWithFeedback(baseCopy, ctx.catalog.api_base_url, { label: "Копировать" }));
    keyCard.append(
      h("div", { class: "acc-field" }, h("span", {}, "Base URL"), h("div", { class: "field-row" }, h("code", { class: "field-value" }, ctx.catalog.api_base_url), baseCopy)),
      h("p", { class: "acc-note" }, "Храните ключ в переменной окружения и не публикуйте его в открытом коде."),
      h(
        "div",
        { class: "acc-links" },
        h("a", { href: "/docs", target: "_blank", rel: "noopener" }, "Документация API →"),
        h("a", { href: api.botLink(ctx.catalog), target: "_blank", rel: "noopener noreferrer" }, "Telegram-бот →"),
      ),
    );

    dlg.body.append(userRow, planCard, keyCard);
  }

  renderBadge();
  return { renderBadge, openPanel, buy };
}
