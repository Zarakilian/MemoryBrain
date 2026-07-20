/* The Codex Room — Leonardo's notebook as a place, not a picture.
   Procedurally drawn studies (geometry, machines, clockwork, architecture,
   anatomy, flight, botany, water, stars) float on depth planes behind the
   Atlas. The room breathes: pieces drift on slow loops, shift in parallax
   with the pointer, the whole plane tilts a fraction of a degree in
   perspective, and a warm lantern glow follows the cursor like candlelight
   over vellum. Inks come from a pastel fable palette. Every visit composes
   a different folio.

   Safety rails (this repo's scar tissue):
   - the layer and everything on it: pointer-events:none !important,
     z-index 0 under the shell — structurally incapable of eating a click
   - one pointermove listener; a self-stopping rAF lerp writes exactly
     four CSS custom properties per frame; all motion is GPU transforms
   - prefers-reduced-motion → no listener, no drift: a still folio
   - ambience toggle (rail + palette) removes the room entirely */
"use strict";

(function () {
  var STORE = "atlas-ambience";
  function enabled() {
    try { return localStorage.getItem(STORE) !== "off"; } catch (e) { return true; }
  }
  function reduced() {
    return window.matchMedia
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  var R = Math.random;
  function rnd(a, b) { return a + R() * (b - a); }
  function pick(arr) { return arr[Math.floor(R() * arr.length)]; }

  /* ================= the studies — each returns SVG inner markup ========= */
  /* one hand: fill none, stroke currentColor, thin line, faint hatching.
     colour comes from an ink class; viewBox 0 0 200 200. */

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

  function escapement() {
    // anchor escapement over a toothed wheel, pendulum hanging into space
    var s = '<path d="' + gearPath(100, 62, 30, 15) + '"/>' +
      '<circle cx="100" cy="62" r="5"/>' +
      '<path d="M70 40 Q100 8 130 40 L118 52 Q100 34 82 52 Z"/>' +   // anchor
      '<line x1="100" y1="24" x2="100" y2="14"/>';
    var swing = rnd(-0.35, 0.35);
    var bx = 100 + 118 * Math.sin(swing), by = 24 + 118 * Math.cos(swing);
    s += '<line x1="100" y1="24" x2="' + bx + '" y2="' + by + '"/>' +
      '<circle cx="' + bx + '" cy="' + by + '" r="9"/>' +
      '<path d="M60 190 A48 48 0 0 1 140 190" stroke-dasharray="2 6"/>'; // swing arc
    return s;
  }

  function gauge() {
    // steam pressure gauge with rivets, needle, pipe stub
    var s = '<circle cx="100" cy="90" r="52"/><circle cx="100" cy="90" r="44"/>';
    for (var i = 0; i <= 10; i++) {
      var a = Math.PI * (0.75 + 1.5 * i / 10);
      s += '<line x1="' + (100 + 38 * Math.cos(a)) + '" y1="' + (90 + 38 * Math.sin(a))
        + '" x2="' + (100 + 44 * Math.cos(a)) + '" y2="' + (90 + 44 * Math.sin(a)) + '"/>';
    }
    var na = Math.PI * rnd(0.85, 2.1);
    s += '<line x1="100" y1="90" x2="' + (100 + 34 * Math.cos(na)) + '" y2="'
      + (90 + 34 * Math.sin(na)) + '" stroke-width="1.8"/>'
      + '<circle cx="100" cy="90" r="4" fill="currentColor" fill-opacity=".4"/>';
    for (var j = 0; j < 6; j++) {
      var ra = j * Math.PI / 3 + 0.3;
      s += '<circle cx="' + (100 + 48 * Math.cos(ra)) + '" cy="' + (90 + 48 * Math.sin(ra)) + '" r="2"/>';
    }
    return s + '<path d="M92 142 h16 v14 a8 8 0 0 1 -16 0 Z"/>' +
      '<line x1="100" y1="164" x2="100" y2="184" stroke-dasharray="3 4"/>';
  }

  function dome() {
    // Brunelleschi's dome: drum, ribs, lantern
    var s = '<path d="M40 120 Q40 40 100 34 Q160 40 160 120"/>' +
      '<path d="M62 120 Q62 52 100 46 Q138 52 138 120" stroke-dasharray="1 4"/>';
    [-30, -12, 12, 30].forEach(function (dx) {
      s += '<path d="M100 34 Q' + (100 + dx * 1.9) + ' 66 ' + (100 + dx * 2) + ' 120"/>';
    });
    s += '<rect x="92" y="14" width="16" height="20"/><line x1="100" y1="14" x2="100" y2="4"/>' +
      '<line x1="34" y1="120" x2="166" y2="120"/><line x1="40" y1="132" x2="160" y2="132"/>';
    for (var i = 0; i < 8; i++) {
      var x = 48 + i * 14.5;
      s += '<path d="M' + x + ' 132 v22 a6 6 0 0 1 12 0 v-22" transform="translate(-6 0)"/>';
    }
    return s + '<line x1="34" y1="176" x2="166" y2="176"/>';
  }

  function colonnade() {
    // arcade of round arches on columns, receding hatch
    var s = '<line x1="12" y1="160" x2="188" y2="160"/>';
    for (var i = 0; i < 4; i++) {
      var x = 24 + i * 44;
      s += '<line x1="' + x + '" y1="160" x2="' + x + '" y2="86"/>' +
        '<line x1="' + (x + 36) + '" y1="160" x2="' + (x + 36) + '" y2="86"/>' +
        '<path d="M' + x + ' 86 A18 18 0 0 1 ' + (x + 36) + ' 86"/>' +
        '<rect x="' + (x - 3) + '" y="156" width="' + 42 + '" height="6"/>';
    }
    s += '<line x1="12" y1="60" x2="188" y2="60"/><line x1="12" y1="68" x2="188" y2="68"/>';
    for (var h = 0; h < 12; h++) {
      var hx = 18 + h * 14;
      s += '<line x1="' + hx + '" y1="172" x2="' + (hx + 8) + '" y2="180" stroke-dasharray="1 3"/>';
    }
    return s;
  }

  function ribcage() {
    var s = '<line x1="100" y1="20" x2="100" y2="150"/>';
    for (var i = 0; i < 6; i++) {
      var y = 36 + i * 18, w = 30 + i * 7 - (i > 3 ? (i - 3) * 9 : 0);
      s += '<path d="M100 ' + y + " Q" + (100 - w) + " " + (y + 2) + " " + (100 - w * 0.8) + " " + (y + 16) + '"/>' +
           '<path d="M100 ' + y + " Q" + (100 + w) + " " + (y + 2) + " " + (100 + w * 0.8) + " " + (y + 16) + '"/>';
    }
    return s + '<path d="M88 150 Q100 166 112 150" stroke-dasharray="1 4"/>' +
      '<circle cx="100" cy="24" r="4"/>';
  }

  function spineStudy() {
    var s = "", n = 11;
    for (var i = 0; i < n; i++) {
      var t = i / (n - 1), y = 18 + t * 160,
          x = 100 + Math.sin(t * Math.PI * 1.6) * 16,
          w = 13 - Math.abs(t - 0.5) * 6;
      s += '<rect x="' + (x - w / 2) + '" y="' + (y - 5) + '" width="' + w + '" height="10" rx="3"/>' +
        '<line x1="' + (x + w / 2) + '" y1="' + y + '" x2="' + (x + w / 2 + 8) + '" y2="' + (y - 3) + '"/>';
    }
    return s;
  }

  function handBones() {
    var s = '<ellipse cx="70" cy="150" rx="26" ry="18"/>';
    for (var f = 0; f < 5; f++) {
      var a = -1.35 + f * 0.32, len = f === 0 ? 52 : 88 - Math.abs(f - 2.6) * 12;
      var x = 70 + 24 * Math.cos(a), y = 150 + 16 * Math.sin(a);
      for (var seg = 0; seg < 3; seg++) {
        var l = len * (0.45 - seg * 0.11);
        var nx = x + l * Math.cos(a), ny = y + l * Math.sin(a);
        s += '<line x1="' + x + '" y1="' + y + '" x2="' + nx + '" y2="' + ny + '"/>' +
          '<circle cx="' + nx + '" cy="' + ny + '" r="2.4"/>';
        x = nx; y = ny;
      }
    }
    return s;
  }

  function moons() {
    var s = "", n = 5;
    for (var i = 0; i < n; i++) {
      var cx = 24 + i * 38, cy = 100, r = 14, k = (i / (n - 1)) * 2 - 1;
      s += '<circle cx="' + cx + '" cy="' + cy + '" r="' + r + '"/>';
      var rx = Math.abs(k) * r;
      s += '<path d="M' + cx + " " + (cy - r) + " A" + rx + " " + r + " 0 0 "
        + (k < 0 ? 1 : 0) + " " + cx + " " + (cy + r)
        + " A" + r + " " + r + " 0 0 " + (k < 0 ? 0 : 1) + " " + cx + " " + (cy - r) + 'Z" fill="currentColor" fill-opacity=".25" stroke="none"/>';
    }
    return s + '<line x1="10" y1="128" x2="198" y2="128" stroke-dasharray="1 4"/>';
  }

  function icosahedron() {
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
    var d = "", a = 0, r = 2;
    for (var i = 0; i <= 90; i++) {
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
    return s + '<circle cx="100" cy="100" r="92" stroke-dasharray="2 8"/>';
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

  var STUDIES = [gears, escapement, gauge, dome, colonnade, ribcage, spineStudy,
                 handBones, moons, icosahedron, spiral, flight, stars, sprig,
                 vortex, eye, wing, script];
  var PLATES = ["vitruvian.jpg", "flowers.jpg", "flying_machine.jpg", "tuscan.jpg"];
  var INKS = 6;                       // ink-0 … ink-5, defined per theme in CSS

  /* ============== composition: margins first, never the middle =========== */
  function slots() {
    var s = [
      [rnd(1, 8), rnd(4, 20)], [rnd(1, 8), rnd(38, 60)], [rnd(2, 10), rnd(72, 88)],
      [rnd(78, 90), rnd(3, 18)], [rnd(80, 92), rnd(36, 58)], [rnd(76, 88), rnd(70, 86)],
      [rnd(25, 40), rnd(78, 90)], [rnd(55, 70), rnd(80, 92)],
      [rnd(28, 44), rnd(2, 8)], [rnd(58, 72), rnd(1, 7)],
      [rnd(14, 24), rnd(20, 40)], [rnd(66, 78), rnd(22, 44)],
    ];
    for (var i = s.length - 1; i > 0; i--) {
      var j = Math.floor(R() * (i + 1)), t = s[i]; s[i] = s[j]; s[j] = t;
    }
    return s;
  }

  function decorate(el, xPct, yPct, size, depth, roam) {
    el.style.left = xPct + "vw";
    el.style.top = yPct + "vh";
    el.style.width = size + "px";
    el.style.height = size + "px";
    el.style.setProperty("--depth", depth.toFixed(2));
    var inner = el.firstChild, st = inner.style;
    // four waypoints of a slow float about the room; nearer pieces roam wider
    var ax = roam * (0.6 + depth), ay = roam * 0.8 * (0.6 + depth);
    for (var i = 1; i <= 3; i++) {
      st.setProperty("--x" + i, rnd(-ax, ax).toFixed(2) + "vw");
      st.setProperty("--y" + i, rnd(-ay, ay).toFixed(2) + "vh");
    }
    var r0 = rnd(-9, 9);
    st.setProperty("--r0", r0.toFixed(1) + "deg");
    for (var j = 1; j <= 3; j++) {
      st.setProperty("--r" + j, (r0 + rnd(-7, 7)).toFixed(1) + "deg");
    }
    st.setProperty("--s1", rnd(1.01, 1.05).toFixed(3));
    st.setProperty("--s2", rnd(0.96, 1.03).toFixed(3));
    st.animationDuration = rnd(70, 150).toFixed(0) + "s";
    st.animationDelay = "-" + rnd(0, 120).toFixed(0) + "s";
  }

  function build() {
    var old = document.getElementById("codex-bg");
    if (old) old.remove();
    stopEngine();
    if (!enabled()) return;

    var layer = document.createElement("div");
    layer.id = "codex-bg";
    layer.setAttribute("aria-hidden", "true");

    var lantern = document.createElement("div");
    lantern.id = "codex-lantern";
    layer.appendChild(lantern);

    var room = document.createElement("div");
    room.id = "codex-room";
    layer.appendChild(room);

    // the breath: two vast blooms that keep the room moving even when
    // the pointer rests
    for (var bi = 0; bi < 2; bi++) {
      var blob = document.createElement("div");
      blob.className = "codex-blob";
      blob.style.left = rnd(-20, 70) + "vw";
      blob.style.top = rnd(-25, 65) + "vh";
      blob.style.setProperty("--bx1", rnd(-14, 14).toFixed(1) + "vw");
      blob.style.setProperty("--by1", rnd(-10, 10).toFixed(1) + "vh");
      blob.style.setProperty("--bs", rnd(1.08, 1.3).toFixed(2));
      blob.style.animationDuration = rnd(50, 95).toFixed(0) + "s";
      blob.style.animationDelay = "-" + rnd(0, 50).toFixed(0) + "s";
      room.appendChild(blob);
    }

    var vignette = document.createElement("div");
    vignette.id = "codex-vignette";
    layer.appendChild(vignette);

    var pos = slots(), n = 0;

    // two scanned plates, deep in the room (small depth = far away)
    var plates = PLATES.slice();
    [[rnd(-4, 2), rnd(55, 70)], [rnd(72, 84), rnd(-2, 10)]].forEach(function (p) {
      var piece = document.createElement("div");
      piece.className = "codex-piece codex-plate";
      var inner = document.createElement("div");
      inner.className = "codex-drift";
      var plate = plates.splice(Math.floor(R() * plates.length), 1)[0];
      inner.style.backgroundImage = "url(/static/img/" + plate + ")";
      piece.appendChild(inner);
      decorate(piece, p[0], p[1], rnd(230, 330), rnd(0.15, 0.3), 1.6);
      room.appendChild(piece);
    });

    // eight to ten studies across the depth planes
    var deck = STUDIES.slice();
    var count = 8 + Math.floor(R() * 3);
    for (var i = 0; i < count && deck.length; i++) {
      var fn = deck.splice(Math.floor(R() * deck.length), 1)[0];
      var piece = document.createElement("div");
      var depth = rnd(0.25, 1);
      piece.className = "codex-piece codex-study ink-" + Math.floor(R() * INKS);
      var size = rnd(110, 150) + depth * rnd(50, 90);   // nearer = larger
      var inner = document.createElement("div");
      inner.className = "codex-drift";
      inner.innerHTML = '<svg viewBox="0 0 200 200" width="' + Math.round(size)
        + '" height="' + Math.round(size)
        + '" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linecap="round">'
        + fn() + "</svg>";
      piece.appendChild(inner);
      var p = pos[n++ % pos.length];
      decorate(piece, p[0], p[1], size, depth, 4.2);
      room.appendChild(piece);
    }

    document.body.prepend(layer);
    if (!reduced()) startEngine(layer);
  }

  /* ================= the engine: one listener, one lerp =================== */
  var engine = { raf: 0, layer: null, tx: 0, ty: 0, x: 0, y: 0,
                 lx: 0, ly: 0, tlx: 0, tly: 0, onMove: null };

  function startEngine(layer) {
    engine.layer = layer;
    engine.tlx = engine.lx = window.innerWidth / 2;
    engine.tly = engine.ly = window.innerHeight / 3;
    engine.onMove = function (ev) {
      engine.tx = (ev.clientX / window.innerWidth - 0.5);   // -0.5 … 0.5
      engine.ty = (ev.clientY / window.innerHeight - 0.5);
      engine.tlx = ev.clientX;
      engine.tly = ev.clientY;
      if (!engine.raf) engine.raf = requestAnimationFrame(tick);
    };
    window.addEventListener("pointermove", engine.onMove, { passive: true });
  }

  function stopEngine() {
    if (engine.onMove) window.removeEventListener("pointermove", engine.onMove);
    if (engine.raf) cancelAnimationFrame(engine.raf);
    engine.raf = 0; engine.onMove = null; engine.layer = null;
  }

  function tick() {
    engine.raf = 0;
    var k = 0.07;
    engine.x += (engine.tx - engine.x) * k;
    engine.y += (engine.ty - engine.y) * k;
    engine.lx += (engine.tlx - engine.lx) * k;
    engine.ly += (engine.tly - engine.ly) * k;
    var st = engine.layer.style;
    st.setProperty("--mx", (engine.x * -26).toFixed(2) + "px");   // parallax shift
    st.setProperty("--my", (engine.y * -20).toFixed(2) + "px");
    st.setProperty("--tiltx", (engine.y * 1.6).toFixed(3) + "deg");
    st.setProperty("--tilty", (engine.x * -1.6).toFixed(3) + "deg");
    st.setProperty("--lx", engine.lx.toFixed(1) + "px");
    st.setProperty("--ly", engine.ly.toFixed(1) + "px");
    var settled = Math.abs(engine.tx - engine.x) < 0.0005
      && Math.abs(engine.ty - engine.y) < 0.0005
      && Math.abs(engine.tlx - engine.lx) < 0.5
      && Math.abs(engine.tly - engine.ly) < 0.5;
    if (!settled) engine.raf = requestAnimationFrame(tick);
  }

  /* ================================ toggle ================================ */
  function updateBtn() {
    var b = document.getElementById("ambience-toggle");
    if (b) b.textContent = enabled() ? "✦ ambience on" : "✧ ambience off";
  }
  function setAmbience(on) {
    try { localStorage.setItem(STORE, on ? "on" : "off"); } catch (e) {}
    build();
    updateBtn();
    document.dispatchEvent(new CustomEvent("atlas:ambience", { detail: { on: on } }));
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
