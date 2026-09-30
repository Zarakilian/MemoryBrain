/* Chronicle lens — how the work actually flowed. A horizontal time axis of
   sessions and handovers per project, with session_chain edges drawn as each
   project's spine. Plain SVG floating on glass inside the world: no physics,
   nothing to go wrong. */
"use strict";

(function () {
  var inited = false, host = null, lastData = null;
  var LANE_H = 64, LABEL_W = 170, PAD_R = 60, TOP = 34, R = 6;

  function init() {
    if (inited) return;
    inited = true;
    host = document.getElementById("chronicle");
    load();
  }

  async function load() {
    host.innerHTML = '<p class="quiet" style="padding:20px">Loading…</p>';
    var proj = document.getElementById("pane-chronicle").dataset.project;
    var data;
    try {
      data = await Atlas.getJSON("/api/ui/chronicle" + (proj ? "?project=" + encodeURIComponent(proj) : ""));
    } catch (e) {
      host.innerHTML = '<p class="quiet" style="padding:20px">Failed: ' + Atlas.esc(e.message) + "</p>";
      return;
    }
    lastData = data;
    render(data);
  }

  function render(data) {
    var lanes = data.lanes.filter(function (l) { return l.items.length; });
    if (!lanes.length) {
      host.innerHTML = '<div class="empty" style="position:absolute;inset:0">'
        + '<div><div class="empty-plate" aria-hidden="true"></div>'
        + '<p class="empty-title">No sessions recorded yet</p>'
        + '<p class="empty-hint">Sessions and handovers appear here as they are ingested.</p></div></div>';
      return;
    }

    var t0 = Infinity, t1 = -Infinity, byId = {};
    lanes.forEach(function (l, li) {
      l.items.forEach(function (m) {
        m._t = Date.parse(m.timestamp) || 0;
        m._lane = li;
        byId[m.id] = m;
        if (m._t) { t0 = Math.min(t0, m._t); t1 = Math.max(t1, m._t); }
      });
    });
    if (!isFinite(t0)) { t0 = Date.now() - 864e5; t1 = Date.now(); }
    if (t1 - t0 < 864e5) t1 = t0 + 864e5;        // at least one day of axis

    var hostW = host.getBoundingClientRect().width || 900;
    var days = (t1 - t0) / 864e5;
    var plotW = Math.max(hostW - LABEL_W - PAD_R, Math.min(days * 26, 6000));
    var W = LABEL_W + plotW + PAD_R;
    var H = TOP + lanes.length * LANE_H + 30;
    var x = function (t) { return LABEL_W + (t - t0) / (t1 - t0) * plotW; };
    var laneY = function (i) { return TOP + i * LANE_H + LANE_H / 2; };
    var esc = Atlas.esc;
    var s = [];
    s.push('<svg width="' + W + '" height="' + H + '" viewBox="0 0 ' + W + " " + H + '">');

    // month/day ticks
    var span = t1 - t0, step = span > 90 * 864e5 ? "month" : span > 5 * 864e5 ? "week" : "day";
    var d = new Date(t0); d.setHours(0, 0, 0, 0);
    var guard = 0;
    while (d.getTime() <= t1 && guard++ < 400) {
      var tx = x(d.getTime());
      if (tx >= LABEL_W - 1) {
        s.push('<line class="chron-grid" x1="' + tx + '" y1="' + (TOP - 12) + '" x2="' + tx + '" y2="'
          + (H - 22) + '" stroke-width="1"/>');
        s.push('<text class="chron-tick" x="' + (tx + 4) + '" y="' + (TOP - 16) + '">'
          + d.toISOString().slice(0, step === "month" ? 7 : 10) + "</text>");
      }
      if (step === "month") d.setMonth(d.getMonth() + 1);
      else d.setDate(d.getDate() + (step === "week" ? 7 : 1));
    }

    // lane baselines + labels
    lanes.forEach(function (l, i) {
      var y = laneY(i);
      s.push('<line class="chron-grid" x1="' + LABEL_W + '" y1="' + y + '" x2="' + (W - PAD_R + 20)
        + '" y2="' + y + '" stroke-dasharray="1 5"/>');
      s.push('<text class="chron-lane-label" x="10" y="' + (y + 4) + '">'
        + esc(l.name || l.project) + "</text>");
    });

    // session_chain spine
    (data.links || []).forEach(function (lk) {
      var a = byId[lk.src], b = byId[lk.dst];
      if (!a || !b) return;
      var x1 = x(a._t), y1 = laneY(a._lane), x2 = x(b._t), y2 = laneY(b._lane);
      var col = Atlas.goldRGBA(0.5);
      if (y1 === y2) {
        s.push('<line x1="' + x1 + '" y1="' + y1 + '" x2="' + x2 + '" y2="' + y2
          + '" stroke="' + col + '" stroke-width="1.6"/>');
      } else {
        var mx = (x1 + x2) / 2;
        s.push('<path d="M' + x1 + " " + y1 + " C" + mx + " " + y1 + ", " + mx + " "
          + y2 + ", " + x2 + " " + y2 + '" fill="none" stroke="' + col
          + '" stroke-width="1.2" stroke-dasharray="3 3"/>');
      }
    });

    // the memories themselves
    lanes.forEach(function (l, i) {
      l.items.forEach(function (m) {
        var cx = x(m._t), cy = laneY(i);
        var hue = Atlas.hue(l.project);
        var fill = "hsl(" + hue + " 70% 66%)";   // luminous: same law as the stars
        var r = R + (m.importance >= 4 ? 2 : 0);
        var shape;
        if (m.type === "handover") {
          shape = '<rect class="chron-node" data-id="' + esc(m.id) + '" x="' + (cx - r)
            + '" y="' + (cy - r) + '" width="' + r * 2 + '" height="' + r * 2
            + '" transform="rotate(45 ' + cx + " " + cy + ')" fill="none" stroke="'
            + fill + '" stroke-width="2"/>';
        } else {
          shape = '<circle class="chron-node" data-id="' + esc(m.id) + '" cx="' + cx
            + '" cy="' + cy + '" r="' + r + '" fill="' + fill + '"/>';
        }
        s.push('<g style="cursor:pointer">' + shape + "<title>"
          + esc((m.timestamp || "").slice(0, 16).replace("T", " ") + " — " + (m.summary || m.id))
          + "</title></g>");
      });
    });

    s.push("</svg>");
    host.innerHTML = s.join("");

    host.querySelector("svg").addEventListener("click", function (ev) {
      var n = ev.target.closest(".chron-node");
      if (!n) return;
      var prev = host.querySelector(".chron-node[data-selected]");
      if (prev) { prev.removeAttribute("data-selected"); prev.style.filter = ""; }
      n.setAttribute("data-selected", "1");
      n.style.filter = "drop-shadow(0 0 4px " + Atlas.goldBright() + ")";
      Atlas.inspect(n.dataset.id, null);
    });
    host.scrollLeft = host.scrollWidth;          // land on "now"
  }

  if (window.Atlas) Atlas.onLens("chronicle", init);
})();
