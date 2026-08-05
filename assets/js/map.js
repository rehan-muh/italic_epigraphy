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

  function cssVar(name, fallback) {
    try {
      var v = getComputedStyle(document.documentElement).getPropertyValue(name);
      return (v && v.trim()) || fallback;
    } catch (e) { return fallback; }
  }

  /* Every base layer is a light raster, so symbols drawn over it are coloured
   * for a light ground in both themes: dark text in a white halo. */
  var INK = '#16181b';
  var HALO = 'rgba(255,255,255,0.92)';

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

      var layers = [{
        id: 'bg', type: 'background',
        paint: { 'background-color': cssVar('--map-void', '#e6e4df') }
      }];

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

      global.addEventListener('atlas:themechange', function () { self.applyTheme(); });

      /* Resolve on style.load, not load: 'load' also waits for the first
       * screenful of raster tiles, so a throttled or firewalled tile service
       * would leave the rail, the timeline and the layer panel behind a
       * loading state even though the corpus itself had arrived. Adding the
       * corpus layers only needs the style. */
      return new Promise(function (resolve) {
        var done = false;
        function ready() {
          if (done) return;
          done = true;
          self.addDataLayers();
          self.bindInteractions();
          resolve(self);
        }
        if (self.map.isStyleLoaded()) ready();
        else self.map.once('style.load', ready);
        self.map.once('load', ready);
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
          'heatmap-opacity': 0.78,
          /* Sequential ramp, light to dark: the base map is light, so a ramp
           * that brightens towards its maximum hides the densest areas. The
           * hue is the interface accent, which no language uses. */
          'heatmap-color': ['interpolate', ['linear'], ['heatmap-density'],
            0, 'rgba(23,82,125,0)',
            0.2, 'rgba(138,178,208,0.45)',
            0.45, 'rgba(74,131,175,0.65)',
            0.7, 'rgba(28,90,136,0.8)',
            1, 'rgba(10,45,72,0.92)']
        }
      });

      map.addLayer({
        id: 'places-circle', type: 'circle', source: 'places',
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'],
            4, ['interpolate', ['linear'], ['sqrt', ['get', 'n']], 1, 2.4, 6, 6, 20, 12, 45, 20],
            10, ['interpolate', ['linear'], ['sqrt', ['get', 'n']], 1, 4, 6, 11, 20, 20, 45, 34]],
          'circle-color': ['coalesce', ['get', 'color'], '#6f776f'],
          'circle-opacity': 0.85,
          'circle-stroke-width': ['case', ['boolean', ['feature-state', 'active'], false], 2.4, 0.9],
          'circle-stroke-color': ['case', ['boolean', ['feature-state', 'active'], false],
                                  INK, HALO]
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
        paint: { 'text-color': INK, 'text-halo-color': HALO, 'text-halo-width': 1.8 }
      });
    },

    /** The neutral behind the tiles follows the theme. */
    applyTheme: function () {
      if (this.map && this.map.getLayer('bg')) {
        this.map.setPaintProperty('bg', 'background-color', cssVar('--map-void', '#e6e4df'));
      }
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
          '<span class="n">' + C.fmt.n(langs[id]) + '</span></div>';
      }).join('');

      // catalogue order: what it is, where, how much, over what period
      var bits = [];
      if (props.level) {
        bits.push(escapeHTML(String(props.level).charAt(0).toUpperCase() +
                             String(props.level).slice(1)));
      }
      if (props.region) bits.push(escapeHTML(props.region));

      var counts = [C.fmt.n(props.n) + (props.n === 1 ? ' inscription' : ' inscriptions')];
      if (props.rolled > 0) {
        counts.push('including ' + props.rolled +
          (props.rolled === 1 ? ' sub-findspot' : ' sub-findspots'));
      }
      var span = C.fmt.span(
        props.date_min === undefined ? null : props.date_min,
        props.date_max === undefined ? null : props.date_max);
      if (span !== 'undated') counts.push(span);

      var href = (CFG.baseurl || '') + '/place/' + props.slug + '/';
      return '<h3 class="pop-title">' + escapeHTML(props.name) + '</h3>' +
        (bits.length ? '<p class="pop-meta">' + bits.join(' &middot; ') + '</p>' : '') +
        '<p class="pop-meta">' + counts.join(' &middot; ') + '</p>' +
        (bars ? '<p class="pop-label">Languages attested</p>' +
                '<div class="pop-bars">' + bars + '</div>' : '') +
        '<a href="' + href + '">Open the findspot record</a>';
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
     * source, so adding a source to the catalogue needs no code here.
     *
     * Hue carries the family: blue water, brown relief and geology, ochre
     * ancient settlement, red excavation, grey-blue modern administration.
     * Values are dark enough to hold against a light topographic raster. */
    STYLES: {
      river:            { color: '#2f6a86', width: [0.5, 1.4, 2.6], label: 'line' },
      lake:             { color: '#2f5a6b', fill: 0.42 },
      coastline:        { color: '#557067', width: [0.4, 0.8, 1.2] },
      basin:            { color: '#3a6b57', fill: 0.10, casing: 0.5 },
      range:            { color: '#5a5241', fill: 0.10, label: 'area' },
      geology:          { color: '#7a5a3c', fill: 0.22 },
      province:         { color: '#7d6242', fill: 0.10, casing: 0.9, label: 'area' },
      'cultural-region':{ color: '#8f6a4a', fill: 0.10, casing: 0.8, label: 'area' },
      'urban-area':     { color: '#a8762a', fill: 0.30, casing: 0.8, label: 'area' },
      wall:             { color: '#a5652a', width: [0.8, 1.6, 3] },
      excavation:       { color: '#a8401b', fill: 0.28, casing: 1 },
      tomb:             { color: '#8a3d2c', fill: 0.35, casing: 0.6, radius: 2.2 },
      road:             { color: '#7d705b', width: [0.4, 0.9, 1.8], label: 'line' },
      'roman-road':     { color: '#9c7838', width: [0.5, 1.1, 2.2], label: 'line' },
      aqueduct:         { color: '#4d7f91', width: [0.4, 0.9, 1.6], label: 'line' },
      peak:             { color: '#6e6754', radius: 2.4, label: 'point' },
      admin:            { color: '#5a6d78', fill: 0.05, casing: 0.5 },
      'ancient-place':  { color: '#a8762a', radius: 2.4, label: 'point' },
      'ancient-footprint': { color: '#a8762a', fill: 0.20, casing: 0.7, label: 'area' },
      'archaeological-site': { color: '#557067', radius: 2.4, label: 'point' },
      feature:          { color: '#557067', radius: 2.2, label: 'point' }
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
            'text-halo-color': HALO,
            'text-halo-width': 1.6,
            'text-opacity': 0.95
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
