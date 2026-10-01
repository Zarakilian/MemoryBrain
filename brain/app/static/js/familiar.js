/* The Familiar — a small companion of starlight that follows the cursor
   through the Nebula. It cycles between forms every so often (or keeps
   the one you pick), each with its own movement personality — every
   creature FACES where it is going and chases with real lag, so it feels
   like pursuit, not glue:

     footprints — stamped along your path, stride varies
     butterfly  — chaotic darts and drifting loops; flutters harder the
                  faster it flies; sheds a little pollen of light
     spider     — RUNS after the pointer on eight legs in a real gait
                  (legs paced by distance, so feet never slide), in bursts
                  and pauses, sometimes pouncing. When it reaches a calm
                  hand it lowers itself head-down on a dragline, hangs
                  and swings like a pendulum when the hand moves (silk
                  barely stretches; it goes slack and sags), then turns
                  and climbs back up hand over hand
     comet      — a hot head on a spring, its tail growing with speed;
                  when the hand rests it circles it like a small moon
     wisps      — a swarm of light orbs that wander and blink around the
                  cursor, stream after a quick hand, scatter from a click

   A click anywhere sheds a small burst of starlight. Everything drawn in
   light (comet, wisps, sparks) shares one canvas and one glow sprite.

   It has its own switch (rail button + palette, remembered), independent
   of the world's ambience. Same safety rails as everything ambient here:
   pointer-events:none !important layer, one pointermove listener, one
   rAF loop (self-stopping: after a long still hand the familiar dozes
   and nothing runs), transforms only, absent under reduced motion. */
"use strict";

(function () {
  function reduced() {
    return window.matchMedia
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }
  if (reduced()) return;

  var STORE = "atlas-familiar";
  var FORM_STORE = "atlas-familiar-form";      // "cycle" or one form's name
  function enabled() {
    try { return localStorage.getItem(STORE) !== "off"; } catch (e) { return true; }
  }

  var R = Math.random;
  function rnd(a, b) { return a + R() * (b - a); }

  /* ------------------------------ the forms ------------------------------
     All creatures are drawn FACING RIGHT (+x) so one heading rotation
     orients them along their motion. */
  var SVG_OPEN = '<svg viewBox="0 0 60 60" width="46" height="46" fill="currentColor" stroke="none">';

  /* the chosen butterfly, the original form, unchanged; it is
     drawn head-up, so facing adds a quarter-turn (headingOff) */
  var BUTTERFLY_SVG = SVG_OPEN
    + '<ellipse cx="30" cy="32" rx="3.4" ry="11"/>'
    + '<path d="M30 24 Q26 14 22 12" fill="none" stroke="currentColor" stroke-width="1.6"/>'
    + '<path d="M30 24 Q34 14 38 12" fill="none" stroke="currentColor" stroke-width="1.6"/>'
    + '<g class="fam-wing-l"><ellipse cx="18" cy="27" rx="12" ry="8" transform="rotate(-24 18 27)"/>'
    + '<ellipse cx="20" cy="39" rx="9" ry="6" transform="rotate(18 20 39)"/></g>'
    + '<g class="fam-wing-r"><ellipse cx="42" cy="27" rx="12" ry="8" transform="rotate(24 42 27)"/>'
    + '<ellipse cx="40" cy="39" rx="9" ry="6" transform="rotate(-18 40 39)"/></g>'
    + "</svg>";

  /* eight legs, each its own path from a hip on the cephalothorax, so
     each can swing on its own; [hipX, hipY, side (+1 left, -1 right),
     gait set] — the alternating tetrapod gait real spiders use: L1 R2 L3
     R4 step together, then R1 L2 R3 L4 */
  var LEGS = [
    [38.5, 26.2, 1, 0, "M38.5 26.2 L45 16.5 L55.5 13"],
    [36.5, 25.4, 1, 1, "M36.5 25.4 L39 13 L45.5 5.5"],
    [34, 25.4, 1, 0, "M34 25.4 L29 13.5 L21.5 7"],
    [31.8, 26.4, 1, 1, "M31.8 26.4 L21.5 18 L9 15.5"],
    [38.5, 33.8, -1, 1, "M38.5 33.8 L45 43.5 L55.5 47"],
    [36.5, 34.6, -1, 0, "M36.5 34.6 L39 47 L45.5 54.5"],
    [34, 34.6, -1, 1, "M34 34.6 L29 46.5 L21.5 53"],
    [31.8, 33.6, -1, 0, "M31.8 33.6 L21.5 42 L9 44.5"],
  ];
  var SPIDER_SVG = SVG_OPEN
    + '<g fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
    + LEGS.map(function (l) { return '<path class="fam-leg" d="' + l[4] + '"/>'; }).join("")
    + '<path d="M40.4 28.2 Q43 27.2 44.6 28.4"/><path d="M40.4 31.8 Q43 32.8 44.6 31.6"/>'
    + "</g>"
    // abdomen behind, cephalothorax ahead, joined at the pedicel
    + '<ellipse cx="23" cy="30" rx="9.2" ry="7.2"/>'
    + '<path d="M16.5 30 Q23 26.4 29.5 30 Q23 33.6 16.5 30 Z" fill="rgba(255,255,255,.2)"/>'
    + '<ellipse cx="29.6" cy="30" rx="1.8" ry="1.5"/>'
    + '<ellipse cx="35" cy="30" rx="5.8" ry="4.9"/>'
    + '<circle cx="39.3" cy="28.7" r="0.95" fill="rgba(8,12,24,.65)"/>'
    + '<circle cx="39.3" cy="31.3" r="0.95" fill="rgba(8,12,24,.65)"/>'
    + '<circle cx="38.3" cy="27.7" r="0.6" fill="rgba(8,12,24,.55)"/>'
    + '<circle cx="38.3" cy="32.3" r="0.6" fill="rgba(8,12,24,.55)"/>'
    + "</svg>";
  var SILK_AT = 12;            // px from the body's centre to where the silk holds

  var ORDER = ["paws", "butterfly", "spider", "comet", "wisps"];
  var LABELS = { paws: "Paws", butterfly: "Butterfly", spider: "Spider",
                 comet: "Comet", wisps: "Wisps" };

  var PRINT_SVG = '<svg viewBox="0 0 20 34" width="13" height="22" fill="currentColor">'
    + '<path d="M10 1 C15 1 17 6 16 13 C15.3 18 14 20.5 12.6 21.5 L7.4 21.5 C6 20.5 4.7 18 4 13 C3 6 5 1 10 1 Z"/>'
    + '<ellipse cx="10" cy="28.5" rx="4.6" ry="4"/></svg>';

  var SLEEP_MS = 12000;        // a hand this still for this long: the familiar dozes

  /* ------------------------------ the layer ------------------------------ */
  var layer = document.createElement("div");
  layer.id = "familiar-layer";
  layer.setAttribute("aria-hidden", "true");
  /* the sky: one canvas for everything drawn in light */
  var sky = document.createElement("canvas");
  sky.className = "fam-sky";
  layer.appendChild(sky);
  var ctx = sky.getContext("2d");
  var body = document.createElement("div");
  body.className = "fam";
  layer.appendChild(body);
  /* the silk: an SVG path so it can SAG and BOW like a real thread */
  var SVGNS = "http://www.w3.org/2000/svg";
  var web = document.createElementNS(SVGNS, "svg");
  web.setAttribute("class", "fam-web-svg");
  var webPath = document.createElementNS(SVGNS, "path");
  webPath.setAttribute("class", "fam-web-path");
  web.appendChild(webPath);
  web.style.display = "none";
  layer.appendChild(web);
  document.body.appendChild(layer);

  var st = {
    form: null, raf: 0, asleep: false,
    x: innerWidth / 2, y: innerHeight / 2,
    tx: innerWidth / 2, ty: innerHeight / 2,
    vx: 0, vy: 0,                       // measured velocity (for facing)
    heading: 0, headingOff: 0,          // smoothed facing, per-form offset
    lastMove: 0, lastX: 0, lastY: 0, dist: 0, stride: 44,
    lastT: 0,
    // butterfly impulses
    bGoal: [0, 0], bLerp: 0.05, bNext: 0, bLoop: 0, wings: [], wingMs: 0,
    // spider
    sState: "chase",                    // chase | jump | descend | hang | climb
    sNext: 0, sJumpT: 0, sLen: 0, sLenT: 0,
    sSpeed: 0, sPause: 0, gait: 0, legAmp: 0, legs: [],
    sp: { x: 0, y: 0, px: 0, py: 0 },   // where the silk holds (a Verlet point)
    sDt: 0.016, sStroke: 0,
    printFlip: false, prints: [],
  };

  /* ------------------------------- the sky -------------------------------
     A glow is drawn once per colour into a small sprite and then stamped
     (additive), so a few hundred sparks cost a few hundred drawImages. */
  var DPR = 1, SKY_W = 0, SKY_H = 0;
  function sizeSky() {
    DPR = Math.min(window.devicePixelRatio || 1, 1.5);   // soft light needs no more
    SKY_W = innerWidth; SKY_H = innerHeight;
    sky.width = Math.round(SKY_W * DPR);
    sky.height = Math.round(SKY_H * DPR);
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  }
  /* a GPU reset (sleep and wake, a driver hiccup) wipes every canvas the
     browser keeps on the GPU, and a sprite painted once would stay blank
     for good: each sprite keeps its painter, repaints when the browser
     restores it, and is checked again whenever the familiar wakes */
  function sprite(r, g, b) {
    var c = document.createElement("canvas");
    c.width = c.height = 64;
    c.paint = function () {
      var x = c.getContext("2d");
      x.clearRect(0, 0, 64, 64);
      var gr = x.createRadialGradient(32, 32, 0, 32, 32, 32);
      gr.addColorStop(0, "rgba(255,255,255,1)");
      gr.addColorStop(0.16, "rgba(" + r + "," + g + "," + b + ",.9)");
      gr.addColorStop(0.42, "rgba(" + r + "," + g + "," + b + ",.26)");
      gr.addColorStop(1, "rgba(" + r + "," + g + "," + b + ",0)");
      x.fillStyle = gr;
      x.fillRect(0, 0, 64, 64);
    };
    c.addEventListener("contextrestored", c.paint);
    c.paint();
    return c;
  }
  var GLOW = {
    star: sprite(255, 222, 160),        // warm starlight
    aura: sprite(150, 190, 240),        // cool counterpoint
    violet: sprite(185, 160, 255),
    teal: sprite(120, 232, 212),
  };
  var HUES = [GLOW.star, GLOW.aura, GLOW.violet, GLOW.teal];
  function spritesAlive() {
    try { return GLOW.star.getContext("2d").getImageData(32, 32, 1, 1).data[3] > 0; }
    catch (e) { return true; }
  }
  function repaintSprites() { HUES.forEach(function (c) { c.paint(); }); }
  sky.addEventListener("contextrestored", function () { sizeSky(); });
  function stampGlow(img, x, y, size, alpha) {
    if (alpha <= 0.004 || size <= 0.3) return;
    ctx.globalAlpha = Math.min(1, alpha);
    ctx.drawImage(img, x - size / 2, y - size / 2, size, size);
  }

  /* sparks: stardust, pollen and click bursts, one pool */
  var sparks = [], rings = [], MAX_SPARKS = 260;
  function spark(x, y, vx, vy, life, size, img, drag) {
    if (sparks.length >= MAX_SPARKS) sparks.shift();
    sparks.push({ x: x, y: y, vx: vx, vy: vy, t: 0, life: life, size: size,
                  img: img, drag: drag || 1.4, tw: rnd(0, 6.28) });
  }
  function burst(x, y) {
    rings.push({ x: x, y: y, t: 0, life: 0.52 });
    for (var i = 0; i < 12; i++) {
      var a = (i / 12) * Math.PI * 2 + rnd(-0.2, 0.2), sp = rnd(110, 250);
      spark(x, y, Math.cos(a) * sp, Math.sin(a) * sp, rnd(0.45, 0.8), rnd(5, 9),
            R() < 0.65 ? GLOW.star : GLOW.aura, 4.2);
    }
  }
  function stepSparks(dt) {
    for (var i = sparks.length - 1; i >= 0; i--) {
      var p = sparks[i];
      p.t += dt;
      if (p.t >= p.life) { sparks.splice(i, 1); continue; }
      var dr = Math.exp(-p.drag * dt);
      p.vx *= dr; p.vy *= dr;
      p.x += p.vx * dt; p.y += p.vy * dt;
      var f = 1 - p.t / p.life;
      stampGlow(p.img, p.x, p.y, p.size * (0.55 + 0.45 * f) * (0.8 + 0.2 * Math.sin(p.tw + p.t * 18)),
                0.85 * f * f);
    }
    for (var j = rings.length - 1; j >= 0; j--) {
      var rg = rings[j];
      rg.t += dt;
      if (rg.t >= rg.life) { rings.splice(j, 1); continue; }
      var k = rg.t / rg.life, ease = 1 - (1 - k) * (1 - k);
      ctx.globalAlpha = 0.5 * (1 - k);
      ctx.strokeStyle = "rgba(255, 228, 176, 1)";
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      ctx.arc(rg.x, rg.y, 4 + 30 * ease, 0, Math.PI * 2);
      ctx.stroke();
    }
    return sparks.length > 0 || rings.length > 0;
  }

  /* the comet: a head on a spring, a tail of where it has been */
  var comet = { x: 0, y: 0, vx: 0, vy: 0, trail: [], emit: 0 };
  function stepComet(now, dt, idleFor) {
    var c = comet, t = now * 0.001;
    /* moving: it rides just off the tip; resting: it circles like a moon */
    var rest = Math.max(0, Math.min(1, (idleFor - 300) / 900));
    var gx = st.tx + 18 * (1 - rest) + Math.cos(t * 2.1) * 36 * rest;
    var gy = st.ty + 14 * (1 - rest) + Math.sin(t * 2.1) * 22 * rest - 4 * rest;
    c.vx += ((gx - c.x) * 38 - c.vx * 7.5) * dt;      // swingy, a slight overshoot
    c.vy += ((gy - c.y) * 38 - c.vy * 7.5) * dt;
    c.x += c.vx * dt; c.y += c.vy * dt;
    c.trail.unshift(c.x, c.y);
    if (c.trail.length > 56) c.trail.length = 56;     // 28 points
    var speed = Math.hypot(c.vx, c.vy);
    /* the tail: stamped along the path, every ~4 px, thinning to nothing */
    var n = c.trail.length / 2, grow = Math.min(1, 0.35 + speed / 650);
    for (var i = n - 1; i >= 1; i--) {
      var x0 = c.trail[i * 2], y0 = c.trail[i * 2 + 1];
      var x1 = c.trail[i * 2 - 2], y1 = c.trail[i * 2 - 1];
      var seg = Math.hypot(x1 - x0, y1 - y0), steps = Math.min(12, Math.max(1, Math.ceil(seg / 5)));
      for (var s = 0; s < steps; s++) {
        var u = (i - s / steps) / n, f = 1 - u;
        var px = x0 + (x1 - x0) * (s / steps), py = y0 + (y1 - y0) * (s / steps);
        stampGlow(u < 0.35 ? GLOW.star : GLOW.aura, px, py, (5 + 20 * f) * grow,
                  0.5 * Math.pow(f, 1.7) * grow);
      }
    }
    stampGlow(GLOW.star, c.x, c.y, 46, 0.32);         // the coma
    stampGlow(GLOW.star, c.x, c.y, 15, 1);            // the hot head
    /* a fast comet sheds sparks; its lazy orbit round a resting hand
       does not (or the familiar could never doze) */
    c.emit += speed > 180 ? speed * dt : 0;
    while (c.emit > 26) {
      c.emit -= 26;
      spark(c.x, c.y, -c.vx * 0.12 + rnd(-30, 30), -c.vy * 0.12 + rnd(-30, 30),
            rnd(0.5, 0.9), rnd(3, 6), R() < 0.5 ? GLOW.star : GLOW.aura, 2.2);
    }
    return true;
  }

  /* the wisps: orbs of light, each on its own wandering orbit */
  var wisps = [];
  function makeWisps() {
    wisps = [];
    for (var i = 0; i < 7; i++) {
      wisps.push({ x: st.tx + rnd(-60, 60), y: st.ty + rnd(-60, 60), vx: 0, vy: 0,
                   ph: rnd(0, 6.28), rad: rnd(24, 68), sp: rnd(0.5, 1.15),
                   k: rnd(11, 22), period: rnd(2.2, 4.2), off: rnd(0, 4),
                   img: HUES[i % HUES.length], size: rnd(5, 8.5) });
    }
  }
  function stepWisps(now, dt, idleFor) {
    var t = now * 0.001, calm = idleFor > 1400 ? 0.55 : 1;
    for (var i = 0; i < wisps.length; i++) {
      var w = wisps[i];
      var gx = st.tx + Math.cos(w.ph + t * w.sp) * w.rad;
      var gy = st.ty + Math.sin(w.ph * 1.3 + t * w.sp * 1.4) * w.rad * 0.62;
      w.vx += ((gx - w.x) * w.k - w.vx * 4.2) * dt;
      w.vy += ((gy - w.y) * w.k - w.vy * 4.2) * dt;
      w.x += w.vx * dt; w.y += w.vy * dt;
      /* a quick flash, then a soft glow: the way fireflies talk */
      var b = ((t + w.off) % w.period) / w.period;
      var glow = (0.55 + 0.45 * Math.exp(-Math.pow((b - 0.08) / 0.05, 2))) * calm;
      stampGlow(w.img, w.x, w.y, w.size * 7, 0.3 * glow);
      stampGlow(w.img, w.x, w.y, w.size * 1.9, glow);
    }
    return true;
  }
  function scatterWisps(x, y) {
    wisps.forEach(function (w) {
      var dx = w.x - x, dy = w.y - y, d = Math.hypot(dx, dy) || 1;
      var kick = 520 * Math.max(0.25, 1 - d / 260);
      w.vx += dx / d * kick; w.vy += dy / d * kick;
    });
  }

  /* --------------------------- choosing a form --------------------------- */
  function chosenForm() {
    try {
      var v = localStorage.getItem(FORM_STORE);
      return ORDER.indexOf(v) >= 0 ? v : "cycle";
    } catch (e) { return "cycle"; }
  }
  function chooseForm(name) {
    try { localStorage.setItem(FORM_STORE, name); } catch (e) {}
    if (name !== "cycle") setForm(name);
    if (!enabled()) setEnabled(true);
  }

  function applyEnabled() {
    layer.style.display = enabled() ? "" : "none";
    var b = document.getElementById("familiar-toggle");
    if (b) b.textContent = enabled() ? "❋ familiar on" : "❋ familiar off";
    if (!enabled()) { sparks = []; rings = []; ctx.clearRect(0, 0, SKY_W, SKY_H); }
  }
  function setEnabled(on) {
    try { localStorage.setItem(STORE, on ? "on" : "off"); } catch (e) {}
    applyEnabled();
    if (on) { sizeSky(); if (!spritesAlive()) repaintSprites(); wake(); }
  }

  function clearForm() {
    body.innerHTML = "";
    body.className = "fam";
    body.style.transform = "";
    web.style.display = "none";
    st.wings = [];
    st.legs = [];
  }

  function setForm(name) {
    st.form = name;
    body.classList.add("morph");
    setTimeout(function () {
      clearForm();
      body.classList.add("morph");
      if (name === "butterfly") {
        body.innerHTML = BUTTERFLY_SVG;
        st.wings = Array.prototype.slice.call(body.querySelectorAll(".fam-wing-l, .fam-wing-r"));
        st.wingMs = 0;
        st.headingOff = Math.PI / 2;           // drawn head-up; 0 rad = right
        st.heading = -Math.PI / 2;             // start upright, no first-frame spin
        st.x = st.tx + 40; st.y = st.ty + 40;
      } else if (name === "spider") {
        body.innerHTML = SPIDER_SVG;
        st.legs = Array.prototype.slice.call(body.querySelectorAll(".fam-leg"));
        st.headingOff = 0;                     // drawn head-right
        st.sState = "chase";
        st.sSpeed = 0; st.sPause = 0; st.gait = 0; st.legAmp = 0;
        st.sNext = performance.now() + rnd(2500, 5000);
        st.x = st.tx - 120; st.y = st.ty + 80;
        st.heading = Math.atan2(st.ty - st.y, st.tx - st.x);
      } else {
        body.classList.add("hidden-form");     // drawn in light, or prints only
        if (name === "comet") {
          comet.x = st.tx - 90; comet.y = st.ty + 60;
          comet.vx = comet.vy = 0; comet.trail = [];
        } else if (name === "wisps") makeWisps();
      }
      requestAnimationFrame(function () { body.classList.remove("morph"); });
      wake();
    }, 180);
  }

  function scheduleMorph() {
    setTimeout(function () {
      if (chosenForm() === "cycle" && !st.asleep) {     // never morph in its sleep
        var i = (ORDER.indexOf(st.form) + 1) % ORDER.length;
        setForm(ORDER[i]);
      }
      scheduleMorph();
    }, rnd(12000, 22000));
  }

  /* ------------------------------ footprints ----------------------------- */
  function stamp(x, y, angle) {
    var d = document.createElement("div");
    d.className = "fam-print";
    st.printFlip = !st.printFlip;
    var side = st.printFlip ? 8 : -8;
    d.style.left = (x + Math.cos(angle + Math.PI / 2) * side) + "px";
    d.style.top = (y + Math.sin(angle + Math.PI / 2) * side) + "px";
    d.style.transform = "translate(-50%,-50%) rotate(" + (angle * 180 / Math.PI + 90)
      + "deg)" + (st.printFlip ? "" : " scaleX(-1)");
    d.innerHTML = PRINT_SVG;
    layer.appendChild(d);
    st.prints.push(d);
    if (st.prints.length > 24) st.prints.shift().remove();
    setTimeout(function () { d.remove(); }, 1800);
  }

  /* ------------------------------- helpers ------------------------------- */
  function face(target) {
    // shortest-path smoothing so the creature turns, never snaps
    var d = target - st.heading;
    while (d > Math.PI) d -= 2 * Math.PI;
    while (d < -Math.PI) d += 2 * Math.PI;
    st.heading += d * 0.18;
  }
  function place(scale) {
    body.style.transform = "translate3d(" + st.x.toFixed(1) + "px,"
      + st.y.toFixed(1) + "px,0) translate(-50%,-50%) rotate("
      + ((st.heading + st.headingOff) * 180 / Math.PI).toFixed(1) + "deg)"
      + (scale && scale !== 1 ? " scale(" + scale.toFixed(3) + ")" : "");
  }
  function drawWeb(x1, y1, x2, y2, slack) {
    /* silk under load is a straight line; slack silk sags under its own
       weight (a shallow parabola of the thread's real length) */
    var d = Math.hypot(x2 - x1, y2 - y1), sag = 0;
    if (slack > 0.5) sag = Math.min(Math.sqrt(3 * Math.max(d, 1) * slack / 8), slack / 2 + 2);
    var mx = (x1 + x2) / 2, my = (y1 + y2) / 2 + 2 * sag;   // control = twice the dip
    web.style.display = "";
    webPath.setAttribute("d",
      "M" + x1.toFixed(1) + " " + y1.toFixed(1)
      + " Q" + mx.toFixed(1) + " " + my.toFixed(1)
      + " " + x2.toFixed(1) + " " + y2.toFixed(1));
  }

  function wrapAngle(a) {
    while (a > Math.PI) a -= 2 * Math.PI;
    while (a < -Math.PI) a += 2 * Math.PI;
    return a;
  }

  /* the legs, posed for this frame. gait is in strides; amp 0..1 is how
     hard they work; tuck pulls them in (a pounce, or hanging still).
     Each leg swings about its hip, forward then back; the stepping leg
     lifts (reads shorter from above) while its partners push. */
  function poseLegs(amp, tuck, twitch, now) {
    var ph = st.gait * Math.PI * 2;
    for (var i = 0; i < st.legs.length; i++) {
      var L = LEGS[i], off = L[3] ? Math.PI : 0;
      var a = L[2] * amp * 0.42 * Math.sin(ph + off);
      if (twitch) a += L[2] * 0.07 * Math.sin(now * 0.0023 + i * 1.9);
      var lift = 1 - 0.08 * amp * Math.max(0, Math.cos(ph + off));
      var sc = lift * (1 - tuck);
      st.legs[i].setAttribute("transform",
        "translate(" + L[0] + " " + L[1] + ") rotate(" + (a * 57.2958).toFixed(1)
        + ") scale(" + sc.toFixed(3) + ") translate(" + (-L[0]) + " " + (-L[1]) + ")");
    }
  }

  /* running: a hunter's dash. It steers at a spider's turning pace,
     slows to pivot on a hard turn, stops and freezes now and then, and
     its legs are paced by the ground it covers. */
  function spiderRun(now, dt, idle) {
    var dx = st.tx - st.x, dy = st.ty - st.y, gap = Math.hypot(dx, dy);
    var want = gap > 220 ? 540 : gap > 70 ? 320 : gap > 22 ? gap * 3.4 : 0;
    var jumping = st.sState === "jump", jt = 0;
    if (jumping) {
      jt = (now - st.sJumpT) / 380;
      if (jt >= 1) { st.sState = "chase"; jumping = false; }
      else want = 640;                        // the pounce carries it
    }
    if (!jumping) {
      if (now < st.sPause) want = 0;          // frozen, watching
      else if (st.sSpeed > 280 && R() < dt * 0.7) st.sPause = now + rnd(220, 600);
    }
    if (gap > 2) {
      var dh = wrapAngle(Math.atan2(dy, dx) - st.heading), turn = 11 * dt;
      st.heading += Math.max(-turn, Math.min(turn, dh));
      if (Math.abs(dh) > 1.6 && !jumping) want *= 0.3;
    }
    var acc = want > st.sSpeed ? 2600 : 3600;
    st.sSpeed += Math.max(-acc * dt, Math.min(acc * dt, want - st.sSpeed));
    st.x += Math.cos(st.heading) * st.sSpeed * dt;
    st.y += Math.sin(st.heading) * st.sSpeed * dt;
    /* one stride per 26 px, so the feet hold the ground; past ~10 strides
       a second a screen cannot show legs, so the beat stops rising there */
    st.gait += Math.min(st.sSpeed * dt / 26, 10 * dt);
    st.legAmp += (Math.min(1, st.sSpeed / 140) - st.legAmp) * Math.min(1, dt * 12);

    var scale = 1;
    if (jumping) {
      var arc = 4 * jt * (1 - jt);
      scale = 1 + arc * 0.2;                   // it leaves the ground, so it grows
      poseLegs(0, 0.12 * arc, false, now);     // legs tucked in the air
    } else {
      poseLegs(st.legAmp, 0, st.legAmp < 0.05, now);
    }

    if (!jumping && now > st.sNext) {
      var calm = idle || now - st.lastMove > 500;
      if (gap < 24 && calm && innerHeight - st.ty > 120) {
        /* at a quiet hand: step off and lower itself on a dragline */
        st.sState = "descend";
        st.sp.x = st.x; st.sp.y = st.y - SILK_AT;
        st.sp.px = st.sp.x; st.sp.py = st.sp.y;
        st.sLen = Math.max(4, Math.hypot(st.sp.x - st.tx, st.sp.y - st.ty));
        st.sLenT = Math.min(rnd(150, 300), innerHeight - st.ty - 40);
        st.sSpeed = 0;
      } else if (gap > 40 && gap < 170 && R() < 0.5) {
        st.sState = "jump";                    // a pounce at the hand
        st.sJumpT = now;
        st.sNext = now + rnd(2800, 6000);
      } else {
        st.sNext = now + rnd(900, 2400);       // bide, keep stalking
      }
    }
    place(scale);
    return true;
  }

  /* on the silk: the holding point is a Verlet point under gravity and
     light air drag, tied to the live cursor by a thread that only pulls
     and barely stretches (a pendulum, not a bungee). The body hangs in
     line with the thread: head down on the way down and while hanging
     (the silk leaves the spinnerets at the back), turned head up to climb. */
  function spiderSilk(now, dt) {
    var a = { x: st.tx, y: st.ty }, P = st.sp, S = st.sState;
    if (S === "descend") {
      st.sLen = Math.min(st.sLenT, st.sLen + 95 * dt);           // paying out silk
      st.gait += 95 * dt / 40;
      if (st.sLen >= st.sLenT - 0.5) { st.sState = "hang"; st.sNext = now + rnd(3500, 6500); }
    } else if (S === "hang") {
      if (now > st.sNext) { st.sState = "climb"; st.sStroke = now; }
    } else if (S === "climb") {
      /* hand over hand: pull for most of a stroke, grip, pull again */
      var ph = ((now - st.sStroke) % 200) / 200;
      if (ph < 0.62) {
        var dl = 11 / (0.62 * 0.2) * dt;      // 11 px a stroke, ~55 px/s overall
        st.sLen = Math.max(0, st.sLen - dl);
        st.gait += dl / 22;                   // half a gait cycle per stroke
      }
    }
    var L = Math.min(st.sLen, Math.max(30, innerHeight - a.y - 24));

    /* time-corrected Verlet: momentum, gravity, air */
    var k = dt / (st.sDt || dt);
    st.sDt = dt;
    var vx = (P.x - P.px) * k, vy = (P.y - P.py) * k;
    var air = Math.exp(-1.4 * dt);            // a small body in air: swings die in a few beats
    vx *= air; vy *= air;
    var vm = Math.hypot(vx, vy), vmax = 1400 * dt;   // a flick cannot fling it off-screen
    if (vm > vmax) { vx *= vmax / vm; vy *= vmax / vm; }
    P.px = P.x; P.py = P.y;
    P.x += vx; P.y += vy + 3800 * dt * dt;
    /* the thread only pulls, and does not stretch: project back onto it */
    var rx = P.x - a.x, ry = P.y - a.y, d = Math.hypot(rx, ry);
    if (d > L && d > 1e-6) { P.x = a.x + rx / d * L; P.y = a.y + ry / d * L; d = L; }
    if (P.y > innerHeight - 10) { P.y = innerHeight - 10; P.py = Math.max(P.py, P.y); }

    var ux = 0, uy = 1;
    if (d > 1) { ux = (P.x - a.x) / d; uy = (P.y - a.y) / d; }
    var down = Math.atan2(uy, ux);
    face(st.sState === "climb" ? down + Math.PI : down);         // turns, never snaps
    st.x = P.x + ux * SILK_AT; st.y = P.y + uy * SILK_AT;
    drawWeb(a.x, a.y, P.x, P.y, L - d);

    if (st.sState === "climb") poseLegs(0.95, 0, false, now);
    else if (st.sState === "descend") poseLegs(0.35, 0.04, false, now);
    else poseLegs(0, 0.1, true, now);          // hanging: legs drawn in, twitching

    if (st.sState === "climb" && st.sLen <= 8) {
      st.sState = "chase";                     // back on the hand, hunting again
      st.sSpeed = 0;
      st.sNext = now + rnd(4000, 8000);
      web.style.display = "none";
    }
    place(1);
    return true;
  }

  /* ------------------------- per-creature movement ------------------------
     Returns whether the creature still needs frames. */
  function stepCreature(now, dt, idleFor) {
    var idle = idleFor > 1400;
    var keep = false;
    var px = st.x, py = st.y;

    if (st.form === "paws") {
      return !idle;                              // stamps happen in the handler

    } else if (st.form === "comet") {
      return stepComet(now, dt, idleFor);

    } else if (st.form === "wisps") {
      return stepWisps(now, dt, idleFor);

    } else if (st.form === "butterfly") {
      /* chaotic darts: frequent impulses, changing speed — and real lag:
         the further behind it is, the harder it flies to catch up, but it
         never teleports. Sometimes it forgets you and loops. */
      if (now > st.bNext) {
        st.bNext = now + rnd(160, 520);
        st.bGoal = [rnd(-52, 52), rnd(-44, 30)];
        st.bLerp = rnd(0.028, 0.085);
        if (R() < 0.09) st.bLoop = now + rnd(500, 900);   // a distracted loop
      }
      var gx, gy;
      if (now < st.bLoop) {                       // loop-the-loop
        var la = now * 0.012;
        gx = st.x + Math.cos(la) * 40 - 20;
        gy = st.y + Math.sin(la) * 40;
      } else {
        gx = st.tx + st.bGoal[0];
        gy = st.ty + st.bGoal[1] + Math.sin(now * 0.006) * 9;
      }
      var ddx = gx - st.x, ddy = gy - st.y;
      st.x += ddx * st.bLerp;
      st.y += ddy * st.bLerp;
      var vx = (st.x - px) / dt, vy = (st.y - py) / dt;
      var sp = Math.hypot(vx, vy);
      if (sp > 14) face(Math.atan2(vy, vx));
      /* flutter harder the faster it flies (only touch the style when the
         beat really changes: no style work on every frame) */
      var durMs = Math.round(Math.max(80, 195 - sp * 0.28) / 10) * 10;
      if (durMs !== st.wingMs) {
        st.wingMs = durMs;
        st.wings.forEach(function (w) { w.style.animationDuration = (durMs / 1000) + "s"; });
      }
      /* a little pollen of light off a quick flight */
      if (sp > 160 && R() < dt * 9) {
        spark(st.x, st.y, rnd(-20, 20), rnd(10, 40), rnd(0.6, 1.1), rnd(3, 5),
              R() < 0.5 ? GLOW.violet : GLOW.aura, 1.2);
      }
      place(1);
      return true;                                 // a butterfly rests only asleep

    } else if (st.form === "spider") {
      return (st.sState === "chase" || st.sState === "jump")
        ? spiderRun(now, dt, idle) : spiderSilk(now, dt);
    }
    return keep;
  }

  /* -------------------------------- the loop ------------------------------ */
  function tick(now) {
    st.raf = 0;
    if (!enabled()) return;
    now = now || performance.now();
    var dt = Math.min((now - (st.lastT || now)) / 1000 || 0.016, 0.05);
    st.lastT = now;
    var idleFor = now - st.lastMove;
    ctx.clearRect(0, 0, SKY_W, SKY_H);
    ctx.globalCompositeOperation = "lighter";
    var keep = stepCreature(now, dt, idleFor);
    var lit = stepSparks(dt);
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "source-over";
    /* doze: a hand this still for this long needs nothing from us. The
       last frame stays drawn (a resting comet, sleeping wisps); a
       hanging spider finishes its climb first. */
    var hanging = st.form === "spider"
      && (st.sState !== "chase" || st.sSpeed > 1);
    if (idleFor > SLEEP_MS && !hanging && !lit) {
      st.asleep = true;
      body.classList.add("resting");
      restFrame();
      return;
    }
    if (keep || lit) st.raf = requestAnimationFrame(tick);
  }

  /* the frame left on screen while it dozes: no tail frozen mid-flight,
     just a comet glowing low, or wisps dimmed to embers */
  function restFrame() {
    ctx.clearRect(0, 0, SKY_W, SKY_H);
    ctx.globalCompositeOperation = "lighter";
    if (st.form === "comet") {
      stampGlow(GLOW.star, comet.x, comet.y, 40, 0.16);
      stampGlow(GLOW.star, comet.x, comet.y, 12, 0.6);
    } else if (st.form === "wisps") {
      wisps.forEach(function (w) {
        stampGlow(w.img, w.x, w.y, w.size * 5, 0.07);
        stampGlow(w.img, w.x, w.y, w.size * 1.4, 0.35);
      });
    }
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "source-over";
  }

  function wake() {
    if (st.asleep) {
      st.asleep = false;
      body.classList.remove("resting");
      if (!spritesAlive()) repaintSprites();     // a GPU reset while it slept
    }
    if (enabled() && !st.raf) { st.lastT = 0; st.raf = requestAnimationFrame(tick); }
  }

  window.addEventListener("pointermove", function (ev) {
    st.tx = ev.clientX; st.ty = ev.clientY;
    st.lastMove = performance.now();
    if (enabled() && st.form === "paws") {
      st.dist += Math.hypot(ev.clientX - st.lastX, ev.clientY - st.lastY);
      if (st.dist > st.stride) {
        st.dist = 0;
        st.stride = rnd(36, 56);                   // stride varies, like a walk
        stamp(ev.clientX, ev.clientY,
              Math.atan2(ev.clientY - st.lastY, ev.clientX - st.lastX));
      }
    }
    st.lastX = ev.clientX; st.lastY = ev.clientY;
    wake();
  }, { passive: true });

  /* a click sheds starlight (feedback only: the layer never takes it) */
  window.addEventListener("pointerdown", function (ev) {
    if (!enabled() || ev.button !== 0) return;
    burst(ev.clientX, ev.clientY);
    if (st.form === "wisps") scatterWisps(ev.clientX, ev.clientY);
    st.lastMove = performance.now();
    wake();
  }, { passive: true });

  window.addEventListener("resize", function () { if (enabled()) sizeSky(); });

  /* ------------------------------- switches ------------------------------ */
  var btn = document.getElementById("familiar-toggle");
  if (btn) btn.addEventListener("click", function () { setEnabled(!enabled()); });

  if (window.Atlas) {
    var cmds = [
      { kind: "fam", label: "Familiar: On", run: function () { setEnabled(true); } },
      { kind: "fam", label: "Familiar: Off", run: function () { setEnabled(false); } },
      { kind: "fam", label: "Familiar: Cycle forms", run: function () { chooseForm("cycle"); } },
    ];
    ORDER.forEach(function (f) {
      cmds.push({ kind: "fam", label: "Familiar: " + LABELS[f],
                  run: function () { chooseForm(f); } });
    });
    window.Atlas.extraCommands = (window.Atlas.extraCommands || []).concat(cmds);
  }

  /* for the console and the doctor, like Nebula.debug(): what it is
     doing, and a way to pick a form ("cycle" to go back to cycling) */
  window.Familiar = {
    choose: function (name) { chooseForm(ORDER.indexOf(name) >= 0 ? name : "cycle"); },
    state: function () {
      return { enabled: enabled(), form: st.form, chosen: chosenForm(), asleep: st.asleep,
               spider: st.form === "spider"
                 ? { state: st.sState, speed: Math.round(st.sSpeed), silk: Math.round(st.sLen),
                     gait: Math.round(st.gait * 100) / 100 } : null,
               sparks: sparks.length };
    },
  };

  if (enabled()) sizeSky();
  applyEnabled();
  var first = chosenForm();
  setForm(first === "cycle" ? ORDER[Math.floor(R() * ORDER.length)] : first);
  scheduleMorph();
})();
