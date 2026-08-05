# Epigraphic Atlas of Ancient Italy

A Jekyll site that turns `data/database_preliminary.csv` into a queryable,
mappable atlas: a topographic map of every findspot, a boustrophedon
quarter-century timeline with interval-aware dating, a toggleable stack of
external geography, a full record table, and an SQL console that runs the
generated SQLite database in the browser.

The CSV is the only file you edit. Everything else is generated.

Three things are worth knowing before reading the rest:

- **The CSV mixes character encodings**, so it is read as latin-1 and repaired
  field by field. See "Character encoding" below.
- **The timeline defaults to aoristic counts**, not start-date bins. A third of
  the corpus carries dating intervals of a century or more, so binning on
  `Date.Start` piles 5,540 inscriptions into 400-376 BC that could belong
  anywhere in the following four centuries.
- **The geography is per-layer and lazy.** Nothing but the corpus and a small
  manifest is downloaded until the reader switches a layer on.

---

## Run it locally

Prerequisites: Ruby 3.x with Bundler, Python 3.9 or newer. The Python
dependencies are PyYAML and certifi; everything else is the standard library.
certifi is only needed for the downloads, and only on networks whose certificate
store is missing an intermediate.

On macOS and Linux the interpreter is `python3`. On Windows it is `python` or
`py`, so substitute accordingly in everything below.

```bash
# 1. dependencies
python3 -m pip install pyyaml certifi
bundle install

# 2. build the database and the JSON the site reads
python3 scripts/build_db.py --site-pages

# 3. optional: the default geography layers (needs network, ~1 min)
python3 scripts/enrich_geo.py

# 4. optional: reconcile findspots against the gazetteers, then rebuild
python3 scripts/link_authorities.py
python3 scripts/build_db.py --force --site-pages

# 5. serve
bundle exec jekyll serve --livereload
```

Windows, in Git Bash:

```bash
python -m pip install pyyaml certifi
bundle install
python scripts/build_db.py --site-pages
python scripts/enrich_geo.py
bundle exec jekyll serve --livereload
```

On this machine the launcher is `py -3.12`, so substitute that for `python3`
everywhere below, or pass it once: `make serve PY="py -3.12"`.

Open <http://127.0.0.1:4000>.

If you have `make`:

```bash
make install     # pip + bundle install
make serve       # rebuilds anything stale, then serves
make geo         # the default geography layers
make geo-all     # every declared layer, including the slow OSM ones
make layers      # list what is declared
make links       # reconcile findspots against the gazetteers
make encoding    # audit the character encoding of the CSV
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
data/links.csv                       <- reviewable, written by link_authorities.py
        |
        |  scripts/build_db.py
        v
db/atlas.sqlite                      normalised database
db/schema.sql                        DDL only
db/atlas.dump.sql                    complete SQL dump, schema + data
        |
        +-> assets/data/corpus.json      column-major records for the browser
        +-> assets/data/places.geojson   one point per findspot
        +-> assets/data/timeline.json    start-bin and aoristic counts
        +-> assets/data/atlas.sqlite     the copy sql.js queries in-browser
        +-> _data/atlas.yml              facets and statistics for Liquid
        +-> _sites/*.md                  one page per located findspot

scripts/enrich_geo.py -> assets/data/layers/*.geojson
                         assets/data/layers.json  (and a copy in _data/)
```

`build_db.py` stores the SHA-256 of the CSV and of `scripts/codebook.yml` in
`db/.build-stamp.json` and does nothing when neither has changed, so it is cheap
to put in front of every serve. `--force` overrides, `--check` exits 1 when a
rebuild is due (useful in CI).

### Database shape

Tables: `inscriptions`, `places`, `place_links`, `quarters`, `regions`,
`languages`, `alphabets`, `directions`, `uses`, `object_types`, `meta`.
Views: `v_inscriptions` (fully denormalised), `v_place_counts`, `v_place_tree`,
`v_timeline`, `v_aoristic`, `v_timeline_aoristic`.
Full-text: `inscriptions_fts` (FTS5, unicode61, diacritics folded).

`meta` records the source filename, its SHA-256, the build timestamp and the
script version, so any figure on the site can be traced back to an exact input.

---

## Character encoding

The CSV is not in one encoding. All four of these occur, sometimes in one row:

| bytes | example | what it is |
| --- | --- | --- |
| `c3 a9` | Vénète | valid UTF-8 |
| `c3 83 c2 a8` | Magrè | UTF-8 encoded twice |
| `e8` | Magrè | raw cp1252 |
| `ef bf bd` | Città | already destroyed, U+FFFD |

Opening the file with any single encoding corrupts whichever fields are not in
it, and the first three regimes are why place slugs used to come out as
`citti12-di-castello` and `tomba-frani12ois`. The file is therefore read as
latin-1, which maps bytes to codepoints without loss, and every field is
repaired individually by `scripts/textfix.py`: 667 fields on the current data.

The fourth case is unrecoverable, because the bytes were replaced before this
file was written. Those strings are substituted by hand in
`scripts/text_repairs.yml`. To find new ones after the CSV changes:

```bash
python3 scripts/textfix.py
```

Anything still unrecovered is reported as a build warning and shown on the About
page rather than being silently published.

---

## Dating

Two histograms are produced and the reader can switch between them.

`start` bins each record on the quarter its `Date.Start` falls in. `aoristic`
spreads each record evenly across the quarters its interval covers. The
difference is not cosmetic:

| quarter | start bin | aoristic |
| --- | --- | --- |
| 400-376 BC | 5,540 | 577 |
| 300-276 BC | 1,495 | 977 |
| 275-251 BC | 166 | 856 |
| 125-101 BC | 528 | 1,281 |

Both sum to the corpus. The whisker on each bar is the analytic standard
deviation of the Poisson binomial the weights imply, recomputed for whatever is
currently selected, so no simulation runs in the browser.

The expansion is queryable rather than materialised: a `quarters` dimension
table plus two ordinal columns on `inscriptions` give the `v_aoristic` view,
which keeps 140,000 rows out of the file the browser downloads.

A handful of records are dated past the end of the axis, almost certainly typos
(one reads 200 BC to AD 799). They are clamped rather than allowed to stretch
the timeline by forty near-empty bins, and the clamp is recorded in `meta`.

---

## Findspots

The CSV keys a findspot by its name string, which conflates two different
things.

**Duplicates.** *Palestrina* and *Palestrina / Praeneste* are one place. Records
merge when they share a coordinate to four decimals **and** a normalised name
form; the discarded spellings survive as `places.aliases`. 223 records merged on
the current data.

**Granularity.** Cities, necropoleis and individual tombs sit in one flat list,
and 699 records share only 222 distinct coordinates. Each cluster now has a
parent, so the map can fold 42 Monterozzi tombs into Monterozzi below zoom 10.5
instead of drawing them on one pixel. Where no member of a cluster was a
site-level record, the parent is synthesised empty rather than promoting an
arbitrary tomb to stand for the cemetery.

Two flags were also buried in the name string and are now columns:
`uncertain_place` for a trailing "(?)", `found_written` for
"[found & written]".

Slugs are deterministic: reordering the CSV no longer renumbers permalinks.

---

## Linked authorities

```bash
python3 scripts/link_authorities.py                  # every authority
python3 scripts/link_authorities.py --only pleiades  # just one
python3 scripts/link_authorities.py --min-score 0.8  # stricter
python3 scripts/link_authorities.py --report         # print, write nothing
python3 scripts/link_authorities.py --periods        # PeriodO, into _data/
```

Matching is on name similarity and distance, and it writes `data/links.csv` for
review; nothing is written back into the corpus. Every row carries the method,
the score and the distance in kilometres, so a weak match reads as a weak match.
Correct or delete rows in that file and re-run `build_db.py`.

| authority | how | result on the current data |
| --- | --- | --- |
| Pleiades | name and distance against the places dump | 823 of 1,750 findspots |
| Wikidata | name and distance against a SPARQL box query | needs a first run |
| iDAI.gazetteer | one cached search per findspot name | needs a first run |
| Trismegistos | deterministic search links, keyed on the Pleiades id where one matched | every findspot |
| PeriodO | period definitions covering Italy, into `_data/periods.yml` | needs a first run |

Trismegistos publishes no reconciliation endpoint and no place dump, so its rows
are marked `method: search` and rendered with a dashed border. They are an honest
pointer, not a resolved identifier.

Reference-level links are a different problem and partly solved for free: 7,052
of the 22,003 reference strings are EDCS identifiers, which resolve directly.

---

## Geography layers

Every layer is declared in `scripts/geosources.yml`. Adding a source is an edit
to that file, not a code change.

```bash
python3 scripts/enrich_geo.py --list                  # what is declared
python3 scripts/enrich_geo.py                         # the default layers
python3 scripts/enrich_geo.py --all                   # everything
python3 scripts/enrich_geo.py --only awmc-urban,pleiades-polygons
python3 scripts/enrich_geo.py --group "Excavated footprints"
python3 scripts/enrich_geo.py --offline               # rebuild from cache
python3 scripts/enrich_geo.py --refresh               # ignore the cache
```

| group | layers | licence |
| --- | --- | --- |
| Hydrography | Natural Earth rivers and lakes, AWMC inland water, HydroBASINS catchments | public domain, ODbL |
| Relief | Natural Earth ranges and physical regions, peaks and passes, OSM summits | public domain, ODbL |
| Ancient geography | Pleiades points **and polygons**, AWMC shoreline, urban areas, city walls, Roman territory 60 BC and provinces AD 200, Barrington regional names, Wikidata sites | CC-BY, ODbL, CC0 |
| Movement | AWMC roads, aqueducts and canals, Itiner-e Roman roads | ODbL, CC BY 4.0 |
| Excavated footprints | OSM archaeological sites and tombs, as ways and relations | ODbL |
| Modern reference | ISTAT comuni and province | CC BY 4.0 |
| Physical setting | bedrock geology, dropped into `.cache/` by hand | check the publisher |

Some layers need a manual download, either because the publisher puts them
behind a form or because their host will not verify on your network. Save the
file under the matching name in `.cache/` and re-run; it is picked up
automatically.

| layer | file | where from |
| --- | --- | --- |
| Itiner-e | `.cache/local-itinere.geojson` | `https://doi.org/10.5281/zenodo.17122148` |
| HydroBASINS | `.cache/local-hydrobasins.geojson` | `https://www.hydrosheds.org/products` |
| Geology | `.cache/local-geology.geojson` | ISPRA |

### When a download fails

**`CERTIFICATE_VERIFY_FAILED: self-signed certificate in certificate chain`**
means the certificate store Python is using does not have the chain that host
presents, which is usual on Windows and on networks that inspect TLS. In order:

```bash
py -3.12 -m pip install certifi                    # used automatically once present
py -3.12 scripts/enrich_geo.py --ca-bundle path\to\root.pem --only itinere-roads
py -3.12 scripts/enrich_geo.py --insecure --only itinere-roads
```

`--insecure` skips verification altogether and says so in the log. Prefer the
first two.

**`HTTP 429 Too Many Requests` from Overpass** is normal on a run of this size.
A tile that is refused rotates to the next endpoint rather than sleeping on the
one that just said no; if all three are busy the script waits out the shortest
`Retry-After` they gave and tries once more. Whatever still fails is reported at
the end, and every successful tile is cached separately, so re-running the same
command fills the gaps and costs nothing for the rest. If it keeps happening:

```bash
py -3.12 scripts/enrich_geo.py --only osm-archaeology --pause 5 --tile 2.5
py -3.12 scripts/enrich_geo.py --only osm-archaeology --endpoint https://overpass.kumi.systems/api/interpreter
```

**Keeping it fast.** Each layer is written to its own file and the browser
fetches it only when the reader ticks it. Geometry is thinned at build time with
Douglas-Peucker and coordinates rounded to five decimals, which drops between a
quarter and two thirds of the vertices depending on the source. The default set
is under 200 kB; the whole catalogue is a few megabytes on disk and none of it
is loaded until asked for. The SQLite file behind the SQL console is likewise
fetched on the first query rather than on page load.

**Licences.** Layers are kept in separate files precisely so that the ODbL
share-alike layers (AWMC, OpenStreetMap) do not drag the CC-BY and public-domain
ones, or the corpus, along with them. The panel shows the licence per layer and
the About page lists them all.

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
