/* The Nebula — one living world.
   A single full-screen three.js scene owns everything behind the glass:
   deep space, two breathing gas clouds, cursor-parallax star dust, and the
   memories themselves as luminous stars joined by synaptic filaments under
   a real 3D force layout (d3-force-3d). The DOM shell floats above as
   translucent panes; this module never touches it.

   Design rules honoured here (this repo's scar tissue):
   - ONE three instance (vendored r170 module) owns the ENTIRE scene.
     Nothing from any other three build may enter it.  (rule 5)
   - Node legibility beats mood: cores are unlit MeshBasicMaterial at full
     opacity; glow is additive and behind them, never over them.  (rule 2)
   - Two motion speeds: ambient drift (60 s+ loops) and feedback (~150 ms
     eases). Nothing in between.  (rule 4)
   - prefers-reduced-motion: no animation loop at all — the world renders
     stills on demand and everything still works.  (rule 7)
   - The canvas sits under the shell; interactivity is granted by CSS
     (body[data-lens="constellation"] #world { pointer-events:auto }).
     No ambient element can ever eat a click.  (rule 6)
   - One pointermove listener; lerp work happens inside the one rAF. (rule 8)
*/
import * as THREE from "three";
import { OrbitControls } from "orbitcontrols";

var reduced = window.matchMedia
  && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function ambienceOn() {
  try { return localStorage.getItem("nebula-ambience") !== "off"; }
  catch (e) { return true; }
}

/* ------------------------------------------------------------ colours */
var HUES = [28, 82, 140, 190, 215, 255, 285, 320, 350, 45, 165, 5];
function hueOf(slug) {
  var h = 0, s = String(slug || "");
  for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 997;
  return HUES[h % 12];
}
function nodeColor(n, out) {
  return out.setHSL(hueOf(n.project) / 360, 0.7, 0.66, THREE.SRGBColorSpace);
}
var STAR = new THREE.Color().setStyle("#ffd98a", THREE.SRGBColorSpace);
var FILAMENT = new THREE.Color().setStyle("#d6be94", THREE.SRGBColorSpace);

/* ------------------------------------------------------------- set-up */
var host = document.getElementById("world");
var N = window.Nebula = { available: false, hooks: {} };

var renderer = null;
try {
  if (host) {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  }
} catch (e) { renderer = null; }

if (renderer) {
  N.available = true;
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(window.innerWidth, window.innerHeight);
  host.appendChild(renderer.domElement);

  var scene = new THREE.Scene();
  var camera = new THREE.PerspectiveCamera(
    50, window.innerWidth / window.innerHeight, 1, 12000);
  camera.position.set(0, 0, 460);

  var controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.minDistance = 60;
  controls.maxDistance = 4000;
  controls.autoRotate = false;
  controls.enabled = false;              // granted in focus (constellation) mode

  /* ------------------------------------------------ ambient: the nebula */
  function glowTexture(size, inner, mid) {
    var c = document.createElement("canvas");
    c.width = c.height = size;
    var g = c.getContext("2d");
    var grad = g.createRadialGradient(size / 2, size / 2, 0,
                                      size / 2, size / 2, size / 2);
    grad.addColorStop(0, inner);
    grad.addColorStop(0.35, mid);
    grad.addColorStop(1, "rgba(0,0,0,0)");
    g.fillStyle = grad;
    g.fillRect(0, 0, size, size);
    var tex = new THREE.CanvasTexture(c);
    tex.colorSpace = THREE.SRGBColorSpace;
    return tex;
  }

  var ambient = new THREE.Group();          // everything the toggle removes
  scene.add(ambient);

  var cloudTex = glowTexture(256, "rgba(255,255,255,.85)", "rgba(255,255,255,.28)");
  var CLOUDS = [
    { col: 0x24345e, s: 2400, p: [-700, 260, -1500], phase: 0.0 },
    { col: 0x3a2c58, s: 2100, p: [820, -340, -1700], phase: 2.1 },
    { col: 0x1d3d4a, s: 1800, p: [180, 620, -1900], phase: 4.2 },
    { col: 0x4a3520, s: 1500, p: [-260, -640, -1600], phase: 5.3 },
  ].map(function (cfg) {
    var m = new THREE.SpriteMaterial({
      map: cloudTex, color: cfg.col, transparent: true, opacity: 0.075,
      blending: THREE.AdditiveBlending, depthWrite: false,
    });
    var sp = new THREE.Sprite(m);
    sp.position.fromArray(cfg.p);
    sp.scale.setScalar(cfg.s);
    sp.userData = cfg;
    ambient.add(sp);
    return sp;
  });

  /* star dust: one instanced point cloud on a far shell, tiny + faint —
     the graph's stars stay unmistakably brighter and bigger. */
  var dustGroup = new THREE.Group();
  ambient.add(dustGroup);
  (function makeDust() {
    var COUNT = 1300, pos = new Float32Array(COUNT * 3),
        col = new Float32Array(COUNT * 3), size = new Float32Array(COUNT);
    var c = new THREE.Color();
    for (var i = 0; i < COUNT; i++) {
      // shell distribution: far behind and around the graph
      var r = 900 + Math.random() * 2400;
      var th = Math.random() * Math.PI * 2, ph = Math.acos(2 * Math.random() - 1);
      pos[i * 3] = r * Math.sin(ph) * Math.cos(th);
      pos[i * 3 + 1] = r * Math.sin(ph) * Math.sin(th) * 0.8;
      pos[i * 3 + 2] = r * Math.cos(ph);
      var warm = Math.random() < 0.18;
      c.setHSL(warm ? 0.11 : 0.6, warm ? 0.5 : 0.25,
               0.55 + Math.random() * 0.3, THREE.SRGBColorSpace);
      col[i * 3] = c.r; col[i * 3 + 1] = c.g; col[i * 3 + 2] = c.b;
      size[i] = 1.1 + Math.random() * 2.2;
    }
    var geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
    geo.setAttribute("psize", new THREE.BufferAttribute(size, 1));
    dustGroup.add(new THREE.Points(geo, pointsMaterial(0.5)));
  })();

  /* soft point-sprite material with per-point size (shared by dust + glow) */
  function pointsMaterial(alpha) {
    return new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
      uniforms: { uAlpha: { value: alpha } },
      vertexShader:
        "attribute float psize; varying vec3 vC;" +
        "void main(){ vC = color;" +
        " vec4 mv = modelViewMatrix * vec4(position,1.0);" +
        " gl_PointSize = psize * (320.0 / -mv.z);" +
        " gl_Position = projectionMatrix * mv; }",
      fragmentShader:
        "uniform float uAlpha; varying vec3 vC;" +
        "void main(){ float d = length(gl_PointCoord - vec2(.5));" +
        " float a = smoothstep(.5, .08, d) * uAlpha;" +
        " gl_FragColor = vec4(vC, a); }",
      vertexColors: true,
    });
  }

  /* the cursor's presence: a faint warm light drifting where you point */
  var cursorLight = new THREE.Sprite(new THREE.SpriteMaterial({
    map: glowTexture(128, "rgba(255,217,138,.5)", "rgba(255,217,138,.12)"),
    transparent: true, opacity: 0.16,
    blending: THREE.AdditiveBlending, depthWrite: false,
  }));
  cursorLight.scale.setScalar(340);
  cursorLight.visible = !reduced;
  ambient.add(cursorLight);

  /* --------------------------------------------------- the constellation */
  var graph = {
    group: new THREE.Group(),
    nodes: [], links: [], byId: {},
    mesh: null, glow: null, lines: null, pulses: null,
    sim: null, selected: null, hoverId: null, dim: 1,
  };
  scene.add(graph.group);

  var nodeGeo = new THREE.SphereGeometry(1, 22, 16);
  var nodeMat = new THREE.MeshBasicMaterial({ color: 0xffffff });
  var lineMat = new THREE.LineBasicMaterial({
    vertexColors: true, transparent: true, opacity: 1,
    blending: THREE.AdditiveBlending, depthWrite: false,
  });

  function radiusOf(n) {
    return (3 + Math.sqrt(n.degree || 0) * 2.6 + (n.importance || 3) * 0.8) * 0.62;
  }

  function disposeGraph() {
    ["mesh", "glow", "lines", "pulses"].forEach(function (k) {
      var o = graph[k];
      if (!o) return;
      graph.group.remove(o);
      if (o.geometry) o.geometry.dispose();
      if (o.material && o.material !== nodeMat && o.material !== lineMat) {
        o.material.dispose();
      }
      graph[k] = null;
    });
  }

  N.setGraph = function (data) {
    var old = graph.byId;
    graph.nodes = data.nodes.map(function (raw) {
      var n = Object.assign({}, raw);
      var prev = old[n.id];
      if (prev) { n.x = prev.x; n.y = prev.y; n.z = prev.z; }
      return n;
    });
    graph.byId = {};
    graph.nodes.forEach(function (n) { graph.byId[n.id] = n; });
    var ids = graph.byId;
    graph.links = (data.edges || [])
      .filter(function (e) { return ids[e.src] && ids[e.dst]; })
      .map(function (e) {
        // resolve to node objects up front: every consumer (buffers, pulses,
        // paint) can rely on .source.x whether or not the sim ever runs
        return { source: ids[e.src], target: ids[e.dst],
                 w: e.w || 0, kinds: e.kinds };
      });

    disposeGraph();
    if (!graph.nodes.length) { N.requestRender(); return; }

    /* cores: one InstancedMesh, unlit, full opacity — unmistakable */
    var mesh = new THREE.InstancedMesh(nodeGeo, nodeMat, graph.nodes.length);
    mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    var c = new THREE.Color();
    graph.nodes.forEach(function (n, i) {
      n._r = radiusOf(n);
      mesh.setColorAt(i, nodeColor(n, c));
    });
    mesh.instanceColor.needsUpdate = true;
    graph.mesh = mesh;
    graph.group.add(mesh);

    /* halos: additive point sprites behind the cores */
    var gpos = new Float32Array(graph.nodes.length * 3);
    var gcol = new Float32Array(graph.nodes.length * 3);
    var gsize = new Float32Array(graph.nodes.length);
    graph.nodes.forEach(function (n, i) {
      nodeColor(n, c);
      gcol[i * 3] = c.r; gcol[i * 3 + 1] = c.g; gcol[i * 3 + 2] = c.b;
      gsize[i] = n._r * 7;
    });
    var ggeo = new THREE.BufferGeometry();
    ggeo.setAttribute("position", new THREE.BufferAttribute(gpos, 3));
    ggeo.setAttribute("color", new THREE.BufferAttribute(gcol, 3));
    ggeo.setAttribute("psize", new THREE.BufferAttribute(gsize, 1));
    graph.glow = new THREE.Points(ggeo, pointsMaterial(0.34));
    graph.group.add(graph.glow);

    /* filaments */
    var lpos = new Float32Array(graph.links.length * 6);
    var lcol = new Float32Array(graph.links.length * 6);
    var lgeo = new THREE.BufferGeometry();
    lgeo.setAttribute("position", new THREE.BufferAttribute(lpos, 3));
    lgeo.setAttribute("color", new THREE.BufferAttribute(lcol, 3));
    graph.lines = new THREE.LineSegments(lgeo, lineMat);
    graph.group.add(graph.lines);

    /* selection pulses (filled during animate when a node is selected) */
    var pgeo = new THREE.BufferGeometry();
    var pmax = 64;
    pgeo.setAttribute("position",
      new THREE.BufferAttribute(new Float32Array(pmax * 3), 3));
    pgeo.setAttribute("color",
      new THREE.BufferAttribute(new Float32Array(pmax * 3), 3));
    pgeo.setAttribute("psize",
      new THREE.BufferAttribute(new Float32Array(pmax), 1));
    pgeo.setDrawRange(0, 0);
    graph.pulses = new THREE.Points(pgeo, pointsMaterial(0.9));
    graph.group.add(graph.pulses);

    startSim();
    paintLinks();
    N.requestRender();
  };

  function startSim() {
    if (typeof window.d3 === "undefined" || !window.d3.forceSimulation) {
      // layout library missing: place nodes on a sphere so the world
      // still shows every memory
      graph.nodes.forEach(function (n, i) {
        var ph = Math.acos(1 - 2 * (i + 0.5) / graph.nodes.length);
        var th = Math.PI * (1 + Math.sqrt(5)) * i;
        n.x = 180 * Math.sin(ph) * Math.cos(th);
        n.y = 180 * Math.sin(ph) * Math.sin(th);
        n.z = 180 * Math.cos(ph);
      });
      updateBuffers();
      return;
    }
    if (graph.sim) graph.sim.stop();
    graph.sim = window.d3.forceSimulation(graph.nodes, 3)
      .force("link", window.d3.forceLink(graph.links)
        .id(function (d) { return d.id; })
        .distance(function (l) { return 26 + 40 * (1 - (l.w || 0)); }))
      .force("charge", window.d3.forceManyBody().strength(-150))
      .force("center", window.d3.forceCenter(0, 0, 0))
      .stop();
    if (reduced) {                     // settle synchronously, render a still
      for (var i = 0; i < 220; i++) graph.sim.tick();
      graph.sim.alpha(0);
      updateBuffers();
    } else {
      graph.sim.alpha(1);
    }
  }

  function updateBuffers() {
    if (!graph.mesh) return;
    var m = new THREE.Matrix4(), q = new THREE.Quaternion(),
        s = new THREE.Vector3(), p = new THREE.Vector3();
    graph.nodes.forEach(function (n, i) {
      var r = n._r * (n.id === graph.selected ? 1.25 :
                      n.id === graph.hoverId ? 1.12 : 1);
      p.set(n.x || 0, n.y || 0, n.z || 0);
      s.setScalar(r);
      m.compose(p, q, s);
      graph.mesh.setMatrixAt(i, m);
      var g = graph.glow.geometry.attributes.position.array;
      g[i * 3] = p.x; g[i * 3 + 1] = p.y; g[i * 3 + 2] = p.z;
    });
    graph.mesh.instanceMatrix.needsUpdate = true;
    graph.glow.geometry.attributes.position.needsUpdate = true;
    graph.glow.geometry.computeBoundingSphere();
    var lp = graph.lines.geometry.attributes.position.array;
    graph.links.forEach(function (l, i) {
      var a = l.source, b = l.target;
      lp[i * 6] = a.x; lp[i * 6 + 1] = a.y; lp[i * 6 + 2] = a.z;
      lp[i * 6 + 3] = b.x; lp[i * 6 + 4] = b.y; lp[i * 6 + 5] = b.z;
    });
    graph.lines.geometry.attributes.position.needsUpdate = true;
    graph.lines.geometry.computeBoundingSphere();
  }

  function paintLinks() {
    if (!graph.lines) return;
    var lc = graph.lines.geometry.attributes.color.array;
    graph.links.forEach(function (l, i) {
      var sid = typeof l.source === "object" ? l.source.id : l.source;
      var tid = typeof l.target === "object" ? l.target.id : l.target;
      var onSel = graph.selected && (sid === graph.selected || tid === graph.selected);
      // additive blending: intensity IS opacity
      var k = (onSel ? 0.95 : 0.10 + (l.w || 0) * 0.5) * graph.dim;
      var col = onSel ? STAR : FILAMENT;
      for (var v = 0; v < 2; v++) {
        lc[i * 6 + v * 3] = col.r * k;
        lc[i * 6 + v * 3 + 1] = col.g * k;
        lc[i * 6 + v * 3 + 2] = col.b * k;
      }
    });
    graph.lines.geometry.attributes.color.needsUpdate = true;
  }

  function paintNodes() {
    if (!graph.mesh) return;
    var c = new THREE.Color();
    graph.nodes.forEach(function (n, i) {
      if (n.id === graph.selected) c.copy(STAR);
      else nodeColor(n, c);
      graph.mesh.setColorAt(i, c);
    });
    graph.mesh.instanceColor.needsUpdate = true;
    updateBuffers();
  }

  N.select = function (id) {
    graph.selected = id;
    paintNodes(); paintLinks(); N.requestRender();
  };
  N.deselect = function () { N.select(null); };
  N.getNode = function (id) { return graph.byId[id] || null; };

  /* --------------------------------------------------- focus vs backdrop */
  var focus = false;
  N.setFocus = function (on) {
    focus = !!on;
    controls.enabled = focus && !orb.veil;
    controls.autoRotate = !focus && !reduced && ambienceOn();
    controls.autoRotateSpeed = 0.25;               // one lap ≈ 4 minutes
    graph.dim = focus ? 1 : 0.38;
    nodeMat.opacity = 1;                            // cores never fade (rule 2)
    if (graph.glow) graph.glow.material.uniforms.uAlpha.value = focus ? 0.34 : 0.14;
    paintLinks();
    if (!focus) hideTip();
    N.requestRender();
  };

  N.setAmbience = function (on) {
    ambient.visible = !!on;
    controls.autoRotate = !focus && !reduced && !!on;
    N.requestRender();
  };
  N.setGraphVisible = function (on) {
    graph.group.visible = !!on;
    N.requestRender();
  };

  /* ------------------------------------------------------------ pointer */
  var pointer = { x: 0, y: 0, nx: 0, ny: 0, sx: 0, sy: 0 };
  var raycaster = new THREE.Raycaster();
  var tip = null;

  function makeTip() {
    tip = document.createElement("div");
    tip.className = "cst-tip";
    tip.style.display = "none";
    document.body.appendChild(tip);
  }
  function hideTip() {
    if (tip) tip.style.display = "none";
    if (graph.hoverId) { graph.hoverId = null; updateBuffers(); }
    renderer.domElement.style.cursor = "";
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  function pick() {
    if (!graph.mesh) return null;
    raycaster.setFromCamera(new THREE.Vector2(pointer.nx, pointer.ny), camera);
    var hit = raycaster.intersectObject(graph.mesh, false)[0];
    return hit && hit.instanceId != null ? graph.nodes[hit.instanceId] : null;
  }

  var hoverPending = false;
  function onHoverCheck() {
    hoverPending = false;
    if (!focus || orb.veil || drag.node) return;
    var n = pick();
    var id = n ? n.id : null;
    if (id !== graph.hoverId) {
      graph.hoverId = id;
      updateBuffers();
      N.requestRender();
      renderer.domElement.style.cursor = n ? "pointer" : "";
      if (!tip) makeTip();
      if (n) {
        tip.innerHTML = "<strong>" + esc(n.label || n.id) + "</strong>"
          + '<p class="quiet">' + esc(n.project || "") + " · " + esc(n.type || "")
          + " · importance " + (n.importance || "?")
          + " · gravity " + (n.degree != null ? Number(n.degree).toFixed(1) : "?")
          + "</p>";
        tip.style.display = "";
      } else tip.style.display = "none";
    }
    if (n && tip) {
      tip.style.left = Math.min(pointer.x + 14, window.innerWidth - 360) + "px";
      tip.style.top = (pointer.y + 14) + "px";
    }
  }

  /* the ONE pointermove listener for the whole world */
  window.addEventListener("pointermove", function (ev) {
    pointer.x = ev.clientX; pointer.y = ev.clientY;
    pointer.nx = (ev.clientX / window.innerWidth) * 2 - 1;
    pointer.ny = -(ev.clientY / window.innerHeight) * 2 + 1;
    if (drag.node) { dragMove(); return; }
    if (!hoverPending && focus && !reduced) {
      hoverPending = true;
      requestAnimationFrame(onHoverCheck);
    }
    N.requestRender();
  }, { passive: true });

  /* --------------------------------------------------------- node drag */
  var drag = { node: null, plane: new THREE.Plane(), off: new THREE.Vector3(),
               moved: 0, downAt: null };

  renderer.domElement.addEventListener("pointerdown", function (ev) {
    if (!focus || orb.veil) return;
    pointer.nx = (ev.clientX / window.innerWidth) * 2 - 1;
    pointer.ny = -(ev.clientY / window.innerHeight) * 2 + 1;
    var n = pick();
    drag.downAt = { x: ev.clientX, y: ev.clientY, node: n };
    if (!n) return;
    drag.node = n; drag.moved = 0;
    controls.enabled = false;
    var p = new THREE.Vector3(n.x, n.y, n.z);
    drag.plane.setFromNormalAndCoplanarPoint(
      camera.getWorldDirection(new THREE.Vector3()).negate(), p);
    var hit = new THREE.Vector3();
    raycaster.setFromCamera(new THREE.Vector2(pointer.nx, pointer.ny), camera);
    raycaster.ray.intersectPlane(drag.plane, hit);
    drag.off.copy(p).sub(hit);
    renderer.domElement.setPointerCapture(ev.pointerId);
  });

  function dragMove() {
    var n = drag.node;
    raycaster.setFromCamera(new THREE.Vector2(pointer.nx, pointer.ny), camera);
    var hit = new THREE.Vector3();
    if (!raycaster.ray.intersectPlane(drag.plane, hit)) return;
    hit.add(drag.off);
    drag.moved++;
    n.fx = n.x = hit.x; n.fy = n.y = hit.y; n.fz = n.z = hit.z;
    if (graph.sim && !reduced) graph.sim.alpha(Math.max(graph.sim.alpha(), 0.35));
    updateBuffers();
    N.requestRender();
  }

  renderer.domElement.addEventListener("pointerup", function (ev) {
    var wasDrag = drag.node && drag.moved > 2;
    if (drag.node) {
      drag.node.fx = drag.node.fy = drag.node.fz = null;
      drag.node = null;
      controls.enabled = focus && !orb.veil;
    }
    if (wasDrag || !drag.downAt) { drag.downAt = null; return; }
    var dx = Math.abs(ev.clientX - drag.downAt.x),
        dy = Math.abs(ev.clientY - drag.downAt.y);
    var clicked = drag.downAt.node;
    drag.downAt = null;
    if (dx > 4 || dy > 4) return;                       // it was an orbit
    if (clicked) { if (N.hooks.onNodeClick) N.hooks.onNodeClick(clicked); }
    else if (N.hooks.onBackgroundClick) N.hooks.onBackgroundClick();
  });

  /* --------------------------------------------- camera flights + orb */
  var flight = null;                       // {p0,p1,t0,t1,start,dur,done}
  function flyCamera(toPos, toTarget, dur, done) {
    if (reduced || dur === 0) {
      camera.position.copy(toPos);
      controls.target.copy(toTarget);
      controls.update();
      N.requestRender();
      if (done) done();
      return;
    }
    flight = {
      p0: camera.position.clone(), p1: toPos.clone(),
      t0: controls.target.clone(), t1: toTarget.clone(),
      start: performance.now(), dur: dur, done: done,
    };
  }

  var orb = { veil: null, saved: null, three: null };

  function orbCanvas(mem, W, H) {
    W = W || 2048; H = H || 1024;
    var c = document.createElement("canvas");
    c.width = W; c.height = H;
    var g = c.getContext("2d");
    g.clearRect(0, 0, W, H);
    g.fillStyle = "rgba(240, 220, 178, .95)";
    g.shadowColor = "rgba(255, 217, 138, .55)";
    g.shadowBlur = 10;
    g.font = 'italic 52px Georgia, "Iowan Old Style", serif';
    var words = ((mem.summary || "") + ".  " + (mem.content || "")).split(/\s+/);
    if (!words.length || (words.length === 1 && !words[0])) words = ["(no", "content)"];
    var margin = 130, lh = 78, wi = 0, y = H * 0.16;
    while (y < H * 0.86) {
      var line = "";
      while (wi < words.length) {
        var t = line ? line + " " + words[wi] : words[wi];
        if (g.measureText(t).width > W - margin * 2) break;
        line = t; wi++;
      }
      if (wi >= words.length) wi = 0;         // the text wraps the sphere forever
      g.save();
      g.translate(margin, y);
      g.rotate((Math.random() - 0.5) * 0.01);
      g.fillText(line, 0, 0);
      g.restore();
      y += lh;
    }
    return c;
  }

  function buildOrbScene(mem, hostEl) {
    var size = Math.min(Math.min(window.innerWidth, window.innerHeight) * 0.62, 560);
    var r2;
    try { r2 = new THREE.WebGLRenderer({ alpha: true, antialias: true }); }
    catch (e) { return null; }
    r2.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    r2.setSize(size, size);
    r2.domElement.className = "orb-canvas";
    hostEl.appendChild(r2.domElement);

    var sc = new THREE.Scene();
    var cam = new THREE.PerspectiveCamera(42, 1, 0.1, 10);
    cam.position.z = 3.1;
    sc.add(new THREE.AmbientLight(0xffffff, 0.9));
    var sun = new THREE.DirectionalLight(0xfff2d8, 0.8);
    sun.position.set(-2, 2.4, 3);
    sc.add(sun);

    var tex = new THREE.CanvasTexture(orbCanvas(mem));
    tex.colorSpace = THREE.SRGBColorSpace;
    var mesh = new THREE.Mesh(
      new THREE.SphereGeometry(1.16, 56, 40),
      new THREE.MeshBasicMaterial({ map: tex, transparent: true,
                                    side: THREE.DoubleSide, depthWrite: false }));
    mesh.rotation.y = Math.PI * 0.15;
    sc.add(mesh);
    var glass = new THREE.Mesh(
      new THREE.SphereGeometry(1.06, 40, 28),
      new THREE.MeshLambertMaterial({ color: 0x8fb4e6, transparent: true,
                                      opacity: 0.06, depthWrite: false }));
    sc.add(glass);
    var rim = new THREE.Sprite(new THREE.SpriteMaterial({
      map: glowTexture(128, "rgba(160,200,255,.35)", "rgba(160,200,255,.08)"),
      transparent: true, opacity: 0.5,
      blending: THREE.AdditiveBlending, depthWrite: false,
    }));
    rim.scale.setScalar(3.4);
    rim.position.z = -0.5;
    sc.add(rim);

    var o = { renderer: r2, scene: sc, camera: cam, mesh: mesh, glass: glass,
              rim: rim, tex: tex, raf: 0, dragging: false, vx: 0 };

    var lastX = 0, lastY = 0, el = r2.domElement;
    el.addEventListener("pointerdown", function (ev) {
      ev.stopPropagation();
      o.dragging = true; lastX = ev.clientX; lastY = ev.clientY;
      el.setPointerCapture(ev.pointerId);
      el.classList.add("grabbing");
    });
    el.addEventListener("pointermove", function (ev) {
      if (!o.dragging) return;
      var dx = ev.clientX - lastX, dy = ev.clientY - lastY;
      lastX = ev.clientX; lastY = ev.clientY;
      mesh.rotation.y += dx * 0.006;
      mesh.rotation.x = Math.max(-0.9, Math.min(0.9, mesh.rotation.x + dy * 0.004));
      o.vx = dx * 0.006;
    });
    el.addEventListener("pointerup", function () {
      o.dragging = false;
      el.classList.remove("grabbing");
    });
    el.addEventListener("click", function (ev) { ev.stopPropagation(); });

    (function frame() {
      if (!orb.three) return;
      if (!o.dragging) {
        o.vx *= 0.96;
        mesh.rotation.y += reduced ? o.vx : Math.max(o.vx, 0.0016);
      }
      r2.render(sc, cam);
      if (!reduced) o.raf = requestAnimationFrame(frame);
    })();
    if (reduced) r2.render(sc, cam);
    return o;
  }

  function destroyOrbScene() {
    var o = orb.three;
    if (!o) return;
    orb.three = null;
    cancelAnimationFrame(o.raf);
    o.tex.dispose();
    o.mesh.geometry.dispose(); o.mesh.material.dispose();
    o.glass.geometry.dispose(); o.glass.material.dispose();
    o.rim.material.dispose();
    o.renderer.dispose();
  }

  N.openOrb = function (mem, nodeId) {
    if (orb.veil) N.closeOrb(true);
    var n = graph.byId[nodeId];
    orb.saved = { pos: camera.position.clone(), target: controls.target.clone() };
    if (n) {
      var p = new THREE.Vector3(n.x || 0.01, n.y || 0.01, n.z || 0.01);
      var r = p.length() || 1;
      var dest = p.clone().multiplyScalar(1 + 70 / r);
      flyCamera(dest, p, 900);
    }
    controls.enabled = false;

    var veil = document.createElement("div");
    veil.className = "orb-veil";
    var stage = document.createElement("div");
    stage.className = "orb-stage";
    veil.appendChild(stage);
    var cap = document.createElement("figcaption");
    cap.className = "orb-caption";
    cap.innerHTML = "<strong>" + esc(mem.summary || mem.id)
      + "</strong><span>drag to turn · scroll to return · Esc</span>";
    veil.appendChild(cap);
    document.body.appendChild(veil);
    orb.veil = veil;

    orb.three = buildOrbScene(mem, stage);
    if (!orb.three) {
      var flat = orbCanvas(mem, 1200, 600);
      var two = document.createElement("canvas");
      two.width = 2400; two.height = 600;
      var ctx = two.getContext("2d");
      ctx.drawImage(flat, 0, 0); ctx.drawImage(flat, 1200, 0);
      var fig = document.createElement("figure");
      fig.className = "orb";
      fig.style.backgroundImage = "url(" + two.toDataURL("image/png") + ")";
      stage.appendChild(fig);
    }

    requestAnimationFrame(function () { requestAnimationFrame(function () {
      veil.classList.add("open");
    }); });

    veil.addEventListener("wheel", function (ev) {
      ev.preventDefault();
      N.closeOrb();
    }, { passive: false });
    veil.addEventListener("click", function (ev) {
      if (!ev.target.closest(".orb-stage")) N.closeOrb();
    });
    document.addEventListener("keydown", orbKey, true);
  };

  function orbKey(ev) {
    if (ev.key === "Escape") { ev.stopPropagation(); N.closeOrb(); }
  }

  N.closeOrb = function (instant) {
    if (!orb.veil) return;
    var veil = orb.veil;
    orb.veil = null;
    document.removeEventListener("keydown", orbKey, true);
    destroyOrbScene();
    veil.classList.remove("open");
    setTimeout(function () { veil.remove(); }, instant ? 0 : 260);
    if (orb.saved) {
      flyCamera(orb.saved.pos, orb.saved.target, instant ? 0 : 800, function () {
        controls.enabled = focus;
      });
      orb.saved = null;
    } else controls.enabled = focus;
    N.requestRender();
  };

  /* ------------------------------------------------------------ animate */
  var needsRender = true;
  N.requestRender = function () { needsRender = true; if (reduced) still(); };

  var stillPending = false;
  function still() {                     // reduced motion: render on demand
    if (stillPending) return;
    stillPending = true;
    requestAnimationFrame(function () {
      stillPending = false;
      if (flight) stepFlight(performance.now());
      controls.update();
      renderer.render(scene, camera);
    });
  }

  function stepFlight(now) {
    var f = flight;
    var t = Math.min(1, (now - f.start) / f.dur);
    var e = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;   // easeInOut
    camera.position.lerpVectors(f.p0, f.p1, e);
    controls.target.lerpVectors(f.t0, f.t1, e);
    if (t >= 1) { flight = null; if (f.done) f.done(); }
  }

  var lastPulse = 0;
  function animate(now) {
    requestAnimationFrame(animate);
    if (document.hidden) return;

    if (flight) stepFlight(now);

    /* physics: settle the constellation */
    if (graph.sim && graph.sim.alpha() > graph.sim.alphaMin()) {
      graph.sim.tick();
      updateBuffers();
    }

    /* ambient drift — the 60 s+ register only */
    if (ambient.visible) {
      var t = now * 0.001;
      dustGroup.rotation.y = t * (Math.PI * 2 / 900);        // one lap / 15 min
      dustGroup.rotation.x += (pointer.ny * 0.05 - dustGroup.rotation.x) * 0.02;
      CLOUDS.forEach(function (sp) {
        var cfg = sp.userData;
        sp.position.x = cfg.p[0] + Math.sin(t * 0.008 + cfg.phase) * 60;
        sp.position.y = cfg.p[1] + Math.cos(t * 0.006 + cfg.phase) * 44;
        var breathe = 1 + Math.sin(t * 0.009 + cfg.phase) * 0.03;   // ~70 s breath
        sp.scale.setScalar(cfg.s * breathe);
      });
      /* the cursor's light drifts to where you point */
      raycaster.setFromCamera(new THREE.Vector2(pointer.nx, pointer.ny), camera);
      var lp = raycaster.ray.at(camera.position.length() * 0.55, new THREE.Vector3());
      cursorLight.position.lerp(lp, 0.06);
    }

    /* selection pulses along the chosen star's filaments (feedback) */
    if (graph.pulses && graph.selected && graph.links.length) {
      var pp = graph.pulses.geometry.attributes.position.array;
      var pc = graph.pulses.geometry.attributes.color.array;
      var ps = graph.pulses.geometry.attributes.psize.array;
      var k = 0;
      var tt = now * 0.00035;
      for (var i = 0; i < graph.links.length && k < 62; i++) {
        var l = graph.links[i];
        var sid = typeof l.source === "object" ? l.source.id : l.source;
        var tid = typeof l.target === "object" ? l.target.id : l.target;
        if (sid !== graph.selected && tid !== graph.selected) continue;
        for (var j = 0; j < 2; j++, k++) {
          var f2 = (tt + i * 0.37 + j * 0.5) % 1;
          pp[k * 3] = l.source.x + (l.target.x - l.source.x) * f2;
          pp[k * 3 + 1] = l.source.y + (l.target.y - l.source.y) * f2;
          pp[k * 3 + 2] = l.source.z + (l.target.z - l.source.z) * f2;
          pc[k * 3] = STAR.r; pc[k * 3 + 1] = STAR.g; pc[k * 3 + 2] = STAR.b;
          ps[k] = 3.4;
        }
      }
      graph.pulses.geometry.setDrawRange(0, k);
      graph.pulses.geometry.attributes.position.needsUpdate = true;
      graph.pulses.geometry.attributes.color.needsUpdate = true;
      graph.pulses.geometry.attributes.psize.needsUpdate = true;
      graph.pulses.geometry.computeBoundingSphere();
    } else if (graph.pulses) {
      graph.pulses.geometry.setDrawRange(0, 0);
    }

    controls.update();
    renderer.render(scene, camera);
  }

  if (!reduced) requestAnimationFrame(animate);
  else still();

  window.addEventListener("resize", function () {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
    N.requestRender();
  });

  N.setAmbience(ambienceOn());
  N.setFocus(document.body.dataset.lens === "constellation");
}

/* let classic scripts know the world is up (or not) */
document.dispatchEvent(new CustomEvent("nebula:ready",
  { detail: { available: N.available } }));
