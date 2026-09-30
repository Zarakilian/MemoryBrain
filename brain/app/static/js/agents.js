// brain/app/static/js/agents.js
// Synapse page (v2.4): multi-AI analytics + the agent interaction network.
// Read-only. Pure canvas — no vendor deps (design rule: no build pipeline).
(function () {
  "use strict";
  const root = document.getElementById("syn-root");
  if (!root) return;

  const PROJECT = root.dataset.project || "";
  const DAYS = root.dataset.days || "90";

  // Fixed palette for the usual suspects; hashed hue for anyone else.
  const AGENT_COLORS = {
    claude: "#ff9d5c",
    grok:   "#8b93ec",
    codex:  "#4fd6c5",
    gemini: "#6ba7f5",
    copilot:"#c792ea",
    other:  "#97a0ba",
  };
  function agentColor(name) {
    if (AGENT_COLORS[name]) return AGENT_COLORS[name];
    let h = 0;
    for (const ch of String(name)) h = (h * 31 + ch.charCodeAt(0)) % 997;
    return `hsl(${h % 360} 60% 68%)`;
  }
  const esc = (s) => String(s).replace(/[&<>"]/g,
    (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
  const qs = (extra) => {
    const p = new URLSearchParams({ days: DAYS, ...extra });
    if (PROJECT) p.set("project", PROJECT);
    return p.toString();
  };
  const fmtAgo = (iso) => {
    if (!iso) return "never";
    const d = (Date.now() - Date.parse(iso)) / 864e5;
    if (d < 0.05) return "just now";
    if (d < 1) return Math.round(d * 24) + "h ago";
    return Math.round(d) + "d ago";
  };

  // ---------------------------------------------------------------- totals
  function renderTotals(stats) {
    const box = document.getElementById("agent-cards");
    const agents = stats.agents || [];
    if (!agents.length) {
      box.innerHTML = '<span class="hint">No agent activity in this window. ' +
        "Memories are attributed via their <code>source</code> field — have " +
        "each assistant pass its name as source when calling add_memory.</span>";
      return;
    }
    const max = Math.max(...agents.map((a) => a.memories + a.messages), 1);
    box.innerHTML = agents.map((a) => {
      const c = agentColor(a.agent);
      const share = Math.round(((a.memories + a.messages) / max) * 100);
      return `<div class="agent-card" style="--ac:${c}">
        <div class="aname"><span class="adot"></span>${esc(a.agent)}</div>
        <div class="anums">
          <span><b>${a.memories}</b>memories</span>
          <span><b>${a.messages}</b>messages</span>
          <span><b>${a.threads_opened}</b>threads</span>
        </div>
        <div class="ashare"><i style="width:${share}%"></i></div>
        <div class="alast">last active ${fmtAgo(a.last_active)}</div>
      </div>`;
    }).join("");
  }

  // ---------------------------------------------------------------- donuts
  function drawDonut(canvas, slices) {
    const dpr = window.devicePixelRatio || 1;
    const size = 130;
    canvas.width = size * dpr; canvas.height = size * dpr;
    canvas.style.width = size + "px"; canvas.style.height = size + "px";
    const ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);
    const cx = size / 2, cy = size / 2, r = size / 2 - 6, inner = r * 0.58;
    const total = slices.reduce((s, x) => s + x.value, 0) || 1;
    let a = -Math.PI / 2;
    for (const s of slices) {
      const sweep = (s.value / total) * Math.PI * 2;
      ctx.beginPath();
      ctx.arc(cx, cy, r, a, a + sweep);
      ctx.arc(cx, cy, inner, a + sweep, a, true);
      ctx.closePath();
      ctx.fillStyle = s.color;
      ctx.globalAlpha = 0.92;
      ctx.fill();
      a += sweep;
    }
    ctx.globalAlpha = 1;
    ctx.fillStyle = "#dce3f4";
    ctx.font = "600 13px system-ui, sans-serif";
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText(String(total), cx, cy);
  }

  function renderDonuts(stats) {
    const box = document.getElementById("donut-row");
    const projects = (stats.projects || []).filter((p) =>
      p.agents.some((a) => a.memories + a.messages > 0));
    if (!projects.length) {
      box.innerHTML = '<span class="hint">Nothing to chart yet.</span>';
      return;
    }
    box.innerHTML = "";
    for (const p of projects.slice(0, 12)) {
      const cell = document.createElement("div");
      cell.className = "donut-cell";
      const canvas = document.createElement("canvas");
      const slices = p.agents
        .map((a) => ({ label: a.agent, value: a.memories + a.messages,
                       color: agentColor(a.agent) }))
        .filter((s) => s.value > 0);
      const legend = slices.map((s) =>
        `<span style="--lc:${s.color}"><i></i>${esc(s.label)} ${s.value}</span>`
      ).join("");
      cell.innerHTML = `<div class="dname" title="${esc(p.project)}">${esc(p.project)}</div>`;
      cell.appendChild(canvas);
      cell.insertAdjacentHTML("beforeend", `<div class="donut-legend">${legend}</div>`);
      box.appendChild(cell);
      drawDonut(canvas, slices);
    }
  }

  // --------------------------------------------------------------- threads
  function renderThreads(data) {
    const box = document.getElementById("thread-list");
    const threads = data.threads || [];
    if (!threads.length) {
      box.innerHTML = '<span class="hint">No threads yet — the first ' +
        "<code>post_task</code> starts the conversation.</span>";
      return;
    }
    box.innerHTML = threads.slice(0, 12).map((t) => {
      const who = t.assigned_to
        ? `${esc(t.created_by)} → ${esc(t.assigned_to)}`
        : esc(t.created_by);
      return `<div class="thread-row">
        <span class="tkind">${esc(t.kind)}</span>
        <span class="ttitle" title="${esc(t.title)}">${esc(t.title)}</span>
        <span class="tagents">${who}</span>
        <span class="tstatus ${esc(t.status)}">${esc(t.status)}</span>
      </div>`;
    }).join("");
  }

  // ------------------------------------------------- synapse (neurons fire)
  function renderSynapse(net) {
    const wrap = document.getElementById("synapse-wrap");
    const canvas = document.getElementById("synapse");
    const empty = document.getElementById("synapse-empty");
    const nodes = net.nodes || [];
    const edges = net.edges || [];
    if (!nodes.length) { empty.hidden = false; return; }
    empty.hidden = true;

    const dpr = window.devicePixelRatio || 1;
    const ctx = canvas.getContext("2d");
    let W = 0, H = 0;
    function resize() {
      W = wrap.clientWidth; H = wrap.clientHeight;
      canvas.width = W * dpr; canvas.height = H * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      layout();
    }

    // Ring layout: few agents, so a circle reads best. Radius ∝ activity.
    const maxMsg = Math.max(...nodes.map((n) => n.messages), 1);
    const N = new Map();
    function layout() {
      const cx = W / 2, cy = H / 2;
      const ring = Math.min(W, H) / 2 - 70;
      nodes.forEach((n, i) => {
        const ang = (i / nodes.length) * Math.PI * 2 - Math.PI / 2;
        N.set(n.agent, {
          ...n,
          x: cx + Math.cos(ang) * (nodes.length === 1 ? 0 : ring),
          y: cy + Math.sin(ang) * (nodes.length === 1 ? 0 : ring),
          r: 12 + 14 * Math.sqrt(n.messages / maxMsg),
          color: agentColor(n.agent),
          phase: Math.random() * Math.PI * 2,
        });
      });
    }

    // Pulses: each edge fires at a rate proportional to its count.
    const maxCount = Math.max(...edges.map((e) => e.count), 1);
    const pulses = [];
    function spawn(now) {
      for (const e of edges) {
        const rate = 0.3 + 1.7 * (e.count / maxCount);   // fires per second
        if (Math.random() < rate / 60) {
          pulses.push({ e, t: 0, speed: 0.006 + Math.random() * 0.006 });
        }
      }
      while (pulses.length > 400) pulses.shift();
    }

    function curve(a, b) {
      // Slight arc so A→B and B→A don't overlap.
      const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
      const dx = b.x - a.x, dy = b.y - a.y;
      const len = Math.hypot(dx, dy) || 1;
      return { cx: mx - dy / len * 34, cy: my + dx / len * 34 };
    }
    function bez(a, c, b, t) {
      const u = 1 - t;
      return { x: u * u * a.x + 2 * u * t * c.cx + t * t * b.x,
               y: u * u * a.y + 2 * u * t * c.cy + t * t * b.y };
    }

    let raf;
    function frame(now) {
      ctx.clearRect(0, 0, W, H);
      // edges
      for (const e of edges) {
        const a = N.get(e.source), b = N.get(e.target);
        if (!a || !b) continue;
        const c = curve(a, b);
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.quadraticCurveTo(c.cx, c.cy, b.x, b.y);
        ctx.strokeStyle = "rgba(150,175,235,0.14)";
        ctx.lineWidth = 1 + 2.5 * (e.count / maxCount);
        ctx.stroke();
      }
      // pulses
      spawn(now);
      for (let i = pulses.length - 1; i >= 0; i--) {
        const p = pulses[i];
        p.t += p.speed;
        if (p.t >= 1) { pulses.splice(i, 1); continue; }
        const a = N.get(p.e.source), b = N.get(p.e.target);
        if (!a || !b) { pulses.splice(i, 1); continue; }
        const pos = bez(a, curve(a, b), b, p.t);
        const glow = Math.sin(p.t * Math.PI);
        ctx.beginPath();
        ctx.arc(pos.x, pos.y, 2.2 + glow * 1.6, 0, Math.PI * 2);
        ctx.fillStyle = a.color;
        ctx.shadowColor = a.color;
        ctx.shadowBlur = 12 * glow;
        ctx.fill();
        ctx.shadowBlur = 0;
      }
      // nodes (soma + breathing halo)
      const t = now / 1000;
      for (const n of N.values()) {
        const breathe = 1 + 0.08 * Math.sin(t * 1.4 + n.phase);
        const grad = ctx.createRadialGradient(n.x, n.y, 0, n.x, n.y, n.r * 2.4 * breathe);
        grad.addColorStop(0, n.color + "55");
        grad.addColorStop(1, "transparent");
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r * 2.4 * breathe, 0, Math.PI * 2);
        ctx.fillStyle = grad;
        ctx.fill();
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r * breathe, 0, Math.PI * 2);
        ctx.fillStyle = n.color;
        ctx.fill();
        ctx.font = "600 13px system-ui, sans-serif";
        ctx.textAlign = "center";
        ctx.fillStyle = "#dce3f4";
        ctx.fillText(n.agent, n.x, n.y + n.r * breathe + 18);
        ctx.font = "400 10px ui-monospace, monospace";
        ctx.fillStyle = "#97a0ba";
        ctx.fillText(n.messages + " msg", n.x, n.y + n.r * breathe + 32);
      }
      raf = requestAnimationFrame(frame);
    }

    resize();
    window.addEventListener("resize", resize);
    raf = requestAnimationFrame(frame);
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) cancelAnimationFrame(raf);
      else raf = requestAnimationFrame(frame);
    });
  }

  // ------------------------------------------------------------------ boot
  async function boot() {
    try {
      const [stats, net, threads] = await Promise.all([
        fetch("/api/ui/agents/stats?" + qs({})).then((r) => r.json()),
        fetch("/api/ui/agents/network?" + qs({})).then((r) => r.json()),
        fetch("/api/ui/agents/threads?" + qs({ limit: 20 })).then((r) => r.json()),
      ]);
      renderTotals(stats);
      renderDonuts(stats);
      renderThreads(threads);
      renderSynapse(net);
    } catch (err) {
      document.getElementById("agent-cards").innerHTML =
        '<span class="hint">Failed to load agent analytics — is the brain healthy? (/ui/doctor)</span>';
      console.error("synapse:", err);
    }
  }
  boot();
})();
