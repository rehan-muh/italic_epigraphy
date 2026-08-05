/* browse.js - record table plus an in-browser SQL console over the generated
 * SQLite file. sql.js compiles SQLite to WebAssembly, so the query runs on the
 * visitor's machine against a copy of db/atlas.sqlite. */
(function (global) {
  'use strict';

  var C = global.Corpus;
  var base = (global.ATLAS_CONFIG && global.ATLAS_CONFIG.baseurl) || '';
  var el = function (id) { return document.getElementById(id); };
  var PAGE = 100;

  function esc(s) {
    return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  // =======================================================================
  // Table
  // =======================================================================

  var view = { rows: [], page: 0, sort: null, dir: 1 };

  function fillSelect(id, lookup) {
    var sel = el(id), table = C.lookups[lookup] || {};
    Object.keys(table)
      .sort(function (a, b) { return String(table[a]).localeCompare(String(table[b])); })
      .forEach(function (k) {
        var o = document.createElement('option');
        o.value = k;
        o.textContent = table[k];
        sel.appendChild(o);
      });
  }

  function cell(i, key) {
    var c = C.col;
    switch (key) {
      case 'ref':    return c.ref[i] || '';
      case 'place':  return C.label('places', c.place[i]) || '';
      case 'region': return C.label('regions', c.region[i]) || '';
      case 'lang':   return C.label('languages', c.lang[i]) || '';
      case 'alpha':  return C.label('alphabets', c.alpha[i]) || '';
      case 'dir':    return C.label('directions', c.dir[i]) || '';
      case 'ds':     return C.fmt.year(c.ds[i]);
      case 'de':     return C.fmt.year(c.de[i]);
      default:       return '';
    }
  }

  function applyTableFilters() {
    var patch = { q: el('t-q').value };
    var lang = el('t-lang').value, region = el('t-region').value;
    patch.lang = lang ? new Set([Number(lang)]) : null;
    patch.region = region ? new Set([Number(region)]) : null;
    Object.keys(patch).forEach(function (k) { C.state[k] = patch[k]; });
    view.rows = C.select();
    view.page = 0;
    sortRows();
    renderTable();
  }

  function sortRows() {
    if (!view.sort) return;
    var key = view.sort, dir = view.dir;
    var numeric = key === 'ds' || key === 'de';
    view.rows.sort(function (a, b) {
      var x, y;
      if (numeric) {
        x = C.col[key][a]; y = C.col[key][b];
        if (x === null) return 1;
        if (y === null) return -1;
        return (x - y) * dir;
      }
      x = cell(a, key); y = cell(b, key);
      return x.localeCompare(y) * dir;
    });
  }

  function renderTable() {
    var body = el('t-table').querySelector('tbody');
    var start = view.page * PAGE;
    var slice = view.rows.slice(start, start + PAGE);
    var keys = ['ref', 'place', 'region', 'lang', 'alpha', 'dir', 'ds', 'de'];

    body.innerHTML = slice.map(function (i) {
      var slug = (C.lookups.place_slugs || {})[C.col.place[i]];
      return '<tr>' + keys.map(function (k) {
        var v = esc(cell(i, k));
        if (k === 'place' && slug) {
          v = '<a href="' + base + '/place/' + slug + '/">' + v + '</a>';
        }
        return '<td' + (k === 'ds' || k === 'de' ? ' class="num"' : '') + '>' + v + '</td>';
      }).join('') + '</tr>';
    }).join('');

    el('t-status').textContent = C.fmt.n(view.rows.length) + ' of ' +
      C.fmt.n(C.n) + ' records match';
    var pages = Math.max(1, Math.ceil(view.rows.length / PAGE));
    el('t-page').textContent = 'Page ' + (view.page + 1) + ' of ' + C.fmt.n(pages);
    el('t-prev').disabled = view.page === 0;
    el('t-next').disabled = view.page >= pages - 1;
  }

  function downloadCSV() {
    var keys = ['ref', 'place', 'region', 'lang', 'alpha', 'dir', 'ds', 'de'];
    var head = ['Reference', 'Findspot', 'Region', 'Language', 'Alphabet', 'Direction',
                'DateStart', 'DateEnd'];
    var lines = [head.join(',')];
    view.rows.forEach(function (i) {
      lines.push(keys.map(function (k) {
        var v = k === 'ds' || k === 'de' ? (C.col[k][i] === null ? '' : C.col[k][i]) : cell(i, k);
        v = String(v);
        return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
      }).join(','));
    });
    var blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'epigraphic-atlas-selection.csv';
    a.click();
    URL.revokeObjectURL(a.href);
  }

  function wireTable() {
    fillSelect('t-lang', 'languages');
    fillSelect('t-region', 'regions');

    var debounce;
    el('t-q').addEventListener('input', function () {
      clearTimeout(debounce);
      debounce = setTimeout(applyTableFilters, 200);
    });
    el('t-lang').addEventListener('change', applyTableFilters);
    el('t-region').addEventListener('change', applyTableFilters);
    el('t-csv').addEventListener('click', downloadCSV);

    el('t-prev').addEventListener('click', function () {
      view.page = Math.max(0, view.page - 1); renderTable();
    });
    el('t-next').addEventListener('click', function () {
      view.page++; renderTable();
    });

    el('t-table').querySelectorAll('th[data-sort]').forEach(function (th) {
      th.style.cursor = 'pointer';
      th.addEventListener('click', function () {
        var key = th.dataset.sort;
        view.dir = view.sort === key ? -view.dir : 1;
        view.sort = key;
        sortRows();
        view.page = 0;
        renderTable();
      });
    });
  }

  // =======================================================================
  // SQL console
  // =======================================================================

  var SAMPLES = [
    ['Inscriptions per language',
     'SELECT language, COUNT(*) AS n\nFROM v_inscriptions\nGROUP BY language\nORDER BY n DESC;'],
    ['Twenty richest findspots',
     'SELECT name, region, n, date_min, date_max\nFROM v_place_counts\nWHERE latitude IS NOT NULL\nORDER BY n DESC\nLIMIT 20;'],
    ['Etruscan and Latin per quarter century',
     "SELECT quarter_start,\n       SUM(language = 'Etruscan') AS etruscan,\n       SUM(language = 'Latin')    AS latin\nFROM v_inscriptions\nWHERE quarter_start IS NOT NULL\nGROUP BY quarter_start\nORDER BY quarter_start;"],
    ['Alphabet by region, cross-tabulated',
     'SELECT region, alphabet, COUNT(*) AS n\nFROM v_inscriptions\nWHERE region IS NOT NULL\nGROUP BY region, alphabet\nHAVING n > 20\nORDER BY region, n DESC;'],
    ['Places where two or more languages are attested',
     'SELECT place, COUNT(DISTINCT language) AS languages, COUNT(*) AS n\nFROM v_inscriptions\nWHERE place IS NOT NULL\nGROUP BY place\nHAVING languages > 1\nORDER BY languages DESC, n DESC\nLIMIT 40;'],
    ['Full-text search across references and notes',
     "SELECT i.reference, p.name AS place, i.notes\nFROM inscriptions_fts f\nJOIN inscriptions i ON i.pk = f.rowid\nLEFT JOIN places p ON p.id = i.place_id\nWHERE inscriptions_fts MATCH 'clusium OR volsinii'\nLIMIT 50;"],
    ['Earliest dated record per region',
     'SELECT region, MIN(date_start) AS earliest, COUNT(*) AS n\nFROM v_inscriptions\nWHERE date_start IS NOT NULL\nGROUP BY region\nORDER BY earliest;'],
    ['Provenance of the build',
     'SELECT key, value FROM meta;']
  ];

  var db = null;

  function runQuery() {
    var sql = el('sql').value.trim();
    if (!sql || !db) return;
    var head = el('sql-table').querySelector('thead');
    var body = el('sql-table').querySelector('tbody');
    var t0 = performance.now();
    try {
      var res = db.exec(sql);
      if (!res.length) {
        head.innerHTML = '';
        body.innerHTML = '';
        el('sql-status').textContent = 'Query ran, no rows returned.';
        return;
      }
      var last = res[res.length - 1];
      head.innerHTML = '<tr>' + last.columns.map(function (c) {
        return '<th>' + esc(c) + '</th>';
      }).join('') + '</tr>';
      body.innerHTML = last.values.slice(0, 2000).map(function (row) {
        return '<tr>' + row.map(function (v) {
          var num = typeof v === 'number';
          return '<td' + (num ? ' class="num"' : '') + '>' + esc(v) + '</td>';
        }).join('') + '</tr>';
      }).join('');
      var ms = (performance.now() - t0).toFixed(0);
      el('sql-status').textContent = C.fmt.n(last.values.length) + ' rows in ' + ms + ' ms' +
        (last.values.length > 2000 ? ' (first 2000 shown)' : '');
    } catch (err) {
      head.innerHTML = '';
      body.innerHTML = '<tr><td style="color:var(--cinnabar)">' + esc(err.message) + '</td></tr>';
      el('sql-status').textContent = 'SQLite rejected the query.';
    }
  }

  function wireSQL() {
    el('sql-samples').innerHTML = SAMPLES.map(function (s, i) {
      return '<button class="chip" data-sample="' + i + '">' + esc(s[0]) + '</button>';
    }).join('');
    el('sql-samples').addEventListener('click', function (e) {
      var b = e.target.closest('[data-sample]');
      if (!b) return;
      el('sql').value = SAMPLES[Number(b.dataset.sample)][1];
      runQuery();
    });

    el('sql-run').addEventListener('click', runQuery);
    el('sql').addEventListener('keydown', function (e) {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); runQuery(); }
    });

    if (typeof initSqlJs !== 'function') {
      el('sql-status').textContent =
        'sql.js could not be loaded from the CDN. The record table above still works.';
      return;
    }

    Promise.all([
      initSqlJs({ locateFile: function (f) {
        return 'https://cdn.jsdelivr.net/npm/sql.js@1.10.3/dist/' + f;
      } }),
      fetch(base + '/assets/data/atlas.sqlite').then(function (r) {
        if (!r.ok) throw new Error('atlas.sqlite is missing. Run scripts/build_db.py.');
        return r.arrayBuffer();
      })
    ]).then(function (out) {
      db = new out[0].Database(new Uint8Array(out[1]));
      el('sql-run').disabled = false;
      el('sql-status').textContent = 'Ready. Ctrl or Cmd + Enter runs the query.';
      runQuery();
    }).catch(function (err) {
      el('sql-status').textContent = err.message;
    });
  }

  // =======================================================================

  C.load().then(function () {
    wireTable();
    applyTableFilters();
    wireSQL();
  }).catch(function (err) {
    el('t-status').textContent = 'Could not load the corpus: ' + err.message +
      '. Run python3 scripts/build_db.py --force.';
  });

})(window);
