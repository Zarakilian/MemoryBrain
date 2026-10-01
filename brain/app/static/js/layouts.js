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
})();
