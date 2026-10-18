// Landing page controller.

import { getCatalog, getMe, botLink } from "./api.js";
import { h, $, $$, icons, toast, copyWithFeedback, reducedMotion, TYPE_LABELS, REASONING_LABELS } from "./utils.js";
import { createModelPicker, createMenuPill } from "./pickers.js";
import { openLoginModal, openPaymentModal } from "./modals.js";
import { setDraft, getPrefs } from "./store.js";
import * as fx from "./fx.js";

const state = {
  catalog: null,
  me: null,
  type: "text",
  vendor: null,
  query: "",
  highlight: null,
  galaxy: null,
  picker: null,
  reasoning: null,
};

const RM = reducedMotion();

init();

async function init() {
  document.getElementById("year").textContent = String(new Date().getFullYear());
  setupHeader();
  startHero3D();
  fx.setup();
  fx.heroIntro();
  fx.reveal();
  fx.tilt();
  fx.waveform(document.getElementById("fx-wave"));
  fx.typing(document.getElementById("fx-code"));
  fx.stepsLine();

  refreshMe();
  const catalog = await getCatalog();
  state.catalog = catalog;

  renderCounts(catalog);
  renderMarquee(catalog);
  setupHeroPrompt(catalog);
  setupModels(catalog);
  renderPricing(catalog);
  renderFaq(catalog);
  applyCatalogLinks(catalog);
  fx.modelSwitcher(
    document.getElementById("fx-switch-name"),
    document.getElementById("fx-switch-answer"),
    ["Claude Opus 5.5", "GPT-6 Sol", "Gemini 3.1 Pro", "Grok 4.7", "DeepSeek V4.1 Flash", "Qwen3.8 Max Prime"].filter((n) => catalog.models.some((m) => m.name === n)),
  );

  fx.counters();
  fx.reveal();
  fx.tilt();
  setupGalaxyLazy(catalog);
  requestAnimationFrame(() => fx.refresh());
  window.addEventListener("load", () => fx.refresh());
}

/* ---------------- header & auth ---------------- */

function setupHeader() {
  const header = document.getElementById("site-header");
  const nav = document.getElementById("site-nav");
  const burger = document.getElementById("burger");

  const onScroll = () => header.classList.toggle("is-scrolled", window.scrollY > 12);
  onScroll();
  window.addEventListener("scroll", onScroll, { passive: true });

  const setOpen = (open) => {
    nav.classList.toggle("is-open", open);
    header.classList.toggle("is-open", open);
    burger.setAttribute("aria-expanded", String(open));
  };
  burger.addEventListener("click", () => setOpen(!nav.classList.contains("is-open")));
  nav.addEventListener("click", (e) => {
    if (e.target.closest("a, button")) setOpen(false);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && nav.classList.contains("is-open")) {
      setOpen(false);
      burger.focus();
    }
  });
  window.matchMedia("(min-width: 921px)").addEventListener("change", (e) => e.matches && setOpen(false));

  $$('[data-action="login"]').forEach((b) => b.addEventListener("click", () => login()));

  // highlight the nav item of the section in view
  if ("IntersectionObserver" in window) {
    const links = $$(".nav a[href^='#']");
    const map = new Map(links.map((a) => [a.getAttribute("href").slice(1), a]));
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((en) => {
          const a = map.get(en.target.id);
          if (a && en.isIntersecting) {
            links.forEach((l) => l.classList.remove("is-current"));
            a.classList.add("is-current");
          }
        });
      },
      { rootMargin: "-45% 0px -50% 0px" },
    );
    map.forEach((_, id) => {
      const sec = document.getElementById(id);
      if (sec) io.observe(sec);
    });
  }
}

async function refreshMe() {
  try {
    state.me = await getMe();
  } catch {
    state.me = null;
  }
  renderUser();
  return state.me;
}

function renderUser() {
  const me = state.me;
  const guest = document.getElementById("guest-actions");
  const user = document.getElementById("user-actions");
  const navLogin = $(".nav .nav-login");
  if (me && me.user) {
    const name = me.user.first_name || (me.user.username ? "@" + me.user.username : "Профиль");
    document.getElementById("user-name").textContent = name;
    document.getElementById("user-avatar").textContent = (name.replace("@", "")[0] || "U").toUpperCase();
    guest.hidden = true;
    user.hidden = false;
    if (navLogin) navLogin.hidden = true;
  } else {
    guest.hidden = false;
    user.hidden = true;
    if (navLogin) navLogin.hidden = false;
  }
}

function login(after) {
  openLoginModal({
    onSuccess: async () => {
      await refreshMe();
      if (after) after();
    },
  });
}

/* ---------------- hero ---------------- */

function startHero3D() {
  const hero = document.getElementById("top");
  const canvas = document.getElementById("hero-canvas");
  if (!canvas) return;
  import("./hero3d.js")
    .then((m) => {
      m.initHero(canvas, { hero, reducedMotion: RM });
      hero.classList.add("has-webgl");
    })
    .catch(() => {
      hero.classList.add("no-webgl");
      canvas.remove();
    });
}

function setupHeroPrompt(catalog) {
  const form = document.getElementById("hero-prompt");
  const ta = document.getElementById("hero-text");
  const tools = document.getElementById("hero-tools");
  const placeholder = document.getElementById("hero-model-placeholder");
  const prefs = getPrefs();
  const initial = catalog.models.find((m) => m.id === prefs.model) || catalog.models.find((m) => m.id === "claude-opus-5-5") || catalog.models[0];

  const reasoningPill = createMenuPill({
    label: "",
    icon: "brain",
    title: "Уровень рассуждения",
    options: [],
    value: null,
    onChange: (v) => (state.reasoning = v),
  });

  const syncReasoning = (model) => {
    if (model && model.type === "text" && model.reasoning && model.reasoning.length) {
      const def = model.default_reasoning && model.reasoning.includes(model.default_reasoning) ? model.default_reasoning : model.reasoning[0];
      const keep = state.reasoning && model.reasoning.includes(state.reasoning) ? state.reasoning : def;
      reasoningPill.setOptions(
        model.reasoning.map((r) => ({ value: r, label: REASONING_LABELS[r] || r, short: REASONING_LABELS[r] || r, hint: r === def ? "по умолчанию" : "" })),
        keep,
      );
      state.reasoning = keep;
      reasoningPill.root.hidden = false;
    } else {
      state.reasoning = null;
      reasoningPill.root.hidden = true;
    }
    ta.placeholder =
      model && model.type === "image"
        ? "Опишите изображение, которое нужно создать…"
        : model && model.type === "video"
          ? "Опишите сцену для видео…"
          : "Спросите что угодно или опишите картинку…";
  };

  const picker = createModelPicker({
    models: catalog.models,
    value: initial && initial.id,
    onChange: (m) => syncReasoning(m),
  });
  state.picker = picker;
  placeholder.replaceWith(picker.root);
  tools.append(reasoningPill.root);
  syncReasoning(picker.value);

  const autogrow = () => {
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 220) + "px";
  };
  ta.addEventListener("input", autogrow);
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      form.requestSubmit ? form.requestSubmit() : form.dispatchEvent(new Event("submit", { cancelable: true }));
    }
  });

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = ta.value.trim();
    if (!text) {
      form.classList.remove("is-shake");
      void form.offsetWidth;
      form.classList.add("is-shake");
      ta.focus();
      return;
    }
    const model = picker.value;
    const ok = setDraft({ model: model ? model.id : null, text, reasoning: state.reasoning || undefined });
    if (!ok) toast("Браузер не дал сохранить черновик, текст придётся вставить в чате заново", { type: "err" });
    window.location.href = "/app";
  });

  document.getElementById("hero-attach").addEventListener("click", () => {
    toast("Файлы и скриншоты прикрепляются в чате: отправьте запрос, и продолжим там");
    ta.focus();
  });

  $$("#hero-chips .chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      ta.value = chip.dataset.text || chip.textContent.trim();
      if (chip.dataset.model && picker.setValue(chip.dataset.model)) syncReasoning(picker.value);
      autogrow();
      ta.focus();
      ta.setSelectionRange(ta.value.length, ta.value.length);
    });
  });
}

/* ---------------- counts & marquee ---------------- */

function renderCounts(catalog) {
  const total = catalog.models.length;
  const by = { text: 0, image: 0, video: 0 };
  catalog.models.forEach((m) => (by[m.type] += 1));
  $$("[data-total-models]").forEach((el) => {
    el.textContent = String(total);
    if (el.hasAttribute("data-count")) el.dataset.count = String(total);
  });
  $$("[data-count-type]").forEach((el) => {
    const n = by[el.dataset.countType] || 0;
    el.textContent = String(n);
    el.dataset.count = String(n);
  });
  $$("[data-tab-count]").forEach((el) => (el.textContent = String(by[el.dataset.tabCount] || 0)));
  $$("[data-legend-count]").forEach((el) => (el.textContent = String(by[el.dataset.legendCount] || 0)));
  $$("[data-sub-days]").forEach((el) => (el.textContent = String(catalog.subscription_days)));
}

function renderMarquee(catalog) {
  const half = Math.ceil(catalog.models.length / 2);
  const rows = [catalog.models.slice(0, half), catalog.models.slice(half)];
  ["marquee-a", "marquee-b"].forEach((id, i) => {
    const row = document.getElementById(id);
    const list = rows[i].length ? rows[i] : catalog.models;
    const group = () =>
      h(
        "div",
        { class: "marquee-group" },
        list.map((m) => h("span", { class: "marquee-item" }, h("i", { class: "type-dot", "data-type": m.type }), m.name, h("small", {}, m.vendor))),
      );
    const g2 = group();
    g2.setAttribute("aria-hidden", "true");
    row.replaceChildren(group(), g2);
  });
}

/* ---------------- models ---------------- */

function setupModels(catalog) {
  const tabs = $$("#model-tabs .tab");
  const ind = $("#model-tabs .tab-ind");
  const tablist = document.getElementById("model-tabs");
  const search = document.getElementById("models-q");

  const moveInd = () => {
    const active = tabs.find((t) => t.dataset.type === state.type);
    if (!active || !ind) return;
    ind.style.width = active.offsetWidth + "px";
    ind.style.transform = `translateX(${active.offsetLeft}px)`;
  };
  state.moveInd = moveInd;

  tabs.forEach((tab, i) => {
    tab.addEventListener("click", () => setType(tab.dataset.type, { clearVendor: true }));
    tab.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft" && e.key !== "Home" && e.key !== "End") return;
      e.preventDefault();
      let j = i;
      if (e.key === "ArrowRight") j = (i + 1) % tabs.length;
      if (e.key === "ArrowLeft") j = (i - 1 + tabs.length) % tabs.length;
      if (e.key === "Home") j = 0;
      if (e.key === "End") j = tabs.length - 1;
      tabs[j].focus();
      setType(tabs[j].dataset.type, { clearVendor: true });
    });
  });
  search.addEventListener("input", () => {
    state.query = search.value.trim().toLowerCase();
    renderGrid({ animate: false });
  });

  $$("#galaxy-legend .legend-btn").forEach((b) =>
    b.addEventListener("click", () => {
      const same = state.galaxyFocus === b.dataset.type;
      setType(b.dataset.type, { clearVendor: true, focus: same ? null : b.dataset.type });
    }),
  );
  $$("#galaxy .seg button").forEach((b) =>
    b.addEventListener("click", () => {
      $$("#galaxy .seg button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      if (state.galaxy) state.galaxy.setMode(b.dataset.mode);
    }),
  );

  window.addEventListener("resize", moveInd, { passive: true });
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(moveInd);
  tablist.classList.add("is-ready");
  syncTabs();
  moveInd();
  renderGrid({ animate: false });
}

function syncTabs() {
  $$("#model-tabs .tab").forEach((t) => {
    const on = t.dataset.type === state.type;
    t.setAttribute("aria-selected", String(on));
    t.tabIndex = on ? 0 : -1;
  });
  const panel = document.getElementById("models-panel");
  panel.setAttribute("aria-labelledby", "tab-" + state.type);
  if (state.moveInd) state.moveInd();
}

function setType(type, { clearVendor = false, focus } = {}) {
  const changed = type !== state.type;
  state.type = type;
  if (clearVendor) {
    state.vendor = null;
    state.highlight = null;
    if (state.galaxy) state.galaxy.select(null);
  }
  state.galaxyFocus = focus === undefined ? type : focus;
  $$("#galaxy-legend .legend-btn").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.type === state.galaxyFocus)));
  if (state.galaxy) state.galaxy.setFocus(state.galaxyFocus);
  syncTabs();
  renderGrid({ animate: changed || clearVendor });
}

let gridTimer = 0;
function renderGrid({ animate = true } = {}) {
  const panel = document.getElementById("models-panel");
  const draw = () => {
    const { catalog } = state;
    const q = state.query;
    const list = catalog.models.filter(
      (m) =>
        m.type === state.type &&
        (!state.vendor || m.vendor === state.vendor) &&
        (!q || m.name.toLowerCase().includes(q) || m.id.toLowerCase().includes(q) || m.vendor.toLowerCase().includes(q)),
    );
    renderVendorFilter();
    if (!list.length) {
      panel.replaceChildren(h("div", { class: "models-empty" }, q ? "По запросу «" + q + "» моделей нет." : "Моделей нет."));
    } else {
      panel.replaceChildren(...list.map((m, i) => modelCard(m, i)));
    }
    panel.classList.remove("is-leaving");
    if (state.highlight) {
      const card = panel.querySelector(`[data-id="${CSS.escape(state.highlight)}"]`);
      if (card) {
        card.classList.add("is-flash");
        setTimeout(() => card.classList.remove("is-flash"), 2200);
      }
    }
  };
  clearTimeout(gridTimer);
  if (animate && !RM) {
    panel.classList.add("is-leaving");
    gridTimer = setTimeout(draw, 180);
  } else draw();
}

function modelCard(m, i) {
  const copyBtn = h(
    "button",
    { type: "button", class: "copy-id", title: "Скопировать ID", "aria-label": "Скопировать ID " + m.id },
    h("span", { "data-label": m.id }, m.id),
    h("span", { html: icons.copy, style: { display: "inline-flex" } }),
  );
  copyBtn.addEventListener("click", () => copyWithFeedback(copyBtn, m.id, { label: m.id }));
  let meta = "";
  if (m.type === "text" && m.reasoning && m.reasoning.length) {
    meta = "Рассуждение: " + m.reasoning[0] + " → " + m.reasoning[m.reasoning.length - 1];
  } else if (m.type === "image") meta = "Генерация и правка изображений";
  else if (m.type === "video") meta = "Генерация видео";
  return h(
    "article",
    { class: "model-card", "data-id": m.id, style: { "--i": String(Math.min(i, 30)) } },
    h("div", { class: "mc-top" }, h("span", { class: "mc-vendor" }, m.vendor), h("i", { class: "type-dot", "data-type": m.type, title: TYPE_LABELS[m.type] })),
    h("h3", { class: "mc-name" }, m.name),
    copyBtn,
    meta ? h("div", { class: "mc-meta" }, meta) : null,
  );
}

function renderVendorFilter() {
  const box = document.getElementById("vendor-filter");
  if (!state.vendor) {
    box.hidden = true;
    box.replaceChildren();
    return;
  }
  const chip = h(
    "button",
    { type: "button", class: "chip", "aria-label": "Сбросить фильтр по вендору " + state.vendor },
    state.vendor,
    h("span", { html: icons.x, style: { display: "inline-flex" } }),
  );
  chip.addEventListener("click", () => {
    state.vendor = null;
    state.highlight = null;
    if (state.galaxy) state.galaxy.select(null);
    renderGrid();
  });
  box.replaceChildren(h("span", {}, "Вендор:"), chip);
  box.hidden = false;
}

function focusGrid() {
  const browser = document.getElementById("models-browser");
  const r = browser.getBoundingClientRect();
  if (r.top > window.innerHeight * 0.7 || r.bottom < 0) browser.scrollIntoView({ behavior: RM ? "auto" : "smooth", block: "start" });
}

function setupGalaxyLazy(catalog) {
  const stage = document.getElementById("galaxy-stage");
  const note = stage.querySelector(".gf-note");
  let started = false;
  const boot = () => {
    if (started) return;
    started = true;
    import("./galaxy3d.js")
      .then((m) => {
        state.galaxy = m.initGalaxy(stage, {
          models: catalog.models,
          reducedMotion: RM,
          onSelect: (model) => {
            state.type = model.type;
            state.vendor = model.vendor;
            state.highlight = model.id;
            state.galaxyFocus = null;
            state.galaxy.setFocus(null);
            $$("#galaxy-legend .legend-btn").forEach((b) => b.setAttribute("aria-pressed", "false"));
            syncTabs();
            renderGrid();
            setTimeout(focusGrid, 250);
          },
          onTypeClick: (type) => setType(type, { clearVendor: true }),
          onVendorClick: (vendor) => {
            const first = catalog.models.find((x) => x.vendor === vendor && x.type === state.type) || catalog.models.find((x) => x.vendor === vendor);
            state.vendor = vendor;
            state.highlight = null;
            state.galaxyFocus = null;
            state.galaxy.setFocus(null);
            if (first) state.type = first.type;
            syncTabs();
            renderGrid();
            setTimeout(focusGrid, 250);
          },
        });
        state.galaxy.setFocus(state.galaxyFocus || null);
      })
      .catch(() => {
        if (note) note.textContent = "3D-сцена недоступна в этом браузере. Список моделей ниже работает как обычно.";
        stage.classList.add("is-fallback");
      });
  };
  if ("IntersectionObserver" in window) {
    const io = new IntersectionObserver(
      ([en]) => {
        if (en.isIntersecting) {
          io.disconnect();
          boot();
        }
      },
      { rootMargin: "400px 0px" },
    );
    io.observe(stage);
  } else boot();
}

/* ---------------- pricing ---------------- */

function renderPricing(catalog) {
  const grid = document.getElementById("pricing-grid");
  const days = catalog.subscription_days;
  grid.replaceChildren(
    ...catalog.tariffs.map((t) => {
      const buy = h("button", { type: "button", class: "btn btn-lg btn-block " + (t.popular ? "btn-accent" : "btn-light") }, "Купить");
      buy.addEventListener("click", () => buyTariff(t));
      return h(
        "article",
        { class: "price-card" + (t.popular ? " glow-border is-popular" : ""), "data-tilt": "", "data-tilt-max": "5" },
        t.popular ? h("span", { class: "pc-badge" }, "Популярный") : null,
        h("div", { class: "pc-head" }, h("span", { class: "pc-emoji", "aria-hidden": "true" }, t.emoji || "💎"), h("h3", {}, t.title)),
        h("p", { class: "pc-tagline" }, t.tagline || ""),
        h("div", { class: "pc-price" }, h("b", {}, "$" + t.price_usd), h("span", {}, "/ " + days + " дней")),
        t.stars ? h("div", { class: "pc-stars" }, "или " + Number(t.stars).toLocaleString("ru-RU") + " ⭐ в Telegram") : null,
        h(
          "ul",
          { class: "pc-features" },
          (t.features || []).map((f) => h("li", {}, h("span", { html: icons.check, style: { display: "inline-flex" } }), f)),
        ),
        buy,
      );
    }),
  );
  grid.setAttribute("data-reveal-stagger", "");
}

async function buyTariff(tariff) {
  const open = () =>
    openPaymentModal({
      tariff,
      catalog: state.catalog,
      onPaid: refreshMe,
      onUnauthorized: () => login(open),
    });
  if (!state.me) await refreshMe();
  if (!state.me) login(open);
  else open();
}

/* ---------------- FAQ ---------------- */

function renderFaq(catalog) {
  const box = document.getElementById("faq-list");
  box.replaceChildren(
    ...catalog.faq.map((item) => {
      const d = h(
        "details",
        { class: "faq-item" },
        h("summary", {}, h("span", {}, item.q), h("span", { class: "faq-ico", "aria-hidden": "true" })),
        h("div", { class: "faq-body" }, h("p", {}, item.a)),
      );
      fx.accordion(d);
      return d;
    }),
  );
  box.setAttribute("data-reveal-stagger", "");
}

/* ---------------- links ---------------- */

function applyCatalogLinks(catalog) {
  $$("[data-bot-link]").forEach((a) => (a.href = botLink(catalog)));
  const base = document.getElementById("api-base");
  if (base) base.textContent = catalog.api_base_url;
}
