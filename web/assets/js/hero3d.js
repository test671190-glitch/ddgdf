// Hero «neural core»: particle sphere + glowing nodes linked by pulsing lines, orbit rings and star dust.
// Pauses when the tab is hidden or the hero is off-screen. Throws if WebGL is unavailable.

import * as THREE from "three";

const PARTICLE_VERT = /* glsl */ `
  uniform float uTime;
  uniform float uSize;
  uniform float uPixelRatio;
  uniform float uWave;
  uniform vec3 uPointer;
  uniform float uPointerAmt;
  attribute float aScale;
  attribute float aSeed;
  attribute vec3 aColor;
  varying vec3 vColor;
  varying float vAlpha;
  void main() {
    vec3 p = position;
    vec3 n = normalize(p);
    float w = sin(p.x * 2.3 + uTime * 0.9 + aSeed * 6.2831) * sin(p.y * 2.1 - uTime * 0.7) * sin(p.z * 1.9 + uTime * 0.6);
    p += n * w * uWave;
    float d = distance(n, uPointer);
    float bulge = smoothstep(0.85, 0.0, d) * uPointerAmt;
    p += n * bulge * 0.42 * step(0.001, uWave);
    vec4 mv = modelViewMatrix * vec4(p, 1.0);
    gl_Position = projectionMatrix * mv;
    float tw = 0.62 + 0.38 * sin(uTime * 1.7 + aSeed * 43.0);
    gl_PointSize = uSize * aScale * uPixelRatio * (1.0 + bulge * 1.1) / max(0.1, -mv.z);
    vec3 vn = normalize(mat3(modelViewMatrix) * n);
    float front = vn.z * 0.5 + 0.5;
    vAlpha = tw * mix(0.16, 1.0, front);
    vColor = mix(aColor, vec3(1.0, 0.93, 0.84), bulge * 0.75);
  }
`;

const PARTICLE_FRAG = /* glsl */ `
  uniform float uOpacity;
  varying vec3 vColor;
  varying float vAlpha;
  void main() {
    vec2 c = gl_PointCoord - 0.5;
    float d = length(c);
    if (d > 0.5) discard;
    float a = pow(1.0 - d * 2.0, 1.7);
    gl_FragColor = vec4(vColor, a * vAlpha * uOpacity);
  }
`;

const LINE_VERT = /* glsl */ `
  attribute float aT;
  attribute float aSeed;
  varying float vT;
  varying float vSeed;
  varying float vFront;
  void main() {
    vT = aT;
    vSeed = aSeed;
    vFront = clamp(normalize(mat3(modelViewMatrix) * position).z * 0.5 + 0.5, 0.0, 1.0);
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const LINE_FRAG = /* glsl */ `
  uniform float uTime;
  uniform vec3 uColor;
  uniform vec3 uHot;
  uniform float uOpacity;
  varying float vT;
  varying float vSeed;
  varying float vFront;
  void main() {
    float head = fract(uTime * (0.16 + vSeed * 0.22) + vSeed * 7.0);
    float pulse = smoothstep(0.1, 0.0, abs(vT - head));
    float base = 0.08 + 0.12 * vFront;
    vec3 col = mix(uColor, uHot, pulse);
    gl_FragColor = vec4(col, (base + pulse * 0.85) * uOpacity * mix(0.3, 1.0, vFront));
  }
`;

const PALETTE = [
  new THREE.Color(1.0, 0.48, 0.24), // ember
  new THREE.Color(1.0, 0.66, 0.4), // orange light
  new THREE.Color(1.0, 0.82, 0.56), // gold
  new THREE.Color(1.0, 0.4, 0.46), // rose
  new THREE.Color(1.0, 0.92, 0.84), // warm white
];

function glowTexture(stops) {
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const g = c.getContext("2d");
  const grd = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  stops.forEach(([o, col]) => grd.addColorStop(o, col));
  g.fillStyle = grd;
  g.fillRect(0, 0, 128, 128);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

function particleMaterial({ size, wave = 0, opacity = 1 }) {
  return new THREE.ShaderMaterial({
    uniforms: {
      uTime: { value: 0 },
      uSize: { value: size },
      uPixelRatio: { value: 1 },
      uWave: { value: wave },
      uPointer: { value: new THREE.Vector3(0, 0, 1) },
      uPointerAmt: { value: 0 },
      uOpacity: { value: opacity },
    },
    vertexShader: PARTICLE_VERT,
    fragmentShader: PARTICLE_FRAG,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
}

function pointsGeometry(positions, colorFn, scaleFn) {
  const n = positions.length / 3;
  const colors = new Float32Array(n * 3);
  const scales = new Float32Array(n);
  const seeds = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const c = colorFn(i, positions[i * 3], positions[i * 3 + 1], positions[i * 3 + 2]);
    colors[i * 3] = c.r;
    colors[i * 3 + 1] = c.g;
    colors[i * 3 + 2] = c.b;
    scales[i] = scaleFn(i);
    seeds[i] = Math.random();
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geo.setAttribute("aColor", new THREE.BufferAttribute(colors, 3));
  geo.setAttribute("aScale", new THREE.BufferAttribute(scales, 1));
  geo.setAttribute("aSeed", new THREE.BufferAttribute(seeds, 1));
  return geo;
}

function pickColor(y, R) {
  // gold on top, ember in the middle, rose at the bottom, sprinkled with warm white
  const t = (y / R + 1) / 2;
  const r = Math.random();
  if (r < 0.08) return PALETTE[4];
  if (t > 0.66) return r < 0.6 ? PALETTE[2] : PALETTE[1];
  if (t < 0.3) return r < 0.55 ? PALETTE[3] : PALETTE[0];
  return r < 0.5 ? PALETTE[0] : PALETTE[1];
}

/**
 * @param {HTMLCanvasElement} canvas
 * @param {{hero: HTMLElement, reducedMotion?: boolean}} opts
 */
export function initHero(canvas, { hero, reducedMotion = false } = {}) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: false, alpha: true, powerPreference: "high-performance" });
  if (!renderer.getContext()) throw new Error("no webgl");
  renderer.setClearColor(0x000000, 0);

  const small = Math.min(window.innerWidth, window.innerHeight) < 640;
  const dpr = Math.min(window.devicePixelRatio || 1, small ? 1.5 : 1.75);
  renderer.setPixelRatio(dpr);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 100);
  camera.position.set(0, 0, 7);

  const root = new THREE.Group(); // scroll offset
  const tiltG = new THREE.Group(); // pointer parallax
  const spin = new THREE.Group(); // continuous rotation
  root.add(tiltG);
  tiltG.add(spin);
  scene.add(root);

  const R = 1.55;
  const materials = [];

  /* --- particle shell --- */
  const COUNT = small ? 1500 : 2800;
  const pos = new Float32Array(COUNT * 3);
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < COUNT; i++) {
    const inner = i % 9 === 0;
    const y = 1 - (i / (COUNT - 1)) * 2;
    const rad = Math.sqrt(1 - y * y);
    const th = golden * i;
    const r = inner ? R * (0.35 + Math.random() * 0.5) : R * (0.94 + Math.random() * 0.1);
    pos[i * 3] = Math.cos(th) * rad * r;
    pos[i * 3 + 1] = y * r;
    pos[i * 3 + 2] = Math.sin(th) * rad * r;
  }
  const dustMat = particleMaterial({ size: 30, wave: 0.11 });
  materials.push(dustMat);
  const dust = new THREE.Points(
    pointsGeometry(pos, (i, x, y) => pickColor(y, R), () => (Math.random() < 0.04 ? 2.2 : 0.55 + Math.random() * 0.9)),
    dustMat,
  );
  spin.add(dust);

  /* --- nodes + connections --- */
  const net = new THREE.Group();
  spin.add(net);
  const NODES = small ? 56 : 84;
  const nodes = [];
  for (let i = 0; i < NODES; i++) {
    const v = new THREE.Vector3().randomDirection().multiplyScalar(R * (1.06 + Math.random() * 0.22));
    nodes.push(v);
  }
  const nodePos = new Float32Array(NODES * 3);
  nodes.forEach((v, i) => v.toArray(nodePos, i * 3));
  const nodeMat = particleMaterial({ size: 90, wave: 0 });
  materials.push(nodeMat);
  net.add(
    new THREE.Points(
      pointsGeometry(nodePos, () => (Math.random() < 0.3 ? PALETTE[4] : PALETTE[1]), () => 0.6 + Math.random() * 0.8),
      nodeMat,
    ),
  );

  const segs = [];
  const seen = new Set();
  nodes.forEach((a, i) => {
    const near = nodes
      .map((b, j) => ({ j, d: a.distanceTo(b) }))
      .filter((x) => x.j !== i && x.d < R * 0.78)
      .sort((x, y) => x.d - y.d)
      .slice(0, 3);
    near.forEach(({ j }) => {
      const key = i < j ? i + "-" + j : j + "-" + i;
      if (seen.has(key)) return;
      seen.add(key);
      segs.push([i, j]);
    });
  });
  const lpos = new Float32Array(segs.length * 6);
  const lt = new Float32Array(segs.length * 2);
  const ls = new Float32Array(segs.length * 2);
  segs.forEach(([i, j], k) => {
    nodes[i].toArray(lpos, k * 6);
    nodes[j].toArray(lpos, k * 6 + 3);
    lt[k * 2] = 0;
    lt[k * 2 + 1] = 1;
    const s = Math.random();
    ls[k * 2] = s;
    ls[k * 2 + 1] = s;
  });
  const lgeo = new THREE.BufferGeometry();
  lgeo.setAttribute("position", new THREE.BufferAttribute(lpos, 3));
  lgeo.setAttribute("aT", new THREE.BufferAttribute(lt, 1));
  lgeo.setAttribute("aSeed", new THREE.BufferAttribute(ls, 1));
  const lineMat = new THREE.ShaderMaterial({
    uniforms: {
      uTime: { value: 0 },
      uColor: { value: new THREE.Color(1.0, 0.5, 0.28) },
      uHot: { value: new THREE.Color(1.0, 0.9, 0.75) },
      uOpacity: { value: 1 },
    },
    vertexShader: LINE_VERT,
    fragmentShader: LINE_FRAG,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
  net.add(new THREE.LineSegments(lgeo, lineMat));

  /* --- core --- */
  const coreTex = glowTexture([
    [0, "rgba(255,240,226,1)"],
    [0.18, "rgba(255,170,110,0.75)"],
    [0.45, "rgba(255,110,50,0.18)"],
    [1, "rgba(0,0,0,0)"],
  ]);
  const haloTex = glowTexture([
    [0, "rgba(255,140,80,0.35)"],
    [0.5, "rgba(255,90,40,0.08)"],
    [1, "rgba(0,0,0,0)"],
  ]);
  const core = new THREE.Sprite(new THREE.SpriteMaterial({ map: coreTex, blending: THREE.AdditiveBlending, depthWrite: false, transparent: true }));
  core.scale.setScalar(1.25);
  const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: haloTex, blending: THREE.AdditiveBlending, depthWrite: false, transparent: true, opacity: 0.9 }));
  halo.scale.setScalar(5.6);
  tiltG.add(halo, core);

  const ico = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.IcosahedronGeometry(0.62, 1)),
    new THREE.LineBasicMaterial({ color: 0xff9a5c, transparent: true, opacity: 0.32, blending: THREE.AdditiveBlending, depthWrite: false }),
  );
  tiltG.add(ico);

  /* --- orbit rings (dotted) --- */
  const rings = [];
  [
    { r: 2.1, n: 240, tilt: [1.2, 0.2, 0.1], speed: 0.12 },
    { r: 2.45, n: 300, tilt: [1.45, -0.35, 0.4], speed: -0.08 },
    { r: 2.85, n: 340, tilt: [1.05, 0.5, -0.3], speed: 0.05 },
  ].forEach((cfg) => {
    const p = new Float32Array(cfg.n * 3);
    for (let i = 0; i < cfg.n; i++) {
      const a = (i / cfg.n) * Math.PI * 2;
      p[i * 3] = Math.cos(a) * cfg.r;
      p[i * 3 + 1] = Math.sin(a) * cfg.r;
      p[i * 3 + 2] = 0;
    }
    const mat = particleMaterial({ size: 26, wave: 0, opacity: 0.9 });
    materials.push(mat);
    const pts = new THREE.Points(
      pointsGeometry(
        p,
        (i) => (i % 37 === 0 ? PALETTE[4] : PALETTE[i % 2 ? 0 : 1]),
        // a bright comet head fading into a tail
        (i) => {
          const f = i / cfg.n;
          return f > 0.9 ? 0.6 + (f - 0.9) * 22 : 0.25 + f * 0.45;
        },
      ),
      mat,
    );
    const g = new THREE.Group();
    g.rotation.set(...cfg.tilt);
    g.add(pts);
    tiltG.add(g);
    rings.push({ g: pts, speed: cfg.speed });
  });

  /* --- star dust --- */
  const STARS = small ? 500 : 900;
  const sp = new Float32Array(STARS * 3);
  for (let i = 0; i < STARS; i++) {
    const v = new THREE.Vector3().randomDirection().multiplyScalar(7 + Math.random() * 12);
    v.toArray(sp, i * 3);
  }
  const starMat = particleMaterial({ size: 30, wave: 0, opacity: 0.55 });
  materials.push(starMat);
  const stars = new THREE.Points(pointsGeometry(sp, () => (Math.random() < 0.7 ? PALETTE[4] : PALETTE[2]), () => 0.4 + Math.random() * 0.9), starMat);
  scene.add(stars);

  /* --- interaction --- */
  const pointer = { x: 0, y: 0, tx: 0, ty: 0, active: false, ndc: new THREE.Vector2(), has: false };
  const raycaster = new THREE.Raycaster();
  const sphere = new THREE.Sphere(new THREE.Vector3(), R);
  const hit = new THREE.Vector3();
  let pointerAmt = 0;

  const onPointer = (e) => {
    const w = window.innerWidth;
    const hh = window.innerHeight;
    pointer.tx = (e.clientX / w) * 2 - 1;
    pointer.ty = (e.clientY / hh) * 2 - 1;
    const rect = canvas.getBoundingClientRect();
    pointer.ndc.set(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
    pointer.has = e.clientY >= rect.top && e.clientY <= rect.bottom;
  };
  const onLeave = () => (pointer.has = false);
  if (!reducedMotion) {
    window.addEventListener("pointermove", onPointer, { passive: true });
    document.addEventListener("pointerleave", onLeave);
  }

  /* --- sizing --- */
  let width = 0;
  let height = 0;
  function resize() {
    const r = canvas.getBoundingClientRect();
    width = Math.max(1, Math.round(r.width));
    height = Math.max(1, Math.round(r.height));
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.position.z = Math.max(7, 2.4 / (Math.tan(THREE.MathUtils.degToRad(21)) * camera.aspect));
    camera.updateProjectionMatrix();
    const sizeK = THREE.MathUtils.clamp(height / 900, 0.65, 1.35) * dpr;
    materials.forEach((m) => (m.uniforms.uPixelRatio.value = sizeK));
    if (!running) render(lastT);
  }
  const ro = new ResizeObserver(resize);
  ro.observe(canvas);

  /* --- loop --- */
  let running = false;
  let raf = 0;
  let inView = true;
  let lastFrame = performance.now();
  let lastT = 2.5;

  function render(t) {
    materials.forEach((m) => (m.uniforms.uTime.value = t));
    lineMat.uniforms.uTime.value = t;
    renderer.render(scene, camera);
  }

  function frame(now) {
    raf = requestAnimationFrame(frame);
    const dt = Math.min(0.05, (now - lastFrame) / 1000);
    lastFrame = now;
    lastT += dt;
    const t = lastT;

    pointer.x += (pointer.tx - pointer.x) * Math.min(1, dt * 2.5);
    pointer.y += (pointer.ty - pointer.y) * Math.min(1, dt * 2.5);
    tiltG.rotation.y = pointer.x * 0.35;
    tiltG.rotation.x = pointer.y * 0.22;

    spin.rotation.y += dt * 0.07;
    net.rotation.y -= dt * 0.025;
    net.rotation.z = Math.sin(t * 0.15) * 0.08;
    ico.rotation.x += dt * 0.18;
    ico.rotation.y += dt * 0.12;
    const pulse = 1 + Math.sin(t * 1.3) * 0.06;
    core.scale.setScalar(1.25 * pulse);
    halo.material.opacity = 0.75 + Math.sin(t * 0.9) * 0.15;
    rings.forEach((r) => (r.g.rotation.z += dt * r.speed));
    stars.rotation.y += dt * 0.006;

    // scroll: drift up and fade as the hero leaves the viewport
    const hr = hero.getBoundingClientRect();
    const prog = THREE.MathUtils.clamp(-hr.top / Math.max(1, hr.height), 0, 1);
    root.position.y = 0.25 + prog * 1.4;
    const fade = 1 - prog * 0.75;
    materials.forEach((m) => (m.uniforms.uOpacity.value = m === starMat ? 0.55 * fade : (m === dustMat || m === nodeMat ? 1 : 0.9) * fade));
    lineMat.uniforms.uOpacity.value = fade;

    // pointer bulge on the sphere surface
    let target = 0;
    if (pointer.has) {
      raycaster.setFromCamera(pointer.ndc, camera);
      dust.getWorldPosition(sphere.center);
      if (raycaster.ray.intersectSphere(sphere, hit)) {
        dust.worldToLocal(hit);
        dustMat.uniforms.uPointer.value.copy(hit).normalize();
        target = 1;
      }
    }
    pointerAmt += (target - pointerAmt) * Math.min(1, dt * 4);
    dustMat.uniforms.uPointerAmt.value = pointerAmt;

    render(t);
  }

  function update() {
    const should = inView && !document.hidden && !reducedMotion;
    if (should && !running) {
      running = true;
      lastFrame = performance.now();
      raf = requestAnimationFrame(frame);
    } else if (!should && running) {
      running = false;
      cancelAnimationFrame(raf);
    }
  }

  const io = new IntersectionObserver(([en]) => {
    inView = en.isIntersecting;
    update();
  });
  io.observe(hero);
  const onVis = () => update();
  document.addEventListener("visibilitychange", onVis);

  canvas.addEventListener("webglcontextlost", (e) => {
    e.preventDefault();
    running = false;
    cancelAnimationFrame(raf);
  });

  resize();
  if (reducedMotion) {
    spin.rotation.y = 0.6;
    render(lastT);
  }
  update();
  requestAnimationFrame(() => canvas.classList.add("is-ready"));

  return {
    destroy() {
      running = false;
      cancelAnimationFrame(raf);
      io.disconnect();
      ro.disconnect();
      document.removeEventListener("visibilitychange", onVis);
      window.removeEventListener("pointermove", onPointer);
      document.removeEventListener("pointerleave", onLeave);
      renderer.dispose();
    },
  };
}
