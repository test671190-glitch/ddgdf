// Lightweight 2D-canvas «neural orb» for the empty chat state (no WebGL needed).

import { reducedMotion } from "./utils.js";

const COLORS = ["255,138,76", "255,176,122", "255,208,138", "255,99,119", "255,236,220"];

export function initOrb(canvas) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return { destroy() {} };
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const N = 340;
  const pts = [];
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < N; i++) {
    const y = 1 - (i / (N - 1)) * 2;
    const r = Math.sqrt(1 - y * y);
    const th = golden * i;
    pts.push({ x: Math.cos(th) * r, y, z: Math.sin(th) * r, c: COLORS[i % COLORS.length], s: 0.6 + Math.random() * 1.1, p: Math.random() * 6.28 });
  }
  let size = 0;
  let raf = 0;
  let running = false;
  let visible = true;
  let t = 0;
  let last = performance.now();
  const rm = reducedMotion();

  function resize() {
    const r = canvas.getBoundingClientRect();
    size = Math.max(1, Math.round(Math.min(r.width, r.height)));
    canvas.width = size * dpr;
    canvas.height = size * dpr;
  }

  function draw() {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, size, size);
    const cx = size / 2;
    const cy = size / 2;
    const R = size * 0.34;

    const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, size * 0.5);
    g.addColorStop(0, "rgba(255,236,220,0.9)");
    g.addColorStop(0.12, "rgba(255,150,90,0.45)");
    g.addColorStop(0.4, "rgba(255,100,50,0.08)");
    g.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(cx, cy, size * 0.5 * (1 + Math.sin(t * 1.4) * 0.03), 0, Math.PI * 2);
    ctx.fill();

    const ay = t * 0.35;
    const ax = 0.45 + Math.sin(t * 0.3) * 0.1;
    const cosY = Math.cos(ay), sinY = Math.sin(ay), cosX = Math.cos(ax), sinX = Math.sin(ax);
    ctx.globalCompositeOperation = "lighter";
    for (const p of pts) {
      const wob = 1 + Math.sin(t * 1.2 + p.p) * 0.05;
      let x = p.x * cosY - p.z * sinY;
      let z = p.x * sinY + p.z * cosY;
      const y = p.y * cosX - z * sinX;
      z = p.y * sinX + z * cosX;
      x *= wob;
      const persp = 1 / (1.9 - z * 0.6);
      const sx = cx + x * R * persp * 1.3;
      const sy = cy + y * wob * R * persp * 1.3;
      const a = 0.15 + (z + 1) * 0.38;
      ctx.fillStyle = `rgba(${p.c},${a.toFixed(3)})`;
      ctx.beginPath();
      ctx.arc(sx, sy, p.s * persp * 1.4, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalCompositeOperation = "source-over";
  }

  function frame(now) {
    t += Math.min(0.05, (now - last) / 1000);
    last = now;
    draw();
    raf = requestAnimationFrame(frame);
  }
  function update() {
    const should = visible && !document.hidden && !rm;
    if (should && !running) {
      running = true;
      last = performance.now();
      raf = requestAnimationFrame(frame);
    } else if (!should && running) {
      running = false;
      cancelAnimationFrame(raf);
    }
  }
  const io = new IntersectionObserver(([en]) => {
    visible = en.isIntersecting;
    update();
  });
  io.observe(canvas);
  const ro = new ResizeObserver(() => {
    resize();
    if (!running) draw();
  });
  ro.observe(canvas);
  document.addEventListener("visibilitychange", update);
  resize();
  t = 1.2;
  draw();
  update();

  return {
    destroy() {
      running = false;
      cancelAnimationFrame(raf);
      io.disconnect();
      ro.disconnect();
      document.removeEventListener("visibilitychange", update);
    },
  };
}
