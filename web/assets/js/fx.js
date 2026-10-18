// Motion helpers for the landing: GSAP ScrollTrigger reveals, counters, 3D tilt, typing, accordion.
// Everything is progressive: without GSAP or with prefers-reduced-motion the content is simply shown.

import { reducedMotion, isCoarsePointer } from "./utils.js";

const gsapReady = () => Boolean(window.gsap && window.ScrollTrigger);
let registered = false;

export function setup() {
  if (!gsapReady() || registered) return;
  window.gsap.registerPlugin(window.ScrollTrigger);
  registered = true;
}

const CLEAR = "transform,opacity,visibility";

/** Reveal elements marked with [data-reveal] / children of [data-reveal-stagger] once they scroll into view. */
export function reveal(root = document) {
  if (reducedMotion() || !gsapReady()) return;
  setup();
  const { gsap } = window;
  root.querySelectorAll("[data-reveal]:not([data-revealed])").forEach((el) => {
    el.dataset.revealed = "1";
    gsap.from(el, {
      autoAlpha: 0,
      y: 36,
      duration: 1,
      ease: "power3.out",
      clearProps: CLEAR,
      scrollTrigger: { trigger: el, start: "top 92%", once: true },
    });
  });
  root.querySelectorAll("[data-reveal-stagger]:not([data-revealed])").forEach((el) => {
    el.dataset.revealed = "1";
    const kids = Array.from(el.children).filter((k) => !k.matches("[data-no-reveal]"));
    if (!kids.length) return;
    gsap.from(kids, {
      autoAlpha: 0,
      y: 34,
      duration: 0.9,
      ease: "power3.out",
      stagger: 0.08,
      clearProps: CLEAR,
      scrollTrigger: { trigger: el, start: "top 90%", once: true },
    });
  });
}

/** Hero intro: words of the heading rise in, then the prompt and chips. */
export function heroIntro() {
  if (reducedMotion() || !gsapReady()) return;
  const { gsap } = window;
  const title = document.getElementById("hero-title");
  if (title && !title.dataset.split) {
    title.dataset.split = "1";
    // Split into words while preserving the accent span.
    const splitNode = (node) => {
      const out = [];
      node.childNodes.forEach((child) => {
        if (child.nodeType === Node.TEXT_NODE) {
          child.textContent.split(/(\s+)/).forEach((part) => {
            if (!part) return;
            if (/^\s+$/.test(part)) out.push(document.createTextNode(part));
            else {
              const w = document.createElement("span");
              w.className = "word";
              w.textContent = part;
              out.push(w);
            }
          });
        } else if (child.nodeType === Node.ELEMENT_NODE) {
          const clone = child.cloneNode(false);
          splitNode(child).forEach((n) => clone.append(n));
          clone.classList.add("word");
          out.push(clone);
        }
      });
      return out;
    };
    const parts = splitNode(title);
    title.replaceChildren(...parts);
  }
  const tl = gsap.timeline({ defaults: { ease: "power3.out" } });
  tl.from(".hero-badge", { autoAlpha: 0, y: 16, duration: 0.7, clearProps: CLEAR })
    .from(".hero-title .word", { autoAlpha: 0, yPercent: 60, rotateX: -40, duration: 0.9, stagger: 0.08, clearProps: CLEAR + ",rotate" }, "-=0.4")
    .from(".hero-sub", { autoAlpha: 0, y: 18, duration: 0.8, clearProps: CLEAR }, "-=0.55")
    .from(".prompt", { autoAlpha: 0, y: 26, scale: 0.98, duration: 0.9, clearProps: CLEAR + ",scale" }, "-=0.55")
    .from(".hero-chips .chip", { autoAlpha: 0, y: 12, duration: 0.5, stagger: 0.06, clearProps: CLEAR }, "-=0.5")
    .from(".hero-note", { autoAlpha: 0, duration: 0.6, clearProps: CLEAR }, "-=0.3");

  // Safety net: in a background tab requestAnimationFrame is paused, so the intro would leave the
  // prompt invisible. Jump to the end if the page starts hidden, and in any case after 3.5 s.
  const finish = () => {
    if (tl.progress() < 1) tl.progress(1);
  };
  if (document.hidden) finish();
  setTimeout(finish, 3500);

  // Gentle parallax of the headline while scrolling past the hero.
  gsap.to("#hero-head", {
    y: -70,
    autoAlpha: 0.25,
    ease: "none",
    scrollTrigger: { trigger: ".hero", start: "top top", end: "bottom top", scrub: 0.6 },
  });
}

/** Steps connector draws itself while scrolling. */
export function stepsLine() {
  if (reducedMotion() || !gsapReady()) return;
  const line = document.querySelector(".steps-line");
  if (!line) return;
  const vertical = window.matchMedia("(max-width: 860px)").matches;
  window.gsap.fromTo(
    line,
    vertical ? { scaleY: 0 } : { scaleX: 0 },
    {
      ...(vertical ? { scaleY: 1 } : { scaleX: 1 }),
      ease: "none",
      scrollTrigger: { trigger: ".steps", start: "top 80%", end: "bottom 60%", scrub: 0.5 },
    },
  );
}

export function refresh() {
  if (gsapReady()) window.ScrollTrigger.refresh();
}

/* ---------------- counters ---------------- */

export function counters(root = document) {
  const els = root.querySelectorAll("[data-count]:not([data-counted])");
  if (!els.length || reducedMotion() || !("IntersectionObserver" in window)) return;
  const io = new IntersectionObserver(
    (entries) => {
      entries.forEach((en) => {
        if (!en.isIntersecting) return;
        io.unobserve(en.target);
        animateCount(en.target);
      });
    },
    { threshold: 0.5 },
  );
  els.forEach((el) => {
    el.dataset.counted = "1";
    io.observe(el);
  });
}

function animateCount(el) {
  const target = Number(el.dataset.count) || 0;
  const dur = 1500;
  const t0 = performance.now();
  const step = (t) => {
    const p = Math.min(1, (t - t0) / dur);
    const e = 1 - Math.pow(1 - p, 4);
    el.textContent = String(Math.round(target * e));
    if (p < 1) requestAnimationFrame(step);
    else el.textContent = String(Number(el.dataset.count) || target);
  };
  requestAnimationFrame(step);
}

/* ---------------- 3D tilt ---------------- */

export function tilt(root = document) {
  if (reducedMotion() || isCoarsePointer()) return;
  root.querySelectorAll("[data-tilt]").forEach((el) => {
    if (el._tilt) return;
    el._tilt = true;
    const max = Number(el.dataset.tiltMax) || 6;
    let raf = 0;
    let lastE = null;
    const apply = () => {
      raf = 0;
      if (!lastE) return;
      const r = el.getBoundingClientRect();
      const px = Math.min(1, Math.max(0, (lastE.clientX - r.left) / r.width));
      const py = Math.min(1, Math.max(0, (lastE.clientY - r.top) / r.height));
      el.style.setProperty("--ry", ((px - 0.5) * max * 2).toFixed(2) + "deg");
      el.style.setProperty("--rx", ((0.5 - py) * max * 2).toFixed(2) + "deg");
      el.style.setProperty("--mx", (px * 100).toFixed(1) + "%");
      el.style.setProperty("--my", (py * 100).toFixed(1) + "%");
    };
    el.addEventListener("pointerenter", () => el.classList.add("is-tilting"));
    el.addEventListener("pointermove", (e) => {
      if (e.pointerType !== "mouse") return;
      lastE = e;
      if (!raf) raf = requestAnimationFrame(apply);
    });
    el.addEventListener("pointerleave", () => {
      lastE = null;
      el.classList.remove("is-tilting");
      el.style.setProperty("--rx", "0deg");
      el.style.setProperty("--ry", "0deg");
    });
  });
}

/* ---------------- typing ---------------- */

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Types the (pre-highlighted) contents of a <code> element when it scrolls into view. */
export function typing(code) {
  if (!code || reducedMotion() || !("IntersectionObserver" in window)) return;
  const tokens = Array.from(code.childNodes).map((n) => ({
    cls: n.nodeType === 1 ? n.className : "",
    text: n.textContent || "",
  }));
  const pre = code.parentElement;
  let started = false;
  const io = new IntersectionObserver(
    ([en]) => {
      if (!en.isIntersecting || started) return;
      started = true;
      io.disconnect();
      run();
    },
    { threshold: 0.45 },
  );
  io.observe(code);

  async function run() {
    pre.style.minHeight = pre.offsetHeight + "px";
    code.replaceChildren();
    code.classList.add("is-typing");
    for (const t of tokens) {
      const span = document.createElement("span");
      if (t.cls) span.className = t.cls;
      code.append(span);
      for (const ch of t.text) {
        span.textContent += ch;
        if (ch === "\n") await sleep(140);
        else if (ch !== " ") await sleep(14 + Math.random() * 26);
      }
    }
    code.classList.remove("is-typing");
    code.classList.add("is-typed");
  }
}

/* ---------------- accordion ---------------- */

export function accordion(details) {
  const summary = details.querySelector("summary");
  const body = details.querySelector(".faq-body");
  if (!summary || !body) return;
  let anim = null;
  summary.addEventListener("click", (e) => {
    if (reducedMotion() || !body.animate) return;
    e.preventDefault();
    if (anim) anim.cancel();
    const easing = "cubic-bezier(0.16, 1, 0.3, 1)";
    if (details.open) {
      const h0 = body.offsetHeight;
      anim = body.animate([{ height: h0 + "px", opacity: 1 }, { height: "0px", opacity: 0 }], { duration: 300, easing });
      anim.onfinish = () => {
        details.open = false;
        anim = null;
      };
    } else {
      details.open = true;
      const h1 = body.offsetHeight;
      anim = body.animate([{ height: "0px", opacity: 0 }, { height: h1 + "px", opacity: 1 }], { duration: 420, easing });
      anim.onfinish = () => (anim = null);
    }
  });
}

/* ---------------- feature micro-animations ---------------- */

/** Cycles model names in the «все модели в одном чате» card. */
export function modelSwitcher(el, answerEl, names) {
  if (!el || !names.length || reducedMotion()) return;
  let i = Math.max(0, names.indexOf(el.textContent.trim()));
  let timer = 0;
  let visible = false;
  const tick = () => {
    if (!visible || document.hidden) return;
    el.classList.remove("is-in");
    el.classList.add("is-out");
    setTimeout(() => {
      i = (i + 1) % names.length;
      el.textContent = names[i];
      el.classList.remove("is-out");
      el.classList.add("is-in");
      if (answerEl) {
        answerEl.classList.add("is-reset");
        void answerEl.offsetWidth;
        answerEl.classList.remove("is-reset");
      }
    }, 300);
  };
  const io = new IntersectionObserver(([en]) => {
    visible = en.isIntersecting;
    clearInterval(timer);
    if (visible) timer = setInterval(tick, 2400);
  });
  io.observe(el);
}

export function waveform(el, bars = 36) {
  if (!el) return;
  const frag = document.createDocumentFragment();
  for (let i = 0; i < bars; i++) {
    const s = document.createElement("span");
    const hgt = 0.3 + Math.abs(Math.sin(i * 1.7) * 0.5) + Math.random() * 0.2;
    s.style.setProperty("--h", hgt.toFixed(2));
    s.style.animationDelay = (-(i * 0.07) % 1.2).toFixed(2) + "s";
    s.style.animationDuration = (0.9 + (i % 5) * 0.12).toFixed(2) + "s";
    frag.append(s);
  }
  el.append(frag);
}
