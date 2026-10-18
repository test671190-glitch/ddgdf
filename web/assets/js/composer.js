// Composer: auto-growing textarea, model picker, per-type options and attachments (button, drag & drop, paste).

import * as store from "./store.js";
import { h, $, icons, toast, REASONING_LABELS } from "./utils.js";
import { createModelPicker, createMenuPill, createTogglePill } from "./pickers.js";

const MAX_IMAGE = 10 * 1024 * 1024;
const MAX_TEXT = 1024 * 1024;
const MAX_ATTACH = 10;
const TEXT_EXT = /\.(txt|md|markdown|csv|tsv|json|jsonl|ya?ml|xml|html?|css|scss|js|mjs|cjs|ts|tsx|jsx|vue|svelte|py|rb|go|rs|java|kt|swift|c|cc|cpp|h|hpp|cs|php|sh|bash|zsh|ps1|bat|sql|toml|ini|cfg|conf|env|log|tex|srt|vtt)$/i;

const IMAGE_ASPECTS = ["auto", "1:1", "4:5", "9:16", "16:9", "21:9"];
const IMAGE_RES = ["1K", "2K", "4K"];
const VIDEO_DURATIONS = [5, 8, 10];
const VIDEO_ASPECTS = ["16:9", "9:16", "1:1"];
const VIDEO_RES = ["720p", "1080p", "4K"];

const PLACEHOLDERS = {
  text: "Спросите что угодно…",
  image: "Опишите изображение, которое нужно создать…",
  video: "Опишите сцену для видео…",
};

function readAs(file, how) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result);
    r.onerror = () => reject(r.error);
    if (how === "text") r.readAsText(file);
    else r.readAsDataURL(file);
  });
}

export function initComposer(ctx, { onSubmit, onStop }) {
  const form = $("#composer");
  const box = $("#composer-box");
  const ta = $("#prompt");
  const tools = $("#composer-tools");
  const sendBtn = $("#send-btn");
  const attList = $("#att-list");
  const fileInput = $("#file-input");
  const attachBtn = $("#attach-btn");
  const main = $("#main");
  const overlay = $("#drop-overlay");

  const prefs = store.getPrefs();
  const models = ctx.catalog.models;
  let model = ctx.models.get(prefs.model) || ctx.models.get("claude-opus-5-5") || models.find((m) => m.type === "text") || models[0];
  let attachments = [];
  let busy = false;

  const opts = {
    reasoning: null,
    webSearch: !!prefs.webSearch,
    image: {
      aspect_ratio: IMAGE_ASPECTS.includes(prefs.imgAspect) ? prefs.imgAspect : "auto",
      resolution: IMAGE_RES.includes(prefs.imgRes) ? prefs.imgRes : "2K",
      n: [1, 2, 3, 4].includes(prefs.imgN) ? prefs.imgN : 1,
    },
    video: {
      duration: VIDEO_DURATIONS.includes(prefs.vidDuration) ? prefs.vidDuration : 5,
      aspect_ratio: VIDEO_ASPECTS.includes(prefs.vidAspect) ? prefs.vidAspect : "16:9",
      resolution: VIDEO_RES.includes(prefs.vidRes) ? prefs.vidRes : "1080p",
      generate_audio: prefs.vidAudio === undefined ? true : !!prefs.vidAudio,
    },
  };

  /* ---------- model picker + options ---------- */
  const picker = createModelPicker({
    models,
    value: model.id,
    preferUp: true,
    onChange: (m) => {
      model = m;
      opts.reasoning = null;
      store.setPrefs({ model: m.id });
      buildOptions();
      if (ctx.onModelChange) ctx.onModelChange(m);
    },
  });
  const optsBox = h("div", { class: "composer-opts" });
  tools.append(picker.root, optsBox);

  function buildOptions() {
    optsBox.replaceChildren();
    ta.placeholder = PLACEHOLDERS[model.type] || PLACEHOLDERS.text;
    if (model.type === "text") {
      if (model.reasoning && model.reasoning.length) {
        const def = model.default_reasoning && model.reasoning.includes(model.default_reasoning) ? model.default_reasoning : model.reasoning[0];
        if (!opts.reasoning || !model.reasoning.includes(opts.reasoning)) opts.reasoning = def;
        const pill = createMenuPill({
          label: "Рассуждение",
          icon: "brain",
          title: "Уровень рассуждения",
          preferUp: true,
          options: model.reasoning.map((r) => ({ value: r, label: REASONING_LABELS[r] || r, hint: r === def ? "по умолчанию" : "" })),
          value: opts.reasoning,
          onChange: (v) => (opts.reasoning = v),
        });
        optsBox.append(pill.root);
      } else opts.reasoning = null;
      const web = createTogglePill({
        label: "Веб-поиск",
        icon: "globe",
        pressed: opts.webSearch,
        title: "Модель поищет информацию в интернете перед ответом",
        onChange: (v) => {
          opts.webSearch = v;
          store.setPrefs({ webSearch: v });
        },
      });
      optsBox.append(web.root);
    } else if (model.type === "image") {
      optsBox.append(
        createMenuPill({
          label: "Формат",
          icon: "ratio",
          title: "Соотношение сторон",
          preferUp: true,
          options: IMAGE_ASPECTS.map((a) => ({ value: a, label: a === "auto" ? "Авто" : a })),
          value: opts.image.aspect_ratio,
          onChange: (v) => {
            opts.image.aspect_ratio = v;
            store.setPrefs({ imgAspect: v });
          },
        }).root,
        createMenuPill({
          label: "Разрешение",
          icon: "layers",
          title: "Разрешение",
          preferUp: true,
          options: IMAGE_RES.map((r) => ({ value: r, label: r })),
          value: opts.image.resolution,
          onChange: (v) => {
            opts.image.resolution = v;
            store.setPrefs({ imgRes: v });
          },
        }).root,
        createMenuPill({
          label: "Кол-во",
          icon: "grid",
          title: "Количество изображений",
          preferUp: true,
          options: [1, 2, 3, 4].map((n) => ({ value: n, label: String(n), short: "×" + n })),
          value: opts.image.n,
          onChange: (v) => {
            opts.image.n = v;
            store.setPrefs({ imgN: v });
          },
        }).root,
      );
    } else if (model.type === "video") {
      optsBox.append(
        createMenuPill({
          label: "Длительность",
          icon: "clock",
          title: "Длительность ролика",
          preferUp: true,
          options: VIDEO_DURATIONS.map((d) => ({ value: d, label: d + " секунд", short: d + " с" })),
          value: opts.video.duration,
          onChange: (v) => {
            opts.video.duration = v;
            store.setPrefs({ vidDuration: v });
          },
        }).root,
        createMenuPill({
          label: "Формат",
          icon: "ratio",
          title: "Соотношение сторон",
          preferUp: true,
          options: VIDEO_ASPECTS.map((a) => ({ value: a, label: a })),
          value: opts.video.aspect_ratio,
          onChange: (v) => {
            opts.video.aspect_ratio = v;
            store.setPrefs({ vidAspect: v });
          },
        }).root,
        createMenuPill({
          label: "Разрешение",
          icon: "layers",
          title: "Разрешение",
          preferUp: true,
          options: VIDEO_RES.map((r) => ({ value: r, label: r })),
          value: opts.video.resolution,
          onChange: (v) => {
            opts.video.resolution = v;
            store.setPrefs({ vidRes: v });
          },
        }).root,
        createTogglePill({
          label: "Звук",
          icon: "sound",
          pressed: opts.video.generate_audio,
          title: "Сгенерировать звуковую дорожку",
          onChange: (v) => {
            opts.video.generate_audio = v;
            store.setPrefs({ vidAudio: v });
          },
        }).root,
      );
    }
  }

  /* ---------- textarea ---------- */
  const autogrow = () => {
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, Math.round(window.innerHeight * 0.4)) + "px";
    updateComposerHeight();
  };
  const updateComposerHeight = () => main.style.setProperty("--composer-h", form.offsetHeight + "px");
  ta.addEventListener("input", autogrow);
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing && !e.altKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      submit();
    }
  });
  window.addEventListener("resize", updateComposerHeight, { passive: true });

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (busy) {
      onStop();
      return;
    }
    submit();
  });

  function shake() {
    box.classList.remove("is-shake");
    void box.offsetWidth;
    box.classList.add("is-shake");
  }

  function submit() {
    if (busy) return;
    const text = ta.value.trim();
    const imgs = attachments.filter((a) => a.kind === "image");
    const files = attachments.filter((a) => a.kind === "file");
    if (model.type !== "text" && !text) {
      shake();
      toast(model.type === "image" ? "Опишите, что нарисовать" : "Опишите сцену для видео");
      ta.focus();
      return;
    }
    if (!text && !attachments.length) {
      shake();
      ta.focus();
      return;
    }
    let sendAtts = attachments;
    if (model.type !== "text" && files.length) {
      toast("Для изображений и видео можно прикрепить только картинки: текстовые файлы не отправлены");
      sendAtts = imgs;
    }
    const options =
      model.type === "text"
        ? { reasoning: model.reasoning ? opts.reasoning : null, webSearch: opts.webSearch }
        : model.type === "image"
          ? { ...opts.image }
          : { ...opts.video };
    const payload = { text, attachments: sendAtts.map(({ id, ...rest }) => rest), model, options };
    const accepted = onSubmit(payload);
    if (accepted !== false) {
      ta.value = "";
      attachments = [];
      renderAttachments();
      autogrow();
    }
  }

  /* ---------- attachments ---------- */
  function renderAttachments() {
    attList.replaceChildren();
    attList.hidden = !attachments.length;
    attachments.forEach((a) => {
      const remove = h("button", { type: "button", class: "att-remove", "aria-label": "Убрать " + a.name, html: icons.x });
      remove.addEventListener("click", () => {
        attachments = attachments.filter((x) => x.id !== a.id);
        renderAttachments();
        ta.focus();
      });
      if (a.kind === "image") {
        attList.append(h("div", { class: "att", title: a.name }, h("img", { src: a.dataUrl, alt: a.name }), remove));
      } else {
        attList.append(h("div", { class: "att file", title: a.name }, h("span", { html: icons.file, style: { display: "inline-flex" } }), h("span", {}, a.name), remove));
      }
    });
    updateComposerHeight();
  }

  async function addFiles(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    for (const file of files) {
      if (attachments.length >= MAX_ATTACH) {
        toast("Можно прикрепить не больше " + MAX_ATTACH + " файлов", { type: "err" });
        break;
      }
      const name = file.name || (file.type.startsWith("image/") ? "screenshot.png" : "file");
      try {
        if (file.type.startsWith("image/")) {
          if (file.size > MAX_IMAGE) {
            toast("«" + name + "» больше 10 МБ", { type: "err" });
            continue;
          }
          const dataUrl = await readAs(file, "dataurl");
          attachments.push({ id: Math.random().toString(36).slice(2), kind: "image", name, type: file.type, size: file.size, dataUrl });
        } else if (file.type.startsWith("text/") || file.type === "application/json" || TEXT_EXT.test(name)) {
          if (file.size > MAX_TEXT) {
            toast("«" + name + "» больше 1 МБ", { type: "err" });
            continue;
          }
          const text = await readAs(file, "text");
          attachments.push({ id: Math.random().toString(36).slice(2), kind: "file", name, type: file.type || "text/plain", size: file.size, text });
        } else {
          toast("«" + name + "»: поддерживаются изображения и текстовые файлы", { type: "err" });
        }
      } catch {
        toast("Не удалось прочитать «" + name + "»", { type: "err" });
      }
    }
    renderAttachments();
  }

  attachBtn.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => {
    addFiles(fileInput.files);
    fileInput.value = "";
  });

  ta.addEventListener("paste", (e) => {
    const items = Array.from((e.clipboardData && e.clipboardData.items) || []);
    const files = items.filter((i) => i.kind === "file").map((i) => i.getAsFile()).filter(Boolean);
    if (!files.length) return;
    const hasText = Array.from(e.clipboardData.types || []).includes("text/plain");
    if (!hasText) e.preventDefault();
    addFiles(files);
  });

  let dragDepth = 0;
  const hasFiles = (e) => e.dataTransfer && Array.from(e.dataTransfer.types || []).includes("Files");
  main.addEventListener("dragenter", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    dragDepth++;
    overlay.hidden = false;
  });
  main.addEventListener("dragover", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  });
  main.addEventListener("dragleave", (e) => {
    if (!hasFiles(e)) return;
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) overlay.hidden = true;
  });
  main.addEventListener("drop", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    dragDepth = 0;
    overlay.hidden = true;
    addFiles(e.dataTransfer.files);
    ta.focus();
  });

  buildOptions();
  autogrow();
  fileInput.setAttribute("accept", "image/*,text/*,.md,.json,.csv,.yaml,.yml,.py,.js,.ts,.tsx,.jsx,.go,.rs,.java,.kt,.c,.cpp,.h,.cs,.php,.rb,.sh,.sql,.toml,.ini,.log,.xml,.html,.css");

  return {
    get model() {
      return model;
    },
    focus() {
      ta.focus({ preventScroll: true });
    },
    setText(text) {
      ta.value = text || "";
      autogrow();
      ta.focus({ preventScroll: true });
      ta.setSelectionRange(ta.value.length, ta.value.length);
    },
    setModel(id, { reasoning } = {}) {
      const m = ctx.models.get(id);
      if (!m) return false;
      model = m;
      picker.setValue(id);
      opts.reasoning = reasoning && m.reasoning && m.reasoning.includes(reasoning) ? reasoning : null;
      store.setPrefs({ model: id });
      buildOptions();
      return true;
    },
    setBusy(b) {
      busy = b;
      sendBtn.classList.toggle("is-stop", b);
      sendBtn.innerHTML = b ? icons.stop : icons.send;
      sendBtn.setAttribute("aria-label", b ? "Остановить" : "Отправить");
      sendBtn.title = b ? "Остановить" : "";
    },
    updateHeight: updateComposerHeight,
  };
}
