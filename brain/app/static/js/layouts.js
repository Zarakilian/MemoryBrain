/* Layout shapes for the Constellation. Pure geometry, no three, no DOM:
   nebula.js (3D) and constellation.js (2D fallback) both read
   window.NebulaLayouts. Every function is deterministic for a given graph
   so a layout looks the same on every reload. */
"use strict";

(function () {
  var L = window.NebulaLayouts = {};
  L.MODES = ["organic", "orb", "flow_down", "flow_right", "web", "tree"];
  L.LABELS = { organic: "Organic", orb: "Orb", flow_down: "Flow down",
               flow_right: "Flow right", web: "Web", tree: "Tree" };

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
