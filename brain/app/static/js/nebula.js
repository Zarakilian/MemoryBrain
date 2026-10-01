/* The Nebula — one living world.
   A single full-screen three.js scene owns everything behind the glass:
   a liquid aurora-fog background that flows like slow smoke and swirls
   around the cursor, a luminous boundary shell around the space the
   memories occupy, cursor-stirred fog-wisps with real spring physics,
   and the memories themselves as plasma stars — fresnel rims,
   domain-warped swirling cores, hot hearts — joined by synaptic
   filaments under a 3D force layout (d3-force-3d).

   v3.1: far stars twinkle on the GPU behind the fog; filaments are curved
   synapses that carry pulses of energy, and the synapses of a touched
   star fire (pulses race away from it, light ripples out); the Brain
   layout puts the memories inside a folded, softly lit cortex; layout
   changes morph instead of jumping; a slow machine drops its pixel ratio
   before it drops a frame.

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
/* Hierarchy is colour (design rule: four colours, no more). The rest of
   the UI keeps project hues; in here, rank is what you need to read. */
var LEVEL_COLORS = [
  new THREE.Color().setStyle("#ffe3a1", THREE.SRGBColorSpace),  // head: gold sun
  new THREE.Color().setStyle("#ff9d5c", THREE.SRGBColorSpace),  // level 1: ember
  new THREE.Color().setStyle("#4fd6c5", THREE.SRGBColorSpace),  // level 2: teal
  new THREE.Color().setStyle("#8b93ec", THREE.SRGBColorSpace),  // level 3+: violet
];
function nodeColor(n, out) {
  var lv = n._level == null ? 2 : Math.min(n._level, 3);
  return out.copy(LEVEL_COLORS[lv]);
}
var SELECT = new THREE.Color().setStyle("#ffffff", THREE.SRGBColorSpace);
var STAR = new THREE.Color().setStyle("#ffd98a", THREE.SRGBColorSpace);
var FILAMENT = new THREE.Color().setStyle("#d6be94", THREE.SRGBColorSpace);
var AURA = new THREE.Color().setStyle("#7fa8e0", THREE.SRGBColorSpace);

/* v2.5 workspace layer: files and folders are a second species. Cold,
   faceted, never plasma, so a crystal is never mistaken for a star. */
var ICE = new THREE.Color().setStyle("#bfe9ff", THREE.SRGBColorSpace);
var ICE_RIM = new THREE.Color().setStyle("#e8f7ff", THREE.SRGBColorSpace);
var SLATE = new THREE.Color().setStyle("#8ea0c8", THREE.SRGBColorSpace);
var FILELINK = new THREE.Color().setStyle("#7fd8ff", THREE.SRGBColorSpace);
var FOLDLINK = new THREE.Color().setStyle("#6b7fa8", THREE.SRGBColorSpace);

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
  /* framing: when a new dataset or shape has settled, the camera glides
     back to hold the whole of it, unless the hand already took the camera */
  var framing = { pending: true, refine: false, userMoved: false, keepDir: false };
  controls.addEventListener("start", function () {
    framing.userMoved = true;
    framing.pending = false;
    framing.refine = false;
  });
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

  /* soft point-sprite material with per-point size; breathe = the halo
     swells and settles slowly per point (needs an aSeed attribute) */
  function pointsMaterial(alpha, breathe) {
    return new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
      uniforms: { uAlpha: { value: alpha }, uTime: { value: 0 } },
      vertexShader:
        "attribute float psize; varying vec3 vC; uniform float uTime;" +
        (breathe ? "attribute float aSeed;" : "") +
        "void main(){ vC = color;" +
        " vec4 mv = modelViewMatrix * vec4(position,1.0);" +
        (breathe
          ? " float br = 1.0 + 0.11 * sin(uTime * (0.9 + aSeed) + aSeed * 31.0);"
          : " float br = 1.0;") +
        " gl_PointSize = min(psize * br * (320.0 / -mv.z), 80.0);" +
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
  shell.frustumCulled = false;
  scene.add(shell);

  /* =========================================== ambient: the nebula body */
  var ambient = new THREE.Group();          // everything the toggle removes
  scene.add(ambient);

  /* THE LIVING FOG — no star specks, no sprites: one screen-covering
     shader where the whole background is liquid. Domain-warped noise
     flows like slow smoke through indigo, violet, teal and a rare warm
     ember; the cursor stirs a gentle vortex into the flow and carries a
     faint warm bloom. Drawn first, under everything, at the far plane. */
  var GLSL_NOISE2 = [
    "float h2(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }",
    "float vn2(vec2 x){ vec2 i = floor(x); vec2 f = fract(x);",
    "  f = f*f*(3.0-2.0*f);",
    "  return mix(mix(h2(i), h2(i+vec2(1,0)), f.x),",
    "             mix(h2(i+vec2(0,1)), h2(i+vec2(1,1)), f.x), f.y); }",
    "float fbm2(vec2 p){ float a = 0.5, r = 0.0;",
    "  for (int i = 0; i < 4; i++){ r += a*vn2(p); p = p*2.03 + vec2(17.0, 9.0); a *= 0.5; }",
    "  return r; }",
  ].join("\n");

  var auroraMat = new THREE.ShaderMaterial({
    depthWrite: false, depthTest: false,
    uniforms: {
      uTime: { value: 0 },
      /* gl_FragCoord is in DEVICE pixels: uRes must carry the pixel ratio */
      uRes: { value: new THREE.Vector2(
        window.innerWidth * Math.min(window.devicePixelRatio || 1, 2),
        window.innerHeight * Math.min(window.devicePixelRatio || 1, 2)) },
      uMouse: { value: new THREE.Vector2(0.5, 0.5) },
    },
    vertexShader:
      "void main(){ gl_Position = vec4(position.xy, 1.0, 1.0); }",
    fragmentShader: [
      "uniform float uTime; uniform vec2 uRes; uniform vec2 uMouse;",
      GLSL_NOISE2,
      "void main(){",
      "  vec2 uv = gl_FragCoord.xy / uRes;",
      "  float aspect = uRes.x / max(uRes.y, 1.0);",
      "  vec2 p = vec2(uv.x * aspect, uv.y);",
      "  vec2 m = vec2(uMouse.x * aspect, uMouse.y);",
      "  /* the hand stirs the fog: a soft vortex around the cursor */",
      "  vec2 d = p - m;",
      "  float sw = exp(-dot(d, d) * 7.0);",
      "  float ca = cos(sw * 1.1), sa = sin(sw * 1.1);",
      "  p = m + mat2(ca, -sa, sa, ca) * d;",
      "  /* liquid: noise fed back into itself, drifting slowly */",
      "  float t = uTime * 0.02;",
      "  vec2 q = vec2(fbm2(p * 1.5 + vec2(t * 0.7, 0.0)),",
      "                fbm2(p * 1.5 + vec2(5.2, t * 0.55)));",
      "  float f  = fbm2(p * 1.5 + 2.1 * q + vec2(0.0, t * 0.35));",
      "  float f2 = fbm2(p * 0.65 - 1.4 * q + vec2(t * 0.22, -t * 0.16));",
      "  vec3 col = vec3(0.027, 0.042, 0.088);",                 // deep indigo
      "  col = mix(col, vec3(0.10, 0.062, 0.20), smoothstep(0.35, 0.85, f));",
      "  col = mix(col, vec3(0.028, 0.125, 0.155), smoothstep(0.42, 0.9, f2) * 0.85);",
      "  col += vec3(0.16, 0.088, 0.045) * pow(max(f * f2 - 0.17, 0.0), 2.0) * 1.4;",
      "  /* the cursor's warmth, carried in the fog itself */",
      "  col += vec3(0.30, 0.24, 0.13) * sw * (0.35 + 0.65 * f) * 0.55;",
      "  /* vignette into the void */",
      "  float vig = smoothstep(1.25, 0.35, distance(uv, vec2(0.5)));",
      "  gl_FragColor = vec4(col * vig, 1.0); }",
    ].join("\n"),
  });
  var aurora = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), auroraMat);
  aurora.frustumCulled = false;      // clip-space quad: culling must not think
  aurora.renderOrder = -10;
  ambient.add(aurora);

  /* DEEP SPACE: far stars all around, twinkling on the GPU alone (the CPU
     never touches them after build). Small and faint by design: the fog
     is the sky, the stars give it depth, a memory always outshines them.
     The field rides with the camera, so it is turned, never reached. */
  var starsMat = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    uniforms: { uTime: { value: 0 }, uPR: { value: renderer.getPixelRatio() } },
    vertexShader: [
      "attribute float aSize; attribute float aPhase; attribute float aRate;",
      "uniform float uTime; uniform float uPR;",
      "varying vec3 vC; varying float vTw;",
      "void main(){",
      "  vC = color;",
      "  vTw = 0.6 + 0.4 * sin(uTime * aRate + aPhase);",
      "  gl_PointSize = aSize * uPR * (0.8 + 0.35 * vTw);",
      "  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }",
    ].join("\n"),
    fragmentShader: [
      "varying vec3 vC; varying float vTw;",
      "void main(){",
      "  vec2 c = gl_PointCoord - 0.5;",
      "  float core = smoothstep(0.5, 0.0, length(c));",
      "  float spike = max(0.0, 1.0 - abs(c.x) * 14.0) * max(0.0, 1.0 - abs(c.y) * 2.2)",
      "              + max(0.0, 1.0 - abs(c.y) * 14.0) * max(0.0, 1.0 - abs(c.x) * 2.2);",
      "  float a = (pow(core, 2.4) + spike * 0.2) * vTw;",
      "  gl_FragColor = vec4(vC * a, a); }",
    ].join("\n"),
    vertexColors: true,
  });
  var stars = (function () {
    var n = 2600, c = new THREE.Color();
    var pos = new Float32Array(n * 3), col = new Float32Array(n * 3);
    var size = new Float32Array(n), phase = new Float32Array(n), rate = new Float32Array(n);
    for (var i = 0; i < n; i++) {
      var u = Math.random() * 2 - 1, th = Math.random() * Math.PI * 2;
      var sn = Math.sqrt(1 - u * u), r = 3600 + Math.random() * 2600;
      pos[i * 3] = r * sn * Math.cos(th);
      pos[i * 3 + 1] = r * u;
      pos[i * 3 + 2] = r * sn * Math.sin(th);
      var temp = Math.random();
      c.setStyle(temp < 0.12 ? "#ffd9a8" : temp < 0.3 ? "#a9c6ff" : "#e8eeff",
                 THREE.SRGBColorSpace);
      var hero = Math.random() < 0.025;
      var bright = hero ? 1.0 : 0.3 + Math.random() * 0.45;
      col[i * 3] = c.r * bright; col[i * 3 + 1] = c.g * bright; col[i * 3 + 2] = c.b * bright;
      size[i] = hero ? 5 + Math.random() * 3 : 1.1 + Math.pow(Math.random(), 3) * 2.4;
      phase[i] = Math.random() * Math.PI * 2;
      rate[i] = 0.4 + Math.random() * 1.8;
    }
    var geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
    geo.setAttribute("aSize", new THREE.BufferAttribute(size, 1));
    geo.setAttribute("aPhase", new THREE.BufferAttribute(phase, 1));
    geo.setAttribute("aRate", new THREE.BufferAttribute(rate, 1));
    var pts = new THREE.Points(geo, starsMat);
    pts.frustumCulled = false;
    pts.renderOrder = -9;
    return pts;
  })();
  ambient.add(stars);

  /* ============================ the cursor field: physics you can stir
     Not stars, not specks: soft fog-wisps — large, blurred, barely-there
     breaths of the nebula drifting through the space, with real dynamics.
     A slow curl drift, a hard-but-soft repulsion around the cursor's ray
     (they scatter, swirl sideways, and spring home with damped inertia).
     This is feedback — it answers the hand — so it runs at full rate. */
  var motes = null;
  var MOTES = { n: 700, home: null, pos: null, vel: null };
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
      var r = SPACE.r * (0.25 + Math.pow(Math.random(), 0.8) * 1.05);
      var th = Math.random() * Math.PI * 2, ph = Math.acos(2 * Math.random() - 1);
      var x = r * Math.sin(ph) * Math.cos(th),
          y = r * Math.sin(ph) * Math.sin(th) * 0.9,
          z = r * Math.cos(ph);
      MOTES.home[i * 3] = pos[i * 3] = x;
      MOTES.home[i * 3 + 1] = pos[i * 3 + 1] = y;
      MOTES.home[i * 3 + 2] = pos[i * 3 + 2] = z;
      var warm = Math.random() < 0.22;
      c.setHSL(warm ? 0.1 : 0.6, 0.35, 0.5 + Math.random() * 0.2,
               THREE.SRGBColorSpace);
      col[i * 3] = c.r; col[i * 3 + 1] = c.g; col[i * 3 + 2] = c.b;
      size[i] = 7 + Math.random() * 9;             // fog breath, not a star
    }
    MOTES.pos = pos;
    var geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
    geo.setAttribute("psize", new THREE.BufferAttribute(size, 1));
    motes = new THREE.Points(geo, pointsMaterial(0.13));
    motes.geometry.attributes.position.setUsage(THREE.DynamicDrawUsage);
    motes.frustumCulled = false;
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

  function anchorSpace() {          // everything that hangs off the radius
    shell.scale.setScalar(SPACE.r);
    buildMotes();
  }
  anchorSpace();

  /* ====================================== the constellation: plasma stars
     Not coloured balls: each memory is a small sun. Domain-warped noise
     churns under the surface (newer memories churn faster), a hot heart
     burns at the centre (importance), and a fresnel rim in the project
     colour holds the silhouette. Selection turns the whole star to warm
     starlight via instanceColor. Opaque, full brightness — rule 2. */
  /* which layout shape is in force. It starts as the saved choice (the
     same key constellation.js writes), so a first load settles once, in
     the right shape, instead of settling organic and then again. */
  var layoutMode = (function () {
    var L = window.NebulaLayouts;
    try {
      var v = localStorage.getItem("nebula-layout");
      if (L && L.MODES.indexOf(v) >= 0) return v;
    } catch (e) {}
    return (L && L.DEFAULT) || "organic";
  })();
  var graph = {
    group: new THREE.Group(),
    nodes: [], links: [], byId: {},
    mesh: null, glow: null, lines: null, pulses: null,
    files: null, folders: null,            // v2.5 instanced crystals / planets
    memNodes: [], fileNodes: [], folderNodes: [],
    sim: null, selected: null, hoverId: null, dim: 1,
    hoverLink: -1, linkFocus: -1,
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
      "attribute float aHov;",
      "varying vec3 vN; varying vec3 vW; varying vec3 vP; varying vec3 vC;",
      "varying float vSeed; varying float vHeat; varying float vChurn;",
      "varying float vHov;",
      "void main(){",
      "  vC = instanceColor;",
      "  vSeed = aSeed; vHeat = aHeat; vChurn = aChurn; vHov = aHov;",
      "  vec4 wp = modelMatrix * instanceMatrix * vec4(position, 1.0);",
      "  vN = normalize(mat3(modelMatrix) * mat3(instanceMatrix) * normal);",
      "  vW = wp.xyz; vP = position;",
      "  gl_Position = projectionMatrix * viewMatrix * wp; }",
    ].join("\n"),
    fragmentShader: [
      "uniform float uTime; uniform vec3 uAura;",
      "varying vec3 vN; varying vec3 vW; varying vec3 vP; varying vec3 vC;",
      "varying float vSeed; varying float vHeat; varying float vChurn;",
      "varying float vHov;",
      GLSL_NOISE,
      "void main(){",
      "  vec3 n = normalize(vN);",
      "  vec3 v = normalize(cameraPosition - vW);",
      "  float ndv = clamp(dot(n, v), 0.0, 1.0);",
      "  /* LIQUID SUN: the surface circulates around the axis while",
      "     domain-warped fbm churns through it — molten, flowing; newer",
      "     memories flow faster, and the hovered star surges */",
      "  float t = uTime * (0.06 + vChurn * 0.10 + vHov * 0.22) + vSeed * 19.0;",
      "  float ca = cos(t * 0.35), sa = sin(t * 0.35);",
      "  vec3 rp = vec3(vP.x * ca - vP.z * sa, vP.y, vP.x * sa + vP.z * ca);",
      "  vec3 p = rp * 2.4 + vSeed * 7.0;",
      "  vec3 q = vec3(fbm(p + vec3(t, 0.0, 0.0)),",
      "                fbm(p + vec3(5.2, t * 0.8, 1.3)),",
      "                fbm(p + vec3(1.7, 9.2, -t * 0.6)));",
      "  float sw = fbm(p + 1.9 * q + 0.5 * vec3(0.0, sin(t * 0.7), 0.0));",
      "  /* palette: shadowed body -> project colour -> white-hot */",
      "  vec3 deep = vC * 0.22;",
      "  vec3 hot = mix(vC, vec3(1.0, 0.97, 0.9), 0.8);",
      "  vec3 col = mix(deep, vC, smoothstep(0.3, 0.75, sw));",
      "  col += hot * pow(max(sw - 0.45, 0.0) * 1.8, 2.0) * (0.6 + vHeat);",
      "  /* the heart: importance burns at the centre of the disc */",
      "  col += hot * pow(ndv, 3.0) * (0.25 + 0.85 * vHeat);",
      "  /* fresnel rim: silhouette in project colour kissed by the aura */",
      "  float rim = pow(1.0 - ndv, 2.6);",
      "  col += mix(vC, uAura, 0.3) * rim * (1.7 + vHov * 0.9);",
      "  col *= 1.0 + vHov * 0.28;",
      "  gl_FragColor = vec4(col, 1.0); }",
    ].join("\n"),
  });

  /* v2.5: one cold material for files (octahedra) and folders (spheres).
     Faceted per face, lit from the viewer, fresnel rim, per-instance colour. */
  var crystalGeo = new THREE.OctahedronGeometry(1, 0);
  var planetGeo = new THREE.SphereGeometry(1, 12, 9);
  var crystalMat = new THREE.ShaderMaterial({
    uniforms: { uRim: { value: ICE_RIM.clone() } },
    vertexShader: [
      "attribute float aHov;",
      "varying vec3 vN; varying vec3 vW; varying vec3 vC; varying float vHov;",
      "void main(){",
      "  vC = instanceColor; vHov = aHov;",
      "  vec4 wp = modelMatrix * instanceMatrix * vec4(position, 1.0);",
      "  vN = normalize(mat3(modelMatrix) * mat3(instanceMatrix) * normal);",
      "  vW = wp.xyz;",
      "  gl_Position = projectionMatrix * viewMatrix * wp; }",
    ].join("\n"),
    fragmentShader: [
      "uniform vec3 uRim;",
      "varying vec3 vN; varying vec3 vW; varying vec3 vC; varying float vHov;",
      "void main(){",
      "  vec3 n = normalize(vN);",
      "  vec3 v = normalize(cameraPosition - vW);",
      "  float ndv = clamp(dot(n, v), 0.0, 1.0);",
      "  vec3 col = vC * (0.42 + 0.58 * ndv);",
      "  float rim = pow(1.0 - ndv, 3.0);",
      "  col += uRim * rim * (0.9 + vHov * 0.6);",
      "  col *= 1.0 + vHov * 0.25;",
      "  gl_FragColor = vec4(col, 1.0); }",
    ].join("\n"),
  });

  /* SYNAPSES: each link is a gentle curve (a dendrite, not a rod), its
     colour running from one star to the other, carrying a pulse of energy.
     At rest the flow is a faint, slow murmur; the links of a hovered or
     chosen star fire, brighter and faster, racing away from it. All on the
     GPU: aT is the distance along the link, aPhase staggers the pulses,
     aLit lights a link and its sign says which way the pulse runs. */
  var SEG = 10;
  var FLAT_MODES = ["flow_down", "flow_right", "web", "tree"];
  var synapseMat = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    uniforms: { uTime: { value: 0 }, uFlow: { value: reduced ? 0 : 1 } },
    vertexShader: [
      "attribute float aT; attribute float aPhase; attribute float aLit;",
      "varying vec3 vC; varying float vT; varying float vPhase; varying float vLit;",
      "void main(){",
      "  vC = color; vT = aT; vPhase = aPhase; vLit = aLit;",
      "  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }",
    ].join("\n"),
    fragmentShader: [
      "uniform float uTime; uniform float uFlow;",
      "varying vec3 vC; varying float vT; varying float vPhase; varying float vLit;",
      "void main(){",
      "  float lit = abs(vLit);",
      "  float t = vLit < 0.0 ? 1.0 - vT : vT;",
      "  float x = fract(t - uTime * (0.07 + 0.5 * lit) + vPhase);",
      "  float pulse = exp(-pow((x - 0.5) * (14.0 - 4.0 * lit), 2.0));",
      "  float k = 1.0 + pulse * (0.9 + 2.6 * lit) * uFlow;",
      "  gl_FragColor = vec4(vC * k, 1.0); }",
    ].join("\n"),
    vertexColors: true,
  });

  /* RIPPLES: a ring of light spreads from a star when it is touched (hover:
     a soft breath; select: a full wave). Sprites face the camera by
     themselves; only a handful live at once. */
  var rippleTex = (function () {
    var size = 256, cv = document.createElement("canvas");
    cv.width = cv.height = size;
    var g = cv.getContext("2d");
    var grad = g.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
    grad.addColorStop(0, "rgba(255,255,255,0)");
    grad.addColorStop(0.62, "rgba(255,255,255,0)");
    grad.addColorStop(0.8, "rgba(255,255,255,0.9)");
    grad.addColorStop(0.88, "rgba(255,255,255,0.3)");
    grad.addColorStop(1, "rgba(255,255,255,0)");
    g.fillStyle = grad;
    g.fillRect(0, 0, size, size);
    var tex = new THREE.CanvasTexture(cv);
    tex.colorSpace = THREE.SRGBColorSpace;
    return tex;
  })();
  var ripples = [];
  function spawnRipple(n, strength, dur) {
    if (reduced || !n) return;
    while (ripples.length >= 6) {
      var old = ripples.shift();
      graph.group.remove(old.s);
      old.s.material.dispose();
    }
    var col = n.kind === "file" ? ICE.clone() : n.kind === "folder" ? SLATE.clone()
            : nodeColor(n, new THREE.Color());
    var mat = new THREE.SpriteMaterial({
      map: rippleTex, color: col.lerp(STAR, 0.35), transparent: true, opacity: strength,
      blending: THREE.AdditiveBlending, depthWrite: false });
    var sp = new THREE.Sprite(mat);
    sp.position.set(n.x || 0, n.y || 0, n.z || 0);
    sp.renderOrder = 3;
    graph.group.add(sp);
    var r = n._r || 4;
    ripples.push({ s: sp, t0: performance.now(), dur: dur, a: strength,
                   r0: r * 2, r1: r * (strength >= 1 ? 16 : 7) });
    N.requestRender();
  }
  function stepRipples(now) {
    for (var i = ripples.length - 1; i >= 0; i--) {
      var rp = ripples[i], t = (now - rp.t0) / rp.dur;
      if (t >= 1) {
        graph.group.remove(rp.s);
        rp.s.material.dispose();
        ripples.splice(i, 1);
        continue;
      }
      var e = 1 - Math.pow(1 - t, 3);
      rp.s.scale.setScalar(rp.r0 + (rp.r1 - rp.r0) * e);
      rp.s.material.opacity = rp.a * (1 - t) * (1 - t);
    }
  }
  function clearRipples() {
    ripples.forEach(function (rp) { graph.group.remove(rp.s); rp.s.material.dispose(); });
    ripples = [];
  }

  /* THE CORTEX: in the Brain layout the memories live inside a brain, so
     the brain is drawn: faint points on folded hemispheres, a cerebellum
     and a brainstem (layouts.js cortexPoints, unit size, scaled here).
     Thought washes over it in slow bands of light. It fades in and out
     with the layout; the round shell gives way to it. */
  var CORTEX = { alpha: 0, target: 0 };
  var cortexGroup = null, cortex = null, glass = null, glassMat = null;
  var cortexSample = null, brainScale = 300;

  /* pixels per world unit at distance 1: a point that should be k world
     units wide is k * this / depth pixels. Tracks resize and pixel ratio. */
  function pxPerUnit() {
    var h = renderer.getDrawingBufferSize(new THREE.Vector2()).y || 1;
    return h / (2 * Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2));
  }

  /* the same slow waves of thought run over the glass and the sparks */
  var BRAIN_WAVES = [
    "float brainWave(vec3 p, float t, out float w1){",
    "  w1 = exp(-pow(sin(p.z * 4.2 + p.y * 2.2 - t * 0.32) * 1.7, 2.0));",
    "  float w2 = exp(-pow(sin(p.x * 5.0 - p.y * 3.6 + t * 0.23 + 1.7) * 1.9, 2.0));",
    "  return max(w1, w2 * 0.75); }",
  ].join("\n");

  var cortexMat = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    uniforms: { uTime: { value: 0 }, uAlpha: { value: 0 },
                uPR: { value: renderer.getPixelRatio() },
                uScale: { value: brainScale }, uPxPerUnit: { value: pxPerUnit() } },
    vertexShader: [
      "attribute float aShade; attribute float aSeed;",
      "uniform float uTime; uniform float uPR; uniform float uScale; uniform float uPxPerUnit;",
      "varying float vA; varying vec3 vC;",
      BRAIN_WAVES,
      "void main(){",
      "  vec3 p = position;",
      "  float w1; float wave = brainWave(p, uTime, w1);",
      "  float tw = 0.7 + 0.3 * sin(uTime * (0.6 + aSeed) + aSeed * 40.0);",
      "  vA = aShade * tw * (0.35 + 0.65 * wave);",
      "  vC = mix(vec3(0.5, 0.62, 1.0), vec3(0.8, 0.6, 1.0), aSeed)",
      "     + vec3(0.45, 0.36, 0.16) * w1;",
      "  vec4 mv = modelViewMatrix * vec4(p, 1.0);",
      /* a world size (a fraction of the brain), so framing never shrinks
         the sparks to nothing; at least ~1.3 px so they always show */
      "  gl_PointSize = max(1.3 * uPR, (0.0042 + 0.0058 * aShade) * uScale * uPxPerUnit / -mv.z);",
      "  gl_Position = projectionMatrix * mv; }",
    ].join("\n"),
    fragmentShader: [
      "uniform float uAlpha; varying float vA; varying vec3 vC;",
      "void main(){",
      "  float a = smoothstep(0.5, 0.05, length(gl_PointCoord - 0.5)) * vA * uAlpha;",
      "  gl_FragColor = vec4(vC * a, a); }",
    ].join("\n"),
  });

  /* the glass skin: lit at its limb (fresnel) and along its folds, which
     are drawn per pixel from the raw noise fields (layouts.js foldOf), so
     they stay thin lines at any distance and never shimmer */
  function makeGlassMat(F) {
    function f(v) { return Number(v).toFixed(4); }
    return new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
      uniforms: { uTime: { value: 0 }, uAlpha: { value: 0 } },
      vertexShader: [
        "attribute vec4 aFold;",
        "varying vec3 vN; varying vec3 vW; varying vec3 vL; varying vec4 vF;",
        "void main(){",
        "  vL = position; vF = aFold;",
        "  vN = normalize(mat3(modelMatrix) * normal);",
        "  vec4 wp = modelMatrix * vec4(position, 1.0);",
        "  vW = wp.xyz;",
        "  gl_Position = projectionMatrix * viewMatrix * wp; }",
      ].join("\n"),
      fragmentShader: [
        "uniform float uTime; uniform float uAlpha;",
        "varying vec3 vN; varying vec3 vW; varying vec3 vL; varying vec4 vF;",
        BRAIN_WAVES,
        "float gv(float n, float w){",
        "  float we = max(w, fwidth(n) * 1.25);",
        "  return exp(-(n * n) / (we * we)) * (w / we); }",
        "void main(){",
        "  vec3 v = normalize(cameraPosition - vW);",
        "  float limb = pow(1.0 - abs(dot(normalize(vN), v)), 2.4);",
        "  float fold = min(1.0, gv(vF.x, " + f(F.w1) + ") + " + f(F.k2) + " * gv(vF.y, " + f(F.w2) + ")",
        "             + " + f(F.ks) + " * gv(vF.z, " + f(F.ws) + ") * vF.w);",
        "  float w1; float wave = brainWave(vL, uTime, w1);",
        "  vec3 col = mix(vec3(0.34, 0.47, 0.96), vec3(0.68, 0.5, 1.0),",
        "                 clamp(vL.y * 1.2 + 0.45, 0.0, 1.0));",
        "  col = mix(col, vec3(1.0, 0.8, 0.52), w1 * 0.4);",
        "  float a = limb * 0.7 + fold * (0.14 + 0.45 * limb) + wave * (0.04 + 0.22 * limb);",
        "  a *= uAlpha;",
        "  gl_FragColor = vec4(col * a, a); }",
      ].join("\n"),
    });
  }

  function ensureCortex() {
    var L = window.NebulaLayouts;
    if (cortexGroup || !L || !L.cortexPoints || !L.brainSurface) return;
    cortexGroup = new THREE.Group();
    cortexGroup.visible = false;
    /* the glass skin: one mesh, both hemispheres, cerebellum and stem */
    var sk = L.brainSurface(1);
    var sg = new THREE.BufferGeometry();
    sg.setAttribute("position", new THREE.BufferAttribute(sk.pos, 3));
    sg.setAttribute("aFold", new THREE.BufferAttribute(sk.fold, 4));
    sg.setIndex(new THREE.BufferAttribute(sk.index, 1));
    sg.computeVertexNormals();
    glassMat = makeGlassMat(L.FOLD);
    glass = new THREE.Mesh(sg, glassMat);
    glass.frustumCulled = false;
    glass.renderOrder = 1;
    cortexGroup.add(glass);
    /* a thin sample of the skin, so framing can hold the whole brain */
    cortexSample = [];
    for (var k = 0; k < sk.pos.length; k += 3 * 97) {
      cortexSample.push(sk.pos[k], sk.pos[k + 1], sk.pos[k + 2]);
    }
    /* sparks on the crowns of the folds */
    var data = L.cortexPoints(3200);
    var geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(data.pos, 3));
    geo.setAttribute("aShade", new THREE.BufferAttribute(data.shade, 1));
    var seeds = new Float32Array(data.shade.length);
    for (var i = 0; i < seeds.length; i++) seeds[i] = (i * 0.61803) % 1;
    geo.setAttribute("aSeed", new THREE.BufferAttribute(seeds, 1));
    cortex = new THREE.Points(geo, cortexMat);
    cortex.frustumCulled = false;
    cortex.renderOrder = 1;
    cortexGroup.add(cortex);
    scene.add(cortexGroup);
  }

  /* a stable 0..1 number for a string: the same link always bends the same way */
  function hash01(str) {
    var h = 2166136261;
    for (var i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); }
    return ((h >>> 0) % 100000) / 100000;
  }

  function radiusOf(n) {
    if (n.kind === "file") return Math.min(1.6 + 1.1 * Math.sqrt(n.degree || 0), 4.2);
    if (n.kind === "folder") return Math.min(4 + 1.4 * Math.sqrt(n.file_count || 0), 11);
    // capped: no star may dwarf the world, however mighty its degree
    // (a consolidated belief hub once ate the whole view)
    return Math.min(
      (3 + Math.sqrt(n.degree || 0) * 2.6 + (n.importance || 3) * 0.8) * 0.62,
      8.6);
  }
  function isMemory(n) { return !n.kind || n.kind === "memory"; }

  /* old summaries may still carry LLM throat-clearing; the server repairs
     them during sleep, this covers the ones it hasn't met yet */
  function cleanLabel(s) {
    var out = String(s || "").replace(
      /^(?:okay[,.!]?\s+|sure[,.!]?\s+)?(?:here(?:'s| is| are)\s+(?:a |the |your )?(?:concise |brief |short )?(?:summary|distillation|overview|breakdown|synopsis)[^:\n]{0,80}[:.]\s*)+/i,
      "").trim();
    return out || String(s || "");
  }

  /* ------------------------------------------------------- hierarchy
     Every project has a HEAD — its most connected memory, the sun of
     that little system. Everything else in the project takes a level
     from BFS distance to the head: direct children ring close, deeper
     descendants ring further out. Heads are visibly bigger, burn
     hotter, and repel each other harder so the projects read as
     separate systems inside the one space. */
  function computeHierarchy() {
    var byProject = {};
    graph.nodes.forEach(function (n) {
      n._head = null; n._parent = null; n._level = 1;
      if (!isMemory(n)) { n._level = 3; return; }
      (byProject[n.project || ""] = byProject[n.project || ""] || []).push(n);
    });
    var adj = {};
    graph.links.forEach(function (l) {
      (adj[l.source.id] = adj[l.source.id] || []).push(l.target);
      (adj[l.target.id] = adj[l.target.id] || []).push(l.source);
    });
    Object.keys(byProject).forEach(function (proj) {
      var members = byProject[proj];
      /* a belief IS a project's distilled truth: if any exist, the
         strongest belief takes the head, however mighty a raw session */
      var pool = members.filter(function (n) { return n.type === "belief"; });
      if (!pool.length) pool = members;
      var head = pool[0];
      pool.forEach(function (n) {
        if ((n.degree || 0) > (head.degree || 0)
            || ((n.degree || 0) === (head.degree || 0)
                && (n.importance || 0) > (head.importance || 0))) head = n;
      });
      /* BFS from the head, staying inside the project */
      var seen = {}; seen[head.id] = true;
      head._head = head; head._level = 0;
      var frontier = [head], level = 0;
      while (frontier.length) {
        level++;
        var next = [];
        frontier.forEach(function (n) {
          (adj[n.id] || []).forEach(function (m) {
            if (seen[m.id] || m.project !== proj || !isMemory(m)) return;
            seen[m.id] = true;
            m._head = head; m._level = level; m._parent = n;
            next.push(m);
          });
        });
        frontier = next;
      }
      members.forEach(function (n) {        // unlinked members still belong
        if (!n._head) { n._head = head; n._level = 2; }
      });
    });
  }
  function churnOf(n) {              // newer memories churn faster
    var t = Date.parse(n.timestamp || "") || 0;
    if (!t) return 0.4;
    var days = (Date.now() - t) / 864e5;
    return Math.max(0, Math.min(1, 1 - days / 60));
  }

  function disposeGraph() {
    ["mesh", "glow", "lines", "pulses", "files", "folders"].forEach(function (k) {
      var o = graph[k];
      if (!o) return;
      graph.group.remove(o);
      if (o.geometry) o.geometry.dispose();
      if (o.material && o.material !== nodeMat && o.material !== synapseMat
          && o.material !== crystalMat) {
        o.material.dispose();
      }
      graph[k] = null;
    });
  }

  /* one InstancedMesh per new species: same cold material, its own
     geometry clone so each carries its own aHov attribute */
  function buildCrystals(list, geo, baseColor, tint) {
    if (!list.length) return null;
    var g = geo.clone();
    var hovs = new Float32Array(list.length);
    var hovAttr = new THREE.InstancedBufferAttribute(hovs, 1);
    hovAttr.setUsage(THREE.DynamicDrawUsage);
    g.setAttribute("aHov", hovAttr);
    var m = new THREE.InstancedMesh(g, crystalMat, list.length);
    m.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    m.frustumCulled = false;
    var c = new THREE.Color();
    list.forEach(function (n, i) {
      n._i = i;
      n._r = radiusOf(n);
      m.setColorAt(i, c.copy(baseColor).multiplyScalar(tint));
    });
    m.instanceColor.needsUpdate = true;
    graph.group.add(m);
    return m;
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
    /* three species, three meshes: every mesh is indexed by its OWN list,
       so n._i is the index within that species, never within graph.nodes */
    graph.memNodes = graph.nodes.filter(isMemory);
    graph.fileNodes = graph.nodes.filter(function (n) { return n.kind === "file"; });
    graph.folderNodes = graph.nodes.filter(function (n) { return n.kind === "folder"; });
    var ids = graph.byId;
    graph.links = (data.edges || [])
      .filter(function (e) { return ids[e.src] && ids[e.dst]; })
      .map(function (e) {
        // resolve to node objects up front: every consumer (buffers, pulses,
        // paint) can rely on .source.x whether or not the sim ever runs
        return { source: ids[e.src], target: ids[e.dst],
                 w: e.w || 0, kinds: e.kinds,
                 _phase: hash01(e.src + "|" + e.dst),
                 _twist: hash01(e.dst + "|" + e.src) * Math.PI * 2 };
      });
    graph.nodes.forEach(function (n) { n._side = hash01(n.id) < 0.5 ? -1 : 1; });

    graph.hoverId = null; graph.hoverLink = -1; graph.linkFocus = -1;
    if (!framing.userMoved) framing.pending = true;
    clearRipples();
    tween = null;
    disposeGraph();
    if (!graph.nodes.length) { fitSpace(); N.requestRender(); return; }

    computeHierarchy();

    /* plasma stars: one InstancedMesh + per-instance seed/heat/churn/hover */
    var count = graph.memNodes.length;
    /* a folder-only view has no stars at all: never build an empty
       InstancedMesh, and never leave a null glow for the loops to find */
    if (count) {
      var geo = nodeGeo.clone();
      var seeds = new Float32Array(count), heats = new Float32Array(count),
          churns = new Float32Array(count), hovs = new Float32Array(count);
      graph.memNodes.forEach(function (n, i) {
        n._i = i;
        /* rank is also SIZE, unmissably: head 1.85x, then shrinking rings */
        n._r = radiusOf(n) * (n._head === n ? 1.85
          : [1, 1.0, 0.84, 0.72][Math.min(n._level, 3)] || 0.72);
        seeds[i] = (i * 0.61803) % 1;
        heats[i] = Math.max(0, Math.min(1,
          ((n.importance || 3) - 1) / 4 + (n._head === n ? 0.3 : 0)));
        churns[i] = churnOf(n);
      });
      geo.setAttribute("aSeed", new THREE.InstancedBufferAttribute(seeds, 1));
      geo.setAttribute("aHeat", new THREE.InstancedBufferAttribute(heats, 1));
      geo.setAttribute("aChurn", new THREE.InstancedBufferAttribute(churns, 1));
      var hovAttr = new THREE.InstancedBufferAttribute(hovs, 1);
      hovAttr.setUsage(THREE.DynamicDrawUsage);
      geo.setAttribute("aHov", hovAttr);
      var mesh = new THREE.InstancedMesh(geo, nodeMat, count);
      mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      mesh.frustumCulled = false;    // the constellation is always on stage;
                                     // culling only ever causes vanishing bugs
      var c = new THREE.Color();
      graph.memNodes.forEach(function (n, i) { mesh.setColorAt(i, nodeColor(n, c)); });
      mesh.instanceColor.needsUpdate = true;
      graph.mesh = mesh;
      graph.group.add(mesh);

      /* halos: additive point sprites behind the stars */
      var gpos = new Float32Array(count * 3);
      var gcol = new Float32Array(count * 3);
      var gsize = new Float32Array(count);
      graph.memNodes.forEach(function (n, i) {
        nodeColor(n, c);
        gcol[i * 3] = c.r; gcol[i * 3 + 1] = c.g; gcol[i * 3 + 2] = c.b;
        gsize[i] = n._r * 7.5;
      });
      var ggeo = new THREE.BufferGeometry();
      ggeo.setAttribute("position", new THREE.BufferAttribute(gpos, 3));
      ggeo.setAttribute("color", new THREE.BufferAttribute(gcol, 3));
      ggeo.setAttribute("psize", new THREE.BufferAttribute(gsize, 1));
      ggeo.setAttribute("aSeed", new THREE.BufferAttribute(seeds, 1));
      graph.glow = new THREE.Points(ggeo, pointsMaterial(0.3, true));
      graph.glow.frustumCulled = false;
      graph.group.add(graph.glow);
    }

    /* the workspace layer: ice crystals for files, dim planets for folders */
    graph.files = buildCrystals(graph.fileNodes, crystalGeo, ICE, 1.0);
    graph.folders = buildCrystals(graph.folderNodes, planetGeo, SLATE, 0.75);

    /* synapses: SEG short segments per link, so each one can curve */
    var nv = graph.links.length * SEG * 2;
    var lgeo = new THREE.BufferGeometry();
    lgeo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(nv * 3), 3));
    lgeo.setAttribute("color", new THREE.BufferAttribute(new Float32Array(nv * 3), 3));
    var aT = new Float32Array(nv), aPh = new Float32Array(nv);
    graph.links.forEach(function (l, i) {
      for (var sg = 0; sg < SEG; sg++) {
        var v = (i * SEG + sg) * 2;
        aT[v] = sg / SEG;
        aT[v + 1] = (sg + 1) / SEG;
        aPh[v] = aPh[v + 1] = l._phase;
      }
    });
    lgeo.setAttribute("aT", new THREE.BufferAttribute(aT, 1));
    lgeo.setAttribute("aPhase", new THREE.BufferAttribute(aPh, 1));
    var litAttr = new THREE.BufferAttribute(new Float32Array(nv), 1);
    litAttr.setUsage(THREE.DynamicDrawUsage);
    lgeo.setAttribute("aLit", litAttr);
    graph.lines = new THREE.LineSegments(lgeo, synapseMat);
    graph.lines.frustumCulled = false;
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
    graph.pulses.frustumCulled = false;
    graph.group.add(graph.pulses);

    startSim();
    updateBuffers();     // matrices valid IMMEDIATELY — a star that has not
                         // simulated yet must still render and pick
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

    /* many projects must read as distinct systems: each project is assigned
       its own direction in the Space (golden-angle spiral, deterministic
       by sorted order) and its head is drawn toward that anchor. Members
       follow their head via the ring force below. */
    var projectList = [];
    graph.nodes.forEach(function (n) {
      var p = n.project || "";
      if (projectList.indexOf(p) < 0) projectList.push(p);
    });
    projectList.sort();
    var anchors = {};
    var GA = Math.PI * (3 - Math.sqrt(5));
    projectList.forEach(function (p, i) {
      if (projectList.length === 1) { anchors[p] = null; return; }
      var t = (i + 0.5) / projectList.length;
      var y = 1 - 2 * t, r = Math.sqrt(Math.max(0, 1 - y * y));
      anchors[p] = { x: Math.cos(GA * i) * r, y: y * 0.7,
                     z: Math.sin(GA * i) * r };
    });
    var anchorR = Math.max(120, 40 * Math.sqrt(graph.nodes.length));

    function anchorForce(alpha) {
      var k = 0.05 * alpha;
      for (var i = 0; i < graph.nodes.length; i++) {
        var n = graph.nodes[i];
        var a = anchors[n.project || ""];
        if (!a) continue;
        var kk = (n._head === n || n.kind === "folder") ? k * 2.2
               : n.kind === "file" ? k * 0.2 : k * 0.35;
        n.vx += (a.x * anchorR - n.x) * kk;
        n.vy += (a.y * anchorR - n.y) * kk;
        n.vz += (a.z * anchorR - n.z) * kk;
      }
    }

    /* the hierarchy force: children seek a ring around their project's
       head — level 1 close, deeper levels further out */
    function hierarchyForce(alpha) {
      var k = 0.10 * alpha;
      for (var i = 0; i < graph.nodes.length; i++) {
        var n = graph.nodes[i], h = n._head;
        if (!h || h === n) continue;
        var dx = n.x - h.x, dy = n.y - h.y, dz = n.z - h.z;
        var d = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1;
        var ring = 30 + Math.min(n._level, 4) * 26;
        var f = (ring - d) / d * k;
        n.vx += dx * f; n.vy += dy * f; n.vz += dz * f;
        h.vx -= dx * f * 0.15; h.vy -= dy * f * 0.15; h.vz -= dz * f * 0.15;
      }
    }
    /* which shape are we in? layouts.js may have failed to load, in which
       case every mode falls back to organic and nothing below runs. */
    var L = window.NebulaLayouts;
    var mode = (L && L.MODES.indexOf(layoutMode) >= 0) ? layoutMode : "organic";
    var ringOf = L ? L.ringOf : function () { return 2; };
    var planeR = Math.max(140, 34 * Math.sqrt(graph.nodes.length));
    var centres = L ? L.projectCentres(graph.nodes, planeR) : {};

    /* tree is the one mode with no physics: every position is fixed from
       the radial tidy tree and there is no simulation at all. Every reader
       of graph.sim (animateBody, dragMove) already guards on it. */
    CORTEX.target = mode === "brain" ? 1 : 0;
    if (reduced) CORTEX.alpha = CORTEX.target;
    if (mode === "tree") {
      var tp = L.treePositions(graph.nodes, graph.links,
        Math.max(48, 26 + 900 / Math.max(graph.nodes.length, 1) * 0.6));
      /* no physics, but no jump either: every star glides to its place */
      var tw = { t0: performance.now(), dur: 1100, list: [] };
      graph.nodes.forEach(function (n) {
        var p = tp[n.id];
        if (!p) return;
        tw.list.push({ n: n, x0: n.x || 0, y0: n.y || 0, z0: n.z || 0,
                       x1: p.x, y1: p.y, z1: p.z });
        n.fx = p.x; n.fy = p.y; n.fz = p.z;
        n.vx = n.vy = n.vz = 0;
      });
      graph.sim = null;
      if (reduced) {
        tw.list.forEach(function (e) { e.n.x = e.x1; e.n.y = e.y1; e.n.z = e.z1; });
        fitSpace(true);
      } else {
        tween = tw;
      }
      updateBuffers();
      return;
    }

    function orbForce(alpha) {                 // everything on one sphere
      var k = 0.14 * alpha, R = Math.max(170, 46 * Math.sqrt(graph.nodes.length));
      for (var i = 0; i < graph.nodes.length; i++) {
        var n = graph.nodes[i];
        var target = n.kind === "folder" ? R * 1.12 : n.kind === "file" ? R * 0.86 : R;
        var d = Math.sqrt(n.x * n.x + n.y * n.y + n.z * n.z) || 1;
        var f = (target - d) / d * k;
        n.vx += n.x * f; n.vy += n.y * f; n.vz += n.z * f;
      }
    }
    function flowForce(axis) {                 // rank layers along one axis
      var gap = Math.max(46, planeR * 0.28), top = 3 * gap;
      return function (alpha) {
        var k = 0.22 * alpha, kz = 0.12 * alpha;
        for (var i = 0; i < graph.nodes.length; i++) {
          var n = graph.nodes[i];
          var t = top - ringOf(n) * gap;
          var c = centres[n.project || ""] || { x: 0, y: 0, z: 0 };
          if (axis === "y") {
            n.vy += (t - n.y) * k;
            n.vx += (c.x * 0.9 - n.x) * 0.02 * alpha;
          } else {
            n.vx += (-t - n.x) * k;
            n.vy += (c.y * 0.9 - n.y) * 0.02 * alpha;
          }
          n.vz += (0 - n.z) * kz;
        }
      };
    }
    function webForce(alpha) {                 // concentric rings, flat
      var k = 0.2 * alpha, kz = 0.3 * alpha;
      for (var i = 0; i < graph.nodes.length; i++) {
        var n = graph.nodes[i];
        var c = centres[n.project || ""] || { x: 0, y: 0, z: 0 };
        var ring = 22 + ringOf(n) * 42;
        var dx = n.x - c.x, dy = n.y - c.y;
        var d = Math.sqrt(dx * dx + dy * dy) || 1;
        var f = (ring - d) / d * k;
        n.vx += dx * f; n.vy += dy * f;
        n.vz += (0 - n.z) * kz;
      }
    }

    /* the Brain: each project's lobe sits under the cortex of a hemisphere
       (layouts.js brainLobes). Memories settle at a depth by rank, heads
       near the surface; files and folders just outside it. */
    var brainR = Math.max(150, 25 * Math.sqrt(graph.nodes.length));
    var lobes = (mode === "brain" && L.brainLobes) ? L.brainLobes(graph.nodes, brainR) : {};
    if (mode === "brain") {
      /* a project spread over both hemispheres splits by family (a head
         and everything under it), each family to the lighter side, so
         related memories stay together and few synapses cross the fissure.
         A family too big for one side (over 15% of its project) splits
         evenly first; the smaller ones then even the sides out. */
      var fam = {}, famList = [], famLoad = {}, projN = {};
      graph.nodes.forEach(function (n) {
        var lb = lobes[n.project || ""];
        if (!lb || lb.side !== 0) return;
        var pj = n.project || "";
        projN[pj] = (projN[pj] || 0) + 1;
        var key = pj + "|" + (n._head ? n._head.id : n.id);
        if (!fam[key]) { fam[key] = []; famList.push(key); }
        fam[key].push(n);
      });
      famList.sort(function (a, b) {
        return fam[b].length - fam[a].length || (a < b ? -1 : 1);
      });
      famList.forEach(function (key) {
        var pj = key.slice(0, key.indexOf("|"));
        var ld = famLoad[pj] || (famLoad[pj] = [0, 0]);
        if (fam[key].length > 0.15 * projN[pj]) {
          fam[key].forEach(function (n) {
            var sh = hash01(n.id) < 0.5 ? 0 : 1;
            ld[sh]++;
            n._side = sh ? 1 : -1;
          });
          return;
        }
        var sd = ld[0] <= ld[1] ? 0 : 1;
        ld[sd] += fam[key].length;
        fam[key].forEach(function (n) { n._side = sd ? 1 : -1; });
      });
      ensureCortex();
      brainScale = brainR;
      if (cortexGroup) cortexGroup.scale.setScalar(brainR);
      cortexMat.uniforms.uScale.value = brainR;
    }
    function brainForce(alpha) {
      var B = L.BRAIN, k = 0.16 * alpha;
      for (var i = 0; i < graph.nodes.length; i++) {
        var n = graph.nodes[i], lobe = lobes[n.project || ""], side, goal = null;
        if (lobe && lobe.side === 0) { side = n._side; goal = side < 0 ? lobe.left : lobe.right; }
        else if (lobe) { side = lobe.side; goal = lobe; }
        else side = n.x < 0 ? -1 : 1;
        var cx = L.hemiCentre(side) * brainR;
        var sq = (n.x - cx) * side < 0 ? B.medial : 1;    // the flat inner half
        var lx = (n.x - cx) / (B.rx * brainR * sq), ly = n.y / (B.ry * brainR),
            lz = n.z / (B.rz * brainR);
        var d = Math.sqrt(lx * lx + ly * ly + lz * lz) || 1e-3;
        var want = n.kind === "folder" ? 1.06 : n.kind === "file" ? 1.0
                 : n._head === n ? 0.94 : 0.9 - 0.07 * Math.min(n._level || 1, 3);
        /* the skin is a wall: leaving it costs four times what drifting
           inward does, so a crowded lobe deepens instead of spilling out */
        var f = (want / d - 1) * k * (d > want ? 4 : 1);
        n.vx += (n.x - cx) * f; n.vy += n.y * f; n.vz += n.z * f;
        if (goal) {
          var kl = (n._head === n ? 0.2 : 0.11) * alpha;
          n.vx += (goal.x - n.x) * kl; n.vy += (goal.y - n.y) * kl; n.vz += (goal.z - n.z) * kl;
        }
      }
    }

    var link = window.d3.forceLink(graph.links)
        .id(function (d) { return d.id; })
        .distance(function (l) {
          // sources orbit their belief tightly — a little solar system
          if (l.kinds && l.kinds.indexOf("in_folder") >= 0) return 16;
          if (l.kinds && l.kinds.indexOf("file_ref") >= 0) return 34 + 30 * (1 - (l.w || 0));
          if (l.kinds && l.kinds.indexOf("derived_from") >= 0) return 22;
          return 26 + 40 * (1 - (l.w || 0));
        });
    /* forceLink().strength(null) is not valid: only the shaped modes get a
       fixed link strength, organic keeps d3's own degree-based default. */
    /* in the Brain, synapses are weak springs and the lobes hold the
       shape; a file still sits beside the memories that name it */
    if (mode === "brain") {
      link.strength(function (l) {
        if (l.kinds && l.kinds.indexOf("in_folder") >= 0) return 0.2;
        if (l.kinds && l.kinds.indexOf("file_ref") >= 0) return 0.12;
        return 0.04;
      });
    } else if (mode !== "organic") link.strength(0.12);
    var sim = window.d3.forceSimulation(graph.nodes, 3)
      .force("link", link);
    if (mode === "organic") {
      sim.force("charge", window.d3.forceManyBody()
          .strength(function (n) {
            return n._head === n ? -520
                 : n.kind === "folder" ? -260
                 : n.kind === "file" ? -30 : -110;
          }))
         .force("hierarchy", hierarchyForce)
         .force("anchor", anchorForce);
    } else if (mode === "orb") {
      sim.force("charge", window.d3.forceManyBody().strength(-70))
         .force("anchor", anchorForce)
         .force("orb", orbForce);
    } else if (mode === "flow_down") {
      sim.force("charge", window.d3.forceManyBody().strength(-60))
         .force("flow", flowForce("y"));
    } else if (mode === "flow_right") {
      sim.force("charge", window.d3.forceManyBody().strength(-60))
         .force("flow", flowForce("x"));
    } else if (mode === "web") {
      sim.force("charge", window.d3.forceManyBody().strength(-40))
         .force("web", webForce);
    } else if (mode === "brain") {
      sim.force("charge", window.d3.forceManyBody().strength(-55))
         .force("brain", brainForce);
    }
    /* center goes on LAST so organic keeps the exact force order it had
       before the layout picker existed. The Brain centres itself: a busy
       hemisphere must not drag the other across the fissure. */
    if (mode !== "brain") sim.force("center", window.d3.forceCenter(0, 0, 0));
    graph.sim = sim.stop();
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
    if (!graph.mesh && !graph.files && !graph.folders) return;
    var m = new THREE.Matrix4(), q = new THREE.Quaternion(),
        s = new THREE.Vector3(), p = new THREE.Vector3();
    if (graph.mesh) {
      graph.memNodes.forEach(function (n, i) {
        /* NaN is contagious: one bad position would silently make the whole
           constellation unpickable (NaN bounding sphere). Quarantine it. */
        if (!isFinite(n.x) || !isFinite(n.y) || !isFinite(n.z)) {
          n.x = n.y = n.z = 0;
          n.vx = n.vy = n.vz = 0;
          N.lastError = "NaN position quarantined on " + n.id;
        }
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
      /* THE outer-stars-unclickable bug: InstancedMesh caches its bounding
         sphere at the FIRST raycast — while the newborn constellation is
         still a tight cluster — and never recomputes it. Rays aimed at
         stars that later drifted outward were rejected by that stale
         sphere before any per-star test ran. Invalidate it on every
         position update; the next pick recomputes an honest one. */
      graph.mesh.boundingSphere = null;
      graph.glow.geometry.attributes.position.needsUpdate = true;
      graph.glow.geometry.computeBoundingSphere();
    }

    function placeCrystals(meshObj, list) {
      if (!meshObj) return;
      list.forEach(function (n, i) {
        if (!isFinite(n.x) || !isFinite(n.y) || !isFinite(n.z)) {
          n.x = n.y = n.z = 0; n.vx = n.vy = n.vz = 0;
          N.lastError = "NaN position quarantined on " + n.id;
        }
        var r = n._r * (n.id === graph.selected ? 1.25 : n.id === graph.hoverId ? 1.12 : 1);
        p.set(n.x || 0, n.y || 0, n.z || 0);
        s.setScalar(r);
        m.compose(p, q, s);
        meshObj.setMatrixAt(i, m);
      });
      meshObj.instanceMatrix.needsUpdate = true;
      meshObj.boundingSphere = null;     // same stale-sphere lesson as the stars
    }
    placeCrystals(graph.files, graph.fileNodes);
    placeCrystals(graph.folders, graph.folderNodes);

    if (graph.lines) {
      var lp = graph.lines.geometry.attributes.position.array;
      var flat = FLAT_MODES.indexOf(layoutMode) >= 0;
      graph.links.forEach(function (l, i) {
        var a = l.source, b = l.target;
        var dx = b.x - a.x, dy = b.y - a.y, dz = b.z - a.z;
        var len = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1;
        dx /= len; dy /= len; dz /= len;
        /* the bend: perpendicular to the link, turned by the link's own
           twist (flat layouts keep it in their plane), a tenth of its
           length at most, never a loop */
        var px, py, pz, bend = Math.min(len * 0.12, 36);
        if (flat) {
          px = -dy; py = dx; pz = 0;
          if (l._twist > Math.PI) { px = -px; py = -py; }
        } else {
          var ux, uy, uz;
          if (Math.abs(dy) < 0.9) { ux = -dz; uy = 0; uz = dx; }      // dir x (0,1,0)
          else { ux = 0; uy = dz; uz = -dy; }                          // dir x (1,0,0)
          var ul = Math.sqrt(ux * ux + uy * uy + uz * uz) || 1;
          ux /= ul; uy /= ul; uz /= ul;
          var wx = dy * uz - dz * uy, wy = dz * ux - dx * uz, wz = dx * uy - dy * ux;
          var cs = Math.cos(l._twist), sn = Math.sin(l._twist);
          px = ux * cs + wx * sn; py = uy * cs + wy * sn; pz = uz * cs + wz * sn;
        }
        var mx = (a.x + b.x) / 2 + px * bend, my = (a.y + b.y) / 2 + py * bend,
            mz = (a.z + b.z) / 2 + pz * bend;
        l._cx = mx; l._cy = my; l._cz = mz;     // the pulses ride the same curve
        var qx = a.x, qy = a.y, qz = a.z, o = i * SEG * 6;
        for (var sg = 1; sg <= SEG; sg++) {
          var t = sg / SEG, u1 = 1 - t;
          var cx = u1 * u1 * a.x + 2 * u1 * t * mx + t * t * b.x;
          var cy = u1 * u1 * a.y + 2 * u1 * t * my + t * t * b.y;
          var cz = u1 * u1 * a.z + 2 * u1 * t * mz + t * t * b.z;
          lp[o] = qx; lp[o + 1] = qy; lp[o + 2] = qz;
          lp[o + 3] = cx; lp[o + 4] = cy; lp[o + 5] = cz;
          o += 6;
          qx = cx; qy = cy; qz = cz;
        }
      });
      graph.lines.geometry.attributes.position.needsUpdate = true;
      graph.lines.geometry.computeBoundingSphere();
    }
  }

  var _ca = new THREE.Color(), _cb = new THREE.Color();
  function linkEndColor(n, out) {
    if (n.kind === "file") return out.copy(FILELINK);
    if (n.kind === "folder") return out.copy(FOLDLINK);
    return nodeColor(n, out).lerp(FILAMENT, 0.45);
  }
  function paintLinks() {
    if (!graph.lines) return;
    var lc = graph.lines.geometry.attributes.color.array;
    var la = graph.lines.geometry.attributes.aLit.array;
    var hov = graph.hoverId, sel = graph.selected;
    var firing = !!(hov || sel || graph.linkFocus >= 0);
    graph.links.forEach(function (l, i) {
      var sid = l.source.id, tid = l.target.id;
      var onSel = sel && (sid === sel || tid === sel);
      var onHov = hov && (sid === hov || tid === hov);
      var lit = i === graph.linkFocus ? 1.0 : i === graph.hoverLink ? 0.8 : 0;
      var provenance = l.kinds && l.kinds.indexOf("derived_from") >= 0;
      var isFile = l.kinds && l.kinds.indexOf("file_ref") >= 0;
      var isFold = l.kinds && l.kinds.indexOf("in_folder") >= 0;
      var base = isFold ? 0.08
               : isFile ? 0.16 + (l.w || 0) * 0.5
               : (provenance ? 0.24 : 0.10) + (l.w || 0) * 0.5;
      // additive blending: intensity IS opacity
      var k = Math.max(lit, (onSel ? (isFold ? 0.5 : 0.95) : onHov ? 0.85 : base) * graph.dim);
      /* while one star fires, the rest of the brain quietens */
      if (firing && !onSel && !onHov && !lit) k *= 0.45;
      /* in the Brain, idle synapses stay faint so the folds and the stars
         read; a touched star brings its own back at full strength */
      if (layoutMode === "brain" && !onSel && !onHov && !lit) k *= 0.5;
      var centre = onHov ? hov : onSel ? sel : null;
      var dir = centre && tid === centre ? -1 : 1;     // pulses race away from it
      var fire = lit || (onHov ? 1 : onSel ? 0.75 : 0);
      linkEndColor(l.source, _ca);
      linkEndColor(l.target, _cb);
      if (onSel || onHov || lit || provenance) { _ca.lerp(STAR, 0.5); _cb.lerp(STAR, 0.5); }
      for (var sg = 0; sg < SEG; sg++) {
        for (var v = 0; v < 2; v++) {
          var t = (sg + v) / SEG, o = (i * SEG + sg) * 2 + v;
          lc[o * 3] = (_ca.r + (_cb.r - _ca.r) * t) * k;
          lc[o * 3 + 1] = (_ca.g + (_cb.g - _ca.g) * t) * k;
          lc[o * 3 + 2] = (_ca.b + (_cb.b - _ca.b) * t) * k;
          la[o] = fire * dir;
        }
      }
    });
    graph.lines.geometry.attributes.color.needsUpdate = true;
    graph.lines.geometry.attributes.aLit.needsUpdate = true;
  }

  function paintNodes() {
    var c = new THREE.Color();
    if (graph.mesh) {
      graph.memNodes.forEach(function (n, i) {
        if (n.id === graph.selected) c.copy(SELECT); // pure white: unmistakable
        else nodeColor(n, c);
        graph.mesh.setColorAt(i, c);
      });
      graph.mesh.instanceColor.needsUpdate = true;
    }
    /* a chosen crystal or planet goes white too — same language as a star */
    function tintAll(meshObj, list, base, tint) {
      if (!meshObj) return;
      list.forEach(function (n, i) {
        if (n.id === graph.selected) c.copy(SELECT);
        else c.copy(base).multiplyScalar(tint);
        meshObj.setColorAt(i, c);
      });
      meshObj.instanceColor.needsUpdate = true;
    }
    tintAll(graph.files, graph.fileNodes, ICE, 1.0);
    tintAll(graph.folders, graph.folderNodes, SLATE, 0.75);
    updateBuffers();
  }

  N.select = function (id) {
    graph.selected = id;
    if (id) graph.linkFocus = -1;      // a chosen star outranks a chosen link
    paintNodes(); paintLinks();
    if (id && graph.byId[id]) spawnRipple(graph.byId[id], 1, 1100);
    N.requestRender();
  };
  N.deselect = function () { graph.linkFocus = -1; N.select(null); };
  N.getNode = function (id) { return graph.byId[id] || null; };

  /* --------------------------------------------------- focus vs backdrop */
  var focus = false;
  N.setFocus = function (on) {
    focus = !!on;
    frame.dirty = true;                 // the frame is the lens canvas only in focus
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

  /* v2.5 layouts: same stars, different shape. Organic is the force
     layout as before; the others add a shaping force or, for tree, fix
     every position and stop the physics. The caller stores the choice,
     the world only obeys it. */
  /* ------------------------------------------------- framing the world
     The frame is the part of the window the stars should fill: in the
     Constellation lens, the lens canvas (right of the rail, under the
     controls); as a backdrop, the whole window. The projection centre
     glides there (a camera view offset), and the camera stands exactly as
     far back as every star (and the drawn brain) needs. */
  var frame = { el: null, w: 1, h: 1, sx: 0, sy: 0, tx: 0, ty: 0, dirty: true };
  var BRAIN_VIEW = new THREE.Vector3(-0.62, 0.42, 0.66).normalize();
  function applyFrame() {
    var W = window.innerWidth, H = window.innerHeight;
    if (!W || !H) return;
    if (Math.abs(frame.sx) < 0.5 && Math.abs(frame.sy) < 0.5) camera.clearViewOffset();
    else camera.setViewOffset(W, H, -frame.sx, -frame.sy, W, H);
  }
  function measureFrame() {
    frame.dirty = false;
    var W = window.innerWidth, H = window.innerHeight, r = null;
    if (focus && frame.el) {
      var b = frame.el.getBoundingClientRect();
      if (b.width > 120 && b.height > 120) r = b;
    }
    frame.w = r ? r.width : W;
    frame.h = r ? r.height : H;
    frame.tx = r ? r.left + r.width / 2 - W / 2 : 0;
    frame.ty = r ? r.top + r.height / 2 - H / 2 : 0;
    if (reduced) { frame.sx = frame.tx; frame.sy = frame.ty; applyFrame(); }
  }
  function stepFrame(dt) {
    if (frame.dirty) measureFrame();
    var dx = frame.tx - frame.sx, dy = frame.ty - frame.sy;
    if (Math.abs(dx) < 0.3 && Math.abs(dy) < 0.3) return;
    var k = Math.min(1, dt * 5);
    frame.sx = Math.abs(dx) < 0.6 ? frame.tx : frame.sx + dx * k;
    frame.sy = Math.abs(dy) < 0.6 ? frame.ty : frame.sy + dy * k;
    applyFrame();
  }
  N.setFrameElement = function (el) {
    frame.el = el || null;
    frame.dirty = true;
    N.requestRender();
  };

  var _fw = new THREE.Vector3(), _rt = new THREE.Vector3(),
      _up = new THREE.Vector3(), _pp = new THREE.Vector3();
  function fitDistance(dir, margin) {
    var W = window.innerWidth, H = window.innerHeight;
    if (!W || !H) return Math.max(300, SPACE.target * 1.95);
    _fw.copy(dir).normalize().negate();             // the camera looks along -dir
    _rt.crossVectors(_fw, camera.up);
    if (_rt.lengthSq() < 1e-8) _rt.set(1, 0, 0);
    _rt.normalize();
    _up.crossVectors(_rt, _fw);
    var t = Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2) * margin;
    var tv = t * Math.min(1, frame.h / H), th = t * camera.aspect * Math.min(1, frame.w / W);
    function need(x, y, z, r) {
      _pp.set(x, y, z);
      return Math.max((Math.abs(_pp.dot(_rt)) + r) / th,
                      (Math.abs(_pp.dot(_up)) + r) / tv) - _pp.dot(_fw);
    }
    /* the bulk decides, not a few far-flung planets: fit the 96th
       percentile star, never closer than 80% of what the farthest needs */
    var needs = graph.nodes.map(function (n) {
      return need(n.x || 0, n.y || 0, n.z || 0, (n._r || 4) * 1.6);
    }).sort(function (a, b) { return a - b; });
    var best = needs.length
      ? Math.max(needs[Math.floor((needs.length - 1) * 0.96)], needs[needs.length - 1] * 0.8)
      : 0;
    if (CORTEX.target > 0 && cortexSample) {          // the whole brain, always
      for (var i = 0; i < cortexSample.length; i += 3) {
        best = Math.max(best, need(cortexSample[i] * brainScale, cortexSample[i + 1] * brainScale,
                                   cortexSample[i + 2] * brainScale, 0));
      }
    }
    return Math.max(160, best);
  }

  /* once a new dataset or shape has settled, glide back to hold all of
     it, unless the hand has taken the camera. The Brain is first seen in
     three-quarter view (front, left, above), the way a brain is drawn. */
  function maybeFrame() {
    if (flight || orb.veil || tween || !graph.nodes.length) return;
    if (!window.innerWidth || !window.innerHeight) return;    // unseen: frame when seen
    var dir, d;
    if (framing.pending) {
      if (graph.sim && graph.sim.alpha() >= 0.12) return;
      framing.pending = false;
      framing.refine = !!graph.sim;
      fitSpace();
      /* a new shape is first seen from its own best side: the Brain in
         three-quarter view, a flat shape face on; otherwise keep the view */
      if (!framing.keepDir && layoutMode === "brain") dir = BRAIN_VIEW.clone();
      else if (!framing.keepDir && FLAT_MODES.indexOf(layoutMode) >= 0) {
        dir = new THREE.Vector3(0, 0.12, 1).normalize();
      } else {
        dir = camera.position.clone().sub(controls.target);
        if (dir.lengthSq() < 1) dir.set(0, 0.12, 1);
        dir.normalize();
      }
      framing.keepDir = true;
      flyCamera(dir.clone().multiplyScalar(fitDistance(dir, 0.9)),
                new THREE.Vector3(0, 0, 0), 1600);
      return;
    }
    /* the first glide happens while the shape is still settling; once
       the physics rests, one gentle correction if the fit is off by much */
    if (framing.refine && !framing.userMoved
        && (!graph.sim || graph.sim.alpha() <= graph.sim.alphaMin() * 1.01)) {
      framing.refine = false;
      dir = camera.position.clone().sub(controls.target);
      var cur = dir.length();
      if (cur < 1) return;
      dir.normalize();
      d = fitDistance(dir, 0.9);
      if (Math.abs(d - cur) / cur > 0.12) {
        flyCamera(dir.multiplyScalar(d), new THREE.Vector3(0, 0, 0), 1200);
      }
    }
  }

  /* fly to a named view of the whole, fitted: front, back, left, right,
     top, or three (the Brain's own three-quarter view) */
  var VIEWS = { front: [0, 0.1, 1], back: [0, 0.1, -1], left: [-1, 0.08, 0.02],
                right: [1, 0.08, 0.02], top: [0.02, 1, 0.06], three: null };
  N.view = function (name) {
    var v = VIEWS[name];
    var dir = v ? new THREE.Vector3(v[0], v[1], v[2]).normalize() : BRAIN_VIEW.clone();
    framing.pending = false;
    framing.userMoved = true;            // a chosen view is the hand's choice
    flyCamera(dir.clone().multiplyScalar(fitDistance(dir, 0.9)),
              new THREE.Vector3(0, 0, 0), 1200);
  };

  N.getLayout = function () { return layoutMode; };
  N.setLayout = function (mode) {
    var L = window.NebulaLayouts;
    if (!L || L.MODES.indexOf(mode) < 0) mode = "organic";
    layoutMode = mode;
    tween = null;
    framing.userMoved = false;          // a new shape is worth seeing whole
    framing.pending = true;
    framing.keepDir = false;            // ... from its own best side
    graph.nodes.forEach(function (n) { n.fx = n.fy = n.fz = null; });
    if (graph.nodes.length) { startSim(); paintLinks(); updateBuffers(); N.requestRender(); }
    else { CORTEX.target = mode === "brain" ? 1 : 0; }
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
    var repaint = false;
    if (graph.hoverId) {
      setHoverInstance(graph.hoverId, null);
      graph.hoverId = null;
      updateBuffers();
      repaint = true;
    }
    if (graph.hoverLink >= 0) { graph.hoverLink = -1; repaint = true; }
    if (repaint) paintLinks();
    renderer.domElement.style.cursor = "";
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  function speciesOf(n) {
    return n.kind === "file" ? [graph.files, graph.fileNodes]
         : n.kind === "folder" ? [graph.folders, graph.folderNodes]
         : [graph.mesh, graph.memNodes];
  }
  /* three meshes, one ray: the NEAREST hit wins, whatever species it is */
  function pick() {
    raycaster.setFromCamera(new THREE.Vector2(pointer.nx, pointer.ny), camera);
    var best = null;
    [[graph.mesh, graph.memNodes], [graph.files, graph.fileNodes],
     [graph.folders, graph.folderNodes]].forEach(function (pair) {
      if (!pair[0]) return;
      var hit = raycaster.intersectObject(pair[0], false)[0];
      if (hit && hit.instanceId != null && (!best || hit.distance < best.d)) {
        best = { d: hit.distance, n: pair[1][hit.instanceId] };
      }
    });
    return best ? best.n : null;
  }

  function pickLink() {
    if (!graph.lines || !graph.links.length) return -1;
    raycaster.setFromCamera(new THREE.Vector2(pointer.nx, pointer.ny), camera);
    raycaster.params.Line.threshold = 3.2;
    var hit = raycaster.intersectObject(graph.lines, false)[0];
    if (!hit || hit.index == null) return -1;
    return Math.floor(hit.index / (2 * SEG));  // SEG segments, two vertices each
  }

  /* the hovered node surges: one float per instance on ITS mesh */
  function setHoverInstance(prevId, newId) {
    function flip(id, v) {
      var n = id != null && graph.byId[id];
      if (!n || n._i == null) return;
      var mesh = speciesOf(n)[0];
      if (!mesh) return;
      var attr = mesh.geometry.getAttribute("aHov");
      if (!attr) return;
      attr.array[n._i] = v;
      attr.needsUpdate = true;
    }
    flip(prevId, 0);
    flip(newId, 1);
  }

  /* click a filament: the camera frames its two stars — pulled square-on
     to the pair, as best the current view allows */
  function focusPair(idx) {
    var l = graph.links[idx];
    if (!l) return;
    graph.linkFocus = idx;
    graph.selected = null;
    paintNodes(); paintLinks();
    var a = l.source, b = l.target;
    var mid = new THREE.Vector3((a.x + b.x) / 2, (a.y + b.y) / 2, (a.z + b.z) / 2);
    var seg = new THREE.Vector3(b.x - a.x, b.y - a.y, b.z - a.z);
    var sep = seg.length() || 1;
    seg.normalize();
    /* view direction: keep the current one, minus its component along the
       pair — so the camera slides square-on to the filament */
    var dir = camera.position.clone().sub(controls.target).normalize();
    dir.addScaledVector(seg, -dir.dot(seg));
    if (dir.lengthSq() < 0.05) dir.set(0, 0.2, 1);   // degenerate: pick a side
    dir.normalize();
    var dist = Math.max(110, sep * 2.2);
    flyCamera(mid.clone().addScaledVector(dir, dist), mid, 750);
    N.requestRender();
  }

  var hoverPending = false;
  function onHoverCheck() {
    hoverPending = false;
    if (!focus || orb.veil || drag.node) return;
    var n = pick();
    var li = n ? -1 : pickLink();          // stars outrank filaments
    var id = n ? n.id : null;
    var changed = false;
    var relink = false;
    if (id !== graph.hoverId) {
      setHoverInstance(graph.hoverId, id);
      graph.hoverId = id;
      updateBuffers();
      if (n) spawnRipple(n, 0.55, 640);      // touched: its synapses fire
      relink = changed = true;
    }
    if (li !== graph.hoverLink) {
      graph.hoverLink = li;
      relink = changed = true;
    }
    if (relink) paintLinks();
    if (changed) {
      N.requestRender();
      renderer.domElement.style.cursor = (n || li >= 0) ? "pointer" : "";
      if (!tip) makeTip();
      if (n && n.kind === "file") {
        tip.innerHTML = '<p class="tip-head">'
          + '<b class="tip-project">' + esc(n.project || "(no project)") + "</b>"
          + ' · <span class="badge t-filenode">file</span>'
          + (n.ext ? " · " + esc(n.ext) : "") + "</p>"
          + "<strong>" + esc(n.label || n.path) + "</strong>"
          + '<p class="quiet">' + esc(n.path)
          + " · heat " + (n.degree != null ? Number(n.degree).toFixed(1) : "0")
          + (n.mtime ? " · modified " + esc(String(n.mtime).slice(0, 10)) : "") + "</p>";
        tip.style.display = "";
      } else if (n && n.kind === "folder") {
        tip.innerHTML = '<p class="tip-head">'
          + '<b class="tip-project">' + esc(n.project || "(unmapped)") + "</b>"
          + ' · <span class="badge t-foldernode">folder</span>'
          + (n.role ? " · " + esc(n.role) : "") + "</p>"
          + "<strong>" + esc(n.label || n.path) + "</strong>"
          + '<p class="quiet">' + esc(n.path || "(root)") + " · " + esc(n.file_count || 0)
          + " files · click to pull them in</p>";
        tip.style.display = "";
      } else if (n) {
        /* what you need FIRST: whose star is this, what kind, when */
        var when = (n.timestamp || "").slice(0, 10) || "undated";
        tip.innerHTML = '<p class="tip-head">'
          + '<b class="tip-project">' + esc(n.project || "(no project)") + "</b>"
          + ' · <span class="badge t-' + esc(n.type) + '">' + esc(n.type) + "</span>"
          + " · " + esc(when)
          + (n._head === n ? ' · <b class="tip-crown">★ head</b>' : "")
          + "</p>"
          + "<strong>" + esc(cleanLabel(n.label || n.id)) + "</strong>"
          + '<p class="quiet">importance ' + (n.importance || "?")
          + " · gravity " + (n.degree != null ? Number(n.degree).toFixed(1) : "?")
          + " · ring " + (n._level != null ? n._level : "?")
          + "</p>";
        tip.style.display = "";
      } else if (li >= 0) {
        var l = graph.links[li];
        tip.innerHTML = '<p class="tip-head"><b class="tip-project">'
          + esc(l.source.project || "") + "</b> · "
          + esc((l.kinds || []).join(", ") || "link")
          + " · weight " + (l.w != null ? l.w.toFixed(2) : "?") + "</p>"
          + "<strong>" + esc(cleanLabel(l.source.label || l.source.id).slice(0, 46))
          + " ⟷ " + esc(cleanLabel(l.target.label || l.target.id).slice(0, 46))
          + "</strong>"
          + '<p class="quiet">click to frame the pair</p>';
        tip.style.display = "";
      } else tip.style.display = "none";
    }
    if ((n || graph.hoverLink >= 0) && tip) {
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
    drag.downAt = { x: ev.clientX, y: ev.clientY, node: n,
                    link: n ? -1 : pickLink() };
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
    var clickedLink = drag.downAt.link;
    drag.downAt = null;
    if (dx > 4 || dy > 4) return;                       // it was an orbit
    if (clicked) { if (N.hooks.onNodeClick) N.hooks.onNodeClick(clicked); }
    else if (clickedLink >= 0) { focusPair(clickedLink); }
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
      if (frame.dirty) measureFrame();
      maybeFrame();
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

  /* a layout without physics (tree) glides there instead of jumping */
  var tween = null;
  function stepTween(now) {
    var t = Math.min(1, (now - tween.t0) / tween.dur);
    var e = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;   // easeInOutCubic
    tween.list.forEach(function (it) {
      it.n.x = it.x0 + (it.x1 - it.x0) * e;
      it.n.y = it.y0 + (it.y1 - it.y0) * e;
      it.n.z = it.z0 + (it.z1 - it.z0) * e;
    });
    updateBuffers();
    if (t >= 1) { tween = null; fitSpace(); }
  }

  /* a slow machine loses pixels before it loses frames: two slow windows
     in a row drop the pixel ratio to 1, then thin the fog wisps. Never
     back up again in this page's life, so it cannot oscillate. */
  var perf = { frames: 0, acc: 0, slow: 0, done: false };
  function resizeUniforms() {
    var pr = renderer.getPixelRatio();
    auroraMat.uniforms.uRes.value.set(window.innerWidth * pr, window.innerHeight * pr);
    starsMat.uniforms.uPR.value = pr;
    cortexMat.uniforms.uPR.value = pr;
    cortexMat.uniforms.uPxPerUnit.value = pxPerUnit();
  }
  function watchPerf(dt) {
    if (perf.done) return;
    perf.frames++; perf.acc += dt;
    if (perf.frames < 120) return;
    var fps = perf.frames / Math.max(perf.acc, 1e-3);
    perf.frames = 0; perf.acc = 0;
    if (fps > 44) { perf.slow = 0; return; }
    if (++perf.slow < 2) return;
    perf.slow = 0;
    if (renderer.getPixelRatio() > 1) {
      renderer.setPixelRatio(1);
      renderer.setSize(window.innerWidth, window.innerHeight);
      resizeUniforms();
      N.quality = "pixel ratio 1";
    } else {
      if (MOTES.n > 320) { MOTES.n = 320; buildMotes(); }
      N.quality = "pixel ratio 1, fewer wisps";
      perf.done = true;
    }
  }

  var lastNow = 0, fitCounter = 0, animErrors = 0;
  var docStyle = document.documentElement.style;
  function animate(now) {
    requestAnimationFrame(animate);
    if (document.hidden) { lastNow = now; return; }
    /* self-healing: one bad frame must never freeze the world into an
       unclickable still image. Log the first few, keep rendering. */
    try {
      animateBody(now);
    } catch (e) {
      if (++animErrors <= 3) {
        N.lastError = String(e && e.stack || e);
        console.error("Nebula animate error (frame kept alive):", e);
      }
      try { controls.update(); renderer.render(scene, camera); } catch (e2) {}
    }
  }

  function animateBody(now) {
    var dt = Math.min((now - lastNow) / 1000 || 0.016, 0.05);
    lastNow = now;
    clockTime = now * 0.001;
    watchPerf(dt);

    if (flight) stepFlight(now);
    if (tween) stepTween(now);
    stepFrame(dt);
    maybeFrame();
    if (ripples.length) stepRipples(now);

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
    synapseMat.uniforms.uTime.value = clockTime;
    if (graph.glow) graph.glow.material.uniforms.uTime.value = clockTime;
    shellMat.uniforms.uTime.value = ambient.visible ? clockTime : 0;
    stars.position.copy(camera.position);      // the far field rides along
    if (ambient.visible) starsMat.uniforms.uTime.value = clockTime;

    /* the cortex fades in with the Brain; the round shell gives way to it */
    CORTEX.alpha += (CORTEX.target - CORTEX.alpha) * Math.min(1, dt * 2.2);
    if (cortexGroup) {
      cortexGroup.visible = CORTEX.alpha > 0.01 && graph.group.visible;
      var ca = CORTEX.alpha * (focus ? 1 : 0.55);
      cortexMat.uniforms.uAlpha.value = ca;
      cortexMat.uniforms.uTime.value = clockTime;
      glassMat.uniforms.uAlpha.value = ca * 0.9;
      glassMat.uniforms.uTime.value = clockTime;
    }
    shellMat.uniforms.uAlpha.value = (focus ? 0.55 : 0.3) * (1 - CORTEX.alpha);

    /* the glass leans with the hand: two custom props, GPU transforms only */
    pointer.px += (pointer.nx - pointer.px) * 0.04;
    pointer.py += (pointer.ny - pointer.py) * 0.04;
    docStyle.setProperty("--px", pointer.px.toFixed(4));
    docStyle.setProperty("--py", pointer.py.toFixed(4));

    if (ambient.visible) {
      /* cursor physics field — feedback, full rate */
      stepMotes(dt);
      /* the fog flows like liquid (slow register) and leans toward the
         hand: lerped mouse gives the vortex weight, not jitter */
      auroraMat.uniforms.uTime.value = clockTime;
      var mu = auroraMat.uniforms.uMouse.value;
      mu.x += ((pointer.nx + 1) / 2 - mu.x) * 0.05;
      mu.y += ((pointer.ny + 1) / 2 - mu.y) * 0.05;
    }

    /* selection pulses along the chosen star's filaments — and along a
       clicked filament itself (feedback) */
    var centre = graph.hoverId || graph.selected;
    if (graph.pulses && graph.links.length && (centre || graph.linkFocus >= 0)) {
      var pp = graph.pulses.geometry.attributes.position.array;
      var pc = graph.pulses.geometry.attributes.color.array;
      var ps = graph.pulses.geometry.attributes.psize.array;
      var k = 0;
      var tt = now * (graph.hoverId ? 0.0008 : 0.00035);   // a touched star fires faster
      for (var i = 0; i < graph.links.length && k < 62; i++) {
        var l = graph.links[i];
        var sid = l.source.id, tid = l.target.id;
        var mine = sid === graph.hoverId || tid === graph.hoverId
                || sid === graph.selected || tid === graph.selected;
        if (i !== graph.linkFocus && !mine) continue;
        /* from the firing star outward, along the synapse's own curve */
        var from = centre && tid === centre ? l.target : l.source;
        var to = from === l.source ? l.target : l.source;
        var cxp = l._cx != null ? l._cx : (from.x + to.x) / 2,
            cyp = l._cy != null ? l._cy : (from.y + to.y) / 2,
            czp = l._cz != null ? l._cz : (from.z + to.z) / 2;
        for (var j = 0; j < 2; j++, k++) {
          var f2 = (tt + i * 0.37 + j * 0.5) % 1, g1 = 1 - f2;
          pp[k * 3] = g1 * g1 * from.x + 2 * g1 * f2 * cxp + f2 * f2 * to.x;
          pp[k * 3 + 1] = g1 * g1 * from.y + 2 * g1 * f2 * cyp + f2 * f2 * to.y;
          pp[k * 3 + 2] = g1 * g1 * from.z + 2 * g1 * f2 * czp + f2 * f2 * to.z;
          var pcol = (l.kinds && l.kinds.indexOf("file_ref") >= 0) ? FILELINK
                   : (l.kinds && l.kinds.indexOf("in_folder") >= 0) ? FOLDLINK : STAR;
          pc[k * 3] = pcol.r; pc[k * 3 + 1] = pcol.g; pc[k * 3 + 2] = pcol.b;
          ps[k] = graph.hoverId ? 4.2 : 3.4;
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

  /* in the Brain: how many stars sit in each hemisphere, and how many
     have strayed outside the skin (should be near zero) */
  function brainStats() {
    var L = window.NebulaLayouts, B = L && L.BRAIN;
    if (!B || layoutMode !== "brain") return null;
    var out = { left: 0, right: 0, outside: 0 };
    graph.nodes.forEach(function (n) {
      var side = n.x < 0 ? -1 : 1;
      if (side < 0) out.left++; else out.right++;
      var cx = L.hemiCentre(side) * brainScale;
      var sq = (n.x - cx) * side < 0 ? B.medial : 1;
      var lx = (n.x - cx) / (B.rx * brainScale * sq), ly = n.y / (B.ry * brainScale),
          lz = n.z / (B.rz * brainScale);
      if (Math.sqrt(lx * lx + ly * ly + lz * lz) > 1.08) out.outside++;
    });
    return out;
  }

  /* one honest answer for "why doesn't it work": paste Nebula.debug()
     into the console (or ask the doctor page) */
  N.debug = function () {
    return {
      focus: focus, reduced: reduced, ambience: ambient.visible,
      nodes: graph.nodes.length, links: graph.links.length,
      selected: graph.selected, hover: graph.hoverId,
      simAlpha: graph.sim ? graph.sim.alpha() : null,
      spaceR: SPACE.r,
      camera: { x: camera.position.x, y: camera.position.y, z: camera.position.z },
      controlsEnabled: controls.enabled,
      orbOpen: !!orb.veil,
      animErrors: animErrors,
      lastError: N.lastError || null,
      layout: layoutMode, cortex: CORTEX.alpha, quality: N.quality || "full",
      glass: !!glass, brainScale: brainScale, brainStats: brainStats(),
      frame: { w: frame.w, h: frame.h, sx: Math.round(frame.sx), sy: Math.round(frame.sy) },
      ripples: ripples.length,
      samplePos: graph.nodes[0]
        ? { x: graph.nodes[0].x, y: graph.nodes[0].y, z: graph.nodes[0].z } : null,
    };
  };

  /* how much one frame costs, measured honestly: runs the real frame
     body and waits for the GPU after every frame (a 1-pixel read), so CPU
     and GPU time are both in the number. It yields between frames: a long
     unbroken burst of GPU work trips the driver watchdog and loses the
     context. A hidden tab never animates, so this is the way to know
     without watching. Returns a promise. */
  N.profile = function (frames) {
    frames = Math.max(1, Math.min(frames || 60, 240));
    var gl = renderer.getContext(), px = new Uint8Array(4);
    var now = 0, i = 0, total = 0, worst = 0;
    return new Promise(function (resolve) {
      function one() {
        if (gl.isContextLost()) { resolve({ error: "webgl context lost" }); return; }
        var t0 = performance.now();
        now = t0;                          // the real clock: flights and tweens run true
        animateBody(now);
        gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
        var ms = performance.now() - t0;
        total += ms; if (ms > worst) worst = ms;
        if (++i < frames) { setTimeout(one, 0); return; }
        lastNow = performance.now();      // the next real frame gets a sane step
        resolve({ msPerFrame: Math.round(total / frames * 100) / 100,
                  worstMs: Math.round(worst * 100) / 100, frames: frames,
                  pixelRatio: renderer.getPixelRatio(),
                  size: [window.innerWidth, window.innerHeight],
                  nodes: graph.nodes.length, links: graph.links.length,
                  simAlpha: graph.sim ? graph.sim.alpha() : null,
                  calls: renderer.info.render.calls,
                  triangles: renderer.info.render.triangles,
                  points: renderer.info.render.points });
      }
      one();
    });
  };

  window.addEventListener("resize", function () {
    camera.aspect = window.innerWidth / window.innerHeight;
    frame.dirty = true;
    applyFrame();
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
    resizeUniforms();
    N.requestRender();
  });

  N.setAmbience(ambienceOn());
  N.setFocus(document.body.dataset.lens === "constellation");
}

/* let classic scripts know the world is up (or not) */
document.dispatchEvent(new CustomEvent("nebula:ready",
  { detail: { available: N.available } }));
