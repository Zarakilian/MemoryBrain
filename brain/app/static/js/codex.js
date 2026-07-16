/* The Codex Margin — Leonardo's notebook, alive behind the Atlas.
   Procedurally drawn line-engravings (geometry, machines, flight, botany,
   astronomy, water) plus the scanned plates, drifting very slowly on a
   fixed layer that can never intercept a pointer. Every visit composes a
   slightly different folio.

   Safety rails, learned the hard way in this repo:
   - the layer and every piece are pointer-events:none (CSS, enforced)
   - z-index 0, beneath .shell; no scroll listeners, no rAF loops —
     drift is pure CSS transform animation on a handful of elements
   - prefers-reduced-motion → perfectly still; ambience toggle → gone */
"use strict";

(function () {
  var STORE = "atlas-ambience";
  function enabled() {
    try { return localStorage.getItem(STORE) !== "off"; } catch (e) { return true; }
  }

  var R = Math.random;
  function rnd(a, b) { return a + R() * (b - a); }
  function pick(arr) { return arr[Math.floor(R() * arr.length)]; }

  /* ---------------- the studies: each returns SVG inner markup ------------ */
  /* consistent hand: fill none, stroke currentColor, thin lines, faint
     cross-hatch — the colour and opacity come from CSS. viewBox 0 0 200 200 */

  function gearPath(cx, cy, r, teeth) {
    var d = [], tooth = r * 0.16;
    for (var i = 0; i < teeth; i++) {
      var a0 = (i / teeth) * 2 * Math.PI, a1 = ((i + 0.35) / teeth) * 2 * Math.PI,
          a2 = ((i + 0.5) / teeth) * 2 * Math.PI, a3 = ((i + 0.85) / teeth) * 2 * Math.PI;
      var R1 = r + tooth;
      d.push((i ? "L" : "M") + (cx + r * Math.cos(a0)) + " " + (cy + r * Math.sin(a0)));
      d.push("L" + (cx + R1 * Math.cos(a1)) + " " + (cy + R1 * Math.sin(a1)));
      d.push("L" + (cx + R1 * Math.cos(a2)) + " " + (cy + R1 * Math.sin(a2)));
      d.push("L" + (cx + r * Math.cos(a3)) + " " + (cy + r * Math.sin(a3)));
    }
    return d.join("") + "Z";
  }
  function gears() {
    var s = '<path d="' + gearPath(78, 105, 42, 12) + '"/>' +
      '<circle cx="78" cy="105" r="12"/><circle cx="78" cy="105" r="4"/>' +
      '<path d="' + gearPath(146, 74, 26, 9) + '"/>' +
      '<circle cx="146" cy="74" r="7"/>';
    for (var i = 0; i < 5; i++) {
      var a = rnd(0, 6.28);
      s += '<line x1="' + (78 + 14 * Math.cos(a)) + '" y1="' + (105 + 14 * Math.sin(a))
        + '" x2="' + (78 + 38 * Math.cos(a)) + '" y2="' + (105 + 38 * Math.sin(a)) + '"/>';
    }
    return s;
  }

  function moons() {
    var s = "", n = 5;
    for (var i = 0; i < n; i++) {
      var cx = 24 + i * 38, cy = 100, r = 14, k = (i / (n - 1)) * 2 - 1; // -1..1
      s += '<circle cx="' + cx + '" cy="' + cy + '" r="' + r + '"/>';
      var rx = Math.abs(k) * r;
      s += '<path d="M' + cx + " " + (cy - r) + " A" + rx + " " + r + " 0 0 "
        + (k < 0 ? 1 : 0) + " " + cx + " " + (cy + r)
        + " A" + r + " " + r + " 0 0 " + (k < 0 ? 0 : 1) + " " + cx + " " + (cy - r) + 'Z" fill="currentColor" fill-opacity=".25" stroke="none"/>';
    }
    s += '<line x1="10" y1="128" x2="198" y2="128" stroke-dasharray="1 4"/>';
    return s;
  }

  function icosahedron() {
    // classic vertex set, orthographic projection, slight tumble
    var t = (1 + Math.sqrt(5)) / 2, V = [];
    [[-1,t,0],[1,t,0],[-1,-t,0],[1,-t,0],[0,-1,t],[0,1,t],[0,-1,-t],[0,1,-t],
     [t,0,-1],[t,0,1],[-t,0,-1],[-t,0,1]].forEach(function (v) { V.push(v); });
    var E = [[0,1],[0,5],[0,7],[0,10],[0,11],[1,5],[1,7],[1,8],[1,9],[2,3],[2,4],
             [2,6],[2,10],[2,11],[3,4],[3,6],[3,8],[3,9],[4,5],[4,9],[4,11],[5,9],
             [5,11],[6,7],[6,8],[6,10],[7,8],[7,10],[8,9],[10,11]];
    var ax = rnd(0, 3), ay = rnd(0, 3), s = "";
    function proj(v) {
      var x = v[0], y = v[1], z = v[2];
      var x1 = x * Math.cos(ay) + z * Math.sin(ay), z1 = -x * Math.sin(ay) + z * Math.cos(ay);
      var y1 = y * Math.cos(ax) - z1 * Math.sin(ax);
      return [100 + x1 * 38, 100 + y1 * 38];
    }
    E.forEach(function (e) {
      var a = proj(V[e[0]]), b = proj(V[e[1]]);
      s += '<line x1="' + a[0] + '" y1="' + a[1] + '" x2="' + b[0] + '" y2="' + b[1] + '"/>';
    });
    return s + '<circle cx="100" cy="100" r="78" stroke-dasharray="2 6"/>';
  }

  function spiral() {
    // logarithmic (golden-ratio growth) spiral, plus its framing square
    var d = "", a = 0, r = 2, steps = 90;
    for (var i = 0; i <= steps; i++) {
      var x = 100 + r * Math.cos(a), y = 100 + r * Math.sin(a);
      d += (i ? " L" : "M") + x.toFixed(1) + " " + y.toFixed(1);
      a += 0.16; r *= Math.pow(1.618, 0.16 / (Math.PI / 2));
      if (r > 92) break;
    }
    return '<path d="' + d + '"/>' +
      '<rect x="22" y="22" width="156" height="156" stroke-dasharray="2 7"/>' +
      '<line x1="22" y1="118" x2="178" y2="118" stroke-dasharray="2 7"/>' +
      '<line x1="118" y1="22" x2="118" y2="118" stroke-dasharray="2 7"/>';
  }

  function flight() {
    var s = '<path d="M15 150 Q100 ' + rnd(30, 70) + ' 190 120" stroke-dasharray="2 7"/>';
    for (var i = 0; i < 5; i++) {
      var p = i / 4, qx = 15 + p * 175,
          qy = (1 - p) * (1 - p) * 150 + 2 * (1 - p) * p * rnd(30, 70) + p * p * 120;
      var w = 7 + i * 1.5;
      s += '<path d="M' + (qx - w) + " " + (qy - w * 0.5) + " Q" + qx + " " + (qy - w * 1.4)
        + " " + qx + " " + qy + " Q" + qx + " " + (qy - w * 1.4) + " "
        + (qx + w) + " " + (qy - w * 0.5) + '"/>';
    }
    return s;
  }

  function stars() {
    var s = "", pts = [];
    for (var i = 0; i < 26; i++) {
      var x = rnd(10, 190), y = rnd(10, 190);
      pts.push([x, y]);
      s += '<circle cx="' + x + '" cy="' + y + '" r="' + rnd(0.6, 1.6) + '" fill="currentColor" stroke="none"/>';
    }
    for (var j = 0; j < 5; j++) {
      var a = pick(pts), b = pick(pts);
      s += '<line x1="' + a[0] + '" y1="' + a[1] + '" x2="' + b[0] + '" y2="' + b[1] + '" stroke-dasharray="1 4"/>';
    }
    s += '<circle cx="100" cy="100" r="92" stroke-dasharray="2 8"/>';
    return s;
  }

  function sprig() {
    var s = '<path d="M100 190 C ' + rnd(80, 120) + ' 140, ' + rnd(85, 115) + ' 80, 100 14"/>';
    for (var i = 0; i < 7; i++) {
      var p = 0.18 + i * 0.11, y = 190 - p * 176, side = i % 2 ? 1 : -1,
          lx = 100 + side * rnd(18, 34), ly = y - rnd(6, 16);
      s += '<path d="M100 ' + y + " Q" + (100 + side * 12) + " " + (y - 4) + " " + lx + " " + ly
        + " Q" + (100 + side * 14) + " " + (y + 6) + " 100 " + y + '"/>';
    }
    return s;
  }

  function vortex() {
    var s = "";
    for (var j = 0; j < 3; j++) {
      var d = "M" + (100 + j * 6) + " 100", r = 3 + j * 2, a = j * 2;
      for (var i = 0; i < 26; i++) {
        a += 0.5; r *= 1.13;
        d += " L" + (100 + r * Math.cos(a)) + " " + (100 + r * Math.sin(a) * 0.7);
      }
      s += '<path d="' + d + '"/>';
    }
    return s;
  }

  function eye() {
    return '<path d="M20 100 Q100 40 180 100 Q100 160 20 100Z"/>' +
      '<circle cx="100" cy="100" r="30"/><circle cx="100" cy="100" r="13" fill="currentColor" fill-opacity=".3"/>' +
      Array.apply(null, Array(9)).map(function (_, i) {
        var a = -0.9 + i * 0.225;
        return '<line x1="' + (100 + 34 * Math.cos(a - 1.57)) + '" y1="' + (100 + 34 * Math.sin(a - 1.57))
          + '" x2="' + (100 + 44 * Math.cos(a - 1.57)) + '" y2="' + (100 + 44 * Math.sin(a - 1.57)) + '"/>';
      }).join("");
  }

  function wing() {
    var s = "", hub = [30, 170];
    for (var i = 0; i < 7; i++) {
      var a = -0.15 - i * 0.17, len = 150 - i * 6;
      s += '<line x1="' + hub[0] + '" y1="' + hub[1] + '" x2="' + (hub[0] + len * Math.cos(a))
        + '" y2="' + (hub[1] + len * Math.sin(a)) + '"/>';
    }
    s += '<path d="M30 170 Q100 ' + rnd(10, 40) + ' 180 ' + rnd(105, 130) + '"/>';
    s += '<path d="M30 170 Q95 ' + rnd(60, 90) + ' 178 ' + rnd(130, 150) + '" stroke-dasharray="3 5"/>';
    return s;
  }

  function script() {
    // rows of mirrored-hand squiggles: his notes, unreadable, unmistakable
    var s = "";
    for (var row = 0; row < 6; row++) {
      var y = 30 + row * 26, d = "M185 " + y, x = 185;
      while (x > rnd(15, 45)) {
        var seg = rnd(6, 16);
        d += " q-" + (seg / 2) + " " + rnd(-5, 5) + " -" + seg + " 0";
        x -= seg;
      }
      s += '<path d="' + d + '"/>';
    }
    return s;
  }

  var STUDIES = [gears, moons, icosahedron, spiral, flight, stars, sprig,
                 vortex, eye, wing, script];
  var PLATES = ["vitruvian.jpg", "flowers.jpg", "flying_machine.jpg", "tuscan.jpg"];

  /* -------------------- composition: margins first, never the middle ------ */
  function slots() {
    // viewport-percentage regions biased to edges and corners
    var s = [
      [rnd(1, 8), rnd(4, 20)], [rnd(1, 8), rnd(38, 60)], [rnd(2, 10), rnd(72, 88)],
      [rnd(78, 90), rnd(3, 18)], [rnd(80, 92), rnd(36, 58)], [rnd(76, 88), rnd(70, 86)],
      [rnd(25, 40), rnd(78, 90)], [rnd(55, 70), rnd(80, 92)],
      [rnd(28, 44), rnd(2, 8)], [rnd(58, 72), rnd(1, 7)],
    ];
    // shuffle
    for (var i = s.length - 1; i > 0; i--) {
      var j = Math.floor(R() * (i + 1)), t = s[i]; s[i] = s[j]; s[j] = t;
    }
    return s;
  }

  function build() {
    var old = document.getElementById("codex-bg");
    if (old) old.remove();
    if (!enabled()) return;

    var layer = document.createElement("div");
    layer.id = "codex-bg";
    layer.setAttribute("aria-hidden", "true");

    var pos = slots(), n = 0;

    // two scanned plates in far corners, large and ghostly
    var plates = PLATES.slice();
    [[rnd(-4, 2), rnd(55, 70)], [rnd(72, 84), rnd(-2, 10)]].forEach(function (p) {
      var img = document.createElement("div");
      img.className = "codex-piece codex-plate";
      var plate = plates.splice(Math.floor(R() * plates.length), 1)[0];
      img.style.backgroundImage = "url(/static/img/" + plate + ")";
      place(img, p[0], p[1], rnd(220, 320));
      layer.appendChild(img);
    });

    // six to eight line studies in the margins
    var deck = STUDIES.slice();
    var count = 6 + Math.floor(R() * 3);
    for (var i = 0; i < count && deck.length; i++) {
      var fn = deck.splice(Math.floor(R() * deck.length), 1)[0];
      var d = document.createElement("div");
      d.className = "codex-piece codex-study";
      var size = rnd(120, 210);
      d.innerHTML = '<svg viewBox="0 0 200 200" width="' + size + '" height="' + size
        + '" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linecap="round">'
        + fn() + "</svg>";
      var p = pos[n++ % pos.length];
      place(d, p[0], p[1], size);
      layer.appendChild(d);
    }

    document.body.prepend(layer);
  }

  function place(el, xPct, yPct, size) {
    el.style.left = xPct + "vw";
    el.style.top = yPct + "vh";
    el.style.width = size + "px";
    el.style.height = size + "px";
    el.style.setProperty("--drift-x", rnd(-18, 18).toFixed(1) + "px");
    el.style.setProperty("--drift-y", rnd(-14, 14).toFixed(1) + "px");
    el.style.setProperty("--drift-r", rnd(-4, 4).toFixed(2) + "deg");
    el.style.setProperty("--base-r", rnd(-9, 9).toFixed(1) + "deg");
    el.style.animationDuration = rnd(45, 110).toFixed(0) + "s";
    el.style.animationDelay = "-" + rnd(0, 60).toFixed(0) + "s";
  }

  /* ------------------------------- toggle -------------------------------- */
  function updateBtn() {
    var b = document.getElementById("ambience-toggle");
    if (b) b.textContent = enabled() ? "✦ ambience on" : "✧ ambience off";
  }
  function setAmbience(on) {
    try { localStorage.setItem(STORE, on ? "on" : "off"); } catch (e) {}
    build();
    updateBtn();
  }

  var btn = document.getElementById("ambience-toggle");
  if (btn) btn.addEventListener("click", function () { setAmbience(!enabled()); });
  updateBtn();

  if (window.Atlas) {
    window.Atlas.extraCommands = (window.Atlas.extraCommands || []).concat([
      { kind: "codex", label: "Ambience: Codex on", run: function () { setAmbience(true); } },
      { kind: "codex", label: "Ambience: Codex off", run: function () { setAmbience(false); } },
    ]);
  }

  build();
})();
