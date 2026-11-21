/* 얼미리서치 — 차트 분석 맡기기(업로드)
 * 차트 캡처를 GitHub Contents API로 저장소에 커밋한다.
 * 토큰은 이 브라우저의 localStorage에만 저장하고, api.github.com 외에는 보내지 않는다.
 */
(function () {
  'use strict';

  var API = 'https://api.github.com';
  var LS_CFG = 'eolmi.chartInbox.cfg.v1';
  var LS_RECENT = 'eolmi.chartInbox.recent.v1';
  var MAX_FILES = 12;
  var MAX_BYTES = 25 * 1024 * 1024;

  var DEFAULTS = { repoFull: 'eolgang/stock-insight', branch: 'main', dir: 'inbox/' };

  var cfg = null;          // {token, repoFull, branch, dir, visibility}
  var files = [];          // [{file, url, name}]
  var ui = null;           // 열려 있는 동안의 요소 참조
  var busy = false;
  var lastFocus = null;

  /* ---------------- 저장소 ---------------- */
  function readCfg() {
    if (cfg) return cfg;
    var raw = null;
    try { raw = JSON.parse(localStorage.getItem(LS_CFG) || 'null'); } catch (e) { raw = null; }
    cfg = {
      token: (raw && raw.token) || '',
      repoFull: (raw && raw.repoFull) || DEFAULTS.repoFull,
      branch: (raw && raw.branch) || DEFAULTS.branch,
      dir: (raw && typeof raw.dir === 'string') ? raw.dir : DEFAULTS.dir,
      visibility: (raw && raw.visibility) || null
    };
    return cfg;
  }
  function saveCfg(patch) {
    cfg = Object.assign(readCfg(), patch);
    try { localStorage.setItem(LS_CFG, JSON.stringify(cfg)); } catch (e) { /* 저장 불가(사생활 모드 등) */ }
    return cfg;
  }
  function readRecent() {
    try { return JSON.parse(localStorage.getItem(LS_RECENT) || '[]') || []; } catch (e) { return []; }
  }
  function pushRecent(item) {
    var list = readRecent();
    list.unshift(item);
    list = list.slice(0, 10);
    try { localStorage.setItem(LS_RECENT, JSON.stringify(list)); } catch (e) {}
    renderRecent();
  }

  /* ---------------- 작은 도구들 ---------------- */
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function stamp(d) {
    return d.getFullYear() + pad(d.getMonth() + 1) + pad(d.getDate()) + '-' +
      pad(d.getHours()) + pad(d.getMinutes()) + pad(d.getSeconds());
  }
  function humanTime(ts) {
    var d = new Date(ts);
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + ' ' +
      pad(d.getHours()) + ':' + pad(d.getMinutes());
  }
  function humanSize(b) {
    if (b < 1024) return b + 'B';
    if (b < 1024 * 1024) return Math.round(b / 1024) + 'KB';
    return (b / 1024 / 1024).toFixed(1) + 'MB';
  }
  function parseRepo(full) {
    var m = /^\s*([A-Za-z0-9_.-]+)\s*\/\s*([A-Za-z0-9_.-]+)\s*$/.exec(String(full || ''));
    return m ? { owner: m[1], repo: m[2], full: m[1] + '/' + m[2] } : null;
  }
  function normDir(d) {
    var s = String(d == null ? '' : d).trim().replace(/^\/+/, '').replace(/\/+$/, '');
    return s ? s + '/' : '';
  }
  function safeName(name) {
    var dot = name.lastIndexOf('.');
    var base = dot > 0 ? name.slice(0, dot) : name;
    var ext = dot > 0 ? name.slice(dot + 1).toLowerCase() : 'png';
    ext = ext.replace(/[^a-z0-9]/g, '') || 'png';
    var clean = base;
    try { clean = base.replace(/[^\p{L}\p{N}._-]+/gu, '-'); }
    catch (e) { clean = base.replace(/[^A-Za-z0-9._-]+/g, '-'); }
    clean = clean.replace(/-{2,}/g, '-').replace(/^[-_.]+|[-_.]+$/g, '').slice(0, 40);
    return { base: clean || 'chart', ext: ext };
  }
  function encodePath(p) {
    return p.split('/').map(encodeURIComponent).join('/');
  }
  function utf8Base64(str) {
    var bytes = new TextEncoder().encode(str);
    var bin = '';
    for (var i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    return btoa(bin);
  }
  function fileBase64(file) {
    return new Promise(function (resolve, reject) {
      var r = new FileReader();
      r.onload = function () {
        var s = String(r.result || '');
        var i = s.indexOf(',');
        resolve(i >= 0 ? s.slice(i + 1) : s);
      };
      r.onerror = function () { reject(new Error('파일을 읽지 못했습니다.')); };
      r.readAsDataURL(file);
    });
  }
  function uaSummary() {
    var ua = navigator.userAgent || '';
    var os = /Android/i.test(ua) ? 'Android'
      : /iPhone|iPad|iPod/i.test(ua) ? 'iOS'
      : /Windows/i.test(ua) ? 'Windows'
      : /Mac OS X/i.test(ua) ? 'macOS'
      : /Linux/i.test(ua) ? 'Linux' : '기타';
    var br = /Edg\//.test(ua) ? 'Edge'
      : /SamsungBrowser/.test(ua) ? 'Samsung Internet'
      : /Chrome\//.test(ua) ? 'Chrome'
      : /Firefox\//.test(ua) ? 'Firefox'
      : /Safari\//.test(ua) ? 'Safari' : '브라우저';
    return os + ' · ' + br + (window.innerWidth ? ' · ' + window.innerWidth + 'px' : '');
  }

  /* ---------------- GitHub API ---------------- */
  function headers(token) {
    return {
      'Authorization': 'Bearer ' + token,
      'Accept': 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'Content-Type': 'application/json'
    };
  }
  function apiCall(path, opts) {
    opts = opts || {};
    opts.headers = headers(opts.token || readCfg().token);
    delete opts.token;
    return fetch(API + path, opts).then(function (r) {
      return r.text().then(function (t) {
        var data = null;
        try { data = t ? JSON.parse(t) : null; } catch (e) { data = { message: t }; }
        return { ok: r.ok, status: r.status, data: data, headers: r.headers };
      });
    });
  }
  function errText(status, data, c) {
    var msg = data && data.message ? String(data.message) : '';
    var tail = msg ? ' (' + msg + ')' : '';
    if (status === 401) return '토큰이 올바르지 않거나 만료됐습니다. 설정에서 새 토큰을 붙여넣어 주세요.' + tail;
    if (status === 403) return '토큰 권한이 부족합니다. 토큰의 Repository permissions에서 Contents를 Read and write로 두고, 저장소 선택에 ' + c.repoFull + '가 들어 있는지 확인해 주세요.' + tail;
    if (status === 404) return '저장소를 찾지 못했습니다. 설정의 저장소 이름(' + c.repoFull + ')과 브랜치(' + c.branch + ')를 확인하고, 토큰이 그 저장소에 접근할 수 있는지 봐 주세요.' + tail;
    if (status === 409) return '같은 이름의 파일이 이미 있습니다.';
    if (status === 413) return '이미지가 너무 큽니다. 장당 25MB 이하로 올려 주세요.';
    if (status === 422) return '파일을 커밋하지 못했습니다(용량 초과 또는 브랜치 문제).' + tail;
    if (status === 429) return '요청이 너무 많습니다. 잠시 뒤 다시 시도해 주세요.' + tail;
    if (status >= 500) return 'GitHub 서버가 응답하지 않습니다. 잠시 뒤 다시 시도해 주세요. (HTTP ' + status + ')';
    return '업로드에 실패했습니다 (HTTP ' + status + ').' + tail;
  }

  function commitFile(c, path, base64, message) {
    return apiCall('/repos/' + c.owner + '/' + c.repo + '/contents/' + encodePath(path), {
      method: 'PUT',
      body: JSON.stringify({ message: message, content: base64, branch: c.branch })
    });
  }

  /* ---------------- 최근 업로드 ---------------- */
  function renderRecent() {
    if (!ui) return;
    var list = readRecent();
    ui.list.innerHTML = list.map(function (it) {
      var ok = it.status === 'ok';
      var names = (it.names || []).join(', ');
      return '<div class="ci-item">' +
        '<span class="ci-dot ' + (ok ? 'ok' : 'fail') + '"></span>' +
        '<div class="ci-item-main">' +
          '<div class="ci-item-t">' + esc(it.memo ? it.memo : names || '(이름 없음)') + '</div>' +
          '<div class="ci-item-s">' + esc(humanTime(it.ts)) + ' · ' + esc(it.count || (it.names || []).length) + '장 · ' +
            (ok ? '완료' : '실패' + (it.error ? ' (' + esc(it.error) + ')' : '')) +
            (ok && (it.paths || []).length ? ' · <em>' + esc((it.paths || [])[0]) + '</em>' : '') +
          '</div>' +
        '</div>' +
      '</div>';
    }).join('');
  }

  /* ---------------- UI ---------------- */
  function buildFAB() {
    var b = document.createElement('button');
    b.type = 'button';
    b.className = 'ci-fab';
    b.id = 'ciFab';
    b.setAttribute('aria-haspopup', 'dialog');
    b.innerHTML = '<span class="ci-plus" aria-hidden="true">+</span><span class="ci-fab-label">차트 분석 맡기기</span>';
    b.addEventListener('click', open);
    document.body.appendChild(b);
  }

  function buildModal() {
    var ov = document.createElement('div');
    ov.className = 'ci-modal';
    ov.id = 'ciModal';
    ov.hidden = true;
    ov.innerHTML =
      '<div class="ci-card" role="dialog" aria-modal="true" aria-labelledby="ciTitle">' +
        '<div class="ci-head">' +
          '<div class="ci-headtxt">' +
            '<div class="ci-title" id="ciTitle">차트 분석 맡기기</div>' +
            '<div class="ci-sub">차트 캡처를 올리면 얼미가 읽고 분석합니다.</div>' +
          '</div>' +
          '<button type="button" class="ci-close" aria-label="닫기">✕</button>' +
        '</div>' +
        '<div class="ci-panes" role="tablist" aria-label="업로드·설정">' +
          '<button type="button" class="ci-pane-btn on" data-pane="upload" role="tab" aria-selected="true">업로드</button>' +
          '<button type="button" class="ci-pane-btn" data-pane="settings" role="tab" aria-selected="false">설정</button>' +
        '</div>' +
        '<div class="ci-body">' +

          /* ---------- 업로드 ---------- */
          '<div class="ci-pane-body" data-pane-body="upload">' +
            '<div class="ci-warn" id="ciWarn" hidden></div>' +
            '<div class="ci-okline" id="ciOk" hidden></div>' +

            '<div class="ci-drop" id="ciDrop" tabindex="0" role="button" aria-label="차트 이미지 선택">' +
              '<div class="ci-drop-ico" aria-hidden="true">+</div>' +
              '<div class="ci-drop-t">차트 캡처 올리기</div>' +
              '<div class="ci-drop-s">눌러서 고르거나, 끌어다 놓거나, <kbd>Ctrl</kbd>+<kbd>V</kbd>로 붙여넣기<br>' +
              '여러 장 한꺼번에 올릴 수 있습니다 · 장당 25MB까지</div>' +
              '<input type="file" id="ciFile" accept="image/*" multiple hidden>' +
            '</div>' +

            '<div class="ci-thumbs" id="ciThumbs"></div>' +

            '<textarea class="ci-memo" id="ciMemo" rows="2" maxlength="500" ' +
              'placeholder="메모 (선택) — 예: 삼성전자 일봉, 20일선 눌림"></textarea>' +

            '<div class="ci-actions">' +
              '<button type="button" class="ci-primary" id="ciUpload">올리기</button>' +
              '<button type="button" class="ci-ghost" id="ciClear" hidden>비우기</button>' +
            '</div>' +
            '<div class="ci-msg" id="ciMsg" hidden></div>' +

            '<div class="ci-recent">' +
              '<div class="ci-recent-h"><b>최근 업로드</b><span>이 브라우저에 최근 10건</span></div>' +
              '<div class="ci-list" id="ciList"></div>' +
            '</div>' +
          '</div>' +

          /* ---------- 설정 ---------- */
          '<div class="ci-pane-body" data-pane-body="settings" hidden>' +
            '<div class="ci-field">' +
              '<label class="ci-label" for="ciToken">GitHub 토큰 (fine-grained PAT)</label>' +
              '<input class="ci-input mono" id="ciToken" type="password" autocomplete="off" spellcheck="false" ' +
                'placeholder="github_pat_… 또는 ghp_…">' +
              '<div class="ci-note"><b>키는 이 브라우저(localStorage)에만 저장됩니다.</b> ' +
                '서버로 보내지 않고, 브라우저에서 GitHub API로만 직접 전송합니다. 남의 PC에서는 쓰지 마세요.</div>' +
              '<div class="ci-row" style="margin-top:8px">' +
                '<button type="button" class="ci-primary" id="ciSave" style="flex:1 1 auto">저장하고 연결 확인</button>' +
                '<button type="button" class="ci-ghost" id="ciDel">키 삭제</button>' +
              '</div>' +
              '<div class="ci-state" id="ciState" hidden></div>' +
            '</div>' +

            '<div class="ci-field">' +
              '<label class="ci-label" for="ciRepo">저장소</label>' +
              '<input class="ci-input mono" id="ciRepo" type="text" spellcheck="false" placeholder="owner/repo">' +
              '<div class="ci-row" style="margin-top:8px">' +
                '<div style="flex:1 1 auto">' +
                  '<label class="ci-label" for="ciBranch">브랜치</label>' +
                  '<input class="ci-input mono" id="ciBranch" type="text" spellcheck="false" placeholder="main">' +
                '</div>' +
                '<div style="flex:1 1 auto">' +
                  '<label class="ci-label" for="ciDir">폴더</label>' +
                  '<input class="ci-input mono" id="ciDir" type="text" spellcheck="false" placeholder="inbox/">' +
                '</div>' +
              '</div>' +
              '<div class="ci-note">비워 두면 저장소 맨 위에 올라갑니다. 저장소·폴더를 바꾼 뒤에는 위의 <b>저장하고 연결 확인</b>을 눌러 주세요.</div>' +
            '</div>' +

            '<details class="ci-guide">' +
              '<summary>토큰 발급 절차 (처음 한 번만)</summary>' +
              '<ol class="ci-steps">' +
                '<li>github.com 로그인 → 오른쪽 위 프로필 → <b>Settings</b></li>' +
                '<li>왼쪽 맨 아래 <b>Developer settings</b></li>' +
                '<li><b>Personal access tokens</b> → <b>Fine-grained tokens</b> → <b>Generate new token</b></li>' +
                '<li>Token name은 아무거나(예: <code>eolmi-chart-inbox</code>), Expiration은 원하는 기간(예: 90 days)</li>' +
                '<li>Repository access → <b>Only select repositories</b> → <code>stock-insight</code> 선택</li>' +
                '<li>Permissions → Repository permissions → <b>Contents: Read and write</b> ' +
                  '(Metadata는 자동으로 Read-only)</li>' +
                '<li>Generate token → <code>github_pat_…</code> 복사 → 위 칸에 붙여넣고 저장</li>' +
              '</ol>' +
            '</details>' +

            '<div class="ci-foot">업로드 경로는 <code>폴더/YYYYMMDD-HHMMSS-파일명.확장자</code>이고, ' +
              '메모를 적으면 같은 시각의 <code>…-memo.txt</code>가 함께 커밋됩니다. ' +
              '올린 뒤 채팅에서 <b>“올렸어”</b>라고 말하면 얼미가 저장소에서 읽어 분석합니다.</div>' +
          '</div>' +

        '</div>' +
      '</div>';
    document.body.appendChild(ov);

    ui = {
      ov: ov,
      warn: ov.querySelector('#ciWarn'),
      ok: ov.querySelector('#ciOk'),
      drop: ov.querySelector('#ciDrop'),
      input: ov.querySelector('#ciFile'),
      thumbs: ov.querySelector('#ciThumbs'),
      memo: ov.querySelector('#ciMemo'),
      upload: ov.querySelector('#ciUpload'),
      clear: ov.querySelector('#ciClear'),
      msg: ov.querySelector('#ciMsg'),
      list: ov.querySelector('#ciList'),
      token: ov.querySelector('#ciToken'),
      save: ov.querySelector('#ciSave'),
      del: ov.querySelector('#ciDel'),
      state: ov.querySelector('#ciState'),
      repo: ov.querySelector('#ciRepo'),
      branch: ov.querySelector('#ciBranch'),
      dir: ov.querySelector('#ciDir')
    };
    bind();
  }

  function bind() {
    ui.ov.addEventListener('click', function (e) {
      if (e.target === ui.ov || e.target.closest('.ci-close')) close();
      var paneBtn = e.target.closest('.ci-pane-btn');
      if (paneBtn) showPane(paneBtn.dataset.pane);
    });

    ui.drop.addEventListener('click', function () { ui.input.click(); });
    ui.drop.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); ui.input.click(); }
    });
    ui.input.addEventListener('change', function () {
      addFiles(ui.input.files);
      ui.input.value = '';
    });

    ['dragenter', 'dragover'].forEach(function (ev) {
      ui.drop.addEventListener(ev, function (e) {
        e.preventDefault(); e.stopPropagation();
        ui.drop.classList.add('over');
      });
    });
    ['dragleave', 'dragend'].forEach(function (ev) {
      ui.drop.addEventListener(ev, function (e) {
        e.preventDefault();
        if (ev === 'dragleave' && e.relatedTarget && ui.drop.contains(e.relatedTarget)) return;
        ui.drop.classList.remove('over');
      });
    });
    ui.drop.addEventListener('drop', function (e) {
      e.preventDefault(); e.stopPropagation();
      ui.drop.classList.remove('over');
      if (e.dataTransfer && e.dataTransfer.files) addFiles(e.dataTransfer.files);
    });
    /* 페이지 어디로 떨어뜨려도 열려 있으면 받는다 */
    document.addEventListener('dragover', function (e) {
      if (ui && ui.ov.hidden) return;
      if (e.dataTransfer && Array.prototype.indexOf.call(e.dataTransfer.types || [], 'Files') >= 0) e.preventDefault();
    });
    document.addEventListener('drop', function (e) {
      if (!ui || ui.ov.hidden) return;
      if (!e.dataTransfer || !e.dataTransfer.files || !e.dataTransfer.files.length) return;
      e.preventDefault();
      addFiles(e.dataTransfer.files);
    });

    document.addEventListener('paste', function (e) {
      if (!ui || ui.ov.hidden) return;
      var items = (e.clipboardData && e.clipboardData.items) || [];
      var picked = [];
      for (var i = 0; i < items.length; i++) {
        if (items[i].kind === 'file') {
          var f = items[i].getAsFile();
          if (f) picked.push(f);
        }
      }
      if (picked.length) {
        e.preventDefault();
        addFiles(picked);
        msg('busy', picked.length + '장을 붙여넣었습니다. 올리기를 눌러 주세요.');
      }
    });

    ui.thumbs.addEventListener('click', function (e) {
      var x = e.target.closest('.ci-thumb-x');
      if (!x) return;
      removeFile(Number(x.getAttribute('data-i')));
    });

    ui.upload.addEventListener('click', doUpload);
    ui.clear.addEventListener('click', function () { resetFiles(); setMsg('', '', true); });

    ui.save.addEventListener('click', saveToken);
    ui.del.addEventListener('click', deleteToken);
    ui.token.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); saveToken(); }
    });

    document.addEventListener('keydown', function (e) {
      if (!ui || ui.ov.hidden) return;
      if (e.key === 'Escape') { e.preventDefault(); close(); }
    });
  }

  function showPane(name) {
    Array.prototype.forEach.call(ui.ov.querySelectorAll('.ci-pane-btn'), function (b) {
      var on = b.dataset.pane === name;
      b.classList.toggle('on', on);
      b.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    Array.prototype.forEach.call(ui.ov.querySelectorAll('[data-pane-body]'), function (p) {
      p.hidden = p.dataset.paneBody !== name;
    });
    ui.ov.querySelector('.ci-body').scrollTop = 0;
  }

  function open() {
    var c = readCfg();
    lastFocus = document.activeElement;
    if (!ui) buildModal();
    if (ui.ov.hidden) {
      ui.ov.hidden = false;
      document.body.style.overflow = 'hidden';
      ui.repo.value = c.repoFull;
      ui.branch.value = c.branch;
      ui.dir.value = c.dir;
      ui.token.value = '';
      ui.token.placeholder = c.token
        ? '저장됨 (' + c.token.slice(0, 10) + '…' + c.token.slice(-4) + ') — 바꿀 때만 입력'
        : 'github_pat_… 또는 ghp_…';
      renderRecent();
      refreshWarning();
      if (!c.token) {
        showPane('settings');
        setState('err', '아직 토큰이 없습니다. 아래 절차로 토큰을 만들어 붙여넣어 주세요.');
      } else {
        showPane('upload');
        verifyRepo(true);
      }
      setTimeout(function () { (c.token ? ui.drop : ui.token).focus(); }, 30);
    }
  }

  function close() {
    if (!ui || ui.ov.hidden) return;
    ui.ov.hidden = true;
    document.body.style.overflow = '';
    ui.drop.classList.remove('over');
    if (lastFocus && lastFocus.focus) { try { lastFocus.focus(); } catch (e) {} }
  }

  /* ---------------- 파일 선택 ---------------- */
  function addFiles(list) {
    var arr = list ? Array.prototype.slice.call(list) : [];
    var skipped = 0;
    arr.forEach(function (f) {
      if (!f) return;
      var looksImage = /^image\//i.test(f.type || '') || /\.(png|jpe?g|gif|webp|bmp)$/i.test(f.name || '');
      if (!looksImage || f.size > MAX_BYTES || files.length >= MAX_FILES) { skipped++; return; }
      if (files.some(function (x) { return x.file.name === f.name && x.file.size === f.size && x.file.lastModified === f.lastModified; })) return;
      files.push({ file: f, name: f.name || 'chart.png', url: URL.createObjectURL(f) });
    });
    renderThumbs();
    if (skipped) msg('err', skipped + '장은 올릴 수 없어 건너뛰었습니다. (이미지 아님·25MB 초과·' + MAX_FILES + '장 초과)');
    else if (files.length) setMsg('', '', true);
  }
  function removeFile(i) {
    var f = files[i];
    if (f && f.url) URL.revokeObjectURL(f.url);
    files.splice(i, 1);
    renderThumbs();
  }
  function resetFiles() {
    files.forEach(function (f) { if (f.url) URL.revokeObjectURL(f.url); });
    files = [];
    renderThumbs();
    setMsg('', '', true);
  }
  function renderThumbs() {
    if (!ui) return;
    ui.thumbs.innerHTML = files.map(function (f, i) {
      return '<div class="ci-thumb">' +
        '<img src="' + esc(f.url) + '" alt="' + esc(f.name) + '">' +
        '<button type="button" class="ci-thumb-x" data-i="' + i + '" aria-label="' + esc(f.name) + ' 빼기">✕</button>' +
        '<div class="ci-thumb-n">' + esc(f.name) + ' · ' + humanSize(f.file.size) + '</div>' +
      '</div>';
    }).join('');
    ui.clear.hidden = files.length === 0;
    ui.upload.textContent = files.length > 1 ? files.length + '장 올리기' : '올리기';
  }

  /* ---------------- 경고/상태 ---------------- */
  function targetRepo() {
    var c = readCfg();
    return parseRepo(c.repoFull) || parseRepo(DEFAULTS.repoFull);
  }
  function refreshWarning() {
    var c = readCfg();
    var r = targetRepo();
    /* 기본값 stock-insight는 공개 저장소다. */
    var isPublic = r.full.toLowerCase() === 'eolgang/stock-insight' ? true : c.visibility === 'public';
    var isPrivate = c.visibility === 'private' && r.full.toLowerCase() !== 'eolgang/stock-insight';
    if (isPrivate) {
      ui.warn.hidden = true;
      ui.ok.hidden = false;
      ui.ok.textContent = '비공개 저장소 ' + r.full + '의 ' + (c.dir || '') + ' 폴더에 올라갑니다.';
    } else if (isPublic) {
      ui.ok.hidden = true;
      ui.warn.hidden = false;
      ui.warn.innerHTML = '<span aria-hidden="true">⚠</span><span>이미지는 <b>공개 저장소 ' + esc(r.full) + '</b>에 올라갑니다. 주소를 아는 사람은 누구나 볼 수 있습니다.</span>';
    } else {
      ui.warn.hidden = true;
      ui.ok.hidden = true;
    }
  }
  function setMsg(kind, text, hidden) {
    if (!ui) return;
    ui.msg.hidden = !!hidden;
    ui.msg.className = 'ci-msg' + (kind ? ' ' + kind : '');
    ui.msg.innerHTML = text || '';
  }
  function msg(kind, text) { setMsg(kind, esc(text)); }
  function setState(kind, text) {
    if (!ui) return;
    ui.state.hidden = !text;
    ui.state.className = 'ci-state' + (kind ? ' ' + kind : '');
    ui.state.innerHTML = text || '';
  }

  /* ---------------- 설정 저장/확인 ---------------- */
  function readForm() {
    var r = parseRepo(ui.repo.value);
    if (!r) return { error: '저장소를 owner/repo 형식으로 적어 주세요. 예: eolgang/stock-insight' };
    var branch = (ui.branch.value || '').trim() || 'main';
    var dir = normDir(ui.dir.value || '');
    return { repo: r, branch: branch, dir: dir };
  }

  function saveToken() {
    var form = readForm();
    if (form.error) { setState('err', esc(form.error)); return; }
    var tok = (ui.token.value || '').trim();
    var c = readCfg();
    var next = {
      token: tok || c.token,
      repoFull: form.repo.full,
      branch: form.branch,
      dir: form.dir,
      visibility: null
    };
    if (!next.token) { setState('err', '토큰을 붙여넣어 주세요.'); return; }
    saveCfg(next);
    ui.token.value = '';
    ui.token.placeholder = '저장됨 (' + next.token.slice(0, 10) + '…' + next.token.slice(-4) + ') — 바꿀 때만 입력';
    refreshWarning();
    verifyRepo(false);
  }

  function deleteToken() {
    saveCfg({ token: '', visibility: null });
    ui.token.value = '';
    ui.token.placeholder = 'github_pat_… 또는 ghp_…';
    setState('err', '이 브라우저에 저장된 키를 지웠습니다.');
    refreshWarning();
  }

  function verifyRepo(silent) {
    var c = readCfg();
    var r = parseRepo(c.repoFull);
    if (!c.token || !r) return Promise.resolve(false);
    if (!silent) setState('busy', '연결을 확인하는 중…');
    return apiCall('/repos/' + r.owner + '/' + r.repo, { method: 'GET', token: c.token })
      .then(function (res) {
        if (!res.ok) {
          setState('err', esc(errText(res.status, res.data, { repoFull: c.repoFull, branch: c.branch })));
          return false;
        }
        var d = res.data || {};
        saveCfg({ visibility: d.private ? 'private' : 'public' });
        var push = d.permissions ? d.permissions.push : null;
        var bits = ['연결됨', d.private ? '비공개 저장소' : '공개 저장소'];
        if (push === false) bits.push('쓰기 권한 없음 — Contents를 Read and write로 바꿔 주세요');
        setState(push === false ? 'err' : 'ok', esc(bits.join(' · ')) +
          ' <br>' + esc(d.full_name || c.repoFull) + ' · 기본 브랜치 ' + esc(d.default_branch || c.branch));
        refreshWarning();
        return true;
      })
      .catch(function () {
        setState('err', '네트워크에 연결하지 못했습니다. 인터넷 연결을 확인해 주세요.');
        return false;
      });
  }

  /* ---------------- 업로드 ---------------- */
  function commitMessage(now, memo, count) {
    var head = 'chart-inbox: 차트 캡처 ' + count + '장 업로드 (' + humanTime(now) + ')';
    var m = memo ? memo.replace(/\s+/g, ' ').trim().slice(0, 60) : '';
    return m ? head + ' — ' + m : head;
  }

  function doUpload() {
    if (busy) return;
    var c = readCfg();
    var r = parseRepo(c.repoFull);
    if (!c.token) {
      showPane('settings');
      setState('err', '먼저 토큰을 넣어 주세요. 아래 절차를 따라 만들면 됩니다.');
      return;
    }
    if (!r) { showPane('settings'); setState('err', '저장소 이름을 확인해 주세요.'); return; }
    if (!files.length) { msg('err', '올릴 이미지를 먼저 골라 주세요.'); return; }

    var target = {
      owner: r.owner, repo: r.repo, repoFull: r.full,
      branch: c.branch || 'main'
    };
    var dir = normDir(c.dir || '');
    var memo = (ui.memo.value || '').trim();
    var now = new Date();
    var ts = stamp(now);
    var batch = files.slice();
    var done = [];
    var failed = [];

    busy = true;
    ui.upload.disabled = true;
    ui.clear.disabled = true;
    setMsg('busy', '<span class="ci-hint">0 / ' + batch.length + '장 올리는 중…</span>');

    var chain = Promise.resolve();
    batch.forEach(function (item, idx) {
      chain = chain.then(function () {
        return fileBase64(item.file).then(function (b64) {
          var sn = safeName(item.name);
          var base = dir + ts + '-' + sn.base + '.' + sn.ext;
          var message = commitMessage(now, memo, batch.length);
          return putWithRetry(target, base, b64, message).then(function (res) {
            if (!res.ok) throw new Error(errText(res.status, res.data, target));
            done.push(res.path);
            setMsg('busy', '<span class="ci-hint">' + done.length + ' / ' + batch.length + '장 올리는 중…</span>');
          }, function (e) { throw new Error(e && e.message ? e.message : '네트워크 오류'); });
        }).catch(function (err) {
          failed.push({ name: item.name, error: err.message });
        });
      });
    });

    chain.then(function () {
      if (!memo || !done.length) return null;
      var text = '업로드: ' + humanTime(now) + ' (기기 시간)\n' +
        '저장소: ' + target.repoFull + ' · 브랜치 ' + target.branch + '\n' +
        '파일 ' + done.length + '장\n' +
        done.map(function (p) { return '- ' + p; }).join('\n') + '\n\n' +
        '메모:\n' + memo + '\n\n' +
        '기기: ' + uaSummary() + '\n' +
        'userAgent: ' + (navigator.userAgent || '') + '\n';
      var memoPath = dir + ts + '-memo.txt';
      return putWithRetry(target, memoPath, utf8Base64(text), 'chart-inbox: 메모 (' + humanTime(now) + ')')
        .then(function (res) {
          if (res.ok) done.push(res.path);
        }, function () {});
    }).then(function () {
      busy = false;
      ui.upload.disabled = false;
      ui.clear.disabled = false;

      if (done.length) {
        pushRecent({
          ts: now.getTime(), names: batch.map(function (b) { return b.name; }),
          count: batch.length, memo: memo, status: failed.length ? 'fail' : 'ok',
          paths: done, error: failed.length ? failed[0].error : ''
        });
        resetFiles();
        ui.memo.value = '';
        setMsg('ok',
          '<b>' + done.filter(function (p) { return !/\.txt$/.test(p); }).length + '장 올렸습니다.</b> ' +
          '채팅에서 “올렸어”라고 말해 주세요. 얼미가 저장소에서 읽어 분석합니다.' +
          '<code>' + done.map(esc).join('<br>') + '</code>' +
          (failed.length ? '<span class="ci-hint">실패 ' + failed.length + '장: ' +
            esc(failed.map(function (f) { return f.name + ' — ' + f.error; }).join(' / ')) + '</span>' : ''));
      } else {
        setMsg('err', '올리지 못했습니다. ' + esc(failed[0] ? failed[0].error : '원인을 알 수 없습니다.'));
        pushRecent({
          ts: now.getTime(), names: batch.map(function (b) { return b.name; }),
          count: batch.length, memo: memo, status: 'fail', paths: [],
          error: failed[0] ? failed[0].error : ''
        });
      }
      refreshWarning();
    });
  }

  /* 같은 경로가 있으면 -2, -3 … 을 붙여 다시 시도 */
  function putWithRetry(target, path, base64, message) {
    var attempt = 0;
    function run(p) {
      attempt++;
      return commitFile(target, p, base64, message).then(function (res) {
        if (res.ok) { res.path = p; return res; }
        var m = String((res.data && res.data.message) || '').toLowerCase();
        var exists = res.status === 409 || (res.status === 422 && /sha|already exists/.test(m));
        if (exists && attempt <= 4) {
          var dot = p.lastIndexOf('.');
          var stem = dot > 0 ? p.slice(0, dot) : p;
          var ext = dot > 0 ? p.slice(dot) : '';
          // 이미 붙은 -N 은 떼고 새 번호를 붙인다
          stem = stem.replace(/-\d+$/, '');
          return run(stem + '-' + (attempt + 1) + ext);
        }
        res.path = p;
        return res;
      });
    }
    return run(path);
  }

  /* ---------------- 시작 ---------------- */
  function init() {
    readCfg();
    buildFAB();
    /* ?upload=1 로 바로 열 수 있다 */
    if (/[?&]upload=1/.test(location.search)) setTimeout(open, 400);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();

  /* 검증용 최소 훅 (테스트·디버깅) */
  window.EolmiChartInbox = {
    open: open, close: close,
    _addFiles: addFiles, _upload: doUpload, _cfg: readCfg,
    _setCfg: function (p) { saveCfg(p); refreshWarning(); return readCfg(); },
    _count: function () { return files.length; }
  };
})();
