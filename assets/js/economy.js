/* economy.js - the latent economic field E(s,t) animation.
 *
 * Drives the markup in _includes/economy-app.html. The data (grid cells,
 * per-layer values x100 as integers, the Italy-wide trajectory, the AWMC
 * shoreline, named places) comes either inline as window.ECONOMY_FIELD - the
 * standalone page built by scripts/economy_field_animation.py - or is fetched
 * from the data-src attribute of #economy-app, which is what the site does so
 * the ~0.7 MB payload is only downloaded on this page.
 *
 * Time is continuous in years (negative = BCE); values are interpolated
 * linearly between the 25-year bin midpoints. Colours are read from the
 * --econ-* tokens in economy.css at draw time, so the theme switch and the
 * OS preference both recolour the map without a reload. */
(function () {
  'use strict';

  var root = document.getElementById('economy-app');
  if (!root) return;

  var $ = function (id) { return document.getElementById('econ-' + id); };

  function boot(D) {
    var NB = D.bins.length, NC = D.lon.length;
    var MIDS = D.bins.map(function (b) { return b + 12.5; });
    var T0 = -700, T1 = -1;
    var LAYERS = [
      { key: 'E_mean', name: 'Shared field E', legend: 'Shared field E, sd units', kind: 'div',
        note: 'Blue: below the 700–1 BCE space-time mean; red: above. Cells outside the Italian support are not shown.' },
      { key: 'E_sd', name: 'Posterior SD of E', legend: 'Posterior SD of E, sd units', kind: 'seq',
        note: 'Darker = less certain. Uncertainty grows where evidence is thin: the interior, the islands, the earliest bins.' },
      { key: 'settlement_field', name: 'Settlement domain', legend: 'Settlement domain field', kind: 'div',
        note: 'Shared field scaled by the domain’s mean loading plus the settlement residual (cities, Pleiades settlements, radiocarbon).' },
      { key: 'exchange_field', name: 'Exchange domain', legend: 'Exchange domain field', kind: 'div',
        note: 'Shared field plus the exchange residual (shipwrecks, amphora stamps).' },
      { key: 'connectivity_field', name: 'Connectivity domain', legend: 'Connectivity domain field', kind: 'div',
        note: 'Shared field plus the connectivity residual (ports, bridges, roads, stations).' },
      { key: 'production_field', name: 'Production domain', legend: 'Production domain field', kind: 'div',
        note: 'Shared field plus the production residual (villas, centuriation, presses, quarries, mines).' },
      { key: 'monetization_field', name: 'Monetization domain', legend: 'Monetization domain field', kind: 'div',
        note: 'Shared field plus the coin-hoard residual; only informed from 200 BCE onward.' }
    ].filter(function (l) { return D.layers[l.key]; });

    /* ---- scale ranges: 1st/99th percentile, symmetric for diverging layers */
    function pct(arr, p) {
      var s = Float64Array.from(arr).sort();
      return s[Math.min(s.length - 1, Math.floor(p * (s.length - 1)))];
    }
    var RANGE = {};
    LAYERS.forEach(function (l) {
      var flat = [];
      D.layers[l.key].forEach(function (row) { row.forEach(function (v) { flat.push(v / 100); }); });
      if (l.kind === 'div') {
        var r = Math.max(Math.abs(pct(flat, 0.01)), Math.abs(pct(flat, 0.99)));
        RANGE[l.key] = [-r, r];
      } else {
        RANGE[l.key] = [pct(flat, 0.005), pct(flat, 0.995)];
      }
    });

    /* ---- OKLab interpolation for perceptually even ramps */
    function hex2rgb(h) {
      h = h.trim(); var n = parseInt(h.slice(1), 16);
      return [(n >> 16) & 255, (n >> 8) & 255, n & 255].map(function (v) { return v / 255; });
    }
    function lin(c) { return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); }
    function gam(c) { return c <= 0.0031308 ? 12.92 * c : 1.055 * Math.pow(c, 1 / 2.4) - 0.055; }
    function rgb2lab(rgb) {
      var r = lin(rgb[0]), g = lin(rgb[1]), b = lin(rgb[2]);
      var l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
      var m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
      var s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
      return [0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
              1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
              0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s];
    }
    function lab2rgb(L) {
      var l_ = L[0] + 0.3963377774 * L[1] + 0.2158037573 * L[2];
      var m_ = L[0] - 0.1055613458 * L[1] - 0.0638541728 * L[2];
      var s_ = L[0] - 0.0894841775 * L[1] - 1.2914855480 * L[2];
      var l = l_ * l_ * l_, m = m_ * m_ * m_, s = s_ * s_ * s_;
      return [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
              -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
              -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s]
        .map(function (c) { return Math.round(255 * Math.min(1, Math.max(0, gam(c)))); });
    }
    function ramp(stops, n) {
      n = n || 256;
      var labs = stops.map(function (h) { return rgb2lab(hex2rgb(h)); }), out = [];
      for (var i = 0; i < n; i++) {
        var u = i / (n - 1) * (stops.length - 1), k = Math.min(stops.length - 2, Math.floor(u)), f = u - k;
        var L = labs[k].map(function (v, j) { return v * (1 - f) + labs[k + 1][j] * f; });
        var c = lab2rgb(L);
        out.push('rgb(' + c[0] + ',' + c[1] + ',' + c[2] + ')');
      }
      return out;
    }
    var LUT = {}, cssKey = '';
    function cssVar(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
    function buildLUTs() {
      var k = ['--econ-div-lo', '--econ-div-mid', '--econ-div-hi', '--econ-seq-lo', '--econ-seq-hi'].map(cssVar).join('|');
      if (k === cssKey) return;
      cssKey = k;
      LUT.div = ramp([cssVar('--econ-div-lo'), cssVar('--econ-div-mid'), cssVar('--econ-div-hi')]);
      LUT.seq = ramp([cssVar('--econ-seq-lo'), cssVar('--econ-seq-hi')]);
      var lut = LUT[LAYERS[state.layer].kind];
      var stops = lut.filter(function (_, i) { return i % 16 === 0; });
      stops.push(lut[lut.length - 1]);
      $('ramp').style.background = 'linear-gradient(90deg, ' + stops.join(',') + ')';
    }

    /* ---- state */
    var state = { t: T0, playing: false, layer: 0, pinned: -1, hover: -1, last: 0 };
    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    /* ---- value at time t: interpolate between bin midpoints */
    function frac(t) {
      if (t <= MIDS[0]) return [0, 0, 0];
      if (t >= MIDS[NB - 1]) return [NB - 1, NB - 1, 0];
      var i = 0; while (MIDS[i + 1] < t) i++;
      return [i, i + 1, (t - MIDS[i]) / (MIDS[i + 1] - MIDS[i])];
    }
    function val(layerKey, c, t) {
      var q = frac(t), row = D.layers[layerKey][c];
      return (row[q[0]] * (1 - q[2]) + row[q[1]] * q[2]) / 100;
    }
    function natAt(t) {
      var q = frac(t), m = D.national.mean, s = D.national.sd;
      return [m[q[0]] * (1 - q[2]) + m[q[1]] * q[2], s[q[0]] * (1 - q[2]) + s[q[1]] * q[2]];
    }
    function fmtYear(t) { var y = Math.round(-t); return y <= 0 ? (1 - y) + ' CE' : y + ' BCE'; }
    function fmt(v, d) { d = d === undefined ? 2 : d; return (v >= 0 ? '+' : '−') + Math.abs(v).toFixed(d); }
    function binLabel(i) { var a = -D.bins[i], b = -(D.bins[i] + 25); return a + '–' + (b || 1) + ' BCE'; }
    function placeAt(c) {
      for (var i = 0; i < D.places.length; i++) if (nearestCell(D.places[i].lon, D.places[i].lat) === c) return D.places[i];
      return null;
    }

    /* ---- map geometry: equirectangular, aspect from cos(42.5 deg) */
    var canvas = $('map'), ctx = canvas.getContext('2d');
    var KX = Math.cos(42.5 * Math.PI / 180), LON0 = 6.5, LONW = 12.5, LAT1 = 47.5, LATH = 12.0;
    var ASPECT = (LONW * KX) / LATH;
    var W = 740, H = Math.round(W / ASPECT), DPR = 1;
    function px(lon) { return (lon - LON0) / LONW * W; }
    function py(lat) { return (LAT1 - lat) / LATH * H; }
    function resize() {
      var cssW = canvas.clientWidth || 740;
      DPR = Math.min(2, window.devicePixelRatio || 1);
      W = cssW; H = Math.round(cssW / ASPECT);
      canvas.width = Math.round(W * DPR); canvas.height = Math.round(H * DPR);
      canvas.style.height = H + 'px';
      draw();
    }

    function draw() {
      buildLUTs();
      var l = LAYERS[state.layer], lut = LUT[l.kind], lo = RANGE[l.key][0], hi = RANGE[l.key][1];
      ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
      ctx.clearRect(0, 0, W, H);
      var cw = 0.25 / LONW * W, ch = 0.25 / LATH * H, t = state.t, c;
      for (c = 0; c < NC; c++) {
        var u = Math.min(1, Math.max(0, (val(l.key, c, t) - lo) / (hi - lo)));
        ctx.fillStyle = lut[Math.round(u * 255)];
        ctx.fillRect(px(D.lon[c]) - cw / 2, py(D.lat[c]) - ch / 2, cw + 0.6, ch + 0.6);
      }
      ctx.strokeStyle = cssVar('--econ-shore'); ctx.lineWidth = 0.8; ctx.globalAlpha = 0.85;
      ctx.beginPath();
      D.shore.forEach(function (run) {
        ctx.moveTo(px(run[0][0]), py(run[0][1]));
        for (var i = 1; i < run.length; i++) ctx.lineTo(px(run[i][0]), py(run[i][1]));
      });
      ctx.stroke(); ctx.globalAlpha = 1;
      function ring(c, color, w) {
        ctx.strokeStyle = color; ctx.lineWidth = w;
        ctx.strokeRect(px(D.lon[c]) - cw / 2 + w / 2, py(D.lat[c]) - ch / 2 + w / 2, cw - w, ch - w);
      }
      if (state.hover >= 0) ring(state.hover, cssVar('--text'), 1.5);
      if (state.pinned >= 0) {
        ring(state.pinned, cssVar('--econ-pin'), 2.5);
        ctx.fillStyle = cssVar('--econ-pin');
        ctx.beginPath(); ctx.arc(px(D.lon[state.pinned]), py(D.lat[state.pinned]), 3, 0, Math.PI * 2); ctx.fill();
      }
      updateReadout(); drawCursor();
    }

    /* ---- cell lookup */
    var INDEX = {};
    for (var ci = 0; ci < NC; ci++) INDEX[D.lon[ci] + ',' + D.lat[ci]] = ci;
    function cellAt(lon, lat) {
      var c = INDEX[(Math.round(lon * 4) / 4) + ',' + (Math.round(lat * 4) / 4)];
      return c === undefined ? -1 : c;
    }
    function nearestCell(lon, lat) {
      var best = 0, bd = 1e9;
      for (var i = 0; i < NC; i++) {
        var dx = (D.lon[i] - lon) * KX, dy = D.lat[i] - lat, d = dx * dx + dy * dy;
        if (d < bd) { bd = d; best = i; }
      }
      return best;
    }

    /* ---- readout */
    function updateReadout() {
      $('yearlabel').textContent = fmtYear(state.t);
      $('r-year').textContent = fmtYear(state.t);
      var nat = natAt(state.t);
      $('r-nat').textContent = fmt(nat[0]) + ' ± ' + (1.96 * nat[1]).toFixed(2);
      var c = state.hover >= 0 ? state.hover : state.pinned;
      if (c < 0) {
        $('p-name').textContent = 'Hover the map, or pin a place';
        $('p-sub').textContent = '';
        $('p-doms').replaceChildren();
        return;
      }
      var pl = state.pinned === c ? placeAt(c) : null;
      var e = val('E_mean', c, state.t), s = D.layers.E_sd ? val('E_sd', c, state.t) : 0;
      $('p-name').textContent = (pl ? pl.name + ' · ' : '') + (state.pinned === c ? 'pinned' : 'hover') +
        ' cell ' + D.lat[c].toFixed(2) + '°N ' + D.lon[c].toFixed(2) + '°E — E ' + fmt(e) + ' ± ' + (1.96 * s).toFixed(2);
      $('p-sub').textContent = state.pinned === c ? 'Click the cell again to unpin.' : 'Click to pin this cell and chart its trajectory.';
      var doms = LAYERS.filter(function (x) { return x.kind === 'div' && x.key !== 'E_mean'; }).map(function (x) {
        var sp = document.createElement('span'); sp.append(x.name.replace(' domain', ''), ' ');
        var b = document.createElement('b'); b.textContent = fmt(val(x.key, c, state.t)); sp.append(b);
        return sp;
      });
      $('p-doms').replaceChildren.apply($('p-doms'), doms);
    }

    /* ---- legend */
    function updateLegend() {
      var l = LAYERS[state.layer], lo = RANGE[l.key][0], hi = RANGE[l.key][1];
      $('legendtitle').textContent = l.legend; $('legendnote').textContent = l.note;
      if (l.kind === 'div') {
        $('t-lo').textContent = '≤ ' + fmt(lo, 1); $('t-mid').textContent = '0'; $('t-hi').textContent = '≥ ' + fmt(hi, 1);
      } else {
        $('t-lo').textContent = lo.toFixed(2); $('t-mid').textContent = ((lo + hi) / 2).toFixed(2); $('t-hi').textContent = '≥ ' + hi.toFixed(2);
      }
      cssKey = ''; buildLUTs();
    }

    /* ---- trajectory chart */
    var svg = $('traj'), NS = 'http://www.w3.org/2000/svg';
    var M = { l: 38, r: 12, t: 12, b: 28 }, TW = 520, TH = 240, yr = [-1, 2];
    var X = function (t) { return M.l + (t - T0) / (T1 - T0) * (TW - M.l - M.r); };
    var YS = function (v) { return M.t + (yr[1] - v) / (yr[1] - yr[0]) * (TH - M.t - M.b); };
    function el(n, a) { var e = document.createElementNS(NS, n); for (var k in a) e.setAttribute(k, a[k]); return e; }
    function text(a, s) { var e = el('text', a); e.textContent = s; return e; }
    function pathOf(xs, ys) { return xs.map(function (x, i) { return (i ? 'L' : 'M') + X(x).toFixed(1) + ',' + YS(ys[i]).toFixed(1); }).join(''); }
    function bandOf(xs, lo, hi) {
      var up = xs.map(function (x, i) { return (i ? 'L' : 'M') + X(x).toFixed(1) + ',' + YS(hi[i]).toFixed(1); }).join('');
      var dn = ''; for (var i = xs.length - 1; i >= 0; i--) dn += 'L' + X(xs[i]).toFixed(1) + ',' + YS(lo[i]).toFixed(1);
      return up + dn + 'Z';
    }
    var cursorEl, hoverEl, tipG;
    function seriesList() {
      var nm = D.national.mean, ns = D.national.sd;
      var out = [{ m: nm, lo: nm.map(function (v, i) { return v - 1.96 * ns[i]; }), hi: nm.map(function (v, i) { return v + 1.96 * ns[i]; }),
                   stroke: 'var(--econ-nat)', fill: 'var(--econ-nat-soft)' }];
      if (state.pinned >= 0) {
        var c = state.pinned, m = D.layers.E_mean[c].map(function (v) { return v / 100; });
        var s = D.layers.E_sd ? D.layers.E_sd[c].map(function (v) { return v / 100; }) : m.map(function () { return 0; });
        out.push({ m: m, lo: m.map(function (v, i) { return v - 1.96 * s[i]; }), hi: m.map(function (v, i) { return v + 1.96 * s[i]; }),
                   stroke: 'var(--econ-pin)', fill: 'var(--econ-pin-soft)' });
      }
      return out;
    }
    function drawTraj() {
      var series = seriesList(), lo = Infinity, hi = -Infinity;
      series.forEach(function (s) { s.lo.forEach(function (v) { if (v < lo) lo = v; }); s.hi.forEach(function (v) { if (v > hi) hi = v; }); });
      yr = [Math.floor(lo * 2) / 2, Math.ceil(hi * 2) / 2];
      svg.replaceChildren();
      for (var v = yr[0]; v <= yr[1] + 1e-9; v += 0.5) {
        svg.append(el('line', { x1: M.l, x2: TW - M.r, y1: YS(v), y2: YS(v), stroke: 'var(--border)', 'stroke-width': 1 }));
        svg.append(text({ x: M.l - 6, y: YS(v) + 4, 'text-anchor': 'end', 'font-size': 11, fill: 'var(--text-muted)' }, fmt(v, 1)));
      }
      if (yr[0] < 0 && yr[1] > 0) svg.append(el('line', { x1: M.l, x2: TW - M.r, y1: YS(0), y2: YS(0), stroke: 'var(--text-muted)', 'stroke-width': 1 }));
      [-700, -600, -500, -400, -300, -200, -100].forEach(function (y) {
        svg.append(text({ x: X(y), y: TH - 8, 'text-anchor': 'middle', 'font-size': 11, fill: 'var(--text-muted)' }, String(-y)));
      });
      svg.append(text({ x: TW - M.r, y: TH - 8, 'text-anchor': 'end', 'font-size': 11, fill: 'var(--text-muted)' }, 'BCE'));
      series.forEach(function (s) {
        svg.append(el('path', { d: bandOf(MIDS, s.lo, s.hi), fill: s.fill, stroke: 'none' }));
        svg.append(el('path', { d: pathOf(MIDS, s.m), fill: 'none', stroke: s.stroke, 'stroke-width': 2, 'stroke-linejoin': 'round' }));
      });
      cursorEl = el('line', { x1: X(state.t), x2: X(state.t), y1: M.t, y2: TH - M.b, stroke: 'var(--text)', 'stroke-width': 1, 'stroke-dasharray': '3 3' });
      hoverEl = el('line', { x1: 0, x2: 0, y1: M.t, y2: TH - M.b, stroke: 'var(--text-2)', 'stroke-width': 1, opacity: 0 });
      tipG = el('g', { opacity: 0 });
      svg.append(cursorEl, hoverEl, tipG);
      $('lgd-pin').hidden = state.pinned < 0;
      if (state.pinned >= 0) {
        var pl = placeAt(state.pinned);
        $('lgd-pin-name').textContent = pl ? pl.name : 'Cell ' + D.lat[state.pinned].toFixed(2) + '°N ' + D.lon[state.pinned].toFixed(2) + '°E';
      }
      buildTable();
    }
    function drawCursor() { if (!cursorEl) return; cursorEl.setAttribute('x1', X(state.t)); cursorEl.setAttribute('x2', X(state.t)); }
    function svgX(ev) { var r = svg.getBoundingClientRect(); return (ev.clientX - r.left) / r.width * TW; }
    svg.addEventListener('pointermove', function (ev) {
      var xs = svgX(ev);
      if (xs < M.l || xs > TW - M.r) { hoverEl.setAttribute('opacity', 0); tipG.setAttribute('opacity', 0); return; }
      var t = T0 + (xs - M.l) / (TW - M.l - M.r) * (T1 - T0), k = 0;
      for (var i = 0; i < NB; i++) if (Math.abs(MIDS[i] - t) < Math.abs(MIDS[k] - t)) k = i;
      hoverEl.setAttribute('x1', X(MIDS[k])); hoverEl.setAttribute('x2', X(MIDS[k])); hoverEl.setAttribute('opacity', 1);
      var rows = [[fmt(D.national.mean[k]) + ' ± ' + (1.96 * D.national.sd[k]).toFixed(2), 'Italy-wide mean', 'var(--econ-nat)']];
      if (state.pinned >= 0) {
        var sd = D.layers.E_sd ? D.layers.E_sd[state.pinned][k] : 0;
        rows.push([fmt(D.layers.E_mean[state.pinned][k] / 100) + ' ± ' + (1.96 * sd / 100).toFixed(2), 'pinned place', 'var(--econ-pin)']);
      }
      tipG.replaceChildren();
      var w = 172, h = 18 + rows.length * 16, bx = X(MIDS[k]) + 10, by = M.t + 4;
      if (bx + w > TW - M.r) bx = X(MIDS[k]) - 10 - w;
      tipG.append(el('rect', { x: bx, y: by, width: w, height: h, rx: 3, fill: 'var(--text)' }));
      tipG.append(text({ x: bx + 8, y: by + 13, 'font-size': 11, fill: 'var(--bg-page)', opacity: 0.8 }, binLabel(k)));
      rows.forEach(function (rw, i) {
        tipG.append(el('line', { x1: bx + 8, x2: bx + 22, y1: by + 26 + i * 16, y2: by + 26 + i * 16, stroke: rw[2], 'stroke-width': 2 }));
        tipG.append(text({ x: bx + 28, y: by + 30 + i * 16, 'font-size': 11, fill: 'var(--bg-page)', 'font-weight': 600 }, rw[0]));
        tipG.append(text({ x: bx + w - 8, y: by + 30 + i * 16, 'font-size': 10.5, fill: 'var(--bg-page)', opacity: 0.75, 'text-anchor': 'end' }, rw[1]));
      });
      tipG.setAttribute('opacity', 1);
    });
    svg.addEventListener('pointerleave', function () { hoverEl.setAttribute('opacity', 0); tipG.setAttribute('opacity', 0); });
    svg.addEventListener('click', function (ev) {
      var xs = svgX(ev); if (xs < M.l || xs > TW - M.r) return;
      stop(); setTime(T0 + (xs - M.l) / (TW - M.l - M.r) * (T1 - T0));
    });

    /* ---- table view */
    function buildTable() {
      var tb = $('table'); tb.replaceChildren();
      var heads = ['Bin', 'Italy-wide mean E', '95% interval'];
      if (state.pinned >= 0) heads.push('Pinned E', '95% interval');
      var thead = document.createElement('thead'), hr = document.createElement('tr');
      heads.forEach(function (h) { var th = document.createElement('th'); th.scope = 'col'; th.textContent = h; hr.append(th); });
      thead.append(hr); tb.append(thead);
      var tbody = document.createElement('tbody');
      for (var i = 0; i < NB; i++) {
        var tr = document.createElement('tr'), m = D.national.mean[i], s = D.national.sd[i];
        var cells = [binLabel(i), fmt(m), fmt(m - 1.96 * s) + ' … ' + fmt(m + 1.96 * s)];
        if (state.pinned >= 0) {
          var pm = D.layers.E_mean[state.pinned][i] / 100, ps = (D.layers.E_sd ? D.layers.E_sd[state.pinned][i] : 0) / 100;
          cells.push(fmt(pm), fmt(pm - 1.96 * ps) + ' … ' + fmt(pm + 1.96 * ps));
        }
        cells.forEach(function (c) { var td = document.createElement('td'); td.textContent = c; tr.append(td); });
        tbody.append(tr);
      }
      tb.append(tbody);
    }

    /* ---- map hover / click */
    var tip = $('tip');
    function toLonLat(ev) {
      var r = canvas.getBoundingClientRect(), x = (ev.clientX - r.left) / r.width, y = (ev.clientY - r.top) / r.height;
      return [LON0 + x * LONW, LAT1 - y * LATH, ev.clientX - r.left, ev.clientY - r.top];
    }
    canvas.addEventListener('pointermove', function (ev) {
      var p = toLonLat(ev), c = cellAt(p[0], p[1]);
      if (c !== state.hover) { state.hover = c; draw(); }
      if (c < 0) { tip.classList.remove('on'); return; }
      var l = LAYERS[state.layer], e = val('E_mean', c, state.t), s = D.layers.E_sd ? val('E_sd', c, state.t) : 0;
      tip.replaceChildren();
      var b = document.createElement('b'); b.textContent = 'E ' + fmt(e) + ' ± ' + (1.96 * s).toFixed(2);
      tip.append(b, document.createElement('br'));
      if (l.key !== 'E_mean') {
        var b2 = document.createElement('b'); b2.textContent = l.name + ': ' + fmt(val(l.key, c, state.t));
        tip.append(b2, document.createElement('br'));
      }
      tip.append(D.lat[c].toFixed(2) + '°N ' + D.lon[c].toFixed(2) + '°E · ' + fmtYear(state.t));
      tip.style.left = (p[2] + 10) + 'px'; tip.style.top = (p[3] + 10) + 'px'; tip.classList.add('on');
    });
    canvas.addEventListener('pointerleave', function () { state.hover = -1; tip.classList.remove('on'); draw(); });
    canvas.addEventListener('click', function (ev) {
      var p = toLonLat(ev), c = cellAt(p[0], p[1]); if (c < 0) return;
      setPinned(state.pinned === c ? -1 : c);
    });
    function setPinned(c) {
      state.pinned = c;
      Array.prototype.forEach.call(root.querySelectorAll('.econ-chip'), function (ch) {
        ch.classList.toggle('on', c >= 0 && nearestCell(+ch.dataset.lon, +ch.dataset.lat) === c);
        ch.setAttribute('aria-pressed', ch.classList.contains('on') ? 'true' : 'false');
      });
      drawTraj(); draw();
    }

    /* ---- named places */
    var chips = $('chips');
    D.places.forEach(function (p) {
      var b = document.createElement('button'); b.type = 'button'; b.className = 'econ-chip';
      b.textContent = p.name; b.dataset.lon = p.lon; b.dataset.lat = p.lat; b.setAttribute('aria-pressed', 'false');
      b.addEventListener('click', function () { var c = nearestCell(p.lon, p.lat); setPinned(state.pinned === c ? -1 : c); });
      chips.append(b);
    });

    /* ---- layer select */
    var sel = $('layer');
    LAYERS.forEach(function (l, i) { var o = document.createElement('option'); o.value = i; o.textContent = l.name; sel.append(o); });
    sel.addEventListener('change', function () { state.layer = +sel.value; updateLegend(); draw(); });

    /* ---- time and playback */
    var slider = $('time'), playBtn = $('play');
    function setTime(t) { state.t = Math.max(T0, Math.min(T1, t)); slider.value = Math.round(state.t); draw(); }
    slider.addEventListener('input', function () { stop(); setTime(+slider.value); });
    function tick(now) {
      if (!state.playing) return;
      var dt = Math.min(0.1, (now - state.last) / 1000); state.last = now;
      var t = state.t + dt * (+$('speed').value);
      if (t >= T1) { if ($('loop').checked) t = T0; else { setTime(T1); stop(); return; } }
      setTime(t); requestAnimationFrame(tick);
    }
    function play() {
      if (state.playing) return;
      if (state.t >= T1) state.t = T0;
      state.playing = true; state.last = performance.now();
      playBtn.textContent = 'Pause'; playBtn.setAttribute('aria-pressed', 'true');
      requestAnimationFrame(tick);
    }
    function stop() { state.playing = false; playBtn.textContent = 'Play'; playBtn.setAttribute('aria-pressed', 'false'); }
    playBtn.addEventListener('click', function () { state.playing ? stop() : play(); });
    document.addEventListener('keydown', function (ev) {
      var tag = document.activeElement ? document.activeElement.tagName : '';
      if (/INPUT|SELECT|TEXTAREA/.test(tag)) return;
      if (ev.code === 'Space' && tag !== 'BUTTON') { ev.preventDefault(); state.playing ? stop() : play(); }
      if (ev.code === 'ArrowRight') { stop(); setTime(state.t + 25); }
      if (ev.code === 'ArrowLeft') { stop(); setTime(state.t - 25); }
    });

    /* ---- theme changes recolour the ramps; resize redraws */
    if (window.matchMedia) window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function () { cssKey = ''; draw(); });
    window.addEventListener('atlas:themechange', function () { cssKey = ''; draw(); });
    new MutationObserver(function () { cssKey = ''; draw(); }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    window.addEventListener('resize', resize);

    /* ---- start (the standalone page can hand back a saved state) */
    function start(saved) {
      var fresh = !(saved && typeof saved.t === 'number');
      if (!fresh) {
        state.t = saved.t; state.layer = saved.layer || 0; state.pinned = saved.pinned === undefined ? -1 : saved.pinned;
        sel.value = state.layer; $('loop').checked = !!saved.loop;
      } else {
        state.t = reduce ? -500 : T0;
      }
      updateLegend(); drawTraj(); resize(); setPinned(state.pinned);
      if (fresh && !reduce) play();
    }
    var hot = window.claude && window.claude.hot;
    if (hot && hot.snapshot) hot.snapshot(function () { return { t: state.t, layer: state.layer, pinned: state.pinned, loop: $('loop').checked }; });
    if (hot && hot.ready) hot.ready(start); else start(hot && hot.data ? hot.data : null);
  }

  function fail(msg) {
    var p = document.createElement('p'); p.className = 'econ-note'; p.setAttribute('role', 'alert');
    p.textContent = msg; root.prepend(p);
  }

  if (window.ECONOMY_FIELD) {
    boot(window.ECONOMY_FIELD);
  } else if (root.dataset.src) {
    fetch(root.dataset.src).then(function (r) {
      if (!r.ok) throw new Error(r.status + ' ' + r.statusText);
      return r.json();
    }).then(boot).catch(function (e) {
      fail('The field data could not be loaded (' + e.message + '). Rebuild it with scripts/economy_field_animation.py.');
    });
  } else {
    fail('No field data: set data-src on #economy-app or define window.ECONOMY_FIELD.');
  }
})();
