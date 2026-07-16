/* MemoryBrain — ambient life layer (all pages except the Night Folio).
   Gold dust drifting through library light, pointer-parallax marginalia,
   and pointer-tracked card tilt. Vanilla JS, ~zero cost, reduced-motion aware. */
(function () {
  "use strict";
  if (document.getElementById("graph3d")) return;   // 3D page has its own life
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------------- gold dust ---------------- */
  const canvas = document.getElementById("ambient");
  if (canvas && !reduced) {
    const ctx = canvas.getContext("2d");
    let w = 0, h = 0, motes = [];
    function resize() {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = window.innerWidth; h = window.innerHeight;
      canvas.width = w * dpr; canvas.height = h * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const n = Math.floor((w * h) / 26000);        // density scales with window
      motes = Array.from({ length: n }, () => ({
        x: Math.random() * w, y: Math.random() * h,
        r: 0.6 + Math.random() * 1.7,
        vx: (Math.random() - 0.5) * 0.14, vy: -0.06 - Math.random() * 0.16,
        tw: Math.random() * Math.PI * 2,
      }));
    }
    window.addEventListener("resize", resize);
    resize();
    (function tick(now) {
      ctx.clearRect(0, 0, w, h);
      for (const m of motes) {
        m.x += m.vx; m.y += m.vy;
        if (m.y < -4) { m.y = h + 4; m.x = Math.random() * w; }
        if (m.x < -4) m.x = w + 4; else if (m.x > w + 4) m.x = -4;
        const a = 0.10 + 0.10 * Math.sin(now / 900 + m.tw);
        ctx.fillStyle = `rgba(165,118,43,${a.toFixed(3)})`;
        ctx.beginPath();
        ctx.arc(m.x, m.y, m.r, 0, Math.PI * 2);
        ctx.fill();
      }
      requestAnimationFrame(tick);
    })(0);
  }

  /* ------------- marginalia parallax ------------- */
  const plates = [...document.querySelectorAll(".marginalia")];
  if (plates.length && !reduced) {
    let tx = 0, ty = 0, cx = 0, cy = 0;
    window.addEventListener("pointermove", (e) => {
      tx = (e.clientX / window.innerWidth - 0.5);
      ty = (e.clientY / window.innerHeight - 0.5);
    }, { passive: true });
    (function drift() {
      cx += (tx - cx) * 0.04; cy += (ty - cy) * 0.04;
      plates.forEach((p, i) => {
        const depth = 10 + i * 8;
        p.style.transform = `translate3d(${(-cx * depth).toFixed(2)}px, ${(-cy * depth).toFixed(2)}px, 0)`;
      });
      requestAnimationFrame(drift);
    })();
  }

  /* ------------- card tilt (feels alive under the hand) ------------- */
  if (!reduced) {
    document.querySelectorAll(".proj-card, .stat").forEach((el) => {
      el.addEventListener("pointermove", (e) => {
        const r = el.getBoundingClientRect();
        const px = (e.clientX - r.left) / r.width - 0.5;
        const py = (e.clientY - r.top) / r.height - 0.5;
        el.style.setProperty("--ry", (px * 5).toFixed(2) + "deg");
        el.style.setProperty("--rx", (-py * 5).toFixed(2) + "deg");
      });
      el.addEventListener("pointerleave", () => {
        el.style.setProperty("--rx", "0deg");
        el.style.setProperty("--ry", "0deg");
      });
    });
  }
})();
