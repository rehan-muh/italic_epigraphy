/* corpus.js - loads the generated data and answers filter questions about it.
 *
 * The corpus is delivered column-major with integer codes and a lookup table,
 * which keeps 22k records to a couple of megabytes and makes filtering a pass
 * over typed arrays rather than an array of objects.
 *
 * Exposes window.Corpus.
 */
(function (global) {
  'use strict';

  var base = (global.ATLAS_CONFIG && global.ATLAS_CONFIG.baseurl) || '';

  function url(p) { return base + p; }

  var Corpus = {
    ready: false,
    n: 0,
    col: {},         // columnar data
    lookups: {},     // id -> label maps
    places: null,    // GeoJSON FeatureCollection, one point per findspot
    features: null,  // enriched geography, may be empty
    timeline: null,
    placeIndex: {},  // place id -> GeoJSON feature
    listeners: [],

    state: {
      lang: null,    // Set of ids, null means "all"
      alpha: null,
      region: null,
      dir: null,
      quarters: null, // [minEdge, maxEdge] inclusive, null means all
      q: ''           // free text
    },

    // ---- loading --------------------------------------------------------

    load: function () {
      var self = this;
      var wanted = [
        fetch(url('/assets/data/corpus.json')).then(function (r) { return r.json(); }),
        fetch(url('/assets/data/places.geojson')).then(function (r) { return r.json(); }),
        fetch(url('/assets/data/timeline.json')).then(function (r) { return r.json(); }),
        fetch(url('/assets/data/features.geojson'))
          .then(function (r) { return r.ok ? r.json() : null; })
          .catch(function () { return null; })
      ];
      return Promise.all(wanted).then(function (res) {
        var corpus = res[0];
        self.n = corpus.n;
        self.col = corpus.columns;
        self.lookups = corpus.lookups;
        self.places = res[1];
        self.timeline = res[2];
        self.features = res[3] && res[3].features && res[3].features.length ? res[3] : null;

        self.places.features.forEach(function (f) {
          self.placeIndex[f.properties.id] = f;
        });

        // a lower-cased haystack per row, built once, for the search box
        self.haystack = new Array(self.n);
        var placeNames = self.lookups.places || {};
        for (var i = 0; i < self.n; i++) {
          self.haystack[i] = (
            (self.col.ref[i] || '') + ' ' +
            (placeNames[self.col.place[i]] || '') + ' ' +
            (self.col.notes[i] || '') + ' ' +
            (self.col.script[i] || '')
          ).toLowerCase();
        }

        self.ready = true;
        return self;
      });
    },

    // ---- vocabulary helpers --------------------------------------------

    label: function (kind, id) {
      var table = this.lookups[kind] || {};
      return table[id] !== undefined ? table[id] : null;
    },

    languageColor: function (id) {
      var c = this.lookups.language_colors || {};
      return c[id] || '#7a827d';
    },

    // ---- filtering ------------------------------------------------------

    /** Indices of the rows that satisfy the current state. */
    select: function () {
      var s = this.state, c = this.col, out = [];
      var qs = s.quarters, needle = s.q ? s.q.toLowerCase() : '';
      for (var i = 0; i < this.n; i++) {
        if (s.lang && !s.lang.has(c.lang[i])) continue;
        if (s.alpha && !s.alpha.has(c.alpha[i])) continue;
        if (s.region && !s.region.has(c.region[i])) continue;
        if (s.dir && !s.dir.has(c.dir[i])) continue;
        if (qs) {
          var q = c.q[i];
          if (q === null || q < qs[0] || q > qs[1]) continue;
        }
        if (needle && this.haystack[i].indexOf(needle) === -1) continue;
        out.push(i);
      }
      return out;
    },

    /** Roll a selection up to per-findspot totals and language mixes. */
    aggregate: function (rows) {
      var c = this.col, byPlace = new Map();
      for (var k = 0; k < rows.length; k++) {
        var i = rows[k], pid = c.place[i];
        if (pid === null) continue;
        var rec = byPlace.get(pid);
        if (!rec) { rec = { n: 0, langs: {}, dmin: null, dmax: null }; byPlace.set(pid, rec); }
        rec.n++;
        var l = c.lang[i];
        if (l !== null) rec.langs[l] = (rec.langs[l] || 0) + 1;
        var ds = c.ds[i], de = c.de[i];
        if (ds !== null && (rec.dmin === null || ds < rec.dmin)) rec.dmin = ds;
        if (de !== null && (rec.dmax === null || de > rec.dmax)) rec.dmax = de;
      }
      return byPlace;
    },

    /** Counts per code for one facet, under the other facets' constraints. */
    facetCounts: function (field) {
      var saved = this.state[field];
      this.state[field] = null;
      var rows = this.select();
      this.state[field] = saved;
      var col = this.col[field], counts = {};
      for (var k = 0; k < rows.length; k++) {
        var v = col[rows[k]];
        if (v === null) continue;
        counts[v] = (counts[v] || 0) + 1;
      }
      return counts;
    },

    /** Per-quarter, per-language counts under everything except the time window. */
    timelineCounts: function () {
      var saved = this.state.quarters;
      this.state.quarters = null;
      var rows = this.select();
      this.state.quarters = saved;
      var c = this.col, out = new Map();
      for (var k = 0; k < rows.length; k++) {
        var i = rows[k], q = c.q[i];
        if (q === null) continue;
        var bucket = out.get(q);
        if (!bucket) { bucket = {}; out.set(q, bucket); }
        var l = c.lang[i];
        if (l !== null) bucket[l] = (bucket[l] || 0) + 1;
      }
      return out;
    },

    // ---- change notification -------------------------------------------

    onChange: function (fn) { this.listeners.push(fn); },

    set: function (patch) {
      Object.keys(patch).forEach(function (k) { this.state[k] = patch[k]; }, this);
      this.emit();
    },

    emit: function () {
      var rows = this.select();
      this.listeners.forEach(function (fn) { fn(rows); });
    }
  };

  // ---- shared formatting -------------------------------------------------

  Corpus.fmt = {
    year: function (y) {
      if (y === null || y === undefined) return '\u2014';
      return y < 0 ? Math.abs(y) + ' BC' : 'AD ' + y;
    },
    /* BC quarters read downwards (150-126 BC), AD quarters upwards. */
    quarter: function (edge) {
      if (edge === null || edge === undefined) return 'undated';
      return edge < 0
        ? Math.abs(edge) + '\u2013' + (Math.abs(edge) - 24) + ' BC'
        : 'AD ' + edge + '\u2013' + (edge + 24);
    },
    span: function (a, b) {
      if (a === null && b === null) return 'undated';
      if (a === b) return Corpus.fmt.year(a);
      return Corpus.fmt.year(a) + ' \u2013 ' + Corpus.fmt.year(b);
    },
    n: function (v) { return v.toLocaleString('en-US'); }
  };

  global.Corpus = Corpus;
})(window);
