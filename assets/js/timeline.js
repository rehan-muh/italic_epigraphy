/* timeline.js - the quarter-century histogram, laid out boustrophedon.
 *
 * Bins run left to right, turn at the end of the row, and run back right to
 * left, so position along the path is chronological throughout even though
 * reading direction alternates. Each bin is a stacked bar by language.
 * Dragging across bins sets the time window; double-clicking clears it.
 *
 * Exposes window.Timeline.
 */
(function (global) {
  'use strict';

  var NS = 'http://www.w3.org/2000/svg';

  function el(name, attrs) {
    var node = document.createElementNS(NS, name);
    if (attrs) Object.keys(attrs).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    return node;
  }

  var Timeline = {
    svg: null,
    quarters: [],
    geometry: [],     // one entry per bin: {x, y, w, h, row, index}
    dragFrom: null,
    onSelect: null,

    init: function (svgEl, quarters, onSelect) {
      this.svg = svgEl;
      this.quarters = quarters;
      this.onSelect = onSelect;
      this.bindEvents();
      var self = this;
      var pending;
      global.addEventListener('resize', function () {
        clearTimeout(pending);
        pending = setTimeout(function () {
          self.render(self.lastCounts, self.lastWindow, self.lastBand);
        }, 120);
      });
    },

    layout: function () {
      var width = this.svg.clientWidth || this.svg.parentNode.clientWidth || 900;
      var n = this.quarters.length;
      var rows = width >= 1000 ? 2 : (width >= 620 ? 3 : 4);
      var perRow = Math.ceil(n / rows);
      rows = Math.ceil(n / perRow);

      var padX = 4, labelH = 13, gap = 8;
      var cellW = (width - padX * 2) / perRow;
      // a share of the viewport rather than a fixed height, so the chart keeps
      // its proportions on a short screen instead of being clipped
      var budget = Math.max(76, Math.min(150, (global.innerHeight || 900) * 0.18));
      var maxRowH = Math.max(28, Math.min(64, (budget - (rows - 1) * gap) / rows));
      var barH = maxRowH - labelH;
      var rowH = maxRowH + gap;

      var geom = [];
      for (var i = 0; i < n; i++) {
        var row = Math.floor(i / perRow);
        var inRow = i % perRow;
        var slot = (row % 2 === 0) ? inRow : (perRow - 1 - inRow);
        geom.push({
          index: i,
          row: row,
          x: padX + slot * cellW,
          w: cellW,
          baseline: row * rowH + barH,
          barH: barH,
          rowH: rowH
        });
      }
      return {
        width: width, rows: rows, perRow: perRow, cellW: cellW,
        barH: barH, rowH: rowH, labelH: labelH, padX: padX,
        height: rows * rowH - gap + 2,
        geom: geom
      };
    },

    /**
     * counts: Map(quarterEdge -> {languageId: n}); n may be fractional in
     *         aoristic mode, where a record is shared across the quarters its
     *         date interval covers
     * window: [minEdge, maxEdge] or null
     * band:   optional {sd: [...]} drawn as an uncertainty whisker per bin
     */
    render: function (counts, window_, band) {
      if (!counts) return;
      this.lastCounts = counts;
      this.lastWindow = window_;
      this.lastBand = band || null;

      var L = this.layout();
      this.geometry = L.geom;
      var svg = this.svg;
      svg.setAttribute('viewBox', '0 0 ' + L.width + ' ' + L.height);
      svg.setAttribute('height', L.height);
      while (svg.firstChild) svg.removeChild(svg.firstChild);

      var max = 1;
      counts.forEach(function (bucket) {
        var t = 0;
        for (var k in bucket) t += bucket[k];
        if (t > max) max = t;
      });

      var scale = function (v) { return Math.sqrt(Math.max(v, 0) / max) * L.barH; };

      // baselines and the turn marks that make the serpentine legible
      for (var r = 0; r < L.rows; r++) {
        var y = r * L.rowH + L.barH + 0.5;
        svg.appendChild(el('line', {
          class: 'rail-line', x1: L.padX, x2: L.width - L.padX, y1: y, y2: y
        }));
        if (r < L.rows - 1) {
          var atRight = (r % 2 === 0);
          var x = atRight ? L.width - L.padX : L.padX;
          var dir = atRight ? 1 : -1;
          var y2 = (r + 1) * L.rowH + L.barH + 0.5;
          svg.appendChild(el('path', {
            class: 'turn-mark',
            d: 'M' + x + ',' + y + ' q' + (dir * 6) + ',0 ' + (dir * 6) + ',' +
               ((y2 - y) / 2) + ' q0,' + ((y2 - y) / 2) + ' ' + (-dir * 6) + ',' + ((y2 - y) / 2)
          }));
        }
      }

      var self = this;
      this.quarters.forEach(function (edge, i) {
        var g = L.geom[i];
        var bucket = counts.get(edge) || {};
        var inWindow = !window_ || (edge >= window_[0] && edge <= window_[1]);

        var group = el('g', { class: 'bin' + (inWindow ? '' : ' out') });
        group.dataset.index = i;

        var total = 0;
        for (var k in bucket) total += bucket[k];

        // stack languages largest first so the dominant one sits on the baseline
        var ids = Object.keys(bucket).sort(function (a, b) { return bucket[b] - bucket[a]; });
        var acc = 0;
        ids.forEach(function (id) {
          var share = bucket[id] / total;
          var h = scale(total) * share;
          acc += h;
          group.appendChild(el('rect', {
            class: 'seg',
            x: g.x + 1,
            y: g.baseline - acc,
            width: Math.max(1, g.w - 2),
            height: Math.max(0.6, h),
            fill: global.Corpus.languageColor(Number(id))
          }));
        });

        // the analytic standard deviation of the Poisson binomial implied by
        // the aoristic weights: how much of this bar's height is an artefact of
        // the dating intervals rather than of the corpus
        if (band && band.sd && band.sd[i] > 0 && total > 0) {
          var sd = band.sd[i] * (band.scale || 1);
          var top = g.baseline - scale(total);
          var hi = g.baseline - scale(total + sd);
          var lo = g.baseline - scale(Math.max(total - sd, 0));
          var cx = g.x + g.w / 2;
          group.appendChild(el('line', {
            class: 'band', x1: cx, x2: cx, y1: hi, y2: lo
          }));
          group.appendChild(el('line', {
            class: 'band', x1: cx - 2, x2: cx + 2, y1: hi, y2: hi
          }));
          void top;
        }

        // an empty quarter keeps a mark, so a gap reads as "nothing recorded"
        // rather than a rendering failure; the fill comes from the stylesheet
        if (total === 0) {
          group.appendChild(el('rect', {
            class: 'seg is-zero', x: g.x + 1, y: g.baseline - 1.5,
            width: Math.max(1, g.w - 2), height: 1.5
          }));
        }

        // one label every other bin, century starts always
        var isCentury = (edge % 100 === 0) || (edge % 100 === 1) || (Math.abs(edge) % 100 === 0);
        if (i % 2 === 0 || isCentury) {
          var t = el('text', {
            class: 'tick' + (isCentury ? ' major' : ''),
            x: g.x + g.w / 2,
            y: g.baseline + L.labelH - 3,
            'text-anchor': 'middle'
          });
          t.textContent = edge < 0 ? Math.abs(edge) : 'A' + edge;
          group.appendChild(t);
        }

        var hit = el('rect', {
          class: 'bin-hit', x: g.x, y: g.row * g.rowH,
          width: g.w, height: g.barH + L.labelH
        });
        hit.setAttribute('data-index', i);
        var title = el('title');
        var shown = total < 10 && total % 1 !== 0
          ? total.toFixed(1) : global.Corpus.fmt.n(Math.round(total));
        title.textContent = global.Corpus.fmt.quarter(edge) + ' \u00b7 ' + shown +
          (Math.round(total) === 1 ? ' inscription' : ' inscriptions') +
          (band && band.sd && band.sd[i] ? ' \u00b1 ' + band.sd[i].toFixed(1) : '');
        hit.appendChild(title);
        group.appendChild(hit);

        svg.appendChild(group);
      });
    },

    bindEvents: function () {
      var self = this;
      var svg = this.svg;

      function indexAt(evt) {
        var target = evt.target;
        if (target && target.hasAttribute && target.hasAttribute('data-index')) {
          return Number(target.getAttribute('data-index'));
        }
        return null;
      }

      svg.addEventListener('pointerdown', function (evt) {
        var i = indexAt(evt);
        if (i === null) return;
        self.dragFrom = i;
        svg.setPointerCapture(evt.pointerId);
        self.commit(i, i);
      });

      svg.addEventListener('pointermove', function (evt) {
        if (self.dragFrom === null) return;
        var i = indexAt(evt);
        if (i === null) return;
        self.commit(Math.min(self.dragFrom, i), Math.max(self.dragFrom, i));
      });

      ['pointerup', 'pointercancel'].forEach(function (name) {
        svg.addEventListener(name, function () { self.dragFrom = null; });
      });

      svg.addEventListener('dblclick', function () {
        self.dragFrom = null;
        if (self.onSelect) self.onSelect(null);
      });

      // keyboard: arrow keys nudge the window when the svg has focus
      svg.setAttribute('tabindex', '0');
      svg.addEventListener('keydown', function (evt) {
        if (evt.key !== 'ArrowLeft' && evt.key !== 'ArrowRight') return;
        evt.preventDefault();
        var w = self.lastWindow;
        var qs = self.quarters;
        var lo = w ? qs.indexOf(w[0]) : 0;
        var hi = w ? qs.indexOf(w[1]) : qs.length - 1;
        var step = evt.key === 'ArrowRight' ? 1 : -1;
        lo = Math.max(0, Math.min(qs.length - 1, lo + step));
        hi = Math.max(lo, Math.min(qs.length - 1, hi + step));
        self.commit(lo, hi);
      });
    },

    commit: function (lo, hi) {
      if (!this.onSelect) return;
      this.onSelect([this.quarters[lo], this.quarters[hi]]);
    }
  };

  global.Timeline = Timeline;
})(window);
