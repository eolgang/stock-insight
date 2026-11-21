/* 경제 인사이트 (자금 흐름) — data/money-flow.json (없으면 money-flow.sample.json)
   두 가지 스키마를 모두 읽는다.
   A) flow_value(숫자)+flow_unit, regime{label,note}, signals.direction 매집/대피/중립, korea_property
   B) flow_usd_or_krw(문자열)+flow_period, regime{verdict,reason,basis_date}, direction 개선/중립/악화 */
(function () {
  'use strict';

  var ROOT_ID = 'mfRoot';
  var state = { status: 'idle', data: null, sample: false, reason: '' };

  var SECTION = {
    id: 'money-flow',
    tab: '경제 인사이트',
    title: '경제 인사이트 — 자금 흐름과 한국 부동산',
    lead: '주식·채권·머니마켓·금·원자재·코인·달러 사이에서 돈이 어디서 빠져 어디로 모이는지, 서울·수도권 아파트 매물과 주간 가격까지 한 스냅숏으로 봅니다. 국면이 바뀌는 신호만 색으로 강조합니다.',
    blocks: [{ type: 'moneyFlow' }]
  };

  /* ---------- utils ---------- */
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function arr(v) { return Object.prototype.toString.call(v) === '[object Array]' ? v : []; }
  function num(v) {
    if (typeof v === 'number' && isFinite(v)) return v;
    if (typeof v === 'string' && v.trim() !== '' && isFinite(Number(v.replace(/,/g, '')))) return Number(v.replace(/,/g, ''));
    return null;
  }
  function txt(v) { return v == null ? '' : String(v); }
  function fmtInt(n) {
    var neg = n < 0, a = Math.abs(Math.round(n));
    return (neg ? '-' : '') + String(a).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }
  function trim1(x) { return (Math.round(x * 10) / 10).toFixed(1).replace(/\.0$/, ''); }
  function fmtPct(v, digits) {
    var n = num(v);
    if (n === null) return '—';
    var d = digits == null ? 1 : digits;
    return (n > 0 ? '+' : n < 0 ? '-' : '') + Math.abs(n).toFixed(d) + '%';
  }
  function fmtMoney(v, unit) {
    var n = num(v);
    if (n === null) return '—';
    var sign = n > 0 ? '+' : n < 0 ? '-' : '';
    var a = Math.abs(n);
    if (unit === 'KRW') {
      if (a >= 1e12) return sign + trim1(a / 1e12) + '조원';
      if (a >= 1e8) return sign + trim1(a / 1e8) + '억원';
      if (a >= 1e4) return sign + fmtInt(a / 1e4) + '만원';
      return sign + fmtInt(a) + '원';
    }
    if (unit === 'USD') {
      if (a >= 1e12) return sign + '$' + trim1(a / 1e12) + 'T';
      if (a >= 1e9) return sign + '$' + trim1(a / 1e9) + 'B';
      if (a >= 1e6) return sign + '$' + trim1(a / 1e6) + 'M';
      if (a >= 1e3) return sign + '$' + fmtInt(a);
      return sign + '$' + trim1(a);
    }
    return sign + fmtInt(a) + (unit ? ' ' + unit : '');
  }
  function unitOf(a) {
    var u = txt(a && a.flow_unit).toUpperCase();
    return u === 'USD' || u === 'KRW' ? u : (u || '');
  }
  /* "+$126.0B", "-14.74조원", "+0.3%" 같은 문자열을 숫자로 */
  function parseFlowText(s) {
    var raw = txt(s).trim();
    if (!raw) return { value: null, unit: '' };
    var cleaned = raw.replace(/\s+/g, '').replace(/[−–—]/g, '-');
    var m = /([+-])?([$₩])?([0-9][0-9,]*(?:\.[0-9]+)?)(조|억|만|T|B|M|K|%)?/i.exec(cleaned);
    if (!m) return { value: null, unit: '' };
    var n = parseFloat(m[3].replace(/,/g, ''));
    if (!isFinite(n)) return { value: null, unit: '' };
    var sign = m[1] === '-' ? -1 : 1;
    var suf = txt(m[4]).toUpperCase();
    var unit;
    if (m[2] === '$' || suf === 'T' || suf === 'B' || suf === 'M' || suf === 'K') unit = 'USD';
    else if (m[2] === '₩' || suf === '조' || suf === '억' || suf === '만' || /원/.test(cleaned)) unit = 'KRW';
    else if (suf === '%') unit = '%';
    else unit = '';
    var mult = suf === '조' ? 1e12 : suf === '억' ? 1e8 : suf === '만' ? 1e4
      : suf === 'T' ? 1e12 : suf === 'B' ? 1e9 : suf === 'M' ? 1e6 : suf === 'K' ? 1e3 : 1;
    return { value: sign * n * mult, unit: unit };
  }
  /* "+0.3% (주간, DXY 103.84)" → {short:'+0.3%', extra:'주간, DXY 103.84'} */
  function pctParts(s) {
    var raw = txt(s).replace(/[−–—]/g, '-').trim();
    var m = /^[+-]?[0-9][0-9,]*(?:\.[0-9]+)?\s*%/.exec(raw);
    if (!m) return { short: raw || '—', extra: '' };
    var extra = raw.slice(m[0].length).trim().replace(/^[(（]\s*/, '').replace(/\s*[)）]\s*$/, '');
    return { short: m[0].replace(/\s+/g, ''), extra: extra };
  }
  function flowOf(a) {
    var v = num(a && a.flow_value);
    var unit = unitOf(a);
    var text = txt(a && a.flow_usd_or_krw).trim();
    if (v !== null) {
      if (unit === '%') {
        var pr = pctParts(text || fmtPct(v, 2));
        return { display: pr.short, value: v, unit: unit, kind: 'pct', extra: pr.extra };
      }
      return { display: text || fmtMoney(v, unit), value: v, unit: unit, kind: 'money', extra: '' };
    }
    if (text) {
      var p = parseFlowText(text);
      if (p.unit === '%') {
        var pr2 = pctParts(text);
        return { display: pr2.short, value: p.value, unit: p.unit, kind: 'pct', extra: pr2.extra };
      }
      return { display: text.replace(/[−–—]/g, '-'), value: p.value, unit: p.unit, kind: 'money', extra: '' };
    }
    return { display: '—', value: null, unit: unit, kind: 'none', extra: '' };
  }
  function ytdOf(a) {
    var raw = a && a.ytd_return;
    var label = txt(a && a.ytd_return_label);
    if (typeof raw === 'number' && isFinite(raw)) return { display: fmtPct(raw, 1), value: raw, full: label };
    var s = txt(raw).trim();
    if (!s) return { display: '—', value: null, full: label };
    var m = /^([+-]?)\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*%/.exec(s);
    if (!m) return { display: s, value: null, full: '' };
    var n = parseFloat(m[2].replace(/,/g, '')) * (m[1] === '-' ? -1 : 1);
    var rest = s.slice(m[0].length).trim().replace(/^[(（]\s*/, '').replace(/\s*[)）]\s*$/, '');
    return {
      display: (n > 0 ? '+' : n < 0 ? '-' : '') + Math.abs(n).toFixed(1).replace(/\.0$/, '') + '%',
      value: n,
      full: rest || label
    };
  }
  function flowDir(a, v) {
    var l = txt(a && a.flow_label);
    if (l.indexOf('유입') >= 0) return 'in';
    if (l.indexOf('유출') >= 0) return 'out';
    if (v !== null && v > 0) return 'in';
    if (v !== null && v < 0) return 'out';
    return 'flat';
  }
  /* 매집/대피/중립 3색. 원자료가 개선/악화면 각각 매집/대피로 옮겨 읽는다. */
  function sigOf(s) {
    var raw = txt(s && s.direction);
    var key = 'neu', label = '중립';
    if (raw.indexOf('매집') >= 0) { key = 'acc'; label = '매집'; }
    else if (raw.indexOf('대피') >= 0) { key = 'esc'; label = '대피'; }
    else if (raw.indexOf('개선') >= 0) { key = 'acc'; label = '매집'; }
    else if (raw.indexOf('악화') >= 0) { key = 'esc'; label = '대피'; }
    return { key: key, label: label, raw: raw };
  }
  function regimeOf(data) {
    var r = (data && data.regime && typeof data.regime === 'object') ? data.regime : {};
    return { label: txt(r.label || r.verdict), note: txt(r.note || r.reason), basis: txt(r.basis_date) };
  }
  function regimeKey(label) {
    var s = txt(label);
    if (s.indexOf('상승') >= 0) return 'up';
    if (s.indexOf('하락') >= 0) return 'down';
    if (s.indexOf('조정') >= 0) return 'mid';
    return 'none';
  }
  function safeUrl(u) {
    var s = txt(u).trim();
    return /^https?:\/\/[^\s<>"']+$/i.test(s) ? s : '';
  }
  function hostOf(u) {
    try { return new URL(u).hostname.replace(/^www\./, ''); } catch (e) { return '출처'; }
  }
  function prettyDate(d) {
    var s = txt(d);
    return s ? s.replace(/-/g, '.') : '기준일 미상';
  }
  function chgCls(v) { var n = num(v); return n === null ? 't-muted' : n > 0 ? 't-pos' : n < 0 ? 't-neg' : 't-muted'; }

  /* ---------- normalize ---------- */
  function normAssets(list) {
    return arr(list).filter(function (a) { return a && typeof a === 'object'; }).map(function (a) {
      var f = flowOf(a);
      var y = ytdOf(a);
      var dir = flowDir(a, f.value);
      return {
        name: txt(a.name) || '자산',
        dir: dir,
        label: txt(a.flow_label) || (dir === 'in' ? '순유입' : dir === 'out' ? '순유출' : '—'),
        display: f.display,
        value: f.value,
        unit: f.unit || '',
        kind: f.kind,
        extra: f.extra || '',
        ytd: y,
        period: txt(a.flow_period),
        note: txt(a.note),
        url: safeUrl(a.source_url)
      };
    });
  }
  function maxByUnit(items) {
    var m = {};
    items.forEach(function (it) {
      if (it.value === null || it.kind !== 'money') return;
      var u = it.unit || '';
      if (!m[u] || Math.abs(it.value) > m[u]) m[u] = Math.abs(it.value);
    });
    return m;
  }

  /* ---------- data ---------- */
  function fetchJSON(url) {
    return fetch(url, { cache: 'no-store' }).then(function (r) {
      if (!r.ok) {
        var e = new Error('HTTP ' + r.status);
        e.status = r.status;
        throw e;
      }
      return r.json().catch(function () {
        var e2 = new Error('parse error');
        e2.status = 0;
        throw e2;
      });
    });
  }

  function loadData() {
    return fetchJSON('data/money-flow.json').then(function (d) {
      if (!d || typeof d !== 'object') { var e = new Error('empty'); e.status = 0; throw e; }
      return { data: d, sample: false, reason: '' };
    }).catch(function (err) {
      var reason = err && err.status === 404 ? 'missing' : 'broken';
      return fetchJSON('data/money-flow.sample.json').then(function (d) {
        return { data: d, sample: true, reason: reason };
      });
    });
  }

  /* history를 시간순(오래된 → 최신)으로. 현재 스냅숏이 빠져 있으면 붙인다. */
  function snapshots(data) {
    var hist = arr(data.history).filter(function (s) { return s && typeof s === 'object'; });
    var cur = {
      as_of: data.as_of,
      regime: data.regime,
      assets: data.assets,
      signals: data.signals,
      korea_property: data.korea_property,
      _current: true
    };
    if (!hist.length) return [cur];
    var last = hist[hist.length - 1];
    if (txt(last.as_of) === txt(data.as_of) && txt(data.as_of) !== '') return hist;
    return hist.concat([cur]);
  }

  /* ---------- blocks ---------- */
  function topHTML(data, sample) {
    var asof = txt(data.as_of);
    var rg = regimeOf(data);
    var key = regimeKey(rg.label);
    var h = '<div class="mf-topbar">' +
      '<span class="mf-cron"><b>매주 일요일 아침</b> 갱신 · 자산별 자금 흐름과 한국 부동산</span>' +
      (sample ? '<span class="mf-sample">예시 데이터</span>' : '') +
      '</div>';
    h += '<div class="mf-hero">' +
      '<div class="mf-hero-l"><span class="mf-k">기준일</span><b class="mf-asof">' + esc(asof || '기준일 미상') + '</b>' +
        (rg.basis && rg.basis !== asof ? '<span class="mf-basis">시장 기준 ' + esc(prettyDate(rg.basis)) + '</span>' : '') +
      '</div>' +
      '<span class="mf-regime mf-rg-' + key + '">' + esc(rg.label || '판정 없음') + '</span>' +
      '</div>';
    if (rg.note) h += '<p class="mf-regime-note">' + esc(rg.note) + '</p>';
    if (sample) {
      h += '<p class="mf-sample-note">' + esc(state.reason === 'broken'
        ? '실제 스냅숏을 읽지 못해 예시 데이터로 보여 줍니다. 매주 일요일 아침 새 스냅숏이 올라오면 그대로 바뀝니다.'
        : '아직 첫 실제 스냅숏이 없어 예시 데이터로 보여 줍니다. 매주 일요일 아침 새 스냅숏이 올라오면 그대로 바뀝니다.') + '</p>';
    }
    return h;
  }

  function flashHTML(data) {
    var evac = arr(data.signals).filter(function (s) { return s && sigOf(s).key === 'esc'; });
    if (!evac.length) return '';
    var names = evac.map(function (s) { return txt(s.name); }).filter(Boolean).slice(0, 3).join(' · ');
    return '<div class="mf-flash">' +
      '<div class="mf-flash-h"><span class="mf-flash-dot"></span>대피 신호 ' + evac.length + '개</div>' +
      '<p>' + esc(names) + ' — 안전자산으로 빠지는 움직임이 켜져 있습니다. 아래 신호에서 근거를 확인하세요.</p>' +
      '</div>';
  }

  function assetsHTML(data) {
    var items = normAssets(data.assets);
    if (!items.length) return '<div class="mf-none">이번 스냅숏에는 자산별 수치가 없습니다.</div>';
    var max = maxByUnit(items);
    var cards = items.map(function (it) {
      var isMoney = it.kind === 'money';
      var showBar = isMoney && it.value !== null && max[it.unit];
      var pct = showBar ? Math.max(7, Math.round(Math.abs(it.value) / max[it.unit] * 100)) : 0;
      var ytdCls = it.ytd.value === null ? 't-muted' : it.ytd.value > 0 ? 't-pos' : it.ytd.value < 0 ? 't-neg' : 't-muted';
      var arrow = it.dir === 'in' ? '▲' : it.dir === 'out' ? '▼' : '–';
      var tip = [it.period, it.note].filter(Boolean).join(' — ');
      var periodText = it.period || it.extra;
      return '<article class="mf-asset mf-on-' + it.dir + '">' +
        '<div class="mf-asset-head"><h4>' + esc(it.name) + '</h4>' +
          '<span class="mf-flowtag mf-ft-' + it.dir + '"' + (it.period ? ' title="' + esc(it.period) + '"' : '') + '>' +
            arrow + ' ' + esc(it.label) + '</span></div>' +
        '<div class="mf-amount"' + (tip ? ' title="' + esc(tip) + '"' : '') + '>' + esc(it.display) + '</div>' +
        (isMoney ? '<div class="mf-bar" aria-hidden="true">' + (pct ? '<i style="width:' + pct + '%"></i>' : '') + '</div>' : '') +
        (periodText ? '<div class="mf-period" title="' + esc(tip || periodText) + '">' + esc(periodText) + '</div>' : '') +
        '<div class="mf-asset-foot">' +
          '<span class="mf-ytd"' + (it.ytd.full ? ' title="' + esc(it.ytd.full) + '"' : '') + '>YTD <b class="' + ytdCls + '">' + esc(it.ytd.display) + '</b></span>' +
          (it.kind === 'pct' ? '<span class="mf-unit">가격 변동</span>' : it.unit ? '<span class="mf-unit">' + esc(it.unit) + '</span>' : '') +
          (it.url ? '<a class="mf-src-link" href="' + esc(it.url) + '" target="_blank" rel="noopener noreferrer" title="출처 열기">출처 ↗</a>' : '') +
        '</div>' +
      '</article>';
    }).join('');
    return '<div class="mf-sec-head"><h3>자산별 자금 흐름</h3><span class="mf-sec-sub">최근 순유입·순유출 · YTD 수익률</span></div>' +
      '<div class="mf-assets">' + cards + '</div>';
  }

  function fmtCount(v) {
    var n = num(v); if (n === null) return '—';
    if (Math.abs(n) >= 10000) return trim1(n / 10000) + '만';
    return fmtInt(n);
  }

  function propertyHTML(p, prevP) {
    var has = p && typeof p === 'object';
    var L = (has && p.listings && typeof p.listings === 'object') ? p.listings : {};
    var P = (has && p.price && typeof p.price === 'object') ? p.price : {};
    var seoul = num(L.seoul), metro = num(L.metro);
    var ps = num(P.seoul_weekly), pm = num(P.metro_weekly);
    var jr = num(has ? p.jeonse_ratio : null);
    if (jr !== null && jr > 0 && jr <= 1.5) jr = jr * 100;
    var any = [seoul, metro, ps, pm, jr].some(function (x) { return x !== null; });

    var sub = !any ? '' : (has && p.as_of ? esc(prettyDate(p.as_of)) + ' 기준 아파트' : '주간 아파트');
    var head = '<div class="mf-sec-head"><h3>한국 부동산 <em>서울 · 수도권</em></h3>' +
      (sub ? '<span class="mf-sec-sub">' + sub + '</span>' : '') + '</div>';
    if (!any) return head + '<div class="mf-none">이번 스냅숏에는 부동산 데이터 없음</div>';

    var tiles = '';
    if (seoul !== null || metro !== null) {
      var prevSeoul = prevP && prevP.listings ? num(prevP.listings.seoul) : null;
      var chg = (seoul !== null && prevSeoul) ? (seoul - prevSeoul) / prevSeoul * 100 : null;
      var dirWord = txt(L.direction) || (chg === null ? '' : chg > 0 ? '증가' : chg < 0 ? '감소' : '보합');
      var dirCls = dirWord.indexOf('증가') >= 0 ? 'up' : dirWord.indexOf('감소') >= 0 ? 'down' : 'flat';
      var arrow = dirCls === 'up' ? '▲' : dirCls === 'down' ? '▼' : '–';
      var noteText = chg !== null ? '전주 대비 ' + fmtPct(chg, 1) : '';
      tiles += '<div class="mf-tile">' +
        '<div class="mf-tile-k">매물 건수</div>' +
        '<div class="mf-rows">' +
          '<div class="mf-row"><span>서울</span><b>' + esc(seoul === null ? '—' : fmtCount(seoul)) + '</b></div>' +
          '<div class="mf-row"><span>수도권</span><b>' + esc(metro === null ? '—' : fmtCount(metro)) + '</b></div>' +
        '</div>' +
        (dirWord ? '<div class="mf-tile-foot mf-dir-' + dirCls + '">' + arrow + ' ' + esc(dirWord) +
          (noteText ? '<span class="mf-dirnote">' + esc(noteText) + '</span>' : '') + '</div>' : '') +
      '</div>';
    }
    if (ps !== null || pm !== null) {
      tiles += '<div class="mf-tile">' +
        '<div class="mf-tile-k">주간 매매가격지수</div>' +
        '<div class="mf-rows">' +
          '<div class="mf-row"><span>서울</span><b class="' + chgCls(ps) + '">' + esc(fmtPct(ps, 2)) + '</b></div>' +
          '<div class="mf-row"><span>수도권</span><b class="' + chgCls(pm) + '">' + esc(fmtPct(pm, 2)) + '</b></div>' +
        '</div>' +
        '<div class="mf-tile-foot mf-foot-mute">주간 변동률</div>' +
      '</div>';
    }
    if (jr !== null) {
      tiles += '<div class="mf-tile">' +
        '<div class="mf-tile-k">전세가율</div>' +
        '<div class="mf-big">' + esc(trim1(jr)) + '%</div>' +
        '<div class="mf-meter" aria-hidden="true"><i style="width:' + Math.max(0, Math.min(100, jr)) + '%"></i></div>' +
      '</div>';
    }

    var srcs = arr(has ? p.source_urls : []).map(safeUrl).filter(Boolean);
    var srcHTML = srcs.length ? '<div class="mf-src">출처 ' + srcs.map(function (u) {
      return '<a href="' + esc(u) + '" target="_blank" rel="noopener noreferrer">' + esc(hostOf(u)) + ' ↗</a>';
    }).join('') + '</div>' : '';

    var notes = [];
    if (txt(L.note)) notes.push(txt(L.note));
    if (txt(P.note) && txt(P.note) !== txt(L.note)) notes.push(txt(P.note));
    var noteHTML = notes.length ? '<details class="mf-prop-more"><summary>지표 설명 보기</summary>' +
      notes.map(function (n) { return '<p>' + esc(n) + '</p>'; }).join('') + '</details>' : '';

    return head + '<div class="mf-prop"><div class="mf-prop-grid">' + tiles + '</div>' + noteHTML + srcHTML + '</div>';
  }

  function signalsHTML(data) {
    var list = arr(data.signals).filter(function (s) { return s && typeof s === 'object'; });
    if (!list.length) return '';
    return '<div class="mf-sec-head"><h3>매집 vs 대피</h3><span class="mf-sec-sub">지금 켜진 신호와 해석</span></div>' +
      '<div class="mf-signals">' + list.map(function (s) {
        var sg = sigOf(s);
        var url = safeUrl(s.source_url);
        return '<div class="mf-sig mf-sg-' + sg.key + '">' +
          '<div class="mf-sig-top">' +
            '<span class="mf-dirb mf-db-' + sg.key + '"' + (sg.raw && sg.raw !== sg.label ? ' title="원자료: ' + esc(sg.raw) + '"' : '') + '>' + esc(sg.label) + '</span>' +
            '<b class="mf-sig-name">' + esc(txt(s.name) || '지표') + '</b>' +
            (txt(s.value) ? '<span class="mf-sig-val">' + esc(s.value) + '</span>' : '') +
          '</div>' +
          (txt(s.interpretation) ? '<p class="mf-sig-note">' + esc(s.interpretation) + '</p>' : '') +
          (url ? '<div class="mf-src"><a href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">출처 ' + esc(hostOf(url)) + ' ↗</a></div>' : '') +
        '</div>';
      }).join('') + '</div>';
  }

  function snapSummary(snap) {
    var items = normAssets(snap && snap.assets).filter(function (it) { return it.value !== null; });
    if (!items.length) return '';
    var max = maxByUnit(items);
    var scored = items.map(function (it) {
      return { it: it, s: max[it.unit] ? Math.abs(it.value) / max[it.unit] : 0 };
    });
    var pick = function (dir) {
      return scored.filter(function (x) { return x.it.dir === dir; })
        .sort(function (p, q) { return q.s - p.s; }).slice(0, 2)
        .map(function (x) { return x.it.name; }).filter(Boolean);
    };
    var ins = pick('in'), outs = pick('out');
    var parts = [];
    if (ins.length) parts.push('순유입 ' + ins.join('·'));
    if (outs.length) parts.push('순유출 ' + outs.join('·'));
    return parts.join(' · ');
  }

  function propSummary(snap, prev) {
    var p = snap && snap.korea_property;
    if (!p || typeof p !== 'object') return '';
    var L = (p.listings && typeof p.listings === 'object') ? p.listings : {};
    var P = (p.price && typeof p.price === 'object') ? p.price : {};
    var s = num(L.seoul), ps = num(P.seoul_weekly);
    var prevS = prev && prev.korea_property && prev.korea_property.listings ? num(prev.korea_property.listings.seoul) : null;
    var parts = [];
    if (s !== null && prevS) parts.push('서울 매물 ' + fmtPct((s - prevS) / prevS * 100, 1));
    else if (s !== null && txt(L.direction)) parts.push('서울 매물 ' + txt(L.direction));
    if (ps !== null) parts.push('매매가 ' + fmtPct(ps, 2));
    return parts.join(' · ');
  }

  function histBody(snap, prev) {
    var items = normAssets(snap && snap.assets);
    var rows = items.map(function (it) {
      return '<div class="mf-hrow mf-hr-' + it.dir + '">' +
        '<span class="mf-hname">' + esc(it.name) + '</span>' +
        '<span class="mf-hflow">' + esc(it.display) + '</span>' +
        '<span class="mf-hytd ' + chgCls(it.ytd.value) + '">' + esc(it.ytd.display) + '</span>' +
      '</div>';
    }).join('');
    var rg = snap && snap.regime ? snap.regime : {};
    var note = txt(rg.note || rg.reason);
    var ps = propSummary(snap, prev);
    return '<div class="mf-hbody">' +
      (rows || '<div class="mf-none">자산 수치 없음</div>') +
      (ps ? '<div class="mf-hprop">한국 부동산 · ' + esc(ps) + '</div>' : '') +
      (note ? '<div class="mf-hnote">' + esc(note) + '</div>' : '') +
    '</div>';
  }

  function historyHTML(snaps) {
    var head = '<div class="mf-sec-head"><h3>스냅숏 기록</h3><span class="mf-sec-sub">최신순 · 눌러서 자산별 수치 펼치기</span></div>';
    if (!snaps.length) return head + '<div class="mf-none">과거 스냅숏이 아직 없습니다. 이번 스냅숏이 첫 기록입니다.</div>';
    var ordered = snaps.slice().reverse();
    var items = ordered.map(function (s, i) {
      var idx = snaps.length - 1 - i;
      var prev = idx > 0 ? snaps[idx - 1] : null;
      var rg = regimeOf(s);
      var k = regimeKey(rg.label);
      var prevK = prev ? regimeKey(regimeOf(prev).label) : '';
      var changed = prev && k !== 'none' && prevK !== k;
      var sum = snapSummary(s);
      var ps = propSummary(s, prev);
      return '<details class="mf-hitem">' +
        '<summary>' +
          '<span class="mf-hdate">' + esc(prettyDate(s.as_of)) + '</span>' +
          '<span class="mf-regime mf-rg-' + k + '">' + esc(rg.label || '—') + '</span>' +
          (changed ? '<span class="mf-hswitch">국면 전환</span>' : '') +
          '<span class="mf-hsum">' + esc(sum || '자산 수치 없음') + '</span>' +
          (ps ? '<span class="mf-hp">' + esc(ps) + '</span>' : '') +
        '</summary>' +
        histBody(s, prev) +
      '</details>';
    }).join('');
    return head + '<div class="mf-hist">' + items + '</div>';
  }

  function footHTML() {
    return '<p class="mf-foot">국내 관례대로 유입·상승은 빨강, 유출·하락은 파랑입니다. 매집은 민트, 대피는 주황. ' +
      '부동산 매물은 증가 ▲(앰버) / 감소 ▼(민트)로 표시합니다. 수치는 기준일까지의 최근 흐름이며, 카드를 누르면 측정 기간이 보입니다. ' +
      '과거 스냅숏은 지우지 않고 쌓입니다.</p>';
  }

  function topBarOnly() {
    return '<div class="mf-topbar"><span class="mf-cron"><b>매주 일요일 아침</b> 갱신 · 자산별 자금 흐름과 한국 부동산</span></div>';
  }
  function emptyHTML() {
    return topBarOnly() +
      '<div class="mf-empty"><b>아직 스냅숏이 없습니다</b>' +
      '<p>매주 일요일 아침에 새 스냅숏이 올라옵니다.</p></div>' + footHTML();
  }

  function build(data, sample) {
    if (!data || typeof data !== 'object') return emptyHTML();
    var snaps = snapshots(data);
    var prev = snaps.length > 1 ? snaps[snaps.length - 2] : null;
    return topHTML(data, sample) +
      flashHTML(data) +
      '<div class="mf-body">' +
        assetsHTML(data) +
        propertyHTML(data.korea_property, prev && prev.korea_property) +
        signalsHTML(data) +
        historyHTML(snaps) +
      '</div>' +
      footHTML();
  }

  /* ---------- mount ---------- */
  function paint(root) {
    if (!root) return;
    root.innerHTML = build(state.data, state.sample);
  }

  function mount() {
    var root = document.getElementById(ROOT_ID);
    if (!root) return;
    if (state.status === 'ready') { paint(root); return; }
    if (state.status === 'loading') return;
    state.status = 'loading';
    root.innerHTML = '<div class="mf-loading">경제 인사이트 불러오는 중…</div>';
    loadData().then(function (res) {
      state.data = res.data;
      state.sample = res.sample;
      state.reason = res.reason || '';
      state.status = 'ready';
      paint(document.getElementById(ROOT_ID));
    }).catch(function () {
      state.status = 'error';
      state.data = null;
      state.sample = false;
      var r = document.getElementById(ROOT_ID);
      if (r) r.innerHTML = emptyHTML();
    });
  }

  function html() {
    return '<div class="block mf" id="' + ROOT_ID + '"><div class="mf-loading">경제 인사이트 불러오는 중…</div></div>';
  }

  function attach(data) {
    if (!data || !arr(data.sections)) return data;
    var exists = data.sections.some(function (s) { return s && s.id === SECTION.id; });
    if (!exists) data.sections.splice(1, 0, SECTION);
    return data;
  }

  window.EolmiMoneyFlow = { attach: attach, html: html, mount: mount, section: SECTION };
})();
