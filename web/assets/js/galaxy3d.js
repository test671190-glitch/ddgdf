// «Галактика моделей»: every model is a glowing point orbiting a core, grouped in rings by type
// or in clusters by vendor. Hover/tap shows name + id (CSS2D tooltip), click selects.

import * as THREE from "three";
import { CSS2DRenderer, CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";

const TYPE_COLOR = { text: new THREE.Color(0xff8a4c), image: new THREE.Color(0xffd08a), video: new THREE.Color(0xff6377) };
const RINGS = {
  image: { r: 1.85, euler: new THREE.Euler(0.34, 0, -0.14), speed: 0.17 },
  video: { r: 2.95, euler: new THREE.Euler(-0.2, 0, 0.2), speed: 0.115 },
  text: { r: 4.25, euler: new THREE.Euler(0.12, 0, 0.05), speed: 0.065 },
};
const TYPE_TITLES = { text: "Текст", image: "Изображения", video: "Видео" };

function glowTexture() {
  const c = document.createElement("canvas");
  c.width = c.height = 64;
  const g = c.getContext("2d");
  const grd = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  grd.addColorStop(0, "rgba(255,255,255,1)");
  grd.addColorStop(0.2, "rgba(255,255,255,0.65)");
  grd.addColorStop(0.5, "rgba(255,255,255,0.12)");
  grd.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = grd;
  g.fillRect(0, 0, 64, 64);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

const easeInOut = (x) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2);

/**
 * @param {HTMLElement} stage
 * @param {{models: Array, onSelect?: Function, onTypeClick?: Function, onVendorClick?: Function, reducedMotion?: boolean}} opts
 */
export function initGalaxy(stage, { models, onSelect, onTypeClick, onVendorClick, reducedMotion = false }) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance" });
  if (!renderer.getContext()) throw new Error("no webgl");
  renderer.setClearColor(0x000000, 0);
  const dpr = Math.min(window.devicePixelRatio || 1, 1.75);
  renderer.setPixelRatio(dpr);
  renderer.domElement.setAttribute("aria-hidden", "true");

  const labelRenderer = new CSS2DRenderer();
  labelRenderer.domElement.className = "galaxy-labels";
  stage.append(renderer.domElement, labelRenderer.domElement);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 200);
  const world = new THREE.Group();
  scene.add(world);

  const tex = glowTexture();
  const disposables = [tex];

  /* --- core --- */
  const coreGlow = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, color: 0xff8a4c, blending: THREE.AdditiveBlending, depthWrite: false, transparent: true, opacity: 0.9 }));
  coreGlow.scale.setScalar(2.6);
  const coreHot = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, color: 0xffe6cc, blending: THREE.AdditiveBlending, depthWrite: false, transparent: true }));
  coreHot.scale.setScalar(0.9);
  world.add(coreGlow, coreHot);

  /* --- type rings --- */
  const ringObjs = {};
  const typeLabels = {};
  const counts = { text: 0, image: 0, video: 0 };
  models.forEach((m) => (counts[m.type] = (counts[m.type] || 0) + 1));

  Object.entries(RINGS).forEach(([type, cfg]) => {
    const g = new THREE.Group();
    g.rotation.copy(cfg.euler);
    world.add(g);
    const pts = [];
    for (let i = 0; i <= 200; i++) {
      const a = (i / 200) * Math.PI * 2;
      pts.push(new THREE.Vector3(Math.cos(a) * cfg.r, 0, Math.sin(a) * cfg.r));
    }
    const lineGeo = new THREE.BufferGeometry().setFromPoints(pts);
    const lineMat = new THREE.LineBasicMaterial({ color: TYPE_COLOR[type], transparent: true, opacity: 0.22, blending: THREE.AdditiveBlending, depthWrite: false });
    const line = new THREE.Line(lineGeo, lineMat);
    g.add(line);

    // dust around the ring
    const N = 320;
    const dp = new Float32Array(N * 3);
    for (let i = 0; i < N; i++) {
      const a = Math.random() * Math.PI * 2;
      const rr = cfg.r + (Math.random() - 0.5) * 0.5;
      dp[i * 3] = Math.cos(a) * rr;
      dp[i * 3 + 1] = (Math.random() - 0.5) * 0.12;
      dp[i * 3 + 2] = Math.sin(a) * rr;
    }
    const dgeo = new THREE.BufferGeometry();
    dgeo.setAttribute("position", new THREE.BufferAttribute(dp, 3));
    const dmat = new THREE.PointsMaterial({ color: TYPE_COLOR[type], size: 0.035, transparent: true, opacity: 0.45, blending: THREE.AdditiveBlending, depthWrite: false, sizeAttenuation: true });
    g.add(new THREE.Points(dgeo, dmat));
    disposables.push(lineGeo, lineMat, dgeo, dmat);

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "g-label";
    btn.dataset.type = type;
    const dot = document.createElement("i");
    dot.className = "type-dot";
    dot.dataset.type = type;
    const b = document.createElement("b");
    b.textContent = String(counts[type] || 0);
    btn.append(dot, document.createTextNode(TYPE_TITLES[type] + " "), b);
    btn.addEventListener("click", () => onTypeClick && onTypeClick(type));
    const label = new CSS2DObject(btn);
    label.position.set(cfg.r, 0, 0);
    g.add(label);
    typeLabels[type] = { obj: label, el: btn };
    ringObjs[type] = { group: g, lineMat, dmat };
  });

  /* --- vendor layout --- */
  const vendors = [];
  models.forEach((m) => {
    if (!vendors.includes(m.vendor)) vendors.push(m.vendor);
  });
  const VR = 4.0;
  const vendorInfo = {};
  vendors.forEach((v, i) => {
    const list = models.filter((m) => m.vendor === v);
    const a = (i / vendors.length) * Math.PI * 2;
    vendorInfo[v] = {
      angle: a,
      y: Math.sin(i * 1.9) * 0.55,
      r: 0.28 + Math.min(0.75, list.length * 0.045),
      count: list.length,
      center: new THREE.Vector3(),
    };
    const el = document.createElement("button");
    el.type = "button";
    el.className = "g-vlabel";
    const b = document.createElement("b");
    b.textContent = String(list.length);
    el.append(document.createTextNode(v + " "), b);
    el.addEventListener("click", () => onVendorClick && onVendorClick(v));
    el.style.opacity = "0";
    el.style.pointerEvents = "none";
    const obj = new CSS2DObject(el);
    world.add(obj);
    vendorInfo[v].label = { obj, el };
  });

  /* --- model points --- */
  const sphereGeo = new THREE.SphereGeometry(0.075, 16, 12);
  disposables.push(sphereGeo);
  const byType = { text: [], image: [], video: [] };
  const byVendor = {};
  const nodes = models.map((m) => {
    const color = TYPE_COLOR[m.type] || TYPE_COLOR.text;
    const g = new THREE.Group();
    const dotMat = new THREE.MeshBasicMaterial({ color: color.clone().lerp(new THREE.Color(0xffffff), 0.35), transparent: true });
    const dot = new THREE.Mesh(sphereGeo, dotMat);
    const glowMat = new THREE.SpriteMaterial({ map: tex, color, blending: THREE.AdditiveBlending, depthWrite: false, transparent: true, opacity: 0.9 });
    const glow = new THREE.Sprite(glowMat);
    glow.scale.setScalar(0.62);
    g.add(glow, dot);
    world.add(g);
    disposables.push(dotMat, glowMat);
    const node = { model: m, group: g, dotMat, glowMat, scale: 1, targetScale: 1, dim: 1, targetDim: 1, pos: new THREE.Vector3() };
    (byType[m.type] || byType.text).push(node);
    (byVendor[m.vendor] = byVendor[m.vendor] || []).push(node);
    return node;
  });
  Object.entries(byType).forEach(([type, list]) => {
    list.forEach((n, i) => {
      n.typeAngle = (i / Math.max(1, list.length)) * Math.PI * 2 + (type === "image" ? 0.3 : type === "video" ? 1.1 : 0);
    });
  });
  Object.values(byVendor).forEach((list) => {
    list.forEach((n, i) => (n.vendorAngle = (i / list.length) * Math.PI * 2));
  });

  /* --- tooltip --- */
  const tipEl = document.createElement("div");
  tipEl.className = "g-tip";
  tipEl.style.display = "none";
  const tip = new CSS2DObject(tipEl);
  tip.center.set(0.5, 1);
  tip.visible = false;
  world.add(tip);

  function showTip(node) {
    if (!node) {
      tip.visible = false;
      return;
    }
    const inner = document.createElement("div");
    inner.className = "g-tip-inner";
    const b = document.createElement("b");
    b.textContent = node.model.name;
    const code = document.createElement("code");
    code.textContent = node.model.id;
    const v = document.createElement("span");
    v.textContent = node.model.vendor + " · " + TYPE_TITLES[node.model.type];
    inner.append(b, code, v);
    tipEl.replaceChildren(inner);
    tip.visible = true;
  }

  /* --- state --- */
  let mode = "type";
  let modeT = 0; // 0 = type rings, 1 = vendor clusters
  let modeFrom = 0;
  let modeTo = 0;
  let modeStart = 0;
  let focusType = null;
  let selectedId = null;
  let hovered = null;
  let speedK = 1;
  let time = 0;
  let yaw = 0.4;
  let pitch = 0.32;
  let yawTarget = yaw;
  let pitchTarget = pitch;
  let autoSpin = !reducedMotion;

  const tmp = new THREE.Vector3();
  const tmpB = new THREE.Vector3();
  const proj = new THREE.Vector3();

  function typePos(n, t, out) {
    const cfg = RINGS[n.model.type] || RINGS.text;
    const a = n.typeAngle + t * cfg.speed;
    out.set(Math.cos(a) * cfg.r, 0, Math.sin(a) * cfg.r).applyEuler(cfg.euler);
    return out;
  }
  function vendorCenter(v, t, out) {
    const info = vendorInfo[v];
    const a = info.angle + t * 0.025;
    return out.set(Math.cos(a) * VR, info.y, Math.sin(a) * VR);
  }
  function vendorPos(n, t, out) {
    const info = vendorInfo[n.model.vendor];
    vendorCenter(n.model.vendor, t, out);
    const a = n.vendorAngle + t * 0.4;
    out.x += Math.cos(a) * info.r;
    out.y += Math.sin(a) * info.r * 0.45;
    out.z += Math.sin(a) * info.r;
    return out;
  }

  function layout(dt) {
    const k = easeInOut(modeT);
    nodes.forEach((n) => {
      typePos(n, time, tmp);
      vendorPos(n, time, tmpB);
      n.pos.copy(tmp).lerp(tmpB, k);
      n.group.position.copy(n.pos);
      n.scale += (n.targetScale - n.scale) * Math.min(1, dt * 10);
      n.dim += (n.targetDim - n.dim) * Math.min(1, dt * 6);
      n.group.scale.setScalar(n.scale);
      n.glowMat.opacity = 0.9 * n.dim;
      n.dotMat.opacity = 0.35 + 0.65 * n.dim;
    });
    Object.entries(ringObjs).forEach(([type, r]) => {
      const vis = 1 - k;
      const f = focusType && focusType !== type ? 0.35 : 1;
      r.lineMat.opacity = 0.22 * vis * f;
      r.dmat.opacity = 0.45 * vis * f;
      const lab = typeLabels[type];
      lab.el.style.opacity = String(vis);
      lab.el.style.pointerEvents = vis > 0.5 ? "auto" : "none";
      lab.el.classList.toggle("is-active", focusType === type);
      lab.el.tabIndex = vis > 0.5 ? 0 : -1;
    });
    vendors.forEach((v) => {
      const info = vendorInfo[v];
      vendorCenter(v, time, info.label.obj.position);
      info.label.obj.position.y += info.r * 0.45 + 0.32;
      info.label.el.style.opacity = String(k);
      info.label.el.style.pointerEvents = k > 0.5 ? "auto" : "none";
      info.label.el.tabIndex = k > 0.5 ? 0 : -1;
    });
    if (hovered) tip.position.copy(hovered.pos);
  }

  function updateTargets() {
    nodes.forEach((n) => {
      const isSel = n.model.id === selectedId;
      const isHover = hovered === n;
      n.targetScale = isHover ? 2.1 : isSel ? 1.7 : 1;
      n.targetDim = focusType && n.model.type !== focusType && !isHover ? 0.22 : 1;
    });
  }

  /* --- sizing --- */
  let width = 1;
  let height = 1;
  function resize() {
    width = Math.max(1, stage.clientWidth);
    height = Math.max(1, stage.clientHeight);
    renderer.setSize(width, height, false);
    renderer.domElement.style.width = "100%";
    renderer.domElement.style.height = "100%";
    labelRenderer.setSize(width, height);
    camera.aspect = width / height;
    const fitW = 5.3 / (Math.tan(THREE.MathUtils.degToRad(20)) * camera.aspect);
    const fitH = 3.6 / Math.tan(THREE.MathUtils.degToRad(20));
    const dist = Math.max(fitW, fitH);
    camera.position.set(0, dist * 0.32, dist);
    camera.lookAt(0, -0.2, 0);
    camera.updateProjectionMatrix();
    invalidate();
  }
  const ro = new ResizeObserver(resize);
  ro.observe(stage);

  /* --- picking --- */
  function nearest(clientX, clientY, radius) {
    const rect = renderer.domElement.getBoundingClientRect();
    const x = clientX - rect.left;
    const y = clientY - rect.top;
    let best = null;
    let bestD = radius;
    world.updateMatrixWorld();
    nodes.forEach((n) => {
      proj.copy(n.pos).applyMatrix4(world.matrixWorld).project(camera);
      if (proj.z > 1) return;
      const sx = ((proj.x + 1) / 2) * rect.width;
      const sy = ((1 - proj.y) / 2) * rect.height;
      const d = Math.hypot(sx - x, sy - y);
      if (d < bestD) {
        bestD = d;
        best = n;
      }
    });
    return best;
  }

  function setHover(n) {
    if (hovered === n) return;
    hovered = n;
    showTip(n);
    stage.classList.toggle("is-hovering", !!n);
    updateTargets();
    invalidate();
  }

  let drag = null;
  const el = renderer.domElement;
  stage.addEventListener("pointerdown", (e) => {
    if (e.target.closest(".g-label, .g-vlabel")) return;
    drag = { id: e.pointerId, x: e.clientX, y: e.clientY, sx: e.clientX, sy: e.clientY, t: performance.now(), moved: false, type: e.pointerType };
    if (e.pointerType === "mouse") stage.setPointerCapture?.(e.pointerId);
  });
  stage.addEventListener("pointermove", (e) => {
    if (drag && drag.id === e.pointerId) {
      const dx = e.clientX - drag.x;
      const dy = e.clientY - drag.y;
      drag.x = e.clientX;
      drag.y = e.clientY;
      if (Math.hypot(e.clientX - drag.sx, e.clientY - drag.sy) > 6) {
        drag.moved = true;
        stage.classList.add("is-dragging");
      }
      if (drag.moved) {
        yawTarget += dx * 0.006;
        if (drag.type === "mouse") pitchTarget = THREE.MathUtils.clamp(pitchTarget + dy * 0.004, -0.35, 0.85);
        invalidate();
      }
      if (drag.moved) return;
    }
    if (e.pointerType === "mouse") setHover(nearest(e.clientX, e.clientY, 22));
  });
  const endDrag = (e) => {
    if (!drag || drag.id !== e.pointerId) return;
    const wasTap = !drag.moved && performance.now() - drag.t < 600;
    const touch = drag.type !== "mouse";
    drag = null;
    stage.classList.remove("is-dragging");
    if (!wasTap || e.type === "pointercancel") return;
    const n = nearest(e.clientX, e.clientY, touch ? 32 : 22);
    if (touch) setHover(n);
    if (n) {
      selectedId = n.model.id;
      updateTargets();
      invalidate();
      if (onSelect) onSelect(n.model);
    }
  };
  stage.addEventListener("pointerup", endDrag);
  stage.addEventListener("pointercancel", endDrag);
  stage.addEventListener("pointerleave", (e) => {
    if (e.pointerType === "mouse" && !drag) setHover(null);
  });

  /* --- loop --- */
  let running = false;
  let raf = 0;
  let inView = false;
  let last = performance.now();
  let pendingFrame = false;

  function draw(dt) {
    if (modeT !== modeTo) {
      const p = Math.min(1, (performance.now() - modeStart) / (reducedMotion ? 1 : 1300));
      modeT = modeFrom + (modeTo - modeFrom) * p;
    }
    speedK += ((hovered ? 0.12 : 1) - speedK) * Math.min(1, dt * 4);
    if (!reducedMotion) time += dt * speedK;
    if (autoSpin && !drag) yawTarget += dt * 0.05 * speedK;
    yaw += (yawTarget - yaw) * Math.min(1, dt * 5);
    pitch += (pitchTarget - pitch) * Math.min(1, dt * 5);
    world.rotation.set(pitch * 0.5, yaw, 0);
    const pulse = 1 + Math.sin(performance.now() * 0.0015) * (reducedMotion ? 0 : 0.06);
    coreGlow.scale.setScalar(2.6 * pulse);
    layout(dt);
    renderer.render(scene, camera);
    labelRenderer.render(scene, camera);
  }

  function frame(now) {
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    draw(dt);
    const settling =
      Math.abs(yawTarget - yaw) > 0.001 ||
      Math.abs(pitchTarget - pitch) > 0.001 ||
      modeT !== modeTo ||
      nodes.some((n) => Math.abs(n.targetScale - n.scale) > 0.01 || Math.abs(n.targetDim - n.dim) > 0.01);
    if (running && (!reducedMotion || settling)) raf = requestAnimationFrame(frame);
    else {
      running = false;
      pendingFrame = false;
    }
  }

  function start() {
    if (running) return;
    if (!inView || document.hidden) return;
    running = true;
    last = performance.now();
    raf = requestAnimationFrame(frame);
  }
  function stop() {
    running = false;
    cancelAnimationFrame(raf);
  }
  function invalidate() {
    if (running) return;
    if (!inView || document.hidden) {
      pendingFrame = true;
      return;
    }
    start();
  }

  const io = new IntersectionObserver(([en]) => {
    inView = en.isIntersecting;
    if (inView) start();
    else stop();
  });
  io.observe(stage);
  const onVis = () => (document.hidden ? stop() : inView && start());
  document.addEventListener("visibilitychange", onVis);

  resize();
  updateTargets();
  draw(0.016);
  stage.classList.add("is-live");

  return {
    setMode(next) {
      if (next === mode) return;
      mode = next;
      modeFrom = modeT;
      modeTo = next === "vendor" ? 1 : 0;
      modeStart = performance.now();
      invalidate();
    },
    setFocus(type) {
      focusType = type || null;
      updateTargets();
      invalidate();
    },
    select(id) {
      selectedId = id || null;
      updateTargets();
      invalidate();
    },
    destroy() {
      stop();
      io.disconnect();
      ro.disconnect();
      document.removeEventListener("visibilitychange", onVis);
      disposables.forEach((d) => d.dispose && d.dispose());
      renderer.dispose();
      el.remove();
      labelRenderer.domElement.remove();
    },
  };
}
