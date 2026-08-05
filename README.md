# Epigraphic Atlas of Ancient Italy

A Jekyll site that turns `data/database_preliminary.csv` into a queryable,
mappable atlas: a topographic map of every findspot, a boustrophedon
quarter-century timeline, a full record table, and an SQL console that runs the
generated SQLite database in the browser.

The CSV is the only file you edit. Everything else is generated.

---

## Run it locally

Prerequisites: Ruby 3.x with Bundler, Python 3.9 or newer. The only Python
dependency is PyYAML; everything else is the standard library.

On macOS and Linux the interpreter is `python3`. On Windows it is `python` or
`py`, so substitute accordingly in everything below.

```bash
# 1. dependencies
python3 -m pip install pyyaml
bundle install

# 2. build the database and the JSON the site reads
python3 scripts/build_db.py --site-pages

# 3. optional: rivers, peaks, ancient places (needs network, ~1 min)
python3 scripts/enrich_geo.py

# 4. serve
bundle exec jekyll serve --livereload
```

Windows, in Git Bash:

```bash
python -m pip install pyyaml
bundle install
python scripts/build_db.py --site-pages
python scripts/enrich_geo.py
bundle exec jekyll serve --livereload
```

Open <http://127.0.0.1:4000>.

If you have `make`:

```bash
make install     # pip + bundle install
make serve       # rebuilds anything stale, then serves
make geo         # enrichment
make rebuild     # force a full rebuild
make clean       # delete every generated file
```

To have the database rebuild itself whenever you touch the CSV, run this in a
second terminal:

```bash
python3 scripts/watch.py            # watcher only
python3 scripts/watch.py --jekyll   # watcher plus jekyll serve
```

The Makefile picks `python3` or `python` automatically. If it guesses wrong,
override it: `make serve PY=py`.

Jekyll's own watcher then picks up the regenerated files and reloads the page.

---

## What gets generated

```
data/database_preliminary.csv        <- the only input you edit
        |
        |  scripts/build_db.py
        v
db/atlas.sqlite                      normalised database
db/schema.sql                        DDL only
db/atlas.dump.sql                    complete SQL dump, schema + data
        |
        +-> assets/data/corpus.json      column-major records for the browser
        +-> assets/data/places.geojson   one point per findspot
        +-> assets/data/timeline.json    quarter-century counts by language
        +-> assets/data/atlas.sqlite     the copy sql.js queries in-browser
        +-> _data/atlas.yml              facets and statistics for Liquid
        +-> _sites/*.md                  one page per located findspot

scripts/enrich_geo.py -> assets/data/features.geojson
```

`build_db.py` stores the SHA-256 of the CSV and of `scripts/codebook.yml` in
`db/.build-stamp.json` and does nothing when neither has changed, so it is cheap
to put in front of every serve. `--force` overrides, `--check` exits 1 when a
rebuild is due (useful in CI).

### Database shape

Tables: `inscriptions`, `places`, `regions`, `languages`, `alphabets`,
`directions`, `uses`, `object_types`, `meta`.
Views: `v_inscriptions` (fully denormalised), `v_place_counts`, `v_timeline`.
Full-text: `inscriptions_fts` (FTS5, unicode61, diacritics folded) over
reference, findspot, notes, script and language.

`meta` records the source filename, its SHA-256, the detected encoding, the
build timestamp and the script version, so any figure on the site can be traced
back to an exact input file.

---

## Fill in the codebook

`Alphabet`, `Inscription.Use` and `Alphabet.Direction` are stored in the CSV as
bare integers with no key. Nothing here guesses what they mean. Open
`scripts/codebook.yml`, fill in the labels, then:

```bash
python3 scripts/build_db.py --force --site-pages
```

Labels propagate to the SQLite lookup tables, the facet rail, the map legend,
the record table and every findspot page. Codes still unlabelled render as
"Alphabet 24" and are listed as a warning on the About page.

The same file holds the per-language map colours.

---

## Geographic enrichment

```bash
python3 scripts/enrich_geo.py                        # naturalearth, pleiades, wikidata
python3 scripts/enrich_geo.py --sources naturalearth # hydrography only, a few seconds
python3 scripts/enrich_geo.py --sources overpass     # peaks, volcanoes, passes
python3 scripts/enrich_geo.py --offline              # rebuild from cache only
python3 scripts/enrich_geo.py --refresh              # ignore the cache
python3 scripts/enrich_geo.py --bbox 6 36 19 47      # explicit bounding box
```

| source | what it contributes | licence | in the default run |
| --- | --- | --- | --- |
| Natural Earth 10m | rivers, lake centrelines, lakes, named mountain ranges and physical regions | public domain | yes |
| Pleiades | ancient settlements, rivers, mountains, with stable URIs | CC-BY | yes |
| Wikidata SPARQL | archaeological sites and ancient settlements, with QIDs | CC0 | yes |
| Overpass (OpenStreetMap) | named peaks, volcanoes, mountain passes; optionally detailed rivers | ODbL | no, opt in |

Overpass is off by default. A single country-scale `out geom` request will time
out on the public endpoints, so the query is split by feature class and tiled,
one request per tile, with every tile cached separately. An interrupted run
resumes for free. Raise `--tile` for fewer, larger requests or lower it if tiles
still time out, and add `--osm-rivers` only if the Natural Earth hydrography is
too generalised for your purpose:

```bash
python3 scripts/enrich_geo.py --sources overpass --tile 1.0 --pause 2
python3 scripts/enrich_geo.py --sources overpass --osm-rivers --timeout 300
```

Responses are cached under `.cache/`, keyed by source and request. Each source
fails independently: a dead endpoint loses that layer and nothing else, and the
site renders fine without the file at all.

The bounding box is derived from the central 99% of located findspots rather
than the raw extremes, because the corpus contains a handful of records from
Africa, Pannonia and Germania Superior that would otherwise make the query
cover half of Europe.

---

## Maps

Base layers, all keyless, switchable from the map panel: OpenTopoMap, Esri World
Shaded Relief, Esri World Physical Map, OpenStreetMap standard. Relief comes
from AWS Terrain Tiles in terrarium encoding and drives both the hillshade layer
and the optional 3-D terrain toggle.

Check the tile services' usage policies before you deploy publicly.
OpenTopoMap in particular asks heavy users to run their own tile server. To swap
in your own, edit `atlas.basemaps` in `_config.yml`; nothing else needs to
change.

---

## Deploying

```bash
JEKYLL_ENV=production bundle exec jekyll build
```

Set `url` and `baseurl` in `_config.yml` first. For a GitHub Pages project site
the baseurl is `/repository-name`.

`assets/data/` totals about 8 MB, most of it the SQLite file the SQL console
downloads. If that is too much for your host, drop `assets/data/atlas.sqlite`
and the SQL console will report the file as missing while everything else keeps
working; or run `build_db.py --no-web-copy` so it is never produced.

Generated files are in `.gitignore` on the assumption that you build on deploy.
If you deploy from a branch instead, remove those lines so the artefacts are
committed.

---

## Layout

```
_config.yml            site and map configuration
Makefile               db / geo / serve / build / clean
scripts/
  build_db.py          CSV -> SQLite -> SQL -> JSON
  codebook.yml         code-to-label mapping, edit this
  enrich_geo.py        Overpass, Pleiades, Wikidata
  watch.py             rebuild on change
index.html             the atlas
pages/browse.html      record table and SQL console
pages/places.html      gazetteer
pages/about.md         provenance, dating conventions, licences
_layouts/              default, page, site
assets/js/             corpus, map, timeline, atlas, browse, place
assets/css/atlas.scss  design tokens and layout
```
