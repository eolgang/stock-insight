(function () {
  'use strict';

  var app = document.getElementById('app');
  var tabsBar = document.getElementById('tabs');
  var DATA = null;
  var activeId = null;
  var tvJsPromise = null;

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

  function blockChart(b) {
    if (b.chart === 'line') return lineChart(b);
    if (b.chart === 'bars') return barsChart(b);
    if (b.chart === 'range') return rangeChart(b);
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
      wrap.innerHTML = '<div class="tv-fallback">' +
        '<div><b style="color:#e9eef7;font-size:13px">한국 거래소 종목은 TradingView 무료 임베드에서 표시되지 않습니다.</b><br>' +
        '라이선스 제한으로 위젯 안에서는 열리지 않습니다. 아래에서 바로 볼 수 있어요.<br><br>' +
        '<a href="https://www.tradingview.com/chart/?symbol=' + encodeURIComponent(symbol) + '" target="_blank" rel="noopener">TradingView에서 보기 →</a><br>' +
        '<a href="https://finance.naver.com/item/main.naver?code=' + encodeURIComponent(code) + '" target="_blank" rel="noopener">네이버 금융에서 보기 →</a>' +
        '<br><br><span style="font-size:11px">미국 종목·ETF 버튼은 위젯 안에서 그대로 열립니다.</span></div></div>';
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

        window.addEventListener('hashchange', function () {
          var id = (location.hash || '').replace('#', '');
          if (id && id !== activeId) setTab(id, false);
        });
        window.addEventListener('resize', function () {
          clearTimeout(boot._t);
          boot._t = setTimeout(syncTableScroll, 150);
        });
      })
      .catch(function (err) {
        app.innerHTML = '<div class="empty">데이터를 불러오지 못했습니다. (' + esc(err.message) + ')</div>';
      });
  }

  boot();
})();
