(function () {
  'use strict';

  var app = document.getElementById('app');
  var tabsBar = document.getElementById('tabs');
  var DATA = null;
  var activeId = null;
  var tvJsPromise = null;
  var SCR = null;
  var SCR_HIST = null;
  var MA_DATA = null;
  var maWidgets = [];

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function tone(t) { return t ? 't-' + t : ''; }

  /* ---------------- cells ---------------- */
  function cellHTML(c) {
    if (c == null) return '';
    if (typeof c === 'string' || typeof c === 'number') {
      return '<span class="cell-main">' + esc(c) + '</span>';
    }
    var cls = ['cell-main', c.strong ? 'cell-strong' : '', tone(c.tone)].filter(Boolean).join(' ');
    var out = '<span class="' + cls + '">' + esc(c.t) + '</span>';
    if (c.sub) out += '<span class="cell-sub">' + esc(c.sub) + '</span>';
    return out;
  }

  /* ---------------- blocks ---------------- */
  function blockCallout(b) {
    return '<div class="block"><div class="callout ' + esc(b.tone || '') + '">' +
      (b.title ? '<div class="c-title">' + esc(b.title) + '</div>' : '') +
      '<div class="c-text">' + esc(b.text) + '</div></div></div>';
  }

  function blockTable(b) {
    var mw = b.minWidth || 640;
    var head = '<tr>' + b.head.map(function (h) { return '<th scope="col">' + esc(h) + '</th>'; }).join('') + '</tr>';
    var body = b.rows.map(function (r) {
      return '<tr>' + r.map(function (c, i) {
        if (b.pin && i === 0) return '<th scope="row">' + cellHTML(c) + '</th>';
        return '<td>' + cellHTML(c) + '</td>';
      }).join('') + '</tr>';
    }).join('');

    return '<div class="block"><div class="table-card">' +
      '<div class="table-head">' +
        '<div class="table-cap">' + esc(b.caption) + '</div>' +
        (b.subcaption ? '<div class="table-sub">' + esc(b.subcaption) + '</div>' : '') +
      '</div>' +
      '<div class="table-hint">← 표를 옆으로 넘겨서 보세요</div>' +
      '<div class="table-wrap' + (b.pin ? ' pin' : '') + '" style="--mw:' + mw + 'px">' +
        '<table' + (b.wrap ? ' class="wrap"' : '') + '><thead>' + head + '</thead><tbody>' + body + '</tbody></table>' +
      '</div>' +
      (b.note ? '<div class="table-note">' + esc(b.note) + '</div>' : '') +
    '</div></div>';
  }

  function blockCards(b) {
    var items = b.items.map(function (it) {
      var head;
      if (it.sub || it.status) {
        head = '<div class="card-head">' +
          (it.tag ? '<span class="tag">' + esc(it.tag) + '</span>' : '') +
          '<div class="card-headtxt"><h3>' + esc(it.title) + '</h3>' +
            (it.sub ? '<div class="card-sub">' + esc(it.sub) + '</div>' : '') +
          '</div>' +
          (it.status ? '<span class="card-status ' + (it.statusTone ? 't-' + esc(it.statusTone) : '') + '">' + esc(it.status) + '</span>' : '') +
        '</div>';
      } else {
        head = (it.tag ? '<span class="tag">' + esc(it.tag) + '</span>' : '') +
          '<h3>' + esc(it.title) + '</h3>';
      }
      return '<div class="card">' + head +
        (it.lines || []).map(function (l) { return '<p>' + esc(l) + '</p>'; }).join('') +
      '</div>';
    }).join('');
    return '<div class="block">' +
      (b.title ? '<div class="cards-head"><div class="table-cap">' + esc(b.title) + '</div>' +
        (b.sub ? '<div class="table-sub">' + esc(b.sub) + '</div>' : '') + '</div>' : '') +
      '<div class="cards' + (b.cols === 3 ? ' c3' : '') + '">' + items + '</div></div>';
  }

  function blockList(b) {
    return '<div class="block"><div class="listcard">' +
      (b.title ? '<div class="lc-title">' + esc(b.title) + '</div>' : '') +
      '<ul>' + b.items.map(function (i) { return '<li>' + esc(i) + '</li>'; }).join('') + '</ul>' +
    '</div></div>';
  }

  function blockTimeline(b) {
    var items = b.items.map(function (it) {
      return '<div class="tl-item">' +
        '<div class="tl-date ' + esc(it.tone || '') + '">' + esc(it.date) + '</div>' +
        '<div><div class="tl-t">' + esc(it.title) + '</div><div class="tl-d">' + esc(it.desc) + '</div></div>' +
      '</div>';
    }).join('');
    return '<div class="block">' + (b.title ? '<div class="table-cap" style="margin-bottom:10px">' + esc(b.title) + '</div>' : '') +
      '<div class="tl">' + items + '</div></div>';
  }

  function blockChips(b) {
    return '<div class="block"><div class="chips-wrap">' +
      (b.title ? '<div class="lc-title">' + esc(b.title) + '</div>' : '') +
      '<div class="chips">' + b.items.map(function (c) {
        return '<span class="chip"><b>' + esc(c.tag) + '</b>' + esc(c.label) + '</span>';
      }).join('') + '</div>' +
      (b.note ? '<div class="chips-note">' + esc(b.note) + '</div>' : '') +
    '</div></div>';
  }

  function blockNews(b) {
    var cards = b.items.map(function (n) {
      return '<div class="news-card">' +
        '<span class="news-tag n-' + esc(n.tone || 'muted') + '">' + esc(n.tag) + '</span>' +
        '<div class="news-title">' + esc(n.title) + '</div>' +
        '<div class="news-body">' + esc(n.body) + '</div>' +
        (n.meta ? '<div class="news-meta">' + esc(n.meta) + '</div>' : '') +
      '</div>';
    }).join('');
    return '<div class="block">' +
      (b.title ? '<div class="table-cap" style="margin-bottom:4px">' + esc(b.title) + '</div>' : '') +
      (b.sub ? '<div class="table-sub" style="margin-bottom:11px">' + esc(b.sub) + '</div>' : '') +
      '<div class="news">' + cards + '</div></div>';
  }

  function blockTV(b) {
    var chips = b.symbols.map(function (s) {
      var on = s.symbol === (b.default || b.symbols[0].symbol);
      return '<button class="sym' + (on ? ' on' : '') + '" data-symbol="' + esc(s.symbol) + '" type="button">' +
        esc(s.label) + '<b>' + esc(s.tag) + '</b></button>';
    }).join('');
    return '<div class="block"><div class="tvcard">' +
      '<div class="symbar">' + chips + '</div>' +
      '<div class="tv-wrap" id="tv-chart"></div>' +
      (b.note ? '<div class="tv-note">' + esc(b.note) + '</div>' : '') +
    '</div></div>';
  }

  function blockTVTimeline(b) {
    return '<div class="block"><div class="tvcard">' +
      '<div class="table-cap">' + esc(b.title) + '</div>' +
      (b.sub ? '<div class="table-sub">' + esc(b.sub) + '</div>' : '') +
      '<div class="tv-wrap tall" id="tv-news" style="margin-top:10px"></div>' +
    '</div></div>';
  }

  /* ---------------- charts ---------------- */
  function chartShell(b, inner) {
    return '<div class="block"><div class="chartcard">' +
      '<div class="chart-head"><div class="t">' + esc(b.title) + '</div>' +
      (b.sub ? '<div class="s">' + esc(b.sub) + '</div>' : '') + '</div>' +
      '<div class="chart-body">' + inner + '</div>' +
      (b.note ? '<div class="chart-note">' + esc(b.note) + '</div>' : '') +
    '</div></div>';
  }

  function lineChart(b) {
    var W = 760, H = 320, pad = { l: 42, r: 20, t: 34, b: 40 };
    var vals = b.values, labels = b.labels, n = vals.length;
    var yMin = b.yMin, yMax = b.yMax;
    var x = function (i) { return pad.l + i * (W - pad.l - pad.r) / (n - 1); };
    var y = function (v) { return pad.t + (yMax - v) / (yMax - yMin) * (H - pad.t - pad.b); };

    var grid = '';
    for (var s = 0; s <= 4; s++) {
      var gv = yMin + (yMax - yMin) * s / 4;
      var gy = y(gv);
      grid += '<line x1="' + pad.l + '" y1="' + gy.toFixed(1) + '" x2="' + (W - pad.r) + '" y2="' + gy.toFixed(1) + '" stroke="rgba(255,255,255,.07)" stroke-width="1"/>' +
        '<text x="' + (pad.l - 8) + '" y="' + (gy + 3.5).toFixed(1) + '" text-anchor="end" font-size="10" fill="#67738a">' + gv.toFixed(1) + '</text>';
    }

    var pts = vals.map(function (v, i) { return x(i).toFixed(1) + ',' + y(v).toFixed(1); });
    var linePath = 'M' + pts.join(' L');
    var areaPath = linePath + ' L' + x(n - 1).toFixed(1) + ',' + (H - pad.b) + ' L' + x(0).toFixed(1) + ',' + (H - pad.b) + ' Z';

    var dots = vals.map(function (v, i) {
      var isLast = i === n - 1;
      var r = isLast ? 4 : 2.6;
      return '<circle cx="' + x(i).toFixed(1) + '" cy="' + y(v).toFixed(1) + '" r="' + r + '" fill="' + (isLast ? '#7cf5c8' : '#66d9ff') + '">' +
        '<title>' + esc(labels[i]) + ' · ' + v.toFixed(2) + '%</title></circle>';
    }).join('');

    var xlabels = '';
    for (var i = 0; i < n; i++) {
      var show = (i % 3 === 0 && i <= n - 4) || i === n - 1;
      if (!show) continue;
      var anchor = i === 0 ? 'start' : (i === n - 1 ? 'end' : 'middle');
      xlabels += '<text x="' + x(i).toFixed(1) + '" y="' + (H - pad.b + 18) + '" text-anchor="' + anchor + '" font-size="10" fill="#5f6b7f">' + esc(labels[i]) + '</text>';
    }

    var marks = (b.marks || []).map(function (m) {
      var mx = x(m.index), my = y(vals[m.index]);
      var above = m.align !== 'below';
      var ty = above ? my - 16 : my + 26;
      var anchor = mx > W - 140 ? 'end' : (mx < 90 ? 'start' : 'middle');
      return '<line x1="' + mx.toFixed(1) + '" y1="' + (above ? my - 6 : my + 6).toFixed(1) + '" x2="' + mx.toFixed(1) + '" y2="' + (above ? ty + 4 : ty - 10).toFixed(1) + '" stroke="rgba(255,255,255,.28)" stroke-width="1" stroke-dasharray="3 3"/>' +
        '<text x="' + mx.toFixed(1) + '" y="' + ty.toFixed(1) + '" text-anchor="' + anchor + '" font-size="11" font-weight="700" fill="#e9eef7">' + esc(m.text) + '</text>';
    }).join('');

    var svg = '<svg class="chart" viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="' + esc(b.title) + '">' +
      '<defs><linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">' +
        '<stop offset="0" stop-color="#66d9ff" stop-opacity=".26"/>' +
        '<stop offset="1" stop-color="#66d9ff" stop-opacity="0"/>' +
      '</linearGradient></defs>' +
      grid +
      '<path d="' + areaPath + '" fill="url(#areaGrad)"/>' +
      '<path d="' + linePath + '" fill="none" stroke="#7cf5c8" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"/>' +
      dots + marks + xlabels +
    '</svg>';

    return chartShell(b, svg);
  }

  function barsChart(b) {
    var max = b.max || 50;
    var rows = b.items.map(function (it) {
      var pct = Math.min(100, Math.abs(it.value) / max * 100);
      var hot = Math.abs(it.value) >= 30 ? ' hot' : '';
      return '<div class="bar-row">' +
        '<div class="bar-label">' + esc(it.label) + '</div>' +
        '<div class="bar-track"><div class="bar-fill' + hot + '" style="width:' + pct.toFixed(1) + '%"></div></div>' +
        '<div class="bar-value">' + (it.value > 0 ? '+' : '') + it.value.toFixed(1) + '%</div>' +
        (it.note ? '<div class="bar-note">' + esc(it.note) + '</div>' : '') +
      '</div>';
    }).join('');
    return chartShell(b, '<div class="bars">' + rows + '</div>');
  }

  function rangeChart(b) {
    var min = b.min != null ? b.min : 0;
    var max = b.max != null ? b.max : 60;
    var span = max - min;
    var pos = function (v) { return (v - min) / span * 100; };

    var segs = b.zones.map(function (z) {
      return '<span class="gauge-seg g-' + esc(z.tone) + '" style="width:' + ((z.to - z.from) / span * 100).toFixed(3) + '%"></span>';
    }).join('');

    var marks = b.markers.map(function (m) {
      var p = pos(m.value);
      var tx = p > 84 ? '-95%' : (p < 10 ? '-5%' : '-50%');
      return '<span class="gauge-mark" style="left:' + p.toFixed(2) + '%">' +
        '<i class="dot"></i>' +
        '<span class="lab ' + esc(m.align || 'above') + '" style="transform:translateX(' + tx + ')">' + esc(m.label) + '</span>' +
      '</span>';
    }).join('');

    var ticks = (function () {
      var set = [min, max];
      b.zones.forEach(function (z) { set.push(z.from, z.to); });
      return set.filter(function (v, idx, arr) { return arr.indexOf(v) === idx; })
        .sort(function (a, c) { return a - c; });
    })().map(function (v) {
      return '<span style="left:' + pos(v).toFixed(2) + '%">' + v + '%</span>';
    }).join('');

    var legend = b.zones.map(function (z) {
      return '<span><i class="g-' + esc(z.tone) + '"></i>' + esc(z.label) + ' ' + z.from + '~' + z.to + '%</span>';
    }).join('');

    var inner = '<div class="gauge"><div class="gauge-track">' + segs + '</div>' + marks + '</div>' +
      '<div class="gauge-scale">' + ticks + '</div>' +
      '<div class="gauge-legend">' + legend + '</div>';

    return chartShell(b, inner);
  }

  /* 반원형 계기판(다이얼) — 공포지수·VIX처럼 0~100 감정/변동성 눈금 */
  function polarPt(cx, cy, r, deg) {
    var a = deg * Math.PI / 180;
    return [cx + r * Math.cos(a), cy - r * Math.sin(a)];
  }

  function arcPath(cx, cy, r, a0, a1) {
    var p0 = polarPt(cx, cy, r, a0), p1 = polarPt(cx, cy, r, a1);
    var large = Math.abs(a0 - a1) > 180 ? 1 : 0;
    return 'M' + p0[0].toFixed(2) + ' ' + p0[1].toFixed(2) +
      ' A' + r + ' ' + r + ' 0 ' + large + ' 1 ' + p1[0].toFixed(2) + ' ' + p1[1].toFixed(2);
  }

  function dialGauge(b) {
    var W = 240, H = 142, cx = 120, cy = 120, r = 92;
    var cards = (b.items || []).map(function (g) {
      var min = g.min != null ? g.min : 0;
      var max = g.max != null ? g.max : 100;
      var span = (max - min) || 1;
      var ang = function (v) {
        var c = Math.max(min, Math.min(max, v));
        return 180 - (c - min) / span * 180;
      };

      var prev = min, segs = '';
      (g.zones || []).forEach(function (z) {
        var to = z.to != null ? z.to : max;
        var a0 = ang(prev), a1 = ang(to), pad = 1.3;
        var s0 = a0 < 179.9 ? a0 - pad : a0;
        var s1 = a1 > 0.1 ? a1 + pad : a1;
        if (s0 - s1 > 0.4) {
          segs += '<path d="' + arcPath(cx, cy, r, s0, s1) + '" class="dseg g-' + esc(z.tone || 'muted') + '"/>';
        }
        prev = to;
      });

      var tip = polarPt(cx, cy, r - 22, ang(g.value));
      var label = (g.name || '') + ' ' + (g.display != null ? g.display : g.value) + (g.unit || '') + ' ' + (g.status || '');
      var svg = '<svg class="dial-svg" viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="' + esc(label) + '">' +
        '<path d="' + arcPath(cx, cy, r, 180, 0) + '" class="dseg-track"/>' + segs +
        '<line x1="' + cx + '" y1="' + cy + '" x2="' + tip[0].toFixed(2) + '" y2="' + tip[1].toFixed(2) + '" class="dneedle"/>' +
        '<circle cx="' + cx + '" cy="' + cy + '" r="5.5" class="dhub"/>' +
        '<circle cx="' + cx + '" cy="' + cy + '" r="2.1" class="dhub-core"/>' +
        '<text x="' + (cx - r) + '" y="' + (cy + 19) + '" text-anchor="middle" class="dscale">' + esc(g.minLabel != null ? g.minLabel : min) + '</text>' +
        '<text x="' + (cx + r) + '" y="' + (cy + 19) + '" text-anchor="middle" class="dscale">' + esc(g.maxLabel != null ? g.maxLabel : max) + '</text>' +
        '</svg>';

      return '<div class="dial">' +
        '<div class="dial-top"><span class="dial-name">' + esc(g.name) + '</span>' +
          (g.status ? '<span class="dial-status' + (g.statusTone ? ' t-' + esc(g.statusTone) : '') + '">' + esc(g.status) + '</span>' : '') +
        '</div>' + svg +
        '<div class="dial-read"><b' + (g.statusTone ? ' class="t-' + esc(g.statusTone) + '"' : '') + '>' +
          esc(g.display != null ? g.display : g.value) + '</b>' +
          (g.unit ? '<span class="dial-unit">' + esc(g.unit) + '</span>' : '') +
        '</div>' +
        (g.foot ? '<div class="dial-foot">' + esc(g.foot) + '</div>' : '') +
        (g.note ? '<p class="dial-note">' + esc(g.note) + '</p>' : '') +
      '</div>';
    }).join('');

    return chartShell(b, '<div class="dial-grid">' + cards + '</div>');
  }

  function blockChart(b) {
    if (b.chart === 'line') return lineChart(b);
    if (b.chart === 'bars') return barsChart(b);
    if (b.chart === 'range') return rangeChart(b);
    if (b.chart === 'gauge') return dialGauge(b);
    return '';
  }

  /* ---------------- TradingView ---------------- */
  function loadTvJs() {
    if (window.TradingView && window.TradingView.widget) return Promise.resolve();
    if (tvJsPromise) return tvJsPromise;
    tvJsPromise = new Promise(function (resolve, reject) {
      var s = document.createElement('script');
      s.src = 'https://s3.tradingview.com/tv.js';
      s.async = true;
      s.onload = function () { resolve(); };
      s.onerror = function () { reject(new Error('tv.js')); };
      document.head.appendChild(s);
    });
    return tvJsPromise;
  }

  function mountChart(block, symbol) {
    var wrap = document.getElementById('tv-chart');
    if (!wrap) return;
    if (symbol.indexOf('KRX:') === 0) {
      var code = symbol.split(':')[1];
      wrap.classList.add('ma-mode');
      wrap.innerHTML = '<div class="ma-wrap" id="ma-realtime"><div class="scr-loading">차트를 그리는 중…</div></div>' +
        '<div class="tv-note">국내 종목은 TradingView 무료 임베드가 막혀 있어 사이트가 직접 그립니다. ' +
        '이동평균선은 범례를, 자동 지지·저항·박스권·삼각수렴은 아래 툴바를 눌러 켜고 끌 수 있습니다. · ' +
        '<a href="https://finance.naver.com/item/main.naver?code=' + encodeURIComponent(code) + '" target="_blank" rel="noopener">네이버 금융에서 보기 →</a></div>';
      var host = document.getElementById('ma-realtime');
      loadMaData().then(function (data) {
        var chart = data.charts && data.charts[code];
        if (!chart) {
          host.innerHTML = '<div class="tv-fallback">이 종목의 일봉 데이터가 아직 없습니다. ' +
            '<a href="https://finance.naver.com/item/main.naver?code=' + encodeURIComponent(code) + '" target="_blank" rel="noopener">네이버 금융에서 보기 →</a></div>';
          return;
        }
        mountMaChart(host, chart, { height: window.innerWidth < 760 ? 280 : 420 });
      }).catch(function () {
        host.innerHTML = '<div class="tv-fallback">차트를 불러오지 못했습니다.</div>';
      });
      return;
    }
    wrap.innerHTML = '<div id="tv-inner" style="height:100%;width:100%"></div>';
    loadTvJs().then(function () {
      /* eslint-disable no-new */
      new window.TradingView.widget({
        container_id: 'tv-inner',
        autosize: true,
        symbol: symbol,
        interval: 'D',
        timezone: 'Asia/Seoul',
        theme: 'dark',
        style: '1',
        locale: 'kr',
        toolbar_bg: '#0b0f17',
        enable_publishing: false,
        allow_symbol_change: true,
        withdateranges: true,
        details: false,
        hide_side_toolbar: window.innerWidth < 760,
        save_image: false,
        studies: [
          { id: 'MASimple@tv-basicstudies', inputs: { length: 20 } },
          { id: 'MASimple@tv-basicstudies', inputs: { length: 200 } }
        ],
        support_host: 'https://www.tradingview.com'
      });
    }).catch(function () {
      wrap.innerHTML = '<div class="tv-fallback">차트를 불러오지 못했습니다.<br>네트워크나 광고 차단 설정을 확인해 주세요.<br>' +
        '<a href="https://www.tradingview.com/chart/?symbol=' + encodeURIComponent(symbol) + '" target="_blank" rel="noopener">TradingView에서 직접 보기 →</a></div>';
    });
  }

  function mountTimeline(block) {
    var holder = document.getElementById('tv-news');
    if (!holder) return;
    holder.innerHTML = '';
    var c = document.createElement('div');
    c.className = 'tradingview-widget-container';
    c.style.height = '100%';
    c.innerHTML = '<div class="tradingview-widget-container__widget" style="height:100%;width:100%"></div>';
    var s = document.createElement('script');
    s.async = true;
    s.src = 'https://s3.tradingview.com/external-embedding/embed-widget-timeline.js';
    s.text = JSON.stringify({
      feedMode: 'all_symbols',
      isTransparent: true,
      displayMode: 'regular',
      width: '100%',
      height: block.height || 520,
      colorTheme: 'dark',
      locale: 'kr'
    });
    c.appendChild(s);
    holder.appendChild(c);
    setTimeout(function () {
      if (!holder.querySelector('iframe')) {
        holder.innerHTML = '<div class="tv-fallback">실시간 속보 위젯을 불러오지 못했습니다.<br>' +
          '<a href="https://www.tradingview.com/news/" target="_blank" rel="noopener">TradingView 뉴스에서 보기 →</a></div>';
      }
    }, 9000);
  }

  function tickerFallback(site) {
    var ticker = document.getElementById('ticker');
    if (!ticker || ticker.querySelector('.ticker-fallback')) return;
    var old = ticker.querySelector('.tradingview-widget-container');
    if (old) old.remove();
    var snap = ticker.querySelector('.ticker-snap');
    if (snap) snap.remove();
    var wrap = document.createElement('div');
    wrap.className = 'ticker-fallback';
    wrap.innerHTML = (site.ticker || []).map(function (t) {
      return '<span class="tk"><span class="k">' + esc(t.label) + '</span>' +
        '<span class="v">' + esc(t.value) + '</span>' +
        '<span class="n ' + tone(t.tone) + '">' + esc(t.note) + '</span></span>';
    }).join('');
    ticker.appendChild(wrap);
  }

  function travelSymbols() {
    return [
      { proName: 'FOREXCOM:SPXUSD', title: 'S&P 500' },
      { proName: 'TVC:GOLD', title: '금' },
      { proName: 'FX_IDC:USDKRW', title: '원/달러' },
      { proName: 'BITSTAMP:BTCUSD', title: '비트코인' },
      { proName: 'AMEX:EWY', title: '한국 ETF' },
      { proName: 'NASDAQ:QQQ', title: '나스닥100(QQQ)' },
      { proName: 'NASDAQ:NVDA', title: '엔비디아' },
      { proName: 'NYSE:TSM', title: 'TSMC' },
      { proName: 'NASDAQ:AVGO', title: '브로드컴' }
    ];
  }

  function mountSnap(site) {
    var ticker = document.getElementById('ticker');
    if (!ticker || !site.snapshot || !site.snapshot.length) return;
    if (ticker.querySelector('.ticker-snap')) return;
    var row = document.createElement('div');
    row.className = 'ticker-snap';
    row.innerHTML = '<span class="snap-lead">11/20 종가</span>' + site.snapshot.map(function (t) {
      return '<span class="tk"><span class="k">' + esc(t.label) + '</span>' +
        '<span class="v">' + esc(t.value) + '</span>' +
        '<span class="n ' + tone(t.tone) + '">' + esc(t.note) + '</span></span>';
    }).join('');
    ticker.appendChild(row);
  }

  function mountTicker(site) {
    var ticker = document.getElementById('ticker');
    if (!ticker) return;
    var c = document.createElement('div');
    c.className = 'tradingview-widget-container';
    c.innerHTML = '<div class="tradingview-widget-container__widget"></div>';
    var s = document.createElement('script');
    s.async = true;
    s.src = 'https://s3.tradingview.com/external-embedding/embed-widget-ticker-tape.js';
    s.text = JSON.stringify({
      symbols: travelSymbols(),
      showSymbolLogo: true,
      isTransparent: true,
      displayMode: 'adaptive',
      colorTheme: 'dark',
      locale: 'kr'
    });
    c.appendChild(s);
    ticker.appendChild(c);
    setTimeout(function () {
      if (!ticker.querySelector('iframe')) tickerFallback(site);
    }, 6000);
  }

  /* ---------------- simulator ---------------- */
  var CURRENCY = {
    KRW: { sym: '₩', dec: 0, locale: 'ko-KR' },
    USD: { sym: '$', dec: 2, locale: 'en-US' }
  };

  function money(v, cur) {
    var c = CURRENCY[cur] || CURRENCY.KRW;
    if (!isFinite(v)) return '—';
    var neg = v < 0;
    var s = Math.abs(v).toLocaleString(c.locale, {
      minimumFractionDigits: c.dec, maximumFractionDigits: c.dec
    });
    return (neg ? '-' : '') + c.sym + s;
  }

  function qtyFmt(v) {
    return v.toLocaleString('ko-KR', { maximumFractionDigits: 2 });
  }

  function blockSimulator(b) {
    var d = b.defaults || {};
    var cur = b.currency || 'KRW';
    function field(k, label, val) {
      return '<label class="field"><span>' + esc(label) + '</span>' +
        '<input class="sim-field" type="number" inputmode="decimal" min="0" step="any" data-k="' + k + '" value="' +
        esc(val == null ? '' : val) + '"></label>';
    }
    var tp = (b.presetsTarget || [10, 15, 20, 25]).map(function (v) {
      return '<button class="preset" type="button" data-k="target" data-v="' + v + '">+' + v + '%</button>';
    }).join('');
    var sp = (b.presetsStop || [3, 5, 7]).map(function (v) {
      return '<button class="preset" type="button" data-k="stop" data-v="' + Math.abs(v) + '">-' + Math.abs(v) + '%</button>';
    }).join('');

    return '<div class="block"><div class="sim" data-cur="' + esc(cur) + '">' +
      '<div class="sim-top">' +
        '<div><div class="sim-title">평단가 · 목표가 · 손절가</div>' +
        '<div class="sim-sub">숫자를 바꾸면 결과가 바로 바뀝니다 · 통화 버튼은 표시 단위만 변경</div></div>' +
        '<div class="cur-toggle">' +
          '<button class="cur' + (cur === 'KRW' ? ' on' : '') + '" type="button" data-cur="KRW">₩ 원</button>' +
          '<button class="cur' + (cur === 'USD' ? ' on' : '') + '" type="button" data-cur="USD">$ 달러</button>' +
        '</div>' +
      '</div>' +
      '<div class="sim-fields">' +
        field('avg0', '기존 평단가', d.avg0) +
        field('qty0', '기존 보유 수량', d.qty0) +
        field('price', '추가 매수 단가', d.price) +
        field('qty', '추가 매수 수량', d.qty) +
      '</div>' +
      '<div class="sim-presets">' +
        '<div class="preset-line"><span class="pl">목표 수익률</span>' + tp +
          '<input class="sim-field" type="number" inputmode="decimal" step="1" data-k="target" value="' + esc(d.target || 0) + '"><span class="unit">%</span></div>' +
        '<div class="preset-line"><span class="pl">손절 기준</span>' + sp +
          '<input class="sim-field" type="number" inputmode="decimal" step="1" data-k="stop" value="' + esc(d.stop || 0) + '"><span class="unit">%</span></div>' +
      '</div>' +
      '<div class="sim-out">' +
        '<div class="res primary"><div class="rl">최종 예상 평단가</div><div class="rv" data-out="avg">—</div><div class="rs" data-out="sum">—</div></div>' +
        '<div class="res"><div class="rl">총 매수 금액</div><div class="rv" data-out="cost">—</div><div class="rs" data-out="costd">—</div></div>' +
        '<div class="res"><div class="rl">목표 매도가</div><div class="rv t-pos" data-out="tp">—</div><div class="rs" data-out="tpd">—</div></div>' +
        '<div class="res"><div class="rl">손절 기준가</div><div class="rv t-neg" data-out="sl">—</div><div class="rs" data-out="sld">—</div></div>' +
        '<div class="res"><div class="rl">손익비</div><div class="rv" data-out="rr">—</div><div class="rs" data-out="rrd">—</div></div>' +
      '</div>' +
      '<div class="planbar">' +
        '<div class="pb-bar"><span class="pb-loss" data-bar="loss"></span><span class="pb-gain" data-bar="gain"></span><i class="pb-pin" data-bar="pin" style="left:50%"></i></div>' +
        '<div class="pb-labels"><span>손절<b data-out="sl2">—</b></span>' +
          '<span style="text-align:center">평단<b data-out="avg2">—</b></span>' +
          '<span style="text-align:right">목표<b data-out="tp2">—</b></span></div>' +
      '</div>' +
      (b.note ? '<div class="sim-note">' + esc(b.note) + '</div>' : '') +
    '</div></div>';
  }

  function recalcSim(root) {
    if (!root) return;
    var cur = root.dataset.cur || 'KRW';
    function get(k) {
      var el = root.querySelector('.sim-field[data-k="' + k + '"]');
      var v = el ? parseFloat(el.value) : 0;
      return isFinite(v) ? v : 0;
    }
    var avg0 = Math.max(0, get('avg0')), qty0 = Math.max(0, get('qty0'));
    var price = Math.max(0, get('price')), qty1 = Math.max(0, get('qty'));
    var target = Math.abs(get('target')), stop = Math.abs(get('stop'));

    var totalQty = qty0 + qty1;
    var cost = avg0 * qty0 + price * qty1;
    var avg = totalQty > 0 ? cost / totalQty : 0;
    var tp = avg * (1 + target / 100);
    var sl = avg * (1 - stop / 100);
    var profit = (tp - avg) * totalQty;
    var loss = (sl - avg) * totalQty;
    var rr = stop > 0 ? target / stop : 0;

    function set(k, v) {
      var el = root.querySelector('[data-out="' + k + '"]');
      if (el) el.textContent = v;
    }

    if (totalQty <= 0 || cost <= 0) {
      ['avg', 'sum', 'cost', 'costd', 'tp', 'tpd', 'sl', 'sld', 'rr', 'rrd', 'sl2', 'avg2', 'tp2'].forEach(function (k) { set(k, '—'); });
      return;
    }

    set('avg', money(avg, cur));
    set('sum', '총 ' + qtyFmt(totalQty) + '주 보유');
    set('cost', money(cost, cur));
    set('costd', '기존 ' + money(avg0 * qty0, cur) + ' + 추가 ' + money(price * qty1, cur));
    set('tp', money(tp, cur));
    set('tpd', '+' + target + '% · 수익 ' + money(profit, cur));
    set('sl', money(sl, cur));
    set('sld', '-' + stop + '% · 손실 ' + money(Math.abs(loss), cur));
    set('rr', rr > 0 ? '1 : ' + rr.toFixed(2) : '—');
    set('rrd', rr >= 2 ? '목표 +' + target + '% / 손절 -' + stop + '% · 양호' : '목표 +' + target + '% / 손절 -' + stop + '% · 1:2 미만');
    set('sl2', money(sl, cur));
    set('avg2', money(avg, cur));
    set('tp2', money(tp, cur));

    var lossDist = Math.max(0, avg - sl);
    var gainDist = Math.max(0, tp - avg);
    var span = lossDist + gainDist;
    var leftPct = span > 0 ? lossDist / span * 100 : 50;
    var barLoss = root.querySelector('[data-bar="loss"]');
    var barGain = root.querySelector('[data-bar="gain"]');
    var pin = root.querySelector('[data-bar="pin"]');
    if (barLoss) barLoss.style.width = leftPct.toFixed(2) + '%';
    if (barGain) barGain.style.width = (100 - leftPct).toFixed(2) + '%';
    if (pin) pin.style.left = leftPct.toFixed(2) + '%';

    Array.prototype.forEach.call(root.querySelectorAll('.preset'), function (btn) {
      var k = btn.dataset.k;
      var v = parseFloat(btn.dataset.v);
      var val = k === 'target' ? target : stop;
      btn.classList.toggle('on', Math.abs(val - v) < 0.001);
    });
  }


  /* ---------------- 이동평균 차트 ---------------- */
  var MA_STYLES = [
    { p: 5,   color: '#8f9bb0', width: 1.1, on: true },
    { p: 20,  color: '#66d9ff', width: 1.6, on: true },
    { p: 60,  color: '#4ade80', width: 1.6, on: true },
    { p: 120, color: '#ff4d5e', width: 1.6, on: true },
    { p: 240, color: '#ff9f43', width: 1.6, on: true },
    { p: 360, color: '#f2d024', width: 1.6, on: false },
    { p: 480, color: '#4d7cff', width: 2.6, on: false },
    { p: 720, color: '#e8e8ed', width: 2.6, on: false }
  ];

  function num(v) {
    return v == null ? '—' : Math.round(v).toLocaleString('ko-KR');
  }
  function pct(v, digits) {
    if (v == null) return '—';
    return (v > 0 ? '+' : '') + Number(v).toFixed(digits == null ? 2 : digits) + '%';
  }

  function loadMaData() {
    if (MA_DATA) return Promise.resolve(MA_DATA);
    return fetch('data/ma-charts.json', { cache: 'no-store' })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function (d) { MA_DATA = d; return d; });
  }

  /* ---------------- 자동 지지·저항·박스권·삼각수렴 ---------------- */
  /* 계산은 브라우저에서 차트를 그릴 때 한 번만 하고 결과를 chart 객체에 캐시한다.
     데이터는 이미 받아 둔 일봉 종가(90봉)를 그대로 쓰므로 추가 요청·추가 파일이 없다. */
  var PAT_STATE = { trend: true, levels: true, box: true, tri: true };
  var PAT_TOL_P = 0.0025;   // 접점 허용오차 = max(가격의 0.25%, ATR의 0.28배)
  var PAT_TOL_A = 0.28;
  var PAT_PIV_L = 5;        // 스윙 판정 좌우 봉수
  var PAT_PIV_AMP = 0.8;    // ATR(14) 대비 스윙 변동폭 하한
  var PAT_MIN_SPAN = 8;     // 두 접점 사이 최소 봉수

  function patATR(c, p) {
    var n = c.length, tr = [0], out = [], i, run = 0;
    for (i = 1; i < n; i++) tr.push(Math.abs(c[i] - c[i - 1]));
    out.push(Math.max(c[0] * 0.01, 1));
    for (i = 1; i < n; i++) {
      run += tr[i];
      if (i > p) run -= tr[i - p];
      out[i] = run / Math.min(i, p);
      if (!(out[i] > 0)) out[i] = Math.max(c[i] * 0.01, 1);
    }
    return out;
  }

  function patTol(c, atr, i) {
    return Math.max(PAT_TOL_P * c[i], PAT_TOL_A * atr[i]);
  }

  function patPivots(c, atr) {
    var n = c.length, hi = [], lo = [], i, k, isHi, isLo, wmax, wmin;
    for (i = PAT_PIV_L; i < n - PAT_PIV_L; i++) {
      isHi = true; isLo = true; wmax = -Infinity; wmin = Infinity;
      for (k = i - PAT_PIV_L; k <= i + PAT_PIV_L; k++) {
        if (k !== i) {
          if (c[k] >= c[i]) isHi = false;
          if (c[k] <= c[i]) isLo = false;
        }
        if (c[k] > wmax) wmax = c[k];
        if (c[k] < wmin) wmin = c[k];
      }
      if (isHi && c[i] - wmin >= PAT_PIV_AMP * atr[i]) hi.push({ i: i, p: c[i] });
      if (isLo && wmax - c[i] >= PAT_PIV_AMP * atr[i]) lo.push({ i: i, p: c[i] });
    }
    return { hi: hi, lo: lo };
  }

  /* 선 하나를 구간 전체에 걸쳐 채점: 접점 수·마지막 접점·이탈 봉수 */
  function patEval(c, atr, m, b, from, kind) {
    var touches = 0, last = -1, first = -1, viol = 0, t, dev, tl;
    for (t = from; t < c.length; t++) {
      dev = c[t] - (m * t + b);
      tl = patTol(c, atr, t);
      if (Math.abs(dev) <= tl) {
        touches++; last = t; if (first < 0) first = t;
      } else if ((kind === 'support' && dev < -tl) || (kind === 'resistance' && dev > tl)) {
        viol++;
      }
    }
    return { touch: touches, last: last, first: first, viol: viol };
  }

  function patLines(c, atr, piv) {
    var n = c.length, out = { support: [], resistance: [] };
    [['support', piv.lo, 1], ['resistance', piv.hi, -1]].forEach(function (cfg) {
      var kind = cfg[0], pts = cfg[1], sign = cfg[2], list = [], a, b, pa, pb, span, m, b0, st, cur, tl, broken, score;
      for (a = 0; a < pts.length; a++) {
        for (b = a + 1; b < pts.length; b++) {
          pa = pts[a]; pb = pts[b];
          span = pb.i - pa.i;
          if (span < PAT_MIN_SPAN) continue;
          m = (pb.p - pa.p) / span;
          if (sign * m <= 0) continue;
          if (Math.abs(m) * (n - 1 - pa.i) > 0.85 * c[n - 1]) continue;
          b0 = pa.p - m * pa.i;
          st = patEval(c, atr, m, b0, pa.i, kind);
          if (st.touch < 3) continue;
          if (st.viol > Math.max(3, 0.12 * (n - pa.i))) continue;
          cur = m * (n - 1) + b0;
          tl = patTol(c, atr, n - 1);
          broken = (kind === 'support' ? c[n - 1] < cur - tl : c[n - 1] > cur + tl);
          if (broken) continue;
          score = st.touch - st.viol * 1.2 + (st.last / n) * 2 + (span / n) * 1.5;
          list.push({ kind: kind, m: m, b: b0, from: pa.i, to: n - 1, touch: st.touch,
                      last: st.last, first: st.first, viol: st.viol, score: score,
                      startP: pa.p, endP: pb.p });
        }
      }
      list.sort(function (x, y) { return y.score - x.score; });
      out[kind] = list;
    });
    return out;
  }

  /* 수평 지지·저항: 스윙 고저점 가격이 1.2% 안에서 겹치는 자리(터치 3회 이상)를 후보로,
     현재가 아래/위에서 가장 많이 반응한 1개씩만 남긴다. */
  function patLevels(c, atr, piv) {
    var n = c.length, last = c[n - 1], pts = [], clusters = [], i, t, cl, lvl, touches, cand = [];
    piv.hi.concat(piv.lo).forEach(function (p) { pts.push(p.p); });
    pts.sort(function (a, b) { return a - b; });
    pts.forEach(function (p) {
      if (clusters.length && p / clusters[clusters.length - 1][clusters[clusters.length - 1].length - 1] - 1 <= 0.012) {
        clusters[clusters.length - 1].push(p);
      } else clusters.push([p]);
    });
    clusters.forEach(function (cl2) {
      if (cl2.length < 2) return;
      lvl = cl2.reduce(function (s, v) { return s + v; }, 0) / cl2.length;
      if (Math.abs(lvl / last - 1) > 0.18) return;
      touches = 0;
      for (t = 0; t < n; t++) if (Math.abs(c[t] - lvl) <= Math.max(0.004 * lvl, 0.4 * atr[t])) touches++;
      if (touches < 3) return;
      cand.push({ price: lvl, touch: touches, n: cl2.length });
    });
    cand.sort(function (a, b) { return (b.touch + b.n * 0.5) - (a.touch + a.n * 0.5); });
    var sup = [], res = [];
    cand.forEach(function (lv) {
      var side = lv.price > last ? res : sup;
      if (side.length >= 1) return;
      if (side.length && Math.abs(lv.price / side[0].price - 1) < 0.012) return;
      lv.kind = lv.price > last ? 'resistance' : 'support';
      side.push(lv);
    });
    return sup.concat(res);
  }

  /* 박스권: 최근 30·40·50·60봉의 (최고−최저)/현재가 폭이 그 직전 같은 길이 구간 폭의 0.7배
     미만이고, 상단·하단을 0.5 ATR 안에서 각 2회 이상 터치한 구간을 박스로 본다. */
  function patBox(c, atr) {
    var n = c.length, last = c[n - 1], best = null, ws = [60, 50, 40, 30];
    ws.forEach(function (w) {
      var i0 = n - w, hi, lo, width, bs, baseSeg, base, up, dn, ratio, score, t;
      if (i0 < 20) return;
      hi = -Infinity; lo = Infinity;
      for (t = i0; t < n; t++) { if (c[t] > hi) hi = c[t]; if (c[t] < lo) lo = c[t]; }
      width = (hi - lo) / last;
      bs = Math.max(0, i0 - w);
      baseSeg = c.slice(bs, i0);
      if (baseSeg.length < 20) return;
      base = (Math.max.apply(null, baseSeg) - Math.min.apply(null, baseSeg)) / last;
      if (base <= 0) return;
      up = 0; dn = 0;
      for (t = i0; t < n; t++) {
        if (c[t] >= hi - 0.5 * atr[t]) up++;
        if (c[t] <= lo + 0.5 * atr[t]) dn++;
      }
      if (up < 2 || dn < 2) return;
      ratio = width / base;
      if (ratio > 0.7 || width > 0.2) return;
      score = (1 - ratio) * 2 + Math.min(up, 12) * 0.05 + Math.min(dn, 12) * 0.05 - w / 300;
      if (!best || score > best.score) {
        best = { top: hi, bottom: lo, from: i0, to: n - 1, width: width, ratio: ratio,
                 touchTop: up, touchBot: dn, bars: w, score: score,
                 breakout: last > hi + 0.3 * atr[n - 1], breakdown: last < lo - 0.3 * atr[n - 1] };
      }
    });
    return best;
  }

  /* 삼각수렴: 저점 상승선(양의 기울기)과 고점 하락선(음의 기울기), 또는 한쪽이 수평 지지·저항인
     조합이 오른쪽에서 교차하고(꼭짓점이 최근 60봉~앞으로 20봉), 현재 폭이 시작 폭의 0.72배 미만으로
     좁아지며, 두 변 모두 2회 이상 터치한 경우만 채택. 현재가가 두 변 사이에 있어야 한다. */
  function patTriangle(c, atr, lines, levels) {
    var n = c.length, sups = lines.support.slice(), ress = lines.resistance.slice(), best = null, tlc = patTol(c, atr, n - 1);
    levels.forEach(function (lv) {
      var side = lv.kind === 'support' ? sups : ress;
      side.push({ kind: lv.kind, m: 0, b: lv.price, from: 0, touch: lv.touch, last: n - 1, viol: 0,
                  flat: true, startP: lv.price, endP: lv.price });
    });
    sups.slice(0, 14).forEach(function (s) {
      ress.slice(0, 14).forEach(function (r) {
        if (s.flat && r.flat) return;
        var den = s.m - r.m, x, st, w0, w1, shrink, loNow, hiNow, score;
        if (Math.abs(den) < 1e-9) return;
        x = (r.b - s.b) / den;
        if (x < n - 60 || x > n - 1 + 20) return;
        if (x <= Math.max(s.last, r.last)) return;
        st = Math.max(0, Math.min(s.last, r.last));
        w0 = (r.m * st + r.b) - (s.m * st + s.b);
        w1 = (r.m * (n - 1) + r.b) - (s.m * (n - 1) + s.b);
        if (w0 <= 0 || w1 <= 0 || w1 / w0 > 0.72) return;
        if (Math.min(s.touch, r.touch) < 2) return;
        if (Math.max(s.last, r.last) < n - 25) return;
        loNow = s.m * (n - 1) + s.b; hiNow = r.m * (n - 1) + r.b;
        if (c[n - 1] < loNow - 0.5 * tlc || c[n - 1] > hiNow + 0.5 * tlc) return;
        if ((hiNow - loNow) / c[n - 1] > 0.22) return;
        shrink = 1 - w1 / w0;
        score = Math.min(s.touch, 10) + Math.min(r.touch, 10) + shrink * 4 -
          Math.max(0, x - (n - 1)) * 0.15 - (s.viol + r.viol) * 0.4;
        if (!best || score > best.score) {
          best = { mS: s.m, bS: s.b, mR: r.m, bR: r.b, apex: x, from: st, shrink: shrink,
                   touchS: s.touch, touchR: r.touch, score: score,
                   flatS: !!s.flat, flatR: !!r.flat, widthEnd: w1 / c[n - 1],
                   name: s.flat ? '하락삼각형' : (r.flat ? '상승삼각형' : '대칭삼각형') };
        }
      });
    });
    return best;
  }

  function patAnalyze(chart) {
    if (chart.__pat !== undefined) return chart.__pat;
    var c = chart.c || [];
    if (c.length < 30) { chart.__pat = null; return null; }
    var atr = patATR(c, 14), piv = patPivots(c, atr), lines = patLines(c, atr, piv), levels = patLevels(c, atr, piv);
    chart.__pat = { atr: atr, lines: lines, levels: levels, box: patBox(c, atr), tri: patTriangle(c, atr, lines, levels) };
    return chart.__pat;
  }

  /* 꼭짓점 날짜: 마지막 봉 이후면 주말을 건너뛰어 예상 거래일로 환산 */
  function patApexDate(chart, x) {
    var d = chart.d || [], n = d.length;
    if (!n) return '';
    if (x <= n - 1) return d[Math.max(0, Math.round(x))] || '';
    var base = (chart.baseDate || '').split('-');
    var yr = base.length === 3 ? +base[0] : 2026;
    var parts = String(d[n - 1]).split('/');
    var mo = +parts[0], day = +parts[1];
    var steps = Math.round(x) - (n - 1), guard = 0;
    while (steps > 0 && guard++ < 60) {
      var dt = new Date(Date.UTC(yr, mo - 1, day));
      dt.setUTCDate(dt.getUTCDate() + 1);
      yr = dt.getUTCFullYear(); mo = dt.getUTCMonth() + 1; day = dt.getUTCDate();
      var wd = dt.getUTCDay();
      if (wd !== 0 && wd !== 6) steps--;
    }
    return (mo < 10 ? '0' : '') + mo + '/' + (day < 10 ? '0' : '') + day;
  }

  function patBarHTML() {
    var items = [['trend', '추세선'], ['levels', '지지·저항'], ['box', '박스권'], ['tri', '삼각수렴']];
    return items.map(function (it) {
      var on = PAT_STATE[it[0]];
      return '<button type="button" class="pat-t' + (on ? ' on' : '') + '" data-k="' + it[0] +
        '" aria-pressed="' + (on ? 'true' : 'false') + '">' + it[1] + '<b></b></button>';
    }).join('') +
      '<button type="button" class="pat-q" title="계산 기준 보기" aria-label="자동 지지선 계산 기준">?</button>' +
      '<div class="pat-info" hidden>' +
        '<b>자동으로 그리는 선의 기준</b>' +
        '<ul>' +
        '<li><em>스윙</em> 좌우 5봉 안에서 최고·최저인 지점(변동폭이 ATR(14)의 0.8배 이상인 것만).</li>' +
        '<li><em>추세선</em> 스윙 저점 2개를 잇는 상승 지지선, 고점 2개를 잇는 하락 저항선 중 ' +
        '접점 3회 이상·이탈이 적은 1개씩. 종가가 선을 뚫고 있으면 그리지 않습니다.</li>' +
        '<li><em>지지·저항</em> 스윙 가격이 1.2% 안에서 겹치고 3회 이상 반응한 가격대. 현재가 아래는 지지, 위는 저항.</li>' +
        '<li><em>박스권</em> 최근 30~60봉의 (최고−최저)/현재가 폭이 직전 같은 길이 구간 폭의 0.7배 미만이고 ' +
        '상·하단을 0.5 ATR 안에서 각 2회 이상 터치한 구간.</li>' +
        '<li><em>삼각수렴</em> 저점 상승선과 고점 하락선(또는 한쪽 수평선)이 오른쪽에서 교차하고, 현재 폭이 시작 폭의 ' +
        '72% 미만으로 좁아지며, 꼭짓점이 최근 60봉~앞으로 20봉 안인 경우.</li>' +
        '</ul></div>';
  }

  /* 화면에 그리기: 뒤(음영) → 주가선 → 앞(선·라벨) 순서로 조각을 돌려준다 */
  function patDraw(chart, pat, geo) {
    var c = chart.c, n = c.length, x = geo.x, y = geo.y, W = geo.W, pad = geo.pad;
    var narrow = geo.W < 560;
    var back = '', front = '', labels = [], counts = { trend: 0, levels: 0, box: 0, tri: 0 };
    var COL_S = '#2ee6c8', COL_R = '#ff8368', COL_BOX = '#a78bfa', COL_TRI = '#c4b5fd';

    function label(py, text, color, pr) {
      labels.push({ y: py, text: text, color: color, pr: pr == null ? 5 : pr });
    }
    function txt(px, py, s, color, size, anchor) {
      return '<text x="' + px.toFixed(1) + '" y="' + (py + 3.6).toFixed(1) + '" text-anchor="' + (anchor || 'end') +
        '" font-size="' + (size || 10) + '" font-weight="700" fill="' + color +
        '" stroke="#080a0f" stroke-width="2.6" paint-order="stroke" style="pointer-events:none">' + esc(s) + '</text>';
    }
    function fmtPx(v) { return num(v); }

    if (PAT_STATE.box && pat.box) {
      var bx = pat.box, x0 = x(bx.from), x1 = x(n - 1);
      var yt = y(bx.top), yb = y(bx.bottom);
      var upCol = bx.breakout ? '#f0abfc' : COL_BOX;
      back += '<rect x="' + x0.toFixed(1) + '" y="' + yt.toFixed(1) + '" width="' + Math.max(1, x1 - x0).toFixed(1) +
        '" height="' + Math.max(1, yb - yt).toFixed(1) + '" fill="rgba(167,139,250,.09)" stroke="none"/>' +
        '<line x1="' + x0.toFixed(1) + '" y1="' + yt.toFixed(1) + '" x2="' + x1.toFixed(1) + '" y2="' + yt.toFixed(1) +
        '" stroke="' + upCol + '" stroke-width="1.4" stroke-dasharray="5 3" opacity=".95"/>' +
        '<line x1="' + x0.toFixed(1) + '" y1="' + yb.toFixed(1) + '" x2="' + x1.toFixed(1) + '" y2="' + yb.toFixed(1) +
        '" stroke="' + COL_BOX + '" stroke-width="1.4" stroke-dasharray="5 3" opacity=".8"/>';
      label(yt, (bx.breakout ? '박스 상단 돌파 ' : '박스 상단 ') + fmtPx(bx.top), upCol, 4);
      label(yb, '박스 하단 ' + fmtPx(bx.bottom), COL_BOX, 3);
      counts.box = 1;
    }

    if (PAT_STATE.tri && pat.tri) {
      var tr = pat.tri;
      var xs0 = x(tr.from), xs1 = x(Math.min(n - 1, tr.apex));
      var s0 = tr.mS * tr.from + tr.bS, s1 = tr.mS * Math.min(n - 1, tr.apex) + tr.bS;
      var r0 = tr.mR * tr.from + tr.bR, r1 = tr.mR * Math.min(n - 1, tr.apex) + tr.bR;
      back += '<path d="M' + xs0.toFixed(1) + ' ' + y(s0).toFixed(1) + ' L' + xs1.toFixed(1) + ' ' + y(s1).toFixed(1) +
        ' L' + xs1.toFixed(1) + ' ' + y(r1).toFixed(1) + ' L' + xs0.toFixed(1) + ' ' + y(r0).toFixed(1) +
        ' Z" fill="rgba(196,181,253,.10)" stroke="none"/>';
      front += '<line x1="' + xs0.toFixed(1) + '" y1="' + y(s0).toFixed(1) + '" x2="' + x(n - 1).toFixed(1) + '" y2="' +
        y(tr.mS * (n - 1) + tr.bS).toFixed(1) + '" stroke="' + COL_S + '" stroke-width="1.5" stroke-dasharray="2 3"/>' +
        '<line x1="' + xs0.toFixed(1) + '" y1="' + y(r0).toFixed(1) + '" x2="' + x(n - 1).toFixed(1) + '" y2="' +
        y(tr.mR * (n - 1) + tr.bR).toFixed(1) + '" stroke="' + COL_R + '" stroke-width="1.5" stroke-dasharray="2 3"/>';
      var ax = Math.min(n - 1, tr.apex), ay = tr.mS * tr.apex + tr.bS;
      if (tr.apex > n - 1) {
        front += '<circle cx="' + x(n - 1).toFixed(1) + '" cy="' + y(tr.mS * (n - 1) + tr.bS).toFixed(1) +
          '" r="2.6" fill="' + COL_TRI + '"/>';
      } else {
        front += '<path d="M' + x(ax).toFixed(1) + ' ' + (y(ay) - 4.2).toFixed(1) + ' l4.2 4.2 -4.2 4.2 -4.2 -4.2 z" fill="' +
          COL_TRI + '" stroke="#080a0f" stroke-width="1"/>';
      }
      var tdate = patApexDate(chart, tr.apex);
      var ttext = tr.name + ' · 꼭짓점 ' + tdate + (tr.apex > n - 1 ? ' 예상' : '');
      front += txt(W - pad.r - 2, y(tr.apex > n - 1 ? tr.mS * (n - 1) + tr.bS : ay) - 9, ttext, COL_TRI, narrow ? 9 : 10, 'end');
      counts.tri = 1;
    }

    if (PAT_STATE.trend) {
      ['support', 'resistance'].forEach(function (k) {
        var ln = pat.lines[k] && pat.lines[k][0];
        if (!ln) return;
        var col = k === 'support' ? COL_S : COL_R;
        front += '<line x1="' + x(ln.from).toFixed(1) + '" y1="' + y(ln.m * ln.from + ln.b).toFixed(1) +
          '" x2="' + x(n - 1).toFixed(1) + '" y2="' + y(ln.m * (n - 1) + ln.b).toFixed(1) +
          '" stroke="' + col + '" stroke-width="1.7" stroke-linecap="round"/>';
        label(y(ln.m * (n - 1) + ln.b), (k === 'support' ? '지지 ' : '저항 ') + fmtPx(ln.m * (n - 1) + ln.b), col, 6);
        counts.trend++;
      });
    }

    if (PAT_STATE.levels) {
      pat.levels.forEach(function (lv) {
        var col = lv.kind === 'support' ? COL_S : COL_R;
        front += '<line x1="' + x(0).toFixed(1) + '" y1="' + y(lv.price).toFixed(1) + '" x2="' + x(n - 1).toFixed(1) +
          '" y2="' + y(lv.price).toFixed(1) + '" stroke="' + col + '" stroke-width="1" stroke-dasharray="6 4" opacity=".55"/>';
        label(y(lv.price), (lv.kind === 'support' ? '지지 ' : '저항 ') + fmtPx(lv.price), col, 2);
        counts.levels++;
      });
    }

    /* 라벨 겹침: 우선순위(추세선 > 박스 > 삼각 > 수평) 순으로 자리를 잡고, 겹치면 생략 */
    labels.sort(function (a, b) { return b.pr - a.pr; });
    var kept = [], top = pad.t + 2, bot = geo.H - pad.b - 2;
    labels.forEach(function (L) {
      if (narrow && kept.length >= 3) return;
      if (L.y < top || L.y > bot) return;
      for (var i = 0; i < kept.length; i++) if (Math.abs(kept[i] - L.y) < 11) return;
      kept.push(L.y);
      front += txt(W - pad.r - 2, L.y, L.text, L.color, narrow ? 9 : 10, 'end');
    });

    return { back: back, front: front, counts: counts };
  }

  /* 차트 한 개를 host 안에 그림(범례 클릭으로 선 토글) */
  function mountMaChart(host, chart, opts) {
    opts = opts || {};
    var state = {};
    MA_STYLES.forEach(function (m) { state[m.p] = m.on; });
    host.innerHTML = '<div class="ma-legend" data-leg></div>' +
      '<div class="pat-bar" data-pat aria-label="자동 지지·저항·박스권·삼각수렴 표시">' + patBarHTML() + '</div>' +
      '<div class="ma-canvas" data-cv></div>';
    var w = { chart: chart, state: state, leg: host.querySelector('[data-leg]'),
              patBar: host.querySelector('[data-pat]'),
              cv: host.querySelector('[data-cv]'), opts: opts };
    maWidgets.push(w);
    drawMaChart(w);
    w.leg.addEventListener('click', function (e) {
      var b = e.target.closest('[data-p]');
      if (!b || b.classList.contains('nodata')) return;
      var k = b.getAttribute('data-p');
      state[k] = !state[k];
      drawMaChart(w);
    });
    w.patBar.addEventListener('click', function (e) {
      var q = e.target.closest('.pat-q');
      if (q) {
        var info = w.patBar.querySelector('.pat-info');
        if (info) info.hidden = !info.hidden;
        return;
      }
      var t = e.target.closest('.pat-t');
      if (!t) return;
      var k2 = t.getAttribute('data-k');
      PAT_STATE[k2] = !PAT_STATE[k2];
      t.classList.toggle('on', PAT_STATE[k2]);
      t.setAttribute('aria-pressed', PAT_STATE[k2] ? 'true' : 'false');
      redrawMaCharts();
    });
    return w;
  }

  function maLinePath(vals, x, y) {
    var d = '', open = false;
    for (var i = 0; i < vals.length; i++) {
      var v = vals[i];
      if (v == null) { open = false; continue; }
      d += (open ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(v).toFixed(1) + ' ';
      open = true;
    }
    return d;
  }

  function drawMaChart(w) {
    var chart = w.chart, closes = chart.c || [], n = closes.length;
    if (!n) { w.cv.innerHTML = '<div class="tv-fallback">차트 데이터가 없습니다.</div>'; return; }

    var W = Math.max(300, w.cv.clientWidth || w.opts.width || 640);
    var H = w.opts.height || (window.innerWidth < 760 ? 250 : 320);
    var pad = { l: 54, r: 14, t: 14, b: 24 };

    var pat = patAnalyze(chart);
    var shown = MA_STYLES.filter(function (m) {
      return w.state[m.p] && chart.ma && chart.ma[String(m.p)];
    });
    var vals = closes.slice();
    shown.forEach(function (m) {
      chart.ma[String(m.p)].forEach(function (v) { if (v != null) vals.push(v); });
    });
    /* 자동 추세선·박스·삼각형이 화면 밖으로 나가지 않도록 그 값도 세로축 범위에 넣는다 */
    if (pat) {
      if (PAT_STATE.trend) {
        ['support', 'resistance'].forEach(function (k) {
          var ln = pat.lines[k] && pat.lines[k][0];
          if (!ln) return;
          vals.push(ln.m * ln.from + ln.b, ln.m * (n - 1) + ln.b);
        });
      }
      if (PAT_STATE.levels) pat.levels.forEach(function (lv) { vals.push(lv.price); });
      if (PAT_STATE.box && pat.box) vals.push(pat.box.top, pat.box.bottom);
      if (PAT_STATE.tri && pat.tri) {
        vals.push(pat.tri.mS * (n - 1) + pat.tri.bS, pat.tri.mR * (n - 1) + pat.tri.bR);
      }
    }
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    var span = hi - lo || hi * 0.04 || 1;
    lo -= span * 0.04; hi += span * 0.04;

    var x = function (i) { return pad.l + i * (W - pad.l - pad.r) / Math.max(1, n - 1); };
    var y = function (v) { return pad.t + (hi - v) / (hi - lo) * (H - pad.t - pad.b); };

    var grid = '';
    for (var k = 0; k <= 4; k++) {
      var gv = lo + (hi - lo) * k / 4, gy = y(gv);
      grid += '<line x1="' + pad.l + '" y1="' + gy.toFixed(1) + '" x2="' + (W - pad.r) +
        '" y2="' + gy.toFixed(1) + '" stroke="rgba(255,255,255,.06)"/>' +
        '<text x="' + (pad.l - 8) + '" y="' + (gy + 3.5).toFixed(1) +
        '" text-anchor="end" font-size="10" fill="#67738a">' + num(gv) + '</text>';
    }

    var line = maLinePath(closes, x, y);
    var area = 'M' + x(0).toFixed(1) + ' ' + y(closes[0]).toFixed(1) + ' ';
    for (var i = 1; i < n; i++) area += 'L' + x(i).toFixed(1) + ' ' + y(closes[i]).toFixed(1) + ' ';
    area += 'L' + x(n - 1).toFixed(1) + ' ' + (H - pad.b) + ' L' + x(0).toFixed(1) + ' ' + (H - pad.b) + ' Z';

    var lines = shown.map(function (m) {
      return '<path d="' + maLinePath(chart.ma[String(m.p)], x, y) + '" fill="none" stroke="' +
        m.color + '" stroke-width="' + m.width + '" stroke-linejoin="round" stroke-linecap="round"/>';
    }).join('');

    var xlab = [0, Math.floor((n - 1) / 2), n - 1].map(function (i) {
      return '<text x="' + x(i).toFixed(1) + '" y="' + (H - 7) + '" text-anchor="' +
        (i === 0 ? 'start' : (i === n - 1 ? 'end' : 'middle')) +
        '" font-size="10" fill="#67738a">' + esc((chart.d || [])[i] || '') + '</text>';
    }).join('');

    var lastDot = '<circle cx="' + x(n - 1).toFixed(1) + '" cy="' + y(closes[n - 1]).toFixed(1) +
      '" r="3" fill="#e9eef7"/>';

    var geo = { x: x, y: y, W: W, H: H, pad: pad };
    var patSvg = pat ? patDraw(chart, pat, geo) : { back: '', front: '', counts: { trend: 0, levels: 0, box: 0, tri: 0 } };

    w.cv.innerHTML = '<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" height="' + H +
      '" preserveAspectRatio="none" role="img" aria-label="주가·이동평균선·자동 지지저항·박스권·삼각수렴">' + grid +
      patSvg.back +
      '<path d="' + area + '" fill="rgba(102,217,255,.07)"/>' +
      '<path d="' + line + '" fill="none" stroke="#e9eef7" stroke-width="1.5" stroke-linejoin="round"/>' +
      lines + patSvg.front + lastDot + xlab + '</svg>';

    Array.prototype.forEach.call(w.patBar.querySelectorAll('.pat-t'), function (b) {
      var c2 = patSvg.counts[b.getAttribute('data-k')] || 0;
      var on = PAT_STATE[b.getAttribute('data-k')];
      var el = b.querySelector('b');
      if (el) el.textContent = on && c2 ? c2 : '';
      b.classList.toggle('empty', on && !c2);
    });

    w.leg.innerHTML = MA_STYLES.map(function (m) {
      var arr = chart.ma && chart.ma[String(m.p)];
      var last = null;
      if (arr) for (var i = arr.length - 1; i >= 0; i--) { if (arr[i] != null) { last = arr[i]; break; } }
      var on = w.state[m.p] && !!arr;
      var cls = 'ma-l' + (on ? ' on' : '') + (arr ? '' : ' nodata');
      return '<button type="button" class="' + cls + '" data-p="' + m.p + '"' +
        ' aria-pressed="' + (on ? 'true' : 'false') + '" style="--c:' + m.color + '">' +
        '<i style="background:' + m.color + ';height:' + Math.max(2, Math.min(4, m.width)) + 'px"></i>' +
        '<span>MA' + m.p + '</span><b>' + (arr ? num(last) : '데이터 없음') + '</b></button>';
    }).join('');
  }

  function redrawMaCharts() {
    maWidgets = maWidgets.filter(function (w) { return document.body.contains(w.cv); });
    maWidgets.forEach(drawMaChart);
  }

  /* ---------------- 조건검색 탭 ---------------- */
  function loadScreener() {
    if (SCR) return Promise.resolve(SCR);
    return fetch('data/screener.json', { cache: 'no-store' })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function (d) { SCR = d; return d; });
  }
  function loadHistory() {
    if (SCR_HIST) return Promise.resolve(SCR_HIST);
    return fetch('data/screener-history.json', { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : { days: [], stats: {} }; })
      .then(function (d) { SCR_HIST = d; return d; });
  }

  function blockScreener() {
    return '<div class="block"><div class="scr">' +
      '<div class="scr-top" id="scr-top"><span class="scr-loading">조건검색 결과를 불러오는 중…</span></div>' +
      '<div class="scr-chips" id="scr-chips"></div>' +
      '<div class="scr-rules" id="scr-rules"></div>' +
      '<div id="scr-table"></div>' +
      '<div id="scr-perf"></div>' +
      '</div></div>';
  }

  var scrActive = null;

  function scrSignalCell(it) {
    return '<span class="scr-badge">' + esc(it.signal) + '</span>';
  }

  function scrSupplyText(it) {
    var out = [];
    out.push('<span class="' + (it.foreign3d >= 3 ? 't-pos' : '') + '">외 ' + it.foreign3d + '일</span>');
    out.push('<span class="' + (it.inst3d >= 3 ? 't-pos' : '') + '">기 ' + it.inst3d + '일</span>');
    return '<span class="cell-main scr-supply">' + out.join(' · ') + '</span>';
  }

  function renderScrTable(p) {
    var box = document.getElementById('scr-table');
    if (!box) return;
    if (!p.items.length) {
      box.innerHTML = '<div class="table-card"><div class="table-head"><div class="table-cap">' +
        esc(p.name) + ' · 조건에 맞는 종목 0개</div><div class="table-sub">' +
        esc(p.desc) + '</div></div><div class="table-note">오늘은 이 조건을 통과한 종목이 없습니다. ' +
        '장 전체가 밀린 날에는 돌파 조건이 비는 게 정상입니다.</div></div>';
      return;
    }
    var head = '<tr><th scope="col">종목</th><th scope="col">종가</th><th scope="col">등락률</th>' +
      '<th scope="col">거래량배수</th><th scope="col">20일선 이격</th><th scope="col">52주 고점</th>' +
      '<th scope="col">외인/기관 3일</th><th scope="col">신호</th></tr>';
    var body = p.items.map(function (it) {
      return '<tr>' +
        '<th scope="row"><button type="button" class="scr-name" data-code="' + esc(it.code) + '">' +
          esc(it.name) + '</button><span class="cell-sub">' + esc(it.code) + ' · ' + esc(it.market) + '</span></th>' +
        '<td><span class="cell-main cell-strong">' + num(it.close) + '</span></td>' +
        '<td><span class="cell-main ' + (it.chgPct > 0 ? 't-pos' : (it.chgPct < 0 ? 't-neg' : '')) + '">' +
          pct(it.chgPct) + '</span></td>' +
        '<td><span class="cell-main">' + (it.volMult ? it.volMult.toFixed(1) + '배' : '—') + '</span></td>' +
        '<td><span class="cell-main">' + pct(it.ma20Gap) + '</span></td>' +
        '<td><span class="cell-main ' + (it.from52wHigh > -3 ? 't-pos' : '') + '">' + pct(it.from52wHigh, 1) + '</span></td>' +
        '<td>' + scrSupplyText(it) + '</td>' +
        '<td>' + scrSignalCell(it) + '</td>' +
      '</tr>';
    }).join('');
    box.innerHTML = '<div class="table-card">' +
      '<div class="table-head"><div class="table-cap">' + esc(p.name) + ' · ' + p.count + '종목</div>' +
      '<div class="table-sub">' + esc(p.desc) + '</div></div>' +
      '<div class="table-hint">← 표를 옆으로 넘겨서 보세요 · 종목명을 누르면 차트</div>' +
      '<div class="table-wrap pin" style="--mw:840px"><table class="scr-table">' +
      '<thead>' + head + '</thead><tbody>' + body + '</tbody></table></div>' +
      '<div class="table-note">종목명을 누르면 일봉 차트에 5·20·60·120·240·360·480·720일선이 함께 뜹니다. ' +
      '수치는 ' + esc(SCR.baseDate) + ' 장마감 확정 데이터(수정주가) 기준입니다.</div></div>';
  }

  function renderScrHeader() {
    var top = document.getElementById('scr-top');
    var chips = document.getElementById('scr-chips');
    var rules = document.getElementById('scr-rules');
    if (!top || !chips || !rules) return;
    var upd = (SCR.updated || '').replace('T', ' ').slice(0, 16);
    top.innerHTML = '<div class="scr-meta">' +
      '<span class="scr-k">기준일</span><b>' + esc(SCR.baseDate) + '</b>' +
      '<span class="scr-k">유니버스</span><b>' + num(SCR.universe) + '종목</b>' +
      '<span class="scr-k">갱신</span><b>' + esc(upd) + '</b>' +
      '<span class="scr-k">과열 제외</span><b>' + num(SCR.hotFiltered) + '종목</b>' +
      '</div><div class="scr-src">' + esc(SCR.source) + ' · ' +
      '시총 500억·거래대금 100억 미만 제외 · 20일선 이격 +15% 초과 제외</div>';
    chips.innerHTML = SCR.presets.map(function (p) {
      return '<button type="button" class="scr-chip' + (p.id === scrActive ? ' on' : '') +
        '" data-pid="' + esc(p.id) + '">' + esc(p.name) + '<b>' + p.count + '</b></button>';
    }).join('');
    var cur = SCR.presets.filter(function (p) { return p.id === scrActive; })[0];
    if (cur) {
      rules.innerHTML = '<div class="scr-ruleset"><ul>' + cur.rules.map(function (r) {
        return '<li>' + esc(r) + '</li>';
      }).join('') + '</ul></div>';
    }
  }

  function scrStatCell(st, key) {
    var done = st['done' + key], avg = st['avg' + key], win = st['win' + key];
    if (!done) return '<span class="scr-pending">집계 중</span>';
    return '<span class="cell-main">' + pct(avg) + '</span>' +
      '<span class="cell-sub">승률 ' + win + '% · n=' + done + '</span>';
  }

  function renderScrPerf(hist) {
    var box = document.getElementById('scr-perf');
    if (!box) return;
    var stats = hist.stats || {};
    var rows = SCR.presets.map(function (p) {
      var st = stats[p.id] || {};
      return '<tr><th scope="row"><span class="cell-strong">' + esc(p.name) + '</span>' +
        '<span class="cell-sub">' + (st.hits != null ? st.hits + '건 누적' : '—') + '</span></th>' +
        '<td>' + scrStatCell(st, '1') + '</td><td>' + scrStatCell(st, '5') + '</td>' +
        '<td>' + scrStatCell(st, '10') + '</td><td>' + scrStatCell(st, '20') + '</td></tr>';
    }).join('');

    var days = (hist.days || []).slice().sort(function (a, b) { return a.date < b.date ? 1 : -1; });
    var recent = [];
    days.slice(0, 12).forEach(function (d) {
      SCR.presets.forEach(function (p) {
        (d.presets[p.id] || []).slice(0, 4).forEach(function (h) {
          recent.push({ date: d.date, preset: p.name, h: h, seeded: d.seeded });
        });
      });
    });
    recent.sort(function (a, b) { return a.date < b.date ? 1 : -1; });
    recent = recent.slice(0, 40);

    var recentRows = recent.map(function (r) {
      var h = r.h;
      function cell(v) {
        return v == null ? '<span class="scr-pending">집계 중</span>'
          : '<span class="cell-main ' + (v > 0 ? 't-pos' : (v < 0 ? 't-neg' : '')) + '">' + pct(v) + '</span>';
      }
      return '<tr><th scope="row"><span class="cell-strong">' + esc(r.h.n) + '</span>' +
        '<span class="cell-sub">' + esc(r.date) + ' · ' + esc(r.preset) + (r.seeded ? ' · 소급' : '') + '</span></th>' +
        '<td><span class="cell-main">' + num(h.e) + '</span></td>' +
        '<td>' + cell(h.r1) + '</td><td>' + cell(h.r5) + '</td>' +
        '<td>' + cell(h.r10) + '</td><td>' + cell(h.r20) + '</td></tr>';
    }).join('');

    box.innerHTML = '<div class="table-card">' +
      '<div class="table-head"><div class="table-cap">지난 스캔 성과 — 조건별 적중률</div>' +
      '<div class="table-sub">신호 다음 1·5·10·20거래일 뒤 종가 기준. 표본이 쌓이는 중인 칸은 “집계 중”.</div></div>' +
      '<div class="table-hint">← 표를 옆으로 넘겨서 보세요</div>' +
      '<div class="table-wrap pin" style="--mw:720px"><table class="scr-table"><thead><tr>' +
      '<th scope="col">조건</th><th scope="col">+1일</th><th scope="col">+5일</th>' +
      '<th scope="col">+10일</th><th scope="col">+20일</th></tr></thead><tbody>' + rows +
      '</tbody></table></div>' +
      '<div class="table-note">최근 12거래일 신호 표본입니다. 2026-11-09~11-19 구간은 같은 규칙으로 소급 계산해 ' +
      '채웠고(미래 데이터 미사용), 이후 매일 자동으로 누적됩니다.</div></div>' +
      '<div class="table-card" style="margin-top:12px">' +
      '<div class="table-head"><div class="table-cap">최근 신호와 그 뒤 흐른 값</div>' +
      '<div class="table-sub">조건 · 종목 · 신호일 당시 종가 → 이후 종가 등락률</div></div>' +
      '<div class="table-hint">← 표를 옆으로 넘겨서 보세요</div>' +
      '<div class="table-wrap pin" style="--mw:700px"><table class="scr-table"><thead><tr>' +
      '<th scope="col">종목 / 신호일</th><th scope="col">당시 종가</th><th scope="col">+1일</th>' +
      '<th scope="col">+5일</th><th scope="col">+10일</th><th scope="col">+20일</th></tr></thead><tbody>' +
      (recentRows || '<tr><td colspan="6">아직 쌓인 신호가 없습니다.</td></tr>') +
      '</tbody></table></div></div>';
  }

  function mountScreener() {
    Promise.all([loadScreener(), loadHistory()]).then(function (res) {
      var d = res[0], hist = res[1];
      if (!scrActive) {
        var first = d.presets.filter(function (p) { return p.count > 0; })[0] || d.presets[0];
        scrActive = first.id;
      }
      renderScrHeader();
      renderScrTable(d.presets.filter(function (p) { return p.id === scrActive; })[0] || d.presets[0]);
      renderScrPerf(hist);
      syncTableScroll();
    }).catch(function (err) {
      var top = document.getElementById('scr-top');
      if (top) top.innerHTML = '<span class="scr-loading">조건검색 데이터를 불러오지 못했습니다. (' + esc(err.message) + ')</span>';
    });
  }

  function openStockChart(code, label) {
    loadMaData().then(function (data) {
      var chart = data.charts && data.charts[code];
      if (!chart) {
        alert('이 종목의 차트 데이터가 아직 없습니다. 다음 스캔에서 함께 생성됩니다.');
        return;
      }
      var ov = document.createElement('div');
      ov.className = 'ma-modal';
      var last = chart.c[chart.c.length - 1];
      var prev = chart.c[chart.c.length - 2];
      var chg = prev ? (last / prev - 1) * 100 : 0;
      ov.innerHTML = '<div class="ma-modal-card" role="dialog" aria-modal="true" aria-label="' + esc(label || chart.name) + ' 차트">' +
        '<div class="ma-modal-head"><div><div class="ma-modal-t">' + esc(chart.name || label) + '</div>' +
        '<div class="ma-modal-s">' + esc(code) + (chart.market ? ' · ' + esc(chart.market) : '') +
        ' · ' + esc(chart.baseDate) + ' 종가 ' + num(last) + ' (' + pct(chg) + ') · 일봉 ' + num(chart.bars) + '개</div></div>' +
        '<button type="button" class="ma-close" aria-label="닫기">✕</button></div>' +
        '<div class="ma-modal-body"></div>' +
        '<div class="ma-modal-foot">이동평균선은 범례를 눌러 켜고 끌 수 있습니다. 데이터가 모자란 선은 회색으로 표시되고 그리지 않습니다. ' +
        '추세선·지지/저항·박스권·삼각수렴은 최근 90봉 종가에서 자동 계산하며, 툴바 <b>?</b>를 누르면 계산 기준이 나옵니다. · ' +
        '<a href="https://finance.naver.com/item/main.naver?code=' + encodeURIComponent(code) + '" target="_blank" rel="noopener">네이버 금융에서 보기 →</a></div>' +
        '</div>';
      document.body.appendChild(ov);
      document.body.style.overflow = 'hidden';
      mountMaChart(ov.querySelector('.ma-modal-body'), chart, { height: window.innerWidth < 760 ? 260 : 340 });
      function close() {
        ov.remove();
        document.body.style.overflow = '';
        document.removeEventListener('keydown', onKey);
      }
      function onKey(e) { if (e.key === 'Escape') close(); }
      ov.addEventListener('click', function (e) {
        if (e.target === ov || e.target.closest('.ma-close')) close();
      });
      document.addEventListener('keydown', onKey);
    }).catch(function () {
      alert('차트 데이터를 불러오지 못했습니다.');
    });
  }

  /* ---------------- render ---------------- */
  function renderBlock(b) {
    switch (b.type) {
      case 'callout': return blockCallout(b);
      case 'table': return blockTable(b);
      case 'cards': return blockCards(b);
      case 'list': return blockList(b);
      case 'timeline': return blockTimeline(b);
      case 'chips': return blockChips(b);
      case 'news': return blockNews(b);
      case 'chart': return blockChart(b);
      case 'tradingview': return blockTV(b);
      case 'simulator': return blockSimulator(b);
      case 'tvTimeline': return blockTVTimeline(b);
      case 'screener': return blockScreener(b);
      default: return '';
    }
  }

  function syncTableScroll() {
    Array.prototype.forEach.call(document.querySelectorAll('.table-wrap'), function (w) {
      var card = w.closest('.table-card');
      if (!card) return;
      if (w.scrollWidth > w.clientWidth + 2) card.classList.add('can-scroll');
      else card.classList.remove('can-scroll');
    });
  }

  function render(section) {
    var html = '<div class="sec-head">' +
      '<h2 class="sec-title">' + esc(section.title) + '</h2>' +
      '<p class="sec-lead">' + esc(section.lead) + '</p></div>';
    html += section.blocks.map(renderBlock).join('');
    app.innerHTML = html;

    syncTableScroll();
    Array.prototype.forEach.call(app.querySelectorAll('.sim'), recalcSim);

    var tv = section.blocks.filter(function (b) { return b.type === 'tradingview'; })[0];
    if (tv) mountChart(tv, tv.default);
    var tl = section.blocks.filter(function (b) { return b.type === 'tvTimeline'; })[0];
    if (tl) mountTimeline(tl);
    if (section.blocks.filter(function (b) { return b.type === 'screener'; }).length) mountScreener();
  }

  function setTab(id, push) {
    var section = DATA.sections.filter(function (s) { return s.id === id; })[0] || DATA.sections[0];
    activeId = section.id;

    Array.prototype.forEach.call(document.querySelectorAll('.tab'), function (t) {
      t.setAttribute('aria-selected', t.dataset.id === section.id ? 'true' : 'false');
    });

    render(section);

    if (push && location.hash !== '#' + section.id) {
      history.pushState(null, '', '#' + section.id);
    }

    var appTop = app.getBoundingClientRect().top + window.pageYOffset - 12;
    if (window.pageYOffset > appTop) {
      window.scrollTo({ top: appTop, behavior: 'smooth' });
    }
  }

  function buildTabs(sections) {
    tabsBar.innerHTML = '<div class="tabs-inner" role="tablist">' + sections.map(function (s, i) {
      return '<button class="tab" role="tab" type="button" data-id="' + esc(s.id) + '" aria-selected="' + (i === 0 ? 'true' : 'false') + '">' +
        esc(s.tab) + '</button>';
    }).join('') + '</div>';

    tabsBar.addEventListener('click', function (e) {
      var btn = e.target.closest('.tab');
      if (!btn) return;
      setTab(btn.dataset.id, true);
    });
  }

  function buildHero(data) {
    var h = data.hero;
    document.getElementById('heroEyebrow').textContent = h.eyebrow || '';
    document.getElementById('heroTitle').innerHTML = esc(h.title) + ' <span class="grad">' + esc(h.titleAccent) + '</span>';
    document.getElementById('heroLead').textContent = h.lead || '';
    document.getElementById('dataAsOf').textContent = data.site.dataAsOf || '';
    document.getElementById('heroStats').innerHTML = (h.stats || []).map(function (s) {
      return '<div class="stat">' +
        '<div class="k">' + esc(s.label) + '</div>' +
        '<div class="v">' + esc(s.value) + '</div>' +
        '<div class="d ' + tone(s.tone) + '">' + esc(s.delta) + '</div>' +
        (s.sub ? '<div class="s">' + esc(s.sub) + '</div>' : '') +
      '</div>';
    }).join('');
  }

  function applyBrand(data) {
    var site = data.site;
    document.title = site.title + ' · 주식 인사이트';
    document.getElementById('updated').textContent = site.updated;
    document.getElementById('updated2').textContent = site.updated;
    document.getElementById('disclaimer').textContent = site.disclaimer || '';
  }

  function bindDelegates() {
    app.addEventListener('input', function (e) {
      if (e.target.classList && e.target.classList.contains('sim-field')) {
        recalcSim(e.target.closest('.sim'));
      }
    });

    app.addEventListener('click', function (e) {
      var preset = e.target.closest('.preset');
      if (preset) {
        var sim = preset.closest('.sim');
        var inp = sim.querySelector('.sim-field[data-k="' + preset.dataset.k + '"]');
        if (inp) inp.value = preset.dataset.v;
        recalcSim(sim);
        return;
      }

      var cur = e.target.closest('.cur');
      if (cur) {
        var root = cur.closest('.sim');
        root.dataset.cur = cur.dataset.cur;
        Array.prototype.forEach.call(root.querySelectorAll('.cur'), function (b) {
          b.classList.toggle('on', b === cur);
        });
        recalcSim(root);
        return;
      }

      var chip = e.target.closest('.scr-chip');
      if (chip) {
        scrActive = chip.getAttribute('data-pid');
        renderScrHeader();
        renderScrTable(SCR.presets.filter(function (p) { return p.id === scrActive; })[0]);
        syncTableScroll();
        return;
      }

      var nm = e.target.closest('.scr-name');
      if (nm) {
        openStockChart(nm.getAttribute('data-code'), nm.textContent);
        return;
      }

      var btn = e.target.closest('.sym');
      if (!btn) return;
      var wrap = btn.closest('.tvcard');
      if (wrap) {
        Array.prototype.forEach.call(wrap.querySelectorAll('.sym'), function (b) {
          b.classList.toggle('on', b === btn);
        });
      }
      mountChart({}, btn.dataset.symbol);
    });
  }

  function boot() {
    fetch('data/insights.json', { cache: 'no-store' })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (data) {
        DATA = data;
        applyBrand(data);
        buildHero(data);
        buildTabs(data.sections);
        bindDelegates();
        mountTicker(data.site);
        mountSnap(data.site);

        var initial = (location.hash || '').replace('#', '');
        setTab(initial, false);

        /* ?code=005930 처럼 종목 코드로 차트를 바로 열 수 있다(검증·공유용) */
        var q = /[?&]code=(\d{6}[0-9A-Z]?)/i.exec(location.search);
        if (q) setTimeout(function () { openStockChart(q[1].toUpperCase(), ''); }, 60);

        window.addEventListener('hashchange', function () {
          var id = (location.hash || '').replace('#', '');
          if (id && id !== activeId) setTab(id, false);
        });
        window.addEventListener('resize', function () {
          clearTimeout(boot._t);
          boot._t = setTimeout(function () { syncTableScroll(); redrawMaCharts(); }, 150);
        });
      })
      .catch(function (err) {
        app.innerHTML = '<div class="empty">데이터를 불러오지 못했습니다. (' + esc(err.message) + ')</div>';
      });
  }

  boot();
})();
