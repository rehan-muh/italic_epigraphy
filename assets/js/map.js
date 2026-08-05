/* map.js - the topographic map.
 *
 * All base layers are raster tile services that need no API key. Relief comes
 * from AWS Terrain Tiles in terrarium encoding, used both for the hillshade
 * layer and for the optional 3-D terrain. Findspots are a GeoJSON source that
 * is rewritten whenever the filter state changes.
 *
 * Exposes window.AtlasMap.
 */
(function (global) {
  'use strict';

  var CFG = global.ATLAS_CONFIG || {};

  var AtlasMap = {
    map: null,
    mode: 'points',
    showFeatures: true,

    init: function (containerId, onPlaceClick) {
      var self = this;
      this.onPlaceClick = onPlaceClick;

      var sources = { dem: {
        type: 'raster-dem',
        tiles: [CFG.dem.tiles],
        tileSize: 256,
        encoding: CFG.dem.encoding || 'terrarium',
        maxzoom: CFG.dem.max_zoom || 13,
        attribution: CFG.dem.attribution || ''
      } };

      var layers = [{ id: 'bg', type: 'background', paint: { 'background-color': '#1f2523' } }];

      CFG.basemaps.forEach(function (b, i) {
        sources['base-' + b.id] = {
          type: 'raster',
          tiles: [b.tiles],
          tileSize: 256,
          maxzoom: b.max_zoom || 16,
          attribution: b.attribution || ''
        };
        layers.push({
          id: 'base-' + b.id,
          type: 'raster',
          source: 'base-' + b.id,
          layout: { visibility: i === 0 ? 'visible' : 'none' },
          paint: { 'raster-opacity': 0.94, 'raster-fade-duration': 200 }
        });
      });

      layers.push({
        id: 'hillshade',
        type: 'hillshade',
        source: 'dem',
        layout: { visibility: 'visible' },
        paint: {
          'hillshade-exaggeration': 0.42,
          'hillshade-shadow-color': '#20211d',
          'hillshade-highlight-color': '#f4efe3',
          'hillshade-accent-color': '#4a5148'
        }
      });

      sources.places = { type: 'geojson', data: { type: 'FeatureCollection', features: [] } };

      this.map = new maplibregl.Map({
        container: containerId,
        style: { version: 8, glyphs: 'https://basemaps.cartocdn.com/gl/fonts/{fontstack}/{range}.pbf',
                 sources: sources, layers: layers },
        center: CFG.center,
        zoom: CFG.zoom,
        minZoom: CFG.minZoom,
        maxZoom: CFG.maxZoom,
        maxPitch: 75,
        attributionControl: { compact: true }
      });

      this.map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-right');
      this.map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-right');
      this.map.addControl(new maplibregl.FullscreenControl(), 'top-right');

      return new Promise(function (resolve) {
        self.map.on('load', function () {
          self.addDataLayers();
          self.bindInteractions();
          resolve(self);
        });
      });
    },

    addDataLayers: function () {
      var map = this.map;

      // The enriched geography is no longer one baked-in source. Layers are
      // declared in scripts/geosources.yml, listed in assets/data/layers.json,
      // and added here the first time the reader switches one on. Everything
      // sits below 'places-heat', which is the first corpus layer.
      // --- corpus ---------------------------------------------------------
      map.addLayer({
        id: 'places-heat', type: 'heatmap', source: 'places',
        layout: { visibility: 'none' },
        paint: {
          'heatmap-weight': ['interpolate', ['linear'], ['get', 'n'], 0, 0.06, 50, 0.5, 400, 1],
          'heatmap-intensity': ['interpolate', ['linear'], ['zoom'], 4, 0.9, 10, 2.4],
          'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 4, 14, 10, 34],
          'heatmap-opacity': 0.72,
          'heatmap-color': ['interpolate', ['linear'], ['heatmap-density'],
            0, 'rgba(0,0,0,0)',
            0.2, 'rgba(79,139,120,0.55)',
            0.45, 'rgba(211,160,74,0.7)',
            0.7, 'rgba(205,74,44,0.82)',
            1, 'rgba(255,232,214,0.95)']
        }
      });

      map.addLayer({
        id: 'places-circle', type: 'circle', source: 'places',
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'],
            4, ['interpolate', ['linear'], ['sqrt', ['get', 'n']], 1, 2.4, 6, 6, 20, 12, 45, 20],
            10, ['interpolate', ['linear'], ['sqrt', ['get', 'n']], 1, 4, 6, 11, 20, 20, 45, 34]],
          'circle-color': ['coalesce', ['get', 'color'], '#7a827d'],
          'circle-opacity': 0.8,
          'circle-stroke-width': ['case', ['boolean', ['feature-state', 'active'], false], 2, 0.8],
          'circle-stroke-color': ['case', ['boolean', ['feature-state', 'active'], false],
                                  '#ece5d8', 'rgba(15,18,17,0.75)']
        }
      });

      map.addLayer({
        id: 'places-label', type: 'symbol', source: 'places',
        minzoom: 7,
        filter: ['>=', ['get', 'n'], 3],
        layout: {
          'text-field': ['get', 'name'],
          'text-font': ['Open Sans Semibold'],
          'text-size': ['interpolate', ['linear'], ['zoom'], 7, 10, 12, 13],
          'text-offset': [0, 1.1],
          'text-anchor': 'top',
          'text-optional': true,
          'text-allow-overlap': false
        },
        paint: { 'text-color': '#ece5d8', 'text-halo-color': '#0f1211', 'text-halo-width': 1.6 }
      });
    },

    bindInteractions: function () {
      var map = this.map, self = this, hovered = null;

      map.on('mouseenter', 'places-circle', function () { map.getCanvas().style.cursor = 'pointer'; });
      map.on('mouseleave', 'places-circle', function () {
        map.getCanvas().style.cursor = '';
        if (hovered !== null) map.setFeatureState({ source: 'places', id: hovered }, { active: false });
        hovered = null;
      });
      map.on('mousemove', 'places-circle', function (e) {
        if (!e.features.length) return;
        var id = e.features[0].id;
        if (hovered !== null && hovered !== id) {
          map.setFeatureState({ source: 'places', id: hovered }, { active: false });
        }
        hovered = id;
        map.setFeatureState({ source: 'places', id: id }, { active: true });
      });

      map.on('click', 'places-circle', function (e) {
        if (!e.features.length) return;
        var f = e.features[0];
        new maplibregl.Popup({ offset: 12, maxWidth: '19rem' })
          .setLngLat(f.geometry.coordinates.slice())
          .setHTML(self.popupHTML(f.properties))
          .addTo(map);
        if (self.onPlaceClick) self.onPlaceClick(f.properties);
      });
    },

    popupHTML: function (props) {
      var C = global.Corpus;
      var langs = typeof props.langs === 'string' ? JSON.parse(props.langs) : (props.langs || {});
      var ids = Object.keys(langs).sort(function (a, b) { return langs[b] - langs[a]; }).slice(0, 5);
      var total = ids.reduce(function (s, k) { return s + langs[k]; }, 0) || 1;

      var bars = ids.map(function (id) {
        var pct = Math.round(langs[id] / total * 100);
        return '<div class="pop-bar">' +
          '<span>' + escapeHTML(C.label('languages', id) || 'unspecified') + '</span>' +
          '<span class="track"><span class="fill" style="width:' + pct + '%;background:' +
          C.languageColor(Number(id)) + '"></span></span>' +
          '<span class="n">' + langs[id] + '</span></div>';
      }).join('');

      var bits = [];
      if (props.region) bits.push(escapeHTML(props.region));
      bits.push(C.fmt.n(props.n) + (props.n === 1 ? ' inscription' : ' inscriptions'));
      if (props.rolled > 0) {
        bits.push('including ' + props.rolled +
          (props.rolled === 1 ? ' sub-findspot' : ' sub-findspots'));
      }
      var span = C.fmt.span(
        props.date_min === undefined ? null : props.date_min,
        props.date_max === undefined ? null : props.date_max);
      if (span !== 'undated') bits.push(span);

      var href = (CFG.baseurl || '') + '/place/' + props.slug + '/';
      return '<h3 class="pop-title">' + escapeHTML(props.name) + '</h3>' +
        '<p class="pop-meta">' + bits.join(' &middot; ') + '</p>' +
        '<div class="pop-bars">' + bars + '</div>' +
        '<a href="' + href + '">Open findspot record &rarr;</a>';
    },

    /** Replace the findspot source with the current selection. */
    setPlaces: function (byPlace) {
      var C = global.Corpus, features = [];
      byPlace.forEach(function (rec, pid) {
        var base = C.placeIndex[pid];
        if (!base) return;   // unlocated findspot
        var ids = Object.keys(rec.langs);
        var dominant = ids.length
          ? ids.reduce(function (a, b) { return rec.langs[a] >= rec.langs[b] ? a : b; })
          : null;
        features.push({
          type: 'Feature',
          id: Number(pid),
          geometry: base.geometry,
          properties: {
            id: pid,
            name: base.properties.name,
            slug: base.properties.slug,
            region: base.properties.region,
            n: rec.n,
            date_min: rec.dmin,
            date_max: rec.dmax,
            langs: rec.langs,
            level: base.properties.level,
            rolled: rec.rolled || 0,
            color: dominant ? C.languageColor(Number(dominant)) : '#7a827d'
          }
        });
      });
      features.sort(function (a, b) { return b.properties.n - a.properties.n; });
      this.map.getSource('places').setData({ type: 'FeatureCollection', features: features });
      return features.length;
    },

    // ---- external layers ------------------------------------------------

    /* One entry per feature kind. Polygons get a fill and a casing, lines get a
     * stroke, points get a dot; the same three maplibre layers serve every
     * source, so adding a source to the catalogue needs no code here. */
    STYLES: {
      river:            { color: '#4d7f96', width: [0.5, 1.4, 2.6], label: 'line' },
      lake:             { color: '#2f5a6b', fill: 0.42 },
      coastline:        { color: '#8fa39a', width: [0.4, 0.8, 1.2] },
      basin:            { color: '#4f7f6b', fill: 0.10, casing: 0.5 },
      range:            { color: '#6b6250', fill: 0.10, label: 'area' },
      geology:          { color: '#8a6a4a', fill: 0.22 },
      province:         { color: '#9a7f5f', fill: 0.10, casing: 0.9, label: 'area' },
      'cultural-region':{ color: '#b08a6a', fill: 0.10, casing: 0.8, label: 'area' },
      'urban-area':     { color: '#d3a04a', fill: 0.30, casing: 0.8, label: 'area' },
      wall:             { color: '#cd8a4a', width: [0.8, 1.6, 3] },
      excavation:       { color: '#c8562b', fill: 0.28, casing: 1 },
      tomb:             { color: '#a8523f', fill: 0.35, casing: 0.6, radius: 2.2 },
      road:             { color: '#b0a189', width: [0.4, 0.9, 1.8], label: 'line' },
      'roman-road':     { color: '#d9b06a', width: [0.5, 1.1, 2.2], label: 'line' },
      aqueduct:         { color: '#7fa8b8', width: [0.4, 0.9, 1.6], label: 'line' },
      peak:             { color: '#c9c0ac', radius: 2.4, label: 'point' },
      admin:            { color: '#6b7f8a', fill: 0.05, casing: 0.5 },
      'ancient-place':  { color: '#d3a04a', radius: 2.4, label: 'point' },
      'ancient-footprint': { color: '#d3a04a', fill: 0.20, casing: 0.7, label: 'area' },
      'archaeological-site': { color: '#8fa39a', radius: 2.4, label: 'point' },
      feature:          { color: '#8fa39a', radius: 2.2, label: 'point' }
    },

    activeLayers: {},

    layerIds: function (id) {
      return ['gx-' + id + '-fill', 'gx-' + id + '-line',
              'gx-' + id + '-point', 'gx-' + id + '-label'];
    },

    /** Add one catalogue layer to the map. Idempotent. */
    addExternalLayer: function (entry, data) {
      if (this.activeLayers[entry.id] || !data) return;
      var map = this.map;
      var style = this.STYLES[entry.kind] || this.STYLES.feature;
      var src = 'gx-' + entry.id;
      var ids = this.layerIds(entry.id);
      var before = map.getLayer('places-heat') ? 'places-heat' : undefined;

      map.addSource(src, { type: 'geojson', data: data });

      map.addLayer({
        id: ids[0], type: 'fill', source: src,
        filter: ['==', ['geometry-type'], 'Polygon'],
        paint: { 'fill-color': style.color, 'fill-opacity': style.fill || 0.14 }
      }, before);

      map.addLayer({
        id: ids[1], type: 'line', source: src,
        filter: ['any', ['==', ['geometry-type'], 'LineString'],
                        ['==', ['geometry-type'], 'Polygon']],
        paint: {
          'line-color': style.color,
          'line-opacity': 0.8,
          'line-width': ['interpolate', ['linear'], ['zoom'],
            5, (style.width || [0.3, 0.6, 1])[0] || style.casing || 0.4,
            9, (style.width || [0.3, 0.6, 1])[1] || style.casing || 0.7,
            13, (style.width || [0.3, 0.6, 1])[2] || style.casing || 1.2]
        }
      }, before);

      map.addLayer({
        id: ids[2], type: 'circle', source: src,
        filter: ['==', ['geometry-type'], 'Point'],
        minzoom: entry.features > 3000 ? 6.5 : 4,
        paint: {
          'circle-radius': style.radius || 2.4,
          'circle-color': style.color,
          'circle-opacity': 0.85
        }
      }, before);

      if (style.label) {
        var layout = {
          'text-field': ['coalesce', ['get', 'name'], ''],
          'text-font': [style.label === 'area' ? 'Open Sans Italic' : 'Open Sans Regular'],
          'text-size': 11,
          'text-optional': true
        };
        if (style.label === 'line') {
          layout['symbol-placement'] = 'line';
          layout['text-max-angle'] = 40;
        } else if (style.label === 'area') {
          layout['text-transform'] = 'uppercase';
          layout['text-letter-spacing'] = 0.18;
          layout['text-max-width'] = 8;
        } else {
          layout['text-offset'] = [0, 0.8];
          layout['text-anchor'] = 'top';
        }
        map.addLayer({
          id: ids[3], type: 'symbol', source: src,
          filter: ['has', 'name'],
          minzoom: style.label === 'area' ? 4.5 : 8.5,
          layout: layout,
          paint: {
            'text-color': style.color,
            'text-halo-color': '#10130f',
            'text-halo-width': 1.5,
            'text-opacity': 0.9
          }
        }, before);
      }

      this.activeLayers[entry.id] = true;
      this.bindExternalPopup(ids[0]);
      this.bindExternalPopup(ids[2]);
    },

    bindExternalPopup: function (layerId) {
      var map = this.map;
      if (!map.getLayer(layerId)) return;
      map.on('click', layerId, function (e) {
        if (!e.features.length) return;
        var p = e.features[0].properties || {};
        if (!p.name && !p.uri) return;
        var bits = [];
        if (p.kind) bits.push(escapeHTML(String(p.kind).replace(/-/g, ' ')));
        if (p.types) bits.push(escapeHTML(String(p.types).replace(/[\[\]"]/g, '')));
        if (p.elevation) bits.push(escapeHTML(p.elevation) + ' m');
        var link = p.uri ? '<a href="' + escapeHTML(p.uri) +
          '" target="_blank" rel="noopener">' + escapeHTML(p.uri.replace(/^https?:\/\//, '')) +
          '</a>' : '';
        new maplibregl.Popup({ offset: 10, maxWidth: '18rem' })
          .setLngLat(e.lngLat)
          .setHTML('<h3 class="pop-title">' + escapeHTML(p.name || 'unnamed') + '</h3>' +
                   '<p class="pop-meta">' + bits.join(' &middot; ') + '</p>' + link)
          .addTo(map);
      });
    },

    setLayerVisible: function (entry, on) {
      var self = this;
      if (!on) {
        this.layerIds(entry.id).forEach(function (id) {
          if (self.map.getLayer(id)) self.map.setLayoutProperty(id, 'visibility', 'none');
        });
        return Promise.resolve(false);
      }
      if (this.activeLayers[entry.id]) {
        this.layerIds(entry.id).forEach(function (id) {
          if (self.map.getLayer(id)) self.map.setLayoutProperty(id, 'visibility', 'visible');
        });
        return Promise.resolve(true);
      }
      return global.Corpus.layer(entry.id).then(function (data) {
        self.addExternalLayer(entry, data);
        return !!data;
      });
    },

    setMode: function (mode) {
      this.mode = mode;
      var points = mode === 'points';
      this.map.setLayoutProperty('places-circle', 'visibility', points ? 'visible' : 'none');
      this.map.setLayoutProperty('places-label', 'visibility', points ? 'visible' : 'none');
      this.map.setLayoutProperty('places-heat', 'visibility', points ? 'none' : 'visible');
    },

    setBasemap: function (id) {
      CFG.basemaps.forEach(function (b) {
        this.map.setLayoutProperty('base-' + b.id, 'visibility', b.id === id ? 'visible' : 'none');
      }, this);
    },

    setHillshade: function (on) {
      this.map.setLayoutProperty('hillshade', 'visibility', on ? 'visible' : 'none');
    },

    setTerrain: function (on) {
      if (on) {
        this.map.setTerrain({ source: 'dem', exaggeration: CFG.exaggeration || 1.4 });
        if (this.map.getPitch() < 30) this.map.easeTo({ pitch: 55, duration: 700 });
      } else {
        this.map.setTerrain(null);
        this.map.easeTo({ pitch: 0, duration: 500 });
      }
    },

    /** Zoom threshold below which children are folded into their parent. */
    ROLLUP_ZOOM: 10.5,

    onZoom: function (fn) {
      var map = this.map, last = null;
      map.on('zoomend', function () {
        var rolled = map.getZoom() < AtlasMap.ROLLUP_ZOOM;
        if (rolled !== last) { last = rolled; fn(rolled); }
      });
      return map.getZoom() < AtlasMap.ROLLUP_ZOOM;
    }
  };

  function escapeHTML(s) {
    return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  AtlasMap.escapeHTML = escapeHTML;
  global.AtlasMap = AtlasMap;
})(window);
