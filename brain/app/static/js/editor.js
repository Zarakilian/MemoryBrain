/* Atlas editor — the write side of the UI. Add / edit / archive / delete
   memories and projects through small modal forms.

   Writes go to /api/ui/edit/* (and /ingest/file), which are NOT part of
   the UI auth bypass: when BRAIN_API_KEY is set the server demands
   X-Brain-Key. On a 401 this module asks for the key once, remembers it
   locally, and retries. Guardrails mirror the server's: archive is the
   default "remove"; hard delete requires typing the id's first 8 chars;
   projects refuse deletion while they still hold memories. */
"use strict";

(function () {
  var esc = window.Atlas ? Atlas.esc : function (s) { return String(s); };

  /* ------------------------------ key + fetch ---------------------------- */
  function getKey() { try { return localStorage.getItem("atlas-key") || ""; } catch (e) { return ""; } }
  function setKey(k) { try { localStorage.setItem("atlas-key", k); } catch (e) {} }

  async function writeFetch(url, opts, retried) {
    opts = opts || {};
    opts.headers = Object.assign({}, opts.headers);
    var k = getKey();
    if (k) opts.headers["X-Brain-Key"] = k;
    var res = await fetch(url, opts);
    if (res.status === 401 && !retried) {
      var entered = await askKey();
      if (entered) return writeFetch(url, opts, true);
    }
    return res;
  }

  function askKey() {
    return new Promise(function (resolve) {
      var m = modal("API key required",
        '<p class="quiet">This brain has BRAIN_API_KEY set. Paste it once — '
        + "it stays in this browser only.</p>"
        + '<label class="field">X-Brain-Key<input type="password" id="f-key" autocomplete="off"></label>',
        [{ label: "Save key", primary: true, run: function () {
            setKey(m.q("#f-key").value.trim());
            m.close(); resolve(true);
          } }],
        function () { resolve(false); });
      m.q("#f-key").focus();
    });
  }

  /* -------------------------------- modal -------------------------------- */
  function modal(title, bodyHTML, buttons, onCancel) {
    var veil = document.createElement("div");
    veil.className = "modal-veil";
    veil.innerHTML = '<div class="modal-card" role="dialog" aria-label="' + esc(title) + '">'
      + "<h2>" + esc(title) + "</h2>"
      + '<div class="modal-body">' + bodyHTML + "</div>"
      + '<div class="modal-actions"><span class="modal-err" aria-live="polite"></span>'
      + '<button type="button" class="m-cancel">Cancel</button></div></div>';
    var actions = veil.querySelector(".modal-actions");
    (buttons || []).forEach(function (b) {
      var el = document.createElement("button");
      el.type = "button";
      el.textContent = b.label;
      el.className = b.danger ? "danger" : b.primary ? "primary" : "";
      el.addEventListener("click", function () { b.run(); });
      actions.appendChild(el);
    });
    function close() {
      document.removeEventListener("keydown", onEsc, true);
      veil.remove();
    }
    function onEsc(ev) {
      if (ev.key === "Escape") { ev.stopPropagation(); cancel(); }
    }
    function cancel() { close(); if (onCancel) onCancel(); }
    veil.addEventListener("pointerdown", function (ev) { if (ev.target === veil) cancel(); });
    veil.querySelector(".m-cancel").addEventListener("click", cancel);
    document.addEventListener("keydown", onEsc, true);
    document.body.appendChild(veil);
    return {
      el: veil, close: close,
      q: function (sel) { return veil.querySelector(sel); },
      err: function (msg) { veil.querySelector(".modal-err").textContent = msg; },
    };
  }

  async function submit(m, method, url, payload, form) {
    m.err("Working…");
    try {
      var res = await writeFetch(url, {
        method: method,
        headers: { "Content-Type": "application/json" },
        body: payload === undefined ? undefined : JSON.stringify(payload),
      });
      if (form) { res._form = true; }
      var data = null;
      try { data = await res.json(); } catch (e) {}
      if (!res.ok) {
        m.err((data && data.detail) ? String(data.detail) : "HTTP " + res.status);
        return null;
      }
      return data || {};
    } catch (e) { m.err(e.message); return null; }
  }

  function projectOptions(selected) {
    var out = "";
    document.querySelectorAll(".rail-projects a").forEach(function (a) {
      var slug = new URL(a.href).searchParams.get("project");
      out += '<option value="' + esc(slug) + '"' + (slug === selected ? " selected" : "")
        + ">" + esc(a.querySelector(".pname").textContent) + "</option>";
    });
    return out;
  }
  function currentProject() {
    return new URLSearchParams(location.search).get("project") || "";
  }

  /* ------------------------------ add a note ----------------------------- */
  function openAddNote() {
    var m = modal("Add to the brain",
      '<label class="field">project<select id="f-proj">' + projectOptions(currentProject())
      + "</select></label>"
      + '<label class="field">type<select id="f-type"><option>note</option>'
      + "<option>fact</option><option>reference</option></select></label>"
      + '<label class="field">content<textarea id="f-content" rows="7" '
      + 'placeholder="What should the brain remember?"></textarea></label>'
      + '<label class="field">tags (comma separated)<input id="f-tags"></label>'
      + '<label class="field">importance<select id="f-imp"><option value="">auto</option>'
      + "<option>1</option><option>2</option><option>3</option><option>4</option>"
      + "<option>5</option></select></label>"
      + '<label class="field">or attach a text file (≤1 MB)<input type="file" id="f-file"></label>',
      [{ label: "Save memory", primary: true, run: save }]);
    m.q("#f-content").focus();

    async function save() {
      var proj = m.q("#f-proj").value;
      if (!proj) { m.err("Create a project first."); return; }
      var file = m.q("#f-file").files[0];
      if (file) {
        m.err("Uploading…");
        var fd = new FormData();
        fd.append("file", file);
        var res = await writeFetch("/ingest/file?project=" + encodeURIComponent(proj),
                                   { method: "POST", body: fd });
        var data = null; try { data = await res.json(); } catch (e) {}
        if (!res.ok) { m.err((data && data.detail) || "HTTP " + res.status); return; }
        m.close(); location.reload(); return;
      }
      var content = m.q("#f-content").value.trim();
      if (!content) { m.err("Content is empty."); return; }
      var body = {
        content: content, project: proj, type: m.q("#f-type").value,
        tags: m.q("#f-tags").value.split(",").map(function (t) { return t.trim(); })
              .filter(Boolean),
      };
      var imp = m.q("#f-imp").value;
      if (imp) body.importance = Number(imp);
      var out = await submit(m, "POST", "/api/ui/edit/notes", body);
      if (out) {
        if (out.duplicate) { m.err("Already stored (same content + project)."); return; }
        m.close(); location.reload();
      }
    }
  }

  /* --------------------------- project add / edit ------------------------ */
  function openProject(existing) {
    var m = modal(existing ? "Edit project" : "New project",
      '<label class="field">slug (lowercase, stable)<input id="f-slug" '
      + (existing ? "readonly" : "") + ' value="' + esc(existing ? existing.slug : "")
      + '" placeholder="my-project"></label>'
      + '<label class="field">name<input id="f-name" value="'
      + esc(existing ? existing.name : "") + '"></label>'
      + '<label class="field">one-liner<input id="f-line" value="'
      + esc(existing ? existing.one_liner || "" : "") + '"></label>'
      + (existing ? '<p class="quiet">Deleting is only possible once the project '
                    + "holds no memories.</p>" : ""),
      existing
        ? [{ label: "Save", primary: true, run: save },
           { label: "Delete project", danger: true, run: del }]
        : [{ label: "Create", primary: true, run: save }]);
    (existing ? m.q("#f-name") : m.q("#f-slug")).focus();

    async function save() {
      var body = { slug: m.q("#f-slug").value.trim(),
                   name: m.q("#f-name").value.trim() || m.q("#f-slug").value.trim(),
                   one_liner: m.q("#f-line").value.trim() };
      if (!/^[a-z0-9][a-z0-9_-]{0,63}$/.test(body.slug)) {
        m.err("Slug: lowercase letters, digits, - and _ only."); return;
      }
      var out = await submit(m, "POST", "/api/ui/edit/projects", body);
      if (out) { m.close(); location.href = "/ui?project=" + encodeURIComponent(body.slug); }
    }
    async function del() {
      var out = await submit(m, "DELETE",
        "/api/ui/edit/projects/" + encodeURIComponent(existing.slug));
      if (out) { m.close(); location.href = "/ui"; }
    }
  }

  /* ------------------------ memory edit / archive / delete --------------- */
  function openEditMemory(mem) {
    var m = modal("Edit memory",
      '<label class="field">summary<textarea id="f-sum" rows="2">'
      + esc(mem.summary || "") + "</textarea></label>"
      + '<label class="field">content<textarea id="f-content" rows="7">'
      + esc(mem.content || "") + "</textarea></label>"
      + '<div class="field-row">'
      + '<label class="field">type<select id="f-type">'
      + ["session", "handover", "note", "fact", "file", "reference"].map(function (t) {
          return "<option" + (t === mem.type ? " selected" : "") + ">" + t + "</option>";
        }).join("") + "</select></label>"
      + '<label class="field">importance<select id="f-imp">'
      + [1, 2, 3, 4, 5].map(function (i) {
          return "<option" + (i === mem.importance ? " selected" : "") + ">" + i + "</option>";
        }).join("") + "</select></label></div>"
      + '<label class="field">project<select id="f-proj">'
      + projectOptions(mem.project) + "</select></label>"
      + '<label class="field">tags<input id="f-tags" value="'
      + esc((mem.tags || []).join(", ")) + '"></label>',
      [{ label: "Save changes", primary: true, run: save }]);

    async function save() {
      var body = {
        summary: m.q("#f-sum").value.trim(),
        content: m.q("#f-content").value,
        type: m.q("#f-type").value,
        importance: Number(m.q("#f-imp").value),
        project: m.q("#f-proj").value,
        tags: m.q("#f-tags").value.split(",").map(function (t) { return t.trim(); })
              .filter(Boolean),
      };
      var out = await submit(m, "PATCH",
        "/api/ui/edit/memories/" + encodeURIComponent(mem.id), body);
      if (out) { m.close(); location.reload(); }
    }
  }

  function openDeleteMemory(mem) {
    var short = mem.id.slice(0, 8);
    var m = modal("Hard delete — no undo",
      '<p class="quiet">Archiving is reversible and usually the right call. '
      + "Hard delete removes the memory, its vector and its links forever.</p>"
      + '<p>Type <code>' + esc(short) + "</code> to confirm:</p>"
      + '<label class="field"><input id="f-confirm" autocomplete="off"></label>',
      [{ label: "Delete forever", danger: true, run: del }]);
    m.q("#f-confirm").focus();

    async function del() {
      var out = await submit(m, "DELETE",
        "/api/ui/edit/memories/" + encodeURIComponent(mem.id),
        { confirm: m.q("#f-confirm").value.trim() });
      if (out) { m.close(); if (window.Atlas) Atlas.closeInspector(); location.reload(); }
    }
  }

  /* --------------------------- inspector actions ------------------------- */
  document.addEventListener("atlas:inspected", function (ev) {
    var mem = ev.detail;
    var host = document.getElementById("insp-body");
    if (!host || !mem) return;
    var row = document.createElement("div");
    row.className = "insp-actions";
    row.innerHTML = '<button type="button" data-act="edit">✎ Edit</button>'
      + '<button type="button" data-act="' + (mem.status === "archived" ? "restore" : "archive")
      + '">' + (mem.status === "archived" ? "↑ Restore" : "⬇ Archive") + "</button>"
      + '<button type="button" class="danger" data-act="delete">✕ Delete</button>';
    host.appendChild(row);
    row.addEventListener("click", async function (e) {
      var b = e.target.closest("button");
      if (!b) return;
      if (b.dataset.act === "edit") openEditMemory(mem);
      else if (b.dataset.act === "archive") {
        var r = await writeFetch("/api/ui/edit/memories/" + encodeURIComponent(mem.id)
                                 + "/archive", { method: "POST" });
        if (r.ok) location.reload();
      } else if (b.dataset.act === "restore") {
        var r2 = await writeFetch("/api/ui/edit/memories/" + encodeURIComponent(mem.id), {
          method: "PATCH", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: "active" }),
        });
        if (r2.ok) location.reload();
      } else if (b.dataset.act === "delete") openDeleteMemory(mem);
    });
  });

  /* ------------------------------- triggers ------------------------------ */
  var addBtn = document.getElementById("add-note-btn");
  if (addBtn) addBtn.addEventListener("click", openAddNote);

  var npBtn = document.getElementById("new-project-btn");
  if (npBtn) npBtn.addEventListener("click", function () { openProject(null); });

  var epBtn = document.getElementById("edit-project-btn");
  if (epBtn) epBtn.addEventListener("click", function () {
    openProject({ slug: epBtn.dataset.slug, name: epBtn.dataset.name,
                  one_liner: epBtn.dataset.line });
  });

  if (window.Atlas) {
    Atlas.extraCommands = (Atlas.extraCommands || []).concat([
      { kind: "edit", label: "Add note / file", run: openAddNote },
      { kind: "edit", label: "New project", run: function () { openProject(null); } },
    ]);
  }
})();
