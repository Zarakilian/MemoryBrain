/* Constellation lens — 2D force graph on the vendored force-graph library
   (vasturiano, MIT). Lazy-initialised on first activation; interaction
   contract: click selects → inspector, drag moves, scroll zooms, hover
   shows details. */
"use strict";

(function () {
  var graph = null, el = null, statusEl = null;
  var selectedId = null, inited = false, loadSeq = 0;

  function status(msg) { if (statusEl) statusEl.textContent = msg; }

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

  async function load() {
    var seq = ++loadSeq;
    status("loading…");
    var data;
    try {
      data = await Atlas.getJSON("/api/ui/graph?" + params());
    } catch (e) { status("failed: " + e.message); return; }
    if (seq !== loadSeq) return;                 // superseded by a newer load
    var nodes = data.nodes.map(function (n) {
      return Object.assign({}, n);               // force-graph mutates nodes
    });
    var ids = {};
    nodes.forEach(function (n) { ids[n.id] = true; });
    var links = data.edges
      .filter(function (e) { return ids[e.src] && ids[e.dst]; })
      .map(function (e) { return { source: e.src, target: e.dst, w: e.w, kinds: e.kinds }; });
    graph.graphData({ nodes: nodes, links: links });
    status(nodes.length + " nodes · " + links.length + " links"
      + (data.truncated ? " · truncated" : ""));
    toggleEmpty(nodes.length === 0);
  }

  function toggleEmpty(show) {
    var existing = el.parentElement.querySelector(".empty");
    if (existing) existing.remove();
    if (show) {
      var d = document.createElement("div");
      d.className = "empty";
      d.style.position = "absolute";
      d.style.inset = "0";
      d.innerHTML = '<div><div class="empty-plate" aria-hidden="true"></div>'
        + '<p class="empty-title">No links to draw</p>'
        + '<p class="empty-hint">Lower the weight floor, or run the graph rebuild.</p></div>';
      el.parentElement.appendChild(d);
    }
  }

  function init() {
    if (inited) { fit(); return; }
    if (typeof ForceGraph !== "function") {
      status("force-graph failed to load");
      return;
    }
    inited = true;
    el = document.getElementById("constellation");
    statusEl = document.getElementById("cst-status");

    graph = ForceGraph()(el)
      .backgroundColor("rgba(0,0,0,0)")
      .nodeId("id")
      .nodeVal(function (n) { return 2 + Math.sqrt(n.degree || 0) * 2.2 + (n.importance || 3) * 0.6; })
      .nodeColor(function (n) {
        return n.id === selectedId ? "#e0bd7d" : Atlas.color(n.project, 60);
      })
      .nodeLabel(function (n) {
        return '<div class="cst-tip"><strong>' + Atlas.esc(n.label || n.id)
          + '</strong><p class="quiet">' + Atlas.esc(n.project || "")
          + " · " + Atlas.esc(n.type || "") + " · importance " + (n.importance || "?")
          + " · gravity " + (n.degree != null ? n.degree.toFixed(1) : "?") + "</p></div>";
      })
      .linkColor(function (l) {
        return "rgba(201,162,95," + Math.min(0.85, 0.12 + (l.w || 0) * 0.7) + ")";
      })
      .linkWidth(function (l) { return 0.4 + (l.w || 0) * 2.2; })
      .enableNodeDrag(true)
      .onNodeClick(function (n) {
        selectedId = n.id;
        graph.nodeColor(graph.nodeColor());        // repaint selection
        Atlas.inspect(n.id, null);
      })
      .onBackgroundClick(function () {
        selectedId = null;
        graph.nodeColor(graph.nodeColor());
        Atlas.closeInspector();
      })
      .cooldownTime(Atlas.reducedMotion ? 0 : 3000)
      .warmupTicks(Atlas.reducedMotion ? 60 : 0);

    document.addEventListener("atlas:deselect", function () {
      if (!selectedId) return;
      selectedId = null;
      if (graph) graph.nodeColor(graph.nodeColor());
    });

    fit();
    window.addEventListener("resize", fit);

    var w = document.getElementById("cst-weight");
    var out = document.getElementById("cst-weight-out");
    if (w) w.addEventListener("input", function () {
      if (out) out.value = w.value;
      clearTimeout(w._t);
      w._t = setTimeout(load, 250);
    });
    var cap = document.getElementById("cst-cap");
    if (cap) cap.addEventListener("change", load);

    load();
  }

  function fit() {
    if (!graph || !el) return;
    var r = el.getBoundingClientRect();
    if (r.width && r.height) graph.width(r.width).height(r.height);
  }

  if (window.Atlas) Atlas.onLens("constellation", init);
})();
