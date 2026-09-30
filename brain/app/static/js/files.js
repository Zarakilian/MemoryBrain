/* Files lens: the workspace index as a browsable tree with heat. Left, the
   bound folders per project (one level of children). Right, the files under
   the chosen folder with a bar for how often memories name them. Clicking a
   row opens the file card in the inspector; Ctrl-click opens the page. Plain
   DOM on glass, no physics. */
"use strict";

(function () {
  var inited = false, pane, tree, rows, bar, extSel, sortSel, filterBox, statusEl, dangling, danglingBody, danglingCount;
  var state = { project: "", folder: "", sort: "ref_degree", ext: "", filter: "", files: [], max: 0 };

  function esc(s) { return Atlas.esc(s); }
  function status(msg) { if (statusEl) statusEl.textContent = msg; }

  function boot() {
    if (inited) return;
    inited = true;
    pane = document.getElementById("pane-files");
    if (!pane) return;
    tree = document.getElementById("files-tree");
    rows = document.getElementById("files-rows");
    bar = pane.querySelector(".files-bar");
    extSel = document.getElementById("files-ext");
    sortSel = document.getElementById("files-sort");
    filterBox = document.getElementById("files-filter");
    statusEl = document.getElementById("files-status");
    dangling = document.getElementById("files-dangling");
    danglingBody = document.getElementById("files-dangling-body");
    danglingCount = document.getElementById("files-dangling-count");
    state.project = pane.dataset.project || "";
    state.folder = pane.dataset.folder || "";
    if (bar) bar.removeAttribute("hidden");
    sortSel.addEventListener("change", function () { state.sort = sortSel.value; loadFiles(); });
    extSel.addEventListener("change", function () { state.ext = extSel.value; renderRows(); });
    filterBox.addEventListener("input", function () { state.filter = filterBox.value.trim().toLowerCase(); renderRows(); });
    rows.addEventListener("click", onRowClick);
    tree.addEventListener("click", onTreeClick);
    dangling.addEventListener("toggle", function () { if (dangling.open) loadDangling(); });
    loadTree();
  }

  /* ------------------------------------------------------------- tree */
  async function loadTree() {
    var data;
    try { data = await Atlas.getJSON("/api/ui/workspace/tree"); }
    catch (e) { tree.innerHTML = '<p class="quiet">Failed: ' + esc(e.message) + "</p>"; return; }
    var h = [];
    var any = false;
    data.projects.forEach(function (p) {
      if (!p.folders.length) return;
      any = true;
      var open = !state.project || state.project === p.slug;
      h.push('<details class="ft-project"' + (open ? " open" : "") + ">",
        '<summary><span class="pdot" style="--ph:' + Atlas.hue(p.slug) + '"></span>',
        '<a href="#" class="ft-link" data-project="' + esc(p.slug) + '" data-folder="">' + esc(p.name || p.slug) + "</a>",
        '<span class="count">' + p.file_count + "</span></summary>");
      p.folders.forEach(function (f) {
        h.push('<div class="ft-folder">',
          '<a href="#" class="ft-link" data-project="' + esc(p.slug) + '" data-folder="' + esc(f.rel_path) + '">'
            + '<span class="ft-glyph">●</span>' + esc(f.rel_path.split("/").pop() || f.rel_path) + "</a>",
          '<span class="count">' + f.file_count + "</span>",
          f.role !== "primary" ? '<span class="badge ext">' + esc(f.role) + "</span>" : "",
          "</div>");
        f.children.forEach(function (c) {
          h.push('<div class="ft-child">',
            '<a href="#" class="ft-link" data-project="' + esc(p.slug) + '" data-folder="' + esc(c.rel_path) + '">' + esc(c.name) + "</a>",
            '<span class="count">' + c.file_count + "</span></div>");
        });
      });
      h.push("</details>");
    });
    if (data.unmapped && data.unmapped.length) {
      h.push('<details class="ft-project ft-unmapped"><summary><span class="pdot all"></span>unmapped folders',
        '<span class="count">' + data.unmapped.length + "</span></summary>");
      data.unmapped.forEach(function (f) {
        h.push('<div class="ft-folder quiet">' + esc(f.rel_path) + "</div>");
      });
      h.push('<p class="quiet ft-hint">Tell your assistant where these belong, it calls set_project_info.</p></details>');
    }
    if (!any) {
      tree.innerHTML = '<div class="empty"><div><div class="empty-plate" aria-hidden="true"></div>'
        + '<p class="empty-title">No files indexed on this machine yet</p>'
        + '<p class="empty-hint">Run <code>python cli/brain.py scan --root "C:\\work\\repos" --label git --init</code>, then <code>--apply</code>, then <code>scan</code>.</p></div></div>';
      rows.innerHTML = "";
      return;
    }
    tree.innerHTML = h.join("");
    if (!state.project) {
      var first = data.projects.filter(function (p) { return p.folders.length; })[0];
      if (first) state.project = first.slug;
    }
    markTree();
    loadFiles();
  }

  function markTree() {
    tree.querySelectorAll(".ft-link").forEach(function (a) {
      a.classList.toggle("on", a.dataset.project === state.project && (a.dataset.folder || "") === (state.folder || ""));
    });
  }

  function onTreeClick(ev) {
    var a = ev.target.closest(".ft-link");
    if (!a) return;
    ev.preventDefault();
    state.project = a.dataset.project;
    state.folder = a.dataset.folder || "";
    var u = new URL(location.href);
    u.searchParams.set("lens", "files");
    u.searchParams.set("project", state.project);
    if (state.folder) u.searchParams.set("folder", state.folder); else u.searchParams.delete("folder");
    history.replaceState(null, "", u);
    markTree();
    loadFiles();
  }

  /* ------------------------------------------------------------- files */
  async function loadFiles() {
    if (!state.project) return;
    status("loading…");
    var qs = new URLSearchParams({ project: state.project, sort: state.sort, limit: 300 });
    if (state.folder) qs.set("folder", state.folder);
    var data;
    try { data = await Atlas.getJSON("/api/ui/workspace/files?" + qs); }
    catch (e) { status("failed: " + e.message); return; }
    state.files = data.files || [];
    state.max = data.max_ref_degree || 0;
    var exts = {};
    state.files.forEach(function (f) { if (f.ext) exts[f.ext] = (exts[f.ext] || 0) + 1; });
    var cur = extSel.value;
    extSel.innerHTML = '<option value="">all</option>' + Object.keys(exts).sort().map(function (e) {
      return '<option value="' + esc(e) + '">' + esc(e) + " (" + exts[e] + ")</option>";
    }).join("");
    extSel.value = exts[cur] ? cur : "";
    state.ext = extSel.value;
    renderRows();
    if (dangling.open) loadDangling(); else if (danglingCount) danglingCount.textContent = "";
  }

  function renderRows() {
    var shown = state.files.filter(function (f) {
      if (state.ext && f.ext !== state.ext) return false;
      if (state.filter && (f.rel_path || "").toLowerCase().indexOf(state.filter) < 0
          && (f.title || "").toLowerCase().indexOf(state.filter) < 0) return false;
      return true;
    });
    if (!shown.length) {
      rows.innerHTML = '<p class="quiet files-empty">' + (state.files.length ? "Nothing matches the filter." : "No files under this folder.") + "</p>";
      status((state.folder || state.project) + " · 0 files");
      return;
    }
    var h = [];
    shown.forEach(function (f) {
      var pct = state.max > 0 ? Math.round((f.ref_degree || 0) / state.max * 100) : 0;
      var name = (f.rel_path || "").split("/").pop();
      var dir = (f.rel_path || "").slice(0, -(name.length)).replace(/\/$/, "");
      h.push('<a class="file-row" href="/ui/file/' + esc(f.file_id) + '" data-file="' + esc(f.file_id) + '">',
        '<span class="heat" title="heat ' + (f.ref_degree || 0).toFixed(1) + '"><i style="width:' + pct + '%"></i></span>',
        '<span class="fname">' + esc(name) + (f.title ? '<small class="ftitle">' + esc(f.title) + "</small>" : "") + "</span>",
        '<span class="fdir">' + esc(dir) + "</span>",
        '<span class="badge ext">' + esc(f.ext || "") + "</span>",
        "<time>" + esc(String(f.mtime || "").slice(0, 10)) + "</time>",
        "</a>");
    });
    rows.innerHTML = h.join("");
    status((state.folder || state.project) + " · " + shown.length + " of " + state.files.length + " files");
  }

  function onRowClick(ev) {
    var a = ev.target.closest(".file-row");
    if (!a || ev.ctrlKey || ev.metaKey || ev.shiftKey) return;   // modifiers: let the link open the page
    ev.preventDefault();
    rows.querySelectorAll(".file-row.on").forEach(function (r) { r.classList.remove("on"); });
    a.classList.add("on");
    Atlas.inspectFile(a.dataset.file);
  }

  /* ---------------------------------------------------------- dangling */
  async function loadDangling() {
    var qs = new URLSearchParams({ limit: 100 });
    if (state.project) qs.set("project", state.project);
    var data;
    try { data = await Atlas.getJSON("/api/ui/workspace/dangling?" + qs); }
    catch (e) { danglingBody.innerHTML = '<p class="quiet">Failed: ' + esc(e.message) + "</p>"; return; }
    if (danglingCount) danglingCount.textContent = data.count;
    if (!data.items.length) { danglingBody.innerHTML = '<p class="quiet">Every mention resolved to a file.</p>'; return; }
    danglingBody.innerHTML = '<p class="quiet">Memories name these, but no indexed file matches, or several do.</p>'
      + data.items.map(function (it) {
        return '<div class="dangle"><code>' + esc(it.token) + "</code> <span class=\"count\">×" + it.mentions + "</span>"
          + (it.candidates.length ? '<span class="cands">' + it.candidates.map(esc).join(" · ") + "</span>" : "")
          + "</div>";
      }).join("");
  }

  if (window.Atlas) {
    Atlas.extraCommands = (Atlas.extraCommands || []).concat([
      { kind: "lens", label: "Lens: Files", run: function () { Atlas.showLens("files", true); } },
    ]);
    Atlas.onLens("files", boot);
  }
  window.addEventListener("DOMContentLoaded", function () {
    if (document.body.dataset.lens === "files") boot();
  });
})();
