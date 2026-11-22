/* ============================================================
   얼미리서치 · 3D 홀로그램 브리핑
   - data/briefing-hologram.json 하나로 텍스트·수치·타임라인·음성을 관리
   - Three.js(CDN, importmap)로 홀로그램 씬, 실패하면 자막+음성만으로 동작
   ============================================================ */
window.__holoBooted = true;

const DATA_URL = 'data/briefing-hologram.json';
const $ = (id) => document.getElementById(id);
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const lerp = (a, b, t) => a + (b - a) * t;
const easeOut = (t) => 1 - Math.pow(1 - t, 3);
const fmt = (s) => {
  s = Math.max(0, s || 0);
  const m = Math.floor(s / 60), r = Math.floor(s % 60);
  return String(m).padStart(2, '0') + ':' + String(r).padStart(2, '0');
};
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const voiceEl = $('voice');
const state = {
  t: 0,
  playing: false,
  sound: true,
  audioOk: false,
  idx: -1,
  done: false,
  started: false,
  duration: 45,
};

let DOC = null;
let STAGES = [];
let THREE = null;
let sceneApi = null;
let speechOn = false;

/* ---------------- 부트 ---------------- */
boot().catch((err) => {
  console.error(err);
  document.body.classList.add('no3d');
  toast('브리핑 데이터를 불러오지 못했습니다. 새로고침해 주세요.');
});

async function boot() {
  const res = await fetch(DATA_URL + '?v=' + Date.now(), { cache: 'no-store' });
  if (!res.ok) throw new Error('data ' + res.status);
  DOC = await res.json();
  STAGES = Array.isArray(DOC.stages) ? DOC.stages : [];
  if (!STAGES.length) throw new Error('stages empty');

  // 타임라인이 비어 있으면 문단 길이 균등 분배
  if (STAGES.some((s) => s.start == null)) equalizeTimeline();
  state.duration = Number(DOC.audio && DOC.audio.duration) || STAGES[STAGES.length - 1].end || 45;

  buildHud();
  setupAudio();
  bindControls();

  // 3D 씬 (실패해도 HUD·음성은 계속)
  try {
    THREE = await import('three');
    sceneApi = await initScene(THREE);
  } catch (err) {
    console.warn('[holo] 3D 초기화 실패:', err);
    document.body.classList.add('no3d');
    toast('3D를 표시할 수 없어 음성·자막으로 재생합니다.');
  }

  // 인트로 뒤에서도 씬이 천천히 보이도록 날씨 파트를 미리 세운다
  setStage(0, true);
  requestAnimationFrame(loop);
  loadBloom();
}

function equalizeTimeline() {
  const per = (Number(DOC.audio && DOC.audio.duration) || STAGES.length * 10) / STAGES.length;
  STAGES.forEach((s, i) => { s.start = +(i * per).toFixed(2); s.end = +((i + 1) * per).toFixed(2); });
}

/* ---------------- HUD ---------------- */
function buildHud() {
  $('hudDate').textContent = (DOC.date || '').replace(/-/g, '.');
  $('hudWeekday').textContent = DOC.weekday || '';
  $('tTotal').textContent = fmt(state.duration);
  $('introDate').textContent = (DOC.date || '').replace(/-/g, '.') + ' (' + (DOC.weekday || '') + ') · 약 ' + Math.round(state.duration) + '초';
  $('introAsOf').textContent = DOC.as_of || '';

  $('badges').innerHTML = STAGES.map((s, i) =>
    '<button class="badge" type="button" data-i="' + i + '"><em>' + esc(s.no || String(i + 1).padStart(2, '0')) + '</em>' + esc(s.badge || s.id) + '</button>'
  ).join('');

  $('marks').innerHTML = STAGES.map((s) => '<i style="left:' + ((s.start / state.duration) * 100).toFixed(2) + '%"></i>').join('');

  $('introList').innerHTML = STAGES.slice(0, 5).map((s) =>
    '<li><em>' + esc(s.no || '') + '</em>' + esc(s.badge || '') + '<b>' + esc(s.headline || '') + '</b></li>'
  ).join('');

  $('badges').addEventListener('click', (e) => {
    const b = e.target.closest('.badge');
    if (!b) return;
    jumpTo(Number(b.dataset.i));
  });

  renderCaption(0, true);
}

function renderCaption(i, silent) {
  const s = STAGES[i];
  if (!s) return;
  document.documentElement.style.setProperty('--stage-accent', s.accent || '#7cf5c8');
  $('capNo').textContent = s.no || String(i + 1).padStart(2, '0');
  $('capTitle').textContent = s.title || '';
  $('capBig').textContent = s.headline || '';
  $('capSub').textContent = s.sub || '';
  $('capText').textContent = s.caption || '';
  const metricLimit = window.innerWidth < 480 ? 3 : 4;
  const metrics = (s.metrics || []).slice(0, metricLimit).map((m) =>
    '<span>' + esc(m.label) + '<b>' + esc(m.value) + '</b></span>'
  ).join('');
  $('capMetrics').innerHTML = metrics;
  [...$('badges').children].forEach((b, k) => {
    b.classList.toggle('on', k === i);
    b.classList.toggle('done', k < i);
  });
}

function toast(msg) {
  const t = $('toast');
  if (!t) return;
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove('show'), 4200);
}

/* ---------------- 오디오 · 재생 엔진 ---------------- */
function setupAudio() {
  const src = DOC.audio && DOC.audio.src;
  if (!src) return;

  voiceEl.addEventListener('canplay', () => { state.audioOk = true; }, { once: true });
  voiceEl.addEventListener('error', () => {
    if (state.audioOk) return;
    state.audioOk = false;
    if (state.started) toast('음성 파일을 불러오지 못해 기기 음성으로 읽습니다.');
  });
  voiceEl.addEventListener('ended', () => { if (state.playing) finish(); });
  voiceEl.src = src;

  // 파일이 이미 준비된 경우
  if (voiceEl.readyState >= 2) state.audioOk = true;
}

function setSound(on) {
  if (state.sound === on) return;
  state.sound = on;
  const btn = $('btnMute');
  btn.classList.toggle('on', on);
  btn.setAttribute('aria-label', on ? '소리 끄기' : '소리 켜기');
  if (!on) {
    try { voiceEl.pause(); } catch (e) { }
    stopSpeech();
  } else if (state.playing) {
    if (state.audioOk) {
      try { voiceEl.currentTime = Math.min(state.t, state.duration - 0.1); } catch (e) { }
      voiceEl.play().catch(() => { state.audioOk = false; speakStage(); });
    } else {
      speakStage();
    }
  }
}

function startPlayback(withSound) {
  state.sound = withSound;
  const btn = $('btnMute');
  btn.classList.toggle('on', withSound);
  btn.setAttribute('aria-label', withSound ? '소리 끄기' : '소리 켜기');

  $('intro').classList.add('hide');
  state.started = true;
  state.done = false;
  document.body.classList.remove('is-done');
  if (state.t >= state.duration - 0.05) state.t = 0;
  state.playing = true;
  updatePlayBtn();

  if (state.sound) {
    if (state.audioOk) {
      try { voiceEl.currentTime = state.t; } catch (e) { }
      const p = voiceEl.play();
      if (p && p.catch) p.catch(() => { state.audioOk = false; toast('소리를 켤 수 없어 기기 음성으로 읽습니다.'); speakStage(); });
    } else {
      speakStage();
    }
  }
}

function togglePlay() {
  if (!state.started) { startPlayback(state.sound); return; }
  if (state.done) { restart(); return; }
  if (state.playing) pause(); else resume();
}

function pause() {
  state.playing = false;
  try { voiceEl.pause(); } catch (e) { }
  stopSpeech();
  updatePlayBtn();
}

function resume() {
  if (state.done) { restart(); return; }
  state.playing = true;
  updatePlayBtn();
  if (state.sound) {
    if (state.audioOk) {
      try { voiceEl.currentTime = Math.min(state.t, state.duration - 0.1); } catch (e) { }
      voiceEl.play().catch(() => { state.audioOk = false; speakStage(); });
    } else speakStage();
  }
}

function restart() {
  state.t = 0;
  state.done = false;
  document.body.classList.remove('is-done');
  state.idx = -1;
  setStage(0, true);
  try { voiceEl.currentTime = 0; } catch (e) { }
  state.playing = true;
  updatePlayBtn();
  if (state.sound) {
    if (state.audioOk) { voiceEl.play().catch(() => { }); } else speakStage();
  }
}

function finish() {
  state.playing = false;
  state.done = true;
  state.t = state.duration;
  document.body.classList.add('is-done');
  $('btnPlay').textContent = '↺ 다시보기';
  $('fill').style.width = '100%';
  updateTimeLabel();
  stopSpeech();
}

function updatePlayBtn() {
  $('btnPlay').textContent = state.playing ? '일시정지' : (state.done ? '↺ 다시보기' : (state.started ? '▶ 이어서 재생' : '▶ 브리핑 재생'));
}

function updateTimeLabel() {
  $('tNow').textContent = fmt(state.t);
  $('fill').style.width = (clamp(state.t / state.duration, 0, 1) * 100).toFixed(2) + '%';
  $('track').setAttribute('aria-valuenow', Math.round(state.t));
}

function jumpTo(i) {
  const s = STAGES[i];
  if (!s) return;
  state.t = Math.max(0, s.start - 0.12);
  state.done = false;
  document.body.classList.remove('is-done');
  if (!state.started) { $('intro').classList.add('hide'); state.started = true; state.playing = true; }
  setStage(i);
  updateTimeLabel();
  updatePlayBtn();
  if (state.playing) {
    if (state.sound && state.audioOk) {
      try { voiceEl.currentTime = state.t; } catch (e) { }
      voiceEl.play().catch(() => { state.audioOk = false; speakStage(); });
    } else if (state.sound) speakStage();
  }
}

/* 기기 음성 fallback */
function speakStage() {
  if (!('speechSynthesis' in window)) return;
  stopSpeech();
  const s = STAGES[state.idx] || STAGES[0];
  if (!s) return;
  const u = new SpeechSynthesisUtterance(s.speech || s.caption || '');
  u.lang = 'ko-KR';
  u.rate = 1.05;
  const ko = (speechSynthesis.getVoices() || []).find((v) => /ko[-_]KR/i.test(v.lang));
  if (ko) u.voice = ko;
  speechOn = true;
  speechSynthesis.speak(u);
}
function stopSpeech() {
  if (speechOn && 'speechSynthesis' in window) { try { speechSynthesis.cancel(); } catch (e) { } }
  speechOn = false;
}

/* ---------------- 컨트롤 바인딩 ---------------- */
function bindControls() {
  $('btnStartSound').addEventListener('click', () => startPlayback(true));
  $('btnStartSilent').addEventListener('click', () => startPlayback(false));
  $('btnPlay').addEventListener('click', togglePlay);
  $('btnRestart').addEventListener('click', restart);
  $('btnMute').addEventListener('click', () => setSound(!state.sound));

  // 진행 바: 누르고 드래그해서 이동
  const track = $('track');
  let dragging = false;
  const seekFromEvent = (e) => {
    const r = track.getBoundingClientRect();
    const x = (e.touches ? e.touches[0].clientX : e.clientX) - r.left;
    const ratio = clamp(x / r.width, 0, 0.999);
    seek(ratio * state.duration);
  };
  track.addEventListener('pointerdown', (e) => { dragging = true; track.setPointerCapture(e.pointerId); seekFromEvent(e); });
  track.addEventListener('pointermove', (e) => { if (dragging) seekFromEvent(e); });
  track.addEventListener('pointerup', () => { dragging = false; });
  track.addEventListener('pointercancel', () => { dragging = false; });

  document.addEventListener('keydown', (e) => {
    if (e.target.matches('input,textarea')) return;
    if (e.code === 'Space') { e.preventDefault(); if (!state.started) startPlayback(state.sound); else togglePlay(); }
    if (e.code === 'ArrowRight' && state.idx < STAGES.length - 1) jumpTo(state.idx + 1);
    if (e.code === 'ArrowLeft' && state.idx > 0) jumpTo(state.idx - 1);
    if (/^Digit[1-9]$/.test(e.code)) {
      const i = Number(e.code.slice(5)) - 1;
      if (i < STAGES.length) jumpTo(i);
    }
  });

  document.addEventListener('visibilitychange', () => { if (document.hidden && state.playing) pause(); });
}

function seek(t) {
  state.t = clamp(t, 0, state.duration - 0.05);
  state.done = false;
  document.body.classList.remove('is-done');
  if (state.sound && state.audioOk) {
    try { voiceEl.currentTime = state.t; } catch (e) { }
  }
  syncStage();
  updateTimeLabel();
  updatePlayBtn();
}

/* ---------------- 타임라인 루프 ---------------- */
let lastTs = 0;
function loop(ts) {
  const dt = lastTs ? Math.min(0.25, (ts - lastTs) / 1000) : 0.016;
  lastTs = ts;
  const now = ts / 1000;

  if (state.playing) {
    if (state.sound && state.audioOk && !voiceEl.paused && !voiceEl.ended) {
      // 음성이 기준 시계
      state.t = voiceEl.currentTime;
    } else {
      state.t += dt;
    }
    if (state.t >= state.duration - 0.03) finish();
    syncStage();
    updateTimeLabel();
  }

  if (sceneApi) sceneApi.update(dt, now, state);
  requestAnimationFrame(loop);
}

function setStage(i, immediate) {
  if (i < 0) return;
  if (i === state.idx && !immediate) return;
  state.idx = i;
  renderCaption(i);
  if (sceneApi) sceneApi.setStage(i, immediate);
}

function syncStage() {
  const t = state.t;
  let i = STAGES.length - 1;
  for (let k = 0; k < STAGES.length; k++) {
    if (t < STAGES[k].end - 0.01) { i = k; break; }
  }
  if (i !== state.idx) setStage(i);
}

/* ---------------- Three.js 씬 ---------------- */
async function initScene(T) {
  const canvas = $('holo-canvas');
  const renderer = new T.WebGLRenderer({ canvas, antialias: true, alpha: false, powerPreference: 'high-performance' });
  const isPhone = Math.min(window.innerWidth, window.innerHeight) < 560;
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, isPhone ? 1.8 : 2));
  renderer.setSize(window.innerWidth, window.innerHeight, false);
  renderer.toneMapping = T.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 0.92;

  const scene = new T.Scene();
  scene.background = new T.Color(0x03080a);
  scene.fog = new T.FogExp2(0x04120f, 0.062);

  const camera = new T.PerspectiveCamera(48, window.innerWidth / window.innerHeight, 0.1, 120);
  const isPortrait = () => window.innerWidth / window.innerHeight < 0.75;
  const fit = () => (isPortrait() ? 11.6 : 10.1);
  let fitZ = fit();
  const world = new T.Group();
  if (isPortrait()) {
    world.position.y = 1.15;
    world.scale.setScalar(0.92);
  }
  scene.add(world);
  camera.position.set(0, 2.5, fitZ);

  /* --- 조명 --- */
  scene.add(new T.HemisphereLight(0x9ff7ff, 0x04120f, 0.55));
  const key = new T.PointLight(0x7cf5c8, 16, 26, 2);
  key.position.set(2.5, 6, 4);
  scene.add(key);
  const rim = new T.DirectionalLight(0x66d9ff, 0.35);
  rim.position.set(-4, 3, -5);
  scene.add(rim);

  /* --- 바닥: 네온 그리드 --- */
  // 바닥 거울 반사 (가볍게 깔고 어둡게 덮어 홀로그램 잔상처럼)
  let mirror = null;
  try {
    const { Reflector } = await import('three/addons/objects/Reflector.js');
    mirror = new Reflector(new T.CircleGeometry(22, 64), {
      clipBias: 0.003,
      textureWidth: isPhone ? 512 : 1024,
      textureHeight: isPhone ? 512 : 1024,
      color: 0x0a1a18,
    });
    mirror.rotation.x = -Math.PI / 2;
    mirror.position.y = 0.0;
    scene.add(mirror);
    const dim = new T.Mesh(
      new T.CircleGeometry(22, 64),
      new T.MeshBasicMaterial({ color: 0x02080a, transparent: true, opacity: 0.44, depthWrite: false })
    );
    dim.rotation.x = -Math.PI / 2;
    dim.position.y = 0.002;
    scene.add(dim);
  } catch (e) {
    console.warn('[holo] 바닥 반사 생략:', e);
  }

  const gridUniforms = {
    uTime: { value: 0 },
    uColor: { value: new T.Color(0x7cf5c8) },
    uAccent: { value: new T.Color(0x7cf5c8) },
  };
  const gridMat = new T.ShaderMaterial({
    uniforms: gridUniforms,
    transparent: true,
    depthWrite: false,
    blending: T.AdditiveBlending,
    vertexShader: `
      varying vec3 vPos;
      void main(){
        vec4 wp = modelMatrix * vec4(position,1.0);
        vPos = wp.xyz;
        gl_Position = projectionMatrix * viewMatrix * wp;
      }`,
    fragmentShader: `
      uniform float uTime; uniform vec3 uColor;
      varying vec3 vPos;
      float grid(vec2 p, float s, float w){
        vec2 q = p * s;
        vec2 fw = fwidth(q);
        vec2 d = abs(fract(q - 0.5) - 0.5) / max(fw, vec2(1e-4));
        float l = min(d.x, d.y);
        float line = 1.0 - clamp(l / w, 0.0, 1.0);
        float cellPx = 1.0 / max(max(fw.x, fw.y), 1e-5);
        return line * smoothstep(1.2, 5.0, cellPx);
      }
      void main(){
        float r = length(vPos.xz);
        float dist = length(cameraPosition - vPos);
        float f1 = grid(vPos.xz, 2.2, 1.0);
        float f2 = grid(vPos.xz, 11.0, 1.5);
        float fadeR = smoothstep(18.0, 2.0, r);
        float fadeD = 1.0 - smoothstep(11.0, 26.0, dist);
        float ripple = 0.5 + 0.5 * sin(r * 0.8 - uTime * 0.7);
        float a = (f1 * 0.035 + f2 * 0.085 + 0.002) * fadeR * fadeD * (0.78 + 0.22 * ripple);
        vec3 col = mix(uColor, vec3(0.55,0.88,1.0), f2 * 0.6);
        gl_FragColor = vec4(col, a);
      }`,
  });
  const grid = new T.Mesh(new T.PlaneGeometry(60, 60), gridMat);
  grid.rotation.x = -Math.PI / 2;
  grid.position.y = 0.005;
  scene.add(grid);

  // 홀로그램 프로젝터 베이스 링
  const projector = new T.Group();
  const ringOuter = new T.Mesh(
    new T.TorusGeometry(1.62, 0.008, 6, 160),
    new T.MeshBasicMaterial({ color: 0x7cf5c8, transparent: true, opacity: 0.5, blending: T.AdditiveBlending, depthWrite: false })
  );
  ringOuter.rotation.x = -Math.PI / 2;
  projector.add(ringOuter);
  const ringInner = new T.Mesh(
    new T.TorusGeometry(1.18, 0.005, 6, 140),
    new T.MeshBasicMaterial({ color: 0x66d9ff, transparent: true, opacity: 0.32, blending: T.AdditiveBlending, depthWrite: false })
  );
  ringInner.rotation.x = -Math.PI / 2;
  projector.add(ringInner);
  const pTicks = new T.Group();
  for (let i = 0; i < 36; i++) {
    const a2 = (i / 36) * Math.PI * 2;
    const tick = new T.Mesh(
      new T.BoxGeometry(0.008, 0.008, 0.11 + (i % 3) * 0.05),
      new T.MeshBasicMaterial({ color: 0x9ff7ff, transparent: true, opacity: 0.35, blending: T.AdditiveBlending, depthWrite: false })
    );
    tick.position.set(Math.cos(a2) * 1.62, 0, Math.sin(a2) * 1.62);
    tick.rotation.y = -a2;
    pTicks.add(tick);
  }
  projector.add(pTicks);
  projector.position.y = 0.02;
  scene.add(projector);

  // 바닥 중앙 글로우
  const floorGlowTex = radialTexture(T, 'rgba(124,245,200,0.55)', 'rgba(124,245,200,0)');
  const floorGlow = new T.Mesh(
    new T.PlaneGeometry(9, 9),
    new T.MeshBasicMaterial({ map: floorGlowTex, transparent: true, opacity: 0.1, blending: T.AdditiveBlending, depthWrite: false })
  );
  floorGlow.rotation.x = -Math.PI / 2;
  floorGlow.position.y = 0.012;
  scene.add(floorGlow);

  /* --- 떠 있는 입자 --- */
  const PCOUNT = reduced ? 320 : (isPhone ? 620 : 950);
  const pGeo = new T.BufferGeometry();
  const pPos = new Float32Array(PCOUNT * 3);
  const pSeed = new Float32Array(PCOUNT);
  for (let i = 0; i < PCOUNT; i++) {
    const a = Math.random() * Math.PI * 2;
    const r = 1.2 + Math.pow(Math.random(), 0.7) * 9.5;
    pPos[i * 3] = Math.cos(a) * r;
    pPos[i * 3 + 1] = Math.random() * 8.5;
    pPos[i * 3 + 2] = Math.sin(a) * r;
    pSeed[i] = 0.25 + Math.random() * 0.9;
  }
  pGeo.setAttribute('position', new T.BufferAttribute(pPos, 3));
  const dustTex = radialTexture(T, 'rgba(190,255,240,1)', 'rgba(124,245,200,0)');
  const dust = new T.Points(pGeo, new T.PointsMaterial({
    size: 0.058, map: dustTex, color: 0xa8ffe6, transparent: true, opacity: 0.34,
    blending: T.AdditiveBlending, depthWrite: false, sizeAttenuation: true,
  }));
  scene.add(dust);

  // 멀리 깔리는 별 먼지
  const farGeo = new T.BufferGeometry();
  const farPos = new Float32Array(300 * 3);
  for (let i = 0; i < 300; i++) {
    farPos[i * 3] = (Math.random() - 0.5) * 60;
    farPos[i * 3 + 1] = Math.random() * 22 - 2;
    farPos[i * 3 + 2] = -12 - Math.random() * 30;
  }
  farGeo.setAttribute('position', new T.BufferAttribute(farPos, 3));
  const farDust = new T.Points(farGeo, new T.PointsMaterial({
    size: 0.1, color: 0x88d8ff, transparent: true, opacity: 0.22, blending: T.AdditiveBlending, depthWrite: false,
  }));
  scene.add(farDust);

  /* --- 단계별 오브젝트 --- */
  const groups = STAGES.map((s) => {
    const g = buildStage(T, s, isPhone, isPortrait());
    g.vis = 0;
    g.userData.baseY = g.position.y;
    fadeMaterials(g);
    world.add(g);
    return g;
  });

  /* --- 상태 --- */
  const api = {
    setStage(i, immediate) {
      groups.forEach((g, k) => {
        g.userData.target = k === i ? 1 : 0;
        if (immediate) { g.vis = g.userData.target; applyVis(g); }
      });
      const acc = STAGES[i] && STAGES[i].accent ? STAGES[i].accent : '#7cf5c8';
      key.color.set(acc);
      gridUniforms.uColor.value.set(acc);
      gridUniforms.uAccent.value.set(acc);
    },
    update(dt, now, st) {
      const speed = st.playing ? 1 : 0.32;
      const t = now;
      gridUniforms.uTime.value = t * speed;

      // 단계 그룹 페이드
      groups.forEach((g, k) => {
        const target = k === st.idx ? 1 : 0;
        g.userData.target = target;
        g.vis = lerp(g.vis, target, Math.min(1, dt * (target ? 5.2 : 7.5)));
        if (Math.abs(g.vis - target) < 0.002) g.vis = target;
        applyVis(g);
        if (g.vis > 0.01 && g.userData.tick) g.userData.tick(t, dt, g.vis, st);
      });

      // 카메라: 천천히 다가가며 미세 궤도
      const prog = clamp(st.t / state.duration, 0, 1);
      const z = (fitZ - 1.15 * easeOut(prog)) + Math.sin(zProg(t)) * 0.08;
      const ang = Math.sin(t * 0.11) * 0.11;
      camera.position.x = Math.sin(ang) * z + pointer.x * 0.5;
      camera.position.z = Math.cos(ang) * z;
      camera.position.y = 2.7 + Math.sin(t * 0.17) * 0.16 - pointer.y * 0.35;
      camera.lookAt(0, 1.95 + Math.sin(t * 0.13) * 0.05, 0);

      // 입자 상승
      const pos = pGeo.attributes.position.array;
      for (let i = 0; i < PCOUNT; i++) {
        pos[i * 3 + 1] += dt * speed * (0.18 + pSeed[i] * 0.42);
        pos[i * 3] += Math.sin(t * 0.4 + i) * dt * 0.012 * speed;
        if (pos[i * 3 + 1] > 8.6) pos[i * 3 + 1] = 0.05;
      }
      pGeo.attributes.position.needsUpdate = true;
      dust.rotation.y = t * 0.012 * speed;
      farDust.rotation.y = -t * 0.006 * speed;
      floorGlow.material.opacity = 0.09 + Math.sin(t * 0.9) * 0.02;
      projector.rotation.y = t * 0.05 * speed;
      ringOuter.material.opacity = 0.4 + Math.sin(t * 1.1) * 0.1;
      ringInner.material.opacity = 0.24 + Math.sin(t * 1.4 + 1) * 0.07;

      if (composer) composer.render();
      else renderer.render(scene, camera);
    },
  };

  /* --- 후처리(블룸) --- */
  let composer = null;
  api.attachBloom = (c) => { composer = c; };
  api.renderer = renderer;
  api.scene = scene;
  api.camera = camera;

  /* --- 리사이즈 --- */
  const onResize = () => {
    const w = window.innerWidth, h = window.innerHeight;
    camera.aspect = w / h;
    fitZ = fit();
    camera.updateProjectionMatrix();
    renderer.setSize(w, h, false);
    if (composer) composer.setSize(w, h);
  };
  window.addEventListener('resize', onResize);
  window.addEventListener('orientationchange', () => setTimeout(onResize, 220));

  const pointer = { x: 0, y: 0 };
  window.addEventListener('pointermove', (e) => {
    if (e.pointerType === 'touch') return;
    pointer.x = (e.clientX / window.innerWidth - 0.5) * 2;
    pointer.y = (e.clientY / window.innerHeight - 0.5) * 2;
  });

  api.setStage(state.idx < 0 ? 0 : state.idx, true);
  return api;

  function applyVis(g) {
    const v = g.vis;
    g.visible = v > 0.004;
    g.scale.setScalar(0.82 + 0.18 * easeOut(clamp(v, 0, 1)));
    g.position.y = (g.userData.baseY || 0) + (1 - v) * 0.55;
    g.rotation.y = g.userData.baseRotY || 0;
    g.traverse((o) => {
      if (!o.material) return;
      const mats = Array.isArray(o.material) ? o.material : [o.material];
      mats.forEach((m) => {
        if (m.userData.base == null) return;
        m.opacity = m.userData.base * clamp(v, 0, 1);
      });
    });
  }
}

function zProg(t) { return t * 0.25; }

/* 블룸은 별도 시도: 실패해도 기본 렌더로 계속 */
async function loadBloom() {
  if (!sceneApi || !THREE) return;
  try {
    const [{ EffectComposer }, { RenderPass }, { UnrealBloomPass }, { OutputPass }] = await Promise.all([
      import('three/addons/postprocessing/EffectComposer.js'),
      import('three/addons/postprocessing/RenderPass.js'),
      import('three/addons/postprocessing/UnrealBloomPass.js'),
      import('three/addons/postprocessing/OutputPass.js'),
    ]);
    const w = window.innerWidth, h = window.innerHeight;
    const composer = new EffectComposer(sceneApi.renderer);
    composer.addPass(new RenderPass(sceneApi.scene, sceneApi.camera));
    const bloom = new UnrealBloomPass(new THREE.Vector2(w, h), 0.36, 0.5, 0.66);
    composer.addPass(bloom);
    composer.addPass(new OutputPass());
    composer.setSize(w, h);
    sceneApi.attachBloom(composer);
  } catch (e) {
    console.warn('[holo] 블룸 생략:', e);
  }
}

/* ---------------- 재질 페이드 준비 ---------------- */
function fadeMaterials(root) {
  root.traverse((o) => {
    if (!o.material) return;
    const mats = Array.isArray(o.material) ? o.material : [o.material];
    mats.forEach((m) => {
      if (m.userData.base == null) {
        m.userData.base = m.opacity == null ? 1 : m.opacity;
        m.transparent = true;
      }
    });
  });
}

/* ---------------- 텍스처 헬퍼 ---------------- */
function radialTexture(T, inner, outer) {
  const c = document.createElement('canvas');
  c.width = c.height = 128;
  const x = c.getContext('2d');
  const g = x.createRadialGradient(64, 64, 0, 64, 64, 64);
  g.addColorStop(0, inner);
  g.addColorStop(0.45, inner.replace(/[\d.]+\)$/, '0.35)'));
  g.addColorStop(1, outer);
  x.fillStyle = g;
  x.fillRect(0, 0, 128, 128);
  const tex = new T.CanvasTexture(c);
  tex.colorSpace = T.SRGBColorSpace;
  return tex;
}

function glowSprite(T, color, size, opacity) {
  const tex = radialTexture(T, hexA(color, 0.9), hexA(color, 0));
  const sp = new T.Sprite(new T.SpriteMaterial({
    map: tex, color: new T.Color(color), transparent: true, opacity,
    blending: T.AdditiveBlending, depthWrite: false,
  }));
  sp.scale.set(size, size, 1);
  return sp;
}

function hexA(hex, a) {
  let h = String(hex || '#7cf5c8').replace('#', '');
  if (h.length === 3) h = h.split('').map((c) => c + c).join('');
  const n = parseInt(h, 16);
  const r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
  return 'rgba(' + r + ',' + g + ',' + b + ',' + a + ')';
}

/* ---------------- 단계별 3D 오브젝트 ---------------- */
function buildStage(T, stage, isPhone, portrait) {
  const accent = stage.accent || '#7cf5c8';
  const g = new T.Group();
  g.userData.baseY = 1.9;
  g.position.set(0, 1.9, 0);

  const kind = stage.visual || stage.id || 'generic';
  if (kind === 'weather' || kind === 'sun') buildWeather(g, T, accent, stage, portrait);
  else if (kind === 'us' || kind === 'market') buildBars(g, T, accent, stage, portrait);
  else if (kind === 'kr') buildPanels(g, T, accent, stage, 'kr', portrait);
  else if (kind === 'calendar') buildPanels(g, T, accent, stage, 'cal', portrait);
  else buildPanels(g, T, accent, stage, 'cal', portrait);

  return g;
}

/* 01 날씨 — 회전하는 태양/빛 링 */
function buildWeather(g, T, accent, stage, portrait) {
  if (portrait) g.scale.setScalar(0.92);
  const shell = new T.Mesh(
    new T.IcosahedronGeometry(0.98, 1),
    new T.MeshBasicMaterial({ color: accent, wireframe: true, transparent: true, opacity: 0.5, blending: T.AdditiveBlending, depthWrite: false })
  );
  g.add(shell);

  const coreTex = radialTexture(T, 'rgba(255,232,180,0.95)', 'rgba(255,196,120,0)');
  const core = new T.Sprite(new T.SpriteMaterial({
    map: coreTex, color: 0xffe6b8, transparent: true, opacity: 0.5, blending: T.AdditiveBlending, depthWrite: false,
  }));
  core.scale.set(2.0, 2.0, 1);
  g.add(core);

  const glow = glowSprite(T, accent, 3.4, 0.14);
  g.add(glow);

  const rings = [];
  [[1.34, 0.011, 0.85], [1.62, 0.009, 0.55], [1.98, 0.007, 0.32]].forEach(([r, tube, op], i) => {
    const ring = new T.Mesh(
      new T.TorusGeometry(r, tube, 6, 128),
      new T.MeshBasicMaterial({ color: accent, transparent: true, opacity: op, blending: T.AdditiveBlending, depthWrite: false })
    );
    ring.rotation.x = Math.PI / 2 - 0.5 + i * 0.42;
    ring.rotation.y = i * 0.8;
    g.add(ring);
    rings.push(ring);
  });

  const rays = new T.Group();
  for (let i = 0; i < 14; i++) {
    const a = (i / 14) * Math.PI * 2;
    const len = 0.36 + (i % 3) * 0.12;
    const ray = new T.Mesh(
      new T.BoxGeometry(0.014, len, 0.014),
      new T.MeshBasicMaterial({ color: accent, transparent: true, opacity: 0.3, blending: T.AdditiveBlending, depthWrite: false })
    );
    ray.position.set(Math.cos(a) * (1.16 + len / 2), Math.sin(a) * (1.16 + len / 2), 0);
    ray.rotation.z = a - Math.PI / 2;
    rays.add(ray);
  }
  g.add(rays);

  g.userData.tick = (t, dt, vis) => {
    shell.rotation.y = t * 0.18;
    shell.rotation.x = Math.sin(t * 0.2) * 0.12;
    rays.rotation.z = -t * 0.22;
    rings.forEach((r, i) => {
      r.rotation.z = t * (0.1 + i * 0.05) * (i % 2 ? -1 : 1);
      r.rotation.x = Math.PI / 2 - 0.5 + i * 0.42 + Math.sin(t * 0.3 + i) * 0.12;
    });
    const pulse = 1 + Math.sin(t * 1.25) * 0.05;
    core.scale.set(2.0 * pulse, 2.0 * pulse, 1);
    glow.scale.setScalar(3.4 * (1 + Math.sin(t * 0.8) * 0.04));
  };
}

/* 02 미증시 — 하락 막대 · 파동 */
function buildBars(g, T, accent, stage, portrait) {
  const bars = (stage.metrics || []).filter((m) => typeof m.pct === 'number').slice(0, 5);
  const maxAbs = Math.max(1, ...bars.map((b) => Math.abs(b.pct)));
  const baseY = -1.9;

  bars.forEach((b, i) => {
    const x = (i - (bars.length - 1) / 2) * 0.92;
    const h = 0.45 + (Math.abs(b.pct) / maxAbs) * 2.5;
    const boxGeo = new T.BoxGeometry(0.42, 1, 0.42);
    const fill = new T.Mesh(boxGeo, new T.MeshBasicMaterial({
      color: accent, transparent: true, opacity: 0.1, depthWrite: false,
    }));
    fill.scale.y = h;
    fill.position.set(x, baseY + h / 2, 0);
    const edges = new T.LineSegments(
      new T.EdgesGeometry(boxGeo),
      new T.LineBasicMaterial({ color: accent, transparent: true, opacity: 0.9 })
    );
    edges.scale.y = h;
    edges.position.copy(fill.position);
    const cap = new T.Mesh(
      new T.PlaneGeometry(0.46, 0.46),
      new T.MeshBasicMaterial({ map: radialTexture(T, hexA(accent, 0.95), hexA(accent, 0)), color: new T.Color(accent), transparent: true, opacity: 0.55, blending: T.AdditiveBlending, depthWrite: false })
    );
    cap.rotation.x = -Math.PI / 2;
    cap.position.set(x, baseY + h + 0.01, 0);
    g.add(fill, edges, cap);

    b._ui = { h, x, fill, edges, cap, baseY };
  });

  // 떨어지는 궤적
  const pts = [];
  const N = 9;
  for (let i = 0; i < N; i++) {
    const x = -2.35 + (i / (N - 1)) * 4.7;
    const y = 1.35 - Math.pow(i / (N - 1), 0.72) * 2.0 + Math.sin(i * 1.1) * 0.16;
    pts.push(new T.Vector3(x, y, 0.55));
  }
  const curve = new T.CatmullRomCurve3(pts);
  const lineGeo = new T.BufferGeometry().setFromPoints(curve.getPoints(120));
  const trend = new T.Line(lineGeo, new T.LineBasicMaterial({ color: 0xff7a8c, transparent: true, opacity: 0.95 }));
  g.add(trend);

  const dots = pts.map((p) => {
    const d = new T.Mesh(
      new T.SphereGeometry(0.035, 8, 8),
      new T.MeshBasicMaterial({ color: 0xffd0d6, transparent: true, opacity: 0.95 })
    );
    d.position.copy(p);
    g.add(d);
    return d;
  });

  // S&P · VIX 패널
  const p1 = hologramPanel(T, 2.55, 0.78, {
    label: 'S&P 500 · 11/20',
    value: '6,910.39',
    sub: '−3.70%',
    accent,
  });
  p1.position.set(portrait ? 0 : -0.15, 0.98, -0.75);
  g.add(p1);

  const p2 = hologramPanel(T, 1.55, 0.62, {
    label: 'VIX · 공포탐욕',
    value: '35.89',
    sub: '공포 9.7',
    accent: '#ffbf5a',
    small: true,
  });
  p2.position.set(portrait ? 0.9 : 1.42, portrait ? -0.55 : -0.12, -0.35);
  g.add(p2);

  g.userData.tick = (t, dt, vis) => {
    const grow = easeOut(clamp((vis - 0.15) / 0.85, 0, 1));
    bars.forEach((b, i) => {
      const u = b._ui;
      const h = u.h * grow;
      u.fill.scale.y = Math.max(0.001, h);
      u.fill.position.y = u.baseY + h / 2;
      u.edges.scale.y = Math.max(0.001, h);
      u.edges.position.y = u.baseY + h / 2;
      u.cap.position.y = u.baseY + h + 0.01;
      u.cap.material.opacity = 0.5 * vis;
      u.fill.material.opacity = 0.1 * vis;
      u.edges.material.opacity = 0.9 * vis;
    });
    trend.material.opacity = 0.95 * clamp((vis - 0.35) / 0.65, 0, 1);
    trend.geometry.setDrawRange(0, Math.floor(120 * clamp((vis - 0.3) / 0.7, 0, 1)));
    dots.forEach((d, i) => {
      d.material.opacity = 0.9 * clamp((vis - 0.5) / 0.5, 0, 1);
      d.scale.setScalar(1 + Math.sin(t * 2.2 + i) * 0.18);
    });
    p1.position.y = 0.98 + Math.sin(t * 0.8) * 0.045;
    p2.position.y = -0.12 + Math.sin(t * 0.95 + 1) * 0.05;
  };
}

/* 03 국장 · 04 일정 — 유리판(홀로그램 패널) 스택 */
function buildPanels(g, T, accent, stage, kind, portrait) {
  const items = (stage.metrics || []).slice(0, 3);
  const wide = [
    { x: -0.95, y: 0.82, z: 0.22, ry: 0.2, w: 1.78, h: 0.88 },
    { x: 0.95, y: 0.82, z: -0.2, ry: -0.2, w: 1.78, h: 0.88 },
    { x: 0, y: -0.42, z: 0.42, ry: 0.0, w: 2.56, h: 0.8 },
  ];
  const tall = [
    { x: 0, y: 1.12, z: 0.1, ry: 0.06, w: 2.16, h: 0.76 },
    { x: 0, y: 0.14, z: 0.22, ry: -0.05, w: 2.16, h: 0.76 },
    { x: 0, y: -0.84, z: 0.34, ry: 0.03, w: 2.16, h: 0.72 },
  ];
  const layouts = portrait ? tall : wide;

  const panels = [];
  items.forEach((m, i) => {
    const L = layouts[i] || layouts[layouts.length - 1];
    const pct = typeof m.pct === 'number' ? m.pct : null;
    const p = hologramPanel(T, L.w, L.h, {
      label: (stage.no ? stage.no + ' · ' : '') + m.label,
      value: m.value,
      sub: pct != null ? (pct > 0 ? '+' : '') + pct.toFixed(2) + '%' : '',
      accent,
      down: pct != null && pct < 0,
      fill: pct != null ? Math.min(0.95, Math.abs(pct) / 6) : 0.3,
    });
    p.position.set(L.x || 0, L.y, L.z);
    p.rotation.y = L.ry;
    p.rotation.x = -0.04 + i * 0.012;
    g.add(p);
    panels.push({ mesh: p, L, i });
  });

  // 패널을 감싸는 궤도 링
  const orbit = new T.Group();
  const ring = new T.Mesh(
    new T.TorusGeometry(portrait ? 1.75 : 2.45, 0.006, 6, 160),
    new T.MeshBasicMaterial({ color: accent, transparent: true, opacity: 0.35, blending: T.AdditiveBlending, depthWrite: false })
  );
  ring.rotation.x = Math.PI / 2 + 0.12;
  orbit.add(ring);
  for (let i = 0; i < 26; i++) {
    const a = (i / 26) * Math.PI * 2;
    const tick = new T.Mesh(
      new T.BoxGeometry(0.006, 0.05 + (i % 4) * 0.02, 0.006),
      new T.MeshBasicMaterial({ color: accent, transparent: true, opacity: 0.4, blending: T.AdditiveBlending, depthWrite: false })
    );
    tick.position.set(Math.cos(a) * (portrait ? 1.75 : 2.45), 0, Math.sin(a) * (portrait ? 1.75 : 2.45));
    orbit.add(tick);
  }
  orbit.position.y = portrait ? -0.95 : -0.62;
  g.add(orbit);

  const halo = glowSprite(T, accent, 5.2, 0.16);
  halo.position.set(0, 0.2, -0.6);
  g.add(halo);

  g.userData.tick = (t, dt, vis) => {
    panels.forEach(({ mesh, L, i }) => {
      mesh.position.y = L.y + Math.sin(t * 0.75 + i * 1.4) * 0.05;
      mesh.rotation.y = L.ry + Math.sin(t * 0.4 + i) * 0.04;
    });
    orbit.rotation.y = t * (kind === 'kr' ? 0.16 : -0.12);
    orbit.rotation.z = Math.sin(t * 0.25) * 0.04;
  };
}

/* 유리판(홀로그램 패널) 한 장 — 캔버스 텍스처 */
function hologramPanel(T, w, h, opt) {
  const accent = opt.accent || '#7cf5c8';
  const pxW = 1024;
  const pxH = Math.round(pxW * (h / w));
  const canvas = document.createElement('canvas');
  canvas.width = pxW;
  canvas.height = pxH;
  const x = canvas.getContext('2d');
  const pad = 36;
  const fontSans = '"Noto Sans KR","Apple SD Gothic Neo","Malgun Gothic",system-ui,sans-serif';
  const fontMono = '"SF Mono","Roboto Mono","Noto Sans Mono CJK KR",ui-monospace,monospace';
  const label = String(opt.label || '').toUpperCase();
  const value = String(opt.value || '');
  const sub = String(opt.sub || '');

  // 유리 배경
  const bg = x.createLinearGradient(0, 0, pxW * 0.7, pxH);
  bg.addColorStop(0, 'rgba(11,34,34,0.94)');
  bg.addColorStop(0.55, 'rgba(5,16,20,0.9)');
  bg.addColorStop(1, 'rgba(8,26,29,0.93)');
  x.fillStyle = bg;
  x.beginPath();
  roundRect(x, 0, 0, pxW, pxH, 20);
  x.fill();

  // 안쪽 광
  const g2 = x.createRadialGradient(pxW * 0.2, pxH * 0.08, 0, pxW * 0.2, pxH * 0.08, pxW * 0.95);
  g2.addColorStop(0, hexA(accent, 0.15));
  g2.addColorStop(1, hexA(accent, 0));
  x.fillStyle = g2;
  x.fillRect(0, 0, pxW, pxH);

  // 스캔라인
  x.globalAlpha = 0.07;
  x.strokeStyle = 'rgba(255,255,255,0.7)';
  x.lineWidth = 1;
  for (let yy = 0; yy < pxH; yy += 6) {
    x.beginPath(); x.moveTo(0, yy); x.lineTo(pxW, yy); x.stroke();
  }
  x.globalAlpha = 1;

  // 테두리
  x.strokeStyle = hexA(accent, 0.68);
  x.lineWidth = 3;
  x.beginPath();
  roundRect(x, 1.5, 1.5, pxW - 3, pxH - 3, 20);
  x.stroke();

  // 상단 액센트 라인
  const grad = x.createLinearGradient(0, 0, pxW, 0);
  grad.addColorStop(0, hexA(accent, 0));
  grad.addColorStop(0.5, hexA(accent, 0.95));
  grad.addColorStop(1, hexA(accent, 0));
  x.fillStyle = grad;
  x.fillRect(0, 0, pxW, 4);

  // 코너 틱
  x.strokeStyle = hexA(accent, 0.95);
  x.lineWidth = 4;
  const k = 30;
  [[12, 12, 1, 1], [pxW - 12, 12, -1, 1], [12, pxH - 12, 1, -1], [pxW - 12, pxH - 12, -1, -1]].forEach(([cx, cy, dx, dy]) => {
    x.beginPath();
    x.moveTo(cx + dx * k, cy);
    x.lineTo(cx, cy);
    x.lineTo(cx, cy + dy * k);
    x.stroke();
  });

  // 라벨 + 값 + 보조 텍스트 — 패널 안에서 세로 가운데 정렬
  x.textBaseline = 'alphabetic';
  const labelSize = 32;
  const maxW = pxW - pad * 2 - 44;

  let size = Math.round(pxH * (opt.small ? 0.3 : 0.33));
  let vw = 0;
  for (; size > 22; size -= 3) {
    x.font = '600 ' + size + 'px ' + fontMono;
    vw = x.measureText(value).width;
    if (vw <= maxW) break;
  }
  let ss = 0, sw = 0;
  if (sub) {
    ss = Math.round(size * 0.4);
    for (; ss > 13; ss -= 2) {
      x.font = '500 ' + ss + 'px ' + fontSans;
      sw = x.measureText(sub).width;
      if (sw <= maxW) break;
    }
  }
  const gap1 = 16, gap2 = sub ? 16 : 0;
  const blockH = labelSize + gap1 + size * 0.78 + (sub ? gap2 + ss : 0);
  const top = Math.max(pad * 0.55, (pxH - blockH) / 2);
  const labelY = top + labelSize;
  const valueY = labelY + gap1 + size * 0.78;
  const subY = valueY + gap2 + ss;

  x.fillStyle = hexA(accent, 0.92);
  x.font = '600 ' + labelSize + 'px ' + fontSans;
  x.fillText(label, pad, labelY);

  x.font = '600 ' + size + 'px ' + fontMono;
  x.fillStyle = '#f4fffd';
  x.shadowColor = hexA(opt.down ? '#ff7a8c' : accent, 0.35);
  x.shadowBlur = 7;
  x.fillText(value, pad, valueY);
  x.shadowBlur = 0;

  if (sub) {
    x.font = '500 ' + ss + 'px ' + fontSans;
    x.fillStyle = hexA(accent, 0.95);
    x.fillText(sub, pad, subY);
    x.strokeStyle = hexA(accent, 0.32);
    x.lineWidth = 2;
    x.beginPath();
    x.moveTo(pad, subY + 9);
    x.lineTo(pad + sw, subY + 9);
    x.stroke();
  }

  // 아래쪽 진행 바(장식)
  x.fillStyle = hexA(accent, 0.18);
  x.fillRect(pad, pxH - pad * 0.62, maxW, 3);
  x.fillStyle = hexA(accent, 0.8);
  x.fillRect(pad, pxH - pad * 0.62, maxW * (opt.fill != null ? opt.fill : 0.42), 3);

  const tex = new T.CanvasTexture(canvas);
  tex.colorSpace = T.SRGBColorSpace;
  tex.anisotropy = 4;

  const group = new T.Group();
  const face = new T.Mesh(
    new T.PlaneGeometry(w, h),
    new T.MeshBasicMaterial({ map: tex, transparent: true, opacity: 0.97, depthWrite: false, side: T.DoubleSide })
  );
  group.add(face);

  const back = new T.Mesh(
    new T.PlaneGeometry(w * 0.99, h * 0.99),
    new T.MeshBasicMaterial({ color: new T.Color(accent), transparent: true, opacity: 0.05, blending: T.AdditiveBlending, depthWrite: false, side: T.DoubleSide })
  );
  back.position.z = -0.02;
  group.add(back);

  const edge = new T.LineSegments(
    new T.EdgesGeometry(new T.PlaneGeometry(w, h)),
    new T.LineBasicMaterial({ color: new T.Color(accent), transparent: true, opacity: 0.5 })
  );
  edge.position.z = 0.001;
  group.add(edge);

  return group;
}

function roundRect(ctx, x, y, w, h, r) {
  r = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}
