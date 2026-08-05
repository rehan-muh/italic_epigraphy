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
    dicts: {},       // dictionary-encoded string columns
    manifest: null,  // the enriched-geography layer catalogue
    layerCache: {},  // id -> GeoJSON, fetched the first time a layer is shown
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
      // Only the three corpus files are fetched up front. The geography is a
      // manifest of a few hundred bytes; each layer's GeoJSON is downloaded the
      // first time the reader switches that layer on, which is what stops a
      // dozen polygon sources from delaying the first paint.
      var wanted = [
        fetch(url('/assets/data/corpus.json')).then(function (r) { return r.json(); }),
        fetch(url('/assets/data/places.geojson')).then(function (r) { return r.json(); }),
        fetch(url('/assets/data/timeline.json')).then(function (r) { return r.json(); }),
        fetch(url('/assets/data/layers.json'))
          .then(function (r) { return r.ok ? r.json() : null; })
          .catch(function () { return null; })
      ];
      return Promise.all(wanted).then(function (res) {
        var corpus = res[0];
        self.n = corpus.n;
        self.col = corpus.columns;
        self.lookups = corpus.lookups;
        self.dicts = corpus.dicts || { notes: [''], script: [''] };
        self.places = res[1];
        self.timeline = res[2];
        self.manifest = res[3] && res[3].layers && res[3].layers.length ? res[3] : null;

        self.qIndex = {};
        (self.timeline.quarters || []).forEach(function (edge, i) { self.qIndex[edge] = i; });

        self.places.features.forEach(function (f) {
          self.placeIndex[f.properties.id] = f;
        });

        // A lower-cased haystack per row for the search box. notes and script
        // are dictionary indices, and the dictionaries are small, so the folded
        // strings are built once per distinct value rather than once per row.
        var foldedNotes = (self.dicts.notes || []).map(function (v) { return v.toLowerCase(); });
        var foldedScript = (self.dicts.script || []).map(function (v) { return v.toLowerCase(); });
        var placeNames = self.lookups.places || {};
        var foldedPlaces = {};
        Object.keys(placeNames).forEach(function (k) {
          foldedPlaces[k] = String(placeNames[k]).toLowerCase();
        });

        self.haystack = new Array(self.n);
        for (var i = 0; i < self.n; i++) {
          self.haystack[i] = (
            (self.col.ref[i] || '').toLowerCase() + ' ' +
            (foldedPlaces[self.col.place[i]] || '') + ' ' +
            (foldedNotes[self.col.notes[i]] || '') + ' ' +
            (foldedScript[self.col.script[i]] || '')
          );
        }

        self.ready = true;
        return self;
      });
    },

    // ---- vocabulary helpers --------------------------------------------

    /** A dictionary-encoded string column, decoded for one row. */
    text: function (field, i) {
      var table = this.dicts[field];
      if (!table) return '';
      return table[this.col[field][i]] || '';
    },

    /** GeoJSON for one enriched-geography layer, fetched at most once. */
    layer: function (id) {
      var self = this;
      if (this.layerCache[id]) return Promise.resolve(this.layerCache[id]);
      var entry = (this.manifest && this.manifest.layers || []).filter(function (l) {
        return l.id === id;
      })[0];
      if (!entry) return Promise.resolve(null);
      return fetch(url('/assets/data/' + entry.file))
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          if (data) self.layerCache[id] = data;
          return data;
        })
        .catch(function () { return null; });
    },

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

    /** Per-quarter, per-language counts under everything except the time window.
     *
     * mode 'start' bins each record on the quarter its Date.Start falls in,
     * which is what epigraphic databases usually show and what produces the
     * spikes on the round century edges. mode 'aoristic' spreads each record
     * evenly over the quarters its date interval covers, so a record dated
     * 400 to 1 BC contributes a sixteenth to each of sixteen bins instead of a
     * whole inscription to the first one.
     */
    timelineCounts: function (mode) {
      var saved = this.state.quarters;
      this.state.quarters = null;
      var rows = this.select();
      this.state.quarters = saved;

      var c = this.col, out = new Map(), qs = this.timeline.quarters, qi = this.qIndex;
      var aoristic = mode === 'aoristic';
      // variance of the Poisson binomial the weights imply, accumulated for the
      // current selection rather than read off the whole-corpus figure
      var variance = new Float64Array(qs.length);

      for (var k = 0; k < rows.length; k++) {
        var i = rows[k], l = c.lang[i];
        if (!aoristic) {
          var q = c.q[i];
          if (q === null) continue;
          add(q, l, 1);
          continue;
        }
        // A few records are dated past the end of the axis; the build clamps
        // them to it rather than stretching the timeline by forty empty bins,
        // and the browser has to clamp the same way.
        var a = qi[c.q[i]], b = qi[c.qe[i]];
        if (a === undefined) a = c.q[i] < qs[0] ? 0 : qs.length - 1;
        if (b === undefined) b = c.qe[i] > qs[qs.length - 1] ? qs.length - 1 : 0;
        if (c.q[i] === null || c.qe[i] === null) continue;
        if (a > b) { var t = a; a = b; b = t; }
        var w = 1 / (c.nq[i] || (b - a + 1));
        for (var j = a; j <= b; j++) {
          add(qs[j], l, w);
          variance[j] += w * (1 - w);
        }
      }

      this.lastSd = aoristic
        ? Array.prototype.map.call(variance, function (v) { return Math.sqrt(v); })
        : null;

      // records with no language recorded are still inscriptions, so they are
      // stacked under id 0, which has no colour of its own and falls back to
      // the neutral grey
      function add(edge, lang, weight) {
        var bucket = out.get(edge);
        if (!bucket) { bucket = {}; out.set(edge, bucket); }
        var key = lang === null ? 0 : lang;
        bucket[key] = (bucket[key] || 0) + weight;
      }
      return out;
    },

    /** Fold child findspots into their parent, for the zoomed-out map.
     *
     * Forty-two Monterozzi tombs share one coordinate to four decimals. Drawn
     * separately they are forty-two dots on one pixel; folded into Monterozzi
     * they are one dot the size of the whole necropolis.
     */
    rollup: function (byPlace) {
      var parents = this.lookups.place_parents || {};
      var out = new Map();
      byPlace.forEach(function (rec, pid) {
        var target = parents[pid] !== undefined ? parents[pid] : pid;
        var into = out.get(target);
        if (!into) {
          into = { n: 0, langs: {}, dmin: null, dmax: null, rolled: 0 };
          out.set(target, into);
        }
        into.n += rec.n;
        if (target !== pid) into.rolled++;
        Object.keys(rec.langs).forEach(function (l) {
          into.langs[l] = (into.langs[l] || 0) + rec.langs[l];
        });
        if (rec.dmin !== null && (into.dmin === null || rec.dmin < into.dmin)) into.dmin = rec.dmin;
        if (rec.dmax !== null && (into.dmax === null || rec.dmax > into.dmax)) into.dmax = rec.dmax;
      });
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
