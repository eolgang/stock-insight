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
  renderer.toneMappingExposure = 0.9;
  renderer.outputColorSpace = T.SRGBColorSpace;

  const scene = new T.Scene();
  scene.background = new T.Color(0x020906);

  const FOV = 42;
  const camera = new T.PerspectiveCamera(FOV, window.innerWidth / window.innerHeight, 0.1, 200);
  const layout = { d: 6, frac: 0.5, aspect: 1.6, w: 1, h: 1 };
  const pointer = { x: 0, y: 0 };

  /* ---------- 배경 셰이더: 깊은 공간 + 지평선 + 물결 반사 ---------- */
  const bgU = {
    uRes: { value: new T.Vector2(1, 1) },
    uTime: { value: 0 },
    uLevel: { value: 0 },
    uCoreX: { value: 0.5 },
    uCoreY: { value: 0.42 },
    uHorizon: { value: 0.68 },
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
      uniform float uCoreX;
      uniform float uCoreY;
      uniform float uHorizon;
      uniform vec3 uAccent;
      varying vec2 vUv;

      void main(){
        float y = 1.0 - vUv.y;
        float x = vUv.x;
        float aspect = uRes.x / max(1.0, uRes.y);
        vec2 cp = vec2((x - uCoreX) * aspect, y - uCoreY);
        float cd = length(cp);

        vec3 top = vec3(0.0012, 0.0052, 0.0042);
        vec3 mid = vec3(0.0040, 0.0190, 0.0150);
        vec3 bot = vec3(0.0008, 0.0032, 0.0028);
        vec3 col = mix(top, mid, smoothstep(0.0, uHorizon, y));
        col = mix(col, bot, smoothstep(uHorizon, min(1.0, uHorizon + 0.34), y));

        col += vec3(0.006, 0.030, 0.024) * exp(-abs(y - uHorizon) * 7.0);

        col += uAccent * exp(-cd * 4.6) * (0.026 + 0.055 * uLevel);
        col += uAccent * exp(-cd * 1.9) * 0.010;

        float py = max(0.0, abs(y - uHorizon) * uRes.y - 0.5);
        float line = exp(-py * 1.9);
        float glow = exp(-py * 0.045);
        float edge = smoothstep(0.0, 0.09, x) * (1.0 - smoothstep(0.91, 1.0, x));
        col += uAccent * line * 0.62 * edge;
        col += uAccent * glow * 0.040 * edge;

        if (y > uHorizon) {
          float d = y - uHorizon;
          float fade = exp(-d * 7.5);
          float cx = (x - 0.5) * 2.3;
          float mask = exp(-cx * cx * 1.35);
          float w = 0.0;
          for (int i = 0; i < 6; i++) {
            float k = float(i);
            float yy = uHorizon + 0.012 + k * 0.026;
            float amp = (0.0018 + 0.0009 * k) * (1.0 + 3.2 * uLevel);
            float ph = uTime * (0.55 + 0.13 * k) + k * 1.7;
            float wob = sin(x * 15.0 + ph) * amp + sin(x * 6.3 - ph * 0.8 + k) * amp * 0.75;
            float dd = abs(y - (yy + wob)) * uRes.y;
            w += (1.0 - smoothstep(0.15, 1.7, dd)) * (0.30 - 0.036 * k);
          }
          col += mix(uAccent, vec3(0.32, 0.85, 1.0), 0.25) * w * mask * fade * (0.30 + 0.65 * uLevel);

          float rx = (x - 0.5) * 2.3;
          float refl = exp(-rx * rx * 2.4) * exp(-d * 10.0);
          col += uAccent * refl * (0.06 + 0.20 * uLevel);
        }

        float g = fract(sin(dot(vUv * uRes, vec2(12.9898, 78.233))) * 43758.5453);
        col += (g - 0.5) * 0.005;

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
  const wire1 = new T.LineSegments(new T.WireframeGeometry(new T.IcosahedronGeometry(1.012, 2)), wireMat(0.34, 1.0));
  core.add(wire1);

  const wire2 = new T.LineSegments(new T.WireframeGeometry(new T.IcosahedronGeometry(1.08, 3)), wireMat(0.09, 0.55));
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

  /* 분할 아크 + 방사형 눈금 링 */
  const rings = [];
  function ringLayer(cfg) {
    const g = new T.Group();
    g.rotation.set(cfg.tilt[0], cfg.tilt[1], cfg.tilt[2]);
    const mat = new T.MeshBasicMaterial({
      color: 0x7cf5c8, transparent: true, opacity: cfg.op,
      blending: T.AdditiveBlending, depthWrite: false, side: T.DoubleSide,
    });
    cfg.arcs.forEach(([from, len]) => {
      const geo = new T.RingGeometry(cfg.r, cfg.r + cfg.thick, 128, 1, from, len);
      g.add(new T.Mesh(geo, mat));
    });
    if (cfg.ticks) {
      const tickGeo = new T.BoxGeometry(0.0055, cfg.tickLen, 0.0055);
      const tickMat = new T.MeshBasicMaterial({
        color: 0x7cf5c8, transparent: true, opacity: cfg.op * 0.85,
        blending: T.AdditiveBlending, depthWrite: false,
      });
      for (let i = 0; i < cfg.ticks; i++) {
        const a = (i / cfg.ticks) * Math.PI * 2;
        const len = cfg.tickLen * (i % 5 === 0 ? 2.0 : 1);
        const m = new T.Mesh(tickGeo, tickMat);
        m.scale.y = len / cfg.tickLen;
        m.position.set(Math.cos(a) * (cfg.r + 0.035), Math.sin(a) * (cfg.r + 0.035), 0);
        m.rotation.z = a - Math.PI / 2;
        g.add(m);
      }
    }
    core.add(g);
    rings.push({ g, cfg, mat, t: Math.random() * 6 });
    return g;
  }
  ringLayer({ r: 1.15, thick: 0.0035, tilt: [1.44, 0.10, 0], speed: 0.20, op: 0.6, arcs: [[0.15, 1.05], [2.35, 0.75], [4.05, 1.15]] });
  ringLayer({ r: 1.29, thick: 0.0030, tilt: [1.22, 0.42, 0.35], speed: -0.15, op: 0.36, arcs: [[0.4, 1.95], [3.1, 1.35]], ticks: 34, tickLen: 0.045 });
  ringLayer({ r: 1.38, thick: 0.0026, tilt: [0.50, 0.66, 0.42], speed: 0.09, op: 0.12, arcs: [[0.9, 2.5], [4.3, 0.8]] });
  ringLayer({ r: 1.04, thick: 0.0022, tilt: [1.50, 0.04, 0.18], speed: -0.06, op: 0.22, arcs: [[0, 4.6]] });

  /* 떠다니는 입자 */
  const PC = reduced ? 180 : (isPhone ? 420 : 720);
  const pGeo = new T.BufferGeometry();
  const pPos = new Float32Array(PC * 3);
  const pSeed = new Float32Array(PC);
  for (let i = 0; i < PC; i++) {
    const d = randomDir(T);
    const r = 1.05 + Math.pow(Math.random(), 0.6) * 1.5;
    pPos[i * 3] = d.x * r;
    pPos[i * 3 + 1] = d.y * r;
    pPos[i * 3 + 2] = d.z * r;
    pSeed[i] = 0.3 + Math.random() * 0.9;
  }
  pGeo.setAttribute('position', new T.BufferAttribute(pPos, 3));
  const particles = new T.Points(pGeo, new T.PointsMaterial({
    size: 0.012, map: dotTexture(T), color: 0xa9ffe4, transparent: true, opacity: 0.42,
    blending: T.AdditiveBlending, depthWrite: false, sizeAttenuation: true,
  }));
  core.add(particles);

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
    size: 0.03, color: 0x8fe8ff, transparent: true, opacity: 0.24,
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
  const MINT = new T.Color(0x7cf5c8);
  const accentNow = new T.Color(0x7cf5c8);
  const accentTo = new T.Color(0x7cf5c8);

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
    bgU.uHorizon.value = Math.min(0.94, coreY + radY + 0.004);
    bgU.uCoreX.value = p.x * 0.5 + 0.5;

    const rSize = renderer.getDrawingBufferSize(new T.Vector2());
    bgU.uRes.value.set(rSize.x, rSize.y);

    const visW = 2 / frac * aspect;
    const targetW = visW * (aspect < 1 ? 0.86 : 0.44);
    const proj = d / Math.max(0.2, d - 1.2);
    const s = clamp(targetW / (3.16 * proj), 0.3, 1.25);
    stageRoot.scale.set(s, s, 1);
    const panelY = -0.36 / (s * proj);
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
      accentTo.set(acc).lerp(MINT, 0.68);
    },
    update(dt, now, st) {
      const lvl = AUDIO.level;
      coreU.uTime.value = now;
      coreU.uLevel.value = lvl;
      bgU.uTime.value = now;
      bgU.uLevel.value = lvl;

      accentNow.lerp(accentTo, 1 - Math.exp(-dt * 1.6));
      coreU.uAccent.value.copy(accentNow);
      bgU.uAccent.value.copy(accentNow);

      /* 카메라 미세 시선 */
      const d = layout.d;
      const sx = Math.sin(now * 0.11) * 0.045 + pointer.x * 0.16;
      camera.position.x = Math.sin(sx) * d;
      camera.position.z = Math.cos(sx) * d;
      camera.position.y = pointer.y * -0.16;
      camera.lookAt(0, -d * 0.0614 + pointer.y * -0.05, 0);

      /* 코어 호흡 */
      const pulse = 1 + lvl * 0.065 + Math.sin(now * 0.85) * 0.004;
      core.scale.setScalar(pulse);
      core.rotation.y = now * 0.045 * (st.playing ? 1 : 0.45);
      wire1.rotation.y = now * 0.085;
      wire1.rotation.x = Math.sin(now * 0.21) * 0.12;
      wire2.rotation.y = -now * 0.05;
      wire2.rotation.z = Math.sin(now * 0.17) * 0.1;
      chordGroup.rotation.y = -now * 0.09;
      chordGroup.rotation.x = now * 0.03;
      nodeMat.opacity = 0.55 + 0.4 * lvl;
      const chordMat = chordGroup.children[0].material;
      chordMat.opacity = 0.05 + 0.09 * lvl;

      coreGlow.material.opacity = 0.14 + 0.5 * lvl;
      coreGlow.scale.setScalar(1.05 + lvl * 0.5);
      coreGlow2.material.opacity = 0.18 + 0.45 * lvl;
      coreGlow2.scale.setScalar(0.4 + lvl * 0.22);
      innerOcta.rotation.y = -now * 0.6;
      innerOcta.rotation.x = now * 0.35;
      innerOcta.material.opacity = 0.3 + 0.3 * lvl;

      rings.forEach((r, i) => {
        const sp = r.cfg.speed * (st.playing ? 1 : 0.4) * (1 + lvl * 1.7);
        r.t += dt * sp;
        r.g.rotation.z = r.t;
        const wob = 1 + Math.sin(now * 0.7 + i) * 0.008 + lvl * 0.055;
        r.g.scale.setScalar(wob);
        r.mat.opacity = r.cfg.op * (0.72 + 0.5 * lvl);
      });

      particles.rotation.y = now * 0.05;
      particles.rotation.x = Math.sin(now * 0.13) * 0.1;
      particles.material.opacity = 0.35 + 0.3 * lvl;
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
      c.traverse((o) => {
        if (!o.material) return;
        const mats = Array.isArray(o.material) ? o.material : [o.material];
        mats.forEach((m) => {
          if (m.userData && m.userData.base != null) m.opacity = m.userData.base * lv;
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
    pointer.x = (e.clientX / window.innerWidth - 0.5) * 2;
    pointer.y = (e.clientY / window.innerHeight - 0.5) * 2;
  });

  api.setStage(0, true);
  api.renderer = renderer;
  api.scene = scene;
  api.camera = camera;
  api.attachComposer = (c) => { composer = c; };
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
    c.addPass(new UnrealBloomPass(new THREE.Vector2(w, h), 0.24, 0.55, 0.62));
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

  // 테두리
  chamferPath(x, 2.5, 2.5, W - 5, H - 5, cut);
  x.strokeStyle = hexA(accent, 0.62);
  x.lineWidth = 3;
  x.stroke();

  // 코너 브래킷
  x.strokeStyle = hexA(accent, 0.98);
  x.lineWidth = 5;
  const B = 54;
  [[8, 8, 1, 1], [W - 8, 8, -1, 1], [8, H - 8, 1, -1], [W - 8, H - 8, -1, -1]].forEach(([cx, cy, dx, dy]) => {
    x.beginPath();
    x.moveTo(cx + dx * B, cy);
    x.lineTo(cx, cy);
    x.lineTo(cx, cy + dy * B);
    x.stroke();
  });

  // 상단 액센트 라인
  const lg = x.createLinearGradient(W * 0.1, 0, W * 0.9, 0);
  lg.addColorStop(0, hexA(accent, 0));
  lg.addColorStop(0.5, hexA(accent, 0.9));
  lg.addColorStop(1, hexA(accent, 0));
  x.fillStyle = lg;
  x.fillRect(W * 0.1, 2, W * 0.8, 3);

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

  const panelC = add(panel, { y: -0.44, z: 1.2, rx: -0.02, delay: 0 });
  g.userData.panel = panelC;

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
