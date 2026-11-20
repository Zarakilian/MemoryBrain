/* Layout shapes for the Constellation. Pure geometry, no three, no DOM:
   nebula.js (3D) and constellation.js (2D fallback) both read
   window.NebulaLayouts. Every function is deterministic for a given graph
   so a layout looks the same on every reload. */
"use strict";

(function () {
  var L = window.NebulaLayouts = {};
  L.MODES = ["brain", "organic", "orb", "flow_down", "flow_right", "web", "tree"];
  L.LABELS = { brain: "Brain", organic: "Organic", orb: "Orb", flow_down: "Flow down",
               flow_right: "Flow right", web: "Web", tree: "Tree" };
  L.DEFAULT = "brain";

  /* ---------------------------------------------------------- the Brain
     Two hemispheres side by side (x), longer front to back (z, front is +z)
     than they are tall (y). Each is an ellipsoid whose inner half is
     squashed into a flat medial wall, so the walls almost touch across a
     narrow longitudinal fissure. Sizes are fractions of one radius R, so
     the 3D world can scale the brain by node count. */
  L.BRAIN = { rx: 0.46, ry: 0.5, rz: 0.8, gap: 0.03, medial: 0.3 };

  /* where a hemisphere's centre sits on x (side -1 left, +1 right), at R = 1 */
  L.hemiCentre = function (side) {
    var B = L.BRAIN;
    return side * (B.gap + B.medial * B.rx);
  };

  /* a project's lobe: a point just under the cortex. Sides are balanced by
     node count (biggest projects first, each to the lighter hemisphere); a
     project holding over a third of the nodes spans both hemispheres
     (side 0, with a left and a right lobe). Within a side, lobes spread
     over the lateral surface on a Fibonacci spiral. Deterministic. */
  L.brainLobes = function (nodes, R) {
    var B = L.BRAIN, count = {}, total = 0;
    nodes.forEach(function (n) {
      var p = n.project || "";
      count[p] = (count[p] || 0) + 1;
      total++;
    });
    var list = Object.keys(count).sort(function (a, b) {
      return count[b] - count[a] || (a < b ? -1 : 1);
    });
    var load = [0, 0], members = [[], []], out = {};
    list.forEach(function (p) {
      if (list.length > 1 && count[p] > total * 0.35) {
        out[p] = { side: 0 };
        load[0] += count[p] / 2; load[1] += count[p] / 2;
        members[0].push(p); members[1].push(p);
        return;
      }
      var s = load[0] <= load[1] ? 0 : 1;
      load[s] += count[p];
      members[s].push(p);
      out[p] = { side: s ? 1 : -1 };
    });
    var GA = Math.PI * (3 - Math.sqrt(5));
    [0, 1].forEach(function (s) {
      var side = s ? 1 : -1, m = Math.max(1, members[s].length);
      members[s].forEach(function (p, j) {
        var y = m === 1 ? 0.15 : 0.8 - 1.6 * (j + 0.5) / m;
        var r = Math.sqrt(Math.max(0, 1 - y * y)), a = GA * j * 1.7 + (s ? 0.9 : 0);
        var x = 0.35 + 0.65 * Math.abs(Math.cos(a)) * r, z = Math.sin(a) * r;
        var len = Math.sqrt(x * x + y * y + z * z) || 1;
        var pt = { x: L.hemiCentre(side) * R + side * B.rx * R * (x / len) * 0.8,
                   y: B.ry * R * (y / len) * 0.8, z: B.rz * R * (z / len) * 0.8 };
        if (out[p].side === 0) out[p][s ? "right" : "left"] = pt;
        else { out[p].x = pt.x; out[p].y = pt.y; out[p].z = pt.z; }
      });
    });
    return out;
  };

  /* ------------------------------------------- the brain's folded skin
     Seeded Perlin noise, so the folds are identical on every load. The
     sulci (grooves) follow the noise's zero lines, which meander like the
     real ones; the gyri between them stay broad and round. */
  var PERM = (function () {
    var p = [], i, seed = 7919;
    for (i = 0; i < 256; i++) p[i] = i;
    for (i = 255; i > 0; i--) {
      seed = (seed * 16807) % 2147483647;
      var j = seed % (i + 1), t = p[i]; p[i] = p[j]; p[j] = t;
    }
    var out = new Uint8Array(512);
    for (i = 0; i < 512; i++) out[i] = p[i & 255];
    return out;
  })();
  function fade(t) { return t * t * t * (t * (t * 6 - 15) + 10); }
  function mix(t, a, b) { return a + t * (b - a); }
  function grad(h, x, y, z) {
    h &= 15;
    var u = h < 8 ? x : y, v = h < 4 ? y : (h === 12 || h === 14 ? x : z);
    return ((h & 1) ? -u : u) + ((h & 2) ? -v : v);
  }
  function noise3(x, y, z) {
    var fx = Math.floor(x), fy = Math.floor(y), fz = Math.floor(z);
    var X = fx & 255, Y = fy & 255, Z = fz & 255;
    x -= fx; y -= fy; z -= fz;
    var u = fade(x), v = fade(y), w = fade(z), P = PERM;
    var A = P[X] + Y, AA = P[A] + Z, AB = P[A + 1] + Z;
    var B = P[X + 1] + Y, BA = P[B] + Z, BB = P[B + 1] + Z;
    return mix(w,
      mix(v, mix(u, grad(P[AA], x, y, z), grad(P[BA], x - 1, y, z)),
             mix(u, grad(P[AB], x, y - 1, z), grad(P[BB], x - 1, y - 1, z))),
      mix(v, mix(u, grad(P[AA + 1], x, y, z - 1), grad(P[BA + 1], x - 1, y, z - 1)),
             mix(u, grad(P[AB + 1], x, y - 1, z - 1), grad(P[BB + 1], x - 1, y - 1, z - 1))));
  }
  L.noise3 = noise3;

  function groove(n, w) { return Math.exp(-(n * n) / (w * w)); }

  /* the visible fold, from the raw fields a skin point carries: two
     octaves of sulci plus the Sylvian fissure (gated to the outer face).
     The shader in nebula.js draws exactly this per pixel, so the lines
     stay thin and crisp whatever the mesh density. 0 crown .. 1 floor. */
  L.FOLD = { w1: 0.05, w2: 0.045, k2: 0.4, ws: 0.03, ks: 1.2 };
  L.foldOf = function (n1, n2, sy, gate) {
    var F = L.FOLD;
    return Math.min(1, groove(n1, F.w1) + F.k2 * groove(n2, F.w2)
                       + F.ks * groove(sy, F.ws) * gate);
  };
  var NO_GROOVE = 9;                         // a field value no groove reaches

  /* one point of a hemisphere's skin, from a direction (x, y, z) on the
     unit sphere. Writes [x, y, z, n1, n2, sy, gate] into out at R = 1:
     the position, then the raw fields the fold lines are drawn from. The
     geometry itself only dents gently (wide grooves), for relief. */
  function hemiPoint(x, y, z, side, out) {
    var B = L.BRAIN;
    if (x * side < 0) x *= B.medial;        // the medial wall faces the fissure
    if (y < 0) y *= 0.78;                   // the base is flatter than the crown
    /* the temporal lobe hangs a little low at the front of the side */
    var lat = Math.max(0, x * side);
    var tl = Math.exp(-((z - 0.22) * (z - 0.22)) / 0.1) * Math.max(0, -y) * lat;
    y -= 0.22 * tl;
    var cx = L.hemiCentre(side);
    var px = cx + B.rx * x, py = B.ry * y, pz = B.rz * z;
    var n1 = noise3(px * 5.2 + 3.1, py * 5.2, pz * 5.2);
    var n2 = noise3(px * 10.4 - 7.7, py * 10.4 + 2.3, pz * 10.4);
    /* the lateral (Sylvian) fissure: front-low, rising toward the back */
    var sy = y - (-0.2 + (0.42 - z) * 0.38);
    var gate = (z > -0.45 && z < 0.5) ? Math.min(1, lat * 2.2) : 0;
    var depth = 0.032 * groove(n1, 0.15) + 0.012 * groove(n2, 0.15)
              + 0.05 * groove(sy, 0.08) * gate;
    out[0] = cx + (px - cx) * (1 - depth);
    out[1] = py * (1 - depth);
    out[2] = pz * (1 - depth);
    out[3] = n1; out[4] = n2; out[5] = sy; out[6] = gate;
  }

  /* the cerebellum: tucked under the back of both hemispheres, its many
     fine folds (folia) running across it like the pages of a closed book */
  function cerebPoint(x, y, z, out) {
    var B = L.BRAIN, cy = -0.6 * B.ry, cz = -0.6 * B.rz;
    var fol = Math.sin(Math.atan2(y, z) * 13 + 0.6 * x) * 0.12;
    var d = 1 - 0.03 * groove(fol, 0.1);
    if (Math.abs(x) < 0.12) d *= 0.97;     // the vermis: a shallow midline notch
    out[0] = 0.58 * x * d;
    out[1] = cy + 0.2 * y * d;
    out[2] = cz + 0.25 * z * d;
    out[3] = fol; out[4] = NO_GROOVE; out[5] = NO_GROOVE; out[6] = 0;
  }

  /* the brainstem: a short tapered column below the middle, bent a little
     forward. z is the column's axis (top +1, bottom -1). */
  function stemPoint(x, y, z, out) {
    var B = L.BRAIN, t = (1 - z) / 2, rr = 0.075 - 0.02 * t;
    out[0] = x * rr;
    out[1] = -0.2 * B.ry - t * 0.72 * B.ry;
    out[2] = -0.2 * B.rz + 0.04 * Math.sin(t * Math.PI) + y * rr - t * 0.1 * B.rz;
    out[3] = NO_GROOVE; out[4] = NO_GROOVE; out[5] = NO_GROOVE; out[6] = 0;
  }

  /* a closed sphere-like grid with shared seams and single poles on z, so
     computed normals are smooth everywhere: W columns, H bands */
  function pushSphere(acc, W, H, fn) {
    var base = acc.n, tmp = [0, 0, 0, 0, 0, 0, 0], i, j;
    function vert(x, y, z) {
      fn(x, y, z, tmp);
      acc.pos.push(tmp[0], tmp[1], tmp[2]);
      acc.fold.push(tmp[3], tmp[4], tmp[5], tmp[6]);
      acc.n++;
    }
    vert(0, 0, 1);
    for (j = 1; j < H; j++) {
      var th = Math.PI * j / H, r = Math.sin(th), zc = Math.cos(th);
      for (i = 0; i < W; i++) {
        var ph = 2 * Math.PI * i / W;
        vert(r * Math.cos(ph), r * Math.sin(ph), zc);
      }
    }
    vert(0, 0, -1);
    var south = acc.n - 1;
    for (i = 0; i < W; i++) acc.index.push(base, base + 1 + i, base + 1 + (i + 1) % W);
    for (j = 0; j < H - 2; j++) {
      for (i = 0; i < W; i++) {
        var a = base + 1 + j * W + i, b = base + 1 + j * W + (i + 1) % W;
        acc.index.push(a, a + W, b, b, a + W, b + W);
      }
    }
    var last = base + 1 + (H - 2) * W;
    for (i = 0; i < W; i++) acc.index.push(south, last + (i + 1) % W, last + i);
  }

  /* the whole skin as one mesh at R = 1: two hemispheres, the cerebellum
     and the brainstem. Returns flat arrays (fold holds 4 raw fields per
     vertex, see foldOf); the 3D world adds normals. */
  L.brainSurface = function (detail) {
    detail = detail || 1;
    var acc = { pos: [], fold: [], index: [], n: 0 };
    var W = Math.round(176 * detail), H = Math.round(120 * detail);
    pushSphere(acc, W, H, function (x, y, z, o) { hemiPoint(x, y, z, -1, o); });
    pushSphere(acc, W, H, function (x, y, z, o) { hemiPoint(x, y, z, 1, o); });
    pushSphere(acc, Math.round(110 * detail), Math.round(72 * detail), cerebPoint);
    pushSphere(acc, 28, 16, stemPoint);
    return { pos: new Float32Array(acc.pos), fold: new Float32Array(acc.fold),
             index: new Uint32Array(acc.index) };
  };

  /* sparks on the same skin at R = 1 (seeded): mostly on the crowns of the
     gyri, a few on the cerebellum and the stem. shade 0..1 is brightness. */
  L.cortexPoints = function (count) {
    var pos = new Float32Array(count * 3), shade = new Float32Array(count);
    var seed = 20260930, tmp = [0, 0, 0, 0, 0, 0, 0];
    function rand() { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646; }
    for (var i = 0; i < count; i++) {
      var u = rand() * 2 - 1, th = rand() * Math.PI * 2, s = Math.sqrt(1 - u * u);
      var x = s * Math.cos(th), y = u, z = s * Math.sin(th), pick = rand(), sh;
      if (pick < 0.09) { cerebPoint(x, y, z, tmp); sh = 0.55; }
      else if (pick < 0.11) { stemPoint(x, y, z, tmp); sh = 0.4; }
      else {
        var side = i % 2 ? 1 : -1;
        hemiPoint(x, y, z, side, tmp);
        sh = 0.45 + 0.55 * Math.max(0, x * side);
      }
      pos[i * 3] = tmp[0]; pos[i * 3 + 1] = tmp[1]; pos[i * 3 + 2] = tmp[2];
      shade[i] = sh * (1 - 0.7 * L.foldOf(tmp[3], tmp[4], tmp[5], tmp[6]));
    }
    return { pos: pos, shade: shade };
  };

  function isMemory(n) { return !n.kind || n.kind === "memory"; }
  L.isMemory = isMemory;

  function idOf(x) { return typeof x === "object" && x ? x.id : x; }
  L.idOf = idOf;

  /* rank ring: memories by hierarchy level, then files, then folders */
  L.ringOf = function (n) {
    if (n.kind === "file") return 5;
    if (n.kind === "folder") return 6;
    return Math.max(0, Math.min(n._level == null ? 2 : n._level, 4));
  };

  /* projects on a circle in the x/y plane, deterministic by sorted slug */
  L.projectCentres = function (nodes, radius) {
    var list = [];
    nodes.forEach(function (n) {
      var p = n.project || "";
      if (list.indexOf(p) < 0) list.push(p);
    });
    list.sort();
    var out = {};
    if (list.length === 1) { out[list[0]] = { x: 0, y: 0, z: 0 }; return out; }
    var GA = Math.PI * (3 - Math.sqrt(5));
    list.forEach(function (p, i) {
      var a = GA * i;
      out[p] = { x: Math.cos(a) * radius, y: Math.sin(a) * radius, z: 0 };
    });
    return out;
  };

  /* ------------------------------------------------ assignHierarchy
     The 2D path never runs nebula's own computeHierarchy, so it carries no
     _level / _head / _parent for ringOf and treePositions to read. This is
     the same shape of pass, kept here so both engines could share it
     (nebula keeps its own, untouched). _head and _parent are node
     OBJECTS, not ids: treePositions compares n._head === n. Links may
     arrive with string or object endpoints, hence idOf. */
  L.assignHierarchy = function (nodes, links) {
    var byId = {}, byProject = {};
    nodes.forEach(function (n) { byId[n.id] = n; });
    nodes.forEach(function (n) {
      n._head = null; n._parent = null; n._level = 1;
      if (!isMemory(n)) { n._level = 3; return; }
      (byProject[n.project || ""] = byProject[n.project || ""] || []).push(n);
    });
    var adj = {};
    (links || []).forEach(function (l) {
      var a = byId[idOf(l.source)], b = byId[idOf(l.target)];
      if (!a || !b) return;
      (adj[a.id] = adj[a.id] || []).push(b);
      (adj[b.id] = adj[b.id] || []).push(a);
    });
    Object.keys(byProject).forEach(function (proj) {
      var members = byProject[proj];
      /* the same head rule as nebula computeHierarchy: a belief IS a
         project distilled truth, so the strongest belief takes the head
         over any raw session. Both engines must agree or the tree would
         reroot when the 3D box is unticked. */
      var pool = members.filter(function (n) { return n.type === "belief"; });
      if (!pool.length) pool = members;
      var head = pool[0];
      pool.forEach(function (n) {
        if ((n.degree || 0) > (head.degree || 0)
            || ((n.degree || 0) === (head.degree || 0)
                && (n.importance || 0) > (head.importance || 0))) head = n;
      });
      var seen = {}; seen[head.id] = true;
      head._head = head; head._level = 0; head._parent = null;
      var frontier = [head], level = 0;
      while (frontier.length) {
        level++;
        var next = [];
        frontier.forEach(function (n) {
          (adj[n.id] || []).forEach(function (m) {
            if (seen[m.id] || (m.project || "") !== proj || !isMemory(m)) return;
            seen[m.id] = true;
            m._head = head; m._level = level; m._parent = n;
            next.push(m);
          });
        });
        frontier = next;
      }
      members.forEach(function (n) {          // unlinked members still belong
        if (!n._head) { n._head = head; n._level = 2; n._parent = null; }
      });
    });
  };

  /* ---------------------------------------------------------- tree
     Radial tidy tree. Parent rules:
       memory  -> its BFS parent (n._parent) inside its project, head has none
       file    -> the referencing memory with the strongest file_ref edge,
                  else its folder (a file pulled from a folder), else project head
       folder  -> its project's head (drawn one ring outside the deepest memory)
       head    -> the project (virtual node), projects -> the root (origin)
     Angular span of every subtree is proportional to its leaf count, so busy
     branches get room and lines stop crossing. */
  L.treePositions = function (nodes, links, radius) {
    radius = radius || 70;
    var byId = {};
    nodes.forEach(function (n) { byId[n.id] = n; });

    var bestRef = {};        // file id -> {parent id, w}
    var folderOf = {};       // file id -> folder id
    (links || []).forEach(function (l) {
      var s = idOf(l.source), t = idOf(l.target), kinds = l.kinds || [];
      if (kinds.indexOf("file_ref") >= 0) {
        var fileId = byId[t] && byId[t].kind === "file" ? t : s;
        var memId = fileId === t ? s : t;
        if (!bestRef[fileId] || (l.w || 0) > bestRef[fileId].w) bestRef[fileId] = { parent: memId, w: l.w || 0 };
      } else if (kinds.indexOf("in_folder") >= 0) {
        var f = byId[s] && byId[s].kind === "file" ? s : t;
        folderOf[f] = f === s ? t : s;
      }
    });

    var heads = {};          // project -> head node
    nodes.forEach(function (n) { if (isMemory(n) && n._head === n) heads[n.project || ""] = n; });
    var projects = Object.keys(heads);
    nodes.forEach(function (n) {         // projects with no memory head still need a slot
      var p = n.project || "";
      if (projects.indexOf(p) < 0) projects.push(p);
    });
    projects.sort();

    var children = { root: [] };
    var parentOf = {};
    function attach(child, parent) {
      parentOf[child] = parent;
      (children[parent] = children[parent] || []).push(child);
    }
    projects.forEach(function (p) { attach("project:" + p, "root"); });
    var deepest = {};
    nodes.forEach(function (n) {
      var p = "project:" + (n.project || "");
      if (isMemory(n)) {
        var lvl = n._level == null ? 1 : n._level;
        deepest[p] = Math.max(deepest[p] || 0, lvl);
        if (n._head === n || !n._parent || !byId[n._parent.id]) attach(n.id, p);
        else attach(n.id, n._parent.id);
      }
    });
    nodes.forEach(function (n) {
      if (n.kind === "folder") attach(n.id, "project:" + (n.project || ""));
    });
    nodes.forEach(function (n) {
      if (n.kind !== "file") return;
      var r = bestRef[n.id];
      if (r && byId[r.parent]) attach(n.id, r.parent);
      else if (folderOf[n.id] && byId[folderOf[n.id]]) attach(n.id, folderOf[n.id]);
      else attach(n.id, "project:" + (n.project || ""));
    });

    var leaves = {};
    function countLeaves(id) {
      var kids = children[id] || [];
      if (!kids.length) { leaves[id] = 1; return 1; }
      var s = 0;
      kids.forEach(function (k) { s += countLeaves(k); });
      leaves[id] = s;
      return s;
    }
    countLeaves("root");

    var depth = {};
    function setDepth(id, d) {
      depth[id] = d;
      (children[id] || []).forEach(function (k) { setDepth(k, d + 1); });
    }
    setDepth("root", 0);
    /* folders sit one ring outside their project's deepest memory. Two
       passes: a file under a folder reads that folder's depth, which must
       already be set, since folders and files are not guaranteed to keep
       the node array's original order. */
    nodes.forEach(function (n) {
      if (n.kind === "folder") depth[n.id] = (deepest["project:" + (n.project || "")] || 1) + 2;
    });
    nodes.forEach(function (n) {
      if (n.kind === "file" && folderOf[n.id] && parentOf[n.id] === folderOf[n.id]) depth[n.id] = depth[folderOf[n.id]] + 1;
    });

    var pos = {};
    function place(id, a0, a1) {
      var d = depth[id] || 0;
      var mid = (a0 + a1) / 2;
      var r = d * radius;
      if (id !== "root") pos[id] = { x: Math.cos(mid) * r, y: Math.sin(mid) * r, z: 0 };
      var kids = (children[id] || []).slice().sort(function (p, q) { return leaves[q] - leaves[p]; });
      var total = 0;
      kids.forEach(function (k) { total += leaves[k]; });
      var a = a0;
      kids.forEach(function (k) {
        var span = (a1 - a0) * (leaves[k] / Math.max(total, 1));
        place(k, a, a + span);
        a += span;
      });
    }
    place("root", -Math.PI, Math.PI);
    var out = {};
    nodes.forEach(function (n) { if (pos[n.id]) out[n.id] = pos[n.id]; });
    return out;
  };

  /* ================================================================ 3.4
     Living shapes. Every layout but the 3D Brain (which keeps its physics)
     is plain geometry: L.compute() gives each node a track, a position that
     is a function of time, plus the structure to draw behind the stars (a
     globe's graticule, a river's channels, a web's silk, a tree's wood) and
     the labels. Both engines read the same shape: 3D draws it in space, 2D
     projects it (x, -y). t = 0 is the still picture reduced motion shows.

     Units are world units, the same in both engines. Everything is seeded
     by ids and slugs, so a shape is the same on every reload. */
  var GA = Math.PI * (3 - Math.sqrt(5)), TAU = Math.PI * 2;
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  function fnv(str) {
    var h = 2166136261;
    for (var i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); }
    return h >>> 0;
  }
  function rnd(id, salt) { return (fnv(String(id) + "|" + salt) % 100003) / 100003; }
  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }
  function timeOf(n) { var t = Date.parse(n.timestamp || ""); return isFinite(t) ? t : 0; }
  function byTime(a, b) { return timeOf(a) - timeOf(b) || (a.id < b.id ? -1 : 1); }
  function dayLabel(ms) {
    var d = new Date(ms);
    return d.getUTCDate() + " " + MONTHS[d.getUTCMonth()];
  }
  function monthLabel(ms) {
    var d = new Date(ms);
    return MONTHS[d.getUTCMonth()] + " " + d.getUTCFullYear();
  }

  /* how big a star is drawn: gravity and importance, then rank (the head of
     a project is unmissably the largest). The same number in 2D and 3D. */
  L.starRadius = function (n) {
    if (n.kind === "file") return Math.min(1.6 + 1.1 * Math.sqrt(n.degree || 0), 4.2);
    if (n.kind === "folder") return Math.min(4 + 1.4 * Math.sqrt(n.file_count || 0), 11);
    var r = Math.min((3 + Math.sqrt(n.degree || 0) * 2.6 + (n.importance || 3) * 0.8) * 0.62, 8.6);
    return r * (n._head === n ? 1.85 : [1, 1.0, 0.84, 0.72][Math.min(n._level == null ? 2 : n._level, 3)]);
  };

  /* nodes by project (largest first), each project's memories head first,
     and where a file hangs: the memory that names it most strongly, else
     its folder */
  function groupNodes(nodes, links) {
    var byId = {}, by = {}, list = [];
    nodes.forEach(function (n) { byId[n.id] = n; });
    nodes.forEach(function (n) {
      var p = n.project || "";
      if (!by[p]) { by[p] = { slug: p, mem: [], files: [], folders: [], n: 0 }; list.push(by[p]); }
      var g = by[p];
      if (n.kind === "file") g.files.push(n);
      else if (n.kind === "folder") g.folders.push(n);
      else g.mem.push(n);
      g.n++;
    });
    list.sort(function (a, b) { return b.n - a.n || (a.slug < b.slug ? -1 : 1); });
    list.forEach(function (g) {
      g.mem.sort(function (a, b) {
        return (a._head === a ? 0 : 1) - (b._head === b ? 0 : 1)
          || (a._level || 0) - (b._level || 0)
          || (b.degree || 0) - (a.degree || 0) || (a.id < b.id ? -1 : 1);
      });
    });
    var best = {}, folderOf = {};
    (links || []).forEach(function (l) {
      var s = byId[idOf(l.source)], t = byId[idOf(l.target)], kinds = l.kinds || [];
      if (!s || !t) return;
      if (kinds.indexOf("file_ref") >= 0) {
        var f = t.kind === "file" ? t : s.kind === "file" ? s : null;
        var m = f === t ? s : t;
        if (f && isMemory(m) && (!best[f.id] || (l.w || 0) > best[f.id].w)) best[f.id] = { node: m, w: l.w || 0 };
      } else if (kinds.indexOf("in_folder") >= 0) {
        var ff = s.kind === "file" ? s : t.kind === "file" ? t : null;
        var fo = ff === s ? t : s;
        if (ff && fo.kind === "folder") folderOf[ff.id] = fo;
      }
    });
    return { byId: byId, by: by, list: list, best: best, folderOf: folderOf };
  }
  function nameOf(slug, names) { return (names && names[slug]) || slug || "(no project)"; }
  function kindsOf(l) { return l.kinds || []; }
  function endsOf(l, G) { return [G.byId[idOf(l.source)], G.byId[idOf(l.target)]]; }

  /* ------------------------------------------------------------ Organic
     Every project is a small galaxy: its head at the core, its memories on
     spiral arms in a tilted disc, its files a halo of ice dust, its folders
     satellites. Each galaxy turns and drifts by itself; only the strongest
     link between two galaxies is drawn bright (the threads). */
  function shapeGalaxies(nodes, links, G, opts) {
    var cl = G.list.map(function (g, i) {
      var m = g.mem.length + g.files.length;
      var t = (i + 0.5) / G.list.length, zz = 1 - 2 * t, rr = Math.sqrt(Math.max(0, 1 - zz * zz));
      var R = 30 + 14 * Math.sqrt(m), far = i ? 60 + 46 * Math.sqrt(i) : 0;
      return { g: g, slug: g.slug, R: R, x: Math.cos(GA * i) * rr * far,
               y: Math.sin(GA * i) * rr * far * 0.8, z: zz * far * 0.5 };
    });
    for (var it = 0; it < 140; it++) {             // push overlapping galaxies apart
      for (var a = 0; a < cl.length; a++) {
        for (var b = a + 1; b < cl.length; b++) {
          var A = cl[a], B = cl[b];
          var dx = B.x - A.x, dy = B.y - A.y, dz = B.z - A.z;
          var d = Math.sqrt(dx * dx + dy * dy + dz * dz) || 0.01;
          var need = (A.R + B.R) * 1.22 + 30;
          if (d >= need) continue;
          var push = (need - d) / d * 0.5, wa = B.R / (A.R + B.R), wb = 1 - wa;
          A.x -= dx * push * wa; A.y -= dy * push * wa; A.z -= dz * push * wa;
          B.x += dx * push * wb; B.y += dy * push * wb; B.z += dz * push * wb;
        }
      }
      cl.forEach(function (c) { c.x *= 0.985; c.y *= 0.975; c.z *= 0.95; });
    }
    var bounds = [];
    cl.forEach(function (c) {
      var s = c.slug;
      /* the disc's own tilt: mostly facing the viewer, leaning back a little */
      var nx = (rnd(s, "nx") - 0.5) * 1.3, ny = (rnd(s, "ny") - 0.5) * 1.3 + 0.35, nz = 1;
      var nl = Math.sqrt(nx * nx + ny * ny + nz * nz);
      nx /= nl; ny /= nl; nz /= nl;
      var ux = nz, uy = 0, uz = -nx, ul = Math.sqrt(ux * ux + uz * uz) || 1;   // n x up
      ux /= ul; uz /= ul;
      c.n = [nx, ny, nz]; c.u = [ux, uy, uz];
      c.v = [ny * uz - nz * uy, nz * ux - nx * uz, nx * uy - ny * ux];
      c.w = (0.016 + 0.02 * rnd(s, "w")) * (rnd(s, "d") < 0.5 ? -1 : 1) * clamp(60 / c.R, 0.5, 1.6);
      c.a = 4 + 0.05 * c.R;
      c.f = [0.05 + 0.04 * rnd(s, "f1"), 0.05 + 0.04 * rnd(s, "f2"), 0.05 + 0.04 * rnd(s, "f3")];
      c.p = [rnd(s, "p1") * TAU, rnd(s, "p2") * TAU, rnd(s, "p3") * TAU];
      var M = c.g.mem.length, arms = M < 10 ? 1 : M < 60 ? 2 : 3;
      c.g.mem.forEach(function (n, i) {
        var r = 0, th = 0;
        if (i > 0 && arms === 1) { r = c.R * Math.sqrt(i / M); th = i * GA; }
        else if (i > 0) {
          r = c.R * (0.14 + 0.86 * Math.sqrt(i / M));
          th = (i % arms) * TAU / arms + 5.4 * (r / c.R) + (rnd(n.id, "t") - 0.5) * 0.6;
        }
        n._gx = [c, r, th, (rnd(n.id, "h") - 0.5) * c.R * 0.2 * (1.2 - r / c.R), 1];
      });
      c.g.files.forEach(function (n) {
        n._gx = [c, c.R * (1.06 + 0.3 * rnd(n.id, "r")), rnd(n.id, "t") * TAU,
                 (rnd(n.id, "h") - 0.5) * c.R * 0.45, 0.55];
      });
      c.g.folders.forEach(function (n, j) {
        n._gx = [c, c.R * 1.5 + 12 * j, j * GA + rnd(n.id, "t"), (rnd(n.id, "h") - 0.5) * 10, 0.3];
      });
      var e = c.R * 1.7;
      bounds.push([c.x - e, c.y, c.z], [c.x + e, c.y, c.z], [c.x, c.y - e, c.z], [c.x, c.y + e, c.z]);
    });
    function centre(c, t, o) {
      o.x = c.x + c.a * Math.sin(t * c.f[0] + c.p[0]);
      o.y = c.y + c.a * Math.sin(t * c.f[1] + c.p[1]);
      o.z = c.z + c.a * Math.sin(t * c.f[2] + c.p[2]);
      return o;
    }
    var threads = {};
    links.forEach(function (l) {
      var e = endsOf(l, G), k = kindsOf(l);
      if (!e[0] || !e[1]) return;
      var pa = e[0].project || "", pb = e[1].project || "";
      if (k.indexOf("in_folder") >= 0) { l._lk = 0.3; return; }
      if (k.indexOf("file_ref") >= 0) { l._lk = 0.4; return; }
      if (pa === pb) { l._lk = 0.2; return; }
      l._lk = 0.04;
      /* each galaxy keeps one thread: its strongest link to any other */
      [pa, pb].forEach(function (key) {
        if (!threads[key] || (l.w || 0) > (threads[key].w || 0)) threads[key] = l;
      });
    });
    Object.keys(threads).forEach(function (k) { threads[k]._lk = 2.1; threads[k]._thread = 1; });
    var tmp = { x: 0, y: 0, z: 0 };
    return {
      mode: "organic", view: [0, 0.16, 1], shell: 0.6, bounds: bounds,
      deco: { kind: "galaxies", clusters: cl, centre: centre },
      labels: cl.filter(function (c) { return c.g.mem.length; }).map(function (c) {
        return { text: nameOf(c.slug, opts.names), cls: "project", pos: function (t, o) {
          centre(c, t, o); o.y -= c.R * 0.86 + 16; return o; } };
      }),
      track: function (n, t, o) {
        var q = n._gx, c = q[0];
        centre(c, t, tmp);
        var ang = q[2] + c.w * q[4] * t, cs = Math.cos(ang) * q[1], sn = Math.sin(ang) * q[1];
        var h = q[3] + 0.6 * Math.sin(t * 0.8 + q[2] * 3);
        o.x = tmp.x + cs * c.u[0] + sn * c.v[0] + h * c.n[0];
        o.y = tmp.y + cs * c.u[1] + sn * c.v[1] + h * c.n[1];
        o.z = tmp.z + cs * c.u[2] + sn * c.v[2] + h * c.n[2];
        return o;
      },
    };
  }

  /* ---------------------------------------------------------------- Orb
     A globe. Every project is a region of the surface, sized by what it
     holds; its memories fill the region from the centre out, its files lie
     along its coast, its folders hang above it as moons. The whole globe
     turns on a tilted axis. */
  function shapeOrb(nodes, links, G, opts) {
    var N = nodes.length, R = Math.max(150, 9.6 * Math.sqrt(N)), P = G.list.length;
    var caps = G.list.map(function (g, i) {
      var t = (i + 0.5) / P, z = 1 - 2 * t, r = Math.sqrt(Math.max(0, 1 - z * z));
      return { g: g, a: Math.acos(clamp(1 - 2 * g.n / N, -1, 1)),
               x: Math.cos(GA * i) * r, y: Math.sin(GA * i) * r, z: z };
    });
    for (var it = 0; it < 80; it++) {              // regions push each other apart
      for (var a = 0; a < P; a++) {
        for (var b = a + 1; b < P; b++) {
          var A = caps[a], B = caps[b];
          var dot = clamp(A.x * B.x + A.y * B.y + A.z * B.z, -1, 1);
          var ang = Math.acos(dot), need = (A.a + B.a) * 0.9;
          if (ang >= need) continue;
          var dx = B.x - A.x, dy = B.y - A.y, dz = B.z - A.z;
          var dl = Math.sqrt(dx * dx + dy * dy + dz * dz) || 0.01;
          var push = (need - ang) * 0.3 / dl, wa = B.a / (A.a + B.a), wb = 1 - wa;
          A.x -= dx * push * wa; A.y -= dy * push * wa; A.z -= dz * push * wa;
          B.x += dx * push * wb; B.y += dy * push * wb; B.z += dz * push * wb;
          [A, B].forEach(function (c) {
            var l = Math.sqrt(c.x * c.x + c.y * c.y + c.z * c.z) || 1;
            c.x /= l; c.y /= l; c.z /= l;
          });
        }
      }
    }
    var tilt = 0.2, K = [Math.sin(tilt), Math.cos(tilt), 0], OMEGA = 0.03;
    function put(n, c, cosT, phi, rad) {
      var sinT = Math.sqrt(Math.max(0, 1 - cosT * cosT));
      var lx = sinT * Math.cos(phi), ly = sinT * Math.sin(phi);
      var px = (lx * c.u[0] + ly * c.v[0] + cosT * c.x) * rad,
          py = (lx * c.u[1] + ly * c.v[1] + cosT * c.y) * rad,
          pz = (lx * c.u[2] + ly * c.v[2] + cosT * c.z) * rad;
      var kp = K[0] * px + K[1] * py + K[2] * pz;
      /* the part along the axis stays; the rest turns around it */
      n._ob = [K[0] * kp, K[1] * kp, K[2] * kp,
               px - K[0] * kp, py - K[1] * kp, pz - K[2] * kp,
               K[1] * pz - K[2] * py, K[2] * px - K[0] * pz, K[0] * py - K[1] * px];
    }
    var regions = [], labels = [];
    caps.forEach(function (c) {
      var ux = c.z, uy = 0, uz = -c.x, ul = Math.sqrt(ux * ux + uz * uz);
      if (ul < 1e-4) { ux = 1; uz = 0; ul = 1; }
      c.u = [ux / ul, 0, uz / ul];
      c.v = [c.y * c.u[2] - c.z * c.u[1], c.z * c.u[0] - c.x * c.u[2], c.x * c.u[1] - c.y * c.u[0]];
      var ae = P === 1 ? Math.PI : c.a * 0.86;
      var members = c.g.mem.concat(c.g.files), m = Math.max(members.length, 1);
      members.forEach(function (n, i) {
        put(n, c, 1 - (i + 0.5) / m * (1 - Math.cos(ae)), i * GA, n.kind === "file" ? R * 0.985 : R);
      });
      c.g.folders.forEach(function (n, j) {
        put(n, c, Math.cos(Math.min(ae * 0.45, 0.12 + 0.07 * j)), j * GA + 1, R * 1.1);
      });
      if (P > 1) regions.push({ c: [c.x, c.y, c.z], u: c.u, v: c.v, a: Math.min(ae + 0.03, Math.PI - 0.05) });
      if (c.g.mem.length) {
        var anchor = {};
        put(anchor, c, 1, 0, R * 1.17);
        labels.push({ text: nameOf(c.g.slug, opts.names), cls: "project", globe: R,
                      pos: function (t, o) { return spin(anchor._ob, t, o); } });
      }
    });
    function spin(q, t, o) {
      var a = OMEGA * t, cs = Math.cos(a), sn = Math.sin(a);
      o.x = q[0] + q[3] * cs + q[6] * sn;
      o.y = q[1] + q[4] * cs + q[7] * sn;
      o.z = q[2] + q[5] * cs + q[8] * sn;
      return o;
    }
    links.forEach(function (l) {
      var k = kindsOf(l);
      l._lk = k.indexOf("in_folder") >= 0 ? 0.25 : k.indexOf("file_ref") >= 0 ? 0.3 : 0.34;
    });
    return {
      mode: "orb", view: [0, 0.22, 1], shell: 1, shellR: R * 1.04, R: R, arc: R,
      bounds: [[R * 1.2, 0, 0], [-R * 1.2, 0, 0], [0, R * 1.2, 0], [0, -R * 1.2, 0]],
      deco: { kind: "globe", R: R, axis: K, omega: OMEGA, regions: regions },
      labels: labels,
      track: function (n, t, o) { return spin(n._ob, t, o); },
    };
  }

  /* --------------------------------------------------------------- Flow
     Time is a river. Every memory sits where it happened along the river
     (oldest at the source, newest at the mouth, evenly spaced by order);
     every project is a channel, its sessions in the current, everything
     else branching off to the banks, its files as stones under the water.
     down = true runs top to bottom, else left to right. */
  function shapeFlow(nodes, links, G, opts, down) {
    var N = nodes.length;
    var order = nodes.filter(isMemory).sort(byTime), M = Math.max(order.length, 1);
    var aspect = down ? 1.15 : 2.0;
    var W = Math.sqrt(Math.max(N, 30) * 1500 / aspect), Len = W * aspect;
    order.forEach(function (n, i) { n._u = M > 1 ? i / (M - 1) : 0.5; });
    /* channels across the river: the busiest in the middle, the rest
       alternating to either bank */
    var seq = [];
    G.list.forEach(function (g, i) { if (i % 2) seq.push(g); else seq.unshift(g); });
    var wsum = 0;
    seq.forEach(function (g) { g._w = Math.pow(g.n, 0.62) + 1; wsum += g._w; });
    var at = 0, lanes = [];
    seq.forEach(function (g) {
      var hw = W * g._w / wsum / 2, s = g.slug;
      var us = g.mem.map(function (n) { return n._u; });
      var u0 = us.length ? Math.min.apply(null, us) : 0.3, u1 = us.length ? Math.max.apply(null, us) : 0.7;
      if (u1 - u0 < 0.06) { u0 = Math.max(0, u0 - 0.03); u1 = Math.min(1, u1 + 0.03); }
      var lane = { g: g, slug: s, c: at + hw - W / 2, hw: hw, A: Math.min(hw * 0.35, 22),
                   k: 1.2 + rnd(s, "k") * 1.3, phi: rnd(s, "phi") * TAU, u0: u0, u1: u1 };
      at += hw * 2;
      lanes.push(lane);
      g._lane = lane;
    });
    function mid(lane, u) { return lane.c + lane.A * Math.sin(TAU * lane.k * u + lane.phi); }
    function set(n, along, across, z, amp) {
      n._fl = [along, across, z, rnd(n.id, "ph") * TAU, amp];
    }
    lanes.forEach(function (lane) {
      var g = lane.g;
      var spine = g.mem.filter(function (n) { return n.type === "session" || n.type === "handover"; });
      if (!spine.length && g.mem.length) spine = [g.mem[0]];
      var onSpine = {};
      spine.forEach(function (n) { onSpine[n.id] = true; });
      var room = Math.max(1, Math.floor((lane.hw - 8) / 12));
      var last = { "-1": [], "1": [] };
      g.mem.slice().sort(byTime).forEach(function (n) {
        var along = n._u * Len, c = mid(lane, n._u);
        if (onSpine[n.id]) { n._side = 0; set(n, along, c, 0, 2); return; }
        var side = rnd(n.id, "s") < 0.5 ? -1 : 1, slots = last[side], pick = 0, oldest = Infinity;
        /* each memory has a place across the channel it would like (so the
           whole width is used); it takes the nearest free one */
        var want = Math.floor(rnd(n.id, "sl") * room);
        for (var s0 = 0; s0 < room; s0++) {
          var s = (want + (s0 % 2 ? (s0 + 1) >> 1 : -(s0 >> 1)) + room * 2) % room;
          var was = slots[s] == null ? -Infinity : slots[s];
          if (along - was >= 13) { pick = s; oldest = -1; break; }
          if (was < oldest) { oldest = was; pick = s; }
        }
        slots[pick] = along;
        n._side = side;
        set(n, along, c + side * (12 + pick * 12), (rnd(n.id, "z") - 0.5) * 30, 2);
      });
      var nf = Math.max(g.folders.length, 1);
      g.folders.forEach(function (n, j) {
        set(n, (lane.u0 + (j + 0.5) / nf * (lane.u1 - lane.u0)) * Len,
            lane.c + (j % 2 ? 1 : -1) * (lane.hw + 8), -12, 0);
      });
    });
    var hung = {};
    nodes.forEach(function (n) {
      if (n.kind !== "file") return;
      var lane = (G.by[n.project || ""] || {})._lane || lanes[0];
      var b = G.best[n.id], fo = G.folderOf[n.id], host = b ? b.node : fo;
      if (host && host._fl) {
        var k = hung[host.id] = (hung[host.id] || 0) + 1;
        var side = host._side || (rnd(n.id, "s") < 0.5 ? -1 : 1);
        set(n, host._fl[0] + (rnd(n.id, "a") - 0.5) * 12, host._fl[1] + side * (10 + 7 * k),
            -20 - 4 * k, 1.2);
      } else {
        set(n, (lane.u0 + rnd(n.id, "a") * (lane.u1 - lane.u0)) * Len,
            lane.c + (rnd(n.id, "s") < 0.5 ? -1 : 1) * (lane.hw + 4), -20, 1.2);
      }
    });
    /* the time axis: month marks where the months begin in the order */
    var ticks = [], times = order.map(timeOf);
    function uAt(ms) {
      var lo = 0, hi = times.length;
      while (lo < hi) { var m2 = (lo + hi) >> 1; if (times[m2] < ms) lo = m2 + 1; else hi = m2; }
      return M > 1 ? lo / (M - 1) : 0.5;
    }
    if (times.length && times[0] > 0) {
      var d0 = new Date(times[0]), end = times[times.length - 1];
      var cur = Date.UTC(d0.getUTCFullYear(), d0.getUTCMonth() + 1, 1), lastU = -1, guard = 0;
      while (cur <= end && guard++ < 240) {
        var u = uAt(cur);
        if (u - lastU >= 0.075 && u < 0.985) { ticks.push({ u: u, text: monthLabel(cur) }); lastU = u; }
        var d = new Date(cur);
        cur = Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1);
      }
      if (ticks.length < 3) {
        ticks = [0.02, 0.26, 0.5, 0.74, 0.98].map(function (q) {
          return { u: q, text: dayLabel(times[Math.round(q * (times.length - 1))]) };
        });
      }
    }
    function xyz(along, across, z, o) {
      if (down) { o.x = across; o.y = Len / 2 - along; }
      else { o.x = along - Len / 2; o.y = -across; }
      o.z = z;
      return o;
    }
    var labels = [], edge = W / 2 + 30;
    ticks.forEach(function (tk) {
      labels.push({ text: tk.text, cls: down ? "tick left" : "tick below", pos: function (t, o) {
        return xyz(tk.u * Len, down ? -edge : edge, 0, o); } });
    });
    lanes.forEach(function (lane) {
      if (!lane.g.mem.length) return;
      labels.push({ text: nameOf(lane.slug, opts.names), cls: down ? "project above" : "project left",
        pos: function (t, o) { return xyz(lane.u0 * Len - 18, mid(lane, lane.u0), 0, o); } });
    });
    links.forEach(function (l) {
      var e = endsOf(l, G), k = kindsOf(l);
      if (!e[0] || !e[1]) return;
      l._lk = k.indexOf("session_chain") >= 0 ? 1.6 : k.indexOf("in_folder") >= 0 ? 0.25
            : k.indexOf("file_ref") >= 0 ? 0.45
            : (e[0].project || "") === (e[1].project || "") ? 0.4 : 0.14;
      if (e[0]._u != null && e[1]._u != null) l._dir = e[1]._u >= e[0]._u ? 1 : -1;   // downstream
    });
    var tmp = { x: 0, y: 0, z: 0 }, pad = edge + 40;
    return {
      mode: down ? "flow_down" : "flow_right", flat: true, shell: 0,
      view: down ? [0, 0.05, 1] : [0, 0.1, 1],
      bounds: [xyz(-30, -pad, 0, {}), xyz(-30, pad, 0, {}), xyz(Len + 30, -pad, 0, {}),
               xyz(Len + 30, pad, 0, {})].map(function (p) { return [p.x, p.y, p.z]; }),
      deco: { kind: "river", down: down, Len: Len, W: W, edge: edge, lanes: lanes, ticks: ticks,
              mid: mid, xyz: xyz },
      labels: labels,
      track: function (n, t, o) {
        var q = n._fl;
        return xyz(q[0], q[1] + q[4] * Math.sin(q[0] * 0.035 - t * 0.9 + q[3]),
                   q[2] + (q[4] ? 1.4 * Math.sin(q[0] * 0.05 - t * 0.7 + q[3]) : 0), o);
      },
    };
  }

  /* ---------------------------------------------------------------- Web
     A spider's web. The hub is the most connected memory of all; every
     project is a spoke; the rings are age (the newest ring nearest the
     hub). Memories sit on the silk like dew, files on the outer frame,
     folders where the web is anchored. */
  L.WEB_SAG = 0.055;
  function shapeWeb(nodes, links, G, opts) {
    var mems = nodes.filter(isMemory), hub = null;
    mems.forEach(function (n) {
      if (n._head === n && (!hub || (n.degree || 0) > (hub.degree || 0))) hub = n;
    });
    var rest = mems.filter(function (n) { return n !== hub; }).sort(function (a, b) { return byTime(b, a); });
    var K = clamp(Math.round(Math.sqrt(rest.length) / 1.7), 3, 9);
    var r0 = 56, dr = clamp(40 + 130 / K, 46, 76), rings = [], rsum = 0;
    for (var k = 0; k < K; k++) { rings.push(r0 + k * dr); rsum += rings[k]; }
    var ringOfMem = {}, ringOldest = [], idx = 0;
    for (k = 0; k < K; k++) {                       // outer rings are longer, so they hold more
      var take = k === K - 1 ? rest.length - idx : Math.round(rest.length * rings[k] / rsum);
      for (var j = 0; j < take && idx < rest.length; j++, idx++) {
        ringOfMem[rest[idx].id] = k;
        ringOldest[k] = timeOf(rest[idx]);
      }
    }
    /* sectors: one per project, wide by what it holds; every sector gets
       an odd number of radials so its own spoke is the middle one */
    var wsum = 0;
    G.list.forEach(function (g) { g._w = Math.sqrt(g.n) + 0.6; wsum += g._w; });
    var radials = [], at = -Math.PI / 2, sectors = [];
    G.list.forEach(function (g) {
      var span = TAU * g._w / wsum, cnt = Math.max(1, Math.round(span / 0.24));
      if (cnt % 2 === 0) cnt++;
      for (var i = 0; i < cnt; i++) {
        radials.push({ a: at + (i + 0.5) / cnt * span, main: i === (cnt - 1) / 2 });
      }
      var sec = { g: g, a: at + span / 2, span: span };
      sectors.push(sec);
      g._sec = sec;
      at += span;
    });
    function sagAt(theta) {                         // how far the silk dips at this angle
      var n = radials.length, lo = 0;
      if (n < 2) return 0;
      for (var i = 0; i < n; i++) if (radials[i].a <= theta) lo = i;
      var a0 = radials[lo].a, a1 = lo + 1 < n ? radials[lo + 1].a : radials[0].a + TAU;
      if (theta < radials[0].a) { a0 = radials[n - 1].a - TAU; a1 = radials[0].a; }
      return L.WEB_SAG * Math.sin(Math.PI * clamp((theta - a0) / Math.max(a1 - a0, 1e-6), 0, 1));
    }
    var far = rings[K - 1];
    function put(n, theta, r) {
      var rr = r * (1 - sagAt(theta));
      n._wb = [Math.cos(theta) * rr, Math.sin(theta) * rr, rr, rnd(n.id, "ph") * TAU];
      if (rr > far) far = rr;
    }
    function fill(list, sec, r, band, grow) {       // rows of dew along one ring
      var c = list.length;
      if (!c) return 0;
      var cap = Math.max(1, Math.floor(sec.span * 0.9 * r / 17)), rows = Math.ceil(c / cap);
      list.forEach(function (n, i) {
        var row = Math.floor(i / cap), inRow = row < rows - 1 ? cap : c - cap * (rows - 1);
        var step = Math.min(sec.span * 0.9 / inRow, 24 / r);
        var off = grow ? row * band : (row - (rows - 1) / 2) * Math.min(band, dr * 0.8 / rows);
        put(n, sec.a + ((i % cap) - (inRow - 1) / 2) * step, r + off);
      });
      return rows;
    }
    if (hub) { hub._wb = [0, 0, 0, 0]; }
    var fileR = rings[K - 1] + dr * 0.85, maxRows = 1;
    sectors.forEach(function (sec) {
      var cells = [];
      sec.g.mem.forEach(function (n) {
        if (n === hub) return;
        var rk = ringOfMem[n.id] || 0;
        (cells[rk] = cells[rk] || []).push(n);
      });
      cells.forEach(function (list, rk) { if (list) fill(list, sec, rings[rk], 15, false); });
      maxRows = Math.max(maxRows, fill(sec.g.files, sec, fileR, 10, true));
    });
    var anchorR = fileR + maxRows * 10 + 22;
    sectors.forEach(function (sec) {
      sec.g.folders.forEach(function (n, j) { put(n, sec.a, (anchorR + j * 16) / (1 - sagAt(sec.a))); });
    });
    var RF = Math.max(far + 20, anchorR);
    var labels = sectors.filter(function (s) { return s.g.mem.length; }).map(function (sec) {
      var cx = Math.cos(sec.a), cy = Math.sin(sec.a);
      return { text: nameOf(sec.g.slug, opts.names),
               cls: "project " + (cx > 0.3 ? "right" : cx < -0.3 ? "left" : cy > 0 ? "above" : "below"),
               pos: function (t, o) { o.x = cx * (RF + 16); o.y = cy * (RF + 16); o.z = 0; return o; } };
    });
    var seam = sectors.length ? sectors[0].a - sectors[0].span / 2 : -Math.PI / 2;
    rings.forEach(function (r, k2) {
      if (!ringOldest[k2]) return;
      labels.push({ text: dayLabel(ringOldest[k2]) + " " + String(new Date(ringOldest[k2]).getUTCFullYear()).slice(2),
                    cls: "tick", pos: function (t, o) {
        o.x = Math.cos(seam) * (r + 5); o.y = Math.sin(seam) * (r + 5); o.z = 0; return o; } });
    });
    links.forEach(function (l) {
      var k = kindsOf(l);
      l._lk = k.indexOf("in_folder") >= 0 ? 0.2 : k.indexOf("file_ref") >= 0 ? 0.3 : 0.24;
    });
    return {
      mode: "web", flat: true, shell: 0, view: [0, -0.16, 1], hub: hub,
      bounds: [[RF * 1.12, 0, 0], [-RF * 1.12, 0, 0], [0, RF * 1.12, 0], [0, -RF * 1.12, 0]],
      deco: { kind: "web", radials: radials, rings: rings, sag: L.WEB_SAG, r0: r0, RF: RF },
      labels: labels,
      track: function (n, t, o) {
        var q = n._wb, f = q[2] / RF;
        o.x = q[0] + 1.6 * f * Math.sin(t * 0.37 + 1);
        o.y = q[1] + 1.2 * f * Math.sin(t * 0.29);
        o.z = 3 * f * Math.sin(t * 1.1 - q[2] * 0.018);
        return o;
      },
    };
  }

  /* --------------------------------------------------------------- Tree
     A real tree. The trunk carries one limb per project (the busiest low
     and long, the small ones up in the crown); the project's head sits on
     its limb; what hangs off the head are twigs along the limb, and what
     hangs off those are leaves. Files are leaves of the memory that names
     them; folders hang under the limb as fruit. It sways.
     opts.flat (2D) fans the limbs left and right in the picture plane. */
  function shapeTree(nodes, links, G, opts) {
    var flat = !!opts.flat, byId = G.byId, kids = {};
    function attach(c, p) { (kids[p] = kids[p] || []).push(c); }
    nodes.forEach(function (n) {
      if (!isMemory(n) || n._head === n) return;
      var p = n._parent && byId[n._parent.id] ? n._parent : (n._head && byId[n._head.id] ? n._head : null);
      if (p) attach(n.id, p.id);
    });
    nodes.forEach(function (n) {
      if (n.kind !== "file") return;
      var b = G.best[n.id], fo = G.folderOf[n.id];
      if (b) attach(n.id, b.node.id); else if (fo) attach(n.id, fo.id);
    });
    var leaves = {};
    function count(id, depth) {
      var ks = kids[id] || [], s = 0;
      if (!ks.length || depth > 60) { leaves[id] = 1; return 1; }
      ks.forEach(function (k) { s += count(k, depth + 1); });
      leaves[id] = s + 1;
      return s + 1;
    }
    var limbs = G.list.map(function (g) {
      var head = g.mem.length ? g.mem[0] : null, size = head ? count(head.id, 0) : 1;
      g.mem.forEach(function (n) { if (leaves[n.id] == null) count(n.id, 0); });
      g.folders.forEach(function (n) { count(n.id, 0); });
      return { g: g, head: head, size: size + g.folders.length, len: 110 + 27 * Math.sqrt(size) };
    });
    var P = limbs.length, N = nodes.length, longest = 0;
    limbs.forEach(function (lb) { if (lb.len > longest) longest = lb.len; });
    var H = clamp(130 + 46 * Math.sqrt(P) + 0.45 * longest, 240, 900);
    var UP = [0, 1, 0], pos = {}, wood = [];
    function trunkAt(t) {
      return [12 * Math.sin(t * Math.PI * 1.2), t * H, flat ? 0 : 8 * Math.sin(t * Math.PI * 0.9 + 1)];
    }
    function norm(v) {
      var l = Math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) || 1;
      return [v[0] / l, v[1] / l, v[2] / l];
    }
    function cross(a, b) { return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]; }
    /* a branch: starts at A, leaves along d, curls up toward the light.
       C(t) = A + d len t + up curl len t^2, a quadratic curve. */
    function grow(id, A, d, len, depth, r0) {
      var curl = depth === 1 ? 0.2 : 0.12, ks = (kids[id] || []).slice();
      function C(t) {
        return [A[0] + d[0] * len * t, A[1] + d[1] * len * t + curl * len * t * t, A[2] + d[2] * len * t];
      }
      var own = depth === 1 ? 0.2 : ks.length ? 0.42 : 1;
      pos[id] = C(own);
      wood.push({ p0: A, p1: C(0.5), p2: C(1), c: [A[0] + d[0] * len / 2, A[1] + d[1] * len / 2, A[2] + d[2] * len / 2],
                  r0: r0, r1: Math.max(0.18, r0 * 0.22), depth: depth });
      ks.sort(function (a, b) { return (leaves[b] || 1) - (leaves[a] || 1) || (a < b ? -1 : 1); });
      ks.forEach(function (c, j) {
        if (depth > 40) { pos[c] = C(1); return; }
        var t = own + (1 - own) * (j + 0.7) / (ks.length + 0.4);
        var At = C(t), T = norm([d[0], d[1] + 2 * curl * t, d[2]]);
        var e1 = flat ? norm([-T[1], T[0], 0]) : norm(cross(T, Math.abs(T[1]) > 0.92 ? [1, 0, 0] : UP));
        var e2 = cross(T, e1);
        var psi = flat ? (j % 2 ? Math.PI : 0) : j * GA + rnd(c, "psi");
        var sb = 0.82, cb = 0.57;                  // about 55 degrees off the branch
        var cd = norm([T[0] * cb + (e1[0] * Math.cos(psi) + e2[0] * Math.sin(psi)) * sb,
                       T[1] * cb + (e1[1] * Math.cos(psi) + e2[1] * Math.sin(psi)) * sb + 0.28,
                       T[2] * cb + (e1[2] * Math.cos(psi) + e2[2] * Math.sin(psi)) * sb]);
        var isLeaf = !(kids[c] || []).length;
        var cl = (isLeaf ? 22 + 12 * rnd(c, "l") : 30 + 15 * Math.sqrt(leaves[c] || 1)) * (1.12 - 0.5 * t)
               * (depth === 1 ? 1 : 0.8);
        grow(c, At, cd, cl, depth + 1, Math.max(0.3, r0 * 0.42));
      });
      return C;
    }
    var tips = [];
    limbs.forEach(function (lb, i) {
      var th = 0.38 + 0.62 * (i + 0.5) / P, base = trunkAt(th);
      var el = (18 + 60 * (P > 1 ? i / (P - 1) : 0.6)) * Math.PI / 180;
      var az = flat ? (i % 2 ? Math.PI : 0) : i * GA + 0.4;
      var d = [Math.cos(el) * Math.cos(az), Math.sin(el), Math.cos(el) * Math.sin(az)];
      var r0 = clamp(1.6 + 0.55 * Math.sqrt(lb.size), 2.2, 9);
      var C;
      if (lb.head) C = grow(lb.head.id, base, d, lb.len, 1, r0);
      else {
        var len = lb.len;
        C = function (t) { return [base[0] + d[0] * len * t, base[1] + d[1] * len * t + 0.2 * len * t * t, base[2] + d[2] * len * t]; };
        wood.push({ p0: base, p1: C(0.5), p2: C(1), c: [base[0] + d[0] * len / 2, base[1] + d[1] * len / 2, base[2] + d[2] * len / 2],
                    r0: r0, r1: 0.3, depth: 1 });
      }
      /* fruit: the project's folders hang under the limb, their files below them */
      lb.g.folders.forEach(function (n, j) {
        var at = C(0.3 + 0.5 * (j + 0.5) / lb.g.folders.length), drop = 30 + 12 * (j % 3);
        pos[n.id] = [at[0], at[1] - drop, at[2]];
        wood.push({ p0: at, p1: [at[0], at[1] - drop / 2, at[2]], p2: pos[n.id], c: [at[0], at[1] - drop / 2, at[2]],
                    r0: 0.3, r1: 0.2, depth: 3 });
        (kids[n.id] || []).forEach(function (f, q) {
          var a = q * GA, rr = 9 + 4 * Math.sqrt(q);
          pos[f] = [pos[n.id][0] + Math.cos(a) * rr, pos[n.id][1] - 14 - 3 * Math.sqrt(q),
                    pos[n.id][2] + (flat ? 0 : Math.sin(a) * rr)];
        });
      });
      lb.tip = C(1);
      tips.push(lb);
    });
    /* whatever is still unplaced (a file nobody names) rests at the foot */
    var stray = 0;
    nodes.forEach(function (n) {
      if (pos[n.id]) return;
      var a = stray++ * GA;
      pos[n.id] = [Math.cos(a) * (60 + 5 * stray), 4 + 4 * rnd(n.id, "y"), flat ? 0 : Math.sin(a) * (60 + 5 * stray)];
    });
    var tr = clamp(6 + 1.8 * Math.sqrt(P) + 0.02 * N, 8, 22);
    wood.push({ p0: trunkAt(0), p1: trunkAt(0.5), p2: trunkAt(1), r0: tr, r1: tr * 0.34, depth: 0,
                c: [2 * trunkAt(0.5)[0] - (trunkAt(0)[0] + trunkAt(1)[0]) / 2, H / 2,
                    2 * trunkAt(0.5)[2] - (trunkAt(0)[2] + trunkAt(1)[2]) / 2] });
    for (var r = 0; r < 6; r++) {                   // roots
      var ra = flat ? (r % 2 ? Math.PI : 0) : r * TAU / 6 + 0.3, rl = 70 + 44 * rnd("root", r);
      var tipR = [Math.cos(ra) * rl * (flat ? 0.6 + 0.25 * r : 1), -20 - 20 * rnd("rootd", r), flat ? 0 : Math.sin(ra) * rl];
      wood.push({ p0: [0, 10, 0], p2: tipR, c: [tipR[0] * 0.55, 2, tipR[2] * 0.55],
                  p1: [tipR[0] * 0.4, 0, tipR[2] * 0.4], r0: tr * 0.5, r1: 0.4, depth: 0 });
    }
    /* centre the whole tree on the origin, and remember where the ground is */
    var lo = Infinity, hi = -Infinity, wide = 0;
    Object.keys(pos).forEach(function (id) {
      var p = pos[id];
      if (p[1] < lo) lo = p[1];
      if (p[1] > hi) hi = p[1];
      wide = Math.max(wide, Math.abs(p[0]), Math.abs(p[2]));
    });
    lo = Math.min(lo, -40); hi = Math.max(hi, H);
    var shift = -(lo + hi) / 2, ground = lo + shift, tall = hi - lo, amp = 0.03 * tall;
    wood.forEach(function (w) { ["p0", "p1", "p2", "c"].forEach(function (k) { w[k] = [w[k][0], w[k][1] + shift, w[k][2]]; }); });
    function sway(x, y, z, t, o) {
      var hh = clamp((y - ground) / tall, 0, 1.3), s = hh * hh;
      o.x = x + amp * s * (Math.sin(t * 0.5 + x * 0.006 + z * 0.004) + 0.35 * Math.sin(t * 1.13 + y * 0.02));
      o.y = y;
      o.z = z + (flat ? 0 : amp * 0.6 * s * Math.sin(t * 0.43 + x * 0.005 + 1.7));
      return o;
    }
    nodes.forEach(function (n) {
      var p = pos[n.id], leaf = !(kids[n.id] || []).length;
      n._tr = [p[0], p[1] + shift, p[2], rnd(n.id, "ph") * TAU, leaf ? 0.9 : 0.3];
    });
    links.forEach(function (l) { l._lk = 0.05; });
    return {
      mode: "tree", shell: 0, view: [0.22, 0.06, 1], flatBend: flat,
      bounds: [[wide + 30, ground, 0], [-wide - 30, ground, 0], [0, ground + tall + 20, 0], [0, ground - 12, 0],
               [0, 0, wide], [0, 0, -wide]],
      deco: { kind: "tree", wood: wood, ground: ground, tall: tall, amp: amp, flat: flat },
      labels: tips.filter(function (lb) { return lb.head; }).map(function (lb) {
        var tp = [lb.tip[0], lb.tip[1] + shift, lb.tip[2]];
        return { text: nameOf(lb.g.slug, opts.names), cls: "project " + (tp[0] >= 0 ? "right" : "left"),
                 pos: function (t, o) { sway(tp[0], tp[1], tp[2], t, o); o.y += 18; return o; } };
      }),
      track: function (n, t, o) {
        var q = n._tr;
        sway(q[0], q[1], q[2], t, o);
        o.x += q[4] * Math.sin(t * 2.3 + q[3]);
        o.y += q[4] * 0.6 * Math.sin(t * 1.9 + q[3] * 1.3);
        return o;
      },
    };
  }

  /* ----------------------------------------------------- the flat Brain
     The 2D Brain, seen from above: two hemispheres, every project a lobe.
     (In 3D the Brain keeps its physics, inside the folded glass.) The
     picture plane is x/y here; the 2D engine flips y like every shape. */
  function shapeBrainFlat(nodes, links, G, opts) {
    var B = L.BRAIN, R = Math.max(196, 48 * Math.sqrt(nodes.length));
    var ex = (1 + B.medial) * B.rx / 2 * R, ey = B.rz * 0.7 * R;
    function hx(side) { return (L.hemiCentre(side) + side * (1 - B.medial) * B.rx / 2) * R; }
    var lobes = L.brainLobes(nodes, R), pts = [];
    G.list.forEach(function (g) {
      var lb = lobes[g.slug];
      if (!lb) return;
      var count = { "-1": 0, "1": 0 };
      g.mem.concat(g.files, g.folders).forEach(function (n) {
        var side = lb.side || (rnd(n.id, "side") < 0.5 ? -1 : 1);
        var c = lb.side === 0 ? (side < 0 ? lb.left : lb.right) : lb;
        var i = count[side]++, r = 13 * Math.sqrt(i), a = i * GA;
        pts.push({ n: n, side: side, x: c.x + Math.cos(a) * r, y: c.z * 0.7 + Math.sin(a) * r,
                   rad: L.starRadius(n) });
      });
    });
    for (var it = 0; it < 10; it++) {               // nudge overlaps apart, stay inside the skin
      for (var a = 0; a < pts.length; a++) {
        for (var b = a + 1; b < pts.length; b++) {
          var p = pts[a], q = pts[b], dx = q.x - p.x, dy = q.y - p.y;
          var need = p.rad + q.rad + 3, d2 = dx * dx + dy * dy;
          if (d2 >= need * need) continue;
          var d = Math.sqrt(d2) || 0.01, k = (need - d) / d * 0.5;
          p.x -= dx * k; p.y -= dy * k; q.x += dx * k; q.y += dy * k;
        }
      }
      pts.forEach(function (p) {
        var cx = hx(p.side), lx = (p.x - cx) / ex, ly = p.y / ey, dd = Math.sqrt(lx * lx + ly * ly);
        if (dd > 0.9) { p.x = cx + lx / dd * 0.9 * ex; p.y = ly / dd * 0.9 * ey; }
      });
    }
    pts.forEach(function (p) { p.n._bf = [p.x, -p.y, hx(p.side), rnd(p.n.id, "ph") * TAU]; p.n._hemi = p.side; });
    /* links that cross the fissure are the faintest: the lobes should read */
    links.forEach(function (l) {
      var e = endsOf(l, G);
      var cross = e[0] && e[1] && e[0]._hemi !== e[1]._hemi;
      l._lk = kindsOf(l).indexOf("in_folder") >= 0 ? 0.3 : cross ? 0.16 : 0.5;
    });
    var w = hx(1) + ex + 20;
    return {
      mode: "brain", flat: true, shell: 0, view: [0, 0, 1],
      bounds: [[w, 0, 0], [-w, 0, 0], [0, ey + 20, 0], [0, -ey - 20, 0]],
      deco: { kind: "brain2d", R: R, ex: ex, ey: ey, hx: hx },
      labels: G.list.filter(function (g) { return g.mem.length && lobes[g.slug]; }).map(function (g) {
        var lb = lobes[g.slug], c = lb.side === 0 ? lb.left : lb;
        var side = lb.side || -1, lx = hx(side) + side * (ex + 12);
        return { text: nameOf(g.slug, opts.names), cls: "project " + (side < 0 ? "left" : "right"),
                 pos: function (t, o) { o.x = lx; o.y = -c.z * 0.7; o.z = 0; return o; } };
      }),
      track: function (n, t, o) {
        var q = n._bf, f = 1 + 0.012 * Math.sin(t * 0.5 + q[1] * 0.012 + q[3] * 0.2);
        o.x = q[2] + (q[0] - q[2]) * f; o.y = q[1] * f; o.z = 0;
        return o;
      },
    };
  }

  /* the one door: a shape for this layout, or null when the layout keeps
     its physics (the 3D Brain). opts: { flat: true for the 2D engine,
     names: { slug: project name } }. The nodes must already carry
     _head / _level / _parent (assignHierarchy, or nebula's own pass). */
  L.compute = function (mode, nodes, links, opts) {
    opts = opts || {};
    links = links || [];
    if (!nodes.length) return null;
    if (mode === "brain" && !opts.flat) {
      links.forEach(function (l) { l._lk = 1; l._dir = 0; l._thread = 0; });
      return null;
    }
    links.forEach(function (l) { l._lk = 1; l._dir = 0; l._thread = 0; });
    var G = groupNodes(nodes, links);
    if (mode === "brain") return shapeBrainFlat(nodes, links, G, opts);
    if (mode === "orb") return shapeOrb(nodes, links, G, opts);
    if (mode === "flow_down") return shapeFlow(nodes, links, G, opts, true);
    if (mode === "flow_right") return shapeFlow(nodes, links, G, opts, false);
    if (mode === "web") return shapeWeb(nodes, links, G, opts);
    if (mode === "tree") return shapeTree(nodes, links, G, opts);
    return shapeGalaxies(nodes, links, G, opts);
  };
})();
