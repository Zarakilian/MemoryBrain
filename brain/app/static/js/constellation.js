/* Constellation lens — force graph of the memory web.
   3D orbit view by default (vendored 3d-force-graph, MIT, three bundled);
   falls back to the 2D canvas build automatically when WebGL is missing and
   on user toggle (remembered). Interaction contract in both modes: click
   selects → inspector, drag moves, scroll zooms, hover shows details.
   Particles pulse along a node's links only on selection — motion as
   feedback, and none at all under prefers-reduced-motion. */
"use strict";

(function () {
  var graph = null, mode = null, el = null, statusEl = null;
  var selectedId = null, inited = false, loadSeq = 0, lastGraph = null;

  function status(msg) { if (statusEl) statusEl.textContent = msg; }

  function webglOK() {
    try {
      var c = document.createElement("canvas");
      return !!(c.getContext("webgl2") || c.getContext("webgl"));
    } catch (e) { return false; }
  }

  function savedMode() {
    var m = null;
    try { m = localStorage.getItem("atlas-cst-mode"); } catch (e) {}
    if (m !== "2d" && m !== "3d") m = "3d";                  // 3D is the default
    if (m === "3d" && (typeof ForceGraph3D !== "function" || !webglOK())) m = "2d";
    if (m === "2d" && typeof ForceGraph !== "function") m = null;
    return m;
  }

  function idOf(x) { return typeof x === "object" && x ? x.id : x; }

  function nodeOnSelection(l) {
    return selectedId
      && (idOf(l.source) === selectedId || idOf(l.target) === selectedId);
  }

  /* shared config for both engines (their APIs mirror each other) */
  function configure(g, is3d) {
    g.backgroundColor("rgba(0,0,0,0)")
      .nodeId("id")
      .nodeVal(function (n) {
        return 2 + Math.sqrt(n.degree || 0) * 2.2 + (n.importance || 3) * 0.6;
      })
      .nodeColor(function (n) {
        return n.id === selectedId ? Atlas.goldBright() : Atlas.color(n.project);
      })
      .nodeLabel(function (n) {
        return '<div class="cst-tip"><strong>' + Atlas.esc(n.label || n.id)
          + '</strong><p class="quiet">' + Atlas.esc(n.project || "")
          + " · " + Atlas.esc(n.type || "") + " · importance " + (n.importance || "?")
          + " · gravity " + (n.degree != null ? n.degree.toFixed(1) : "?") + "</p></div>";
      })
      .linkColor(function (l) {
        return Atlas.goldRGBA(nodeOnSelection(l)
          ? 0.9 : Math.min(0.85, 0.12 + (l.w || 0) * 0.7));
      })
      .linkDirectionalParticles(function (l) {
        return (!Atlas.reducedMotion && nodeOnSelection(l)) ? 2 : 0;
      })
      .linkDirectionalParticleWidth(2.2)
      .linkDirectionalParticleSpeed(0.006)
      .enableNodeDrag(true)
      .onNodeClick(function (n) {
        selectedId = n.id;
        repaint();
        Atlas.inspect(n.id, null);
        openOrb(n);
      })
      .onBackgroundClick(function () {
        selectedId = null;
        repaint();
        Atlas.closeInspector();
      })
      .cooldownTime(Atlas.reducedMotion ? 0 : 3000)
      .warmupTicks(Atlas.reducedMotion ? 60 : 0);
    if (is3d) {
      g.linkOpacity(0.9);            // per-link alpha carried by linkColor
      if (g.showNavInfo) g.showNavInfo(false);
    } else {
      g.linkWidth(function (l) { return 0.4 + (l.w || 0) * 2.2; });
    }
    return g;
  }

  function repaint() {
    if (!graph) return;
    graph.nodeColor(graph.nodeColor());
    graph.linkColor(graph.linkColor());
    graph.linkDirectionalParticles(graph.linkDirectionalParticles());
  }

  function build(newMode) {
    if (graph && graph._destructor) { try { graph._destructor(); } catch (e) {} }
    el.innerHTML = "";
    mode = newMode;
    var is3d = mode === "3d";
    graph = configure(is3d ? ForceGraph3D()(el) : ForceGraph()(el), is3d);
    fit();
    var box = document.getElementById("cst-3d");
    if (box) box.checked = is3d;
    if (lastGraph) setData(lastGraph);
  }

  function setMode(m) {
    if (m === mode) return;
    if (m === "3d" && (typeof ForceGraph3D !== "function" || !webglOK())) {
      status("3D unavailable here — staying 2D");
      var box = document.getElementById("cst-3d");
      if (box) box.checked = false;
      return;
    }
    try { localStorage.setItem("atlas-cst-mode", m); } catch (e) {}
    build(m);
  }

  function params() {
    var w = document.getElementById("cst-weight");
    var cap = document.getElementById("cst-cap");
    var proj = document.getElementById("pane-constellation").dataset.project;
    var qs = new URLSearchParams({
      min_weight: w ? w.value : 0.35,
      max_nodes: cap ? cap.value : 150,
    });
    if (proj) qs.set("project", proj);
    return qs;
  }

  function setData(data) {
    var nodes = data.nodes.map(function (n) { return Object.assign({}, n); });
    var ids = {};
    nodes.forEach(function (n) { ids[n.id] = true; });
    var links = data.edges
      .filter(function (e) { return ids[e.src] && ids[e.dst]; })
      .map(function (e) { return { source: e.src, target: e.dst, w: e.w, kinds: e.kinds }; });
    graph.graphData({ nodes: nodes, links: links });
    status(nodes.length + " nodes · " + links.length + " links"
      + (data.truncated ? " · truncated" : "") + (mode === "3d" ? " · 3D" : " · 2D"));
    toggleEmpty(nodes.length === 0);
  }

  async function load() {
    var seq = ++loadSeq;
    status("loading…");
    var data;
    try {
      data = await Atlas.getJSON("/api/ui/graph?" + params());
    } catch (e) { status("failed: " + e.message); return; }
    if (seq !== loadSeq) return;
    lastGraph = data;
    setData(data);
  }

  function toggleEmpty(show) {
    var existing = el.parentElement.querySelector(".empty");
    if (existing) existing.remove();
    if (show) {
      var d = document.createElement("div");
      d.className = "empty";
      d.style.position = "absolute";
      d.style.inset = "0";
      d.innerHTML = '<div><div class="empty-plate plate-anatomy" aria-hidden="true"></div>'
        + '<p class="empty-title">No links to draw</p>'
        + '<p class="empty-hint">Lower the weight floor, or run the graph rebuild.</p></div>';
      el.parentElement.appendChild(d);
    }
  }

  function fit() {
    if (!graph || !el) return;
    var r = el.getBoundingClientRect();
    if (r.width && r.height) graph.width(r.width).height(r.height);
  }

  function init() {
    if (inited) { fit(); return; }
    var m = savedMode();
    if (!m) { status("graph libraries failed to load"); return; }
    inited = true;
    el = document.getElementById("constellation");
    statusEl = document.getElementById("cst-status");

    build(m);
    window.addEventListener("resize", fit);
    document.addEventListener("atlas:deselect", function () {
      if (!selectedId) return;
      selectedId = null;
      repaint();
    });
    document.addEventListener("atlas:theme", repaint);

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

    load();
  }

  /* ---------------- the orb: fly into a memory, read its sphere ----------
     Click an orb → the camera flies to it and the memory itself appears as
     a slowly turning sphere wrapped in its own writing. Scroll (or Esc, or
     click the veil) to fly back out to the graph. Works in 3D and 2D. */
  var orbVeil = null, savedCam = null;

  function orbDur(ms) { return Atlas.reducedMotion ? 0 : ms; }

  function orbCanvas(mem) {
    var W = 1200, H = 600;
    var c = document.createElement("canvas");
    c.width = W * 2; c.height = H;
    var g = c.getContext("2d");
    var parch = document.documentElement.dataset.theme === "parchment";
    var draw = function (ox) {
      var grad = g.createLinearGradient(ox, 0, ox + W, H);
      if (parch) { grad.addColorStop(0, "#efe4c8"); grad.addColorStop(1, "#dcc99e"); }
      else { grad.addColorStop(0, "#33291c"); grad.addColorStop(1, "#211a12"); }
      g.fillStyle = grad;
      g.fillRect(ox, 0, W, H);
      g.fillStyle = parch ? "rgba(74,58,32,.85)" : "rgba(224,196,138,.8)";
      g.font = 'italic 30px Georgia, "Iowan Old Style", serif';
      var words = ((mem.summary || "") + ".  " + (mem.content || "")).split(/\s+/);
      if (!words.length || (words.length === 1 && !words[0])) words = ["(no", "content)"];
      var x = ox + 60, y = 70, wi = 0, lh = 44;
      while (y < H - 40) {
        var line = "";
        while (wi < words.length) {
          var t = line ? line + " " + words[wi] : words[wi];
          if (g.measureText(t).width > W - 120) break;
          line = t; wi++;
        }
        if (wi >= words.length) wi = 0;             // wrap the text around the sphere
        g.save();
        g.translate(x, y);
        g.rotate((Math.random() - 0.5) * 0.012);    // a living hand, not a printer
        g.fillText(line, 0, 0);
        g.restore();
        y += lh;
      }
    };
    draw(0); draw(W);                               // duplicated → seamless rotation
    return c.toDataURL("image/jpeg", 0.85);
  }

  async function openOrb(n) {
    if (orbVeil) closeOrb(true);
    var mem;
    try { mem = await Atlas.getJSON("/api/ui/memories/" + encodeURIComponent(n.id)); }
    catch (e) { return; }

    if (mode === "3d") {
      var c = graph.cameraPosition();
      savedCam = { mode: "3d", x: c.x, y: c.y, z: c.z };
      var dist = 70, r = Math.hypot(n.x || 1, n.y || 1, n.z || 1) || 1;
      var k = 1 + dist / r;
      graph.cameraPosition({ x: n.x * k, y: n.y * k, z: n.z * k }, n, orbDur(900));
    } else {
      savedCam = { mode: "2d", zoom: graph.zoom(), center: graph.centerAt() };
      graph.centerAt(n.x, n.y, orbDur(700));
      graph.zoom(Math.max(graph.zoom(), 5), orbDur(700));
    }

    orbVeil = document.createElement("div");
    orbVeil.className = "orb-veil";
    orbVeil.innerHTML =
      '<figure class="orb" style="background-image:url(' + orbCanvas(mem) + ')"></figure>' +
      '<figcaption class="orb-caption"><strong>' + Atlas.esc(mem.summary || mem.id)
      + '</strong><span>scroll to return · Esc</span></figcaption>';
    document.body.appendChild(orbVeil);
    requestAnimationFrame(function () { requestAnimationFrame(function () {
      orbVeil && orbVeil.classList.add("open");
    }); });

    orbVeil.addEventListener("wheel", function (ev) {
      ev.preventDefault();
      closeOrb();
    }, { passive: false });
    orbVeil.addEventListener("click", function (ev) {
      if (!ev.target.closest(".orb")) closeOrb();
    });
    document.addEventListener("keydown", orbKey, true);
  }

  function orbKey(ev) {
    if (ev.key === "Escape") { ev.stopPropagation(); closeOrb(); }
  }

  function closeOrb(instant) {
    if (!orbVeil) return;
    var veil = orbVeil;
    orbVeil = null;
    document.removeEventListener("keydown", orbKey, true);
    veil.classList.remove("open");
    setTimeout(function () { veil.remove(); }, instant ? 0 : 260);
    if (!savedCam || !graph) return;
    if (savedCam.mode === "3d" && mode === "3d") {
      graph.cameraPosition({ x: savedCam.x, y: savedCam.y, z: savedCam.z },
                           { x: 0, y: 0, z: 0 }, orbDur(800));
    } else if (savedCam.mode === "2d" && mode === "2d") {
      graph.centerAt(savedCam.center.x, savedCam.center.y, orbDur(600));
      graph.zoom(savedCam.zoom, orbDur(600));
    }
    savedCam = null;
  }

  if (window.Atlas) Atlas.onLens("constellation", init);
})();
