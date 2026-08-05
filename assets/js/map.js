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
      sources.geofeatures = { type: 'geojson', data: { type: 'FeatureCollection', features: [] } };

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

      // --- enriched geography, underneath the corpus -----------------------
      // named physical regions: Alps, Appennino Ligure, Calabria, and so on
      map.addLayer({
        id: 'geo-range-fill', type: 'fill', source: 'geofeatures',
        filter: ['==', ['get', 'kind'], 'range'],
        paint: { 'fill-color': '#6b6250', 'fill-opacity': 0.1 }
      });
      map.addLayer({
        id: 'geo-range-label', type: 'symbol', source: 'geofeatures',
        filter: ['in', ['get', 'kind'], ['literal', ['range', 'region']]],
        minzoom: 4.5,
        layout: {
          'text-field': ['get', 'name'],
          'text-font': ['Open Sans Italic'],
          'text-size': ['interpolate', ['linear'], ['zoom'], 5, 11, 9, 16],
          'text-letter-spacing': 0.22,
          'text-transform': 'uppercase',
          'text-max-width': 8,
          'text-optional': true
        },
        paint: {
          'text-color': '#b6ad97',
          'text-halo-color': '#10130f',
          'text-halo-width': 1.6,
          'text-opacity': 0.85
        }
      });

      map.addLayer({
        id: 'geo-lakes', type: 'fill', source: 'geofeatures',
        filter: ['==', ['get', 'kind'], 'lake'],
        paint: { 'fill-color': '#2f5a6b', 'fill-opacity': 0.42 }
      });
      map.addLayer({
        id: 'geo-rivers', type: 'line', source: 'geofeatures',
        filter: ['==', ['get', 'kind'], 'river'],
        paint: {
          'line-color': '#4d7f96',
          'line-opacity': 0.75,
          'line-width': ['interpolate', ['linear'], ['zoom'], 5, 0.5, 9, 1.4, 13, 2.6]
        }
      });
      map.addLayer({
        id: 'geo-rivers-label', type: 'symbol', source: 'geofeatures',
        filter: ['all', ['==', ['get', 'kind'], 'river'], ['has', 'name']],
        minzoom: 8,
        layout: {
          'symbol-placement': 'line',
          'text-field': ['get', 'name'],
          'text-font': ['Open Sans Italic'],
          'text-size': 11,
          'text-max-angle': 40
        },
        paint: { 'text-color': '#9fc4d4', 'text-halo-color': '#10130f', 'text-halo-width': 1.4 }
      });
      map.addLayer({
        id: 'geo-points', type: 'circle', source: 'geofeatures',
        filter: ['in', ['get', 'kind'],
                 ['literal', ['peak', 'volcano', 'pass', 'landmark', 'ancient-place', 'archaeological-site']]],
        minzoom: 6.5,
        paint: {
          'circle-radius': 2.4,
          'circle-color': ['match', ['get', 'kind'],
            'peak', '#c9c0ac', 'volcano', '#d3703f', 'pass', '#c9c0ac',
            'landmark', '#9db3a8', 'ancient-place', '#d3a04a', '#8fa39a'],
          'circle-opacity': 0.85
        }
      });
      map.addLayer({
        id: 'geo-points-label', type: 'symbol', source: 'geofeatures',
        filter: ['in', ['get', 'kind'],
                 ['literal', ['peak', 'volcano', 'pass', 'landmark', 'ancient-place', 'archaeological-site']]],
        minzoom: 8.5,
        layout: {
          'text-field': ['get', 'name'],
          'text-font': ['Open Sans Regular'],
          'text-size': 10,
          'text-offset': [0, 0.8],
          'text-anchor': 'top',
          'text-optional': true
        },
        paint: { 'text-color': '#b9b2a2', 'text-halo-color': '#10130f', 'text-halo-width': 1.4 }
      });

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
            color: dominant ? C.languageColor(Number(dominant)) : '#7a827d'
          }
        });
      });
      features.sort(function (a, b) { return b.properties.n - a.properties.n; });
      this.map.getSource('places').setData({ type: 'FeatureCollection', features: features });
      return features.length;
    },

    setGeoFeatures: function (fc) {
      if (!fc) return;
      this.map.getSource('geofeatures').setData(fc);
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

    setGeoVisible: function (on) {
      ['geo-range-fill', 'geo-range-label', 'geo-lakes', 'geo-rivers',
       'geo-rivers-label', 'geo-points', 'geo-points-label']
        .forEach(function (id) {
          if (this.map.getLayer(id)) {
            this.map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none');
          }
        }, this);
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
