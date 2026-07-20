/* The Nebula — one living world.
   A single full-screen three.js scene owns everything behind the glass:
   deep space, procedural fbm gas clouds, a luminous boundary shell around
   the space the memories occupy, a cursor-stirred particle field with real
   spring physics, and the memories themselves as plasma stars — fresnel
   rims, domain-warped swirling cores, hot hearts — joined by synaptic
   filaments under a 3D force layout (d3-force-3d).

   Design rules honoured here (this repo's scar tissue):
   - ONE three instance (vendored r170 module) owns the ENTIRE scene. (rule 5)
   - Node legibility beats mood: cores are opaque, full-brightness, and
     their colour stays unmistakably the project colour.  (rule 2)
   - Two motion speeds: ambient (60 s+ drifts, near-still plasma churn)
     and feedback (~150 ms; the cursor field is direct feedback).  (rule 4)
   - prefers-reduced-motion: no animation loop — stills on demand,
     everything still works.  (rule 7)
   - The canvas sits under the shell; CSS grants it the pointer only in
     the Constellation lens. No ambient element can eat a click. (rule 6)
   - One pointermove listener; all lerp work inside the one rAF. (rule 8)
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
  return out.setHSL(hueOf(n.project) / 360, 0.7, 0.62, THREE.SRGBColorSpace);
}
var STAR = new THREE.Color().setStyle("#ffd98a", THREE.SRGBColorSpace);
var FILAMENT = new THREE.Color().setStyle("#d6be94", THREE.SRGBColorSpace);
var AURA = new THREE.Color().setStyle("#7fa8e0", THREE.SRGBColorSpace);

/* ------------------------------------------------ shared GLSL: noise */
var GLSL_NOISE = [
  "float nhash(vec3 p){ p = fract(p*0.3183099 + vec3(0.1,0.17,0.13));",
  "  p *= 17.0; return fract(p.x*p.y*p.z*(p.x+p.y+p.z)); }",
  "float vnoise(vec3 x){ vec3 i = floor(x); vec3 f = fract(x);",
  "  f = f*f*(3.0-2.0*f);",
  "  return mix(mix(mix(nhash(i+vec3(0,0,0)), nhash(i+vec3(1,0,0)), f.x),",
  "                 mix(nhash(i+vec3(0,1,0)), nhash(i+vec3(1,1,0)), f.x), f.y),",
  "             mix(mix(nhash(i+vec3(0,0,1)), nhash(i+vec3(1,0,1)), f.x),",
  "                 mix(nhash(i+vec3(0,1,1)), nhash(i+vec3(1,1,1)), f.x), f.y), f.z); }",
  "float fbm(vec3 p){ float a = 0.5, r = 0.0;",
  "  for (int i = 0; i < 4; i++){ r += a*vnoise(p); p *= 2.03; a *= 0.5; }",
  "  return r; }",
].join("\n");

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
    50, window.innerWidth / window.innerHeight, 1, 14000);
  camera.position.set(0, 60, 520);

  var controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;          // real weight and inertia
  controls.dampingFactor = 0.07;
  controls.minDistance = 60;
  controls.maxDistance = 5000;
  controls.autoRotate = false;
  controls.enabled = false;               // granted in focus mode only

  var clockTime = 0;                      // world time, seconds

  /* =========================================================== textures */
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

  /* real gas: domain-warped fbm on a canvas, faded radially — one-time
     cost at load, no external assets, endlessly variable */
  function cloudTexture(seed) {
    var S = 256, c = document.createElement("canvas");
    c.width = c.height = S;
    var g = c.getContext("2d");
    var img = g.createImageData(S, S);
    function h2(x, y) {
      var n = Math.sin(x * 127.1 + y * 311.7 + seed * 74.7) * 43758.5453;
      return n - Math.floor(n);
    }
    function vn(x, y) {
      var xi = Math.floor(x), yi = Math.floor(y), xf = x - xi, yf = y - yi;
      var u = xf * xf * (3 - 2 * xf), v = yf * yf * (3 - 2 * yf);
      return h2(xi, yi) * (1 - u) * (1 - v) + h2(xi + 1, yi) * u * (1 - v)
           + h2(xi, yi + 1) * (1 - u) * v + h2(xi + 1, yi + 1) * u * v;
    }
    function fbm2(x, y) {
      var a = 0.5, r = 0;
      for (var i = 0; i < 4; i++) { r += a * vn(x, y); x *= 2.03; y *= 2.03; a *= 0.5; }
      return r;
    }
    for (var y = 0; y < S; y++) {
      for (var x = 0; x < S; x++) {
        var u = x / S, v = y / S;
        var qx = fbm2(u * 4 + seed, v * 4);          // domain warp: the swirl
        var qy = fbm2(u * 4 + 5.2, v * 4 + 1.3);
        var d = fbm2(u * 4 + 1.7 * qx, v * 4 + 1.7 * qy);
        var dx = u - 0.5, dy = v - 0.5;
        var fall = Math.max(0, 1 - 2.15 * Math.sqrt(dx * dx + dy * dy));
        var a = Math.pow(Math.max(0, d - 0.28), 1.5) * fall * fall * 255 * 2.2;
        var i4 = (y * S + x) * 4;
        img.data[i4] = 255; img.data[i4 + 1] = 255; img.data[i4 + 2] = 255;
        img.data[i4 + 3] = Math.min(255, a);
      }
    }
    g.putImageData(img, 0, 0);
    var tex = new THREE.CanvasTexture(c);
    tex.colorSpace = THREE.SRGBColorSpace;
    return tex;
  }

  /* soft point-sprite material with per-point size */
  function pointsMaterial(alpha) {
    return new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
      uniforms: { uAlpha: { value: alpha } },
      vertexShader:
        "attribute float psize; varying vec3 vC;" +
        "void main(){ vC = color;" +
        " vec4 mv = modelViewMatrix * vec4(position,1.0);" +
        " gl_PointSize = min(psize * (320.0 / -mv.z), 72.0);" +
        " gl_Position = projectionMatrix * mv; }",
      fragmentShader:
        "uniform float uAlpha; varying vec3 vC;" +
        "void main(){ float d = length(gl_PointCoord - vec2(.5));" +
        " float a = smoothstep(.5, .08, d) * uAlpha;" +
        " gl_FragColor = vec4(vC, a); }",
      vertexColors: true,
    });
  }

  /* ================================================= THE SPACE (shell)
     A faint luminous membrane around the volume the memories occupy —
     bright only at its limb (fresnel), banded by slow aurora. Inside is
     clear; beyond it, the nebula thickens. */
  var SPACE = { r: 240, target: 240 };
  var shellMat = new THREE.ShaderMaterial({
    side: THREE.DoubleSide, transparent: true, depthWrite: false,
    blending: THREE.AdditiveBlending,
    uniforms: {
      uTime: { value: 0 },
      uColor: { value: AURA.clone() },
      uColor2: { value: new THREE.Color().setStyle("#b18fe0", THREE.SRGBColorSpace) },
      uAlpha: { value: 0.55 },
    },
    vertexShader: [
      "varying vec3 vN; varying vec3 vW; varying vec3 vP;",
      "void main(){",
      "  vP = position;",
      "  vN = normalize(mat3(modelMatrix) * normal);",
      "  vec4 wp = modelMatrix * vec4(position, 1.0);",
      "  vW = wp.xyz;",
      "  gl_Position = projectionMatrix * viewMatrix * wp; }",
    ].join("\n"),
    fragmentShader: [
      "uniform float uTime; uniform vec3 uColor; uniform vec3 uColor2;",
      "uniform float uAlpha;",
      "varying vec3 vN; varying vec3 vW; varying vec3 vP;",
      GLSL_NOISE,
      "void main(){",
      "  vec3 v = normalize(cameraPosition - vW);",
      "  float limb = pow(1.0 - abs(dot(normalize(vN), v)), 3.0);",
      "  if (limb < 0.003) discard;",
      "  float lat = normalize(vP).y;",
      "  float au = fbm(vec3(normalize(vP).xz * 3.0, lat * 2.0 + uTime * 0.01));",
      "  vec3 col = mix(uColor, uColor2, smoothstep(0.3, 0.7, au));",
      "  float band = 0.75 + 0.25 * au;",
      "  gl_FragColor = vec4(col * limb * band * uAlpha, limb * uAlpha); }",
    ].join("\n"),
  });
  var shell = new THREE.Mesh(new THREE.SphereGeometry(1, 72, 48), shellMat);
  shell.scale.setScalar(SPACE.r);
  shell.renderOrder = 2;
  scene.add(shell);

  /* =========================================== ambient: the nebula body */
  var ambient = new THREE.Group();          // everything the toggle removes
  scene.add(ambient);

  /* gas clouds live OUTSIDE the space: direction + distance factor,
     re-anchored whenever the shell grows */
  var CLOUD_DEFS = [
    { col: 0x2a3f78, f: 2.6, s: 5.2, dir: [-0.62, 0.28, -0.73], phase: 0.0 },
    { col: 0x4a3670, f: 3.1, s: 4.6, dir: [0.71, -0.32, -0.62], phase: 2.1 },
    { col: 0x1d4a58, f: 3.6, s: 4.0, dir: [0.14, 0.62, -0.77], phase: 4.2 },
    { col: 0x5a3f22, f: 2.9, s: 3.4, dir: [-0.28, -0.66, -0.70], phase: 5.3 },
    { col: 0x33508c, f: 3.3, s: 4.4, dir: [0.55, 0.45, 0.70], phase: 1.2 },
    { col: 0x452a60, f: 2.7, s: 3.8, dir: [-0.70, -0.10, 0.71], phase: 3.6 },
  ];
  var clouds = CLOUD_DEFS.map(function (cfg, i) {
    var m = new THREE.SpriteMaterial({
      map: cloudTexture(i * 3.7 + 1), color: cfg.col,
      transparent: true, opacity: 0.16,
      blending: THREE.AdditiveBlending, depthWrite: false,
    });
    var sp = new THREE.Sprite(m);
    sp.userData = cfg;
    ambient.add(sp);
    return sp;
  });
  function anchorClouds() {
    clouds.forEach(function (sp) {
      var cfg = sp.userData;
      var d = new THREE.Vector3().fromArray(cfg.dir).normalize()
        .multiplyScalar(SPACE.r * cfg.f);
      cfg.base = d;
      sp.position.copy(d);
      sp.scale.setScalar(SPACE.r * cfg.s);
    });
  }

  /* far starfield: a distant shell of tiny lights, denser than before,
     always beyond the space so its edge reads clearly */
  var farDust = null;
  function buildFarDust() {
    if (farDust) {
      ambient.remove(farDust);
      farDust.geometry.dispose(); farDust.material.dispose();
    }
    var COUNT = 2600, pos = new Float32Array(COUNT * 3),
        col = new Float32Array(COUNT * 3), size = new Float32Array(COUNT);
    var c = new THREE.Color();
    for (var i = 0; i < COUNT; i++) {
      var r = SPACE.r * (1.45 + Math.pow(Math.random(), 0.7) * 4.5);
      var th = Math.random() * Math.PI * 2, ph = Math.acos(2 * Math.random() - 1);
      pos[i * 3] = r * Math.sin(ph) * Math.cos(th);
      pos[i * 3 + 1] = r * Math.sin(ph) * Math.sin(th) * 0.85;
      pos[i * 3 + 2] = r * Math.cos(ph);
      var warm = Math.random() < 0.22;
      c.setHSL(warm ? 0.09 : 0.6, warm ? 0.55 : 0.3,
               0.5 + Math.random() * 0.4, THREE.SRGBColorSpace);
      col[i * 3] = c.r; col[i * 3 + 1] = c.g; col[i * 3 + 2] = c.b;
      size[i] = 1.0 + Math.random() * 2.6;
    }
    var geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
    geo.setAttribute("psize", new THREE.BufferAttribute(size, 1));
    farDust = new THREE.Points(geo, pointsMaterial(0.55));
    ambient.add(farDust);
  }

  /* ============================== the cursor field: physics you can stir
     Motes inside and just beyond the space with real dynamics: a slow
     curl drift, a hard-but-soft repulsion around the cursor's ray (they
     scatter, swirl sideways, and spring home with damped inertia). This
     is feedback — it answers the hand — so it runs at full rate. */
  var motes = null;
  var MOTES = { n: 1600, home: null, pos: null, vel: null };
  function buildMotes() {
    if (motes) {
      ambient.remove(motes);
      motes.geometry.dispose(); motes.material.dispose();
    }
    var n = MOTES.n;
    MOTES.home = new Float32Array(n * 3);
    MOTES.vel = new Float32Array(n * 3);
    var pos = new Float32Array(n * 3);
    var col = new Float32Array(n * 3), size = new Float32Array(n);
    var c = new THREE.Color();
    for (var i = 0; i < n; i++) {
      var r = SPACE.r * (0.25 + Math.pow(Math.random(), 0.8) * 0.92);
      var th = Math.random() * Math.PI * 2, ph = Math.acos(2 * Math.random() - 1);
      var x = r * Math.sin(ph) * Math.cos(th),
          y = r * Math.sin(ph) * Math.sin(th) * 0.9,
          z = r * Math.cos(ph);
      MOTES.home[i * 3] = pos[i * 3] = x;
      MOTES.home[i * 3 + 1] = pos[i * 3 + 1] = y;
      MOTES.home[i * 3 + 2] = pos[i * 3 + 2] = z;
      var warm = Math.random() < 0.3;
      c.setHSL(warm ? 0.11 : 0.58, 0.45, 0.6 + Math.random() * 0.25,
               THREE.SRGBColorSpace);
      col[i * 3] = c.r; col[i * 3 + 1] = c.g; col[i * 3 + 2] = c.b;
      size[i] = 1.2 + Math.random() * 2.0;
    }
    MOTES.pos = pos;
    var geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
    geo.setAttribute("psize", new THREE.BufferAttribute(size, 1));
    motes = new THREE.Points(geo, pointsMaterial(0.5));
    motes.geometry.attributes.position.setUsage(THREE.DynamicDrawUsage);
    ambient.add(motes);
  }

  var _ro = new THREE.Vector3(), _rd = new THREE.Vector3(), _tmp = new THREE.Vector3();
  function stepMotes(dt) {
    if (!motes || reduced) return;
    dt = Math.min(dt, 0.05);
    raycaster.setFromCamera(new THREE.Vector2(pointer.nx, pointer.ny), camera);
    _ro.copy(raycaster.ray.origin);
    _rd.copy(raycaster.ray.direction);
    var pos = MOTES.pos, vel = MOTES.vel, home = MOTES.home;
    var R = SPACE.r * 0.45, R2 = R * R;          // reach of the hand
    var t = clockTime;
    for (var i = 0; i < MOTES.n; i++) {
      var ix = i * 3, x = pos[ix], y = pos[ix + 1], z = pos[ix + 2];
      /* curl-ish drift — the ambient register, barely-there */
      vel[ix]     += 1.3 * Math.sin(0.011 * y + t * 0.05 + i) * dt;
      vel[ix + 1] += 1.3 * Math.sin(0.012 * z + t * 0.045) * dt;
      vel[ix + 2] += 1.3 * Math.sin(0.010 * x + t * 0.04) * dt;
      /* repulsion from the cursor's ray + a sideways swirl */
      var wx = x - _ro.x, wy = y - _ro.y, wz = z - _ro.z;
      var a = wx * _rd.x + wy * _rd.y + wz * _rd.z;
      if (a > 0) {
        var cx = wx - a * _rd.x, cy = wy - a * _rd.y, cz = wz - a * _rd.z;
        var d2 = cx * cx + cy * cy + cz * cz;
        if (d2 < R2 && d2 > 1e-4) {
          var d = Math.sqrt(d2);
          var f = (1 - d / R); f = 260 * f * f * dt / d;
          vel[ix] += cx * f; vel[ix + 1] += cy * f; vel[ix + 2] += cz * f;
          /* swirl: ray × offset — the stir */
          var sx = _rd.y * cz - _rd.z * cy,
              sy = _rd.z * cx - _rd.x * cz,
              sz = _rd.x * cy - _rd.y * cx;
          vel[ix] += sx * f * 0.45; vel[ix + 1] += sy * f * 0.45; vel[ix + 2] += sz * f * 0.45;
        }
      }
      /* spring home, damped — inertia you can feel */
      vel[ix]     += (home[ix] - x) * 1.1 * dt;
      vel[ix + 1] += (home[ix + 1] - y) * 1.1 * dt;
      vel[ix + 2] += (home[ix + 2] - z) * 1.1 * dt;
      var damp = 1 - 1.6 * dt;
      vel[ix] *= damp; vel[ix + 1] *= damp; vel[ix + 2] *= damp;
      pos[ix] += vel[ix] * dt; pos[ix + 1] += vel[ix + 1] * dt; pos[ix + 2] += vel[ix + 2] * dt;
    }
    motes.geometry.attributes.position.needsUpdate = true;
  }

  /* the cursor's presence: a faint warm light drifting where you point */
  var cursorLight = new THREE.Sprite(new THREE.SpriteMaterial({
    map: glowTexture(128, "rgba(255,217,138,.5)", "rgba(255,217,138,.12)"),
    transparent: true, opacity: 0.15,
    blending: THREE.AdditiveBlending, depthWrite: false,
  }));
  cursorLight.scale.setScalar(340);
  cursorLight.visible = !reduced;
  ambient.add(cursorLight);

  function anchorSpace() {          // everything that hangs off the radius
    shell.scale.setScalar(SPACE.r);
    anchorClouds();
    buildFarDust();
    buildMotes();
  }
  anchorSpace();

  /* ====================================== the constellation: plasma stars
     Not coloured balls: each memory is a small sun. Domain-warped noise
     churns under the surface (newer memories churn faster), a hot heart
     burns at the centre (importance), and a fresnel rim in the project
     colour holds the silhouette. Selection turns the whole star to warm
     starlight via instanceColor. Opaque, full brightness — rule 2. */
  var graph = {
    group: new THREE.Group(),
    nodes: [], links: [], byId: {},
    mesh: null, glow: null, lines: null, pulses: null,
    sim: null, selected: null, hoverId: null, dim: 1,
  };
  scene.add(graph.group);

  var nodeGeo = new THREE.SphereGeometry(1, 28, 20);
  var nodeMat = new THREE.ShaderMaterial({
    uniforms: {
      uTime: { value: 0 },
      uAura: { value: AURA.clone() },
    },
    vertexShader: [
      "attribute float aSeed; attribute float aHeat; attribute float aChurn;",
      "varying vec3 vN; varying vec3 vW; varying vec3 vP; varying vec3 vC;",
      "varying float vSeed; varying float vHeat; varying float vChurn;",
      "void main(){",
      "  vC = instanceColor;",
      "  vSeed = aSeed; vHeat = aHeat; vChurn = aChurn;",
      "  vec4 wp = modelMatrix * instanceMatrix * vec4(position, 1.0);",
      "  vN = normalize(mat3(modelMatrix) * mat3(instanceMatrix) * normal);",
      "  vW = wp.xyz; vP = position;",
      "  gl_Position = projectionMatrix * viewMatrix * wp; }",
    ].join("\n"),
    fragmentShader: [
      "uniform float uTime; uniform vec3 uAura;",
      "varying vec3 vN; varying vec3 vW; varying vec3 vP; varying vec3 vC;",
      "varying float vSeed; varying float vHeat; varying float vChurn;",
      GLSL_NOISE,
      "void main(){",
      "  vec3 n = normalize(vN);",
      "  vec3 v = normalize(cameraPosition - vW);",
      "  float ndv = clamp(dot(n, v), 0.0, 1.0);",
      "  /* plasma: domain-warped fbm churning under the surface */",
      "  float t = uTime * (0.015 + vChurn * 0.035) + vSeed * 19.0;",
      "  vec3 p = vP * 2.4 + vSeed * 7.0;",
      "  vec3 q = vec3(fbm(p + vec3(t, 0.0, 0.0)),",
      "                fbm(p + vec3(5.2, t * 0.8, 1.3)),",
      "                fbm(p + vec3(1.7, 9.2, -t * 0.6)));",
      "  float sw = fbm(p + 1.9 * q);",
      "  /* palette: shadowed body -> project colour -> white-hot */",
      "  vec3 deep = vC * 0.22;",
      "  vec3 hot = mix(vC, vec3(1.0, 0.97, 0.9), 0.8);",
      "  vec3 col = mix(deep, vC, smoothstep(0.3, 0.75, sw));",
      "  col += hot * pow(max(sw - 0.45, 0.0) * 1.8, 2.0) * (0.6 + vHeat);",
      "  /* the heart: importance burns at the centre of the disc */",
      "  col += hot * pow(ndv, 3.0) * (0.25 + 0.85 * vHeat);",
      "  /* fresnel rim: silhouette in project colour kissed by the aura */",
      "  float rim = pow(1.0 - ndv, 2.6);",
      "  col += mix(vC, uAura, 0.3) * rim * 1.7;",
      "  gl_FragColor = vec4(col, 1.0); }",
    ].join("\n"),
  });

  var lineMat = new THREE.LineBasicMaterial({
    vertexColors: true, transparent: true, opacity: 1,
    blending: THREE.AdditiveBlending, depthWrite: false,
  });

  function radiusOf(n) {
    return (3 + Math.sqrt(n.degree || 0) * 2.6 + (n.importance || 3) * 0.8) * 0.62;
  }
  function churnOf(n) {              // newer memories churn faster
    var t = Date.parse(n.timestamp || "") || 0;
    if (!t) return 0.4;
    var days = (Date.now() - t) / 864e5;
    return Math.max(0, Math.min(1, 1 - days / 60));
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
    if (!graph.nodes.length) { fitSpace(); N.requestRender(); return; }

    /* plasma stars: one InstancedMesh + per-instance seed/heat/churn */
    var count = graph.nodes.length;
    var geo = nodeGeo.clone();
    var seeds = new Float32Array(count), heats = new Float32Array(count),
        churns = new Float32Array(count);
    graph.nodes.forEach(function (n, i) {
      n._r = radiusOf(n);
      seeds[i] = (i * 0.61803) % 1;
      heats[i] = Math.max(0, Math.min(1, ((n.importance || 3) - 1) / 4));
      churns[i] = churnOf(n);
    });
    geo.setAttribute("aSeed", new THREE.InstancedBufferAttribute(seeds, 1));
    geo.setAttribute("aHeat", new THREE.InstancedBufferAttribute(heats, 1));
    geo.setAttribute("aChurn", new THREE.InstancedBufferAttribute(churns, 1));
    var mesh = new THREE.InstancedMesh(geo, nodeMat, count);
    mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    var c = new THREE.Color();
    graph.nodes.forEach(function (n, i) { mesh.setColorAt(i, nodeColor(n, c)); });
    mesh.instanceColor.needsUpdate = true;
    graph.mesh = mesh;
    graph.group.add(mesh);

    /* halos: additive point sprites behind the stars */
    var gpos = new Float32Array(count * 3);
    var gcol = new Float32Array(count * 3);
    var gsize = new Float32Array(count);
    graph.nodes.forEach(function (n, i) {
      nodeColor(n, c);
      gcol[i * 3] = c.r; gcol[i * 3 + 1] = c.g; gcol[i * 3 + 2] = c.b;
      gsize[i] = n._r * 7.5;
    });
    var ggeo = new THREE.BufferGeometry();
    ggeo.setAttribute("position", new THREE.BufferAttribute(gpos, 3));
    ggeo.setAttribute("color", new THREE.BufferAttribute(gcol, 3));
    ggeo.setAttribute("psize", new THREE.BufferAttribute(gsize, 1));
    graph.glow = new THREE.Points(ggeo, pointsMaterial(0.3));
    graph.group.add(graph.glow);

    /* filaments */
    var lpos = new Float32Array(graph.links.length * 6);
    var lcol = new Float32Array(graph.links.length * 6);
    var lgeo = new THREE.BufferGeometry();
    lgeo.setAttribute("position", new THREE.BufferAttribute(lpos, 3));
    lgeo.setAttribute("color", new THREE.BufferAttribute(lcol, 3));
    graph.lines = new THREE.LineSegments(lgeo, lineMat);
    graph.group.add(graph.lines);

    /* selection pulses (filled during animate when a star is selected) */
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
      graph.nodes.forEach(function (n, i) {
        var ph = Math.acos(1 - 2 * (i + 0.5) / graph.nodes.length);
        var th = Math.PI * (1 + Math.sqrt(5)) * i;
        n.x = 180 * Math.sin(ph) * Math.cos(th);
        n.y = 180 * Math.sin(ph) * Math.sin(th);
        n.z = 180 * Math.cos(ph);
      });
      updateBuffers();
      fitSpace();
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
      fitSpace(true);
    } else {
      graph.sim.alpha(1);
    }
  }

  /* the space breathes outward to hold its stars (with margin), never
     jumps: the shell eases toward the fit */
  function fitSpace(now) {
    var maxR = 0;
    graph.nodes.forEach(function (n) {
      var d = Math.sqrt((n.x || 0) * (n.x || 0) + (n.y || 0) * (n.y || 0)
                        + (n.z || 0) * (n.z || 0)) + (n._r || 4) * 3;
      if (d > maxR) maxR = d;
    });
    SPACE.target = Math.max(170, maxR * 1.22);
    if (now || reduced) {
      SPACE.r = SPACE.target;
      anchorSpace();
    } else if (Math.abs(SPACE.target - SPACE.r) / SPACE.r > 0.45) {
      SPACE.r = SPACE.target;          // big jump (new dataset): re-anchor
      anchorSpace();
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
    if (graph.glow) graph.glow.material.uniforms.uAlpha.value = focus ? 0.3 : 0.13;
    shellMat.uniforms.uAlpha.value = focus ? 0.55 : 0.3;
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
    shell.visible = !!on;
    N.requestRender();
  };

  /* ------------------------------------------------------------ pointer */
  var pointer = { x: 0, y: 0, nx: 0, ny: 0, px: 0, py: 0 };
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
    if (!hoverPending && focus) {       // hover is feedback, not animation:
      hoverPending = true;              // it works under reduced motion too
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
              rim: rim, tex: tex, raf: 0, dragging: false, vx: 0, alive: true };

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

    /* the loop keys off its OWN liveness flag — the old version checked a
       field that was only assigned after this returned, so the orb never
       drew a single frame. Never again. */
    o.start = function () {
      (function frame() {
        if (!o.alive) return;
        if (!o.dragging) {
          o.vx *= 0.96;
          mesh.rotation.y += reduced ? o.vx : Math.max(o.vx, 0.0016);
        }
        r2.render(sc, cam);
        if (!reduced) o.raf = requestAnimationFrame(frame);
      })();
      if (reduced) r2.render(sc, cam);
    };
    return o;
  }

  function destroyOrbScene() {
    var o = orb.three;
    if (!o) return;
    orb.three = null;
    o.alive = false;
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
    if (orb.three) {
      orb.three.start();
    } else {
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

  var lastNow = 0, fitCounter = 0;
  var docStyle = document.documentElement.style;
  function animate(now) {
    requestAnimationFrame(animate);
    if (document.hidden) { lastNow = now; return; }
    var dt = Math.min((now - lastNow) / 1000 || 0.016, 0.05);
    lastNow = now;
    clockTime = now * 0.001;

    if (flight) stepFlight(now);

    /* physics: settle the constellation */
    if (graph.sim && graph.sim.alpha() > graph.sim.alphaMin()) {
      graph.sim.tick();
      updateBuffers();
      if (++fitCounter % 20 === 0) fitSpace();
    }

    /* the shell eases toward its fit — weight, not snap */
    if (Math.abs(SPACE.target - SPACE.r) > 0.5) {
      SPACE.r += (SPACE.target - SPACE.r) * 0.03;
      shell.scale.setScalar(SPACE.r);
    }

    /* time flows through the shaders (slow: the plasma register) */
    nodeMat.uniforms.uTime.value = clockTime;
    shellMat.uniforms.uTime.value = ambient.visible ? clockTime : 0;

    /* the glass leans with the hand: two custom props, GPU transforms only */
    pointer.px += (pointer.nx - pointer.px) * 0.04;
    pointer.py += (pointer.ny - pointer.py) * 0.04;
    docStyle.setProperty("--px", pointer.px.toFixed(4));
    docStyle.setProperty("--py", pointer.py.toFixed(4));

    if (ambient.visible) {
      /* cursor physics field — feedback, full rate */
      stepMotes(dt);
      /* ambient drift — the 60 s+ register only */
      var t = clockTime;
      if (farDust) farDust.rotation.y = t * (Math.PI * 2 / 900);   // 15 min/lap
      clouds.forEach(function (sp) {
        var cfg = sp.userData;
        if (!cfg.base) return;
        sp.position.set(
          cfg.base.x + Math.sin(t * 0.008 + cfg.phase) * SPACE.r * 0.14,
          cfg.base.y + Math.cos(t * 0.006 + cfg.phase) * SPACE.r * 0.1,
          cfg.base.z);
        var breathe = 1 + Math.sin(t * 0.009 + cfg.phase) * 0.04;   // ~70 s breath
        sp.scale.setScalar(SPACE.r * cfg.s * breathe);
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
