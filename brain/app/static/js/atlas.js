/* MemoryBrain Atlas shell: lens switching, inspector, command palette,
   stream load-more. Everything here is progressive enhancement — the
   server-rendered Stream works without any of it. */
"use strict";

/* ---- shared helpers (constellation.js / chronicle.js use these) ---- */
var Atlas = window.Atlas = {};

/* Deterministic project colour — MUST match _project_hue in routes.py. */
var HUES = [28, 82, 140, 190, 215, 255, 285, 320, 350, 45, 165, 5];
Atlas.hue = function (slug) {
  var h = 0, s = String(slug || "");
  for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 997;
  return HUES[h % 12];
};
function parchment() { return document.documentElement.dataset.theme === "parchment"; }
Atlas.color = function (slug, l) {
  /* pastel-leaning on parchment, deeper on umber */
  return "hsl(" + Atlas.hue(slug) + (parchment() ? " 46% " : " 42% ")
    + (l || (parchment() ? 46 : 60)) + "%)";
};
Atlas.goldRGBA = function (a) {
  return (parchment() ? "rgba(138,107,49," : "rgba(201,162,95,") + a + ")";
};
Atlas.goldBright = function () { return parchment() ? "#8a6b31" : "#e0bd7d"; };

function updateThemeBtn() {
  var b = document.getElementById("theme-toggle");
  if (b) b.textContent = parchment() ? "\u263e umber" : "\u2600 parchment";
}
Atlas.setTheme = function (t) {
  if (t) document.documentElement.dataset.theme = t;
  else document.documentElement.removeAttribute("data-theme");
  try { localStorage.setItem("atlas-theme", t || ""); } catch (e) {}
  updateThemeBtn();
  document.dispatchEvent(new CustomEvent("atlas:theme"));
};
Atlas.esc = function (s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
};
Atlas.getJSON = async function (url) {
  var r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error("HTTP " + r.status + " " + url);
  return r.json();
};
Atlas.reducedMotion = window.matchMedia
  && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

document.querySelectorAll("[data-js]").forEach(function (el) {
  el.removeAttribute("hidden");
});
updateThemeBtn();
var _tb = document.getElementById("theme-toggle");
if (_tb) _tb.addEventListener("click", function () {
  Atlas.setTheme(parchment() ? "" : "parchment");
});

/* -------------------------------------------------------------- lenses */
var LENSES = ["stream", "constellation", "chronicle"];
var lensHooks = {};             // constellation.js / chronicle.js register here
Atlas.onLens = function (name, fn) { lensHooks[name] = fn; };

function currentProject() {
  return new URLSearchParams(location.search).get("project") || "";
}

function showLens(name, push) {
  if (LENSES.indexOf(name) < 0) return;
  document.querySelectorAll(".pane").forEach(function (p) {
    var on = p.id === "pane-" + name;
    p.classList.toggle("on", on);
    if (on) p.removeAttribute("hidden"); else p.setAttribute("hidden", "");
  });
  document.querySelectorAll(".lens-tab").forEach(function (t) {
    var on = t.dataset.lens === name;
    t.classList.toggle("on", on);
    t.setAttribute("aria-selected", on ? "true" : "false");
  });
  if (push) {
    var u = new URL(location.href);
    u.searchParams.set("lens", name);
    history.replaceState(null, "", u);
  }
  if (lensHooks[name]) lensHooks[name]();   // lazy init on first show
}
Atlas.showLens = showLens;

document.querySelectorAll(".lens-tab").forEach(function (tab) {
  tab.addEventListener("click", function (ev) {
    ev.preventDefault();
    showLens(tab.dataset.lens, true);
  });
});

document.addEventListener("keydown", function (ev) {
  if (ev.target.matches("input, select, textarea")) return;
  if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
  if (ev.key === "1") showLens("stream", true);
  else if (ev.key === "2") showLens("constellation", true);
  else if (ev.key === "3") showLens("chronicle", true);
  else if (ev.key === "/") { ev.preventDefault(); openPalette(); }
  else if (ev.key === "Escape") { closePalette(); closeInspector(); }
});

/* if the page arrived with ?lens=…, activate its hook once scripts load */
window.addEventListener("DOMContentLoaded", function () {
  var lens = new URLSearchParams(location.search).get("lens") || "stream";
  if (lens !== "stream" && LENSES.indexOf(lens) >= 0) showLens(lens, false);
});

/* ------------------------------------------------------------ inspector */
var inspector = document.getElementById("inspector");
var inspBody = document.getElementById("insp-body");
var selectedRow = null;

function closeInspector() {
  if (!inspector) return;
  inspector.classList.remove("open");
  inspector.setAttribute("hidden", "");
  if (selectedRow) { selectedRow.classList.remove("selected"); selectedRow = null; }
  document.dispatchEvent(new CustomEvent("atlas:deselect"));
}
Atlas.closeInspector = closeInspector;

Atlas.inspect = async function (id, sourceRow) {
  if (!inspector || !inspBody) return;
  if (selectedRow) selectedRow.classList.remove("selected");
  selectedRow = sourceRow || null;
  if (selectedRow) selectedRow.classList.add("selected");

  inspector.removeAttribute("hidden");
  // double rAF so the transition actually plays after display flips
  requestAnimationFrame(function () { requestAnimationFrame(function () {
    inspector.classList.add("open");
  }); });
  inspBody.innerHTML = '<p class="quiet">Loading…</p>';
  try {
    var mem = await Atlas.getJSON("/api/ui/memories/" + encodeURIComponent(id));
    var rel = await Atlas.getJSON("/api/ui/memories/" + encodeURIComponent(id)
                                  + "/related?min_weight=0.2&limit=12");
    renderInspector(mem, rel.related || []);
  } catch (e) {
    inspBody.innerHTML = '<p class="quiet">Could not load memory: '
      + Atlas.esc(e.message) + "</p>";
  }
};

function renderInspector(m, related) {
  var esc = Atlas.esc;
  var h = ['<button class="insp-close" aria-label="Close">×</button>'];
  h.push('<div class="mem-meta">',
    '<span class="badge t-' + esc(m.type) + '">' + esc(m.type) + "</span>",
    m.project ? '<span class="pdot" style="--ph:' + Atlas.hue(m.project) + '"></span> '
      + esc(m.project) : "",
    "<time>" + esc(m.timestamp) + "</time>",
    '<span class="imp" data-imp="' + (m.importance || 1) + '"></span>',
    "</div>");
  h.push("<h1>" + esc(m.summary || "(no summary)") + "</h1>");
  if (m.tags && m.tags.length) {
    h.push('<div class="tags">' + m.tags.map(function (t) {
      return '<span class="tag">' + esc(t) + "</span>";
    }).join("") + "</div>");
  }
  if (m.content) h.push("<pre>" + esc(m.content) + "</pre>");
  h.push('<a class="mem-open" href="/ui/memory/' + esc(m.id) + '">Open full page →</a>');
  if (related.length) {
    h.push("<h2>Related</h2>");
    related.forEach(function (r) {
      var why = (r.explanations || []).map(function (e) {
        return esc(e.kind) + (e.direction === "in" ? " ←" : " →") + " "
          + (e.weight != null ? e.weight.toFixed(2) : "");
      }).join(" · ");
      h.push('<a class="insp-rel" href="/ui/memory/' + esc(r.id) + '" data-id="' + esc(r.id) + '">',
        '<span class="sum">' + esc(r.summary || r.id) + "</span>",
        '<span class="why-line">' + why + "</span>", "</a>");
    });
  } else {
    h.push('<h2>Related</h2><p class="quiet">No links above the weight floor.</p>');
  }
  inspBody.innerHTML = h.join("");
}

inspector && inspector.addEventListener("click", function (ev) {
  var closeBtn = ev.target.closest(".insp-close");
  if (closeBtn) { closeInspector(); return; }
  var rel = ev.target.closest(".insp-rel");
  if (rel) { ev.preventDefault(); Atlas.inspect(rel.dataset.id, null); }
});

/* clicking a memory row anywhere opens the inspector instead of navigating */
document.addEventListener("click", function (ev) {
  if (ev.defaultPrevented || ev.ctrlKey || ev.metaKey || ev.shiftKey) return;
  var row = ev.target.closest("a.mem");
  if (!row || !row.dataset.id) return;
  ev.preventDefault();
  Atlas.inspect(row.dataset.id, row);
});

/* --------------------------------------------------------------- stream */
var moreLink = document.getElementById("stream-more");
if (moreLink) {
  moreLink.addEventListener("click", async function (ev) {
    ev.preventDefault();
    var before = moreLink.dataset.before;
    var p = new URLSearchParams(location.search);
    var qs = new URLSearchParams({ before: before, limit: 60 });
    if (p.get("project")) qs.set("project", p.get("project"));
    if (p.get("type")) qs.set("type", p.get("type"));
    if (p.get("min_importance")) qs.set("min_importance", p.get("min_importance"));
    moreLink.textContent = "Loading…";
    try {
      var data = await Atlas.getJSON("/api/ui/stream?" + qs);
      appendStream(data);
      if (data.next_before) {
        moreLink.dataset.before = data.next_before;
        moreLink.textContent = "Older memories";
      } else moreLink.remove();
    } catch (e) { moreLink.textContent = "Failed — retry"; }
  });
}

function appendStream(data) {
  var feed = document.getElementById("stream-feed");
  if (!feed) return;
  var esc = Atlas.esc;
  var showProject = !currentProject();
  data.days.forEach(function (d) {
    var last = feed.lastElementChild;
    var container;
    if (last && last.querySelector(".day-head span")
        && last.querySelector(".day-head span").textContent === d.day) {
      container = last.querySelector(".day-rows");
    } else {
      var sec = document.createElement("section");
      sec.className = "day";
      sec.innerHTML = '<h3 class="day-head"><span>' + esc(d.day)
        + '</span></h3><div class="day-rows"></div>';
      feed.appendChild(sec);
      container = sec.querySelector(".day-rows");
    }
    d.items.forEach(function (m) {
      var a = document.createElement("a");
      a.className = "mem";
      a.href = "/ui/memory/" + m.id;
      a.dataset.id = m.id;
      a.innerHTML = "<time>" + esc((m.timestamp || "").slice(11, 16) || "·") + "</time>"
        + '<span class="badge t-' + esc(m.type) + '">' + esc(m.type) + "</span>"
        + (showProject ? '<span class="pdot" style="--ph:' + Atlas.hue(m.project)
           + '" title="' + esc(m.project) + '"></span>' : "")
        + '<span class="sum">' + esc(m.summary || "(no summary)") + "</span>"
        + '<span class="imp" data-imp="' + (m.importance || 1) + '"></span>';
      container.appendChild(a);
    });
  });
}

/* filters auto-apply (no-JS path is the noscript submit button) */
var filterForm = document.getElementById("stream-filters");
if (filterForm) {
  filterForm.addEventListener("change", function () { filterForm.submit(); });
}

/* -------------------------------------------------------------- palette */
var palette = document.getElementById("palette");
var palInput = document.getElementById("palette-input");
var palResults = document.getElementById("palette-results");
var palItems = [], palActive = 0, palTimer = null;

function openPalette() {
  if (!palette) return;
  palette.removeAttribute("hidden");
  palInput.value = "";
  palInput.focus();
  renderPalette([]);
  showCommands("");
}
function closePalette() {
  if (palette) palette.setAttribute("hidden", "");
}
document.addEventListener("keydown", function (ev) {
  if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "k") {
    ev.preventDefault();
    palette && palette.hasAttribute("hidden") ? openPalette() : closePalette();
  }
});
palette && palette.addEventListener("pointerdown", function (ev) {
  if (ev.target === palette) closePalette();   // click the backdrop = close
});

function commandsFor(qtext) {
  var cmds = [
    { kind: "lens", label: "Lens: Stream", run: function () { showLens("stream", true); } },
    { kind: "lens", label: "Lens: Constellation", run: function () { showLens("constellation", true); } },
    { kind: "lens", label: "Lens: Chronicle", run: function () { showLens("chronicle", true); } },
    { kind: "go", label: "All memories", run: function () { location.href = "/ui"; } },
    { kind: "go", label: "Doctor (diagnostics)", run: function () { location.href = "/ui/doctor"; } },
    { kind: "theme", label: "Theme: Parchment (pastel)", run: function () { Atlas.setTheme("parchment"); } },
    { kind: "theme", label: "Theme: Umber (dark)", run: function () { Atlas.setTheme(""); } },
  ];
  document.querySelectorAll(".rail-projects a").forEach(function (a) {
    var name = a.querySelector(".pname").textContent;
    cmds.push({ kind: "project", label: name,
      run: function () { location.href = a.getAttribute("href"); } });
  });
  var q = qtext.trim().toLowerCase();
  return q ? cmds.filter(function (c) { return c.label.toLowerCase().indexOf(q) >= 0; })
           : cmds;
}

function showCommands(q) {
  var cmds = commandsFor(q).slice(0, 8);
  renderPalette(cmds.map(function (c) {
    return { kind: c.kind, html: Atlas.esc(c.label), run: c.run };
  }), q ? null : "Type to search memories · ↑↓ select · Enter open");
}

function renderPalette(items, note) {
  palItems = items; palActive = 0;
  var h = items.map(function (it, i) {
    return '<div class="pal-item' + (i === 0 ? " active" : "") + '" data-i="' + i + '">'
      + '<span class="pal-kind">' + Atlas.esc(it.kind) + "</span>"
      + '<span class="sum">' + it.html + "</span></div>";
  }).join("");
  if (note) h += '<div class="pal-note">' + Atlas.esc(note) + "</div>";
  palResults.innerHTML = h;
}

palInput && palInput.addEventListener("input", function () {
  var q = palInput.value;
  clearTimeout(palTimer);
  palTimer = setTimeout(async function () {
    var base = commandsFor(q).slice(0, 3).map(function (c) {
      return { kind: c.kind, html: Atlas.esc(c.label), run: c.run };
    });
    if (!q.trim()) { showCommands(""); return; }
    try {
      var proj = currentProject();
      var url = "/api/ui/search?q=" + encodeURIComponent(q) + "&limit=8"
        + (proj ? "&project=" + encodeURIComponent(proj) : "");
      var data = await Atlas.getJSON(url);
      var hits = (data.results || []).map(function (m) {
        return {
          kind: m.type, html: Atlas.esc(m.summary || m.id),
          run: function () { closePalette(); Atlas.inspect(m.id, null); },
        };
      });
      renderPalette(base.concat(hits),
        hits.length ? null : "No memory matches — " + data.mode + " mode");
    } catch (e) {
      renderPalette(base, "Search failed: " + e.message);
    }
  }, 160);
});

palInput && palInput.addEventListener("keydown", function (ev) {
  if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
    ev.preventDefault();
    if (!palItems.length) return;
    palActive = (palActive + (ev.key === "ArrowDown" ? 1 : palItems.length - 1))
      % palItems.length;
    palResults.querySelectorAll(".pal-item").forEach(function (el, i) {
      el.classList.toggle("active", i === palActive);
    });
    var act = palResults.querySelector(".pal-item.active");
    act && act.scrollIntoView({ block: "nearest" });
  } else if (ev.key === "Enter") {
    ev.preventDefault();
    var it = palItems[palActive];
    if (it) { closePalette(); it.run(); }
  }
});

palResults && palResults.addEventListener("click", function (ev) {
  var el = ev.target.closest(".pal-item");
  if (!el) return;
  var it = palItems[Number(el.dataset.i)];
  if (it) { closePalette(); it.run(); }
});
