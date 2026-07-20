/* Constellation lens — the memories as stars in the one living world.
   3D by default: the graph lives INSIDE the Nebula (nebula.js), the same
   full-screen scene that breathes behind every lens; entering the lens only
   grants it the pointer and brings it to full luminosity. 2D fallback (user
   toggle, remembered; automatic when WebGL is missing) uses the vendored
   force-graph canvas build inside a glass pane — its bundled three never
   touches the world's (design rule 5).
   Interaction contract in both modes: click selects → inspector + orb,
   drag moves, scroll zooms, hover shows details. */
"use strict";

(function () {
  var mode = null, el = null, statusEl = null;
  var graph2d = null;                     // ForceGraph instance (2D fallback)
  var selectedId = null, loadSeq = 0, lastGraph = null, inited = false;

  function nebula() {
    return (window.Nebula && window.Nebula.available) ? window.Nebula : null;
  }
  function status(msg) { if (statusEl) statusEl.textContent = msg; }

  function webglOK() {
    try {
      var c = document.createElement("canvas");
      return !!(c.getContext("webgl2") || c.getContext("webgl"));
    } catch (e) { return false; }
  }

  function savedMode() {
    var m = null;
    try { m = localStorage.getItem("nebula-cst-mode"); } catch (e) {}
    if (m !== "2d" && m !== "3d") m = "3d";                  // 3D is the default
    if (m === "3d" && !nebula()) m = "2d";
    if (m === "2d" && typeof ForceGraph !== "function") m = nebula() ? "3d" : null;
    return m;
  }

  var SELECT_COL = "#ffd98a";
  function nodeCol(n) {
    return n.id === selectedId ? SELECT_COL
      : "hsl(" + Atlas.hue(n.project) + " 70% 66%)";
  }
  function nodeShade(n) { return "hsl(" + Atlas.hue(n.project) + " 68% 38%)"; }
  function nodeSize(n) {
    return 3 + Math.sqrt(n.degree || 0) * 2.6 + (n.importance || 3) * 0.8;
  }
  function idOf(x) { return typeof x === "object" && x ? x.id : x; }
  function nodeOnSelection(l) {
    return selectedId
      && (idOf(l.source) === selectedId || idOf(l.target) === selectedId);
  }

  /* ------------------------------------------------------- selection flow */
  async function selectNode(id) {
    selectedId = id;
    var neb = nebula();
    if (neb) neb.select(id);
    if (graph2d) repaint2d();
    Atlas.inspect(id, null);
    var mem;
    try { mem = await Atlas.getJSON("/api/ui/memories/" + encodeURIComponent(id)); }
    catch (e) { return; }
    if (neb) neb.openOrb(mem, mode === "3d" ? id : null);
    else openOrbFallback(mem);
  }

  function deselect() {
    if (!selectedId) return;
    selectedId = null;
    var neb = nebula();
    if (neb) neb.deselect();
    if (graph2d) repaint2d();
  }

  /* ------------------------------------------------------------ 2D engine */
  function build2d() {
    el.innerHTML = "";
    el.classList.add("flat");
    graph2d = ForceGraph()(el)
      .backgroundColor("rgba(0,0,0,0)")
      .nodeId("id")
      .nodeVal(nodeSize)
      .nodeColor(nodeCol)
      .nodeLabel(function (n) {
        return '<div class="cst-tip"><strong>' + Atlas.esc(n.label || n.id)
          + '</strong><p class="quiet">' + Atlas.esc(n.project || "")
          + " · " + Atlas.esc(n.type || "") + " · importance " + (n.importance || "?")
          + " · gravity " + (n.degree != null ? n.degree.toFixed(1) : "?") + "</p></div>";
      })
      .linkColor(function (l) {
        return "rgba(214, 190, 148," + (nodeOnSelection(l)
          ? 0.9 : Math.min(0.7, 0.1 + (l.w || 0) * 0.55)) + ")";
      })
      .linkWidth(function (l) { return 0.4 + (l.w || 0) * 2.2; })
      .linkDirectionalParticles(function (l) {
        return (!Atlas.reducedMotion && nodeOnSelection(l)) ? 2 : 0;
      })
      .linkDirectionalParticleWidth(2.2)
      .linkDirectionalParticleSpeed(0.006)
      .enableNodeDrag(true)
      .onNodeClick(function (n) { selectNode(n.id); })
      .onBackgroundClick(function () { deselect(); Atlas.closeInspector(); })
      .cooldownTime(Atlas.reducedMotion ? 0 : 3000)
      .warmupTicks(Atlas.reducedMotion ? 60 : 0)
      .nodeCanvasObjectMode(function () { return "replace"; })
      .nodeCanvasObject(function (n, ctx) {
        var r = 4 * Math.sqrt(nodeSize(n));           // nodeRelSize default = 4
        var sel = n.id === selectedId;
        var col = nodeCol(n);
        ctx.save();
        ctx.shadowColor = sel ? SELECT_COL : col;
        ctx.shadowBlur = sel ? 24 : (n.importance >= 4 ? 15 : 8);
        var grad = ctx.createRadialGradient(
          n.x - r * 0.35, n.y - r * 0.4, r * 0.1, n.x, n.y, r);
        grad.addColorStop(0, "rgba(255,252,240,.95)");
        grad.addColorStop(0.35, col);
        grad.addColorStop(1, nodeShade(n));
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.arc(n.x, n.y, r, 0, 2 * Math.PI);
        ctx.fill();
        ctx.shadowBlur = 0;
        if (n.type === "fact" || n.type === "reference") ctx.setLineDash([2.5, 2.5]);
        ctx.strokeStyle = sel ? SELECT_COL : "rgba(230, 238, 255, .4)";
        ctx.lineWidth = sel ? 1.8 : 0.8;
        ctx.beginPath();
        ctx.arc(n.x, n.y, r + 1.4, 0, 2 * Math.PI);
        ctx.stroke();
        ctx.restore();
      })
      .nodePointerAreaPaint(function (n, color, ctx) {
        var r = 4 * Math.sqrt(nodeSize(n)) + 2;
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(n.x, n.y, r, 0, 2 * Math.PI);
        ctx.fill();
      });
    fit2d();
  }

  function repaint2d() {
    if (!graph2d) return;
    graph2d.nodeColor(graph2d.nodeColor());
    graph2d.linkColor(graph2d.linkColor());
    graph2d.linkDirectionalParticles(graph2d.linkDirectionalParticles());
  }

  function fit2d() {
    if (!graph2d || !el) return;
    var r = el.getBoundingClientRect();
    if (r.width && r.height) graph2d.width(r.width).height(r.height);
  }

  function destroy2d() {
    if (graph2d && graph2d._destructor) { try { graph2d._destructor(); } catch (e) {} }
    graph2d = null;
    if (el) { el.innerHTML = ""; el.classList.remove("flat"); }
  }

  /* -------------------------------------------------------- mode plumbing */
  function apply(data) {
    lastGraph = data;
    var neb = nebula();
    if (mode === "3d" && neb) {
      neb.setGraph(data);
      neb.setGraphVisible(true);
    } else if (mode === "2d" && graph2d) {
      var byId = {};
      var nodes = data.nodes.map(function (n) {
        var c = Object.assign({}, n); byId[c.id] = true; return c;
      });
      var links = data.edges
        .filter(function (e) { return byId[e.src] && byId[e.dst]; })
        .map(function (e) { return { source: e.src, target: e.dst, w: e.w, kinds: e.kinds }; });
      graph2d.graphData({ nodes: nodes, links: links });
      if (neb) neb.setGraphVisible(false);       // one constellation at a time
    }
    status(data.nodes.length + " stars · " + data.edges.length + " filaments"
      + (data.truncated ? " · truncated" : "") + (mode === "3d" ? " · 3D" : " · 2D"));
    toggleEmpty(data.nodes.length === 0);
  }

  function setMode(m) {
    if (m === mode) return;
    if (m === "3d" && !nebula()) {
      status("3D unavailable here — staying 2D");
      var box = document.getElementById("cst-3d");
      if (box) box.checked = false;
      return;
    }
    try { localStorage.setItem("nebula-cst-mode", m); } catch (e) {}
    mode = m;
    if (m === "2d") {
      if (typeof ForceGraph !== "function") { status("2D engine missing"); return; }
      build2d();
    } else destroy2d();
    var box = document.getElementById("cst-3d");
    if (box) box.checked = m === "3d";
    if (lastGraph) apply(lastGraph);
  }

  function params() {
    var w = document.getElementById("cst-weight");
    var cap = document.getElementById("cst-cap");
    var pane = document.getElementById("pane-constellation");
    var proj = pane ? pane.dataset.project : "";
    var qs = new URLSearchParams({
      min_weight: w ? w.value : 0.35,
      max_nodes: cap ? cap.value : 150,
    });
    if (proj) qs.set("project", proj);
    return qs;
  }

  async function load() {
    var seq = ++loadSeq;
    status("loading…");
    var data;
    try {
      data = await Atlas.getJSON("/api/ui/graph?" + params());
    } catch (e) { status("failed: " + e.message); return; }
    if (seq !== loadSeq) return;
    apply(data);
  }

  function toggleEmpty(show) {
    var pane = document.getElementById("pane-constellation");
    var existing = pane.querySelector(".empty");
    if (existing) existing.remove();
    if (show) {
      var d = document.createElement("div");
      d.className = "empty";
      d.style.position = "absolute";
      d.style.inset = "0";
      d.innerHTML = '<div><div class="empty-plate" aria-hidden="true"></div>'
        + '<p class="empty-title">No links to draw</p>'
        + '<p class="empty-hint">Lower the weight floor, or run the graph rebuild.</p></div>';
      pane.appendChild(d);
    }
  }

  /* ------------------------------------- orb fallback (no WebGL anywhere) */
  var fbVeil = null;
  function orbTexture(mem) {
    var W = 1200, H = 600;
    var c = document.createElement("canvas");
    c.width = W * 2; c.height = H;
    var g = c.getContext("2d");
    g.fillStyle = "rgba(240, 220, 178, .95)";
    g.font = 'italic 30px Georgia, serif';
    var words = ((mem.summary || "") + ".  " + (mem.content || "")).split(/\s+/);
    var margin = 70, lh = 44, wi = 0, y = H * 0.16;
    while (y < H * 0.86) {
      var line = "";
      while (wi < words.length) {
        var t = line ? line + " " + words[wi] : words[wi];
        if (g.measureText(t).width > W - margin * 2) break;
        line = t; wi++;
      }
      if (wi >= words.length) wi = 0;
      g.fillText(line, margin, y);
      g.fillText(line, margin + W, y);
      y += lh;
    }
    return c.toDataURL("image/png");
  }
  function openOrbFallback(mem) {
    closeOrbFallback(true);
    fbVeil = document.createElement("div");
    fbVeil.className = "orb-veil";
    var stage = document.createElement("div");
    stage.className = "orb-stage";
    var fig = document.createElement("figure");
    fig.className = "orb";
    fig.style.backgroundImage = "url(" + orbTexture(mem) + ")";
    stage.appendChild(fig);
    fbVeil.appendChild(stage);
    var cap = document.createElement("figcaption");
    cap.className = "orb-caption";
    cap.innerHTML = "<strong>" + Atlas.esc(mem.summary || mem.id)
      + "</strong><span>scroll to return · Esc</span>";
    fbVeil.appendChild(cap);
    document.body.appendChild(fbVeil);
    requestAnimationFrame(function () { requestAnimationFrame(function () {
      fbVeil && fbVeil.classList.add("open");
    }); });
    fbVeil.addEventListener("wheel", function (ev) {
      ev.preventDefault(); closeOrbFallback();
    }, { passive: false });
    fbVeil.addEventListener("click", function () { closeOrbFallback(); });
    document.addEventListener("keydown", fbKey, true);
  }
  function fbKey(ev) {
    if (ev.key === "Escape") { ev.stopPropagation(); closeOrbFallback(); }
  }
  function closeOrbFallback(instant) {
    if (!fbVeil) return;
    var v = fbVeil; fbVeil = null;
    document.removeEventListener("keydown", fbKey, true);
    v.classList.remove("open");
    setTimeout(function () { v.remove(); }, instant ? 0 : 260);
  }

  /* --------------------------------------------------------------- wiring */
  function boot() {
    if (inited) return;
    inited = true;
    el = document.getElementById("constellation");
    statusEl = document.getElementById("cst-status");
    if (!el) return;

    var m = savedMode();
    if (!m) { status("graph engines failed to load"); return; }
    mode = m;
    if (mode === "2d") build2d();

    var neb = nebula();
    if (neb) {
      neb.hooks.onNodeClick = function (n) { selectNode(n.id); };
      neb.hooks.onBackgroundClick = function () { deselect(); Atlas.closeInspector(); };
    }

    document.addEventListener("atlas:deselect", deselect);
    document.addEventListener("atlas:lens", function (ev) {
      var on = ev.detail.lens === "constellation";
      var neb2 = nebula();
      if (neb2) neb2.setFocus(on && mode === "3d");
      if (on && mode === "2d") fit2d();
    });
    window.addEventListener("resize", fit2d);

    var w = document.getElementById("cst-weight");
    var out = document.getElementById("cst-weight-out");
    if (w) w.addEventListener("input", function () {
      if (out) out.value = w.value;
      clearTimeout(w._t);
      w._t = setTimeout(load, 250);
    });
    var cap = document.getElementById("cst-cap");
    if (cap) cap.addEventListener("change", load);
    var box = document.getElementById("cst-3d");
    if (box) box.addEventListener("change", function () {
      setMode(box.checked ? "3d" : "2d");
    });

    /* The constellation is the world: load it immediately, whatever lens is
       active — it breathes dimly behind the Stream and the Chronicle. */
    load();
  }

  window.addEventListener("DOMContentLoaded", boot);
  if (window.Atlas) Atlas.onLens("constellation", boot);
})();
