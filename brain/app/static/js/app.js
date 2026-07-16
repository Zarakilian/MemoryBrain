/* MemoryBrain UI — shared behaviour: project colours + live search preview.
   Vanilla JS, no dependencies. */
(function () {
  "use strict";

  /* ---- stable project colour: slug → HSL, same colour every session ---- */
  function hashHue(s) {
    let h = 2166136261;
    for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
    return ((h >>> 0) % 360);
  }
  window.projectColor = function (slug) {
    return "hsl(" + hashHue(String(slug || "?")) + " 62% 62%)";
  };
  document.querySelectorAll(".proj-dot[data-project]").forEach(function (el) {
    el.style.background = window.projectColor(el.dataset.project);
  });

  /* ---- live search preview ---- */
  const input = document.getElementById("q");
  const preview = document.getElementById("search-preview");
  if (!input || !preview) return;

  let timer = null, sel = -1, items = [];

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function render(results) {
    items = results;
    sel = -1;
    if (!results.length) { preview.hidden = true; return; }
    preview.innerHTML = results.map(function (r) {
      return '<a href="/ui/memory/' + esc(r.id) + '">' +
        '<span class="chip type-' + esc(r.type) + '">' + esc(r.type) + "</span> " +
        esc(r.summary || "").slice(0, 120) +
        ' <span class="mem-meta" style="display:inline">· ' + esc(r.project || "") + "</span></a>";
    }).join("");
    preview.hidden = false;
  }

  input.addEventListener("input", function () {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) { preview.hidden = true; return; }
    timer = setTimeout(function () {
      fetch("/api/ui/search?q=" + encodeURIComponent(q) + "&limit=8")
        .then(function (r) { return r.ok ? r.json() : { results: [] }; })
        .then(function (d) { render(d.results || []); })
        .catch(function () { preview.hidden = true; });
    }, 180);
  });

  input.addEventListener("keydown", function (e) {
    const links = preview.querySelectorAll("a");
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (preview.hidden || !links.length) return;
      e.preventDefault();
      sel = e.key === "ArrowDown"
        ? Math.min(sel + 1, links.length - 1)
        : Math.max(sel - 1, 0);
      links.forEach(function (a, i) { a.classList.toggle("sel", i === sel); });
    } else if (e.key === "Enter" && sel >= 0 && !preview.hidden) {
      e.preventDefault();
      window.location.href = links[sel].href;
    } else if (e.key === "Escape") {
      preview.hidden = true;
    }
  });

  document.addEventListener("click", function (e) {
    if (!preview.contains(e.target) && e.target !== input) preview.hidden = true;
  });

  /* "/" focuses search from anywhere */
  document.addEventListener("keydown", function (e) {
    if (e.key === "/" && document.activeElement !== input &&
        !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) {
      e.preventDefault();
      input.focus();
      input.select();
    }
  });
})();
