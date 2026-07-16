/* MemoryBrain — "Night Folio" 3D graph. three.js (vendored locally), v3.
   A slowly turning constellation of gilded memory-orbs on dark folio paper:
   3D force layout, glowing sprites, inked edge strands, armillary rings on
   heavy nodes, drifting dust, raycast hover/select/drag, orbit/pan/dolly
   camera, in-graph search fly-to. ES module — no build step, fully offline. */
import * as THREE from "/static/vendor/three.module.min.js";

const canvas = document.getElementById("graph3d");
if (canvas) main();

function main() {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const tooltip = $("g-tooltip"), emptyMsg = $("g-empty"), legend = $("g-legend");
  const projectSel = $("g-project"), weightSlider = $("g-weight"), weightVal = $("g-weight-val");
  const nodeSearch = $("g-find"), physicsBtn = $("g-physics"), infoCard = $("g-info");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ----------------------------------------------------------- palette */
  const INK = 0xd8c49a;          // faded sepia-white ink
  const GOLD = 0xc9962e;
  const BG = 0x221a12;           // night folio

  function projectColor3(project) {   // warm-shifted stable project hue
    let h = 2166136261;
    const s = String(project || "?");
    for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
    const hue = ((h >>> 0) % 360) / 360;
    const c = new THREE.Color();
    c.setHSL(hue, 0.55, 0.62);
    return c.lerp(new THREE.Color(GOLD), 0.25);   // gild everything slightly
  }

  /* ------------------------------------------------------------- scene */
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(BG);
  scene.fog = new THREE.FogExp2(BG, 0.0018);
  const camera = new THREE.PerspectiveCamera(55, 1, 1, 5000);

  // vignette + parchment tint plane behind everything (subtle depth)
  scene.add(new THREE.AmbientLight(0xffe8c0, 0.9));
  const keyLight = new THREE.PointLight(0xffd98a, 1.2, 0, 1.6);
  keyLight.position.set(300, 400, 500);
  scene.add(keyLight);

  const world = new THREE.Group();      // rotates as one armillary sphere
  scene.add(world);

  /* --------------------------------------------------- sprite textures */
  function glowTexture() {
    const c = document.createElement("canvas");
    c.width = c.height = 128;
    const g = c.getContext("2d");
    const grad = g.createRadialGradient(64, 64, 4, 64, 64, 64);
    grad.addColorStop(0.0, "rgba(255,255,255,1)");
    grad.addColorStop(0.18, "rgba(255,240,200,.95)");
    grad.addColorStop(0.45, "rgba(255,220,140,.35)");
    grad.addColorStop(1.0, "rgba(255,220,140,0)");
    g.fillStyle = grad;
    g.fillRect(0, 0, 128, 128);
    return new THREE.CanvasTexture(c);
  }
  const GLOW_TEX = glowTexture();

  function labelSprite(text, color) {
    const pad = 8, fs = 26;
    const c = document.createElement("canvas");
    const g = c.getContext("2d");
    g.font = `${fs}px Palatino, Georgia, serif`;
    c.width = Math.min(g.measureText(text).width + pad * 2, 520);
    c.height = fs + pad * 2;
    g.font = `${fs}px Palatino, Georgia, serif`;
    g.shadowColor = "rgba(0,0,0,.9)"; g.shadowBlur = 6;
    g.fillStyle = color;
    g.fillText(text, pad, fs + pad / 2, c.width - pad * 2);
    const tex = new THREE.CanvasTexture(c);
    const mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false });
    const sp = new THREE.Sprite(mat);
    sp.scale.set(c.width * 0.3, c.height * 0.3, 1);
    return sp;
  }

  /* ------------------------------------------------------------- state */
  let nodes = [], edges = [], nodeById = new Map();
  let lineSeg = null, dust = null, particlesPts = null, particles = [];
  let hovered = null, selected = null, draggedNode = null;
  let physicsOn = true, heat = 1;
  const IDLE_HEAT = reducedMotion ? 0 : 0.015;
  let lastInteraction = 0;

  // camera rig: spherical orbit around a target
  const rig = {
    target: new THREE.Vector3(0, 0, 0), tTarget: new THREE.Vector3(0, 0, 0),
    theta: 0.5, phi: 1.15, dist: 620, tDist: 620, tTheta: 0.5, tPhi: 1.15,
  };
  function applyCamera() {
    rig.theta += (rig.tTheta - rig.theta) * 0.14;
    rig.phi += (rig.tPhi - rig.phi) * 0.14;
    rig.dist += (rig.tDist - rig.dist) * 0.14;
    rig.target.lerp(rig.tTarget, 0.14);
    const sp = Math.sin(rig.phi), cp = Math.cos(rig.phi);
    camera.position.set(
      rig.target.x + rig.dist * sp * Math.sin(rig.theta),
      rig.target.y + rig.dist * cp,
      rig.target.z + rig.dist * sp * Math.cos(rig.theta));
    camera.lookAt(rig.target);
  }

  /* ----------------------------------------------------------- physics */
  const PHYS = { repulsion: 26000, spring: 0.06, springLen: 70, center: 0.012, damping: 0.86 };
  function step() {
    if (!physicsOn && !draggedNode) { heat = Math.max(heat * 0.9, 0); return; }
    const a = Math.max(heat, IDLE_HEAT);
    for (let i = 0; i < nodes.length; i++) {
      const na = nodes[i];
      for (let j = i + 1; j < nodes.length; j++) {
        const nb = nodes[j];
        let dx = na.p.x - nb.p.x, dy = na.p.y - nb.p.y, dz = na.p.z - nb.p.z;
        let d2 = dx * dx + dy * dy + dz * dz;
        if (d2 < 1) { dx = Math.random() - .5; dy = Math.random() - .5; dz = Math.random() - .5; d2 = 1; }
        if (d2 > 360000) continue;
        const f = (PHYS.repulsion / d2) * a, d = Math.sqrt(d2);
        dx /= d; dy /= d; dz /= d;
        na.v.x += dx * f; na.v.y += dy * f; na.v.z += dz * f;
        nb.v.x -= dx * f; nb.v.y -= dy * f; nb.v.z -= dz * f;
      }
    }
    for (const e of edges) {
      const dx = e.b.p.x - e.a.p.x, dy = e.b.p.y - e.a.p.y, dz = e.b.p.z - e.a.p.z;
      const d = Math.max(Math.sqrt(dx * dx + dy * dy + dz * dz), 1);
      const f = PHYS.spring * (d - PHYS.springLen / (0.35 + e.w)) * a / d;
      e.a.v.x += dx * f; e.a.v.y += dy * f; e.a.v.z += dz * f;
      e.b.v.x -= dx * f; e.b.v.y -= dy * f; e.b.v.z -= dz * f;
    }
    for (const n of nodes) {
      n.v.x -= n.p.x * PHYS.center * a;
      n.v.y -= n.p.y * PHYS.center * a;
      n.v.z -= n.p.z * PHYS.center * a;
      if (n !== draggedNode) {
        n.v.multiplyScalar(PHYS.damping);
        n.p.add(n.v);
      }
    }
    heat *= 0.992;
  }
  const reheat = (a) => { heat = Math.min(1, Math.max(heat, a)); };

  /* ---------------------------------------------------------- data load */
  function load() {
    const p = projectSel ? projectSel.value : "";
    const w = weightSlider ? weightSlider.value : 0.35;
    fetch("/api/ui/graph?min_weight=" + w + (p ? "&project=" + encodeURIComponent(p) : ""))
      .then((r) => r.json()).then(build)
      .catch(() => { if (emptyMsg) emptyMsg.hidden = false; });
  }

  function clearWorld() {
    while (world.children.length) world.remove(world.children[0]);
    lineSeg = dust = particlesPts = null;
    particles = [];
  }

  function build(data) {
    const prev = nodeById;
    clearWorld();
    nodeById = new Map();

    nodes = (data.nodes || []).map((n, i) => {
      const old = prev.get(n.id);
      const golden = i * 2.39996;                     // fibonacci sphere seed
      const r = 190 + Math.random() * 120;
      const y = (i / Math.max(data.nodes.length - 1, 1)) * 2 - 1;
      const rr = Math.sqrt(1 - y * y);
      const node = {
        id: n.id, label: n.label || "", type: n.type, project: n.project,
        importance: n.importance || 3, degree: n.degree || 0,
        p: old ? old.p.clone() : new THREE.Vector3(Math.cos(golden) * rr * r, y * r, Math.sin(golden) * rr * r),
        v: new THREE.Vector3(),
        color: projectColor3(n.project),
        size: 9 + Math.min(26, Math.sqrt(n.degree || 0) * 6) + (n.importance || 3) * 1.2,
        adj: [],
      };
      nodeById.set(node.id, node);
      return node;
    });
    edges = (data.edges || [])
      .filter((e) => nodeById.has(e.src) && nodeById.has(e.dst))
      .map((e) => ({ a: nodeById.get(e.src), b: nodeById.get(e.dst), w: e.w, kinds: e.kinds }));
    for (const e of edges) { e.a.adj.push(e); e.b.adj.push(e); }

    // — node sprites
    for (const n of nodes) {
      const mat = new THREE.SpriteMaterial({
        map: GLOW_TEX, color: n.color, transparent: true,
        blending: THREE.AdditiveBlending, depthWrite: false,
      });
      n.sprite = new THREE.Sprite(mat);
      n.sprite.scale.set(n.size, n.size, 1);
      n.sprite.userData.node = n;
      world.add(n.sprite);

      // armillary ring for gravity wells — da Vinci instrument feel
      if (n.degree >= 3) {
        const ring = new THREE.Mesh(
          new THREE.TorusGeometry(n.size * 0.75, 0.35, 6, 48),
          new THREE.MeshBasicMaterial({ color: GOLD, transparent: true, opacity: 0.5 }));
        ring.rotation.x = Math.random() * Math.PI;
        ring.rotation.y = Math.random() * Math.PI;
        ring.userData.spin = (Math.random() - 0.5) * 0.01;
        n.ring = ring;
        world.add(ring);
        if (n.degree >= 6) {                        // second ring, tilted
          const r2 = ring.clone();
          r2.geometry = new THREE.TorusGeometry(n.size * 1.05, 0.3, 6, 48);
          r2.rotation.x += Math.PI / 2.3;
          r2.userData.spin = -ring.userData.spin * 1.4;
          n.ring2 = r2;
          world.add(r2);
        }
      }
      // labels for the most gravitational memories
      if (n.degree >= 2.5) {
        n.labelSp = labelSprite(n.label.slice(0, 40), "#e6d3a3");
        world.add(n.labelSp);
      }
    }

    // — edges as one LineSegments buffer (positions + vertex colours per frame)
    if (edges.length) {
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(edges.length * 6), 3));
      geo.setAttribute("color", new THREE.BufferAttribute(new Float32Array(edges.length * 6), 3));
      lineSeg = new THREE.LineSegments(geo, new THREE.LineBasicMaterial({
        vertexColors: true, transparent: true, opacity: 0.55,
        blending: THREE.AdditiveBlending, depthWrite: false,
      }));
      world.add(lineSeg);
    }

    // — library dust drifting through the light
    if (!reducedMotion) {
      const N = 350, pos = new Float32Array(N * 3), seed = [];
      for (let i = 0; i < N; i++) {
        pos[i * 3] = (Math.random() - .5) * 1400;
        pos[i * 3 + 1] = (Math.random() - .5) * 1000;
        pos[i * 3 + 2] = (Math.random() - .5) * 1400;
        seed.push(Math.random() * Math.PI * 2);
      }
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
      dust = new THREE.Points(g, new THREE.PointsMaterial({
        color: 0xd8c49a, size: 1.6, transparent: true, opacity: 0.35,
        blending: THREE.AdditiveBlending, depthWrite: false, sizeAttenuation: true,
      }));
      dust.userData.seed = seed;
      scene.add(dust);
    }

    // — particle pool for edge flows
    const P = 160, ppos = new Float32Array(P * 3);
    const pg = new THREE.BufferGeometry();
    pg.setAttribute("position", new THREE.BufferAttribute(ppos, 3));
    particlesPts = new THREE.Points(pg, new THREE.PointsMaterial({
      color: 0xfff0c8, size: 3.2, transparent: true, opacity: 0.95,
      blending: THREE.AdditiveBlending, depthWrite: false,
    }));
    particlesPts.frustumCulled = false;
    world.add(particlesPts);

    if (emptyMsg) emptyMsg.hidden = nodes.length > 0;
    buildLegend();
    selected = (selected && nodeById.get(selected.id)) || null;
    updateInfoCard();
    heat = 1;
  }

  function buildLegend() {
    if (!legend) return;
    const projects = [...new Set(nodes.map((n) => n.project))].slice(0, 12);
    legend.innerHTML = projects.map((p) =>
      `<span><i style="background:#${projectColor3(p).getHexString()};color:#${projectColor3(p).getHexString()}"></i>${esc(p)}</span>`).join("");
  }
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  /* ------------------------------------------------------ per-frame gfx */
  const _tmpC = new THREE.Color();
  function updateGraphics(t) {
    const focus = draggedNode || hovered || selected;
    const neigh = focus ? new Set(focus.adj.map((e) => (e.a === focus ? e.b : e.a))) : null;

    for (const n of nodes) {
      n.sprite.position.copy(n.p);
      const breathe = reducedMotion ? 1 : 1 + 0.05 * Math.sin(t * 1.6 + n.p.x * 0.01);
      const dim = focus && n !== focus && !neigh.has(n);
      const s = n.size * breathe * (n === focus ? 1.5 : 1);
      n.sprite.scale.set(s, s, 1);
      n.sprite.material.opacity = dim ? 0.14 : 1;
      if (n.ring) {
        n.ring.position.copy(n.p);
        n.ring.rotation.z += n.ring.userData.spin;
        n.ring.material.opacity = dim ? 0.08 : (n === focus ? 0.9 : 0.5);
      }
      if (n.ring2) {
        n.ring2.position.copy(n.p);
        n.ring2.rotation.z += n.ring2.userData.spin;
        n.ring2.material.opacity = dim ? 0.06 : 0.4;
      }
      if (n.labelSp) {
        n.labelSp.position.set(n.p.x, n.p.y + n.size * 0.9 + 6, n.p.z);
        n.labelSp.material.opacity = dim ? 0.1 : 0.9;
      }
    }

    if (lineSeg) {
      const pos = lineSeg.geometry.attributes.position.array;
      const col = lineSeg.geometry.attributes.color.array;
      edges.forEach((e, i) => {
        pos[i * 6] = e.a.p.x; pos[i * 6 + 1] = e.a.p.y; pos[i * 6 + 2] = e.a.p.z;
        pos[i * 6 + 3] = e.b.p.x; pos[i * 6 + 4] = e.b.p.y; pos[i * 6 + 5] = e.b.p.z;
        const active = focus && (e.a === focus || e.b === focus);
        const k = focus ? (active ? 1.4 : 0.12) : (0.35 + e.w * 0.6);
        _tmpC.copy(e.a.color).multiplyScalar(k);
        col[i * 6] = _tmpC.r; col[i * 6 + 1] = _tmpC.g; col[i * 6 + 2] = _tmpC.b;
        _tmpC.copy(e.b.color).multiplyScalar(k);
        col[i * 6 + 3] = _tmpC.r; col[i * 6 + 4] = _tmpC.g; col[i * 6 + 5] = _tmpC.b;
      });
      lineSeg.geometry.attributes.position.needsUpdate = true;
      lineSeg.geometry.attributes.color.needsUpdate = true;
    }

    // particles along focused edges
    if (!reducedMotion && focus && focus.adj) {
      for (const e of focus.adj) {
        if (particles.length < 150 && Math.random() < 0.12 * e.w) {
          particles.push({ e, t: 0, sp: 0.008 + 0.014 * e.w, fromA: e.a === focus });
        }
      }
    }
    for (const p of particles) p.t += p.sp;
    particles = particles.filter((p) => p.t < 1);
    if (particlesPts) {
      const arr = particlesPts.geometry.attributes.position.array;
      let i = 0;
      for (const p of particles) {
        const a = p.fromA ? p.e.a : p.e.b, b = p.fromA ? p.e.b : p.e.a;
        arr[i++] = a.p.x + (b.p.x - a.p.x) * p.t;
        arr[i++] = a.p.y + (b.p.y - a.p.y) * p.t;
        arr[i++] = a.p.z + (b.p.z - a.p.z) * p.t;
      }
      for (; i < arr.length; i++) arr[i] = 99999;
      particlesPts.geometry.setDrawRange(0, particles.length);
      particlesPts.geometry.attributes.position.needsUpdate = true;
    }

    if (dust) {
      const arr = dust.geometry.attributes.position.array;
      const seed = dust.userData.seed;
      for (let i = 0; i < seed.length; i++) {
        arr[i * 3 + 1] += Math.sin(t * 0.3 + seed[i]) * 0.05;
        arr[i * 3] += Math.cos(t * 0.2 + seed[i]) * 0.04;
      }
      dust.geometry.attributes.position.needsUpdate = true;
    }

    // idle: the armillary sphere turns
    if (!reducedMotion && !draggedNode && performance.now() - lastInteraction > 6000) {
      world.rotation.y += 0.0008;
    }
  }

  /* ------------------------------------------------------- interaction */
  const ray = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  let mode = null;              // 'orbit' | 'pan' | 'node'
  let last = { x: 0, y: 0 }, downAt = { x: 0, y: 0 }, moved = 0;
  const dragPlane = new THREE.Plane();
  const _v3 = new THREE.Vector3();

  function pick(e) {
    const r = canvas.getBoundingClientRect();
    ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    ray.params.Sprite = { threshold: 6 };
    const hits = ray.intersectObjects(nodes.map((n) => n.sprite), false);
    return hits.length ? hits[0].object.userData.node : null;
  }
  function worldFromEvent(e, plane) {
    const r = canvas.getBoundingClientRect();
    ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    return ray.ray.intersectPlane(plane, _v3.clone());
  }

  canvas.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    canvas.setPointerCapture(e.pointerId);
    lastInteraction = performance.now();
    last = downAt = { x: e.clientX, y: e.clientY };
    moved = 0;
    const n = pick(e);
    if (n && e.button === 0) {
      mode = "node";
      draggedNode = n;
      const wp = n.p.clone().applyMatrix4(world.matrixWorld);
      dragPlane.setFromNormalAndCoplanarPoint(
        camera.getWorldDirection(new THREE.Vector3()).negate(), wp);
      reheat(0.4);
    } else if (e.button === 2 || e.shiftKey) {
      mode = "pan";
    } else {
      mode = "orbit";
    }
    canvas.classList.add("dragging");
  });
  canvas.addEventListener("contextmenu", (e) => e.preventDefault());

  canvas.addEventListener("pointermove", (e) => {
    const dx = e.clientX - last.x, dy = e.clientY - last.y;
    moved += Math.abs(dx) + Math.abs(dy);
    if (mode === "node" && draggedNode) {
      lastInteraction = performance.now();
      const hit = worldFromEvent(e, dragPlane);
      if (hit) {
        world.updateMatrixWorld();
        const local = world.worldToLocal(hit.clone());
        draggedNode.v.copy(local.sub(draggedNode.p).multiplyScalar(0.5));
        draggedNode.p.add(draggedNode.v);
        reheat(0.35);
      }
    } else if (mode === "orbit") {
      lastInteraction = performance.now();
      rig.tTheta -= dx * 0.005;
      rig.tPhi = Math.min(Math.PI - 0.15, Math.max(0.15, rig.tPhi - dy * 0.005));
    } else if (mode === "pan") {
      lastInteraction = performance.now();
      const scale = rig.dist * 0.0011;
      const right = new THREE.Vector3().setFromMatrixColumn(camera.matrix, 0);
      const up = new THREE.Vector3().setFromMatrixColumn(camera.matrix, 1);
      rig.tTarget.addScaledVector(right, -dx * scale).addScaledVector(up, dy * scale);
    } else {
      const n = pick(e);
      if (n !== hovered) hovered = n;
      if (tooltip) {
        if (n && n !== selected) {
          const r = canvas.getBoundingClientRect();
          tooltip.hidden = false;
          tooltip.style.left = Math.min(e.clientX - r.left + 16, r.width - 290) + "px";
          tooltip.style.top = (e.clientY - r.top + 16) + "px";
          tooltip.innerHTML =
            `<span class="chip type-${esc(n.type)}">${esc(n.type)}</span> ` +
            `<strong>${esc(n.label)}</strong><br>` +
            `<span class="hint">${esc(n.project)} · gravity ${n.degree.toFixed(1)} · imp ${n.importance}</span>`;
        } else tooltip.hidden = true;
      }
    }
    last = { x: e.clientX, y: e.clientY };
  });

  function endPointer(e) {
    if (moved < 6 && mode !== null) {
      const n = pick(e);
      selected = (n === selected) ? null : n;
      updateInfoCard();
      if (selected) reheat(0.1);
    }
    if (draggedNode) reheat(0.3);
    draggedNode = null; mode = null;
    canvas.classList.remove("dragging");
    try { canvas.releasePointerCapture(e.pointerId); } catch (_) {}
  }
  canvas.addEventListener("pointerup", endPointer);
  canvas.addEventListener("pointercancel", endPointer);

  canvas.addEventListener("dblclick", (e) => {
    const n = pick(e);
    if (n) window.location.href = "/ui/memory/" + encodeURIComponent(n.id);
  });

  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    lastInteraction = performance.now();
    rig.tDist = Math.min(2200, Math.max(120, rig.tDist * (e.deltaY > 0 ? 1.12 : 1 / 1.12)));
  }, { passive: false });

  document.addEventListener("keydown", (e) => {
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) return;
    if (e.key === "Escape") { selected = null; updateInfoCard(); }
    if (e.key === " ") { e.preventDefault(); togglePhysics(); }
    if (e.key.toLowerCase() === "r") { rig.tTarget.set(0, 0, 0); rig.tDist = 620; }
  });

  /* --------------------------------------------------------- info card */
  function updateInfoCard() {
    if (!infoCard) return;
    if (!selected) { infoCard.hidden = true; return; }
    const n = selected;
    infoCard.hidden = false;
    infoCard.innerHTML =
      `<div class="g-info-head">
         <span class="chip type-${esc(n.type)}">${esc(n.type)}</span>
         <span class="g-info-close" id="g-info-close" title="Close (Esc)">×</span>
       </div>
       <div class="g-info-title">${esc(n.label)}</div>
       <div class="hint" style="margin:6px 0 10px">
         ${esc(n.project)} · gravity ${n.degree.toFixed(1)} · importance ${n.importance}/5 · ${n.adj.length} links
       </div>
       <a class="btn g-info-open" href="/ui/memory/${encodeURIComponent(n.id)}">Open memory →</a>`;
    const close = $("g-info-close");
    if (close) close.onclick = () => { selected = null; updateInfoCard(); };
  }

  /* ----------------------------------------------------------- controls */
  function togglePhysics() {
    physicsOn = !physicsOn;
    if (physicsOn) reheat(0.5);
    if (physicsBtn) physicsBtn.textContent = physicsOn ? "❚❚ physics" : "▶ physics";
  }
  if (physicsBtn) physicsBtn.addEventListener("click", togglePhysics);

  if (projectSel) projectSel.addEventListener("change", () => {
    const u = new URL(window.location);
    if (projectSel.value) u.searchParams.set("project", projectSel.value);
    else u.searchParams.delete("project");
    history.replaceState(null, "", u);
    selected = null; updateInfoCard();
    load();
  });
  if (weightSlider) {
    weightSlider.addEventListener("input", () => {
      if (weightVal) weightVal.textContent = Number(weightSlider.value).toFixed(2);
    });
    weightSlider.addEventListener("change", load);
  }
  if (nodeSearch) nodeSearch.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    const q = nodeSearch.value.trim().toLowerCase();
    if (!q) return;
    const match = nodes.find((n) => n.label.toLowerCase().includes(q)) ||
                  nodes.find((n) => n.project.toLowerCase().includes(q));
    if (!match) return;
    selected = match;
    updateInfoCard();
    world.updateMatrixWorld();
    rig.tTarget.copy(match.p.clone().applyMatrix4(world.matrixWorld));
    rig.tDist = 220;
    reheat(0.08);
  });

  /* -------------------------------------------------------------- loop */
  function resize() {
    const r = canvas.parentElement.getBoundingClientRect();
    renderer.setSize(r.width, r.height, false);
    camera.aspect = r.width / r.height;
    camera.updateProjectionMatrix();
  }
  new ResizeObserver(resize).observe(canvas.parentElement);

  const t0 = performance.now();
  function frame() {
    const t = (performance.now() - t0) / 1000;
    step();
    updateGraphics(t);
    applyCamera();
    renderer.render(scene, camera);
    requestAnimationFrame(frame);
  }

  resize();
  load();
  requestAnimationFrame(frame);
}
