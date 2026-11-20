# brain/app/ui/doctor.py
"""/ui/doctor — dependency-free in-browser diagnostics page.

Deliberately has NO dependencies: no Jinja template, no /static assets, no DB
connection at render time (the API checks run client-side). If FastAPI is up,
this page renders — even with a hosed database or a broken static mount.

The literal token __BUILD__ is replaced with the build stamp at request time
(see routes.py). Served with Cache-Control: no-store, and the page itself
verifies that the server's live stamp matches the one baked into this HTML,
so a stale cached copy diagnoses itself.

Checks (per the rebuild protocol):
  * build stamp: rendered-vs-live comparison (detects cached HTML)
  * every /api/ui/* endpoint fetches and has the right JSON shape
  * workspace layer (v2.5): map shape, last scan age, unmapped folders, homeless projects, dangling references
  * canvas 2D and WebGL contexts can be created
  * pointerdown reaches a handler ("click received at x,y")
  * elementsFromPoint probe over the click target (automatic click-eater test)
  * overlay detector: any element covering >50% of the viewport that can
    swallow pointer events
  * window.onerror / unhandledrejection capture, dumped on the page
Plus a "Copy report" button that produces a plain-text block to paste back.

3.3 adds, still with no database at render time and no static asset:
  * the age of the file index, from the workspace block of GET /status, with
    what to run when it is over a day old
  * three cleanup lists (summaries the old model wrote, corrections stated
    only in prose, stale open loops). Each is a dry run that only reads
    (/api/ui/cleanup/*); nothing changes until the user presses an apply
    button, which calls /api/ui/edit/* with the key Atlas stores
  * links to the name pages (/ui/names, /ui/name/<name>)
The report carries the counts of the cleanup lists, never their text.
"""

DOCTOR_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MemoryBrain Doctor</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  body {
    background: #201b16; color: #d8d2c8;
    font: 14px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    padding: 24px;
  }
  h1 { font-size: 18px; margin: 0 0 4px; color: #e8e2d6; }
  .sub { color: #98917f; margin-bottom: 20px; }
  .sub b { color: #c9a961; }
  section { margin-bottom: 22px; }
  h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .08em;
       color: #98917f; margin: 0 0 8px; }
  #results div, #errors div { padding: 2px 0; white-space: pre-wrap; word-break: break-word; }
  .PASS { color: #8fbf8f; }
  .FAIL { color: #e0705f; }
  .WARN { color: #d9a441; }
  .WAIT { color: #8a8578; }
  #target {
    width: 100%; max-width: 480px; height: 110px;
    border: 1px dashed #6b6355; border-radius: 6px;
    display: flex; align-items: center; justify-content: center;
    color: #b8b0a0; cursor: pointer; user-select: none;
    background: #2a241d;
  }
  button {
    background: #3a3227; color: #e8e2d6; border: 1px solid #6b6355;
    border-radius: 6px; padding: 8px 14px; font: inherit; cursor: pointer;
    margin-right: 10px;
  }
  button:hover { border-color: #c9a961; }
  #copystate { color: #8fbf8f; }
  a { color: #c9a961; }
  h3 { font-size: 13px; margin: 14px 0 2px; color: #e8e2d6; }
  .hint { color: #98917f; margin: 0 0 8px; max-width: 820px; }
  .cl { border-top: 1px solid #3a3227; padding: 2px 0 12px; max-width: 980px; }
  .state { margin-left: 4px; color: #b8b0a0; }
  .item { border: 1px solid #3a3227; border-radius: 6px; padding: 8px 10px;
          margin: 6px 0; background: #2a241d; }
  .item .line { white-space: pre-wrap; word-break: break-word; }
  .item .lab { color: #98917f; }
  .item .old { color: #c7a59b; }
  .item .new { color: #a9c7a0; }
  .item .acts { margin-top: 8px; }
  .item button { padding: 4px 10px; margin: 0 8px 0 0; }
  .item.settled { opacity: .55; }
  button:disabled { opacity: .5; cursor: progress; }
  button.armed { border-color: #e0705f; color: #f0a093; }
  input { background: #2a241d; color: #e8e2d6; border: 1px solid #6b6355;
          border-radius: 6px; padding: 7px 10px; font: inherit; }
  #keybox { border: 1px solid #d9a441; border-radius: 6px; padding: 10px;
            margin: 10px 0; max-width: 820px; }
</style>
</head>
<body>
<h1 data-doctor-ok>MemoryBrain Doctor</h1>
<div class="sub" data-doctor-ok>build <b id="stamp">__BUILD__</b> &middot; <span id="envline"></span></div>

<section data-doctor-ok>
  <h2>Checks</h2>
  <div id="results"></div>
</section>

<section data-doctor-ok>
  <h2>Pointer test &mdash; click or tap inside the box</h2>
  <div id="target">click me</div>
</section>

<section data-doctor-ok>
  <h2>JS errors captured</h2>
  <div id="errors"><div class="WAIT">(none so far)</div></div>
</section>

<section data-doctor-ok>
  <button id="rerun">Re-run checks</button>
  <button id="copy">Copy report</button>
  <span id="copystate"></span>
</section>

<section data-doctor-ok>
  <h2>Names</h2>
  <div><a href="/ui/names">Name patterns, and the names your notes mention most</a></div>
  <form action="/ui/names" method="get" style="margin-top: 8px">
    <input name="name" size="34" placeholder="a ticket, server, file or product"
           aria-label="A name to look up">
    <button type="submit">Everything about this name</button>
  </form>
</section>

<section data-doctor-ok id="cleanup">
  <h2>Cleanup</h2>
  <p class="hint">Each list starts with a dry run that only reads. Nothing changes until you
    press an apply button. Every change is audited as "ui" and can be undone from the memory's page.</p>
  <div><label>project <input id="cl-project" size="22" placeholder="all projects"></label></div>
  <div id="keybox" hidden>
    This brain has an API key, and a change needs it. Paste it once: it stays in this browser only.
    <div style="margin-top: 8px">
      <input type="password" id="cl-key" autocomplete="off" aria-label="API key">
      <button id="cl-key-save" type="button">Save key</button>
    </div>
  </div>

  <div class="cl">
    <h3>Summaries the old model wrote</h3>
    <p class="hint">Before 3.3 a small model summarised long notes, and it got details wrong.
      Apply replaces each of those summaries with the opening of its own text. A summary the
      writer gave is never touched, and the old one is kept in the audit row.</p>
    <button id="sum-dry" type="button">Dry run</button>
    <button id="sum-apply" type="button" hidden>Apply</button>
    <span id="sum-state" class="state"></span>
    <div id="sum-list"></div>
  </div>

  <div class="cl">
    <h3>Corrections stated only in prose</h3>
    <p class="hint">A newer note says it supersedes, corrects or closes an older one, but the
      older one is still active. Decide each pair: Supersede archives the older note, Correct
      keeps it and marks it disputed, Close finishes an open loop.</p>
    <button id="cor-dry" type="button">Dry run</button>
    <span id="cor-state" class="state"></span>
    <div id="cor-list"></div>
  </div>

  <div class="cl">
    <h3>Stale open loops</h3>
    <p class="hint">Open loops nobody has touched for 30 days, oldest first. Close the ones
      that are finished and leave the ones that still stand.</p>
    <button id="loop-dry" type="button">Dry run</button>
    <span id="loop-state" class="state"></span>
    <div id="loop-list"></div>
  </div>
</section>

<script>
"use strict";
/* ---- error capture: install first, before anything can throw ---- */
var JS_ERRORS = [];
function renderErrors() {
  var el = document.getElementById("errors");
  if (!JS_ERRORS.length) { el.innerHTML = '<div class="WAIT">(none so far)</div>'; return; }
  el.innerHTML = "";
  JS_ERRORS.forEach(function (e) {
    var d = document.createElement("div");
    d.className = "FAIL";
    d.textContent = e;
    el.appendChild(d);
  });
}
window.onerror = function (msg, src, line, col) {
  JS_ERRORS.push("onerror: " + msg + " @ " + (src || "?") + ":" + line + ":" + col);
  renderErrors(); setLine("js errors", "FAIL", JS_ERRORS.length + " captured (see below)");
  return false;
};
window.addEventListener("unhandledrejection", function (ev) {
  JS_ERRORS.push("unhandledrejection: " + (ev.reason && ev.reason.message || ev.reason));
  renderErrors(); setLine("js errors", "FAIL", JS_ERRORS.length + " captured (see below)");
});

/* ---- result lines ---- */
var LINES = {};           // name -> {status, detail}
var ORDER = [];
function setLine(name, status, detail) {
  if (!(name in LINES)) ORDER.push(name);
  LINES[name] = { status: status, detail: detail || "" };
  var box = document.getElementById("results");
  box.innerHTML = "";
  ORDER.forEach(function (n) {
    var r = LINES[n], d = document.createElement("div");
    d.className = r.status;
    d.textContent = "[" + r.status + "] " + n + (r.detail ? " — " + r.detail : "");
    box.appendChild(d);
  });
}

/* ---- helpers ---- */
/* the key Atlas stores in this browser: sent on changes, and on /status,
   which needs it on a brain that has one */
function brainKey() { try { return localStorage.getItem("atlas-key") || ""; } catch (e) { return ""; } }
function keyHeaders(extra) {
  var h = extra || {}, k = brainKey();
  if (k) h["X-Brain-Key"] = k;
  return h;
}
function hasKeys(obj, keys) {
  return keys.every(function (k) { return obj != null && (k in obj); });
}
async function checkJson(name, url, keys) {
  try {
    var res = await fetch(url, { cache: "no-store" });
    if (!res.ok) { setLine(name, "FAIL", "HTTP " + res.status + " " + url); return null; }
    var j = await res.json();
    if (!hasKeys(j, keys)) {
      setLine(name, "FAIL", "missing keys; got: " + Object.keys(j).join(",")); return null;
    }
    setLine(name, "PASS", url);
    return j;
  } catch (e) {
    setLine(name, "FAIL", url + " — " + (e && e.message || e)); return null;
  }
}

/* ---- the file index: how old it is, and what to run ----
   GET /status reports it (the figure the session hook reads). On a brain with
   an API key and no key in this browser, /status is closed: the same answer
   is then worked out from the workspace map. */
async function fileIndex(wm) {
  try {
    var res = await fetch("/status", { cache: "no-store", headers: keyHeaders() });
    if (res.ok) {
      var st = await res.json();
      if (st && st.workspace) return st.workspace;
    }
  } catch (e) {}
  if (!wm) return null;
  var scans = Object.keys(wm.last_scan || {}).map(function (k) { return wm.last_scan[k]; })
    .filter(Boolean).sort();
  var last = scans.length ? scans[scans.length - 1] : "";
  var hours = last ? (Date.now() - Date.parse(last)) / 36e5 : NaN;
  return { roots: (wm.roots || []).length, last_scan: last,
           scan_age_hours: isNaN(hours) ? null : Math.max(0, Math.round(hours * 10) / 10) };
}
function reportFileIndex(ws) {
  var name = "workspace: last scan";
  var run = "On the host run: brain scan (python cli/brain.py scan, from the install folder)";
  if (!ws) { setLine(name, "WARN", "the age of the file index could not be read"); return; }
  var roots = ws.roots + " root" + (ws.roots === 1 ? "" : "s");
  if (!ws.roots) {
    setLine(name, "WARN", "no folder has been scanned yet, so file references cannot resolve. "
      + "On the host run: brain scan --root <folder> --init");
    return;
  }
  var h = ws.scan_age_hours;
  if (h == null) {
    setLine(name, "WARN", roots + " set up, but no scan time is recorded. " + run);
    return;
  }
  var age = h < 48 ? h.toFixed(1) + " hours" : Math.round(h / 24) + " days";
  var when = String(ws.last_scan || "").slice(0, 16).replace("T", " ") + " UTC";
  if (h > 24) {
    setLine(name, "WARN", "the file index is " + age + " old (last scan " + when + ", " + roots
      + "). That is over a day, so files added since then do not resolve. " + run);
  } else {
    setLine(name, "PASS", "the file index was scanned " + age + " ago (" + when + ", " + roots + ")");
  }
}

/* ---- the checks ---- */
async function runChecks() {
  var stamp = document.getElementById("stamp").textContent.trim();
  document.getElementById("envline").textContent =
    window.innerWidth + "x" + window.innerHeight + " @ dpr " + (window.devicePixelRatio || 1);

  // 1. build stamp: live server vs the one baked into this HTML
  try {
    var v = await fetch("/api/ui/version", { cache: "no-store" });
    var vj = await v.json();
    if (!vj.build) setLine("build stamp", "FAIL", "/api/ui/version returned no build field");
    else if (vj.build !== stamp)
      setLine("build stamp", "FAIL",
        "THIS PAGE IS STALE/CACHED — page says " + stamp + ", server says " + vj.build +
        ". Hard-reload (Ctrl+Shift+R).");
    else setLine("build stamp", "PASS", stamp);
  } catch (e) {
    setLine("build stamp", "FAIL", "/api/ui/version unreachable — " + (e && e.message || e));
  }

  // 2. every /api/ui/* endpoint, shape-checked
  await checkJson("api: stats", "/api/ui/stats", ["total", "by_type", "projects", "edges"]);
  await checkJson("api: search", "/api/ui/search?q=doctor&limit=3", ["results", "mode"]);
  var g = await checkJson("api: graph", "/api/ui/graph?min_weight=0&max_nodes=25",
                          ["nodes", "edges", "truncated"]);
  if (g && g.nodes && g.nodes.length) {
    await checkJson("api: related", "/api/ui/memories/" +
      encodeURIComponent(g.nodes[0].id) + "/related?min_weight=0", ["memory_id", "related"]);
  } else if (g) {
    setLine("api: related", "PASS", "skipped — graph has no nodes (empty database is fine)");
  } else {
    setLine("api: related", "WARN", "skipped — graph check failed first");
  }

  // 2b. workspace layer (v2.5): the file index the Files lens and the crystals read
  var wm = await checkJson("api: workspace map", "/api/ui/workspace/map",
                           ["roots", "projects", "unmapped_folders"]);
  reportFileIndex(await fileIndex(wm));
  if (wm) {
    var un = wm.unmapped_folders || [];
    setLine("workspace: unmapped folders", un.length ? "WARN" : "PASS",
      un.length ? un.length + " — " + un.slice(0, 5).map(function (f) { return f.rel_path; }).join(", ")
        + (un.length > 5 ? ", …" : "") + " (tell your assistant where they belong)"
      : "every folder is bound to a project");
    var homeless = (wm.projects || []).filter(function (p) {
      return !p.home_folders || !p.home_folders.length;
    }).map(function (p) { return p.slug; });
    setLine("workspace: projects without a folder", homeless.length ? "WARN" : "PASS",
      homeless.length ? homeless.join(", ") : "every project has a home folder");
  }
  var dg = await checkJson("api: workspace dangling", "/api/ui/workspace/dangling?limit=3",
                           ["count", "items"]);
  if (dg) {
    setLine("workspace: dangling references", "PASS", dg.count + " token(s) named by memories but not resolved"
      + (dg.items.length ? " — top: " + dg.items.map(function (i) { return i.token + " ×" + i.mentions; }).join(", ") : ""));
  }

  // 3. canvas 2D + WebGL
  try {
    var c2 = document.createElement("canvas").getContext("2d");
    setLine("canvas 2d", c2 ? "PASS" : "FAIL", c2 ? "" : "getContext('2d') returned null");
  } catch (e) { setLine("canvas 2d", "FAIL", e.message); }
  try {
    var cv = document.createElement("canvas");
    var gl = cv.getContext("webgl2") || cv.getContext("webgl");
    setLine("webgl", gl ? "PASS" : "FAIL",
            gl ? (gl.getParameter(gl.VERSION) || "") : "no webgl/webgl2 context");
  } catch (e) { setLine("webgl", "FAIL", e.message); }

  // 4. elementsFromPoint probe over the click target: automatic click-eater test
  try {
    var t = document.getElementById("target");
    var r = t.getBoundingClientRect();
    var cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    var stack = document.elementsFromPoint(cx, cy);
    if (!stack.length) {
      setLine("click-eater probe", "FAIL", "elementsFromPoint returned nothing");
    } else if (stack[0] === t || t.contains(stack[0])) {
      setLine("click-eater probe", "PASS", "top element at target centre is the target");
    } else {
      var s = stack[0];
      setLine("click-eater probe", "FAIL",
        "element ON TOP of the click target: <" + s.tagName.toLowerCase() +
        (s.id ? "#" + s.id : "") +
        (s.className && typeof s.className === "string" ? "." + s.className.split(" ").join(".") : "") +
        "> — this is the click-eater pattern");
    }
  } catch (e) { setLine("click-eater probe", "FAIL", e.message); }

  // 5. overlay detector: anything covering >50% of the viewport that can
  //    swallow pointer events (invisible-but-clickable is exactly the bug,
  //    so do NOT filter on opacity)
  try {
    var vw = window.innerWidth, vh = window.innerHeight, varea = vw * vh;
    var offenders = [];
    document.querySelectorAll("body *").forEach(function (el) {
      if (el.closest("[data-doctor-ok]")) return;
      var cs = getComputedStyle(el);
      if (cs.display === "none" || cs.visibility === "hidden" || cs.pointerEvents === "none") return;
      var b = el.getBoundingClientRect();
      var ix = Math.max(0, Math.min(b.right, vw) - Math.max(b.left, 0));
      var iy = Math.max(0, Math.min(b.bottom, vh) - Math.max(b.top, 0));
      if (ix * iy > 0.5 * varea) {
        offenders.push("<" + el.tagName.toLowerCase() +
          (el.id ? "#" + el.id : "") +
          (el.className && typeof el.className === "string" ? "." + el.className.split(" ").join(".") : "") +
          "> display:" + cs.display + " position:" + cs.position +
          " z:" + cs.zIndex + " opacity:" + cs.opacity);
      }
    });
    if (offenders.length)
      setLine("overlay detector", "FAIL", offenders.length + " element(s) cover >50% of viewport and accept pointer events: " + offenders.join(" | "));
    else
      setLine("overlay detector", "PASS", "no full-viewport pointer-capturing overlays");
  } catch (e) { setLine("overlay detector", "FAIL", e.message); }

  // 6. js errors summary (updates live if more arrive)
  setLine("js errors", JS_ERRORS.length ? "FAIL" : "PASS",
          JS_ERRORS.length ? JS_ERRORS.length + " captured (see below)" : "none captured");
}

/* ---- pointer test ---- */
setLine("pointerdown", "WAIT", "click/tap inside the box below");
setLine("click event", "WAIT", "click/tap inside the box below");
(function () {
  var t = document.getElementById("target");
  t.addEventListener("pointerdown", function (ev) {
    setLine("pointerdown", "PASS", "received at " + Math.round(ev.clientX) + "," + Math.round(ev.clientY));
    t.textContent = "pointerdown received at " + Math.round(ev.clientX) + "," + Math.round(ev.clientY);
  });
  t.addEventListener("click", function () {
    setLine("click event", "PASS", "click fired on target");
  });
})();

/* ---- cleanup: three lists, each a dry run first ----
   A dry run is a GET that only reads. The apply buttons POST to
   /api/ui/edit/*, the same doors Atlas uses. Everything a note says is put
   on the page as text, never as HTML. */
function byId(id) { return document.getElementById(id); }
function el(tag, cls, text) {
  var e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
function memLink(id, text) {
  var a = el("a", "", text);
  a.href = "/ui/memory/" + encodeURIComponent(id);
  a.target = "_blank"; a.rel = "noopener";
  a.title = "Open this memory in a new tab";
  return a;
}
function scope() { return byId("cl-project").value.trim(); }
function scopeText() { return scope() ? " in " + scope() : ""; }
function withScope(url) {
  if (!scope()) return url;
  return url + (url.indexOf("?") < 0 ? "?" : "&") + "project=" + encodeURIComponent(scope());
}
function plural(n, one, many) { return n + " " + (n === 1 ? one : many || one + "s"); }
function detailText(data, status) {
  var d = data && data.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) {
    return d.map(function (x) { return x && x.msg || ""; }).filter(Boolean).join("; ")
      || "HTTP " + status;
  }
  return "HTTP " + status;
}
async function readJson(url) {
  var res = await fetch(url, { cache: "no-store" });
  var data = null;
  try { data = await res.json(); } catch (e) {}
  if (!res.ok) throw new Error(detailText(data, res.status));
  return data || {};
}
async function writeJson(url, payload) {
  var res = await fetch(url, {
    method: "POST",
    headers: keyHeaders({ "Content-Type": "application/json", "X-Brain-Client": "atlas" }),
    body: JSON.stringify(payload || {}),
  });
  var data = null;
  try { data = await res.json(); } catch (e) {}
  if (res.status === 401) {
    byId("keybox").hidden = false;
    byId("cl-key").focus();
    throw new Error("this brain has an API key. Paste it in the box above, then press the button again");
  }
  if (!res.ok) throw new Error(detailText(data, res.status));
  return data || {};
}
byId("cl-key-save").addEventListener("click", function () {
  try { localStorage.setItem("atlas-key", byId("cl-key").value.trim()); } catch (e) {}
  byId("cl-key").value = "";
  byId("keybox").hidden = true;
});

function say(state, cls, text) {
  state.className = "state" + (cls ? " " + cls : "");
  state.textContent = text;
}
/* run one button's work; a failure is said next to the button, not thrown */
async function guarded(btn, state, work) {
  btn.disabled = true;
  try { await work(); }
  catch (e) { say(state, "FAIL", "failed: " + (e && e.message || e)); }
  btn.disabled = false;
}

/* 1. summaries the old model wrote */
var SUM = { count: 0, project: "", armed: 0 };
function disarm() {
  var b = byId("sum-apply");
  clearTimeout(SUM.armed);
  SUM.armed = 0;
  b.classList.remove("armed");
  b.textContent = "Apply: replace " + plural(SUM.count, "summary", "summaries");
}
byId("sum-dry").addEventListener("click", function () {
  var state = byId("sum-state");
  guarded(byId("sum-dry"), state, async function () {
    say(state, "", "reading…");
    var d = await readJson(withScope("/api/ui/cleanup/summaries"));
    var box = byId("sum-list"), shown = d.examples || [];
    box.innerHTML = "";
    SUM.count = d.would_change;
    SUM.project = scope();
    shown.forEach(function (x) {
      var it = el("div", "item");
      var head = el("div", "line lab", "memory ");
      head.appendChild(memLink(x.id, x.id.slice(0, 8)));
      var was = el("div", "line old"), will = el("div", "line new");
      was.appendChild(el("span", "lab", "now:   "));
      was.appendChild(document.createTextNode(x.old));
      will.appendChild(el("span", "lab", "after: "));
      will.appendChild(document.createTextNode(x["new"]));
      it.appendChild(head); it.appendChild(was); it.appendChild(will);
      box.appendChild(it);
    });
    byId("sum-apply").hidden = !d.would_change;
    disarm();
    say(state, "", d.would_change
      ? plural(d.would_change, "summary", "summaries") + " would change" + scopeText()
        + ". Nothing has changed yet. Showing the first " + shown.length + "."
      : "nothing to replace" + scopeText() + ".");
    setLine("cleanup: model summaries", d.would_change ? "WARN" : "PASS",
      d.would_change + " would change" + scopeText() + " (dry run)");
  });
});
byId("sum-apply").addEventListener("click", function () {
  var btn = byId("sum-apply"), state = byId("sum-state");
  if (!SUM.armed) {                       // the first press asks, the second does it
    btn.classList.add("armed");
    btn.textContent = "Press again to replace " + SUM.count + " (this writes to the brain)";
    SUM.armed = setTimeout(disarm, 8000);
    return;
  }
  disarm();
  guarded(btn, state, async function () {
    say(state, "", "replacing…");
    var d = await writeJson("/api/ui/edit/cleanup/resummarise",
                            { project: SUM.project, expect: SUM.count });
    btn.hidden = true;
    byId("sum-list").innerHTML = "";
    say(state, "PASS", plural(d.changed, "summary", "summaries")
      + " replaced. Run the dry run again to see what is left.");
    setLine("cleanup: model summaries", "PASS", d.changed + " replaced");
  });
});

/* a row's button: one press settles the row, and the row says what happened */
function settleButton(item, state, label, title, work) {
  var b = el("button", "", label);
  b.type = "button";
  b.title = title;
  b.addEventListener("click", async function () {
    var all = item.querySelectorAll("button");
    all.forEach(function (x) { x.disabled = true; });
    say(state, "", "working…");
    try {
      var done = await work();
      item.classList.add("settled");
      say(state, "PASS", done);
    } catch (e) {
      all.forEach(function (x) { x.disabled = false; });
      say(state, "FAIL", "failed: " + (e && e.message || e));
    }
  });
  return b;
}

/* 2. corrections stated only in prose */
function pairItem(p) {
  var it = el("div", "item"), state = el("span", "state");
  var newer = el("div", "line new"), older = el("div", "line old");
  newer.appendChild(el("span", "lab", "newer: "));
  newer.appendChild(memLink(p.new_id, p.new_id.slice(0, 8)));
  newer.appendChild(document.createTextNode("  " + (p.new_summary || "(no summary)")));
  older.appendChild(el("span", "lab", "older: "));
  older.appendChild(memLink(p.old_id, p.old_id.slice(0, 8)));
  older.appendChild(document.createTextNode("  [" + String(p.old_type).replace(/_/g, " ") + "]  "
    + (p.old_summary || "(no summary)")));
  it.appendChild(newer);
  it.appendChild(older);
  it.appendChild(el("div", "line lab", p.project + " · the newer note says: “" + p.text + "”"));
  if (p.partial) {
    it.appendChild(el("div", "line WARN",
      "It speaks of a part or of a summary, so Correct may fit better than Supersede."));
  }
  var url = "/api/ui/edit/memories/" + encodeURIComponent(p.old_id) + "/";
  var why = "stated in the newer note";
  var acts = el("div", "acts");
  acts.appendChild(settleButton(it, state, "Supersede",
    "Archive the older note: it points at the newer one", async function () {
      var d = await writeJson(url + "supersede", { by: p.new_id, reason: why });
      return (d.superseded || []).length
        ? "done: the older note is archived and points at the newer one"
        : "nothing changed: the older note is no longer active";
    }));
  acts.appendChild(settleButton(it, state, "Correct",
    "Keep the older note, marked disputed", async function () {
      var d = await writeJson(url + "correct", { by: p.new_id, reason: why });
      return (d.corrected || []).length
        ? "done: the older note stays, marked as corrected by the newer one"
        : "nothing changed: the older note is no longer active";
    }));
  if (p.old_type === "open_loop") {
    acts.appendChild(settleButton(it, state, "Close",
      "The older note is an open loop: mark it done", async function () {
        var d = await writeJson(url + "close", { by: p.new_id, note: why });
        return d.closed ? "done: the loop is closed" : "nothing changed: the loop was " + d.status;
      }));
  }
  acts.appendChild(state);
  it.appendChild(acts);
  return it;
}
byId("cor-dry").addEventListener("click", function () {
  var state = byId("cor-state");
  guarded(byId("cor-dry"), state, async function () {
    say(state, "", "reading…");
    var d = await readJson(withScope("/api/ui/cleanup/corrections?limit=200"));
    var box = byId("cor-list"), shown = d.pairs || [];
    box.innerHTML = "";
    shown.forEach(function (p) { box.appendChild(pairItem(p)); });
    say(state, "", d.total
      ? plural(d.total, "pair") + scopeText() + ". Nothing has changed yet."
        + (shown.length < d.total ? " Showing the first " + shown.length + "." : "")
      : "no pair found" + scopeText() + ".");
    setLine("cleanup: corrections stated in prose", d.total ? "WARN" : "PASS",
      plural(d.total, "pair") + scopeText() + " (dry run)");
  });
});

/* 3. stale open loops */
function loopItem(l) {
  var it = el("div", "item"), state = el("span", "state");
  var line = el("div", "line");
  line.appendChild(el("span", "lab", plural(l.age_days, "day") + " · " + l.project + " · "));
  line.appendChild(memLink(l.id, l.id.slice(0, 8)));
  line.appendChild(document.createTextNode("  " + (l.summary || "(no summary)")));
  it.appendChild(line);
  var acts = el("div", "acts");
  acts.appendChild(settleButton(it, state, "Close",
    "This loop is finished: mark it done", async function () {
      var d = await writeJson("/api/ui/edit/memories/" + encodeURIComponent(l.id) + "/close",
                              { note: "closed from the Doctor page" });
      return d.closed ? "done: the loop is closed" : "nothing changed: the loop was " + d.status;
    }));
  acts.appendChild(state);
  it.appendChild(acts);
  return it;
}
byId("loop-dry").addEventListener("click", function () {
  var state = byId("loop-state");
  guarded(byId("loop-dry"), state, async function () {
    say(state, "", "reading…");
    var d = await readJson(withScope("/api/ui/cleanup/loops?stale_only=true&limit=200"));
    var box = byId("loop-list"), shown = d.open_loops || [];
    box.innerHTML = "";
    shown.forEach(function (l) { box.appendChild(loopItem(l)); });
    say(state, "", d.total
      ? plural(d.total, "stale open loop") + scopeText() + ". Nothing has changed yet."
        + (shown.length < d.total ? " Showing the oldest " + shown.length + "." : "")
      : "no stale open loop" + scopeText() + ".");
    setLine("cleanup: stale open loops", d.total ? "WARN" : "PASS",
      plural(d.total, "stale open loop") + scopeText() + " (dry run)");
  });
});

/* ---- report ---- */
function buildReport() {
  var out = [];
  out.push("MemoryBrain Doctor report");
  out.push("build: " + document.getElementById("stamp").textContent.trim());
  out.push("ua: " + navigator.userAgent);
  out.push("viewport: " + window.innerWidth + "x" + window.innerHeight +
           " dpr " + (window.devicePixelRatio || 1));
  out.push("time: " + new Date().toISOString());
  out.push("");
  ORDER.forEach(function (n) {
    var r = LINES[n];
    out.push("[" + r.status + "] " + n + (r.detail ? " — " + r.detail : ""));
  });
  if (JS_ERRORS.length) { out.push(""); out.push("JS errors:"); JS_ERRORS.forEach(function (e) { out.push("  " + e); }); }
  return out.join("\n");
}
document.getElementById("copy").addEventListener("click", function () {
  var txt = buildReport();
  function done(ok) {
    document.getElementById("copystate").textContent = ok ? "copied — paste it into the chat" : "copy failed — select the text manually";
  }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(txt).then(function () { done(true); }, function () { fallback(); });
  } else { fallback(); }
  function fallback() {
    var ta = document.createElement("textarea");
    ta.value = txt; document.body.appendChild(ta); ta.select();
    try { done(document.execCommand("copy")); } catch (e) { done(false); }
    document.body.removeChild(ta);
  }
});
document.getElementById("rerun").addEventListener("click", function () { runChecks(); });

runChecks();
</script>
</body>
</html>
"""
