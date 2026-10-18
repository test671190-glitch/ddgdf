// Reusable controls: searchable model picker, option menu pills and toggle pills.

import { h, icons, TYPE_LABELS } from "./utils.js";

const openPopovers = new Set();

function closeAllExcept(keep) {
  for (const p of [...openPopovers]) if (p !== keep) p.close();
}

document.addEventListener("pointerdown", (e) => {
  for (const p of [...openPopovers]) {
    if (!p.root.contains(e.target)) p.close();
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && openPopovers.size) {
    for (const p of [...openPopovers]) p.close(true);
  }
});

function place(pop, btn, { minHeight = 320, width = 340, preferUp = false } = {}) {
  const r = btn.getBoundingClientRect();
  const below = window.innerHeight - r.bottom;
  const above = r.top;
  pop.classList.toggle("up", preferUp ? above > 200 || above > below : below < minHeight && above > below);
  pop.classList.toggle("align-right", r.left + Math.min(width, window.innerWidth - 32) > window.innerWidth - 8);
}

/**
 * Searchable model picker grouped by type.
 * @param {{models: Array, value?: string, onChange?: Function, types?: string[], preferUp?: boolean, compact?: boolean}} opts
 */
export function createModelPicker({ models, value, onChange, types = ["text", "image", "video"], preferUp = false, compact = false }) {
  let list = models.filter((m) => types.includes(m.type));
  let current = list.find((m) => m.id === value) || list[0] || null;
  let activeIdx = 0;
  let visible = [];

  const btn = h("button", {
    type: "button",
    class: "pill mp-btn",
    "aria-haspopup": "listbox",
    "aria-expanded": "false",
    title: "Выбрать модель",
  });
  const search = h("input", {
    class: "input",
    type: "search",
    placeholder: "Поиск модели или вендора",
    "aria-label": "Поиск модели",
    autocomplete: "off",
    spellcheck: "false",
  });
  const listEl = h("div", { class: "mp-list", role: "listbox", "aria-label": "Модели" });
  const pop = h("div", { class: "popover mp-pop sheet", hidden: true }, h("div", { class: "mp-search" }, search), listEl);
  const root = h("div", { class: "popover-anchor mp" }, btn, pop);

  function renderButton() {
    btn.replaceChildren();
    if (!current) {
      btn.append(h("span", { class: "pill-value" }, "Модель"));
      return;
    }
    btn.append(
      h("span", { class: "type-dot", "data-type": current.type, "aria-hidden": "true" }),
      h("span", { class: "pill-value" }, current.name),
      h("span", { html: icons.caret, "aria-hidden": "true", style: { display: "inline-flex" } }),
    );
    btn.setAttribute("aria-label", "Модель: " + current.name + ". Изменить");
    if (compact) btn.classList.add("is-compact");
  }

  function renderList() {
    const q = search.value.trim().toLowerCase();
    visible = list.filter((m) => !q || m.name.toLowerCase().includes(q) || m.id.toLowerCase().includes(q) || m.vendor.toLowerCase().includes(q));
    listEl.replaceChildren();
    if (!visible.length) {
      listEl.append(h("div", { class: "mp-empty" }, "Ничего не нашлось"));
      return;
    }
    let i = 0;
    for (const type of types) {
      const group = visible.filter((m) => m.type === type);
      if (!group.length) continue;
      const gid = "mpg-" + type + "-" + Math.random().toString(36).slice(2, 7);
      const groupEl = h("div", { role: "group", "aria-labelledby": gid });
      groupEl.append(
        h("div", { class: "mp-group-title", id: gid }, h("span", { class: "type-dot", "data-type": type }), TYPE_LABELS[type] + " · " + group.length),
      );
      for (const m of group) {
        const idx = i++;
        const item = h(
          "div",
          {
            class: "menu-item mp-item" + (idx === activeIdx ? " is-active" : ""),
            role: "option",
            "aria-selected": current && m.id === current.id ? "true" : "false",
            "data-idx": idx,
            id: "mpo-" + m.id.replace(/[^a-z0-9-]/gi, "_"),
          },
          h("span", { class: "mp-name" }, m.name),
          h("span", { class: "mp-vendor" }, m.vendor),
          h("span", { html: icons.checkSmall, style: { display: "contents" } }),
        );
        item.addEventListener("click", () => select(m));
        item.addEventListener("pointermove", () => setActive(idx, false));
        groupEl.append(item);
      }
      listEl.append(groupEl);
    }
    // keep visible[] order aligned with rendered order
    visible = types.flatMap((t) => visible.filter((m) => m.type === t));
  }

  function setActive(idx, scroll = true) {
    activeIdx = Math.max(0, Math.min(visible.length - 1, idx));
    listEl.querySelectorAll(".mp-item").forEach((el) => el.classList.toggle("is-active", Number(el.dataset.idx) === activeIdx));
    const el = listEl.querySelector(`.mp-item[data-idx="${activeIdx}"]`);
    if (el) {
      search.setAttribute("aria-activedescendant", el.id);
      if (scroll) el.scrollIntoView({ block: "nearest" });
    }
  }

  function select(m) {
    const changed = !current || current.id !== m.id;
    current = m;
    renderButton();
    api.close(true);
    if (changed && onChange) onChange(m);
  }

  const api = {
    root,
    el: root,
    open() {
      closeAllExcept(api);
      search.value = "";
      activeIdx = Math.max(0, list.indexOf(current));
      renderList();
      // re-map active index to rendered order
      activeIdx = Math.max(0, visible.indexOf(current));
      setActive(activeIdx);
      pop.hidden = false;
      place(pop, btn, { preferUp });
      btn.setAttribute("aria-expanded", "true");
      openPopovers.add(api);
      requestAnimationFrame(() => {
        if (window.matchMedia("(hover: hover)").matches) search.focus({ preventScroll: true });
        const sel = listEl.querySelector('[aria-selected="true"]');
        if (sel) sel.scrollIntoView({ block: "center" });
      });
    },
    close(focusBtn = false) {
      if (pop.hidden) return;
      pop.hidden = true;
      btn.setAttribute("aria-expanded", "false");
      openPopovers.delete(api);
      if (focusBtn) btn.focus({ preventScroll: true });
    },
    get value() {
      return current;
    },
    setValue(id, { silent = true } = {}) {
      const m = list.find((x) => x.id === id);
      if (!m) return false;
      current = m;
      renderButton();
      if (!silent && onChange) onChange(m);
      return true;
    },
    setModels(next) {
      list = next.filter((m) => types.includes(m.type));
      current = list.find((m) => current && m.id === current.id) || list[0] || null;
      renderButton();
    },
  };

  btn.addEventListener("click", () => (pop.hidden ? api.open() : api.close()));
  btn.addEventListener("keydown", (e) => {
    if ((e.key === "ArrowDown" || e.key === "ArrowUp") && pop.hidden) {
      e.preventDefault();
      api.open();
    }
  });
  search.addEventListener("input", () => {
    activeIdx = 0;
    renderList();
    setActive(0);
  });
  search.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive(activeIdx + 1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive(activeIdx - 1);
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (visible[activeIdx]) select(visible[activeIdx]);
    } else if (e.key === "Tab") {
      api.close();
    }
  });

  renderButton();
  return api;
}

/**
 * Pill that opens a small radio menu.
 * options: [{value, label, hint?}]
 */
export function createMenuPill({ label, icon, options, value, onChange, title, preferUp = false, valueLabel }) {
  let opts = options.slice();
  let current = opts.find((o) => o.value === value) ? value : opts[0]?.value;
  const btn = h("button", { type: "button", class: "pill", "aria-haspopup": "menu", "aria-expanded": "false", title: title || label });
  const menu = h("div", { class: "popover", role: "menu", hidden: true, "aria-label": label });
  const root = h("div", { class: "popover-anchor" }, btn, menu);

  const labelFor = (v) => {
    const o = opts.find((x) => x.value === v);
    return valueLabel ? valueLabel(o) : o ? o.short || o.label : "";
  };

  function renderBtn() {
    btn.replaceChildren();
    if (icon) btn.append(h("span", { html: icons[icon], "aria-hidden": "true", style: { display: "inline-flex" } }));
    if (label) btn.append(h("span", { class: "pill-label" }, label));
    btn.append(h("span", { class: "pill-value" }, labelFor(current)));
    btn.append(h("span", { html: icons.caret, "aria-hidden": "true", style: { display: "inline-flex" } }));
    btn.setAttribute("aria-label", (label || title || "") + ": " + labelFor(current));
  }

  function renderMenu() {
    menu.replaceChildren();
    if (title) menu.append(h("div", { class: "menu-title" }, title));
    opts.forEach((o) => {
      const item = h(
        "button",
        { type: "button", class: "menu-item", role: "menuitemradio", "aria-checked": o.value === current ? "true" : "false" },
        h("span", {}, o.label),
        o.hint ? h("span", { style: { color: "var(--muted)", fontSize: "12px" } }, o.hint) : null,
        h("span", { html: icons.checkSmall, style: { display: "contents" } }),
      );
      item.addEventListener("click", () => {
        const changed = current !== o.value;
        current = o.value;
        renderBtn();
        api.close(true);
        if (changed && onChange) onChange(o.value);
      });
      menu.append(item);
    });
  }

  function focusItem(delta) {
    const items = [...menu.querySelectorAll(".menu-item")];
    if (!items.length) return;
    const i = items.indexOf(document.activeElement);
    const next = items[(i + delta + items.length) % items.length];
    next.focus();
  }

  const api = {
    root,
    el: root,
    open() {
      closeAllExcept(api);
      renderMenu();
      menu.hidden = false;
      place(menu, btn, { minHeight: 280, width: 240, preferUp });
      btn.setAttribute("aria-expanded", "true");
      openPopovers.add(api);
      const sel = menu.querySelector('[aria-checked="true"]') || menu.querySelector(".menu-item");
      if (sel) sel.focus({ preventScroll: true });
    },
    close(focusBtn = false) {
      if (menu.hidden) return;
      menu.hidden = true;
      btn.setAttribute("aria-expanded", "false");
      openPopovers.delete(api);
      if (focusBtn) btn.focus({ preventScroll: true });
    },
    get value() {
      return current;
    },
    setValue(v) {
      if (opts.some((o) => o.value === v)) {
        current = v;
        renderBtn();
      }
    },
    setOptions(next, v) {
      opts = next.slice();
      current = opts.some((o) => o.value === v) ? v : opts.some((o) => o.value === current) ? current : opts[0]?.value;
      renderBtn();
    },
  };

  btn.addEventListener("click", () => (menu.hidden ? api.open() : api.close()));
  menu.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      focusItem(1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      focusItem(-1);
    } else if (e.key === "Tab") api.close();
  });
  renderBtn();
  return api;
}

/** On/off pill (aria-pressed). */
export function createTogglePill({ label, icon, pressed = false, onChange, title }) {
  let state = !!pressed;
  const btn = h(
    "button",
    { type: "button", class: "pill", "aria-pressed": String(state), title: title || label },
    icon ? h("span", { html: icons[icon], "aria-hidden": "true", style: { display: "inline-flex" } }) : null,
    h("span", {}, label),
  );
  btn.addEventListener("click", () => {
    state = !state;
    btn.setAttribute("aria-pressed", String(state));
    if (onChange) onChange(state);
  });
  return {
    root: btn,
    el: btn,
    get value() {
      return state;
    },
    setValue(v) {
      state = !!v;
      btn.setAttribute("aria-pressed", String(state));
    },
  };
}
