/* MemoryBrain graph — "vault brain" renderer, v2.1.
   Canvas 2D, zero dependencies, fully offline.
   Feel: a living web you can grab. Glow, depth starfield with parallax,
   particles flowing along links, elastic physics that never quite sleeps,
   pointer-capture dragging with inertia and flick, animated camera,
   node selection with info card, in-graph search. */
(function () {
  "use strict";

  const canvas = document.getElementById("graph");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const $ = (id) => document.getElementById(id);
  const tooltip = $("g-tooltip"), emptyMsg = $("g-empty"), legend = $("g-legend");
  const projectSel = $("g-project"), weightSlider = $("g-weight"), weightVal = $("g-weight-val");
  const nodeSearch = $("g-find"), physicsBtn = $("g-physics"), labelsBtn = $("g-labels");
  const infoCard = $("g-info");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ------------------------------------------------------------ config */
  const PHYS = {
    repulsion: 3200, spring: 0.045, springLen: 95,
    center: 0.010, damping: 0.86,
    idleHeat: reducedMotion ? 0 : 0.012,   // web never fully sleeps
    maxHeat: 1.0,
  };
  const STAR_LAYERS = [
    { n: 90, speed: 0.25, size: 1.0, alpha: 0.35 },
    { n: 50, speed: 0.55, size: 1.6, alpha: 0.5 },
  ];

  /* ------------------------------------------------------------- state */
  let nodes = [], edges = [], nodeById = new Map();
  let cam = { x: 0, y: 0, zoom: 1, tx: 0, ty: 0, tzoom: 1 }; // t* = targets, lerped
  let heat = 1;
  let hovered = null, selected = null, dragged = null;
  let panning = false, panVel = { x: 0, y: 0 };
  let pointer = { x: 0, y: 0, downX: 0, downY: 0, moved: 0 };
  let physicsOn = true, labelsOn = true;
  let particles = [];
  let stars = [];
  let tick0 = performance.now();

  /* --------------------------------------------------------- utilities */
  function mulberry32(a) {          // deterministic PRNG for the starfield
    return function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function viewSize() {
    const r = canvas.parentElement.getBoundingClientRect();
    return { w: r.width, h: r.height };
  }
  function toWorld(px, py) {
    return { x: (px - cam.x) / cam.zoom, y: (py - cam.y) / cam.zoom };
  }
  function escHtml(s) {
    return String(s).replace(/[&<>"]/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }
  function nodeR(n) { return 4 + Math.min(11, Math.sqrt(n.degree || 0) * 2.2) + (n.importance || 3) * 0.5; }
  function reheat(a) { heat = Math.min(PHYS.maxHeat, Math.max(heat, a)); }

  /* ------------------------------------------------------------ sizing */
  function resize() {
    const { w, h } = viewSize();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    buildStars();
  }
  new ResizeObserver(resize).observe(canvas.parentElement);

  function buildStars() {
    const { w, h } = viewSize();
    stars = STAR_LAYERS.map((layer, li) => {
      const rnd = mulberry32(1234 + li * 999);
      return {
        ...layer,
        pts: Array.from({ length: layer.n }, () => ({
          x: rnd() * w * 2 - w * 0.5,
          y: rnd() * h * 2 - h * 0.5,
          tw: rnd() * Math.PI * 2,          // twinkle phase
        })),
      };
    });
  }

  /* ---------------------------------------------------------- data load */
  function load() {
    const p = projectSel ? projectSel.value : "";
    const w = weightSlider ? weightSlider.value : 0.35;
    fetch("/api/ui/graph?min_weight=" + w + (p ? "&project=" + encodeURIComponent(p) : ""))
      .then((r) => r.json())
      .then(init)
      .catch(() => { if (emptyMsg) emptyMsg.hidden = false; });
  }

  function init(data) {
    const { w, h } = viewSize();
    const prev = nodeById;
    nodeById = new Map();
    nodes = (data.nodes || []).map((n, i) => {
      const old = prev.get(n.id);
      const angle = (i / Math.max(data.nodes.length, 1)) * Math.PI * 2;
      const r = Math.min(w, h) * 0.32 * (0.35 + 0.65 * Math.random());
      const node = {
        id: n.id, label: n.label || "", type: n.type, project: n.project,
        importance: n.importance || 3, degree: n.degree || 0,
        x: old ? old.x : w / 2 + Math.cos(angle) * r,
        y: old ? old.y : h / 2 + Math.sin(angle) * r,
        vx: 0, vy: 0, phase: Math.random() * Math.PI * 2,
        color: window.projectColor(n.project),
      };
      nodeById.set(node.id, node);
      return node;
    });
    edges = (data.edges || [])
      .filter((e) => nodeById.has(e.src) && nodeById.has(e.dst))
      .map((e) => ({ a: nodeById.get(e.src), b: nodeById.get(e.dst), w: e.w, kinds: e.kinds }));
    for (const n of nodes) n.adj = [];
    for (const e of edges) { e.a.adj.push(e); e.b.adj.push(e); }
    if (emptyMsg) emptyMsg.hidden = nodes.length > 0;
    buildLegend();
    selected = selected && nodeById.get(selected.id) || null;
    updateInfoCard();
    heat = 1;
  }

  function buildLegend() {
    if (!legend) return;
    const projects = [...new Set(nodes.map((n) => n.project))].slice(0, 12);
    legend.innerHTML = projects.map((p) =>
      `<span><i style="background:${window.projectColor(p)}"></i>${escHtml(p)}</span>`).join("");
  }

  /* ----------------------------------------------------------- physics */
  function step() {
    if (!physicsOn && !dragged) { heat = Math.max(heat * 0.9, 0); return; }
    const { w, h } = viewSize();
    const cx = w / 2, cy = h / 2;
    const a = Math.max(heat, PHYS.idleHeat);

    for (let i = 0; i < nodes.length; i++) {
      const na = nodes[i];
      for (let j = i + 1; j < nodes.length; j++) {
        const nb = nodes[j];
        let dx = na.x - nb.x, dy = na.y - nb.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 1) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 1; }
        if (d2 > 260000) continue;
        const f = (PHYS.repulsion / d2) * a;
        const d = Math.sqrt(d2);
        dx /= d; dy /= d;
        na.vx += dx * f; na.vy += dy * f;
        nb.vx -= dx * f; nb.vy -= dy * f;
      }
    }
    for (const e of edges) {
      const dx = e.b.x - e.a.x, dy = e.b.y - e.a.y;
      const d = Math.max(Math.hypot(dx, dy), 1);
      const f = PHYS.spring * (d - PHYS.springLen / (0.4 + e.w)) * a / d;
      e.a.vx += dx * f; e.a.vy += dy * f;
      e.b.vx -= dx * f; e.b.vy -= dy * f;
    }
    for (const n of nodes) {
      n.vx += (cx - n.x) * PHYS.center * a;
      n.vy += (cy - n.y) * PHYS.center * a;
      if (n !== dragged) {
        n.vx *= PHYS.damping; n.vy *= PHYS.damping;
        n.x += n.vx; n.y += n.vy;
      }
    }
    heat *= 0.992;
  }

  /* --------------------------------------------------------- particles */
  function spawnParticles() {
    if (reducedMotion) return;
    const focus = dragged || hovered || selected;
    if (!focus || !focus.adj || particles.length > 90) return;
    for (const e of focus.adj) {
      if (Math.random() < 0.10 * e.w) {
        particles.push({ e, t: 0, speed: 0.006 + 0.012 * e.w, fromA: e.a === focus });
      }
    }
  }
  function stepParticles() {
    for (const p of particles) p.t += p.speed;
    particles = particles.filter((p) => p.t < 1);
  }

  /* ------------------------------------------------------------ render */
  function render(now) {
    const { w, h } = viewSize();
    const t = (now - tick0) / 1000;

    // smooth camera
    cam.x += (cam.tx - cam.x) * 0.18;
    cam.y += (cam.ty - cam.y) * 0.18;
    cam.zoom += (cam.tzoom - cam.zoom) * 0.18;

    ctx.clearRect(0, 0, w, h);

    // -- starfield with parallax
    for (const layer of stars) {
      ctx.fillStyle = "#7d8db1";
      for (const s of layer.pts) {
        const sx = ((s.x + cam.x * layer.speed) % (w * 1.5) + w * 1.5) % (w * 1.5) - w * 0.25;
        const sy = ((s.y + cam.y * layer.speed) % (h * 1.5) + h * 1.5) % (h * 1.5) - h * 0.25;
        const tw = reducedMotion ? 0.6 : 0.35 + 0.35 * Math.sin(t * 1.4 + s.tw);
        ctx.globalAlpha = layer.alpha * tw;
        ctx.fillRect(sx, sy, layer.size, layer.size);
      }
    }
    ctx.globalAlpha = 1;

    ctx.save();
    ctx.translate(cam.x, cam.y);
    ctx.scale(cam.zoom, cam.zoom);

    const focus = dragged || hovered || selected;
    const neighbours = focus ? new Set(focus.adj.map((e) => (e.a === focus ? e.b : e.a))) : null;

    // -- edges
    for (const e of edges) {
      const active = focus && (e.a === focus || e.b === focus);
      if (focus && !active) { ctx.strokeStyle = "rgba(138,148,166,.05)"; }
      else {
        const g = ctx.createLinearGradient(e.a.x, e.a.y, e.b.x, e.b.y);
        g.addColorStop(0, e.a.color); g.addColorStop(1, e.b.color);
        ctx.strokeStyle = g;
        ctx.globalAlpha = active ? 0.85 : 0.28 + e.w * 0.25;
      }
      ctx.lineWidth = (active ? 1.2 + e.w * 2.6 : 0.5 + e.w * 2.0) / cam.zoom;
      ctx.beginPath();
      ctx.moveTo(e.a.x, e.a.y);
      ctx.lineTo(e.b.x, e.b.y);
      ctx.stroke();
      ctx.globalAlpha = 1;
    }

    // -- particles flowing along focused edges
    for (const p of particles) {
      const { a, b } = { a: p.fromA ? p.e.a : p.e.b, b: p.fromA ? p.e.b : p.e.a };
      const px = a.x + (b.x - a.x) * p.t, py = a.y + (b.y - a.y) * p.t;
      ctx.fillStyle = "#cfe0ff";
      ctx.globalAlpha = 0.9 * (1 - p.t);
      ctx.beginPath();
      ctx.arc(px, py, 1.6 / cam.zoom + 0.6, 0, Math.PI * 2);
      ctx.fill();
      ctx.globalAlpha = 1;
    }

    // -- nodes: glow halo + gradient core + breathing
    for (const n of nodes) {
      const dim = focus && n !== focus && !neighbours.has(n);
      const r = nodeR(n) * (reducedMotion ? 1 : 1 + 0.04 * Math.sin(t * 1.8 + n.phase));
      const isFocus = n === focus;

      ctx.globalAlpha = dim ? 0.15 : 1;
      // halo
      const halo = ctx.createRadialGradient(n.x, n.y, r * 0.4, n.x, n.y, r * (isFocus ? 4.2 : 2.6));
      halo.addColorStop(0, n.color + "");
      halo.addColorStop(1, "rgba(0,0,0,0)");
      ctx.globalAlpha = (dim ? 0.05 : isFocus ? 0.5 : 0.18);
      ctx.fillStyle = halo;
      ctx.beginPath();
      ctx.arc(n.x, n.y, r * (isFocus ? 4.2 : 2.6), 0, Math.PI * 2);
      ctx.fill();

      // core
      ctx.globalAlpha = dim ? 0.2 : 1;
      const core = ctx.createRadialGradient(n.x - r * 0.3, n.y - r * 0.3, r * 0.1, n.x, n.y, r);
      core.addColorStop(0, "#ffffff");
      core.addColorStop(0.25, n.color);
      core.addColorStop(1, n.color);
      ctx.fillStyle = core;
      ctx.beginPath();
      ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
      ctx.fill();

      if (n === selected) {
        ctx.strokeStyle = "#dce3ee";
        ctx.lineWidth = 1.6 / cam.zoom;
        ctx.setLineDash([4 / cam.zoom, 3 / cam.zoom]);
        ctx.lineDashOffset = -t * 12;
        ctx.beginPath();
        ctx.arc(n.x, n.y, r + 5 / cam.zoom, 0, Math.PI * 2);
        ctx.stroke();
        ctx.setLineDash([]);
      }

      // label
      if (labelsOn && (cam.zoom > 0.85 || isFocus || (neighbours && neighbours.has(n)))) {
        if (n.degree > 1.5 || isFocus || (neighbours && neighbours.has(n)) || cam.zoom > 1.6) {
          ctx.fillStyle = dim ? "rgba(138,148,166,.18)" : "rgba(220,227,238,.88)";
          ctx.font = `${11 / cam.zoom}px Inter, system-ui, sans-serif`;
          ctx.fillText(n.label.slice(0, 36), n.x + r + 5 / cam.zoom, n.y + 3 / cam.zoom);
        }
      }
      ctx.globalAlpha = 1;
    }
    ctx.restore();
  }

  /* --------------------------------------------------------- main loop */
  function frame(now) {
    step();
    spawnParticles();
    stepParticles();
    // pan inertia
    if (!panning && (Math.abs(panVel.x) > 0.05 || Math.abs(panVel.y) > 0.05)) {
      cam.tx += panVel.x; cam.ty += panVel.y;
      panVel.x *= 0.92; panVel.y *= 0.92;
    }
    render(now);
    requestAnimationFrame(frame);
  }

  /* ------------------------------------------------------- interaction */
  function pick(px, py) {
    const p = toWorld(px, py);
    for (let i = nodes.length - 1; i >= 0; i--) {
      const n = nodes[i];
      const rr = nodeR(n) + 6 / cam.zoom;
      const dx = p.x - n.x, dy = p.y - n.y;
      if (dx * dx + dy * dy <= rr * rr) return n;
    }
    return null;
  }
  function localXY(e) {
    const r = canvas.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }

  canvas.addEventListener("pointerdown", (e) => {
    canvas.setPointerCapture(e.pointerId);
    const m = localXY(e);
    pointer = { x: m.x, y: m.y, downX: m.x, downY: m.y, moved: 0 };
    dragged = pick(m.x, m.y);
    panning = !dragged;
    panVel = { x: 0, y: 0 };
    canvas.classList.add("dragging");
    if (dragged) reheat(0.4);
  });

  canvas.addEventListener("pointermove", (e) => {
    const m = localXY(e);
    const dx = m.x - pointer.x, dy = m.y - pointer.y;
    pointer.moved += Math.abs(dx) + Math.abs(dy);

    if (dragged) {
      const p = toWorld(m.x, m.y);
      // elastic grab: node chases the pointer, dragging the web with it
      dragged.vx = (p.x - dragged.x) * 0.55;
      dragged.vy = (p.y - dragged.y) * 0.55;
      dragged.x += dragged.vx; dragged.y += dragged.vy;
      reheat(0.35);
    } else if (panning) {
      cam.tx += dx; cam.ty += dy;
      cam.x += dx; cam.y += dy;
      panVel = { x: dx, y: dy };
    } else {
      const n = pick(m.x, m.y);
      if (n !== hovered) { hovered = n; if (n) reheat(0.06); }
      if (tooltip) {
        if (n && n !== selected) {
          tooltip.hidden = false;
          tooltip.style.left = Math.min(m.x + 16, viewSize().w - 280) + "px";
          tooltip.style.top = (m.y + 16) + "px";
          tooltip.innerHTML =
            `<span class="chip type-${escHtml(n.type)}">${escHtml(n.type)}</span> ` +
            `<strong>${escHtml(n.label)}</strong><br>` +
            `<span class="hint">${escHtml(n.project)} · gravity ${n.degree.toFixed(1)} · imp ${n.importance}</span>`;
        } else tooltip.hidden = true;
      }
    }
    pointer.x = m.x; pointer.y = m.y;
  });

  function endPointer(e) {
    if (dragged) {
      // flick: keep release velocity, let the web swing
      reheat(0.3);
    }
    if (pointer.moved < 6) {                    // click, not drag
      const n = pick(pointer.x, pointer.y);
      selected = (n === selected) ? null : n;
      updateInfoCard();
      if (selected) reheat(0.12);
    }
    dragged = null; panning = false;
    canvas.classList.remove("dragging");
    try { canvas.releasePointerCapture(e.pointerId); } catch (_) {}
  }
  canvas.addEventListener("pointerup", endPointer);
  canvas.addEventListener("pointercancel", endPointer);

  canvas.addEventListener("dblclick", (e) => {
    const m = localXY(e);
    const n = pick(m.x, m.y);
    if (n) window.location.href = "/ui/memory/" + encodeURIComponent(n.id);
  });

  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    const m = localXY(e);
    const factor = e.deltaY < 0 ? 1.14 : 1 / 1.14;
    const z = Math.min(4.5, Math.max(0.15, cam.tzoom * factor));
    cam.tx = m.x - (m.x - cam.tx) * (z / cam.tzoom);
    cam.ty = m.y - (m.y - cam.ty) * (z / cam.tzoom);
    cam.tzoom = z;
  }, { passive: false });

  document.addEventListener("keydown", (e) => {
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) return;
    if (e.key === "Escape") { selected = null; updateInfoCard(); }
    if (e.key === " ") { e.preventDefault(); togglePhysics(); }
  });

  /* --------------------------------------------------------- info card */
  function updateInfoCard() {
    if (!infoCard) return;
    if (!selected) { infoCard.hidden = true; return; }
    const n = selected;
    infoCard.hidden = false;
    infoCard.innerHTML =
      `<div class="g-info-head">
         <span class="chip type-${escHtml(n.type)}">${escHtml(n.type)}</span>
         <span class="g-info-close" id="g-info-close" title="Close (Esc)">×</span>
       </div>
       <div class="g-info-title">${escHtml(n.label)}</div>
       <div class="hint" style="margin:6px 0 10px">
         <span class="proj-dot" style="background:${n.color}"></span> ${escHtml(n.project)}
         · gravity ${n.degree.toFixed(1)} · importance ${n.importance}/5
         · ${n.adj ? n.adj.length : 0} links
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
  if (labelsBtn) labelsBtn.addEventListener("click", () => {
    labelsOn = !labelsOn;
    labelsBtn.classList.toggle("off", !labelsOn);
  });

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

  // in-graph search: fly the camera to the best label match
  if (nodeSearch) nodeSearch.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    const q = nodeSearch.value.trim().toLowerCase();
    if (!q) return;
    const match = nodes.find((n) => n.label.toLowerCase().includes(q)) ||
                  nodes.find((n) => n.project.toLowerCase().includes(q));
    if (!match) return;
    const { w, h } = viewSize();
    selected = match;
    updateInfoCard();
    cam.tzoom = Math.max(cam.tzoom, 1.4);
    cam.tx = w / 2 - match.x * cam.tzoom;
    cam.ty = h / 2 - match.y * cam.tzoom;
    reheat(0.1);
  });

  /* -------------------------------------------------------------- boot */
  resize();
  load();
  requestAnimationFrame(frame);
})();
