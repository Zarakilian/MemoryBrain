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

  /* luminous palette against the night stage — theme-independent */
  var SELECT_COL = "#ffd98a";
  function nodeCol(n) {
    return n.id === selectedId ? SELECT_COL
      : "hsl(" + Atlas.hue(n.project) + " 70% 66%)";
  }
  function nodeShade(n) { return "hsl(" + Atlas.hue(n.project) + " 68% 38%)"; }
  function nodeSize(n) {
    return 3 + Math.sqrt(n.degree || 0) * 2.6 + (n.importance || 3) * 0.8;
  }

  function nodeOnSelection(l) {
    return selectedId
      && (idOf(l.source) === selectedId || idOf(l.target) === selectedId);
  }

  /* shared config for both engines (their APIs mirror each other) */
  function configure(g, is3d) {
    g.backgroundColor("rgba(0,0,0,0)")
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
        // fixed warm-white on the night stage, whatever the theme
        return "rgba(214, 190, 148," + (nodeOnSelection(l)
          ? 0.9 : Math.min(0.7, 0.1 + (l.w || 0) * 0.55)) + ")";
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
      g.nodeOpacity(1);              // the library default (.75) buries the orbs
      if (g.nodeResolution) g.nodeResolution(14);
      if (g.showNavInfo) g.showNavInfo(false);
    } else {
      g.linkWidth(function (l) { return 0.4 + (l.w || 0) * 2.2; });
      // glass beads: specular highlight, depth-shaded edge, type-coded ring
      g.nodeCanvasObjectMode(function () { return "replace"; })
        .nodeCanvasObject(function (n, ctx) {
          var r = 4 * Math.sqrt(nodeSize(n));         // nodeRelSize default = 4
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
          ctx.strokeStyle = sel ? SELECT_COL : "rgba(255,246,224,.45)";
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

  /* ---------------- the orb: fly into a memory, hold it, turn it --------
     Click an orb → the camera flies to it and the memory appears as a real
     three.js sphere wrapped in its own writing, floating on the blurred
     veil. Drag the sphere to turn it; it also turns slowly on its own.
     Scroll, Esc, or click the veil to fly back out. The inspector stays
     above the veil, visible and fully interactive. Falls back to a CSS
     sphere when THREE is unavailable. */
  var orbVeil = null, savedCam = null;
  var orb3 = null;   // { renderer, scene, camera, mesh, tex, raf, dragging }

  function orbDur(ms) { return Atlas.reducedMotion ? 0 : ms; }

  function orbCanvas(mem, W, H) {
    W = W || 2048; H = H || 1024;
    var c = document.createElement("canvas");
    c.width = W; c.height = H;
    var g = c.getContext("2d");
    // fully transparent ground: the writing floats on glass, the blurred
    // constellation glows through from behind
    g.clearRect(0, 0, W, H);
    g.fillStyle = "rgba(240, 214, 166, .95)";
    g.shadowColor = "rgba(224, 189, 125, .6)";
    g.shadowBlur = 10;                       // a soft glow keeps it readable
    g.font = 'italic 52px Georgia, "Iowan Old Style", serif';
    var words = ((mem.summary || "") + ".  " + (mem.content || "")).split(/\s+/);
    if (!words.length || (words.length === 1 && !words[0])) words = ["(no", "content)"];
    var margin = 130, lh = 78, wi = 0;
    var y = H * 0.16;                       // stay off the poles
    while (y < H * 0.86) {
      var line = "";
      while (wi < words.length) {
        var t = line ? line + " " + words[wi] : words[wi];
        if (g.measureText(t).width > W - margin * 2) break;
        line = t; wi++;
      }
      if (wi >= words.length) wi = 0;       // the text wraps the sphere forever
      g.save();
      g.translate(margin, y);
      g.rotate((Math.random() - 0.5) * 0.01);
      g.fillText(line, 0, 0);
      g.restore();
      y += lh;
    }
    return c;
  }

  function buildOrb3(mem, host) {
    if (typeof THREE === "undefined") return false;
    var size = Math.min(Math.min(window.innerWidth, window.innerHeight) * 0.62, 560);
    var renderer;
    try {
      renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
    } catch (e) { return false; }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(size, size);
    renderer.domElement.className = "orb-canvas";
    host.appendChild(renderer.domElement);

    var scene = new THREE.Scene();
    var camera = new THREE.PerspectiveCamera(42, 1, 0.1, 10);
    camera.position.z = 3.1;
    scene.add(new THREE.AmbientLight(0xffffff, 0.9));
    var sun = new THREE.DirectionalLight(0xfff2d8, 0.65);
    sun.position.set(-2, 2.4, 3);
    scene.add(sun);

    var tex = new THREE.CanvasTexture(orbCanvas(mem));
    // the writing: an unlit transparent shell — glass, not parchment.
    // DoubleSide lets the far side's script drift by behind the front's.
    var mesh = new THREE.Mesh(
      new THREE.SphereGeometry(1.16, 56, 40),
      new THREE.MeshBasicMaterial({ map: tex, transparent: true,
                                    side: THREE.DoubleSide, depthWrite: false })
    );
    mesh.rotation.y = Math.PI * 0.15;
    scene.add(mesh);
    // the faintest inner globe so the sphere has presence
    var glass = new THREE.Mesh(
      new THREE.SphereGeometry(1.06, 40, 28),
      new THREE.MeshLambertMaterial({ color: 0xe8c58a, transparent: true,
                                      opacity: 0.07, depthWrite: false })
    );
    scene.add(glass);

    orb3 = { renderer: renderer, scene: scene, camera: camera, mesh: mesh,
             glass: glass, tex: tex, raf: 0, dragging: false, vx: 0 };

    var lastX = 0, lastY = 0;
    var el = renderer.domElement;
    el.addEventListener("pointerdown", function (ev) {
      ev.stopPropagation();
      orb3.dragging = true;
      lastX = ev.clientX; lastY = ev.clientY;
      el.setPointerCapture(ev.pointerId);
      el.classList.add("grabbing");
    });
    el.addEventListener("pointermove", function (ev) {
      if (!orb3 || !orb3.dragging) return;
      var dx = ev.clientX - lastX, dy = ev.clientY - lastY;
      lastX = ev.clientX; lastY = ev.clientY;
      mesh.rotation.y += dx * 0.006;
      mesh.rotation.x = Math.max(-0.9, Math.min(0.9, mesh.rotation.x + dy * 0.004));
      orb3.vx = dx * 0.006;                  // fling momentum
    });
    el.addEventListener("pointerup", function () {
      if (orb3) orb3.dragging = false;
      el.classList.remove("grabbing");
    });
    el.addEventListener("click", function (ev) { ev.stopPropagation(); });

    (function frame() {
      if (!orb3) return;
      if (!orb3.dragging) {
        orb3.vx *= 0.96;                     // momentum decays into…
        mesh.rotation.y += Atlas.reducedMotion
          ? orb3.vx
          : Math.max(orb3.vx, 0.0016);       // …the idle turn
      }
      renderer.render(scene, camera);
      orb3.raf = requestAnimationFrame(frame);
    })();
    return true;
  }

  function destroyOrb3() {
    if (!orb3) return;
    cancelAnimationFrame(orb3.raf);
    orb3.tex.dispose();
    orb3.mesh.geometry.dispose();
    orb3.mesh.material.dispose();
    orb3.glass.geometry.dispose();
    orb3.glass.material.dispose();
    orb3.renderer.dispose();
    orb3 = null;
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
    var stage = document.createElement("div");
    stage.className = "orb-stage";
    orbVeil.appendChild(stage);
    var cap = document.createElement("figcaption");
    cap.className = "orb-caption";
    cap.innerHTML = "<strong>" + Atlas.esc(mem.summary || mem.id)
      + "</strong><span>drag to turn · scroll to return · Esc</span>";
    orbVeil.appendChild(cap);
    document.body.appendChild(orbVeil);

    if (!buildOrb3(mem, stage)) {
      // CSS fallback: shaded circle with the sliding texture
      var flat = orbCanvas(mem, 1200, 600);
      var two = document.createElement("canvas");
      two.width = 2400; two.height = 600;
      var ctx = two.getContext("2d");
      ctx.drawImage(flat, 0, 0); ctx.drawImage(flat, 1200, 0);
      var fig = document.createElement("figure");
      fig.className = "orb";
      fig.style.backgroundImage = "url(" + two.toDataURL("image/png") + ")";
      stage.appendChild(fig);
    }

    requestAnimationFrame(function () { requestAnimationFrame(function () {
      orbVeil && orbVeil.classList.add("open");
    }); });

    orbVeil.addEventListener("wheel", function (ev) {
      ev.preventDefault();
      closeOrb();
    }, { passive: false });
    orbVeil.addEventListener("click", function (ev) {
      if (!ev.target.closest(".orb-stage")) closeOrb();
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
    destroyOrb3();
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
