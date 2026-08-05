/* place.js - one findspot: a close-up topographic map and its records. */
(function (global) {
  'use strict';

  var C = global.Corpus, CFG = global.ATLAS_CONFIG, P = CFG.place;
  var el = function (id) { return document.getElementById(id); };

  function esc(s) {
    return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function cssVar(name, fallback) {
    try {
      var v = getComputedStyle(document.documentElement).getPropertyValue(name);
      return (v && v.trim()) || fallback;
    } catch (e) { return fallback; }
  }

  function drawMap() {
    var b = CFG.basemaps[0];
    var map = new maplibregl.Map({
      container: 'place-map',
      style: {
        version: 8,
        sources: {
          base: { type: 'raster', tiles: [b.tiles], tileSize: 256,
                  maxzoom: b.max_zoom || 16, attribution: b.attribution },
          dem: { type: 'raster-dem', tiles: [CFG.dem.tiles], tileSize: 256,
                 encoding: CFG.dem.encoding || 'terrarium',
                 maxzoom: CFG.dem.max_zoom || 13, attribution: CFG.dem.attribution }
        },
        layers: [
          { id: 'bg', type: 'background',
            paint: { 'background-color': cssVar('--map-void', '#e6e4df') } },
          { id: 'base', type: 'raster', source: 'base' },
          { id: 'hillshade', type: 'hillshade', source: 'dem',
            paint: { 'hillshade-exaggeration': 0.45 } }
        ]
      },
      center: [P.lon, P.lat],
      zoom: 10.5,
      attributionControl: { compact: true }
    });
    map.addControl(new maplibregl.NavigationControl(), 'top-right');
    map.on('load', function () {
      map.addSource('here', {
        type: 'geojson',
        data: { type: 'Feature', geometry: { type: 'Point', coordinates: [P.lon, P.lat] } }
      });
      // the accent, not a language colour, so it reads as chrome
      var mark = cssVar('--accent', '#17527d');
      map.addLayer({
        id: 'here-halo', type: 'circle', source: 'here',
        paint: { 'circle-radius': 16, 'circle-color': mark, 'circle-opacity': 0.16 }
      });
      map.addLayer({
        id: 'here-dot', type: 'circle', source: 'here',
        paint: { 'circle-radius': 5, 'circle-color': mark,
                 'circle-stroke-width': 1.6, 'circle-stroke-color': '#ffffff' }
      });
    });
  }

  function renderRecords() {
    var rows = [];
    for (var i = 0; i < C.n; i++) {
      if (C.col.place[i] === P.id) rows.push(i);
    }
    rows.sort(function (a, b) {
      var x = C.col.ds[a], y = C.col.ds[b];
      if (x === null) return 1;
      if (y === null) return -1;
      return x - y;
    });

    var langs = {}, dmin = null, dmax = null;
    rows.forEach(function (i) {
      var l = C.col.lang[i];
      if (l !== null) langs[l] = (langs[l] || 0) + 1;
      if (C.col.ds[i] !== null && (dmin === null || C.col.ds[i] < dmin)) dmin = C.col.ds[i];
      if (C.col.de[i] !== null && (dmax === null || C.col.de[i] > dmax)) dmax = C.col.de[i];
    });

    el('p-span').textContent = C.fmt.span(dmin, dmax);
    var names = Object.keys(langs)
      .sort(function (a, b) { return langs[b] - langs[a]; })
      .map(function (id) { return C.label('languages', id); });
    el('p-langs').textContent = names.length ? names.join(', ') : 'None recorded';

    el('p-table').querySelector('tbody').innerHTML = rows.map(function (i) {
      // notes wrap rather than clipping to an ellipsis
      return '<tr>' +
        '<td>' + esc(C.col.ref[i]) + '</td>' +
        '<td>' + esc(C.label('languages', C.col.lang[i])) + '</td>' +
        '<td>' + esc(C.label('alphabets', C.col.alpha[i])) + '</td>' +
        '<td>' + esc(C.label('directions', C.col.dir[i])) + '</td>' +
        '<td class="num">' + esc(C.fmt.year(C.col.ds[i])) + '</td>' +
        '<td class="num">' + esc(C.fmt.year(C.col.de[i])) + '</td>' +
        '<td class="wrap">' + esc(C.text('notes', i)) + '</td>' +
        '</tr>';
    }).join('');

    el('p-status').textContent = rows.length === 0
      ? 'No records are filed under this findspot in the current build.'
      : C.fmt.n(rows.length) + (rows.length === 1 ? ' record' : ' records');
  }

  drawMap();
  C.load().then(renderRecords).catch(function (err) {
    var status = el('p-status');
    status.className = 'status status--error';
    status.textContent = 'The corpus could not be loaded: ' + err.message +
      '. Run python3 scripts/build_db.py --force, then reload.';
    el('p-langs').textContent = 'Unavailable';
  });

})(window);
