/* The Familiar — a small companion of starlight that follows the cursor
   through the Nebula. It cycles between forms every so often, each with
   its own movement personality — and every creature FACES where it is
   going, and chases with real lag, so it feels like pursuit, not glue:

     footprints — stamped along your path, stride varies
     butterfly  — chaotic darts and drifting loops; flutters harder the
                  faster it flies; trails well behind a quick hand
     spider     — scuttles after the pointer in bursts, sometimes jumps,
                  and sometimes fires a silk line AT the cursor and hangs
                  BELOW it, swinging like a damped pendulum while you
                  move, then climbs back up the thread and resumes

   It has its own switch (rail button + palette, remembered), independent
   of the world's ambience. Same safety rails as everything ambient here:
   pointer-events:none !important layer, one pointermove listener, one
   rAF loop (self-stopping when nothing needs to move), transforms only,
   absent under reduced motion. */
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

  /* ------------------------------ the forms ------------------------------
     All creatures are drawn FACING RIGHT (+x) so one heading rotation
     orients them along their motion. */
  var SVG_OPEN = '<svg viewBox="0 0 60 60" width="46" height="46" fill="currentColor" stroke="none">';

  /* the butterfly the owner liked — the original form, unchanged; it is
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

  var SPIDER_SVG = SVG_OPEN
    // abdomen left, head right — running along +x
    + '<ellipse cx="24" cy="30" rx="8.5" ry="6.5"/>'
    + '<circle cx="35" cy="30" r="4.6"/>'
    + '<circle cx="37.5" cy="28.4" r="1" fill="rgba(8,12,24,.6)"/>'
    + '<circle cx="37.5" cy="31.6" r="1" fill="rgba(8,12,24,.6)"/>'
    + '<g class="fam-legs-a" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round">'
    + '<path d="M30 26 Q36 14 46 10"/><path d="M28 25 Q28 12 20 6"/>'
    + '<path d="M30 34 Q36 46 46 50"/><path d="M28 35 Q28 48 20 54"/>'
    + "</g>"
    + '<g class="fam-legs-b" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round">'
    + '<path d="M33 27 Q42 18 52 16"/><path d="M25 25 Q18 14 8 12"/>'
    + '<path d="M33 33 Q42 42 52 44"/><path d="M25 35 Q18 46 8 48"/>'
    + "</g>"
    + "</svg>";

  var ORDER = ["paws", "butterfly", "spider"];

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
  var web = document.createElement("div");        // the spider's silk line
  web.className = "fam-web";
  web.style.display = "none";
  layer.appendChild(web);
  document.body.appendChild(layer);

  var st = {
    form: null, raf: 0,
    x: innerWidth / 2, y: innerHeight / 2,
    tx: innerWidth / 2, ty: innerHeight / 2,
    vx: 0, vy: 0,                       // measured velocity (for facing)
    heading: 0, headingOff: 0,          // smoothed facing, per-form offset
    lastMove: 0, lastX: 0, lastY: 0, dist: 0, stride: 44,
    lastT: 0,
    // butterfly impulses
    bGoal: [0, 0], bLerp: 0.05, bNext: 0, bLoop: 0,
    // spider
    sState: "chase",                    // chase | jump | hang | climb
    sNext: 0, sJumpT: 0, sTheta: 0, sOmega: 0, sLen: 0, sLenT: 0,
    sAnchorVX: 0, sPrevTX: 0,
    printFlip: false, prints: [],
  };

  function applyEnabled() {
    layer.style.display = enabled() ? "" : "none";
    var b = document.getElementById("familiar-toggle");
    if (b) b.textContent = enabled() ? "❋ familiar on" : "❋ familiar off";
  }
  function setEnabled(on) {
    try { localStorage.setItem(STORE, on ? "on" : "off"); } catch (e) {}
    applyEnabled();
    if (on) wake();
  }

  function clearForm() {
    body.innerHTML = "";
    body.className = "fam";
    body.style.transform = "";
    web.style.display = "none";
  }

  function setForm(name) {
    st.form = name;
    body.classList.add("morph");
    setTimeout(function () {
      clearForm();
      body.classList.add("morph");
      if (name === "butterfly") {
        body.innerHTML = BUTTERFLY_SVG;
        st.headingOff = Math.PI / 2;           // drawn head-up; 0 rad = right
        st.heading = -Math.PI / 2;             // start upright, no first-frame spin
        st.x = st.tx + 40; st.y = st.ty + 40;
      } else if (name === "spider") {
        body.innerHTML = SPIDER_SVG;
        st.headingOff = 0;                     // drawn head-right
        st.sState = "chase";
        st.sNext = performance.now() + rnd(2500, 5000);
        st.x = st.tx - 120; st.y = st.ty + 80;
      } else {
        body.classList.add("hidden-form");     // paws: prints only
      }
      requestAnimationFrame(function () { body.classList.remove("morph"); });
      wake();
    }, 180);
  }

  function scheduleMorph() {
    setTimeout(function () {
      var i = (ORDER.indexOf(st.form) + 1) % ORDER.length;
      setForm(ORDER[i]);
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
  function drawWeb(x1, y1, x2, y2) {
    var dx = x2 - x1, dy = y2 - y1, len = Math.hypot(dx, dy);
    web.style.display = "";
    web.style.left = x1 + "px";
    web.style.top = y1 + "px";
    web.style.width = len + "px";
    web.style.transform = "rotate(" + Math.atan2(dy, dx) + "rad)";
  }

  /* ------------------------- per-creature movement ------------------------ */
  function tick(now) {
    st.raf = 0;
    if (!enabled()) return;
    now = now || performance.now();
    var dt = Math.min((now - (st.lastT || now)) / 1000 || 0.016, 0.05);
    st.lastT = now;
    var idleFor = now - st.lastMove;
    var idle = idleFor > 1400;
    var keep = false;
    var px = st.x, py = st.y;

    if (st.form === "paws") {
      if (!idle) keep = true;                    // stamps happen in the handler

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
      keep = true;                                 // a butterfly never rests

    } else if (st.form === "spider") {
      var S = st.sState;
      if (S === "chase" || S === "jump") {
        /* scuttle in bursts with lag; face the run */
        var gap = Math.hypot(st.tx - st.x, st.ty - st.y);
        var k = gap > 220 ? 0.075 : gap > 60 ? 0.05 : 0.012;
        st.x += (st.tx - st.x) * k;
        st.y += (st.ty - st.y) * k;
        body.classList.toggle("scuttle", gap > 26 && !idle);

        var jumpScale = 1, jumpLift = 0;
        if (S === "jump") {
          var jt = (now - st.sJumpT) / 380;        // 0..1
          if (jt >= 1) { st.sState = "chase"; }
          else {
            var arc = 4 * jt * (1 - jt);           // parabola
            jumpLift = arc * 18;                   // a pounce, not a launch
            jumpScale = 1 + arc * 0.16;
          }
        } else if (now > st.sNext) {
          if (gap < 620 && R() < 0.6) {
            /* fire silk AT the cursor, then DROP below it: the thread
               unspools (sLen grows toward sLenT) so the fall reads as a
               fall — downward, along the tether */
            st.sState = "hang";
            st.sLenT = Math.min(Math.max(gap * 0.9, 180), 340);   // a proper drop
            st.sLen = Math.min(26, st.sLenT);      // starts at the hand…
            var ang = Math.atan2(st.x - st.tx, st.y - st.ty);  // from straight-down
            st.sTheta = Math.max(-0.9, Math.min(0.9, ang));
            st.sOmega = 0;
            st.sAnchorVX = 0;
            st.sPrevTX = st.tx;
            st.sNext = now + rnd(3500, 6000);      // it HANGS a while
          } else if (R() < 0.5) {
            st.sState = "jump";
            st.sJumpT = now;
            st.sNext = now + rnd(2800, 6500);
          } else {
            st.sNext = now + rnd(1800, 4000);      // bide, keep stalking
          }
        }
        st.y -= jumpLift;
        keep = true;
        // face the run
        var mvx = st.x - px, mvy = (st.y - py);
        if (Math.hypot(mvx, mvy) > 0.35) face(Math.atan2(mvy, mvx));
        place(jumpScale);
        st.vx = mvx / dt; st.vy = mvy / dt;
        if (keep) st.raf = requestAnimationFrame(tick);
        return;

      } else if (S === "hang") {
        /* a real pendulum from the LIVE cursor: moving the mouse whips
           the anchor and the spider swings; damped, weighty. θ = 0 is
           STRAIGHT DOWN (screen +y) and the swing is clamped well short
           of horizontal — the spider always hangs beneath the hand. */
        st.sLen += (st.sLenT - st.sLen) * Math.min(1, 3.2 * dt);  // unspool: the drop
        /* never dangle off the bottom of the screen */
        var maxL = Math.max(50, innerHeight - st.ty - 30);
        var L = Math.min(st.sLen, maxL);
        var anchorVX = (st.tx - st.sPrevTX) / dt;
        var dvx = anchorVX - st.sAnchorVX;
        st.sAnchorVX = anchorVX;
        st.sPrevTX = st.tx;
        var g = 2600;
        st.sOmega += (-(g / L) * Math.sin(st.sTheta)) * dt
                   - (dvx / L) * Math.cos(st.sTheta) * 0.55
                   - st.sOmega * 1.6 * dt;
        st.sTheta += st.sOmega * dt;
        if (st.sTheta > 1.25) { st.sTheta = 1.25; st.sOmega = Math.min(st.sOmega, 0); }
        if (st.sTheta < -1.25) { st.sTheta = -1.25; st.sOmega = Math.max(st.sOmega, 0); }
        st.x = st.tx + Math.sin(st.sTheta) * L;
        st.y = st.ty + Math.cos(st.sTheta) * L;   // +y: BELOW the cursor
        drawWeb(st.tx, st.ty, st.x, st.y);
        body.classList.remove("scuttle");
        /* dangle head-down: face away from the thread */
        face(Math.atan2(st.y - st.ty, st.x - st.tx));
        place(1);
        if (now > st.sNext) { st.sState = "climb"; }
        st.raf = requestAnimationFrame(tick);
        return;

      } else if (S === "climb") {
        /* SLOW, hand over hand, back up the silk — then resume the chase */
        st.sLen -= 85 * dt;
        st.sOmega *= (1 - 1.8 * dt);
        st.sTheta += st.sOmega * dt;
        if (st.sLen <= 14) {
          st.sState = "chase";
          st.sNext = now + rnd(3200, 7000);
          web.style.display = "none";
          body.classList.remove("scuttle");
        } else {
          st.x = st.tx + Math.sin(st.sTheta) * st.sLen;
          st.y = st.ty + Math.cos(st.sTheta) * st.sLen;
          drawWeb(st.tx, st.ty, st.x, st.y);
          body.classList.add("scuttle");   // little legs working the thread
          /* climbing: head UP the thread, toward the cursor */
          face(Math.atan2(st.ty - st.y, st.tx - st.x));
          place(1);
          st.raf = requestAnimationFrame(tick);
          return;
        }
      }
    }

    /* shared facing + place for butterfly (and any straggler) */
    if (st.form === "butterfly") {
      var vx = (st.x - px) / dt, vy = (st.y - py) / dt;
      var sp = Math.hypot(vx, vy);
      if (sp > 14) face(Math.atan2(vy, vx));
      /* flutter harder the faster it flies */
      var wings = body.querySelectorAll(".fam-wing-l, .fam-wing-r");
      var durMs = Math.max(80, 195 - sp * 0.28);
      wings.forEach(function (w) { w.style.animationDuration = (durMs / 1000) + "s"; });
      place(1);
    }

    if (keep) st.raf = requestAnimationFrame(tick);
  }

  function wake() {
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
