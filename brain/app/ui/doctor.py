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
  * canvas 2D and WebGL contexts can be created
  * pointerdown reaches a handler ("click received at x,y")
  * elementsFromPoint probe over the click target (automatic click-eater test)
  * overlay detector: any element covering >50% of the viewport that can
    swallow pointer events
  * window.onerror / unhandledrejection capture, dumped on the page
Plus a "Copy report" button that produces a plain-text block to paste back.
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
