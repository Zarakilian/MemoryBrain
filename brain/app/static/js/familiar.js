/* The Familiar — a small shadow companion that follows the cursor across
   the Atlas, Marauder's-Map style. It cycles between forms every few
   seconds: footprints padding after the pointer, then a shadow bird, then
   a moth, then a cat that sits when you stop moving.

   Same safety rails as the codex room: the layer is pointer-events:none
   !important (it can never eat a click), one pointermove listener, a
   self-stopping rAF loop, all motion via transforms, disabled entirely
   under prefers-reduced-motion, and governed by the same ambience toggle. */
"use strict";

(function () {
  function reduced() {
    return window.matchMedia
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }
  function enabled() {
    try { return localStorage.getItem("atlas-ambience") !== "off"; } catch (e) { return true; }
  }
  if (reduced()) return;

  var R = Math.random;
  function rnd(a, b) { return a + R() * (b - a); }

  /* ------------------------------ the forms ------------------------------ */
  var SVG_OPEN = '<svg viewBox="0 0 60 60" width="46" height="46" fill="currentColor" stroke="none">';

  var FORMS = {
    bird: {
      offset: [26, -34], lerp: 0.085, bob: true,
      svg: SVG_OPEN
        + '<path d="M14 32 C20 26 30 24 38 27 C43 29 50 28 55 24 C51 33 43 37 34 36 C26 35 18 36 12 33 L4 38 L8 32 L4 27 L12 31 Z"/>'
        + '<path class="fam-wing" d="M28 28 C24 14 36 6 47 9 C39 13 34 20 32 28 Z"/>'
        + "</svg>",
    },
    moth: {
      offset: [10, -14], lerp: 0.16, orbit: true,
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
      offset: [-42, -18], lerp: 0.055, sits: true,
      svg: SVG_OPEN
        + '<path class="fam-cat-walk" d="M8 42 C10 34 18 30 26 31 L40 32 C46 26 45 20 43 16 L47 10 L49 17 L54 15 L52 22 C54 27 51 33 45 35 L44 42 L40 42 L39 36 L22 36 L20 42 L16 42 L15 37 C11 38 9 40 8 42 Z"/>'
        + '<path d="M8 42 C2 38 2 30 7 27" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"/>'
        + "</svg>",
      svgSit: SVG_OPEN
        + '<path d="M30 12 L26 4 L31 9 L36 4 L34 12 C40 14 42 20 40 26 C46 32 48 42 44 50 L16 50 C14 42 17 34 24 30 C21 24 23 15 30 12 Z"/>'
        + '<path d="M16 50 C8 48 6 40 11 36" fill="none" stroke="currentColor" stroke-width="2.8" stroke-linecap="round"/>'
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
    form: null, running: false, raf: 0,
    x: innerWidth / 2, y: innerHeight / 2,      // creature position
    tx: innerWidth / 2, ty: innerHeight / 2,    // cursor
    lastMove: 0, lastX: 0, lastY: 0, dist: 0,
    facing: 1, orbitA: 0, sitting: false,
    prints: [], printFlip: false, morphT: 0,
  };

  function setForm(name) {
    st.form = name;
    st.sitting = false;
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
      if (enabled()) setForm(ORDER[i]);
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

  function tick(t) {
    st.raf = 0;
    if (!enabled()) { body.classList.add("hidden-form"); return; }
    var f = FORMS[st.form] || {};
    var idle = performance.now() - st.lastMove > 1400;

    if (f.prints) {
      // footprints: stamped along the cursor's path in the move handler
      if (!idle) st.raf = requestAnimationFrame(tick);
      return;
    }

    var goalX = st.tx + (f.offset ? f.offset[0] * st.facing : 0);
    var goalY = st.ty + (f.offset ? f.offset[1] : 0);
    if (f.orbit) {
      st.orbitA += 0.045;
      goalX += Math.cos(st.orbitA) * 24;
      goalY += Math.sin(st.orbitA * 1.7) * 14;
    }
    var k = f.lerp || 0.08;
    var dx = goalX - st.x, dy = goalY - st.y;
    st.x += dx * k;
    st.y += dy * k;
    if (Math.abs(dx) > 12) st.facing = dx < 0 ? -1 : 1;

    if (f.sits) {
      var shouldSit = idle;
      if (shouldSit !== st.sitting) {
        st.sitting = shouldSit;
        body.innerHTML = shouldSit ? FORMS.cat.svgSit : FORMS.cat.svg;
      }
    }

    body.style.transform = "translate3d(" + st.x.toFixed(1) + "px," + st.y.toFixed(1)
      + "px,0) translate(-50%,-50%) scaleX(" + (st.facing < 0 ? -1 : 1) + ")";

    var settled = Math.abs(dx) < 0.4 && Math.abs(dy) < 0.4 && (idle || f.orbit === undefined);
    if (!settled || f.orbit || (f.bob && !idle)) st.raf = requestAnimationFrame(tick);
    else if (f.orbit) st.raf = requestAnimationFrame(tick);
  }

  window.addEventListener("pointermove", function (ev) {
    st.tx = ev.clientX; st.ty = ev.clientY;
    st.lastMove = performance.now();
    var f = FORMS[st.form] || {};
    if (f.prints) {
      st.dist += Math.hypot(ev.clientX - st.lastX, ev.clientY - st.lastY);
      if (st.dist > 42) {
        st.dist = 0;
        stamp(ev.clientX, ev.clientY,
              Math.atan2(ev.clientY - st.lastY, ev.clientX - st.lastX));
      }
    }
    st.lastX = ev.clientX; st.lastY = ev.clientY;
    if (!st.raf) st.raf = requestAnimationFrame(tick);
  }, { passive: true });

  document.addEventListener("atlas:ambience", function (ev) {
    var on = !ev.detail || ev.detail.on !== false;
    layer.style.display = on ? "" : "none";
  });

  setForm(ORDER[Math.floor(R() * ORDER.length)]);
  scheduleMorph();
})();
