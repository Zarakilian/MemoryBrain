/* The Familiar — a small shadow companion that follows the cursor across
   the Atlas, Marauder's-Map style. It cycles between forms every few
   seconds, and each creature moves like itself:

     footprints — stamped along your path, stride varies
     bird       — swoops with banked turns, hovers with a bob when you stop
     moth       — darts erratically, drawn to the pointer like a lamp
     cat        — stalks in bursts: waits, trots when you pull away,
                  sits and flicks its tail when you stay still

   It has its own switch (rail button + palette, remembered), independent
   of the codex ambience. Same safety rails as everything ambient here:
   pointer-events:none !important layer, one pointermove listener, a
   self-stopping rAF loop, transforms only, absent under reduced motion. */
"use strict";

(function () {
  function reduced() {
    return window.matchMedia
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }
  if (reduced()) return;

  var STORE = "atlas-familiar";
  function enabled() {
    try { return localStorage.getItem(STORE) !== "off"; } catch (e) { return true; }
  }

  var R = Math.random;
  function rnd(a, b) { return a + R() * (b - a); }

  /* ------------------------------ the forms ------------------------------ */
  var SVG_OPEN = '<svg viewBox="0 0 60 60" width="46" height="46" fill="currentColor" stroke="none">';

  var FORMS = {
    bird: {
      offset: [26, -34],
      svg: SVG_OPEN
        + '<path d="M14 32 C20 26 30 24 38 27 C43 29 50 28 55 24 C51 33 43 37 34 36 C26 35 18 36 12 33 L4 38 L8 32 L4 27 L12 31 Z"/>'
        + '<path class="fam-wing" d="M28 28 C24 14 36 6 47 9 C39 13 34 20 32 28 Z"/>'
        + "</svg>",
    },
    moth: {
      offset: [10, -14],
      svg: SVG_OPEN
        + '<ellipse cx="30" cy="32" rx="3.4" ry="11"/>'
        + '<path d="M30 24 Q26 14 22 12" fill="none" stroke="currentColor" stroke-width="1.6"/>'
        + '<path d="M30 24 Q34 14 38 12" fill="none" stroke="currentColor" stroke-width="1.6"/>'
        + '<g class="fam-wing-l"><ellipse cx="18" cy="27" rx="12" ry="8" transform="rotate(-24 18 27)"/>'
        + '<ellipse cx="20" cy="39" rx="9" ry="6" transform="rotate(18 20 39)"/></g>'
        + '<g class="fam-wing-r"><ellipse cx="42" cy="27" rx="12" ry="8" transform="rotate(24 42 27)"/>'
        + '<ellipse cx="40" cy="39" rx="9" ry="6" transform="rotate(-18 40 39)"/></g>'
        + "</svg>",
    },
    cat: {
      offset: [-46, -18],
      svg: SVG_OPEN
        + '<path d="M8 42 C10 34 18 30 26 31 L40 32 C46 26 45 20 43 16 L47 10 L49 17 L54 15 L52 22 C54 27 51 33 45 35 L44 42 L40 42 L39 36 L22 36 L20 42 L16 42 L15 37 C11 38 9 40 8 42 Z"/>'
        + '<path d="M8 42 C2 38 2 30 7 27" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"/>'
        + "</svg>",
      svgSit: SVG_OPEN
        + '<path d="M30 12 L26 4 L31 9 L36 4 L34 12 C40 14 42 20 40 26 C46 32 48 42 44 50 L16 50 C14 42 17 34 24 30 C21 24 23 15 30 12 Z"/>'
        + '<path class="fam-tail" d="M16 50 C8 48 6 40 11 36" fill="none" stroke="currentColor" stroke-width="2.8" stroke-linecap="round"/>'
        + "</svg>",
    },
    paws: { prints: true },
  };
  var ORDER = ["paws", "bird", "moth", "cat"];

  var PRINT_SVG = '<svg viewBox="0 0 20 34" width="13" height="22" fill="currentColor">'
    + '<path d="M10 1 C15 1 17 6 16 13 C15.3 18 14 20.5 12.6 21.5 L7.4 21.5 C6 20.5 4.7 18 4 13 C3 6 5 1 10 1 Z"/>'
    + '<ellipse cx="10" cy="28.5" rx="4.6" ry="4"/></svg>';

  /* ------------------------------ the layer ------------------------------ */
  var layer = document.createElement("div");
  layer.id = "familiar-layer";
  layer.setAttribute("aria-hidden", "true");
  var body = document.createElement("div");
  body.className = "fam";
  layer.appendChild(body);
  document.body.appendChild(layer);

  var st = {
    form: null, raf: 0,
    x: innerWidth / 2, y: innerHeight / 2,
    tx: innerWidth / 2, ty: innerHeight / 2,
    lastMove: 0, lastX: 0, lastY: 0, dist: 0, stride: 44,
    facing: 1, bank: 0, sitting: false,
    // moth impulses
    mothGoal: [0, 0], mothLerp: 0.16, mothNext: 0,
    // cat stalking
    catState: "wait",
    printFlip: false, prints: [], t: 0,
  };

  function applyEnabled() {
    layer.style.display = enabled() ? "" : "none";
    var b = document.getElementById("familiar-toggle");
    if (b) b.textContent = enabled() ? "❋ familiar on" : "❋ familiar off";
  }
  function setEnabled(on) {
    try { localStorage.setItem(STORE, on ? "on" : "off"); } catch (e) {}
    applyEnabled();
    if (on && !st.raf) st.raf = requestAnimationFrame(tick);
  }

  function setForm(name) {
    st.form = name;
    st.sitting = false;
    body.classList.remove("walking", "hovering");
    var f = FORMS[name];
    body.classList.add("morph");
    setTimeout(function () {
      body.innerHTML = f.svg || "";
      body.classList.toggle("hidden-form", !!f.prints);
      body.classList.remove("morph");
    }, 180);
  }

  function scheduleMorph() {
    setTimeout(function () {
      var i = (ORDER.indexOf(st.form) + 1) % ORDER.length;
      setForm(ORDER[i]);
      scheduleMorph();
    }, rnd(7000, 14000));
  }

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

  /* ------------------------- per-creature movement ------------------------ */
  function tick(now) {
    st.raf = 0;
    if (!enabled()) return;
    st.t = now || performance.now();
    var f = FORMS[st.form] || {};
    var idleFor = performance.now() - st.lastMove;
    var idle = idleFor > 1400;
    var keep = false;

    if (f.prints) {
      if (!idle) keep = true;                       // stamps happen in the handler
    } else {
      var goalX, goalY, k;

      if (st.form === "bird") {
        // swooping pursuit with banked turns; lazy hover-bob circle when idle
        if (idle) {
          goalX = st.tx + f.offset[0] * st.facing + Math.cos(st.t * 0.0011) * 16;
          goalY = st.ty + f.offset[1] + Math.sin(st.t * 0.0017) * 8;
          k = 0.04;
          body.classList.add("hovering");
        } else {
          goalX = st.tx + f.offset[0] * st.facing;
          goalY = st.ty + f.offset[1] + Math.sin(st.t * 0.004) * 11;   // swoop
          k = 0.09;
          body.classList.remove("hovering");
        }
      } else if (st.form === "moth") {
        // erratic darts: new impulse every 250–650ms, speed changes each time
        if (st.t > st.mothNext) {
          st.mothNext = st.t + rnd(250, 650);
          st.mothGoal = [rnd(-38, 38), rnd(-30, 22)];
          st.mothLerp = rnd(0.1, 0.3);
        }
        goalX = st.tx + f.offset[0] + st.mothGoal[0];
        goalY = st.ty + f.offset[1] + st.mothGoal[1];
        k = st.mothLerp;
        keep = true;                                 // a moth never quite rests
      } else {                                       // cat: stalk in bursts
        goalX = st.tx + f.offset[0] * st.facing;
        goalY = st.ty + f.offset[1];
        var gap = Math.hypot(goalX - st.x, goalY - st.y);
        if (st.catState === "wait" && gap > 150) st.catState = "trot";
        if (st.catState === "trot" && gap < 60) st.catState = "wait";
        k = st.catState === "trot" ? 0.085 : 0.015;
        body.classList.toggle("walking", st.catState === "trot" && !idle);
        if (idle !== st.sitting) {
          st.sitting = idle;
          body.innerHTML = idle ? FORMS.cat.svgSit : FORMS.cat.svg;
          body.classList.remove("walking");
        }
      }

      var dx = goalX - st.x, dy = goalY - st.y;
      st.x += dx * k;
      st.y += dy * k;
      if (Math.abs(dx) > 12) st.facing = dx < 0 ? -1 : 1;

      // bird banks into vertical motion
      var bankTarget = st.form === "bird" ? Math.max(-22, Math.min(22, dy * k * 14)) : 0;
      st.bank += (bankTarget - st.bank) * 0.15;

      body.style.transform = "translate3d(" + st.x.toFixed(1) + "px," + st.y.toFixed(1)
        + "px,0) translate(-50%,-50%) scaleX(" + (st.facing < 0 ? -1 : 1)
        + ") rotate(" + st.bank.toFixed(1) + "deg)";

      if (Math.abs(dx) > 0.4 || Math.abs(dy) > 0.4 || Math.abs(st.bank) > 0.5
          || (st.form === "bird" && !idle)) keep = true;
      if (st.form === "bird" && idle) keep = true;   // hover circle keeps breathing
    }

    if (keep) st.raf = requestAnimationFrame(tick);
  }

  window.addEventListener("pointermove", function (ev) {
    st.tx = ev.clientX; st.ty = ev.clientY;
    st.lastMove = performance.now();
    var f = FORMS[st.form] || {};
    if (enabled() && f.prints) {
      st.dist += Math.hypot(ev.clientX - st.lastX, ev.clientY - st.lastY);
      if (st.dist > st.stride) {
        st.dist = 0;
        st.stride = rnd(36, 56);                     // stride varies, like a walk
        stamp(ev.clientX, ev.clientY,
              Math.atan2(ev.clientY - st.lastY, ev.clientX - st.lastX));
      }
    }
    st.lastX = ev.clientX; st.lastY = ev.clientY;
    if (enabled() && !st.raf) st.raf = requestAnimationFrame(tick);
  }, { passive: true });

  /* ------------------------------- switches ------------------------------ */
  var btn = document.getElementById("familiar-toggle");
  if (btn) btn.addEventListener("click", function () { setEnabled(!enabled()); });

  if (window.Atlas) {
    window.Atlas.extraCommands = (window.Atlas.extraCommands || []).concat([
      { kind: "fam", label: "Familiar: On", run: function () { setEnabled(true); } },
      { kind: "fam", label: "Familiar: Off", run: function () { setEnabled(false); } },
    ]);
  }

  applyEnabled();
  setForm(ORDER[Math.floor(R() * ORDER.length)]);
  scheduleMorph();
})();
