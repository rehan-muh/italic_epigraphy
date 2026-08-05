/* atlas.js - wires the rail, the map and the timeline to the corpus store. */
(function (global) {
  'use strict';

  var C = global.Corpus, M = global.AtlasMap, T = global.Timeline;
  var esc = M.escapeHTML;

  var FACETS = [
    { key: 'lang',   list: 'facet-lang',   count: 'n-lang',   lookup: 'languages',
      colored: true, sort: 'count' },
    { key: 'alpha',  list: 'facet-alpha',  count: 'n-alpha',  lookup: 'alphabets',
      colored: false, sort: 'count' },
    { key: 'region', list: 'facet-region', count: 'n-region', lookup: 'regions',
      colored: false, sort: 'count' },
    { key: 'dir',    list: 'facet-dir',    count: null,       lookup: 'directions',
      colored: false, sort: 'label' }
  ];

  var el = function (id) { return document.getElementById(id); };

  function buildFacet(spec) {
    var table = C.lookups[spec.lookup] || {};
    var counts = C.facetCounts(spec.key);
    var ids = Object.keys(table);

    ids.sort(function (a, b) {
      if (spec.sort === 'count') {
        var d = (counts[b] || 0) - (counts[a] || 0);
        if (d) return d;
      }
      return String(table[a]).localeCompare(String(table[b]));
    });

    var ul = el(spec.list);
    ul.innerHTML = '';
    var selected = C.state[spec.key];

    ids.forEach(function (id) {
      var n = counts[id] || 0;
      var li = document.createElement('li');
      li.className = 'facet' + (n === 0 ? ' is-empty' : '');

      var box = document.createElement('input');
      box.type = 'checkbox';
      box.checked = !selected || selected.has(Number(id));
      box.id = spec.key + '-' + id;
      box.addEventListener('change', function () { toggle(spec, Number(id), box.checked); });

      var label = document.createElement('label');
      label.className = 'label';
      label.htmlFor = box.id;
      if (spec.colored) {
        var sw = document.createElement('i');
        sw.className = 'swatch';
        sw.style.background = C.languageColor(Number(id));
        label.appendChild(sw);
      }
      var span = document.createElement('span');
      span.textContent = table[id];
      span.title = table[id];
      label.appendChild(span);

      var num = document.createElement('span');
      num.className = 'n';
      num.textContent = C.fmt.n(n);

      li.appendChild(box);
      li.appendChild(label);
      li.appendChild(num);
      ul.appendChild(li);
    });

    if (spec.count) {
      var chosen = selected ? selected.size : ids.length;
      el(spec.count).textContent = chosen + ' of ' + ids.length;
    }
  }

  function toggle(spec, id, on) {
    var table = C.lookups[spec.lookup] || {};
    var all = Object.keys(table).map(Number);
    var current = C.state[spec.key] ? new Set(C.state[spec.key]) : new Set(all);
    if (on) current.add(id); else current.delete(id);
    var patch = {};
    patch[spec.key] = current.size === all.length ? null : current;
    C.set(patch);
  }

  function setAll(key, on) {
    var spec = FACETS.filter(function (f) { return f.key === key; })[0];
    var patch = {};
    patch[key] = on ? null : new Set();
    C.set(patch);
    void spec;
  }

  // ------------------------------------------------------------------------

  function renderReadout(rows, placeCount) {
    el('sel-inscriptions').textContent = C.fmt.n(rows.length);
    el('sel-places').textContent = C.fmt.n(placeCount);
    el('sel-share').textContent = Math.round(rows.length / C.n * 100) + '% of corpus';

    var w = C.state.quarters;
    el('sel-window').textContent = w
      ? C.fmt.quarter(w[0]).replace(/ (BC|AD)$/, '') + ' to ' + C.fmt.quarter(w[1])
      : 'all periods';
    el('timeline-range').textContent = w
      ? C.fmt.quarter(w[0]) + '  \u2192  ' + C.fmt.quarter(w[1])
      : 'whole sequence, ' + C.timeline.quarters.length + ' quarter centuries';
  }

  function renderLegend() {
    var counts = C.facetCounts('lang');
    var ids = Object.keys(C.lookups.languages).sort(function (a, b) {
      return (counts[b] || 0) - (counts[a] || 0);
    }).slice(0, 8);
    el('legend-items').innerHTML = ids.map(function (id) {
      return '<li><i class="swatch" style="background:' + C.languageColor(Number(id)) + '"></i>' +
        esc(C.lookups.languages[id]) + '</li>';
    }).join('');
  }

  function refresh(rows) {
    var byPlace = C.aggregate(rows);
    var shown = M.setPlaces(byPlace);
    renderReadout(rows, shown);
    FACETS.forEach(buildFacet);
    T.render(C.timelineCounts(), C.state.quarters);
  }

  // ------------------------------------------------------------------------

  function wireControls() {
    document.querySelectorAll('[data-all]').forEach(function (b) {
      b.addEventListener('click', function () { setAll(b.dataset.all, true); });
    });
    document.querySelectorAll('[data-none]').forEach(function (b) {
      b.addEventListener('click', function () { setAll(b.dataset.none, false); });
    });

    el('reset-all').addEventListener('click', function () {
      el('q').value = '';
      C.set({ lang: null, alpha: null, region: null, dir: null, quarters: null, q: '' });
    });

    var debounce;
    el('q').addEventListener('input', function (e) {
      clearTimeout(debounce);
      var v = e.target.value;
      debounce = setTimeout(function () { C.set({ q: v }); }, 200);
    });

    el('basemap').addEventListener('change', function (e) { M.setBasemap(e.target.value); });

    function chip(id, initial, fn) {
      var b = el(id);
      var on = initial;
      b.addEventListener('click', function () {
        on = !on;
        b.setAttribute('aria-pressed', String(on));
        fn(on);
      });
      return b;
    }

    chip('toggle-hillshade', true, function (on) { M.setHillshade(on); });
    chip('toggle-terrain', false, function (on) { M.setTerrain(on); });

    var featureChip = chip('show-features', true, function (on) { M.setGeoVisible(on); });
    if (!C.features) {
      featureChip.disabled = true;
      featureChip.setAttribute('aria-pressed', 'false');
      featureChip.title = 'No enriched geography yet. Run: python3 scripts/enrich_geo.py';
    }

    var pts = el('mode-points'), heat = el('mode-heat');
    function setMode(mode) {
      M.setMode(mode);
      pts.setAttribute('aria-pressed', String(mode === 'points'));
      heat.setAttribute('aria-pressed', String(mode === 'heat'));
    }
    pts.addEventListener('click', function () { setMode('points'); });
    heat.addEventListener('click', function () { setMode('heat'); });
  }

  // ------------------------------------------------------------------------

  C.load().then(function () {
    return M.init('map', null);
  }).then(function () {
    M.setGeoFeatures(C.features || { type: 'FeatureCollection', features: [] });

    T.init(el('timeline'), C.timeline.quarters, function (window_) {
      C.set({ quarters: window_ });
    });

    wireControls();
    renderLegend();
    C.onChange(refresh);
    C.emit();

    el('loading').hidden = true;
  }).catch(function (err) {
    var box = el('loading');
    box.innerHTML = '<span>Could not load the corpus</span>' +
      '<span style="text-transform:none;letter-spacing:0;max-width:26rem;text-align:center">' +
      esc(err && err.message ? err.message : String(err)) +
      '. Run <code>python3 scripts/build_db.py --force</code>, then reload.</span>';
    console.error(err);
  });

})(window);
