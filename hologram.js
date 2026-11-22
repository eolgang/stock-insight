/* =============================================================
   얼미리서치 · 얼미 브리핑
   J.A.R.V.I.S 스타일 3D 홀로그램 브리핑
   - Three.js: 프레넬 셸 + 지오데식 와이어프레임 + 분할 아크 링 + 노드 네트워크
   - WebAudio AnalyserNode: 음성 진폭/대역에 반응하는 코어·링·지평선 파동
   - 단계 전환: 깊은 Z축에서 fly-in / fly-out
   - data/briefing-hologram.json 의 텍스트·수치·타임라인·음성만 사용
   ============================================================= */
window.__holoBooted = true;

const DATA_URL = 'data/briefing-hologram.json';
const $ = (id) => document.getElementById(id);
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const lerp = (a, b, t) => a + (b - a) * t;
const easeOut = (t) => 1 - Math.pow(1 - t, 3);
const easeInOut = (t) => (t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2);
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = (s) => {
  s = Math.max(0, s || 0);
  const m = Math.floor(s / 60), r = Math.floor(s % 60);
  return m + ':' + String(r).padStart(2, '0');
};
const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const GATE_START = 0;   /* 단계 패널 본문 페이드 게이트 초기값 (입자 테두리 조립 후 열린다) */

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
let composer = null;
let speechOn = false;

/* 음성 반응 상태 (scene 과 공유) */
const AUDIO = { level: 0, low: 0, mid: 0, high: 0, live: false };

/* ---------------- 부트 ---------------- */
boot().catch((err) => {
  console.error('[holo]', err);
  document.body.classList.add('no3d');
  toast('브리핑 데이터를 불러오지 못했습니다. 새로고침해 주세요.');
});

async function boot() {
  const res = await fetch(DATA_URL + '?v=' + Date.now(), { cache: 'no-store' });
  if (!res.ok) throw new Error('data ' + res.status);
  DOC = await res.json();
  STAGES = Array.isArray(DOC.stages) ? DOC.stages : [];
  if (!STAGES.length) throw new Error('stages empty');
  if (STAGES.some((s) => s.start == null)) equalizeTimeline();
  state.duration = Number(DOC.audio && DOC.audio.duration) || STAGES[STAGES.length - 1].end || 45;

  buildHud();
  setupAudio();
  bindControls();
  renderCaption(0);

  try {
    THREE = await import('three');
    sceneApi = await initScene(THREE);
  } catch (err) {
    console.warn('[holo] 3D 초기화 실패:', err);
    document.body.classList.add('no3d');
    toast('3D를 표시할 수 없어 음성·자막으로 재생합니다.');
  }

  window.__holoAudio = AUDIO;
  requestAnimationFrame(loop);
  loadBloom();
}

function equalizeTimeline() {
  const per = (Number(DOC.audio && DOC.audio.duration) || STAGES.length * 10) / STAGES.length;
  STAGES.forEach((s, i) => { s.start = +(i * per).toFixed(2); s.end = +((i + 1) * per).toFixed(2); });
}

/* ---------------- HUD ---------------- */
function buildHud() {
  const ymd = String(DOC.date || '').split('-').join('. ');
  const wd = DOC.weekday || '';
  $('hudDate').textContent = ymd + (wd ? ' · ' + wd + '요일' : '');
  $('introNum').textContent = String(DOC.date || '').slice(5).replace('-', '.');
  $('introSub').textContent = (DOC.weekday ? DOC.weekday + '요일 · ' : '') + '약 ' + Math.round(state.duration) + '초';
  $('track').setAttribute('aria-valuemax', String(Math.round(state.duration)));

  $('marks').innerHTML = STAGES.map((s) =>
    '<i style="left:' + ((s.start / state.duration) * 100).toFixed(2) + '%"></i>'
  ).join('');
}

/* 자막 · 무3D 폴백 */
function renderCaption(i) {
  const s = STAGES[i];
  if (!s) return;
  document.documentElement.style.setProperty('--stage-accent', s.accent || '#7cf5c8');
  $('subtitle').innerHTML = esc(s.caption || '');
  $('nfBig').textContent = s.headline || '';
  $('nfSub').textContent = s.sub || '';
  $('nfCap').textContent = s.caption || '';
}

function toast(msg) {
  const t = $('toast');
  if (!t) return;
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove('show'), 4200);
}

/* =============================================================
   오디오 — AnalyserNode + 음성 리듬 합성 fallback
   ============================================================= */
let actx = null;
let analyser = null;
let mediaSrc = null;
let freqData = null;

function ensureAudioGraph() {
  if (actx) return;
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    actx = new Ctx();
    mediaSrc = actx.createMediaElementSource(voiceEl);
    analyser = actx.createAnalyser();
    analyser.fftSize = 512;
    analyser.smoothingTimeConstant = 0.72;
    freqData = new Uint8Array(analyser.frequencyBinCount);
    mediaSrc.connect(analyser);
    analyser.connect(actx.destination); // 반드시: 소리가 끊기지 않게
  } catch (e) {
    console.warn('[holo] WebAudio 사용 불가:', e);
    actx = null; analyser = null; mediaSrc = null; freqData = null;
  }
}

/* 사용자 클릭 핸들러 안에서 호출: 오디오 잠금 해제 */
function unlockAudio() {
  ensureAudioGraph();
  if (actx && actx.state !== 'running') {
    const p = actx.resume();
    if (p && p.catch) p.catch(() => { });
  }
}

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
  if (voiceEl.readyState >= 2) state.audioOk = true;
}

/* 음성 리듬 합성 (무음 모드 · WebAudio 제한 환경) */
function synthLevel(now) {
  if (!state.started) return 0.085 + 0.035 * Math.sin(now * 1.25) + 0.015 * Math.sin(now * 0.57);
  if (!state.playing) return 0.05 + 0.018 * Math.sin(now * 0.9);
  const s = STAGES[state.idx] || STAGES[0];
  const len = Math.max(0.4, (s.end || 10) - (s.start || 0));
  const p = clamp((state.t - (s.start || 0)) / len, 0, 1);
  const env = 0.55 + 0.45 * Math.sin(Math.PI * Math.pow(p, 0.85));
  const syl = Math.pow(Math.abs(Math.sin(now * Math.PI * 6.1)), 0.85);
  const syl2 = 0.5 + 0.5 * Math.sin(now * 11.3 + 1.7);
  const gate = 0.5 + 0.5 * Math.pow(0.5 + 0.5 * Math.sin(now * 0.85 + 2.2), 2);
  return clamp((0.1 + 0.72 * syl * (0.7 + 0.3 * syl2)) * env * (0.55 + 0.45 * gate), 0.04, 1);
}

/* 매 프레임 음성 분석 */
function sampleAudio(dt, now) {
  const hasAudio = state.playing && state.sound && state.audioOk &&
    !voiceEl.paused && !voiceEl.ended && voiceEl.readyState >= 2;
  let target = 0, low = 0, mid = 0, high = 0, live = false;

  if (analyser && actx && actx.state === 'running' && hasAudio) {
    analyser.getByteFrequencyData(freqData);
    const n = freqData.length;
    let acc = 0, l = 0, m = 0, h = 0, ln = 0, mn = 0, hn = 0;
    for (let i = 1; i < n; i++) {
      const v = freqData[i] / 255;
      acc += v;
      if (i < 9) { l += v; ln++; } else if (i < 42) { m += v; mn++; } else { h += v; hn++; }
    }
    low = ln ? clamp(l / ln * 1.5, 0, 1) : 0;
    mid = mn ? clamp(m / mn * 1.9, 0, 1) : 0;
    high = hn ? clamp(h / hn * 2.6, 0, 1) : 0;
    target = clamp((acc / (n - 1)) * 2.5, 0, 1);
    live = true;
  } else {
    target = synthLevel(now);
    low = target * 0.85;
    mid = target;
    high = target * 0.55;
  }
  if (state.playing) target = Math.max(target, 0.055);

  const ka = 1 - Math.exp(-dt * 22);
  const kr = 1 - Math.exp(-dt * 5.2);
  AUDIO.level += (target - AUDIO.level) * (target > AUDIO.level ? ka : kr);
  const kb = 1 - Math.exp(-dt * 12);
  AUDIO.low = lerp(AUDIO.low, low, kb);
  AUDIO.mid = lerp(AUDIO.mid, mid, kb);
  AUDIO.high = lerp(AUDIO.high, high, kb);
  AUDIO.live = live;
}

/* =============================================================
   재생 엔진
   ============================================================= */
function setSound(on) {
  state.sound = on;
  const btn = $('btnMute');
  btn.classList.toggle('on', on);
  btn.setAttribute('aria-label', on ? '소리 끄기' : '소리 켜기');
  if (!on) {
    try { voiceEl.pause(); } catch (e) { }
    stopSpeech();
  } else if (state.playing) {
    unlockAudio();
    if (state.audioOk) {
      try { voiceEl.currentTime = Math.min(state.t, state.duration - 0.1); } catch (e) { }
      voiceEl.play().catch(() => { state.audioOk = false; speakStage(); });
    } else speakStage();
  }
}

function startPlayback(withSound) {
  unlockAudio(); // 사용자 제스처 안에서 오디오 잠금 해제
  state.sound = withSound;
  const mb = $('btnMute');
  mb.classList.toggle('on', withSound);
  mb.setAttribute('aria-label', withSound ? '소리 끄기' : '소리 켜기');

  $('intro').classList.add('hide');
  $('subtitle').classList.add('show');
  document.body.classList.add('started');
  state.started = true;
  state.done = false;
  state.t = 0;
  state.idx = -1;
  state.playing = true;
  $('player').classList.remove('done');
  $('player').classList.add('playing');
  setStage(0);
  updateTime();

  if (withSound) {
    if (state.audioOk) {
      try { voiceEl.currentTime = 0; } catch (e) { }
      const p = voiceEl.play();
      if (p && p.catch) p.catch(() => { state.audioOk = false; toast('소리를 켤 수 없어 기기 음성으로 읽습니다.'); speakStage(); });
    } else speakStage();
  } else {
    stopSpeech();
  }
}

function togglePlay() {
  if (!state.started) { startPlayback(state.sound); return; }
  if (state.done) { restart(); return; }
  if (state.playing) pause(); else resume();
}

function pause() {
  state.playing = false;
  $('player').classList.remove('playing');
  try { voiceEl.pause(); } catch (e) { }
  stopSpeech();
}

function resume() {
  if (state.done) { restart(); return; }
  unlockAudio();
  state.playing = true;
  $('player').classList.add('playing');
  $('player').classList.remove('done');
  if (state.sound) {
    if (state.audioOk) {
      try { voiceEl.currentTime = Math.min(state.t, state.duration - 0.1); } catch (e) { }
      voiceEl.play().catch(() => { state.audioOk = false; speakStage(); });
    } else speakStage();
  }
}

function restart() {
  unlockAudio();
  state.t = 0;
  state.done = false;
  state.idx = -1;
  state.started = true;
  state.playing = true;
  $('player').classList.remove('done');
  $('player').classList.add('playing');
  $('subtitle').classList.add('show');
  setStage(0, true);
  try { voiceEl.currentTime = 0; } catch (e) { }
  if (state.sound) {
    if (state.audioOk) voiceEl.play().catch(() => { state.audioOk = false; speakStage(); });
    else speakStage();
  }
  updateTime();
}

function finish() {
  state.playing = false;
  state.done = true;
  state.t = state.duration;
  $('player').classList.remove('playing');
  $('player').classList.add('done');
  $('fill').style.width = '100%';
  $('tNow').textContent = fmt(state.t);
  try { voiceEl.pause(); } catch (e) { }
  stopSpeech();
}

function updateTime() {
  $('tNow').textContent = fmt(state.t);
  $('fill').style.width = (clamp(state.t / state.duration, 0, 1) * 100).toFixed(2) + '%';
  $('track').setAttribute('aria-valuenow', Math.round(state.t));
}

function jumpTo(i) {
  const s = STAGES[i];
  if (!s) return;
  if (!state.started) {
    state.started = true;
    $('intro').classList.add('hide');
    $('subtitle').classList.add('show');
    state.playing = true;
    $('player').classList.add('playing');
    unlockAudio();
  }
  state.t = Math.max(0, s.start - 0.1);
  state.done = false;
  $('player').classList.remove('done');
  state.idx = -1;
  setStage(i);
  updateTime();
  if (state.playing && state.sound && state.audioOk) {
    try { voiceEl.currentTime = state.t; } catch (e) { }
    voiceEl.play().catch(() => { state.audioOk = false; speakStage(); });
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

/* ---------------- 컨트롤 ---------------- */
function bindControls() {
  $('btnStartSound').addEventListener('click', () => startPlayback(true));
  $('btnStartSilent').addEventListener('click', () => startPlayback(false));
  $('btnPlay').addEventListener('click', togglePlay);
  $('btnRestart').addEventListener('click', restart);
  $('btnMute').addEventListener('click', () => setSound(!state.sound));

  const track = $('track');
  let dragging = false;
  const seekFrom = (e) => {
    const r = track.getBoundingClientRect();
    const x = (e.touches ? e.touches[0].clientX : e.clientX) - r.left;
    seek(clamp(x / r.width, 0, 0.999) * state.duration);
  };
  track.addEventListener('pointerdown', (e) => { dragging = true; track.setPointerCapture(e.pointerId); seekFrom(e); });
  track.addEventListener('pointermove', (e) => { if (dragging) seekFrom(e); });
  track.addEventListener('pointerup', () => { dragging = false; });
  track.addEventListener('pointercancel', () => { dragging = false; });

  document.addEventListener('keydown', (e) => {
    if (e.target && e.target.matches && e.target.matches('input,textarea')) return;
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
  if (!state.started) {
    state.started = true;
    $('intro').classList.add('hide');
    $('subtitle').classList.add('show');
  }
  state.t = clamp(t, 0, state.duration - 0.05);
  state.done = false;
  $('player').classList.remove('done');
  unlockAudio();
  if (state.sound && state.audioOk) { try { voiceEl.currentTime = state.t; } catch (e) { } }
  syncStage(true);
  updateTime();
}

/* ---------------- 타임라인 루프 ---------------- */
let lastTs = 0;
function loop(ts) {
  if (!lastTs) lastTs = ts;
  const dt = Math.min(0.1, Math.max(0.001, (ts - lastTs) / 1000));
  lastTs = ts;
  const now = ts / 1000;

  sampleAudio(dt, now);

  if (state.playing) {
    if (state.sound && state.audioOk && !voiceEl.paused && !voiceEl.ended && voiceEl.readyState >= 2) {
      state.t = voiceEl.currentTime;
    } else {
      state.t += dt;
    }
    if (state.t >= state.duration - 0.02) {
      state.t = state.duration;
      finish();
    } else {
      syncStage();
    }
    updateTime();
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

function syncStage(force) {
  const t = state.t;
  let i = STAGES.length - 1;
  for (let k = 0; k < STAGES.length; k++) {
    if (t < (STAGES[k].end || 0) - 0.01) { i = k; break; }
  }
  if (i !== state.idx || force) setStage(i);
}

/* =============================================================
   Three.js 씬
   ============================================================= */
function initScene(T) {
  const canvas = $('holo-canvas');
  const renderer = new T.WebGLRenderer({ canvas, antialias: true, alpha: false, powerPreference: 'high-performance' });
  const isPhone = Math.min(window.innerWidth, window.innerHeight) < 560;
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, isPhone ? 1.7 : 2));
  renderer.setSize(window.innerWidth, window.innerHeight, false);
  renderer.toneMapping = T.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 0.92;
  renderer.outputColorSpace = T.SRGBColorSpace;

  const scene = new T.Scene();
  scene.background = new T.Color(0x010605);

  const FOV = 42;
  const camera = new T.PerspectiveCamera(FOV, window.innerWidth / window.innerHeight, 0.1, 200);
  const layout = { d: 6, frac: 0.5, aspect: 1.6, w: 1, h: 1 };
  const pointer = { x: 0, y: 0, tx: 0, ty: 0 };

  /* ---------- 배경: 딥블랙 · 코어 주변만 옅은 빛 · 하단은 순수 여백 ---------- */
  const bgU = {
    uRes: { value: new T.Vector2(1, 1) },
    uTime: { value: 0 },
    uLevel: { value: 0 },
    uConv: { value: 0 },
    uCoreX: { value: 0.5 },
    uCoreY: { value: 0.42 },
    uHorizon: { value: 0.72 },
    uAccent: { value: new T.Color(0x7cf5c8) },
  };
  const bgMat = new T.ShaderMaterial({
    uniforms: bgU,
    depthTest: false,
    depthWrite: false,
    vertexShader: `
      varying vec2 vUv;
      void main(){
        vUv = uv;
        gl_Position = vec4(position.xy, 0.999, 1.0);
      }`,
    fragmentShader: `
      precision highp float;
      uniform vec2 uRes;
      uniform float uTime;
      uniform float uLevel;
      uniform float uConv;
      uniform float uCoreX;
      uniform float uCoreY;
      uniform float uHorizon;
      uniform vec3 uAccent;
      varying vec2 vUv;

      float hash21(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }

      void main(){
        float y = 1.0 - vUv.y;
        float x = vUv.x;
        float aspect = uRes.x / max(1.0, uRes.y);
        vec2 cp = vec2((x - uCoreX) * aspect, y - uCoreY);
        float cd = length(cp);

        /* 딥블랙 바탕 + 코어 뒤 아주 옅은 볼륨 */
        vec3 col = vec3(0.0006, 0.0017, 0.0016);
        col += uAccent * exp(-cd * 2.3) * 0.0115;
        col += uAccent * exp(-cd * 5.6) * (0.014 + 0.032 * uLevel + 0.024 * uConv);
        col += vec3(0.008, 0.024, 0.022) * exp(-cd * 1.15) * 0.55;

        /* 아래로 갈수록 조용한 딥블랙 (장식 없음) */
        col *= smoothstep(1.0, 0.34, y) * 0.96 + 0.04;

        /* 하단은 아무 장식도 두지 않는다 (딥블랙 여백) */

        /* 필름 그레인 */
        float g = hash21(vUv * uRes + fract(uTime * 0.37) * 53.0);
        col += (g - 0.5) * 0.0038;

        gl_FragColor = vec4(max(col, 0.0), 1.0);
      }`,
  });
  const bg = new T.Mesh(new T.PlaneGeometry(2, 2), bgMat);
  bg.frustumCulled = false;
  bg.renderOrder = -100;
  scene.add(bg);

  /* ---------- 코어 ---------- */
  const coreU = {
    uTime: { value: 0 },
    uLevel: { value: 0 },
    uAccent: { value: new T.Color(0x7cf5c8) },
  };
  const WAVE_GLSL = `
    float jarvisWave(vec3 p, float t){
      return sin(p.x * 3.1 + t * 1.15) * sin(p.y * 2.7 - t * 0.85)
           + 0.55 * sin(p.z * 4.3 + t * 1.9) * sin(p.x * 2.2 - t * 0.6)
           + 0.30 * sin(p.y * 6.1 + p.z * 3.4 + t * 2.6);
    }`;

  const core = new T.Group();
  scene.add(core);

  /* 프레넬 림 + 스캔라인 셸 */
  const shellMat = new T.ShaderMaterial({
    uniforms: coreU,
    transparent: true,
    depthWrite: false,
    side: T.DoubleSide,
    blending: T.AdditiveBlending,
    vertexShader: `
      uniform float uTime;
      uniform float uLevel;
      varying vec3 vN;
      varying vec3 vV;
      varying vec3 vP;
      ${WAVE_GLSL}
      void main(){
        vec3 n = normalize(position);
        float d = 1.0 + 0.0035 * sin(uTime * 0.95 + n.y * 2.0)
                      + uLevel * (0.018 + 0.075 * (0.5 + 0.5 * jarvisWave(n, uTime)));
        vec3 p = n * d;
        vec4 mv = modelViewMatrix * vec4(p, 1.0);
        vN = normalize(normalMatrix * n);
        vV = normalize(-mv.xyz);
        vP = p;
        gl_Position = projectionMatrix * mv;
      }`,
    fragmentShader: `
      precision highp float;
      uniform vec3 uAccent;
      uniform float uTime;
      uniform float uLevel;
      varying vec3 vN;
      varying vec3 vV;
      varying vec3 vP;
      void main(){
        float fres = pow(1.0 - abs(dot(normalize(vN), normalize(vV))), 4.2);
        float scan = 0.5 + 0.5 * sin(vP.y * 52.0 - uTime * 3.4);
        float scan2 = 0.5 + 0.5 * sin(vP.y * 190.0 + uTime * 7.0);
        float a = fres * 0.14 + scan * 0.014 + scan2 * 0.006;
        a *= 0.55 + 0.75 * uLevel;
        gl_FragColor = vec4(uAccent, clamp(a, 0.0, 0.85));
      }`,
  });
  const shell = new T.Mesh(new T.IcosahedronGeometry(1.0, isPhone ? 3 : 4), shellMat);
  core.add(shell);

  /* 지오데식 와이어프레임 (2겹) */
  function wireMat(opacity, disp) {
    return new T.ShaderMaterial({
      uniforms: {
        uTime: coreU.uTime,
        uLevel: coreU.uLevel,
        uColor: coreU.uAccent,
        uOpacity: { value: opacity },
        uDisp: { value: disp },
      },
      transparent: true,
      depthWrite: false,
      blending: T.AdditiveBlending,
      vertexShader: `
        uniform float uTime;
        uniform float uLevel;
        uniform float uDisp;
        varying float vShade;
        ${WAVE_GLSL}
        void main(){
          vec3 n = normalize(position);
          float d = 1.0 + 0.0030 * sin(uTime * 1.05 + n.y * 2.2)
                        + uLevel * uDisp * (0.010 + 0.050 * (0.5 + 0.5 * jarvisWave(n, uTime)));
          vec3 p = n * (length(position) * d);
          vShade = 0.55 + 0.45 * sin(p.y * 9.0 + uTime * 2.4) * sin(p.x * 7.0 - uTime * 1.6);
          gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
        }`,
      fragmentShader: `
        uniform vec3 uColor;
        uniform float uOpacity;
        varying float vShade;
        void main(){
          gl_FragColor = vec4(uColor, uOpacity * (0.72 + 0.28 * vShade));
        }`,
    });
  }
  const wire1 = new T.LineSegments(new T.WireframeGeometry(new T.IcosahedronGeometry(1.012, 2)), wireMat(0.27, 1.0));
  core.add(wire1);

  const wire2 = new T.LineSegments(new T.WireframeGeometry(new T.IcosahedronGeometry(1.08, 3)), wireMat(0.055, 0.55));
  core.add(wire2);

  /* 내부 연결 코드(대각선 네트워크) + 노드 */
  const chordGroup = new T.Group();
  const chordPos = [];
  const nodePos = [];
  for (let i = 0; i < 96; i++) {
    const a = randomDir(T), b = randomDir(T);
    const r1 = 0.62 + Math.random() * 0.3, r2 = 0.62 + Math.random() * 0.3;
    chordPos.push(a.x * r1, a.y * r1, a.z * r1, b.x * r2, b.y * r2, b.z * r2);
  }
  for (let i = 0; i < 64; i++) {
    const d = randomDir(T);
    nodePos.push(d.x, d.y, d.z);
  }
  const chordGeo = new T.BufferGeometry();
  chordGeo.setAttribute('position', new T.Float32BufferAttribute(chordPos, 3));
  chordGroup.add(new T.LineSegments(chordGeo, new T.LineBasicMaterial({
    color: 0x7cf5c8, transparent: true, opacity: 0.07, blending: T.AdditiveBlending, depthWrite: false,
  })));
  const nodeGeo = new T.BufferGeometry();
  nodeGeo.setAttribute('position', new T.Float32BufferAttribute(nodePos, 3));
  const nodeMat = new T.PointsMaterial({
    size: 0.028, map: dotTexture(T), color: 0xd8fff4, transparent: true, opacity: 0.85,
    blending: T.AdditiveBlending, depthWrite: false, sizeAttenuation: true,
  });
  chordGroup.add(new T.Points(nodeGeo, nodeMat));
  core.add(chordGroup);

  /* 내부 발광 코어 */
  const glowTex = radialTexture(T, 'rgba(220,255,246,1)', 'rgba(124,245,200,0)');
  const coreGlow = new T.Sprite(new T.SpriteMaterial({
    map: glowTex, color: 0x9dffdf, transparent: true, opacity: 0.30,
    blending: T.AdditiveBlending, depthWrite: false,
  }));
  coreGlow.scale.set(1.15, 1.15, 1);
  core.add(coreGlow);

  const coreGlow2 = new T.Sprite(new T.SpriteMaterial({
    map: glowTex, color: 0xf2fffa, transparent: true, opacity: 0.22,
    blending: T.AdditiveBlending, depthWrite: false,
  }));
  coreGlow2.scale.set(0.42, 0.42, 1);
  core.add(coreGlow2);

  const innerOcta = new T.LineSegments(
    new T.EdgesGeometry(new T.IcosahedronGeometry(0.34, 1)),
    new T.LineBasicMaterial({ color: 0xd6fff2, transparent: true, opacity: 0.42, blending: T.AdditiveBlending, depthWrite: false })
  );
  core.add(innerOcta);

  /* =============================================================
     링 시스템 — 균일한 선 원이 아니라, 불규칙하게 끊긴 빛의 호
     · 세그먼트마다 길이·두께·밝기·기울기 다름
     · 밝은 머리 → 꼬리로 꺼지는 그라디언트, 끝은 뾰족하게 taper
     · 깊이(코어 앞/뒤)에 따라 밝기 변화 → 코어를 감싸 도는 입체감
     · 일정 각속도 대신 아주 느리게 흔들리는 속도, 단계 전환 시 벌어졌다 조여듦
     ============================================================= */
  const ringUniforms = {
    uTime: coreU.uTime,
    uAccent: coreU.uAccent,
    uLevel: coreU.uLevel,
    uCoreZ: { value: -6 },
    uRingDim: { value: 1 },
  };

  function arcBandGeometry(o) {
    const N = o.segs || 110;
    const pos = [], uv = [], idx = [];
    for (let i = 0; i <= N; i++) {
      const u = i / N;
      const a = o.from + o.len * u;
      const taper = Math.pow(Math.max(0.0001, Math.sin(Math.PI * u)), o.taperPow == null ? 0.6 : o.taperPow);
      const th = o.thick * (0.16 + 0.84 * taper);
      const ca = Math.cos(a), sa = Math.sin(a);
      const z = Math.sin(u * Math.PI * o.waves + o.wavePhase) * o.bulge;
      for (let k = 0; k < 2; k++) {
        const rr = o.r + (k === 0 ? -th * 0.5 : th * 0.5);
        pos.push(ca * rr, sa * rr, z);
        uv.push(u, k);
      }
    }
    for (let i = 0; i < N; i++) {
      const a0 = i * 2, b0 = a0 + 1, a1 = a0 + 2, b1 = a0 + 3;
      idx.push(a0, b0, a1, b0, b1, a1);
    }
    const g = new T.BufferGeometry();
    g.setAttribute('position', new T.Float32BufferAttribute(pos, 3));
    g.setAttribute('uv', new T.Float32BufferAttribute(uv, 2));
    g.setIndex(idx);
    return g;
  }

  function arcMat(opts) {
    return new T.ShaderMaterial({
      uniforms: {
        uTime: ringUniforms.uTime,
        uAccent: ringUniforms.uAccent,
        uLevel: ringUniforms.uLevel,
        uCoreZ: ringUniforms.uCoreZ,
        uRingDim: ringUniforms.uRingDim,
        uAlpha: { value: opts.alpha },
        uSeed: { value: opts.seed },
        uDir: { value: opts.dir },
        uWave: { value: opts.wave },
        uWaveAmt: { value: opts.waveAmt },
      },
      transparent: true,
      depthWrite: false,
      side: T.DoubleSide,
      blending: T.AdditiveBlending,
      vertexShader: `
        uniform float uTime;
        uniform float uSeed;
        uniform float uWave;
        uniform float uWaveAmt;
        varying float vU;
        varying float vV;
        varying float vZ;
        varying vec3 vN;
        varying vec3 vVw;
        void main(){
          vU = uv.x;
          vV = uv.y;
          vec3 p = position;
          p.z += sin(uv.x * uWave * 6.2831 + uTime * 0.42 + uSeed * 6.2831) * uWaveAmt;
          vec4 mv = modelViewMatrix * vec4(p, 1.0);
          vZ = mv.z;
          vN = normalize(normalMatrix * vec3(0.0, 0.0, 1.0));
          vVw = normalize(-mv.xyz);
          gl_Position = projectionMatrix * mv;
        }`,
      fragmentShader: `
        precision highp float;
        uniform vec3 uAccent;
        uniform float uAlpha;
        uniform float uTime;
        uniform float uSeed;
        uniform float uCoreZ;
        uniform float uLevel;
        uniform float uRingDim;
        uniform float uDir;
        varying float vU;
        varying float vV;
        varying float vZ;
        varying vec3 vN;
        varying vec3 vVw;
        void main(){
          /* 폭 방향: 선명한 중심선 + 그 뒤로 번지는 빛 (홀로그램 기기 느낌) */
          float band = smoothstep(0.0, 0.40, vV) * (1.0 - smoothstep(0.60, 1.0, vV));
          float lineC = exp(-pow((vV - 0.5) * 6.2, 2.0));

          /* 길이 방향: 머리는 밝고 꼬리는 꺼지듯 */
          float uu = uDir > 0.0 ? vU : 1.0 - vU;
          float head = pow(smoothstep(0.0, 1.0, uu), 1.15);

          /* 아주 느린 호흡 + 흐르는 결 */
          float breathe = 0.80 + 0.20 * sin(uu * 4.2 - uTime * 0.55 + uSeed * 6.2831);
          float streak = 0.88 + 0.12 * sin(uu * 38.0 + uTime * 0.9 + uSeed * 12.0);

          float a = (band * 0.20 + lineC * 0.95) * (0.05 + 0.95 * head) * breathe * streak * uAlpha;

          /* 깊이: 코어 뒤로 돌면 어둡고, 앞으로 나오면 밝다 */
          float dm = smoothstep(uCoreZ - 0.80, uCoreZ + 0.80, vZ);
          a *= mix(0.16, 1.0, dm);

          /* 프레넬: 비껴 보이는 부분은 진하게, 정면은 옅게 */
          float face = abs(dot(vN, vVw));
          a *= mix(1.25, 0.72, face);

          a *= 0.88 + 0.32 * uLevel;
          a *= uRingDim;

          vec3 c = mix(uAccent, vec3(0.78, 0.99, 0.96), 0.16 + 0.46 * head);
          gl_FragColor = vec4(c, clamp(a, 0.0, 1.0));
        }`,
    });
  }

  const tickMatFor = (seed) => new T.ShaderMaterial({
    uniforms: {
      uColor: ringUniforms.uAccent,
      uLevel: ringUniforms.uLevel,
      uCoreZ: ringUniforms.uCoreZ,
      uRingDim: ringUniforms.uRingDim,
      uAlpha: { value: 0.55 + seed * 0.45 },
    },
    transparent: true,
    depthWrite: false,
    side: T.DoubleSide,
    blending: T.AdditiveBlending,
    vertexShader: `
      attribute float aAlpha;
      varying float vA;
      varying float vZ;
      void main(){
        vA = aAlpha;
        vec4 mv = modelViewMatrix * vec4(position, 1.0);
        vZ = mv.z;
        gl_Position = projectionMatrix * mv;
      }`,
    fragmentShader: `
      uniform vec3 uColor;
      uniform float uLevel;
      uniform float uCoreZ;
      uniform float uRingDim;
      uniform float uAlpha;
      varying float vA;
      varying float vZ;
      void main(){
        float dm = smoothstep(uCoreZ - 0.80, uCoreZ + 0.80, vZ);
        float a = vA * uAlpha * uRingDim * mix(0.18, 1.0, dm) * (0.8 + 0.4 * uLevel);
        gl_FragColor = vec4(uColor, clamp(a, 0.0, 1.0));
      }`,
  });

  const ringSys = [];
  const ringDefs = [
    { r: 1.07, thick: 0.026, tilt: [0.84, 0.08, 0.05], w: 0.155, op: 0.95, parts: 7, cover: 0.62, wob: 0.008, ticks: 30, tickR: 0.02 },
    { r: 1.19, thick: 0.022, tilt: [0.80, 0.22, 0.30], w: -0.115, op: 0.72, parts: 8, cover: 0.54, wob: 0.009, ticks: 24, tickR: 0.014 },
    { r: 1.31, thick: 0.017, tilt: [0.87, 0.12, 0.58], w: 0.075, op: 0.48, parts: 6, cover: 0.46, wob: 0.010, ticks: 0, tickR: 0 },
    { r: 1.43, thick: 0.013, tilt: [0.76, 0.28, 0.16], w: -0.05, op: 0.22, parts: 5, cover: 0.40, wob: 0.012, ticks: 14, tickR: 0.008 },
  ];

  ringDefs.forEach((def, ri) => {
    const g = new T.Group();
    g.rotation.set(def.tilt[0], def.tilt[1], def.tilt[2]);
    core.add(g);

    const segs = [];
    let a0 = Math.random() * Math.PI * 2;
    const total = Math.PI * 2 * def.cover;
    const raw = [];
    for (let k = 0; k < def.parts; k++) raw.push(0.5 + Math.random() * 1.1);
    const sum = raw.reduce((x, y) => x + y, 0);

    for (let k = 0; k < def.parts; k++) {
      const len = total * (raw[k] / sum);
      const gap = ((Math.PI * 2 - total) / def.parts) * (0.45 + Math.random() * 1.35);
      const geo = arcBandGeometry({
        r: def.r * (0.985 + Math.random() * 0.03),
        thick: def.thick * (0.75 + Math.random() * 0.6),
        from: a0,
        len,
        segs: isPhone ? 74 : 120,
        bulge: def.wob * (0.2 + Math.random() * 0.6),
        wavePhase: Math.random() * 6.28,
        waves: 1.1 + Math.random() * 2.2,
        taperPow: 0.30 + Math.random() * 0.25,
      });
      const mesh = new T.Mesh(geo, arcMat({
        alpha: def.op * (0.62 + Math.random() * 0.7),
        seed: Math.random(),
        dir: def.w >= 0 ? 1 : -1,
        wave: 0.6 + Math.random() * 1.4,
        waveAmt: def.wob * (0.16 + Math.random() * 0.3),
      }));
      mesh.userData = {
        amp: 0.004 + Math.random() * 0.016,
        ph: Math.random() * 6.28,
        k: 0.055 + Math.random() * 0.10,
        exK: (Math.random() - 0.5) * 0.16,
      };
      g.add(mesh);
      segs.push(mesh);
      a0 += len + gap;
    }

    /* 눈금: 개수·길이·밝기를 불규칙하게 흩뿌린다 */
    if (def.ticks) {
      const tp = [], ta = [], ti = [];
      let vc = 0;
      const n = def.ticks;
      for (let i = 0; i < n; i++) {
        if (Math.random() < 0.3) continue;
        const ang = (i / n) * Math.PI * 2 + (Math.random() - 0.5) * 0.06;
        const len = def.thick * (2.0 + Math.random() * 8.5);
        const wdt = 0.0012 + Math.random() * 0.0024;
        const rr = def.r + def.thick * (1.6 + Math.random() * 3.2);
        const ca = Math.cos(ang), sa = Math.sin(ang);
        const nx = -sa * wdt, ny = ca * wdt;
        const x0 = ca * rr, y0 = sa * rr;
        const x1 = ca * (rr + len), y1 = sa * (rr + len);
        const al = 0.2 + Math.random() * 0.8;
        tp.push(x0 - nx, y0 - ny, 0, x0 + nx, y0 + ny, 0, x1 + nx, y1 + ny, 0, x1 - nx, y1 - ny, 0);
        ta.push(al, al, 0.0, 0.0);
        ti.push(vc, vc + 1, vc + 2, vc, vc + 2, vc + 3);
        vc += 4;
      }
      if (vc) {
        const tg = new T.BufferGeometry();
        tg.setAttribute('position', new T.Float32BufferAttribute(tp, 3));
        tg.setAttribute('aAlpha', new T.Float32BufferAttribute(ta, 1));
        tg.setIndex(ti);
        g.add(new T.Mesh(tg, tickMatFor(Math.random())));
      }
    }

    ringSys.push({ g, def, segs, ang: Math.random() * 6.28, phase: Math.random() * 12 });
  });

  /* =============================================================
     에너지 입자 — 평소엔 아주 느린 궤도, 말할 때 코어로 스며들며
     모였다가 플레어 후 조용히 흩어진다 (튀지 않게 스프링 이징)
     ============================================================= */
  const EPC = reduced ? 150 : (isPhone ? 330 : 520);
  const ep = {
    ang: new Float32Array(EPC),
    r0: new Float32Array(EPC),
    rIn: new Float32Array(EPC),
    y0: new Float32Array(EPC),
    bobA: new Float32Array(EPC),
    bobS: new Float32Array(EPC),
    bobP: new Float32Array(EPC),
    spd: new Float32Array(EPC),
    swirl: new Float32Array(EPC),
    stag: new Float32Array(EPC),
    size: new Float32Array(EPC),
    tint: new Float32Array(EPC),
    bright: new Float32Array(EPC),
  };
  const ePos = new Float32Array(EPC * 3);
  const eAlpha = new Float32Array(EPC);
  const h1 = new Float32Array(EPC * 3);
  const h2 = new Float32Array(EPC * 3);

  for (let i = 0; i < EPC; i++) {
    const sy = Math.random() * 2 - 1;
    ep.y0[i] = (Math.random() < 0.74 ? sy * 1.30 : sy * 2.45);
    const lat = Math.sqrt(Math.max(0.10, 1 - Math.pow(Math.abs(ep.y0[i]) / 2.85, 2)));
    ep.r0[i] = (1.42 + Math.pow(Math.random(), 0.78) * 2.85) * lat;
    ep.ang[i] = Math.random() * Math.PI * 2;
    ep.rIn[i] = 1.03 + Math.random() * 0.28;
    ep.bobA[i] = 0.05 + Math.random() * 0.26;
    ep.bobS[i] = 0.16 + Math.random() * 0.44;
    ep.bobP[i] = Math.random() * Math.PI * 2;
    ep.spd[i] = (0.030 + Math.random() * 0.075) * (Math.random() < 0.5 ? -1 : 1);
    ep.swirl[i] = (0.45 + Math.random() * 1.5) * (Math.random() < 0.5 ? -1 : 1);
    ep.stag[i] = Math.random() * 0.5;
    ep.size[i] = 0.014 + Math.pow(Math.random(), 2.4) * 0.030;
    ep.tint[i] = Math.random();
    ep.bright[i] = 0.30 + Math.random() * 0.70;
  }

  const eGeo = new T.BufferGeometry();
  eGeo.setAttribute('position', new T.BufferAttribute(ePos, 3));
  eGeo.setAttribute('aSize', new T.Float32BufferAttribute(Float32Array.from(ep.size), 1));
  eGeo.setAttribute('aAlpha', new T.BufferAttribute(eAlpha, 1));
  eGeo.setAttribute('aTint', new T.Float32BufferAttribute(Float32Array.from(ep.tint), 1));

  const eU = {
    uSize: { value: 1000 },
    uAccent: coreU.uAccent,
    uC2: { value: new T.Color(0xe4fffa) },
  };
  const eMat = new T.ShaderMaterial({
    uniforms: eU,
    transparent: true,
    depthWrite: false,
    blending: T.AdditiveBlending,
    vertexShader: `
      attribute float aSize;
      attribute float aAlpha;
      attribute float aTint;
      uniform float uSize;
      varying float vA;
      varying float vT;
      void main(){
        vA = aAlpha;
        vT = aTint;
        vec4 mv = modelViewMatrix * vec4(position, 1.0);
        gl_PointSize = aSize * uSize / max(0.5, -mv.z);
        gl_Position = projectionMatrix * mv;
      }`,
    fragmentShader: `
      precision highp float;
      uniform vec3 uAccent;
      uniform vec3 uC2;
      varying float vA;
      varying float vT;
      void main(){
        vec2 q = gl_PointCoord - 0.5;
        float r = length(q) * 2.0;
        if (r > 1.0) discard;
        float core = pow(1.0 - r, 2.8);
        float halo = pow(1.0 - r, 1.1) * 0.16;
        vec3 c = mix(uAccent, uC2, vT * 0.8);
        gl_FragColor = vec4(c, (core + halo) * vA);
      }`,
  });
  const ePoints = new T.Points(eGeo, eMat);
  ePoints.frustumCulled = false;
  core.add(ePoints);

  /* 잔궤적 */
  const trailGeo = new T.BufferGeometry();
  const tPos = new Float32Array(EPC * 4 * 3);
  const tAlpha = new Float32Array(EPC * 4);
  trailGeo.setAttribute('position', new T.BufferAttribute(tPos, 3));
  trailGeo.setAttribute('aAlpha', new T.BufferAttribute(tAlpha, 1));
  const trailMat = new T.ShaderMaterial({
    uniforms: { uColor: coreU.uAccent },
    transparent: true,
    depthWrite: false,
    blending: T.AdditiveBlending,
    vertexShader: `
      attribute float aAlpha;
      varying float vA;
      void main(){
        vA = aAlpha;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }`,
    fragmentShader: `
      uniform vec3 uColor;
      varying float vA;
      void main(){ gl_FragColor = vec4(uColor, clamp(vA, 0.0, 1.0)); }`,
  });
  const trails = new T.LineSegments(trailGeo, trailMat);
  trails.frustumCulled = false;
  core.add(trails);

  /* 수렴 시 코어에 번지는 빛 */
  const eFlash = new T.Sprite(new T.SpriteMaterial({
    map: glowTex, color: 0xd6fff6, transparent: true, opacity: 0,
    blending: T.AdditiveBlending, depthWrite: false,
  }));
  eFlash.scale.set(2.4, 2.4, 1);
  core.add(eFlash);

  /* =============================================================
     바깥 표류 입자 + 단계 HUD 프레임 조립
     - 화면 넓은 영역에 아주 옅은 빛가루가 늘 느리게 떠다닌다.
     - 단계가 시작되면 그중 일부가 패널 테두리 위로 날아가 프레임을 짜맞춘다.
       (모서리 브래킷 → 변을 따라 스캔, 완성 뒤 패널 몸체가 늦게 열린다)
     - 단계가 끝나면 테두리가 다시 입자로 풀려 표류 궤도로 흩어진다.
     ============================================================= */
  const FRAME_W = 3.16, FRAME_H = 0.97, FRAME_CUT = 0.077;
  const FSLOT = reduced ? 28 : (isPhone ? 38 : 54);
  const FRAME_COLORS = { weather: 0x8ff0dc, us: 0xff6f86, kr: 0xff7f92, calendar: 0x8beed6 };

  const frameRoot = new T.Group();
  scene.add(frameRoot);

  function buildFrame(T, color) {
    const w = FRAME_W / 2, h = FRAME_H / 2, c = FRAME_CUT;
    const pts = [
      [-w + c, h], [w - c, h], [w, h - c], [w, -h + c],
      [w - c, -h], [-w + c, -h], [-w, -h + c], [-w, h - c], [-w + c, h],
    ];
    const pos = [], prog = [];
    let total = 0;
    for (let i = 1; i < pts.length; i++) total += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);

    /* 변: 0.12 ~ 1.0 구간을 따라 채워진다 */
    let acc = 0;
    for (let i = 1; i < pts.length; i++) {
      const L = Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]) / total;
      pos.push(pts[i - 1][0], pts[i - 1][1], 0, pts[i][0], pts[i][1], 0);
      prog.push(0.12 + acc * 0.88, 0.12 + (acc + L) * 0.88);
      acc += L;
    }

    /* 모서리 브래킷: 조립 초반에 먼저 박힌다 */
    const K = 0.17;
    const corners = [[w, h, -1, -1], [-w, h, 1, -1], [-w, -h, 1, 1], [w, -h, -1, 1]];
    corners.forEach(([cx, cy, dx, dy], i) => {
      const p0 = 0.004 + i * 0.026;
      pos.push(cx + dx * K, cy, 0, cx, cy, 0, cx, cy, 0, cx, cy + dy * K, 0);
      prog.push(p0, p0 + 0.014, p0 + 0.014, p0 + 0.028);
    });

    const g = new T.BufferGeometry();
    g.setAttribute('position', new T.Float32BufferAttribute(pos, 3));
    g.setAttribute('aProg', new T.Float32BufferAttribute(prog, 1));

    const uni = {
      uProg: { value: 0 },
      uAlpha: { value: 0 },
      uColor: { value: new T.Color(color) },
      uHead: { value: 0 },
    };
    const mat = new T.ShaderMaterial({
      uniforms: uni,
      transparent: true,
      depthWrite: false,
      depthTest: false,
      blending: T.AdditiveBlending,
      vertexShader: `
        attribute float aProg;
        varying float vP;
        void main(){
          vP = aProg;
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        }`,
      fragmentShader: `
        precision highp float;
        uniform float uProg;
        uniform float uAlpha;
        uniform vec3 uColor;
        uniform float uHead;
        varying float vP;
        void main(){
          float built = 1.0 - smoothstep(uProg - 0.018, uProg + 0.018, vP);
          float head = exp(-abs(vP - uProg) * 40.0) * uHead;
          float a = uAlpha * (built * 0.58 + head * 1.95);
          vec3 c = mix(uColor, vec3(1.0), clamp(head * 0.55, 0.0, 1.0));
          gl_FragColor = vec4(c, clamp(a, 0.0, 1.0));
        }`,
    });
    const mesh = new T.LineSegments(g, mat);
    mesh.frustumCulled = false;
    mesh.renderOrder = 26;

    /* 입자가 도착할 지점: 변을 따라 고르게 */
    const targets = new Float32Array(FSLOT * 3);
    const progOf = new Float32Array(FSLOT);
    let travelled = 0, seg = 1;
    for (let s = 0; s < FSLOT; s++) {
      const want = (s + 0.5) / FSLOT * total;
      while (seg < pts.length - 1 && travelled + Math.hypot(pts[seg][0] - pts[seg - 1][0], pts[seg][1] - pts[seg - 1][1]) < want) {
        travelled += Math.hypot(pts[seg][0] - pts[seg - 1][0], pts[seg][1] - pts[seg - 1][1]);
        seg++;
      }
      const segLen = Math.hypot(pts[seg][0] - pts[seg - 1][0], pts[seg][1] - pts[seg - 1][1]) || 1;
      const f = clamp((want - travelled) / segLen, 0, 1);
      targets[s * 3] = lerp(pts[seg - 1][0], pts[seg][0], f);
      targets[s * 3 + 1] = lerp(pts[seg - 1][1], pts[seg][1], f);
      targets[s * 3 + 2] = 0;
      progOf[s] = 0.12 + (want / total) * 0.88;
    }

    return { mesh, uni, targets, progOf, t: 0, alive: 0, active: false, prog: 0 };
  }

  const frameSys = STAGES.map((s) => {
    const hex = FRAME_COLORS[s.id] || String(s.accent || '#8ff0dc').replace('#', '');
    const col = typeof hex === 'number' ? hex : parseInt(hex, 16) || 0x8ff0dc;
    const fr = buildFrame(T, col);
    fr.mesh.visible = false;
    frameRoot.add(fr.mesh);
    return fr;
  });

  /* ---------- 표류 입자 ---------- */
  const DUST = reduced ? 70 : (isPhone ? 100 : 140);
  const du = {
    ang: new Float32Array(DUST), r: new Float32Array(DUST), y: new Float32Array(DUST),
    spd: new Float32Array(DUST), ph: new Float32Array(DUST), ph2: new Float32Array(DUST),
    w1: new Float32Array(DUST), w2: new Float32Array(DUST), w3: new Float32Array(DUST),
    size: new Float32Array(DUST), bright: new Float32Array(DUST), cap: new Float32Array(DUST),
    slot: new Int16Array(DUST),
  };
  const dPos = new Float32Array(DUST * 3);
  const dAlpha = new Float32Array(DUST);

  for (let i = 0; i < DUST; i++) {
    du.ang[i] = Math.random() * 6.2831;
    du.r[i] = 1.85 + Math.pow(Math.random(), 0.62) * 2.45;
    du.y[i] = (Math.random() * 2 - 1) * 2.7;
    du.spd[i] = (0.006 + Math.random() * 0.018) * (Math.random() < 0.5 ? -1 : 1);
    du.ph[i] = Math.random() * 6.2831;
    du.ph2[i] = Math.random() * 6.2831;
    du.w1[i] = 0.09 + Math.random() * 0.22;
    du.w2[i] = 0.07 + Math.random() * 0.2;
    du.w3[i] = 0.08 + Math.random() * 0.19;
    du.size[i] = 0.006 + Math.pow(Math.random(), 2.2) * 0.010;
    du.bright[i] = 0.28 + Math.random() * 0.72;
    du.slot[i] = i < FSLOT * 2 ? (i % FSLOT) : -1;
  }

  const dGeo = new T.BufferGeometry();
  dGeo.setAttribute('position', new T.BufferAttribute(dPos, 3));
  dGeo.setAttribute('aSize', new T.Float32BufferAttribute(Float32Array.from(du.size), 1));
  dGeo.setAttribute('aAlpha', new T.BufferAttribute(dAlpha, 1));
  dGeo.setAttribute('aTint', new T.Float32BufferAttribute(new Float32Array(DUST).map(() => Math.random()), 1));

  const dustMat = new T.ShaderMaterial({
    uniforms: {
      uSize: eU.uSize,
      uAccent: coreU.uAccent,
      uC2: eU.uC2,
      uMul: { value: 1 },
    },
    transparent: true,
    depthWrite: false,
    blending: T.AdditiveBlending,
    vertexShader: `
      attribute float aSize;
      attribute float aAlpha;
      attribute float aTint;
      uniform float uSize;
      varying float vA;
      varying float vT;
      void main(){
        vA = aAlpha;
        vT = aTint;
        vec4 mv = modelViewMatrix * vec4(position, 1.0);
        gl_PointSize = aSize * uSize / max(0.5, -mv.z);
        gl_Position = projectionMatrix * mv;
      }`,
    fragmentShader: `
      precision highp float;
      uniform vec3 uAccent;
      uniform vec3 uC2;
      uniform float uMul;
      varying float vA;
      varying float vT;
      void main(){
        vec2 q = gl_PointCoord - 0.5;
        float r = length(q) * 2.0;
        if (r > 1.0) discard;
        float core = pow(1.0 - r, 2.8);
        float halo = pow(1.0 - r, 1.0) * 0.10;
        vec3 c = mix(uAccent, uC2, vT * 0.7);
        gl_FragColor = vec4(c, (core + halo) * vA * uMul);
      }`,
  });
  const dust = new T.Points(dGeo, dustMat);
  dust.frustumCulled = false;
  dust.renderOrder = 8;
  scene.add(dust);

  function updateDust(dt, now, st) {
    const activeIdx = st.started ? st.idx : -1;
    const fr = activeIdx >= 0 ? frameSys[activeIdx] : null;
    const sx = frameRoot.scale.x, sy = frameRoot.scale.y;
    const px = frameRoot.position.x, py = frameRoot.position.y, pz = frameRoot.position.z;

    /* 단계 프레임 조립/해체 */
    frameSys.forEach((f, k) => {
      const on = k === activeIdx;
      if (on && !f.active) f.t = 0;
      f.active = on;
      if (on) {
        f.t += dt;
        f.prog = easeInOut(clamp(f.t / 0.78, 0, 1));
      }
      const wantA = on ? 1 : 0;
      f.alive += (wantA - f.alive) * (1 - Math.exp(-dt * (on ? 5.5 : 6.5)));
      if (f.alive < 0.004 && !on) f.alive = 0;
      if (on && f.prog >= 1 && !f.done) { f.done = true; f.flash = 1; }
      if (!on) { f.done = false; }
      f.flash = Math.max(0, (f.flash || 0) - dt * 1.7);
      f.uni.uProg.value = on ? f.prog : 1;
      f.uni.uAlpha.value = 0.72 * f.alive * (1 + f.flash * 0.55);
      f.uni.uHead.value = on && f.prog < 0.995 ? 1.0 : 0.20 * f.alive;
      f.mesh.visible = f.alive > 0.008;
      const g = stageGroups[k];
      if (g) g.userData.gate = clamp((f.t - 0.94) / 0.26, 0, 1) * (on ? 1 : 0);
    });

    /* 표류 입자 */
    const arr = fr ? fr.targets : null;
    for (let i = 0; i < DUST; i++) {
      const ang = du.ang[i] + now * du.spd[i];
      let x = Math.cos(ang) * du.r[i] + Math.sin(now * du.w1[i] + du.ph[i]) * 0.16;
      let y = du.y[i] + Math.sin(now * du.w2[i] + du.ph2[i]) * 0.2;
      let z = Math.sin(ang) * du.r[i] + Math.cos(now * du.w3[i] + du.ph[i]) * 0.16;

      let want = 0;
      let slot = -1;
      if (arr && du.slot[i] >= 0) {
        slot = du.slot[i];
        const t0 = fr.progOf[slot] * 0.78 - 0.40;
        want = fr.t > t0 ? 1 : 0;
      }
      const k = 1 - Math.exp(-dt * (want ? 3.2 : 2.4));
      du.cap[i] += (want - du.cap[i]) * k;
      const c = du.cap[i];
      const ce = c * c * (3 - 2 * c);

      if (slot >= 0 && ce > 0.001) {
        const tx = arr[slot * 3] * sx + px;
        const ty = arr[slot * 3 + 1] * sy + py;
        const tz = arr[slot * 3 + 2] + pz;
        x = lerp(x, tx, ce);
        y = lerp(y, ty, ce);
        z = lerp(z, tz, ce);
      }

      const i3 = i * 3;
      dPos[i3] = x; dPos[i3 + 1] = y; dPos[i3 + 2] = z;

      const twinkle = 0.72 + 0.28 * Math.sin(now * (0.7 + du.w2[i]) + du.ph[i] * 3.0);
      let a = du.bright[i] * (0.022 + 0.045 * conv.act) * twinkle;
      a += ce * du.bright[i] * 0.20;
      a *= clamp((y + 1.15) / 1.7, 0.02, 1);          /* 아래쪽은 조용히 */
      dAlpha[i] = clamp(a, 0, 1);
    }
    dGeo.attributes.position.needsUpdate = true;
    dGeo.attributes.aAlpha.needsUpdate = true;
  }

  /* 아주 먼 별가루 */
  const farGeo = new T.BufferGeometry();
  const farPos = new Float32Array(260 * 3);
  for (let i = 0; i < 260; i++) {
    const d = randomDir(T);
    const r = 6 + Math.random() * 16;
    farPos[i * 3] = d.x * r;
    farPos[i * 3 + 1] = Math.abs(d.y) * r * 0.7;
    farPos[i * 3 + 2] = -Math.abs(d.z) * r - 2;
  }
  farGeo.setAttribute('position', new T.BufferAttribute(farPos, 3));
  const farStars = new T.Points(farGeo, new T.PointsMaterial({
    size: 0.03, color: 0x8fe8ff, transparent: true, opacity: 0.20,
    blending: T.AdditiveBlending, depthWrite: false, sizeAttenuation: true,
  }));
  scene.add(farStars);

  /* ---------- 단계 오버레이 ---------- */
  const stageRoot = new T.Group();
  scene.add(stageRoot);
  const stageGroups = STAGES.map((s) => {
    const g = buildStage(T, s, { isPhone });
    g.visible = false;
    stageRoot.add(g);
    return g;
  });

  /* ---------- 상태 ---------- */
  const MINT = new T.Color(0x8fe6cd);
  const accentNow = new T.Color(0x7cf5c8);
  const accentTo = new T.Color(0x7cf5c8);

  /* 수렴 · 플레어 · 링 벌어짐 (스프링) */
  const conv = { v: 0, vel: 0, flash: 0, burst: 0, lvlSlow: 0.18, act: 0 };
  const ringSpring = { v: 0, vel: 0 };

  function energyBurst() {
    conv.burst = 1;
  }
  function ringBurst() {
    ringSpring.vel += 4.4;
  }

  function updateEnergy(dt, now) {
    const lvl = AUDIO.level;
    conv.lvlSlow += (lvl - conv.lvlSlow) * (1 - Math.exp(-dt / 2.6));
    const act = clamp((lvl - conv.lvlSlow * 1.02) / 0.15, 0, 1);
    const kA = act > conv.act ? 0.62 : 0.45;
    conv.act += (act - conv.act) * (1 - Math.exp(-dt * kA));
    conv.burst = Math.max(0, conv.burst - dt * 0.42);

    const target = Math.min(1, conv.act * 0.88 + conv.burst * 0.5);

    /* 임계 감쇠 스프링 → 튀지 않고 물 흐르듯 */
    conv.vel += ((target - conv.v) * 6.0 - conv.vel * 6.4) * dt;
    conv.v += conv.vel * dt;

    const cg = conv.v;
    const flashT = clamp((cg - 0.42) / 0.58, 0, 1);
    conv.flash += (flashT * flashT * 0.9 - conv.flash) * (1 - Math.exp(-dt * 6.5));

    /* 히스토리: h2 = 두 프레임 전, h1 = 직전 프레임 */
    h2.set(h1);
    h1.set(ePos);

    const dtS = Math.max(dt, 0.001);
    for (let i = 0; i < EPC; i++) {
      ep.ang[i] += dt * ep.spd[i] * (1 + 0.45 * conv.act) * (1 - 0.3 * Math.max(0, cg));
      const ci = clamp((cg - ep.stag[i] * (1 - cg) * 0.5) * 1.05, -0.25, 1.1);
      const ce = ci <= 0 ? ci : ci * ci * (3 - 2 * ci);

      const rr = ep.r0[i] * (1 - ce) + ep.rIn[i] * ce;
      const ay = ep.ang[i] + ce * ep.swirl[i];
      const bob = Math.sin(now * ep.bobS[i] + ep.bobP[i]) * ep.bobA[i] * (1 - ce * 0.9);
      const x = Math.cos(ay) * rr;
      const z = Math.sin(ay) * rr;
      const y = ep.y0[i] * (1 - ce * 0.9) + bob;

      const i3 = i * 3;
      ePos[i3] = x; ePos[i3 + 1] = y; ePos[i3 + 2] = z;

      const dist = Math.sqrt(x * x + y * y + z * z);
      const near = 1 - clamp((dist - 1.0) / 2.9, 0, 1);
      let a = ep.bright[i] * (0.085 + 0.26 * conv.act + 0.34 * near + 0.62 * Math.max(0, ce) * (0.55 + 0.9 * conv.flash));
      a *= 0.86 + 0.14 * Math.sin(now * 1.6 + ep.bobP[i] * 3.1);
      eAlpha[i] = clamp(a, 0, 1);
    }

    eGeo.attributes.position.needsUpdate = true;
    eGeo.attributes.aAlpha.needsUpdate = true;

    /* 잔궤적: 움직임이 빠를 때만 얇게 */
    const MAXT = 0.075;
    for (let i = 0; i < EPC; i++) {
      const i3 = i * 3, v4 = i * 12;
      /* 궤적은 프레임 길이와 무관하게 짧게 자른다 (저사양에서 길게 늘어지지 않게) */
      let ax = h2[i3], ay2 = h2[i3 + 1], az = h2[i3 + 2];
      let bx = h1[i3], by2 = h1[i3 + 1], bz = h1[i3 + 2];
      let da = Math.hypot(ax - bx, ay2 - by2, az - bz);
      if (da > MAXT) { const f = MAXT / da; ax = bx + (ax - bx) * f; ay2 = by2 + (ay2 - by2) * f; az = bz + (az - bz) * f; }
      let cx2 = ePos[i3], cy2 = ePos[i3 + 1], cz2 = ePos[i3 + 2];
      let db = Math.hypot(bx - cx2, by2 - cy2, bz - cz2);
      if (db > MAXT) { const f = MAXT / db; cx2 = bx + (cx2 - bx) * f; cy2 = by2 + (cy2 - by2) * f; cz2 = bz + (cz2 - bz) * f; }
      tPos[v4] = ax; tPos[v4 + 1] = ay2; tPos[v4 + 2] = az;
      tPos[v4 + 3] = bx; tPos[v4 + 4] = by2; tPos[v4 + 5] = bz;
      tPos[v4 + 6] = bx; tPos[v4 + 7] = by2; tPos[v4 + 8] = bz;
      tPos[v4 + 9] = cx2; tPos[v4 + 10] = cy2; tPos[v4 + 11] = cz2;

      const dx = ePos[i3] - h1[i3], dy = ePos[i3 + 1] - h1[i3 + 1], dz = ePos[i3 + 2] - h1[i3 + 2];
      const spd = Math.sqrt(dx * dx + dy * dy + dz * dz) / dtS;
      const base = clamp((spd - 2.2) * 0.028, 0, 0.085) * (0.3 + 0.7 * conv.act) * ep.bright[i];
      tAlpha[v4] = base * 0.08;
      tAlpha[v4 + 1] = base * 0.45;
      tAlpha[v4 + 2] = base * 0.45;
      tAlpha[v4 + 3] = base * 0.85;
    }
    trailGeo.attributes.position.needsUpdate = true;
    trailGeo.attributes.aAlpha.needsUpdate = true;

    eFlash.material.opacity = conv.flash * 0.16;
    eFlash.scale.setScalar(2.1 + conv.flash * 1.5);
    bgU.uConv.value = conv.v;
  }

  function applyCamera() {
    const w = window.innerWidth, h = window.innerHeight;
    const aspect = w / h;
    const diamPx = Math.min(h * 0.54, w * 0.80);
    const frac = diamPx / h;
    const d = 1 / (Math.tan((FOV * Math.PI) / 360) * frac);
    layout.d = d; layout.frac = frac; layout.aspect = aspect; layout.w = w; layout.h = h;
    camera.aspect = aspect;
    camera.updateProjectionMatrix();
    camera.position.set(0, 0, d);
    camera.lookAt(0, -d * 0.0614, 0);
    camera.updateMatrixWorld(true);

    const p = new T.Vector3(0, 0, 0).project(camera);
    const edge = new T.Vector3(0, 1, 0).project(camera);
    const coreY = 1 - (p.y * 0.5 + 0.5);
    const radY = Math.abs(edge.y - p.y) * 0.5;
    bgU.uCoreY.value = coreY;
    bgU.uHorizon.value = Math.min(0.92, coreY + radY + 0.012);
    bgU.uCoreX.value = p.x * 0.5 + 0.5;

    const rSize = renderer.getDrawingBufferSize(new T.Vector2());
    bgU.uRes.value.set(rSize.x, rSize.y);
    eU.uSize.value = rSize.y / (2 * Math.tan((FOV * Math.PI) / 360));
    ringUniforms.uCoreZ.value = -d;

    const visW = 2 / frac * aspect;
    const targetW = visW * (aspect < 1 ? 0.86 : 0.44);
    const proj = d / Math.max(0.2, d - 1.2);
    const s = clamp(targetW / (3.16 * proj), 0.3, 1.25);
    stageRoot.scale.set(s, s, 1);
    const panelY = -0.36 / (s * proj);
    frameRoot.position.set(0, panelY * s, 1.2);
    frameRoot.scale.set(s, s, 1);
    stageGroups.forEach((g) => {
      const pc = g.userData.panel;
      if (!pc) return;
      pc.position.y = panelY;
      pc.userData.baseY = panelY;
    });

    const corePx = Math.round(frac * h);
    document.documentElement.style.setProperty('--core-d', corePx + 'px');

    renderer.setSize(w, h, false);
    if (composer) composer.setSize(w, h);
  }
  applyCamera();

  const api = {
    setStage(i, immediate) {
      stageGroups.forEach((g, k) => {
        g.userData.target = k === i ? 1 : 0;
        if (immediate) { g.userData.v = state.started ? g.userData.target : 0; applyStage(g); }
      });
      const acc = (STAGES[i] && STAGES[i].accent) || '#7cf5c8';
      accentTo.set(acc).lerp(MINT, 0.58);
      if (!immediate && i >= 0) { energyBurst(); ringBurst(); }
    },
    update(dt, now, st) {
      const lvl = AUDIO.level;
      coreU.uTime.value = now;
      coreU.uLevel.value = lvl;
      bgU.uTime.value = now;
      bgU.uLevel.value = lvl;

      accentNow.lerp(accentTo, 1 - Math.exp(-dt * 1.4));
      coreU.uAccent.value.copy(accentNow);
      bgU.uAccent.value.copy(accentNow);

      /* 카메라: 아주 느린 궤도만 (흔들림 없이) */
      pointer.x += (pointer.tx - pointer.x) * (1 - Math.exp(-dt * 1.1));
      pointer.y += (pointer.ty - pointer.y) * (1 - Math.exp(-dt * 1.1));
      const d = layout.d;
      const sx = Math.sin(now * 0.058) * 0.040 + pointer.x * 0.085;
      camera.position.x = Math.sin(sx) * d;
      camera.position.z = Math.cos(sx) * d;
      camera.position.y = pointer.y * -0.10;
      camera.lookAt(0, -d * 0.0614 + pointer.y * -0.03, 0);

      /* 코어 호흡 */
      const pulse = 1 + lvl * 0.06 + Math.sin(now * 0.78) * 0.004;
      core.scale.setScalar(pulse);
      core.rotation.y = now * 0.038 * (st.playing ? 1 : 0.45);
      wire1.rotation.y = now * 0.085;
      wire1.rotation.x = Math.sin(now * 0.21) * 0.12;
      wire2.rotation.y = -now * 0.05;
      wire2.rotation.z = Math.sin(now * 0.17) * 0.1;
      chordGroup.rotation.y = -now * 0.09;
      chordGroup.rotation.x = now * 0.03;
      nodeMat.opacity = 0.55 + 0.4 * lvl;
      const chordMat = chordGroup.children[0].material;
      chordMat.opacity = 0.05 + 0.09 * lvl;

      coreGlow.material.opacity = 0.11 + 0.34 * lvl;
      coreGlow.scale.setScalar(1.05 + lvl * 0.45);
      coreGlow2.material.opacity = 0.14 + 0.30 * lvl;
      coreGlow2.scale.setScalar(0.4 + lvl * 0.22);
      innerOcta.rotation.y = -now * 0.6;
      innerOcta.rotation.x = now * 0.35;
      innerOcta.material.opacity = 0.3 + 0.3 * lvl;

      /* 링 */
      ringSpring.vel += (-ringSpring.v * 34 - ringSpring.vel * 7.6) * dt;
      ringSpring.v += ringSpring.vel * dt;
      const ex = clamp(ringSpring.v, -0.6, 1.6);
      ringUniforms.uRingDim.value = (1 - 0.30 * clamp(ex, 0, 1)) * (0.92 + 0.18 * lvl);

      ringSys.forEach((R, i) => {
        R.phase += dt;
        const wob = 1 + 0.20 * Math.sin(R.phase * 0.16 + i * 2.3) + 0.07 * Math.sin(R.phase * 0.57 + i);
        const w = R.def.w * wob * (1 + 2.6 * Math.max(0, ex));
        R.ang += dt * w;
        R.g.rotation.z = R.ang;
        R.g.rotation.x = R.def.tilt[0] + Math.sin(R.phase * 0.18 + i) * 0.05;
        R.g.rotation.y = R.def.tilt[1] + Math.sin(R.phase * 0.13 + i * 1.7) * 0.055;
        R.g.scale.setScalar(1 + 0.17 * ex);
        R.segs.forEach((m) => {
          const ud = m.userData;
          m.rotation.z = Math.sin(R.phase * ud.k + ud.ph) * ud.amp + ex * ud.exK;
        });
      });

      /* 에너지 입자 · 표류 입자(프레임 조립) */
      updateEnergy(dt, now);
      updateDust(dt, now, st);

      farStars.rotation.y = -now * 0.008;

      /* 단계 전환 */
      stageGroups.forEach((g, k) => {
        const target = (st.started && k === st.idx) ? 1 : 0;
        g.userData.target = target;
        const speed = target ? 4.4 : 6.4;
        g.userData.v = lerp(g.userData.v, target, Math.min(1, dt * speed));
        if (Math.abs(g.userData.v - target) < 0.0015) g.userData.v = target;
        applyStage(g);
        if (g.userData.v > 0.01 && g.userData.tick) g.userData.tick(now, dt, g.userData.v);
      });

      if (composer) composer.render();
      else renderer.render(scene, camera);
    },
    dispose() {
      scene.traverse((o) => {
        if (o.geometry) o.geometry.dispose();
        if (o.material) {
          const mats = Array.isArray(o.material) ? o.material : [o.material];
          mats.forEach((m) => {
            if (m.map) m.map.dispose();
            m.dispose();
          });
        }
      });
      bgMat.dispose();
      bg.geometry.dispose();
    },
  };

  function applyStage(g) {
    const v = g.userData.v;
    const vis = v > 0.004;
    g.visible = vis;
    if (!vis) return;
    (g.userData.layers || []).forEach((c) => {
      const delay = c.userData.delay || 0;
      const lv = clamp((v - delay) / Math.max(0.01, 1 - delay), 0, 1);
      const e = easeOut(lv);
      c.visible = lv > 0.004;
      c.position.z = c.userData.baseZ - (1 - e) * 2.9;
      c.position.y = c.userData.baseY + (1 - e) * 0.16;
      c.scale.setScalar(0.9 + 0.1 * e);
      c.rotation.x = c.userData.baseRX + (1 - e) * -0.55;
      const gate = g.userData.gate == null ? 1 : g.userData.gate;
      c.traverse((o) => {
        if (!o.material) return;
        const mats = Array.isArray(o.material) ? o.material : [o.material];
        mats.forEach((m) => {
          if (m.userData && m.userData.base != null) m.opacity = m.userData.base * lv * (m.userData.frameGated ? gate : 1);
        });
      });
    });
  }

  const onResize = () => {
    applyCamera();
    requestAnimationFrame(() => { applyCamera(); });
  };
  window.addEventListener('resize', onResize);
  window.addEventListener('orientationchange', () => setTimeout(onResize, 220));
  window.addEventListener('pointermove', (e) => {
    if (e.pointerType === 'touch') return;
    pointer.tx = (e.clientX / window.innerWidth - 0.5) * 2;
    pointer.ty = (e.clientY / window.innerHeight - 0.5) * 2;
  });

  api.setStage(0, true);
  api.renderer = renderer;
  api.scene = scene;
  api.camera = camera;
  api.attachComposer = (c) => { composer = c; };
  window.__holoApi = api;
  api.__dbg = () => ({
    idx: state.idx, started: state.started, lvl: +AUDIO.level.toFixed(2),
    act: +conv.act.toFixed(2), cv: +conv.v.toFixed(2), flash: +conv.flash.toFixed(2),
    frames: frameSys.map((f) => ({ t: +f.t.toFixed(2), alive: +f.alive.toFixed(2), prog: +f.prog.toFixed(2), vis: f.mesh.visible })),
    gates: stageGroups.map((g) => +(g.userData.gate == null ? -1 : g.userData.gate).toFixed(2)),
    panelOp: stageGroups.map((g) => +(g.userData.panel ? g.userData.panel.children[0].material.opacity : -1).toFixed(2)),
    pv: stageGroups.map((g) => +g.userData.v.toFixed(2)),
  });
  return api;
}

/* 후처리 블룸 */
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
    const c = new EffectComposer(sceneApi.renderer);
    c.addPass(new RenderPass(sceneApi.scene, sceneApi.camera));
    c.addPass(new UnrealBloomPass(new THREE.Vector2(w, h), 0.17, 0.70, 0.74));
    c.addPass(new OutputPass());
    c.setSize(w, h);
    sceneApi.attachComposer(c);
  } catch (e) {
    console.warn('[holo] 블룸 생략:', e);
  }
}

/* =============================================================
   텍스처 · 패널 헬퍼
   ============================================================= */
function randomDir(T) {
  const z = Math.random() * 2 - 1;
  const a = Math.random() * Math.PI * 2;
  const r = Math.sqrt(Math.max(0, 1 - z * z));
  return new T.Vector3(Math.cos(a) * r, z, Math.sin(a) * r);
}

function dotTexture(T) {
  const c = document.createElement('canvas');
  c.width = c.height = 64;
  const x = c.getContext('2d');
  const g = x.createRadialGradient(32, 32, 0, 32, 32, 32);
  g.addColorStop(0, 'rgba(255,255,255,1)');
  g.addColorStop(0.35, 'rgba(190,255,238,0.55)');
  g.addColorStop(1, 'rgba(124,245,200,0)');
  x.fillStyle = g;
  x.fillRect(0, 0, 64, 64);
  const tex = new T.CanvasTexture(c);
  tex.colorSpace = T.SRGBColorSpace;
  return tex;
}

function radialTexture(T, inner, outer) {
  const c = document.createElement('canvas');
  c.width = c.height = 128;
  const x = c.getContext('2d');
  const g = x.createRadialGradient(64, 64, 0, 64, 64, 64);
  g.addColorStop(0, inner);
  g.addColorStop(0.45, inner.replace(/[\d.]+\)$/, '0.32)'));
  g.addColorStop(1, outer);
  x.fillStyle = g;
  x.fillRect(0, 0, 128, 128);
  const tex = new T.CanvasTexture(c);
  tex.colorSpace = T.SRGBColorSpace;
  return tex;
}

function hexA(hex, a) {
  let h = String(hex || '#7cf5c8').replace('#', '');
  if (h.length === 3) h = h.split('').map((c) => c + c).join('');
  const n = parseInt(h, 16);
  return 'rgba(' + ((n >> 16) & 255) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + a + ')';
}

function chamferPath(ctx, x, y, w, h, c) {
  ctx.beginPath();
  ctx.moveTo(x + c, y);
  ctx.lineTo(x + w - c, y);
  ctx.lineTo(x + w, y + c);
  ctx.lineTo(x + w, y + h - c);
  ctx.lineTo(x + w - c, y + h);
  ctx.lineTo(x + c, y + h);
  ctx.lineTo(x, y + h - c);
  ctx.lineTo(x, y + c);
  ctx.closePath();
}

function fadeMaterials(root) {
  root.traverse((o) => {
    if (!o.material) return;
    const mats = Array.isArray(o.material) ? o.material : [o.material];
    mats.forEach((m) => {
      if (m.userData && m.userData.base == null) {
        m.userData.base = m.opacity == null ? 1 : m.opacity;
        m.transparent = true;
      }
    });
  });
}

/* ---- 패널 캔버스 ---- */
const FONT_SANS = '"Pretendard","Apple SD Gothic Neo","Noto Sans KR","Malgun Gothic",system-ui,sans-serif';
const FONT_MONO = '"SF Mono","Roboto Mono","Noto Sans Mono CJK KR",ui-monospace,Menlo,monospace';

function fitFont(ctx, text, maxW, start, min, weight, family) {
  let s = start;
  for (; s > min; s -= 2) {
    ctx.font = weight + ' ' + s + 'px ' + family;
    if (ctx.measureText(text).width <= maxW) break;
  }
  ctx.font = weight + ' ' + s + 'px ' + family;
  return s;
}

function makeTexture(T, canvas, modelW, modelH) {
  const tex = new T.CanvasTexture(canvas);
  tex.colorSpace = T.SRGBColorSpace;
  tex.anisotropy = 4;
  tex.minFilter = T.LinearFilter;
  tex.magFilter = T.LinearFilter;
  tex.needsUpdate = true;
  const mesh = new T.Mesh(
    new T.PlaneGeometry(modelW, modelH),
    new T.MeshBasicMaterial({ map: tex, transparent: true, depthWrite: false, depthTest: false, opacity: 1 })
  );
  mesh.renderOrder = 20;
  return mesh;
}

/* 깎인 HUD 프레임 패널 */
function hudPanel(T, opt) {
  const W = 1400, H = 430;
  const cv = document.createElement('canvas');
  cv.width = W; cv.height = H;
  const x = cv.getContext('2d');
  const accent = opt.accent || '#7cf5c8';
  const cut = 34;

  // 유리 바탕 (아주 옅게 — 홀로그램 판)
  chamferPath(x, 2, 2, W - 4, H - 4, cut);
  const bg = x.createLinearGradient(0, 0, W * 0.85, H);
  bg.addColorStop(0, 'rgba(5,26,22,0.8)');
  bg.addColorStop(0.5, 'rgba(2,12,10,0.72)');
  bg.addColorStop(1, 'rgba(4,18,16,0.78)');
  x.fillStyle = bg;
  x.fill();

  // 내부 광
  const rg = x.createRadialGradient(W * 0.22, H * 0.1, 0, W * 0.22, H * 0.1, W * 0.9);
  rg.addColorStop(0, hexA(accent, 0.10));
  rg.addColorStop(1, hexA(accent, 0));
  x.fillStyle = rg;
  x.fillRect(0, 0, W, H);

  // 스캔라인
  x.save();
  chamferPath(x, 2, 2, W - 4, H - 4, cut);
  x.clip();
  x.globalAlpha = 0.05;
  x.strokeStyle = hexA(accent, 0.9);
  x.lineWidth = 1;
  for (let yy = 0; yy < H; yy += 5) { x.beginPath(); x.moveTo(0, yy); x.lineTo(W, yy); x.stroke(); }
  x.restore();

  // 테두리: 입자 프레임이 그리므로 여기서는 아주 옅은 유리 가장자리만
  chamferPath(x, 2.5, 2.5, W - 5, H - 5, cut);
  x.strokeStyle = hexA(accent, 0.10);
  x.lineWidth = 3;
  x.stroke();

  const padL = 62;
  // 라벨
  x.textBaseline = 'alphabetic';
  x.fillStyle = hexA(accent, 0.95);
  x.fillRect(padL, 46, 5, 22);
  fitFont(x, opt.label || '', W - padL * 2 - 260, 32, 20, '600', FONT_SANS);
  x.fillStyle = hexA(accent, 0.9);
  x.fillText(opt.label || '', padL + 18, 72);

  // 우측 태그
  if (opt.tag) {
    x.textAlign = 'right';
    fitFont(x, opt.tag, 400, 24, 15, '500', FONT_SANS);
    x.fillStyle = 'rgba(196,236,226,0.62)';
    x.fillText(opt.tag, W - 70, 72);
    x.textAlign = 'left';
  }

  // 큰 값
  const value = String(opt.value || '');
  const vs = fitFont(x, value, W - padL * 2 - 30, 156, 70, '600', FONT_MONO);
  x.fillStyle = '#f6fffc';
  x.shadowColor = hexA(accent, 0.5);
  x.shadowBlur = 24;
  x.fillText(value, padL, 108 + vs);
  x.shadowBlur = 0;

  // 보조 라인
  if (opt.sub) {
    const parts = String(opt.sub).split('·').map((s) => s.trim()).filter(Boolean);
    let cx = padL;
    const baseline = H - 74;
    const maxW = W - padL * 2 - 10;
    let fs = 40;
    x.font = '500 ' + fs + 'px ' + FONT_SANS;
    while (x.measureText(String(opt.sub)).width > maxW && fs > 20) {
      fs -= 2; x.font = '500 ' + fs + 'px ' + FONT_SANS;
    }
    parts.forEach((p, i) => {
      const isPct = /^[−+\-]?\d+(\.\d+)?%$/.test(p);
      x.fillStyle = isPct ? hexA('#ff7a8c', 0.95) : 'rgba(214,242,235,0.84)';
      x.fillText(p, cx, baseline);
      cx += x.measureText(p).width;
      if (i < parts.length - 1) {
        x.fillStyle = 'rgba(178,216,208,0.8)';
        x.fillText('   ·   ', cx, baseline);
        cx += x.measureText('   ·   ').width;
      }
    });
  }


  return makeTexture(T, cv, 3.16, 0.97);
}

/* 얇은 홀로그램 칩 */
function hudChip(T, opt) {
  const W = 900, H = 250;
  const cv = document.createElement('canvas');
  cv.width = W; cv.height = H;
  const x = cv.getContext('2d');
  const accent = opt.accent || '#7cf5c8';
  const cut = 22;

  chamferPath(x, 2, 2, W - 4, H - 4, cut);
  const bg = x.createLinearGradient(0, 0, W, H);
  bg.addColorStop(0, 'rgba(5,24,20,0.7)');
  bg.addColorStop(1, 'rgba(3,12,11,0.58)');
  x.fillStyle = bg;
  x.fill();

  chamferPath(x, 2.5, 2.5, W - 5, H - 5, cut);
  x.strokeStyle = hexA(accent, 0.55);
  x.lineWidth = 2.4;
  x.stroke();

  x.strokeStyle = hexA(accent, 0.95);
  x.lineWidth = 4;
  [[6, 6, 1, 1], [W - 6, 6, -1, 1], [6, H - 6, 1, -1], [W - 6, H - 6, -1, -1]].forEach(([cx, cy, dx, dy]) => {
    const K = 28;
    x.beginPath(); x.moveTo(cx + dx * K, cy); x.lineTo(cx, cy); x.lineTo(cx, cy + dy * K); x.stroke();
  });

  // 스캔라인
  x.save();
  chamferPath(x, 2, 2, W - 4, H - 4, cut);
  x.clip();
  x.globalAlpha = 0.05;
  x.strokeStyle = hexA(accent, 0.9);
  x.lineWidth = 1;
  for (let yy = 0; yy < H; yy += 5) { x.beginPath(); x.moveTo(0, yy); x.lineTo(W, yy); x.stroke(); }
  x.restore();

  x.textBaseline = 'alphabetic';
  x.fillStyle = hexA(accent, 0.95);
  x.fillRect(44, 40, 4, 20);
  x.font = '600 30px ' + FONT_SANS;
  x.fillText(opt.label || '', 58, 58);

  const val = String(opt.value || '');
  fitFont(x, val, W - 108, 66, 26, '600', FONT_MONO);
  x.fillStyle = '#f2fffb';
  x.shadowColor = hexA(accent, 0.5);
  x.shadowBlur = 16;
  x.fillText(val, 44, 168);
  x.shadowBlur = 0;

  x.fillStyle = hexA(accent, 0.55);
  x.fillRect(W * 0.6, 44, W * 0.3, 2);

  return makeTexture(T, cv, 1.62, 0.45);
}

/* ---- 한 단계 = 깊은 Z에서 날아오는 레이어 묶음 ---- */
function buildStage(T, s, opt) {
  const g = new T.Group();
  const accent = s.accent || '#7cf5c8';
  const layers = [];
  const m = s.metrics || [];

  const add = (obj, cfg = {}) => {
    const c = new T.Group();
    c.position.set(cfg.x || 0, cfg.y || 0, cfg.z || 0);
    c.rotation.set(cfg.rx || 0, cfg.ry || 0, cfg.rz || 0);
    c.userData.baseY = cfg.y || 0;
    c.userData.baseZ = cfg.z || 0;
    c.userData.baseRX = cfg.rx || 0;
    c.userData.delay = cfg.delay || 0;
    c.add(obj);
    fadeMaterials(obj);
    g.add(c);
    layers.push(c);
    return c;
  };

  const visual = s.visual || s.id || 'generic';
  const pct = (v) => (typeof v === 'number' ? (v > 0 ? '+' : '') + v.toFixed(2) + '%' : '');

  let value = s.headline || '';
  let label = m[0] && m[0].label ? m[0].label : (s.title || '');
  const tag = s.title || '';
  if (m[0] && m[0].value && String(m[0].value).length <= 12) value = m[0].value;

  const panel = hudPanel(T, {
    accent,
    label,
    value,
    sub: s.sub || '',
    tag,
  });
  /* 패널 몸체는 입자 테두리가 조립된 뒤에 늦게 나타난다 */
  panel.material.userData.frameGated = true;

  const panelC = add(panel, { y: -0.44, z: 1.2, rx: -0.02, delay: 0 });
  g.userData.panel = panelC;
  g.userData.gate = GATE_START; // 시작 전엔 숨김

  /* 단계별 3D 오브젝트 */
  if (visual === 'weather') {
    // 앰버 기온 게이지 (분할 호 + 방사 눈금)
    const arcG = new T.Group();
    const amber = 0xffc76b;
    const arcMat = new T.MeshBasicMaterial({
      color: amber, transparent: true, opacity: 0.55,
      blending: T.AdditiveBlending, depthWrite: false, side: T.DoubleSide,
    });
    [[0.30, 1.15], [2.15, 1.05], [4.05, 0.95]].forEach(([from, len]) => {
      arcG.add(new T.Mesh(new T.RingGeometry(1.32, 1.326, 96, 1, from, len), arcMat));
    });
    const tickMat = new T.MeshBasicMaterial({
      color: amber, transparent: true, opacity: 0.5,
      blending: T.AdditiveBlending, depthWrite: false,
    });
    for (let i = 0; i < 36; i++) {
      const a = (i / 36) * Math.PI * 2;
      const big = i % 6 === 0;
      const tk = new T.Mesh(new T.BoxGeometry(0.004, big ? 0.075 : 0.038, 0.004), tickMat);
      tk.position.set(Math.cos(a) * 1.355, Math.sin(a) * 1.355, 0);
      tk.rotation.z = a - Math.PI / 2;
      arcG.add(tk);
    }
    arcG.rotation.set(1.30, 0.18, 0.1);
    add(arcG, { y: 0, z: 0, delay: 0.05 });
    g.userData.arcG = arcG;
  }

  if (visual === 'us' || s.id === 'us') {
    // 네온 하락 막대 (지수별 낙폭)
    const bars = m.filter((d) => typeof d.pct === 'number').slice(0, 4);
    const maxAbs = Math.max(1, ...bars.map((b) => Math.abs(b.pct)));
    const bg2 = new T.Group();
    const baseY = -0.02;
    bars.forEach((b, i) => {
      const x = (i - (bars.length - 1) / 2) * 0.30;
      const h = 0.08 + (Math.abs(b.pct) / maxAbs) * 0.40;
      const mat = new T.MeshBasicMaterial({
        color: 0xff6b81, transparent: true, opacity: 0.34, blending: T.AdditiveBlending, depthWrite: false,
      });
      const bar = new T.Mesh(new T.BoxGeometry(0.075, 1, 0.075), mat);
      bar.scale.y = h;
      bar.position.set(x, baseY + h / 2, 0);
      const edge = new T.LineSegments(
        new T.EdgesGeometry(new T.BoxGeometry(0.075, 1, 0.075)),
        new T.LineBasicMaterial({ color: 0xff8f9d, transparent: true, opacity: 0.9, blending: T.AdditiveBlending, depthWrite: false })
      );
      edge.scale.y = h;
      edge.position.copy(bar.position);
      bg2.add(bar, edge);
    });
    const baseline = new T.Mesh(
      new T.BoxGeometry(1.36, 0.004, 0.004),
      new T.MeshBasicMaterial({ color: 0xff6b81, transparent: true, opacity: 0.7, blending: T.AdditiveBlending, depthWrite: false })
    );
    baseline.position.set(0, baseY, 0);
    bg2.add(baseline);
    bg2.position.set(0, 0.10, 0.18);
    add(bg2, { y: 0.10, z: 0.18, delay: 0.16 });
    g.userData.bars = bg2;
  }

  if (visual === 'kr') {
    if (m[2] && m[2].value) {
      const chip = hudChip(T, { accent, label: m[2].label, value: m[2].value });
      add(chip, { x: 0.46, y: 0.80, z: 1.14, ry: 0.05, delay: 0.18 });
    }
  }

  if (visual === 'calendar') {
    if (m[1] && m[1].value) {
      const chip1 = hudChip(T, { accent, label: m[1].label, value: m[1].value });
      add(chip1, { x: -0.40, y: 0.86, z: 1.12, ry: 0.06, delay: 0.16 });
    }
    if (m[2] && m[2].value) {
      const chip2 = hudChip(T, { accent, label: m[2].label, value: m[2].value });
      add(chip2, { x: 0.34, y: 0.40, z: 1.08, ry: -0.05, delay: 0.3 });
    }
  }

  g.userData.layers = layers;
  g.userData.v = 0;
  g.tick = (now, dt, vis) => {
    if (g.userData.bars) {
      const e = easeOut(clamp(vis, 0, 1));
      g.userData.bars.scale.set(1, Math.max(0.001, e), 1);
      g.userData.bars.position.y = 0.52 + Math.sin(now * 0.7) * 0.01;
    }
    if (g.userData.arcG) {
      g.userData.arcG.rotation.z = now * 0.12;
    }
    layers.forEach((c, i) => {
      c.position.y = c.userData.baseY + Math.sin(now * 0.6 + i * 1.3) * 0.014;
    });
  };

  return g;
}
