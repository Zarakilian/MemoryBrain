/* Constellation lens — the memories as stars in the one living world.
   3D by default: the graph lives INSIDE the Nebula (nebula.js), the same
   full-screen scene that breathes behind every lens; entering the lens only
   grants it the pointer and brings it to full luminosity. 2D fallback (user
   toggle, remembered; automatic when WebGL is missing) uses the vendored
   force-graph canvas build inside a glass pane — its bundled three never
   touches the world's (design rule 5). Both draw the same shapes: layouts.js
   decides where every star is, and when.
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
      var L = window.NebulaLayouts;
      if (L && L.MODES.indexOf(v) >= 0) return v;
      return (L && L.DEFAULT) || "organic";      // first visit: the brain
    } catch (e) { return (window.NebulaLayouts && window.NebulaLayouts.DEFAULT) || "organic"; }
  }
  function layoutLabel() {
    var m = savedLayout();
    return (m !== "organic" && window.NebulaLayouts)
      ? " · " + window.NebulaLayouts.LABELS[m] : "";
  }
  var FILE_COL = "#bfe9ff", FOLDER_COL = "#8ea0c8", FILELINK_COL = "127, 216, 255", FOLDLINK_COL = "107, 127, 168";

  function nebula() {
    var N = window.Nebula;
    return (N && N.available && !N.lost) ? N : null;
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
  window.Constellation = {
    expandFolder: expandFolder, collapseFolders: collapseFolders,
    /* one honest answer for "what is the 2D picture doing": paste
       Constellation.debug() into the console */
    debug: function () {
      return { mode: mode, layout: savedLayout(), shape: shape2d ? shape2d.mode : null,
               nodes: nodes2d.length, morphing: morphing2d, paused: paused2d,
               frames: draw2d.frames, msPerFrame: Math.round(draw2d.ms * 100) / 100,
               life: Math.round(life2d * 10) / 10 };
    },
  };

  function deselect() {
    if (!selectedId) return;
    selectedId = null;
    var neb = nebula();
    if (neb) neb.deselect();
    if (graph2d) repaint2d();
  }

  /* ------------------------------------------------------------ 2D engine
     The same shapes as the 3D world, flat. layouts.js gives every node a
     track (a place that is a function of time); here that place is simply
     projected onto the page (x, -y) and painted on force-graph's canvas,
     which now only draws: its physics is switched off. So the 2D picture
     is the 3D shape seen from the front, it moves the same way, and a
     change of layout glides instead of jumping. */
  var shape2d = null, nodes2d = [], life2d = 0, last2d = 0, morphing2d = false;
  var motes2d = [], paused2d = null;
  var LEVEL_COLS = ["#ffe3a1", "#ff9d5c", "#4fd6c5", "#8b93ec"];
  var _o = { x: 0, y: 0, z: 0 };
  var LINK_BUDGET_2D = 700;

  function alive2d() {
    if (Atlas.reducedMotion) return false;
    try { return localStorage.getItem("nebula-ambience") !== "off"; } catch (e) { return true; }
  }
  function starCol(n) {
    if (n.kind === "file") return FILE_COL;
    if (n.kind === "folder") return FOLDER_COL;
    return LEVEL_COLS[Math.min(n._level == null ? 2 : n._level, 3)];
  }
  function radius2d(n) {
    return n._r2 || (n._r2 = window.NebulaLayouts ? window.NebulaLayouts.starRadius(n) : 5);
  }

  /* a star is two stamped pictures (a soft halo, a lit ball), painted once
     per colour and reused: a canvas shadow per star per frame is far too
     slow for a picture that never stops moving */
  var stamps = {};
  function stamp(col, kind) {
    var key = kind + col;
    if (stamps[key]) return stamps[key];
    var S = 64, c = document.createElement("canvas");
    c.width = c.height = S;
    var g = c.getContext("2d"), grad;
    if (kind === "halo") {
      grad = g.createRadialGradient(S / 2, S / 2, 0, S / 2, S / 2, S / 2);
      grad.addColorStop(0, col); grad.addColorStop(0.3, col); grad.addColorStop(1, "rgba(0,0,0,0)");
      g.globalAlpha = 0.55;
    } else {
      grad = g.createRadialGradient(S * 0.36, S * 0.32, S * 0.04, S / 2, S / 2, S / 2);
      grad.addColorStop(0, "rgba(255,252,240,1)");
      grad.addColorStop(0.42, col);
      grad.addColorStop(1, "rgba(10,14,28,1)");
    }
    g.fillStyle = grad;
    g.beginPath();
    g.arc(S / 2, S / 2, S / 2 - 0.5, 0, 2 * Math.PI);
    g.fill();
    return (stamps[key] = c);
  }

  function drawStar(n, ctx, scale) {
    var r = radius2d(n), sel = n.id === selectedId, col = starCol(n), a = 1;
    /* on the globe the far side shows through faintly */
    if (shape2d && shape2d.mode === "orb" && n._z < 0) a = 0.3;
    var st = n.standing, t = life2d;
    if (st === "stale" || st === "superseded" || st === "archived" || st === "done") a *= 0.42;
    else if (st === "disputed" && alive2d()) {
      a *= 0.6 + 0.4 * (Math.sin(t * 7 + r * 9) * Math.sin(t * 17 + r * 3) > -0.25 ? 1 : 0.2);
    }
    ctx.save();
    ctx.globalAlpha = a;
    if (n.kind === "file" || n.kind === "folder") {
      ctx.strokeStyle = sel ? SELECT_COL : col;
      ctx.fillStyle = sel ? SELECT_COL : col;
      ctx.lineWidth = sel ? 2 : 1.2;
      ctx.beginPath();
      if (n.kind === "file") {            // a rotated square: the crystal
        var rr = r * 1.25;
        ctx.moveTo(n.x, n.y - rr); ctx.lineTo(n.x + rr, n.y); ctx.lineTo(n.x, n.y + rr); ctx.lineTo(n.x - rr, n.y);
        ctx.closePath(); ctx.fill();
      } else {                            // a ring: the planet
        ctx.arc(n.x, n.y, r, 0, 2 * Math.PI); ctx.stroke();
      }
      ctx.restore();
      return;
    }
    var halo = r * (st === "changed" ? 3.6 + 0.5 * Math.sin(t * 1.6 + r) : 2.5);
    ctx.globalCompositeOperation = "lighter";
    ctx.globalAlpha = a * (st === "changed" ? 0.8 : 0.5);
    ctx.drawImage(stamp(col, "halo"), n.x - halo, n.y - halo, halo * 2, halo * 2);
    ctx.globalCompositeOperation = "source-over";
    ctx.globalAlpha = a;
    ctx.drawImage(stamp(col, "ball"), n.x - r, n.y - r, r * 2, r * 2);
    if (sel || st === "disputed" || st === "proven" || st === "verified") {
      ctx.strokeStyle = sel ? SELECT_COL : st === "disputed" ? "rgba(255, 92, 108, .9)"
                      : "rgba(255, 246, 214, .7)";
      ctx.lineWidth = (sel ? 2 : 1) / Math.max(scale, 0.4);
      ctx.beginPath();
      ctx.arc(n.x, n.y, r + (sel ? 2.2 : 1.2), 0, 2 * Math.PI);
      ctx.stroke();
    }
    ctx.restore();
  }

  /* ---- what each shape stands on, painted under the stars ---- */
  function decoBrain(ctx, scale, d) {
    ctx.save();
    [-1, 1].forEach(function (side) {
      var cx = d.hx(side), ex = d.ex, ey = d.ey;
      var g = ctx.createRadialGradient(cx, 0, 0, cx, 0, Math.max(ex, ey));
      g.addColorStop(0, "rgba(120, 140, 255, .08)");
      g.addColorStop(1, "rgba(120, 140, 255, .015)");
      ctx.fillStyle = g;
      ctx.strokeStyle = "rgba(165, 178, 255, .38)";
      ctx.lineWidth = 1.3 / scale;
      ctx.beginPath();
      ctx.ellipse(cx, 0, ex, ey, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
      /* the folds: wavy lines across each hemisphere, like sulci from above */
      ctx.strokeStyle = "rgba(165, 178, 255, .15)";
      [-0.68, -0.42, -0.14, 0.14, 0.4, 0.66].forEach(function (f, i) {
        var y0 = f * ey, half = ex * Math.sqrt(Math.max(0, 1 - f * f)) * 0.88;
        ctx.beginPath();
        for (var k = 0; k <= 16; k++) {
          var u = k / 16, x = cx - side * half + side * 2 * half * u;
          var y = y0 + ey * 0.045 * Math.sin(u * 9 + i * 1.7) + ey * 0.02 * Math.sin(u * 23 + i);
          if (k) ctx.lineTo(x, y); else ctx.moveTo(x, y);
        }
        ctx.stroke();
      });
    });
    ctx.restore();
  }
  function decoGlobe(ctx, scale, d, t) {
    var R = d.R, tilt = Math.atan2(d.axis[0], d.axis[1]);
    ctx.save();
    var g = ctx.createRadialGradient(-R * 0.3, -R * 0.35, R * 0.1, 0, 0, R);
    g.addColorStop(0, "rgba(34, 52, 104, .55)");
    g.addColorStop(1, "rgba(6, 10, 24, .7)");
    ctx.fillStyle = g;
    ctx.beginPath(); ctx.arc(0, 0, R * 0.985, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = "rgba(143, 184, 232, .5)";
    ctx.lineWidth = 1.4 / scale;
    ctx.beginPath(); ctx.arc(0, 0, R, 0, Math.PI * 2); ctx.stroke();
    ctx.rotate(tilt);                       // the axis leans, as in 3D
    ctx.strokeStyle = "rgba(143, 184, 232, .17)";
    ctx.lineWidth = 1 / scale;
    /* meridians: ellipses that open and close as the globe turns */
    for (var k = 0; k < 9; k++) {
      var w = Math.abs(Math.cos(d.omega * t + k * Math.PI / 9)) * R;
      ctx.beginPath(); ctx.ellipse(0, 0, Math.max(w, 0.01), R, 0, 0, Math.PI * 2); ctx.stroke();
    }
    /* parallels are seen edge on: chords across the disc */
    for (var la = -75; la <= 75; la += 15) {
      var y = Math.sin(la * Math.PI / 180) * R, x = Math.cos(la * Math.PI / 180) * R;
      ctx.beginPath(); ctx.moveTo(-x, y); ctx.lineTo(x, y); ctx.stroke();
    }
    ctx.restore();
  }
  function decoRiver(ctx, scale, d, t) {
    var p = _o;
    ctx.save();
    ctx.lineCap = "round"; ctx.lineJoin = "round";
    d.lanes.forEach(function (lane) {
      if (!lane.g.mem.length) return;
      ctx.beginPath();
      for (var i = 0; i <= 40; i++) {
        var u = lane.u0 - 0.03 + (lane.u1 - lane.u0 + 0.06) * i / 40;
        d.xyz(u * d.Len, d.mid(lane, u), 0, p);
        if (i) ctx.lineTo(p.x, -p.y); else ctx.moveTo(p.x, -p.y);
      }
      ctx.strokeStyle = "rgba(74, 122, 240, .13)";
      ctx.lineWidth = Math.max(14, lane.hw * 0.92);
      ctx.stroke();
      ctx.strokeStyle = "rgba(110, 200, 235, .12)";
      ctx.lineWidth = Math.max(4, lane.hw * 0.3);
      ctx.stroke();
    });
    /* motes carried downstream */
    ctx.fillStyle = "rgba(160, 220, 255, .8)";
    motes2d.forEach(function (m) {
      var f = (m.seed + t * m.speed) % 1, u = m.lane.u0 + (m.lane.u1 - m.lane.u0) * f;
      d.xyz(u * d.Len, d.mid(m.lane, u) + m.off, 0, p);
      ctx.globalAlpha = 0.75 * Math.sin(Math.PI * f);
      ctx.beginPath(); ctx.arc(p.x, -p.y, m.size, 0, 2 * Math.PI); ctx.fill();
    });
    /* the time axis and its notches */
    ctx.globalAlpha = 1;
    ctx.strokeStyle = "rgba(143, 184, 232, .4)";
    ctx.lineWidth = 1 / scale;
    var side = d.down ? -(d.edge - 12) : d.edge - 12, end = d.down ? -(d.edge - 4) : d.edge - 4;
    ctx.beginPath();
    d.xyz(0, side, 0, p); ctx.moveTo(p.x, -p.y);
    d.xyz(d.Len, side, 0, p); ctx.lineTo(p.x, -p.y);
    d.ticks.forEach(function (tk) {
      d.xyz(tk.u * d.Len, side, 0, p); ctx.moveTo(p.x, -p.y);
      d.xyz(tk.u * d.Len, end, 0, p); ctx.lineTo(p.x, -p.y);
    });
    ctx.stroke();
    ctx.restore();
  }
  function decoWeb(ctx, scale, d) {
    var n = d.radials.length, RF = d.RF;
    ctx.save();
    ctx.lineWidth = 1 / scale;
    ctx.beginPath();
    d.radials.forEach(function (rd) {
      var cs = Math.cos(rd.a), sn = -Math.sin(rd.a);
      ctx.moveTo(cs * d.r0 * 0.3, sn * d.r0 * 0.3);
      ctx.lineTo(cs * RF * (rd.main ? 1.5 : 1), sn * RF * (rd.main ? 1.5 : 1));
    });
    ctx.strokeStyle = "rgba(207, 224, 255, .2)";
    ctx.stroke();
    function ring(r, sag) {
      for (var i = 0; i < n; i++) {
        var a0 = d.radials[i].a, a1 = i + 1 < n ? d.radials[i + 1].a : d.radials[0].a + Math.PI * 2;
        for (var k = 0; k <= 6; k++) {
          var u = k / 6, a = a0 + (a1 - a0) * u, rr = r * (1 - sag * Math.sin(Math.PI * u));
          if (k) ctx.lineTo(Math.cos(a) * rr, -Math.sin(a) * rr);
          else ctx.moveTo(Math.cos(a) * rr, -Math.sin(a) * rr);
        }
      }
    }
    if (n > 1) {
      ctx.beginPath();
      d.rings.forEach(function (r) { ring(r, d.sag); });
      ring(RF, d.sag * 0.6);
      [0.34, 0.52, 0.72].forEach(function (f) { ring(d.r0 * f, 0); });
      ctx.strokeStyle = "rgba(207, 224, 255, .26)";
      ctx.stroke();
    }
    ctx.restore();
  }
  function decoTree(ctx, scale, d, t) {
    function sway(q) {                    // the same wind as layouts.js
      var hh = Math.max(0, Math.min((q[1] - d.ground) / d.tall, 1.3)), s = hh * hh;
      return [q[0] + d.amp * s * (Math.sin(t * 0.5 + q[0] * 0.006 + q[2] * 0.004) + 0.35 * Math.sin(t * 1.13 + q[1] * 0.02)), -q[1]];
    }
    ctx.save();
    ctx.lineCap = "round";
    d.wood.forEach(function (w) {
      var a = sway(w.p0), c = sway(w.c), b = sway(w.p2);
      if (w.r0 < 0.9) {
        ctx.strokeStyle = "rgba(233, 199, 154, .34)";
        ctx.lineWidth = Math.max(0.6, w.r0 * 1.4);
        ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.quadraticCurveTo(c[0], c[1], b[0], b[1]); ctx.stroke();
        return;
      }
      /* a limb tapers: paint it in pieces, each a little thinner */
      var px = a[0], py = a[1];
      for (var i = 1; i <= 6; i++) {
        var u = i / 6, v = 1 - u;
        var x = v * v * a[0] + 2 * v * u * c[0] + u * u * b[0], y = v * v * a[1] + 2 * v * u * c[1] + u * u * b[1];
        ctx.strokeStyle = "rgba(96, 66, 48, .95)";
        ctx.lineWidth = 2 * (w.r0 + (w.r1 - w.r0) * (u - 1 / 12));
        ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(x, y); ctx.stroke();
        px = x; py = y;
      }
    });
    ctx.restore();
  }
  function decoGalaxies(ctx, scale, d, t) {
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    d.clusters.forEach(function (c) {
      d.centre(c, t, _o);
      var R = c.R * 1.5, hue = Atlas.hue(c.slug);
      var g = ctx.createRadialGradient(_o.x, -_o.y, 0, _o.x, -_o.y, R);
      g.addColorStop(0, "hsla(" + hue + ", 60%, 66%, .16)");
      g.addColorStop(0.5, "hsla(" + hue + ", 60%, 60%, .05)");
      g.addColorStop(1, "hsla(" + hue + ", 60%, 60%, 0)");
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.arc(_o.x, -_o.y, R, 0, 2 * Math.PI); ctx.fill();
    });
    ctx.restore();
  }

  /* one frame: move every star along its track, then paint the structure */
  var draw2d = { t0: 0, ms: 0, frames: 0 };
  function frame2d(ctx, scale) {
    var now = performance.now();
    draw2d.t0 = now;
    var dt = Math.min((now - last2d) / 1000 || 0.016, 0.05);
    last2d = now;
    var alive = alive2d();
    if (alive) life2d += dt;
    if (!shape2d) return;
    var t = life2d, any = false;
    for (var i = 0; i < nodes2d.length; i++) {
      var n = nodes2d[i];
      if (n._held) continue;
      shape2d.track(n, t, _o);
      n._z = _o.z;
      var x = _o.x, y = -_o.y, m = n._m;
      if (m) {
        var u = (now - m.t0 - m.d) / m.dur;
        if (u >= 1) n._m = null;
        else {
          any = true;
          if (u < 0) u = 0;
          var e = u < 0.5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2;
          x = m.x0 + (x - m.x0) * e; y = m.y0 + (y - m.y0) * e;
        }
      }
      n.x = x; n.y = y;
    }
    morphing2d = any;
    /* keep painting only while something moves; a stilled picture rests */
    var pause = !(alive || any);
    if (pause !== paused2d) { paused2d = pause; graph2d.autoPauseRedraw(pause); }
    var d = shape2d.deco;
    if (d.kind === "brain2d") decoBrain(ctx, scale, d);
    else if (d.kind === "globe") decoGlobe(ctx, scale, d, t);
    else if (d.kind === "river") decoRiver(ctx, scale, d, t);
    else if (d.kind === "web") decoWeb(ctx, scale, d);
    else if (d.kind === "tree") decoTree(ctx, scale, d, t);
    else if (d.kind === "galaxies") decoGalaxies(ctx, scale, d, t);
  }
  /* names and date marks, at a constant size whatever the zoom */
  function labels2d(ctx, scale) {
    if (!shape2d) return;
    ctx.save();
    ctx.textBaseline = "middle";
    ctx.lineJoin = "round";
    shape2d.labels.forEach(function (lb) {
      lb.pos(life2d, _o);
      var tick = lb.cls.indexOf("tick") >= 0, pad = 8 / scale;
      var x = _o.x, y = -_o.y, text = tick ? lb.text : lb.text.toUpperCase();
      ctx.font = (tick ? "" : "600 ") + ((tick ? 10 : 11) / scale) + "px "
        + (tick ? "ui-monospace, Consolas, monospace" : "system-ui, 'Segoe UI', sans-serif");
      if (lb.cls.indexOf("left") >= 0) { ctx.textAlign = "right"; x -= pad; }
      else if (lb.cls.indexOf("right") >= 0) { ctx.textAlign = "left"; x += pad; }
      else {
        ctx.textAlign = "center";
        if (lb.cls.indexOf("above") >= 0) y -= pad * 1.4;
        else if (lb.cls.indexOf("below") >= 0) y += pad * 1.4;
      }
      ctx.globalAlpha = lb.globe && _o.z < 0 ? 0.18 : 1;
      ctx.lineWidth = 3 / scale;
      ctx.strokeStyle = "rgba(4, 6, 12, .85)";
      ctx.strokeText(text, x, y);
      ctx.fillStyle = tick ? "rgba(151, 160, 186, .9)" : "rgba(220, 227, 244, .82)";
      ctx.fillText(text, x, y);
    });
    ctx.restore();
    /* what one painted frame cost (a running average), for Constellation.debug() */
    draw2d.ms += (performance.now() - draw2d.t0 - draw2d.ms) * 0.1;
    draw2d.frames++;
  }

  function linkShown(l) { return !l._far2 || nodeOnSelection(l); }
  function linkAlpha(l) {
    var isFold = l.kinds && l.kinds.indexOf("in_folder") >= 0;
    if (nodeOnSelection(l)) return 0.9;
    var a = (isFold ? 0.18 : Math.min(0.7, 0.1 + (l.w || 0) * 0.55)) * (l._lk == null ? 1 : l._lk);
    if (shape2d && shape2d.mode === "orb"
        && ((l.source && l.source._z < 0) || (l.target && l.target._z < 0))) a *= 0.25;
    return Math.min(0.85, a);
  }

  function build2d() {
    el.innerHTML = "";
    el.classList.add("flat");
    paused2d = null;
    graph2d = ForceGraph()(el)
      .backgroundColor("rgba(0,0,0,0)")
      .onRenderFramePre(frame2d)
      .onRenderFramePost(labels2d)
      .nodeId("id")
      .nodeVal(function (n) { return radius2d(n); })
      .nodeLabel(function (n) {
        var sub = n.kind === "file" ? Atlas.esc(n.path || "") + " · heat " + (n.degree != null ? Number(n.degree).toFixed(1) : "0")
                : n.kind === "folder" ? Atlas.esc(n.path || "(root)") + " · " + Atlas.esc(n.file_count || 0) + " files"
                : Atlas.esc(n.project || "") + " · " + Atlas.esc(n.type || "")
                  + (n.standing && n.standing !== "current" && n.standing !== "open" ? " · " + Atlas.esc(n.standing) : "")
                  + " · importance " + (n.importance || "?")
                  + " · gravity " + (n.degree != null ? n.degree.toFixed(1) : "?");
        return '<div class="cst-tip"><strong>' + Atlas.esc(Atlas.cleanSummary(n.label || n.id))
          + '</strong><p class="quiet">' + sub + "</p></div>";
      })
      .linkVisibility(linkShown)
      .linkCurvature(function (l) { return shape2d && shape2d.mode === "tree" ? 0 : 0.14; })
      .linkColor(function (l) {
        var isFile = l.kinds && l.kinds.indexOf("file_ref") >= 0;
        var isFold = l.kinds && l.kinds.indexOf("in_folder") >= 0;
        var rgb = l._thread || nodeOnSelection(l) ? "255, 217, 138"
                : isFile ? FILELINK_COL : isFold ? FOLDLINK_COL : "214, 190, 148";
        return "rgba(" + rgb + "," + linkAlpha(l).toFixed(3) + ")";
      })
      .linkWidth(function (l) { return l._thread ? 1.6 : 0.4 + (l.w || 0) * 1.4; })
      /* a bead of light on a chosen star's links, and at rest on a few of
         the others: the pulse that says this is alive */
      .linkDirectionalParticles(function (l) {
        if (Atlas.reducedMotion) return 0;
        if (nodeOnSelection(l)) return 2;
        return l._bead && alive2d() ? 1 : 0;
      })
      .linkDirectionalParticleWidth(function (l) { return nodeOnSelection(l) ? 2.2 : 1.5; })
      .linkDirectionalParticleSpeed(function (l) { return nodeOnSelection(l) ? 0.006 : 0.0022; })
      .linkDirectionalParticleColor(function () { return "rgba(255, 233, 180, .85)"; })
      .enableNodeDrag(true)
      .onNodeDrag(function (n) { n._held = true; })
      .onNodeDragEnd(function (n) {
        /* let go, and the star glides back to its place in the shape */
        n._held = false; n.fx = n.fy = undefined;
        n._m = { x0: n.x, y0: n.y, t0: performance.now(), d: 0, dur: 650 };
        morphing2d = true;
        if (paused2d) { paused2d = false; graph2d.autoPauseRedraw(false); }
      })
      .onNodeClick(function (n) { selectAny(n); })
      .onBackgroundClick(function () { deselect(); Atlas.closeInspector(); })
      .cooldownTicks(0)
      .warmupTicks(0)
      .nodeCanvasObjectMode(function () { return "replace"; })
      .nodeCanvasObject(drawStar)
      .nodePointerAreaPaint(function (n, color, ctx) {
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(n.x, n.y, radius2d(n) + 2, 0, 2 * Math.PI);
        ctx.fill();
      });
    /* no physics here: the shapes place every star */
    graph2d.d3Force("charge", null);
    graph2d.d3Force("center", null);
    var lf = graph2d.d3Force("link");
    if (lf && lf.strength) lf.strength(0);
    fit2d();
  }

  function repaint2d() {
    if (!graph2d) return;
    graph2d.nodeColor(graph2d.nodeColor());
    graph2d.linkColor(graph2d.linkColor());
    graph2d.linkVisibility(graph2d.linkVisibility());
    graph2d.linkDirectionalParticles(graph2d.linkDirectionalParticles());
  }

  function fit2d() {
    if (!graph2d || !el) return;
    var r = el.getBoundingClientRect();
    if (r.width && r.height) graph2d.width(r.width).height(r.height);
  }

  function destroy2d() {
    if (graph2d && graph2d._destructor) { try { graph2d._destructor(); } catch (e) {} }
    graph2d = null; shape2d = null; nodes2d = [];
    if (el) { el.innerHTML = ""; el.classList.remove("flat"); }
  }

  /* put the 2D picture into a shape. Every star glides from where it is to
     its new track; the view glides to hold the whole shape. */
  function apply2dLayout(m) {
    if (!graph2d || !window.NebulaLayouts) return;
    var L = window.NebulaLayouts;
    var data = graph2d.graphData();
    nodes2d = data.nodes;
    var links = data.links;
    nodes2d.forEach(function (n) { n.fx = n.fy = undefined; n.vx = n.vy = 0; });
    try {
      shape2d = L.compute(m, nodes2d, links, { flat: true, names: Atlas.projectNames() });
    } catch (e) {
      shape2d = null;
      status("layout failed: " + e.message);
      return;
    }
    if (!shape2d) return;
    /* draw only the strongest links at rest, and put a bead on a few */
    var order = links.map(function (l, i) {
      var isFold = l.kinds && l.kinds.indexOf("in_folder") >= 0;
      return [(isFold ? 0.18 : 0.1 + (l.w || 0) * 0.55) * (l._lk == null ? 1 : l._lk), i];
    }).sort(function (a, b) { return b[0] - a[0] || a[1] - b[1]; });
    order.forEach(function (it, rank) {
      var l = links[it[1]];
      l._far2 = rank >= LINK_BUDGET_2D;
      l._bead = !l._far2 && (l._thread || rank % 9 === 0) && rank < 360;
    });
    /* the river's motes: a few per channel, seeded once */
    motes2d = [];
    if (shape2d.deco.kind === "river") {
      shape2d.deco.lanes.forEach(function (lane) {
        if (!lane.g.mem.length) return;
        var count = Math.round(5 + 22 * (lane.u1 - lane.u0) * Math.min(1.6, lane.hw / 26));
        for (var i = 0; i < count; i++) {
          motes2d.push({ lane: lane, seed: Math.random(), off: (Math.random() - 0.5) * lane.hw * 0.7,
                         speed: (0.012 + Math.random() * 0.016) / Math.max(0.15, lane.u1 - lane.u0),
                         size: 0.9 + Math.random() * 1.2 });
        }
      });
    }
    var now = performance.now(), still = Atlas.reducedMotion;
    nodes2d.forEach(function (n, i) {
      if (!isFinite(n.x) || !isFinite(n.y)) { n.x = 0; n.y = 0; }
      /* a ragged start: the stars leave like a flock, not like a slab */
      n._m = still ? null : { x0: n.x, y0: n.y, t0: now, d: 380 * ((i * 0.61803) % 1), dur: 1400 };
      if (still) { shape2d.track(n, 0, _o); n.x = _o.x; n.y = -_o.y; n._z = _o.z; }
    });
    morphing2d = !still;
    /* frame the whole shape */
    var x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    shape2d.bounds.forEach(function (b) {
      x0 = Math.min(x0, b[0]); x1 = Math.max(x1, b[0]);
      y0 = Math.min(y0, -b[1]); y1 = Math.max(y1, -b[1]);
    });
    var box = el.getBoundingClientRect();
    if (box.width && box.height && isFinite(x0)) {
      var k = Math.min(box.width / Math.max(x1 - x0, 1), box.height / Math.max(y1 - y0, 1)) * 0.9;
      graph2d.centerAt((x0 + x1) / 2, (y0 + y1) / 2, still ? 0 : 900).zoom(k, still ? 0 : 900);
    }
    paused2d = null;
    repaint2d();
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
      var byId = {}, was = {};
      /* a star that was already on the page starts from where it stood */
      nodes2d.forEach(function (n) { was[n.id] = n; });
      var nodes = data.nodes.map(function (n) {
        var c = Object.assign({}, n); byId[c.id] = true;
        if (was[c.id]) { c.x = was[c.id].x; c.y = was[c.id].y; }
        return c;
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

  /* persist false: a switch forced by circumstance (a graphics reset),
     not chosen, so the saved choice stays and the next load tries again */
  function setMode(m, persist) {
    if (m === mode) return;
    if (m === "3d" && !nebula()) {
      status("3D unavailable here — staying 2D");
      var box = document.getElementById("cst-3d");
      if (box) box.checked = false;
      return;
    }
    if (persist !== false) {
      try { localStorage.setItem("nebula-cst-mode", m); } catch (e) {}
    }
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
    if (!neb) return;
    /* the stars centre in the lens canvas, not behind the rail */
    if (neb.setFrameElement) neb.setFrameElement(document.getElementById("constellation"));
    neb.setFocus(document.body.dataset.lens === "constellation" && mode === "3d");
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
    /* the ambience switch stills or wakes the 2D picture too */
    document.addEventListener("atlas:ambience", function () {
      if (!graph2d) return;
      paused2d = false;
      graph2d.autoPauseRedraw(false);
      repaint2d();
    });
    document.addEventListener("atlas:lens", function (ev) {
      syncFocus();
      if (ev.detail.lens === "constellation" && mode === "2d") fit2d();
    });
    syncFocus();   // nebula assumed 3D at its own init; correct for 2D boots

    /* the graphics card reset and the world did not come back: show the
       2D constellation rather than an empty sky */
    document.addEventListener("nebula:lost", function () {
      setTimeout(function () {
        if (!window.Nebula || !window.Nebula.lost || mode !== "3d") return;
        if (typeof ForceGraph !== "function") return;
        setMode("2d", false);
        status("3D paused after a graphics reset · showing 2D");
      }, 2500);
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
    if (box) {
      box.checked = mode === "3d";       // the box must say what is on screen
      box.addEventListener("change", function () {
        setMode(box.checked ? "3d" : "2d");
      });
    }
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
