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
  var expanded = [];                       // folders pulled into the world (rel paths)
  function filesOn() {
    try { return localStorage.getItem("nebula-cst-files") !== "off"; } catch (e) { return true; }
  }
  function setFilesOn(v) {
    try { localStorage.setItem("nebula-cst-files", v ? "on" : "off"); } catch (e) {}
  }
  /* v2.5: the layout shape, remembered. layouts.js is a plain script that
     may have failed to load, so an unknown value always reads organic. */
  var LAYOUT_STORE = "nebula-layout";
  function savedLayout() {
    try {
      var v = localStorage.getItem(LAYOUT_STORE);
      return (window.NebulaLayouts && window.NebulaLayouts.MODES.indexOf(v) >= 0) ? v : "organic";
    } catch (e) { return "organic"; }
  }
  function layoutLabel() {
    var m = savedLayout();
    return (m !== "organic" && window.NebulaLayouts)
      ? " · " + window.NebulaLayouts.LABELS[m] : "";
  }
  var FILE_COL = "#bfe9ff", FOLDER_COL = "#8ea0c8", FILELINK_COL = "127, 216, 255", FOLDLINK_COL = "107, 127, 168";

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
    if (n.id === selectedId) return SELECT_COL;
    if (n.kind === "file") return FILE_COL;
    if (n.kind === "folder") return FOLDER_COL;
    return "hsl(" + Atlas.hue(n.project) + " 70% 66%)";
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

  /* one click handler for every species (3D hooks and 2D onNodeClick) */
  function selectAny(n) {
    if (!n) return;
    if (n.kind === "file" || n.kind === "folder") {
      selectedId = n.id;
      var neb = nebula();
      if (neb) neb.select(n.id);
      if (graph2d) repaint2d();
      if (n.kind === "file") Atlas.inspectFile(String(n.id).replace(/^file:/, ""));
      else Atlas.inspectFolder(n, { pull: function () { return expandFolder(n.path); } });
      return;
    }
    selectNode(n.id);
  }

  function expandFolder(path) {
    if (path === "" || path == null) {
      var st = document.getElementById("cst-status");
      if (st) st.textContent = "a root folder holds every file already; browse it in the Files lens";
      return false;
    }
    if (expanded.indexOf(path) < 0) expanded.push(path);
    load();
    return true;
  }
  function collapseFolders() { expanded = []; load(); }
  window.Constellation = { expandFolder: expandFolder, collapseFolders: collapseFolders };

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
        var sub = n.kind === "file" ? Atlas.esc(n.path || "") + " · heat " + (n.degree != null ? Number(n.degree).toFixed(1) : "0")
                : n.kind === "folder" ? Atlas.esc(n.path || "(root)") + " · " + Atlas.esc(n.file_count || 0) + " files"
                : Atlas.esc(n.project || "") + " · " + Atlas.esc(n.type || "") + " · importance " + (n.importance || "?")
                  + " · gravity " + (n.degree != null ? n.degree.toFixed(1) : "?");
        return '<div class="cst-tip"><strong>' + Atlas.esc(n.label || n.id)
          + '</strong><p class="quiet">' + sub + "</p></div>";
      })
      .linkColor(function (l) {
        var isFile = l.kinds && l.kinds.indexOf("file_ref") >= 0;
        var isFold = l.kinds && l.kinds.indexOf("in_folder") >= 0;
        var rgb = isFile ? FILELINK_COL : isFold ? FOLDLINK_COL : "214, 190, 148";
        var a = nodeOnSelection(l) ? 0.9 : isFold ? 0.18 : Math.min(0.7, 0.1 + (l.w || 0) * 0.55);
        return "rgba(" + rgb + "," + a + ")";
      })
      .linkWidth(function (l) { return 0.4 + (l.w || 0) * 2.2; })
      .linkDirectionalParticles(function (l) {
        return (!Atlas.reducedMotion && nodeOnSelection(l)) ? 2 : 0;
      })
      .linkDirectionalParticleWidth(2.2)
      .linkDirectionalParticleSpeed(0.006)
      .enableNodeDrag(true)
      .onNodeClick(function (n) { selectAny(n); })
      .onBackgroundClick(function () { deselect(); Atlas.closeInspector(); })
      .cooldownTime(Atlas.reducedMotion ? 0 : 3000)
      .warmupTicks(Atlas.reducedMotion ? 60 : 0)
      .nodeCanvasObjectMode(function () { return "replace"; })
      .nodeCanvasObject(function (n, ctx) {
        if (n.kind === "file" || n.kind === "folder") {
          var rr = n.kind === "file" ? 3 + Math.sqrt(n.degree || 0) * 1.6 : 6 + Math.sqrt(n.file_count || 0) * 0.9;
          var selc = n.id === selectedId;
          ctx.save();
          ctx.strokeStyle = selc ? SELECT_COL : nodeCol(n);
          ctx.fillStyle = selc ? SELECT_COL : nodeCol(n);
          ctx.lineWidth = selc ? 2 : 1.2;
          ctx.beginPath();
          if (n.kind === "file") {           // a rotated square: the crystal
            ctx.moveTo(n.x, n.y - rr); ctx.lineTo(n.x + rr, n.y); ctx.lineTo(n.x, n.y + rr); ctx.lineTo(n.x - rr, n.y);
            ctx.closePath(); ctx.fill();
          } else {                           // a ring: the planet
            ctx.arc(n.x, n.y, rr, 0, 2 * Math.PI); ctx.stroke();
          }
          ctx.restore();
          return;
        }
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

  /* ------------------------------------------------------------ 2D shapes
     The same six shapes as the 3D world, through force-graph's own custom
     force slots. Tree needs no force at all: fx/fy pin every node. */
  function apply2dLayout(m) {
    if (!graph2d || !window.NebulaLayouts) return;
    var L = window.NebulaLayouts;
    var data = graph2d.graphData();
    var nodes = data.nodes, links = data.links;
    nodes.forEach(function (n) { n.fx = n.fy = undefined; });
    ["flow", "web", "orb"].forEach(function (name) { graph2d.d3Force(name, null); });
    var planeR = Math.max(140, 34 * Math.sqrt(nodes.length));
    var centres = L.projectCentres(nodes, planeR);
    if (m === "tree") {
      var tp = L.treePositions(nodes, links, 60);
      nodes.forEach(function (n) {
        var p = tp[n.id];
        if (p) { n.x = n.fx = p.x; n.y = n.fy = p.y; }
      });
    } else if (m === "flow_down" || m === "flow_right") {
      var gap = Math.max(46, planeR * 0.28), top = 3 * gap;
      graph2d.d3Force("flow", function (alpha) {
        var k = 0.22 * alpha;
        nodes.forEach(function (n) {
          var t = top - L.ringOf(n) * gap, c = centres[n.project || ""] || { x: 0, y: 0 };
          if (m === "flow_down") { n.vy += (-t - n.y) * k; n.vx += (c.x * 0.9 - n.x) * 0.02 * alpha; }
          else { n.vx += (-t - n.x) * k; n.vy += (c.y * 0.9 - n.y) * 0.02 * alpha; }
        });
      });
    } else if (m === "web" || m === "orb") {
      graph2d.d3Force("web", function (alpha) {
        var k = 0.2 * alpha;
        nodes.forEach(function (n) {
          var c = centres[n.project || ""] || { x: 0, y: 0 };
          var ring = 22 + L.ringOf(n) * 42, dx = n.x - c.x, dy = n.y - c.y;
          var d = Math.sqrt(dx * dx + dy * dy) || 1, f = (ring - d) / d * k;
          n.vx += dx * f; n.vy += dy * f;
        });
      });
    }
    graph2d.d3ReheatSimulation();
  }

  /* Switching shape must never rebuild the graph: in 2D a rebuild would
     throw away the fx/fy and the hierarchy that were just assigned. */
  function applyLayout() {
    var m = savedLayout();
    var neb = nebula();
    if (mode === "3d" && neb) neb.setLayout(m);
    else if (mode === "2d" && graph2d) apply2dLayout(m);
    if (lastGraph) renderStatus(lastGraph);
  }

  /* -------------------------------------------------------- mode plumbing */
  function renderStatus(data) {
    var counts = { memory: 0, file: 0, folder: 0 };
    data.nodes.forEach(function (n) { counts[n.kind || "memory"] = (counts[n.kind || "memory"] || 0) + 1; });
    status(counts.memory + " stars"
      + (filesOn() ? " · " + counts.file + " crystals · " + counts.folder + " planets" : "")
      + " · " + data.edges.length + " filaments"
      + (data.truncated ? " · truncated" : "")
      + (data.files_truncated ? " · folder pull capped, some files not shown" : "")
      + (mode === "3d" ? " · 3D" : " · 2D")
      + layoutLabel());
  }

  function apply(data) {
    lastGraph = data;
    var neb = nebula();
    if (mode === "3d" && neb) {
      neb.setGraph(data);
      /* nebula keeps layoutMode itself, so only correct it when it drifts
         from the stored choice: that avoids a second startSim per load */
      if (neb.getLayout && neb.getLayout() !== savedLayout()) neb.setLayout(savedLayout());
      neb.setGraphVisible(true);
    } else if (mode === "2d" && graph2d) {
      var byId = {};
      var nodes = data.nodes.map(function (n) {
        var c = Object.assign({}, n); byId[c.id] = true; return c;
      });
      var links = data.edges
        .filter(function (e) { return byId[e.src] && byId[e.dst]; })
        .map(function (e) { return { source: e.src, target: e.dst, w: e.w, kinds: e.kinds }; });
      /* ringOf and treePositions need _level / _head / _parent, and the 2D
         path never runs nebula's computeHierarchy: do it here, on the same
         node objects force-graph is about to adopt. */
      if (window.NebulaLayouts) window.NebulaLayouts.assignHierarchy(nodes, links);
      graph2d.graphData({ nodes: nodes, links: links });
      /* unconditional, organic included: the cleanup inside clears any
         custom force still closed over the previous, discarded nodes */
      apply2dLayout(savedLayout());
      if (neb) neb.setGraphVisible(false);       // one constellation at a time
    }
    renderStatus(data);
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
    syncFocus();
  }

  /* the world holds the pointer only when the lens is on AND we are 3D */
  function syncFocus() {
    var neb = nebula();
    if (neb) neb.setFocus(document.body.dataset.lens === "constellation"
                          && mode === "3d");
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
    if (filesOn()) {
      qs.set("include_files", "1");
      expanded.forEach(function (f) { qs.append("folder", f); });
    }
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
      neb.hooks.onNodeClick = function (n) { selectAny(n); };
      neb.hooks.onBackgroundClick = function () { deselect(); Atlas.closeInspector(); };
    }

    document.addEventListener("atlas:deselect", deselect);
    document.addEventListener("atlas:lens", function (ev) {
      syncFocus();
      if (ev.detail.lens === "constellation" && mode === "2d") fit2d();
    });
    syncFocus();   // nebula assumed 3D at its own init; correct for 2D boots
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
    var fbox = document.getElementById("cst-files");
    if (fbox) {
      fbox.checked = filesOn();
      fbox.addEventListener("change", function () { setFilesOn(fbox.checked); expanded = []; load(); });
    }
    var lay = document.getElementById("cst-layout");
    if (lay) {
      lay.value = savedLayout();
      lay.addEventListener("change", function () {
        try { localStorage.setItem(LAYOUT_STORE, lay.value); } catch (e) {}
        applyLayout();
      });
    }

    /* The constellation is the world: load it immediately, whatever lens is
       active — it breathes dimly behind the Stream and the Chronicle. */
    load();
  }

  window.addEventListener("DOMContentLoaded", boot);
  if (window.Atlas) Atlas.onLens("constellation", boot);
})();
