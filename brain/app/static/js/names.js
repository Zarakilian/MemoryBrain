/* The name patterns screen (/ui/names): add and remove this install's own
   patterns, and reindex the stored notes after a change.

   The lists are server-rendered; this only adds the writes. They go to
   /api/ui/edit/names/* through Atlas.writeFetch (editor.js), so they carry
   the client header and the API key when the brain has one. A pattern the
   brain refuses comes back as a sentence (422), shown under the form. */
"use strict";

(function () {
  var list = document.getElementById("pat-list");
  var form = document.getElementById("pat-form");
  if (!list || !form || !window.Atlas || !Atlas.writeFetch) return;
  var esc = Atlas.esc;
  var msg = document.getElementById("pat-msg");
  var reindexBox = document.getElementById("pat-reindex");
  var reindexWhy = document.getElementById("pat-reindex-why");
  var reindexBtn = document.getElementById("pat-reindex-btn");
  var reindexOut = document.getElementById("pat-reindex-out");

  function say(el, text, bad) {
    el.textContent = text;
    el.classList.toggle("bad", !!bad);
  }

  /* what the server said, as one sentence: a refusal's detail is a string,
     a malformed body's is a list of problems */
  function detail(data, status) {
    var d = data && data.detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) {
      var parts = d.map(function (x) { return x && x.msg || ""; }).filter(Boolean);
      if (parts.length) return parts.join("; ");
    }
    return "HTTP " + status;
  }

  async function send(method, url, payload) {
    var res = await Atlas.writeFetch(url, {
      method: method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {}),
    });
    var data = null;
    try { data = await res.json(); } catch (e) {}
    if (!res.ok) throw new Error(detail(data, res.status));
    return data || {};
  }

  function render(patterns) {
    var count = document.getElementById("pat-count");
    if (count) count.textContent = patterns.length;
    if (!patterns.length) {
      list.innerHTML = '<p class="quiet pat-none">No pattern yet.</p>';
      return;
    }
    list.innerHTML = patterns.map(function (p) {
      return '<div class="pat-row" data-kind="' + esc(p.kind) + '" data-pattern="' + esc(p.pattern) + '">'
        + '<span class="badge t-name">' + esc(String(p.kind).replace(/_/g, " ")) + "</span>"
        + "<code>" + esc(p.pattern) + "</code>"
        + "<time>" + esc(String(p.created_at || "").slice(0, 10)) + "</time>"
        + '<button type="button" class="pat-remove" title="Remove this pattern">Remove</button></div>';
    }).join("");
  }

  /* stored notes keep the old names until they are indexed again: say so */
  function reindexDue() {
    reindexBox.classList.add("due");
    say(reindexWhy, "The patterns changed. Notes already stored still follow the old ones until you reindex.");
    say(reindexOut, "");
  }

  form.addEventListener("submit", async function (ev) {
    ev.preventDefault();
    var kind = document.getElementById("pat-kind"), pattern = document.getElementById("pat-pattern");
    say(msg, "Checking…");
    try {
      var out = await send("POST", "/api/ui/edit/names/patterns",
                           { kind: kind.value, pattern: pattern.value });
      render(out.patterns || []);
      if (out.added) {
        say(msg, "Added. New notes follow it from now on.");
        pattern.value = "";
        reindexDue();
      } else {
        say(msg, "That pattern is already in the list.");
      }
    } catch (e) { say(msg, e.message, true); }
  });

  list.addEventListener("click", async function (ev) {
    var btn = ev.target.closest(".pat-remove");
    if (!btn) return;
    var row = btn.closest(".pat-row");
    btn.disabled = true;
    say(msg, "Removing…");
    try {
      var out = await send("DELETE", "/api/ui/edit/names/patterns",
                           { kind: row.dataset.kind, pattern: row.dataset.pattern });
      render(out.patterns || []);
      say(msg, "Removed.");
      reindexDue();
    } catch (e) { btn.disabled = false; say(msg, e.message, true); }
  });

  reindexBtn.addEventListener("click", async function () {
    reindexBtn.disabled = true;
    say(reindexOut, "Reindexing every note. This can take a minute on a large brain…");
    try {
      var out = await send("POST", "/api/ui/edit/names/reindex");
      reindexBox.classList.remove("due");
      say(reindexWhy, "A note's names are indexed when it is written. After you change a pattern, "
        + "reindex so the notes already stored follow it.");
      say(reindexOut, "Done: read " + Number(out.memories).toLocaleString() + " notes. The index now holds "
        + Number(out.entities).toLocaleString() + " names. Reload the page to see the new counts.");
    } catch (e) { say(reindexOut, e.message, true); }
    reindexBtn.disabled = false;
  });
})();
