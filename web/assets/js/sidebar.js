// Chat history sidebar: search, type filters, grouping by date, rename/delete, mobile drawer.

import * as store from "./store.js";
import { h, $, $$, icons } from "./utils.js";
import { createDialog } from "./modals.js";

export function initSidebar(ctx, { onSelect, onNew, onDelete }) {
  const app = $("#app");
  const list = $("#chat-list");
  const search = $("#chat-search");
  const backdrop = $("#sb-backdrop");
  const openBtn = $("#sb-open");
  const mobile = window.matchMedia("(max-width: 860px)");
  let filter = "all";
  let query = "";
  let activeId = null;
  let menu = null;
  let lastIds = new Set();

  /* ---------- open / collapse ---------- */
  const prefs = store.getPrefs();
  if (prefs.sidebarCollapsed && !mobile.matches) app.classList.add("sb-collapsed");

  function setOpen(open) {
    if (mobile.matches) {
      app.classList.toggle("sb-open", open);
      backdrop.hidden = !open;
      openBtn.setAttribute("aria-expanded", String(open));
      if (open) setTimeout(() => search.focus({ preventScroll: true }), 50);
    } else {
      app.classList.toggle("sb-collapsed", !open);
      openBtn.setAttribute("aria-expanded", String(open));
      store.setPrefs({ sidebarCollapsed: !open });
    }
  }
  openBtn.addEventListener("click", () => setOpen(true));
  $("#sb-collapse").addEventListener("click", () => setOpen(false));
  backdrop.addEventListener("click", () => setOpen(false));
  mobile.addEventListener("change", () => {
    app.classList.remove("sb-open");
    backdrop.hidden = true;
    if (!mobile.matches && store.getPrefs().sidebarCollapsed) app.classList.add("sb-collapsed");
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && app.classList.contains("sb-open") && !document.querySelector("dialog[open]")) setOpen(false);
  });

  const newBtns = [$("#new-chat"), $("#tb-new")];
  newBtns.forEach((b) =>
    b.addEventListener("click", () => {
      if (mobile.matches) setOpen(false);
      onNew();
    }),
  );

  /* ---------- filters ---------- */
  $$("#sb-filters .sb-chip").forEach((chip) =>
    chip.addEventListener("click", () => {
      filter = chip.dataset.filter;
      $$("#sb-filters .sb-chip").forEach((c) => c.setAttribute("aria-pressed", String(c === chip)));
      render();
    }),
  );
  search.addEventListener("input", () => {
    query = search.value.trim().toLowerCase();
    render();
  });

  /* ---------- rendering ---------- */
  function matches(chat) {
    if (filter !== "all" && chat.type !== filter) return false;
    if (!query) return true;
    if (chat.title.toLowerCase().includes(query)) return true;
    return chat.messages.some((m) => typeof m.content === "string" && m.content.toLowerCase().includes(query));
  }

  function groupOf(ts) {
    const d = new Date(ts);
    const now = new Date();
    const start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    if (ts >= start) return "Сегодня";
    if (ts >= start - 86400000) return "Вчера";
    if (ts >= start - 6 * 86400000) return "За неделю";
    if (d.getFullYear() === now.getFullYear()) return d.toLocaleDateString("ru-RU", { month: "long" }).replace(/^./, (c) => c.toUpperCase());
    return "Ранее";
  }

  function render(nextActive) {
    if (nextActive !== undefined) activeId = nextActive;
    closeMenu();
    const all = store.loadChats().slice().sort((a, b) => b.updatedAt - a.updatedAt);
    const chats = all.filter(matches);
    list.replaceChildren();
    if (!chats.length) {
      list.append(
        h(
          "p",
          { class: "sb-empty" },
          all.length ? "Ничего не нашлось. Попробуйте другой запрос или фильтр." : "Здесь появится история ваших чатов. Она хранится только в этом браузере.",
        ),
      );
      lastIds = new Set(all.map((c) => c.id));
      return;
    }
    let group = null;
    for (const chat of chats) {
      const g = groupOf(chat.updatedAt);
      if (g !== group) {
        group = g;
        list.append(h("div", { class: "sb-group-title" }, g));
      }
      list.append(item(chat, !lastIds.has(chat.id) && lastIds.size > 0));
    }
    lastIds = new Set(all.map((c) => c.id));
  }

  function item(chat, isNew) {
    const active = chat.id === activeId;
    const main = h(
      "button",
      { type: "button", class: "ci-main", "aria-current": active ? "page" : null, title: chat.title },
      h("i", { class: "type-dot", "data-type": chat.type, "aria-hidden": "true" }),
      h("span", { class: "ci-title" }, chat.title),
    );
    main.addEventListener("click", () => {
      if (mobile.matches) setOpen(false);
      onSelect(chat.id);
    });
    const more = h("button", {
      type: "button",
      class: "icon-btn sm plain ci-more",
      "aria-label": "Действия с чатом «" + chat.title + "»",
      "aria-haspopup": "menu",
      "aria-expanded": "false",
      html: icons.dots,
    });
    more.addEventListener("click", (e) => {
      e.stopPropagation();
      if (menu && menu.owner === more) closeMenu();
      else openMenu(more, chat);
    });
    return h("div", { class: "chat-item" + (active ? " is-active" : "") + (isNew ? " is-new" : ""), "data-id": chat.id }, main, more);
  }

  /* ---------- item menu ---------- */
  function openMenu(btn, chat) {
    closeMenu();
    const rename = h("button", { type: "button", class: "menu-item", role: "menuitem" }, h("span", { html: icons.edit, style: { display: "inline-flex" } }), "Переименовать");
    const del = h("button", { type: "button", class: "menu-item danger", role: "menuitem" }, h("span", { html: icons.trash, style: { display: "inline-flex" } }), "Удалить");
    const el = h("div", { class: "popover floating-menu", role: "menu", "aria-label": "Действия с чатом" }, rename, del);
    document.body.append(el);
    const r = btn.getBoundingClientRect();
    const top = Math.min(r.bottom + 6, window.innerHeight - el.offsetHeight - 8);
    el.style.top = top + "px";
    el.style.left = Math.max(8, Math.min(r.right - el.offsetWidth, window.innerWidth - el.offsetWidth - 8)) + "px";
    btn.setAttribute("aria-expanded", "true");
    rename.focus();
    rename.addEventListener("click", () => {
      closeMenu();
      startRename(chat);
    });
    del.addEventListener("click", () => {
      closeMenu();
      confirmDelete(chat);
    });
    el.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        closeMenu();
        btn.focus();
      }
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        (document.activeElement === rename ? del : rename).focus();
      }
    });
    const onDoc = (e) => {
      if (!el.contains(e.target) && e.target !== btn) closeMenu();
    };
    setTimeout(() => document.addEventListener("pointerdown", onDoc), 0);
    menu = { el, owner: btn, onDoc };
  }
  function closeMenu() {
    if (!menu) return;
    document.removeEventListener("pointerdown", menu.onDoc);
    menu.owner.setAttribute("aria-expanded", "false");
    menu.el.remove();
    menu = null;
  }
  list.addEventListener("scroll", closeMenu, { passive: true });

  function startRename(chat) {
    const row = list.querySelector(`.chat-item[data-id="${CSS.escape(chat.id)}"]`);
    if (!row) return;
    const input = h("input", { class: "input ci-rename", type: "text", "aria-label": "Новое название чата", maxlength: "120" });
    input.value = chat.title;
    row.replaceChildren(input);
    input.focus();
    input.select();
    let done = false;
    const finish = (save) => {
      if (done) return;
      done = true;
      if (save && input.value.trim()) {
        store.renameChat(chat.id, input.value);
        if (ctx.onRenamed) ctx.onRenamed(chat.id);
      }
      render();
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        finish(true);
      } else if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        finish(false);
      }
    });
    input.addEventListener("blur", () => finish(true));
  }

  function confirmDelete(chat) {
    const dlg = createDialog({ title: "Удалить чат?", subtitle: "«" + chat.title + "» исчезнет из истории этого браузера. Отменить удаление нельзя." });
    const cancel = h("button", { type: "button", class: "btn btn-ghost" }, "Отмена");
    const ok = h("button", { type: "button", class: "btn btn-danger" }, h("span", { html: icons.trash, style: { display: "inline-flex" } }), "Удалить");
    cancel.addEventListener("click", () => dlg.close());
    ok.addEventListener("click", () => {
      dlg.close();
      onDelete(chat.id);
    });
    dlg.body.append(h("div", { class: "confirm-actions" }, cancel, ok));
    setTimeout(() => cancel.focus(), 50);
  }

  store.onExternalChange(() => render());

  return {
    render,
    setActive(id) {
      activeId = id;
      $$(".chat-item", list).forEach((el) => {
        const on = el.dataset.id === id;
        el.classList.toggle("is-active", on);
        const b = el.querySelector(".ci-main");
        if (b) on ? b.setAttribute("aria-current", "page") : b.removeAttribute("aria-current");
      });
    },
    close: () => setOpen(false),
  };
}
