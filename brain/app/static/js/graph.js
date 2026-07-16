/* MemoryBrain graph view — custom canvas force-directed layout.
   No libraries. Comfortable at the 150–500 node scale the API serves. */
(function () {
  "use strict";

  const canvas = document.getElementById("graph");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const tooltip = document.getElementById("g-tooltip");
  const emptyMsg = document.getElementById("g-empty");
  const legend = document.getElementById("g-legend");
  const projectSel = document.getElementById("g-project");
  const weightSlider = document.getElementById("g-weight");
  const weightVal = document.getElementById("g-weight-val");

  let nodes = [], edges = [], nodeById = new Map();
  let cam = { x: 0, y: 0, zoom: 1 };
  let alpha = 0;                       // simulation heat, cools to 0
  let hovered = null, dragged = null, panning = false;
  let last = { x: 0, y: 0 };
  let raf = null;

  /* ------------------------------------------------ sizing (HiDPI aware) */
  function resize() {
    const r = canvas.parentElement.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = r.width * dpr;
    canvas.height = r.height * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    draw();
  }
  window.addEventListener("resize", resize);

  function viewSize() {
    const r = canvas.parentElement.getBoundingClientRect();
    return { w: r.width, h: r.height };
  }

  /* --------------------------------------------------------- data load */
  function load() {
    const p = projectSel ? projectSel.value : "";
    const w = weightSlider ? weightSlider.value : 0.35;
    const url = "/api/ui/graph?min_weight=" + w +
      (p ? "&project=" + encodeURIComponent(p) : "");
    fetch(url)
      .then(function (r) { return r.json(); })
      .then(init)
      .catch(function () { emptyMsg.hidden = false; });
  }

  function init(data) {
    const { w, h } = viewSize();
    const prev = nodeById;
    nodeById = new Map();
    nodes = (data.nodes || []).map(function (n, i) {
      const old = prev.get(n.id);
      const angle = (i / Math.max(data.nodes.length, 1)) * Math.PI * 2;
      const r = Math.min(w, h) * 0.33 * (0.4 + 0.6 * Math.random());
      const node = {
        id: n.id, label: n.label || "", type: n.type, project: n.project,
        importance: n.importance || 1, degree: n.degree || 0,
        x: old ? old.x : w / 2 + Math.cos(angle) * r,
        y: old ? old.y : h / 2 + Math.sin(angle) * r,
        vx: 0, vy: 0,
        radius: 4 + Math.min(10, Math.sqrt(n.degree || 0) * 2.2),
        color: window.projectColor(n.project),
      };
      nodeById.set(node.id, node);
      return node;
    });
    edges = (data.edges || []).filter(function (e) {
      return nodeById.has(e.src) && nodeById.has(e.dst);
    }).map(function (e) {
      return { a: nodeById.get(e.src), b: nodeById.get(e.dst), w: e.w, kinds: e.kinds };
    });
    emptyMsg.hidden = nodes.length > 0;
    buildLegend();
    alpha = 1;
    if (!raf) tick();
  }

  function buildLegend() {
    const projects = [...new Set(nodes.map(function (n) { return n.project; }))].slice(0, 12);
    legend.innerHTML = projects.map(function (p) {
      return "<span><i style='background:" + window.projectColor(p) + "'></i>" + p + "</span>";
    }).join("");
  }

  /* ----------------------------------------------------- force physics */
  const REPULSION = 2600, SPRING = 0.04, SPRING_LEN = 90;
  const CENTER_PULL = 0.012, DAMPING = 0.85, COOL = 0.995, MIN_ALPHA = 0.005;

  function step() {
    const { w, h } = viewSize();
    const cx = w / 2, cy = h / 2;

    // O(n^2) repulsion — fine at <=500 nodes, ~125k pair ops per frame
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j];
        let dx = a.x - b.x, dy = a.y - b.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 1) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 1; }
        if (d2 > 250000) continue;                 // 500px cutoff
        const f = (REPULSION / d2) * alpha;
        const d = Math.sqrt(d2);
        dx /= d; dy /= d;
        a.vx += dx * f; a.vy += dy * f;
        b.vx -= dx * f; b.vy -= dy * f;
      }
    }
    // springs along edges; stronger edges pull tighter
    for (const e of edges) {
      const dx = e.b.x - e.a.x, dy = e.b.y - e.a.y;
      const d = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
      const f = SPRING * (d - SPRING_LEN / (0.4 + e.w)) * alpha / d;
      e.a.vx += dx * f; e.a.vy += dy * f;
      e.b.vx -= dx * f; e.b.vy -= dy * f;
    }
    // gentle centering + integrate
    for (const n of nodes) {
      n.vx += (cx - n.x) * CENTER_PULL * alpha;
      n.vy += (cy - n.y) * CENTER_PULL * alpha;
      if (n !== dragged) {
        n.vx *= DAMPING; n.vy *= DAMPING;
        n.x += n.vx; n.y += n.vy;
      }
    }
    alpha *= COOL;
  }

  function tick() {
    if (alpha > MIN_ALPHA) { step(); draw(); raf = requestAnimationFrame(tick); }
    else { draw(); raf = null; }
  }
  function reheat(a) { alpha = Math.max(alpha, a); if (!raf) tick(); }

  /* ------------------------------------------------------------ render */
  function draw() {
    const { w, h } = viewSize();
    ctx.clearRect(0, 0, w, h);
    ctx.save();
    ctx.translate(cam.x, cam.y);
    ctx.scale(cam.zoom, cam.zoom);

    const neighbours = hovered ? neighbourSet(hovered) : null;

    for (const e of edges) {
      const active = hovered && (e.a === hovered || e.b === hovered);
      ctx.strokeStyle = active ? "rgba(122,162,247,.75)"
        : hovered ? "rgba(138,148,166,.06)" : "rgba(138,148,166,.22)";
      ctx.lineWidth = (0.5 + e.w * 2.2) / cam.zoom;
      ctx.beginPath();
      ctx.moveTo(e.a.x, e.a.y);
      ctx.lineTo(e.b.x, e.b.y);
      ctx.stroke();
    }
    for (const n of nodes) {
      const dim = hovered && n !== hovered && !neighbours.has(n);
      ctx.globalAlpha = dim ? 0.18 : 1;
      ctx.fillStyle = n.color;
      ctx.beginPath();
      ctx.arc(n.x, n.y, n.radius, 0, Math.PI * 2);
      ctx.fill();
      if (n === hovered) {
        ctx.strokeStyle = "#dce3ee";
        ctx.lineWidth = 1.5 / cam.zoom;
        ctx.stroke();
      }
      if (cam.zoom > 0.9 && (n.degree > 2 || n === hovered || (neighbours && neighbours.has(n)))) {
        ctx.fillStyle = dim ? "rgba(138,148,166,.2)" : "rgba(220,227,238,.85)";
        ctx.font = (11 / cam.zoom) + "px Inter, system-ui, sans-serif";
        ctx.fillText(n.label.slice(0, 34), n.x + n.radius + 4, n.y + 3);
      }
      ctx.globalAlpha = 1;
    }
    ctx.restore();
  }

  function neighbourSet(node) {
    const s = new Set();
    for (const e of edges) {
      if (e.a === node) s.add(e.b);
      if (e.b === node) s.add(e.a);
    }
    return s;
  }

  /* ------------------------------------------------------- interaction */
  function toWorld(px, py) {
    return { x: (px - cam.x) / cam.zoom, y: (py - cam.y) / cam.zoom };
  }
  function pick(px, py) {
    const p = toWorld(px, py);
    for (let i = nodes.length - 1; i >= 0; i--) {
      const n = nodes[i];
      const dx = p.x - n.x, dy = p.y - n.y;
      if (dx * dx + dy * dy <= (n.radius + 4) * (n.radius + 4)) return n;
    }
    return null;
  }
  function mouse(e) {
    const r = canvas.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }

  canvas.addEventListener("mousedown", function (e) {
    const m = mouse(e);
    dragged = pick(m.x, m.y);
    panning = !dragged;
    last = m;
    canvas.classList.add("dragging");
  });

  canvas.addEventListener("mousemove", function (e) {
    const m = mouse(e);
    if (dragged) {
      const p = toWorld(m.x, m.y);
      dragged.x = p.x; dragged.y = p.y;
      dragged.vx = dragged.vy = 0;
      reheat(0.15);
    } else if (panning) {
      cam.x += m.x - last.x; cam.y += m.y - last.y;
      draw();
    } else {
      const n = pick(m.x, m.y);
      if (n !== hovered) { hovered = n; draw(); }
      if (n) {
        tooltip.hidden = false;
        tooltip.style.left = (m.x + 14) + "px";
        tooltip.style.top = (m.y + 14) + "px";
        tooltip.innerHTML =
          "<span class='chip type-" + n.type + "'>" + n.type + "</span> " +
          "<strong>" + escHtml(n.label) + "</strong><br>" +
          "<span class='hint'>" + escHtml(n.project) + " · " + n.degree.toFixed(1) +
          " link weight · imp " + n.importance + "</span>";
      } else tooltip.hidden = true;
    }
    last = m;
  });

  window.addEventListener("mouseup", function () {
    dragged = null; panning = false;
    canvas.classList.remove("dragging");
  });

  canvas.addEventListener("click", function (e) {
    if (Math.abs(e.movementX) > 3 || Math.abs(e.movementY) > 3) return;
    const m = mouse(e);
    const n = pick(m.x, m.y);
    if (n) window.location.href = "/ui/memory/" + encodeURIComponent(n.id);
  });

  canvas.addEventListener("wheel", function (e) {
    e.preventDefault();
    const m = mouse(e);
    const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
    const z = Math.min(4, Math.max(0.2, cam.zoom * factor));
    // zoom about the cursor
    cam.x = m.x - (m.x - cam.x) * (z / cam.zoom);
    cam.y = m.y - (m.y - cam.y) * (z / cam.zoom);
    cam.zoom = z;
    draw();
  }, { passive: false });

  function escHtml(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  /* ----------------------------------------------------------- controls */
  if (projectSel) projectSel.addEventListener("change", function () {
    const u = new URL(window.location);
    if (projectSel.value) u.searchParams.set("project", projectSel.value);
    else u.searchParams.delete("project");
    history.replaceState(null, "", u);
    load();
  });
  if (weightSlider) weightSlider.addEventListener("input", function () {
    weightVal.textContent = Number(weightSlider.value).toFixed(2);
  });
  if (weightSlider) weightSlider.addEventListener("change", load);

  resize();
  load();
})();
