/* site.js: Jay Reed, Napa and Sonoma. Plain JavaScript, no dependencies, nothing generated at runtime. */

/* ---------- config: the only lines to edit ---------- */
var SITE_CONFIG = {
  // Form endpoint (Formspree or similar, https only). Empty means both forms send nothing anywhere.
  // When you set it, put the same URL in action="" on both forms in index.html for visitors without JavaScript.
  formEndpoint: '',
  // seconds between sends from one visitor (the endpoint should rate-limit too)
  cooldown: 30,
  // the tile skin that ships (pearl plaque, PLAN.md 11:35); other skins are a dev tool behind ?skins=1
  skin: 'pearl'
};

(function () {
  'use strict';

  var desk = document.getElementById('desk');
  if (desk) pile(desk);
  forms();

  /* ---------- the pile (behavior spec: reference/index.html) ---------- */
  function pile(desk) {
    var CATS = [
      { id: 'apps',     label: 'Apps' },
      { id: 'tools',    label: 'Tools' },
      { id: 'sites',    label: 'Websites' },
      { id: 'research', label: 'Research' }
    ];

    // the cards live in index.html (so they show without JavaScript); read them back into data
    var DATA = [];
    var els = {};
    function text(el, sel) { var n = el.querySelector(sel); return n ? n.textContent.trim() : ''; }
    Array.prototype.forEach.call(desk.querySelectorAll('.card'), function (el) {
      var d = {
        id: +el.getAttribute('data-id'),
        cat: el.getAttribute('data-cat'),
        shape: el.getAttribute('data-shape') || null,
        tag: text(el, '.tag'), name: text(el, '.name'), line: text(el, '.line'), num: text(el, '.num'),
        w: +el.getAttribute('data-w'), h: +el.getAttribute('data-h'),
        live: el.getAttribute('data-live') || null,
        size: el.getAttribute('data-size') || 'standard'
      };
      d.bw = d.w; d.bh = d.h;
      DATA.push(d);
      els[d.id] = el;
    });

    // per-skin: how much tilt, and whether every card is the same size
    // pearl plaque sizes (PLAN.md 11:35): heavy for the three big builds, small for websites
    var PEARL_SIZES = { heavy: [500, 330], standard: [420, 280], small: [280, 180] };
    var SKINS = {
      pearl: { rot: 6, sizeFor: function (d) { return PEARL_SIZES[d.size] || PEARL_SIZES.standard; } },
      plain: { rot: 6 },
      paper: { rot: 6 },
      mag:   { rot: 4, sizeFor: function (d) {
        if (d.id % 3 === 0) return [330, 230];          // half-page ads stay half-page
        if (d.shape === 'port') return [330, 420];
        if (d.shape === 'square') return [360, 360];
        return [420, 300];
      } },
      app:   { rot: 3 },
      corp:  { rot: 1.5 },
      deck:  { rot: 4, sizeFor: function () { return [360, 250]; } },
      '80s': { rot: 5 },
      cine:  { rot: 2 }
    };
    var skin = SKINS[SITE_CONFIG.skin] ? SITE_CONFIG.skin : 'plain';

    // base size by shape; portrait and square vary by up to ~12% each way,
    // seeded from the id so a card is the same size on every render
    var SHAPES = { port: [300, 400], square: [320, 320] };
    function vary(id, salt) {
      var t = Math.sin(id * 12.9898 + salt * 78.233) * 43758.5453;
      return 0.88 + (t - Math.floor(t)) * 0.24;
    }
    function applySizes() {
      var s = SKINS[skin];
      DATA.forEach(function (d) {
        var sz;
        if (s.sizeFor) sz = s.sizeFor(d);
        else if (d.shape === 'port') sz = [SHAPES.port[0] * vary(d.id, 1), SHAPES.port[1] * vary(d.id, 2)];
        else if (d.shape === 'square') sz = [SHAPES.square[0] * vary(d.id, 1), SHAPES.square[1] * vary(d.id, 1)];
        else sz = [d.bw, d.bh];
        d.w = Math.round(sz[0]); d.h = Math.round(sz[1]);
      });
    }

    var countEl = document.getElementById('count');
    var dlg = document.getElementById('open');
    var tidyBtn = document.getElementById('tidy');
    var top = DATA.length;
    var tidy = false;
    var drag = null;
    var filter = null;
    var lastOpened = null;
    var skipRestore = false;

    function area() { return { w: desk.clientWidth, h: desk.clientHeight }; }
    function isHeap() { return window.matchMedia('(min-width: 720px)').matches; }

    function clamp(d, x, y) {
      var a = area();
      return {
        x: Math.max(-d.w * 0.3, Math.min(a.w - d.w * 0.7, x)),
        y: Math.max(-d.h * 0.3, Math.min(a.h - d.h * 0.7, y))
      };
    }
    // stricter: wholly on the desk, for dealing and re-fitting (the desk has no visible edge,
    // so a card half off it would just look cut). Drags still use clamp() above.
    var PAD = 24;
    function inside(d, x, y) {
      var a = area();
      return {
        x: Math.max(PAD, Math.min(a.w - d.w - PAD, x)),
        y: Math.max(PAD, Math.min(a.h - d.h - PAD, y))
      };
    }

    // depth: where a card sits in the pile, 0 = bottom, 1 = top, from its rank in z
    // (so a long run of drags that keeps raising `top` does not flatten the untouched pile).
    function shade(d) {
      var below = 0;
      DATA.forEach(function (o) { if (o.z < d.z) below++; });
      // in the tidy grid nothing overlaps, so every card sits on top
      els[d.id].style.setProperty('--depth', (tidy || DATA.length < 2) ? '1' : (below / (DATA.length - 1)).toFixed(3));
    }
    function depth() { DATA.forEach(shade); }

    function place(d) {
      var el = els[d.id];
      el.style.left = d.x + 'px';
      el.style.top = d.y + 'px';
      el.style.width = d.w + 'px';
      el.style.height = d.h + 'px';
      el.style.transform = 'rotate(' + d.r + 'deg)';
      el.style.zIndex = d.z;
      shade(d);
    }

    // bell-shaped random in [-1, 1]: three uniforms averaged, most of the mass near 0
    function bell() { return (Math.random() + Math.random() + Math.random()) / 1.5 - 1; }

    function scatter() {
      desk.style.removeProperty('--desk-h'); // back from the tidy grid's height before measuring
      tidy = false;
      var a = area();
      var rot = SKINS[skin].rot;
      var order = DATA.map(function (d) { return d.id; });
      for (var i = order.length - 1; i > 0; i--) { var j = Math.floor(Math.random() * (i + 1)); var t = order[i]; order[i] = order[j]; order[j] = t; }
      // one to three strays slide out toward the edges; the rest pile up in the middle
      var strays = {};
      var nStray = 1 + Math.floor(Math.random() * 3);
      for (var k = 0; k < nStray; k++) strays[order[Math.floor(Math.random() * order.length)]] = true;
      DATA.forEach(function (d) {
        // how far a card's center can travel from the desk's center and stay on the desk
        var hx = Math.max(0, a.w / 2 - d.w / 2 - PAD), hy = Math.max(0, a.h / 2 - d.h / 2 - PAD);
        var cx, cy;
        if (strays[d.id]) {
          var ang = Math.random() * Math.PI * 2, rad = 0.72 + Math.random() * 0.28;
          cx = Math.cos(ang) * rad * hx; cy = Math.sin(ang) * rad * hy;
        } else {
          // bell() has sd 0.33, so most centers land within about a fifth of the desk from the middle
          cx = Math.max(-hx, Math.min(hx, bell() * a.w * 0.6));
          cy = Math.max(-hy, Math.min(hy, bell() * a.h * 0.56));
        }
        var p = inside(d, a.w / 2 + cx - d.w / 2, a.h / 2 + cy - d.h / 2);
        d.x = p.x; d.y = p.y;
        d.r = Math.round((Math.random() * 2 - 1) * rot * 10) / 10;
        d.z = order.indexOf(d.id) + 1;
        place(d);
      });
      top = DATA.length;
      tidyBtn.textContent = 'tidy';
      tidyBtn.setAttribute('aria-pressed', 'false');
      applyFilter();
    }

    function grid() {
      var a = area();
      var cw = 0, ch = 0;
      DATA.forEach(function (d) { cw = Math.max(cw, d.w); ch = Math.max(ch, d.h); });
      var gap = 20;
      var cols = Math.max(1, Math.floor((a.w - gap) / (cw + gap)));
      var rows = Math.ceil(DATA.length / cols);
      // the grid is taller than the heap area; let the desk grow to fit it
      desk.style.setProperty('--desk-h', Math.max(900, gap + rows * (ch + gap)) + 'px');
      tidy = true;
      DATA.forEach(function (d, i) {
        var col = i % cols, row = Math.floor(i / cols);
        d.x = gap + col * (cw + gap); d.y = gap + row * (ch + gap); d.r = 0; d.z = i + 1;
        place(d);
      });
      top = DATA.length;
      depth();
      tidyBtn.textContent = 'scatter';
      tidyBtn.setAttribute('aria-pressed', 'true');
    }

    // the hood card: its body is copied from the card's own .hood block (static markup in index.html)
    function openCard(d) {
      var card = els[d.id];
      lastOpened = card.querySelector('.hbtn') || card;
      skipRestore = false;
      dlg.style.setProperty('--cat', 'var(--c-' + d.cat + ')');
      document.getElementById('open-k').textContent = 'Under the hood · ' + d.name;
      document.getElementById('open-name').textContent = d.name;
      var hood = card.querySelector('.hood');
      var clientBox = document.getElementById('open-client');
      var body = document.getElementById('open-body');
      clientBox.innerHTML = ''; body.innerHTML = '';
      if (hood) {
        Array.prototype.forEach.call(hood.children, function (n) {
          (n.classList.contains('client') ? clientBox : body).appendChild(n.cloneNode(true));
        });
      } else {
        body.appendChild(el('p', 'p', d.line));
      }
      clientBox.hidden = !clientBox.firstChild;
      var liveEl = document.getElementById('open-live');
      liveEl.hidden = !d.live;
      dlg.classList.toggle('wide', !!d.live);
      if (d.live === 'smoke') showSmoke(liveEl);
      if (typeof dlg.showModal === 'function') dlg.showModal(); else dlg.setAttribute('open', '');
    }
    function closeCard() {
      if (typeof dlg.close === 'function') dlg.close(); else { dlg.removeAttribute('open'); onClosed(); }
    }
    function onClosed() {
      // keyboard users land back on the H button they used; the "ask" link goes on to the form instead
      if (lastOpened && !skipRestore) lastOpened.focus({ preventScroll: true });
    }

    /* ---------- Smoke watch: the open card reads data/smoke.json (written every 3 hours by
       tools/smoke/fetch.py). The only runtime request on the site besides fonts. ---------- */
    var SMOKE_URL = 'data/smoke.json';
    var SMOKE_STALE_HOURS = 12;
    var smokeData = null;      // a promise, so the file is read once per visit
    function loadSmoke() {
      if (!smokeData) {
        smokeData = fetch(SMOKE_URL, { cache: 'no-cache' }).then(function (r) {
          if (!r.ok) throw new Error('HTTP ' + r.status);
          return r.json();
        });
        smokeData.catch(function () { smokeData = null; }); // try again next time the card opens
      }
      return smokeData;
    }
    function pacific(iso) {
      var t = new Date(iso);
      if (isNaN(t)) return iso;
      return t.toLocaleString('en-US', { timeZone: 'America/Los_Angeles', weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
        .replace(/\s?AM$/, ' am').replace(/\s?PM$/, ' pm');
    }
    function el(tag, cls, text) {
      var n = document.createElement(tag);
      if (cls) n.className = cls;
      if (text != null) n.textContent = text;
      return n;
    }
    function showSmoke(box) {
      box.innerHTML = '';
      box.appendChild(el('p', 'live-note', 'Loading.'));
      function noData() {
        box.innerHTML = '';
        box.appendChild(el('p', 'live-note', 'No data right now. The smoke table could not be loaded; try again in a few minutes.'));
      }
      loadSmoke().then(function (data) {
        try { renderSmoke(box, data); } catch (e) { noData(); }  // a malformed file must not leave "Loading." up
      }, noData);
    }
    function renderSmoke(box, data) {
      if (!data || !Array.isArray(data.towns) || !data.towns.length) throw new Error('no towns');
      box.innerHTML = '';
      var wrap = el('div', 'table-wrap');
      wrap.tabIndex = 0;                        // scrollable on a phone, so reachable by keyboard
      wrap.setAttribute('role', 'region');
      wrap.setAttribute('aria-labelledby', 'smoke-caption');
      var table = el('table', 'smoke-table');
      var cap = el('caption', 'visually-hidden', 'Smoke watch by town' + (data.weekend && data.weekend.label ? ', weekend of ' + data.weekend.label : ''));
      cap.id = 'smoke-caption';
      table.appendChild(cap);
      var head = el('thead'), hr = el('tr');
      ['Town', 'Now', 'This weekend', 'Why'].forEach(function (h) { var th = el('th', null, h); th.scope = 'col'; hr.appendChild(th); });
      head.appendChild(hr); table.appendChild(head);
      var body = el('tbody');
      data.towns.forEach(function (t) {
        if (!t || typeof t !== 'object') return;
        var tr = el('tr');
        var th = el('th', null, t.name); th.scope = 'row'; tr.appendChild(th);
        var now = el('td', 'now', t.now); if (t.band) now.setAttribute('data-band', t.band); tr.appendChild(now);
        var wk = el('td', 'wk', t.weekend); if (t.weekend_band) wk.setAttribute('data-band', t.weekend_band); tr.appendChild(wk);
        tr.appendChild(el('td', 'why', t.why));
        body.appendChild(tr);
      });
      table.appendChild(body);
      wrap.appendChild(table);
      box.appendChild(wrap);

      var made = new Date(data.generated_at);
      var known = data.generated_at && !isNaN(made.getTime());
      var age = known ? (Date.now() - made.getTime()) / 36e5 : NaN;
      // stale if old, if the time is missing or unreadable, or if it claims to be from the future (clock trouble)
      var stale = !(age < SMOKE_STALE_HOURS && age > -1);
      var note = el('p', 'live-note' + (stale ? ' stale' : ''));
      var when = !known ? 'Stale: last update time unknown. '
        : age <= -1 ? 'Stale: the update time is in the future (' + pacific(data.generated_at) + ' Pacific). '
        : stale ? 'Stale since ' + pacific(data.generated_at) + ' Pacific (more than ' + SMOKE_STALE_HOURS + ' hours old). '
        : 'Updated ' + pacific(data.generated_at) + ' Pacific. ';
      var credits = Array.isArray(data.credits) && data.credits.length ? data.credits.join(', ') : 'Open-Meteo, National Weather Service, NIFC';
      note.textContent = when + 'Sources: ' + credits + '. A summary of public forecasts, not advice.';
      box.appendChild(note);
    }

    function abbrev(tag) {
      return tag.split(/[^a-z0-9]+/i).filter(Boolean).slice(0, 2).map(function (w) { return w[0]; }).join('').toUpperCase();
    }

    // enhance each card: add the H button (the keyboard and screen-reader way in), the skin decorations,
    // and drag/click. The card itself is not a button: it holds a link (client credit) and the H button.
    function interactive(target) { return !!(target && target.closest && target.closest('a, button')); }
    DATA.forEach(function (d) {
      var el = els[d.id];
      if (d.id % 3 === 0) el.setAttribute('data-half', '');
      var nameEl = el.querySelector('.name');
      if (nameEl) nameEl.id = 'card-name-' + d.id;
      var hbtn = document.createElement('button');
      hbtn.type = 'button';
      hbtn.className = 'hbtn';
      hbtn.textContent = 'H';
      hbtn.setAttribute('aria-label', 'Under the hood: ' + d.name);  // unique names in a screen reader's button list
      hbtn.setAttribute('aria-haspopup', 'dialog');
      hbtn.addEventListener('click', function (e) { e.stopPropagation(); openCard(d); });
      el.appendChild(hbtn);
      el.insertAdjacentHTML('afterbegin', '<div class="corner tl" aria-hidden="true"></div><div class="corner br" aria-hidden="true"></div><div class="icon" aria-hidden="true"><span class="adlabel">Advertisement</span></div>');
      el.querySelector('.corner.tl').textContent = abbrev(d.tag);
      el.querySelector('.corner.br').textContent = abbrev(d.tag);

      el.addEventListener('pointerdown', function (e) {
        if (!isHeap() || !desk.classList.contains('heap')) return;
        if (interactive(e.target)) return;  // let the link and the H button do their own thing
        e.preventDefault();
        top += 1; d.z = top; el.style.zIndex = d.z; depth();
        drag = { d: d, dx: e.clientX - d.x, dy: e.clientY - d.y, sx: e.clientX, sy: e.clientY, moved: false };
        el.classList.add('dragging');
        el.setPointerCapture(e.pointerId);
      });
      el.addEventListener('pointermove', function (e) {
        if (!drag || drag.d !== d) return;
        if (!drag.moved && (Math.abs(e.clientX - drag.sx) > 4 || Math.abs(e.clientY - drag.sy) > 4)) drag.moved = true;
        var p = clamp(d, e.clientX - drag.dx, e.clientY - drag.dy);
        d.x = p.x; d.y = p.y; place(d);
      });
      el.addEventListener('pointerup', function () {
        if (!drag || drag.d !== d) return;
        el.classList.remove('dragging');
        var moved = drag.moved; drag = null;
        if (!moved) openCard(d);
      });
      el.addEventListener('pointercancel', function () { if (drag && drag.d === d) { el.classList.remove('dragging'); drag = null; } });
      el.addEventListener('click', function (e) { if (interactive(e.target)) return; if (!isHeap() || !desk.classList.contains('heap')) openCard(d); });
    });

    // category chips
    var catsEl = document.getElementById('cats');
    function makeChip(id, label, n, dot) {
      var b = document.createElement('button');
      b.type = 'button'; b.className = 'chip'; b.dataset.cat = id || '';
      b.setAttribute('aria-pressed', 'false');
      b.innerHTML = '<span class="dot" aria-hidden="true"></span><span class="l"></span><span class="n"></span>';
      if (dot) b.querySelector('.dot').style.setProperty('--dot', dot); else b.querySelector('.dot').style.display = 'none';
      b.querySelector('.l').textContent = label;
      b.querySelector('.n').textContent = n;
      b.addEventListener('click', function () { setFilter(filter === id ? null : id); });
      catsEl.appendChild(b);
      return b;
    }
    makeChip(null, 'All', DATA.length, null);
    CATS.forEach(function (c) {
      var n = DATA.filter(function (d) { return d.cat === c.id; }).length;
      makeChip(c.id, c.label, n, 'var(--c-' + c.id + ')');
    });
    function setFilter(id) {
      filter = id;
      Array.prototype.forEach.call(catsEl.querySelectorAll('.chip'), function (b) {
        b.setAttribute('aria-pressed', String((b.dataset.cat || null) === filter));
      });
      applyFilter();
    }
    function applyFilter() {
      var shown = 0;
      // matching cards rise above the dimmed ones, keeping their relative order
      var dimmed = DATA.filter(function (d) { return filter && d.cat !== filter; });
      var lit = DATA.filter(function (d) { return !filter || d.cat === filter; });
      dimmed.sort(function (a, b) { return a.z - b.z; }).forEach(function (d, i) { d.z = i + 1; });
      lit.sort(function (a, b) { return a.z - b.z; }).forEach(function (d, i) { d.z = dimmed.length + i + 1; });
      top = DATA.length;
      DATA.forEach(function (d) {
        var el = els[d.id];
        var dim = filter && d.cat !== filter;
        el.classList.toggle('dim', !!dim);
        el.style.zIndex = d.z;
        if (!dim) shown++;
      });
      depth();
      countEl.textContent = (filter ? shown + ' of ' + DATA.length : DATA.length) + ' cards';
    }

    // slideshow: step through the heap, one card to the top every few seconds (only when asked)
    var playBtn = document.getElementById('play');
    var playing = null, playIdx = -1;
    function stopPlay() {
      if (playing) { clearInterval(playing); playing = null; }
      playBtn.textContent = 'play';
      playBtn.setAttribute('aria-pressed', 'false');
      DATA.forEach(function (d) { els[d.id].classList.remove('hot'); });
    }
    function step() {
      var list = DATA.filter(function (d) { return !filter || d.cat === filter; });
      if (!list.length) return;
      var prev = list[playIdx];
      if (prev) els[prev.id].classList.remove('hot');
      playIdx = (playIdx + 1) % list.length;
      var d = list[playIdx];
      top += 1; d.z = top;
      var a = area();
      var p = clamp(d, a.w / 2 - d.w / 2 + (Math.random() * 2 - 1) * 80, a.h / 2 - d.h / 2 + (Math.random() * 2 - 1) * 50);
      d.x = p.x; d.y = p.y; place(d);
      depth();
      els[d.id].classList.add('hot');
    }
    function startPlay() {
      if (!desk.classList.contains('heap')) desk.classList.add('heap');
      if (!isHeap()) return;
      if (tidy) scatter();
      playIdx = -1;
      step();
      playing = setInterval(step, 2600);
      playBtn.textContent = 'pause';
      playBtn.setAttribute('aria-pressed', 'true');
    }
    playBtn.addEventListener('click', function () { if (playing) stopPlay(); else startPlay(); });
    desk.addEventListener('pointerdown', function () { if (playing) stopPlay(); });

    document.getElementById('shuffle').addEventListener('click', function () { stopPlay(); if (!desk.classList.contains('heap')) desk.classList.add('heap'); scatter(); });
    tidyBtn.addEventListener('click', function () { stopPlay(); if (!desk.classList.contains('heap')) desk.classList.add('heap'); if (tidy) scatter(); else grid(); });
    document.getElementById('list-link').addEventListener('click', function (e) {
      e.preventDefault(); stopPlay();
      desk.classList.remove('heap'); desk.style.removeProperty('--desk-h');
      DATA.forEach(function (d) { els[d.id].style.cssText = ''; });
    });
    document.getElementById('open-close').addEventListener('click', closeCard);
    document.getElementById('open-ask').addEventListener('click', function () { skipRestore = true; closeCard(); });
    dlg.addEventListener('click', function (e) { if (e.target === dlg) closeCard(); });
    dlg.addEventListener('close', onClosed);
    // keep Tab inside the open hood card (a native modal lets focus slip out to the browser's own UI)
    dlg.addEventListener('keydown', function (e) {
      if (e.key !== 'Tab') return;
      var f = Array.prototype.filter.call(
        dlg.querySelectorAll('a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])'),
        function (n) { return n.offsetParent !== null || n === document.activeElement; });
      if (!f.length) return;
      var first = f[0], last = f[f.length - 1];
      if (e.shiftKey && (document.activeElement === first || document.activeElement === dlg)) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    });

    // skins: one ships (SITE_CONFIG.skin); ?skins=1 shows the picker and remembers the choice
    function setBodySkin(name) {
      Array.prototype.slice.call(document.body.classList).forEach(function (c) { if (c.indexOf('skin-') === 0) document.body.classList.remove(c); });
      document.body.classList.add('skin-' + name);
    }
    function setSkin(name) {
      var prevRot = SKINS[skin].rot;
      skin = SKINS[name] ? name : 'plain';
      setBodySkin(skin);
      var s = SKINS[skin];
      applySizes();
      DATA.forEach(function (d) { d.r = Math.round((d.r / prevRot) * s.rot * 10) / 10; });
      if (desk.classList.contains('heap') && isHeap()) {
        if (tidy) grid(); else DATA.forEach(function (d) { var p = inside(d, d.x, d.y); d.x = p.x; d.y = p.y; place(d); });
      }
      try { localStorage.setItem('skin', skin); } catch (e) {}
    }
    var skinSel = document.getElementById('skin');
    var pickerOn = /(?:^|[?&])skins=1(?:&|$)/.test(window.location.search);
    if (pickerOn) {
      document.getElementById('skin-picker').hidden = false;
      try { var saved = localStorage.getItem('skin'); if (saved && SKINS[saved]) skin = saved; } catch (e) {}
      skinSel.addEventListener('change', function () { setSkin(skinSel.value); });
    }
    skinSel.value = skin;
    setBodySkin(skin);

    // the controls only mean something with JavaScript; show them now
    ['pile-controls', 'list-wrap', 'pile-hint'].forEach(function (id) { document.getElementById(id).hidden = false; });

    desk.classList.add('heap');
    applySizes();
    setFilter(null);
    scatter();

    var resizeTimer = null;
    window.addEventListener('resize', function () {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(function () {
        if (desk.classList.contains('heap') && isHeap()) {
          if (tidy) grid(); else DATA.forEach(function (d) { var p = inside(d, d.x, d.y); d.x = p.x; d.y = p.y; place(d); });
        }
      }, 120);
    });
  }

  /* ---------- forms: contact and link intake, one endpoint, honeypot ---------- */
  function forms() {
    var endpoint = String(SITE_CONFIG.formEndpoint || '').trim();
    var connected = /^https:\/\/[^\s]+$/.test(endpoint);
    Array.prototype.forEach.call(document.querySelectorAll('form[data-intake]'), function (form) {
      var lastSent = 0; // per form, so sending one does not block the other
      var status = form.querySelector('.form-status');
      var button = form.querySelector('[type="submit"]');
      function say(msg) { if (status) status.textContent = msg; }
      if (connected) form.setAttribute('action', endpoint);

      form.addEventListener('submit', function (e) {
        e.preventDefault();
        var trap = form.querySelector('input[name="_gotcha"]');
        if (trap && trap.value) { form.reset(); say('Sent. Thank you.'); return; } // a bot filled the trap: send nothing
        if (!connected) { say('This form is not connected yet, so nothing was sent.'); return; }
        var now = Date.now();
        if (now - lastSent < SITE_CONFIG.cooldown * 1000) { say('Already sent. Please wait a moment.'); return; }
        lastSent = now;
        if (button) button.disabled = true;
        say('Sending.');
        fetch(endpoint, { method: 'POST', body: new FormData(form), headers: { Accept: 'application/json' } })
          .then(function (r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            form.reset();
            say('Sent. Thank you.');
          })
          .catch(function () {
            lastSent = 0;
            say('That did not go through. Please try again later.');
          })
          .then(function () { if (button) button.disabled = false; });
      });
    });
  }
})();
