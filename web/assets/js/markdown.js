// Markdown rendering for model answers: marked → DOMPurify → highlight.js.
// Model output is never inserted as HTML without passing through DOMPurify.

import { esc, icons, copyWithFeedback } from "./utils.js";

let configured = false;

function setup() {
  if (configured) return;
  configured = true;
  if (window.marked && typeof window.marked.use === "function") {
    window.marked.use({ gfm: true, breaks: true });
  }
  if (window.DOMPurify && typeof window.DOMPurify.addHook === "function") {
    window.DOMPurify.addHook("afterSanitizeAttributes", (node) => {
      if (node.tagName === "A") {
        node.setAttribute("target", "_blank");
        node.setAttribute("rel", "noopener noreferrer nofollow");
      }
    });
  }
}

const PURIFY_CFG = {
  USE_PROFILES: { html: true },
  FORBID_TAGS: ["style", "form", "input", "button", "textarea", "select", "iframe", "object", "embed"],
  FORBID_ATTR: ["style", "onerror", "onload"],
};

/** Markdown → sanitized HTML string. Falls back to escaped text if libraries failed to load. */
export function renderMarkdown(md) {
  setup();
  const src = String(md ?? "");
  if (!window.marked || !window.DOMPurify) {
    return "<p>" + esc(src).replace(/\n{2,}/g, "</p><p>").replace(/\n/g, "<br>") + "</p>";
  }
  let html;
  try {
    html = window.marked.parse(src);
  } catch {
    html = "<p>" + esc(src) + "</p>";
  }
  return window.DOMPurify.sanitize(html, PURIFY_CFG);
}

/**
 * Wrap <pre><code> blocks with a header (language + copy button) and highlight them.
 * Safe to call repeatedly; already-enhanced blocks are skipped.
 */
export function enhanceCode(root, { highlight = true } = {}) {
  const blocks = root.querySelectorAll("pre > code");
  blocks.forEach((code) => {
    const pre = code.parentElement;
    if (pre.parentElement && pre.parentElement.classList.contains("code-block")) return;
    const langMatch = /language-([\w+#.-]+)/.exec(code.className || "");
    const lang = langMatch ? langMatch[1] : "";
    const wrap = document.createElement("div");
    wrap.className = "code-block";
    const head = document.createElement("div");
    head.className = "code-head";
    const label = document.createElement("span");
    label.textContent = lang || "код";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "code-copy";
    btn.innerHTML = icons.copy + '<span data-label="Копировать">Копировать</span>';
    btn.addEventListener("click", () => copyWithFeedback(btn, code.textContent || "", { label: "Копировать" }));
    head.append(label, btn);
    pre.replaceWith(wrap);
    wrap.append(head, pre);
    if (highlight && window.hljs) {
      try {
        if (lang && window.hljs.getLanguage(lang)) window.hljs.highlightElement(code);
        else if (!lang && (code.textContent || "").length < 20000) {
          const res = window.hljs.highlightAuto(code.textContent || "");
          code.innerHTML = res.value; // hljs output is escaped token markup
          code.classList.add("hljs");
        }
      } catch {
        /* leave plain */
      }
    }
  });
}
