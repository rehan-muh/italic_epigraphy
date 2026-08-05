---
layout: page
permalink: /about/
nav: about
title: "About this atlas"
lede: "How the site is built, what the data does and does not say, and what to fix before publishing."
---

## The pipeline

The CSV is the only thing you edit. Everything else is generated.

```
data/database_preliminary.csv
        |
        |  scripts/build_db.py
        v
db/atlas.sqlite  db/schema.sql  db/atlas.dump.sql
        |
        +--> assets/data/corpus.json      column-major records for the browser
        +--> assets/data/places.geojson   one point per findspot
        +--> assets/data/timeline.json    quarter-century counts
        +--> assets/data/atlas.sqlite     the copy sql.js queries in the browser
        +--> _data/atlas.yml              facets and statistics for Liquid
        +--> _sites/*.md                  one page per located findspot

scripts/enrich_geo.py --> assets/data/features.geojson
        rivers, lakes and named ranges from Natural Earth
        ancient places from Pleiades
        archaeological sites from Wikidata
        peaks and passes from OpenStreetMap (opt in)
```

`build_db.py` hashes the CSV and the codebook and skips the rebuild when neither
has changed, so it is cheap to put in front of every `jekyll serve`.

## Provenance of this build

<div class="stat-grid">
  <div class="stat"><span class="n">{{ site.data.atlas.stats.inscriptions }}</span><span class="k">Inscriptions</span></div>
  <div class="stat"><span class="n">{{ site.data.atlas.stats.dated }}</span><span class="k">Dated</span></div>
  <div class="stat"><span class="n">{{ site.data.atlas.stats.located }}</span><span class="k">Located</span></div>
  <div class="stat"><span class="n">{{ site.data.atlas.stats.languages }}</span><span class="k">Languages</span></div>
  <div class="stat"><span class="n">{{ site.data.atlas.stats.alphabets }}</span><span class="k">Alphabet codes</span></div>
  <div class="stat"><span class="n">{{ site.data.atlas.stats.regions }}</span><span class="k">Regions</span></div>
</div>

Source file `{{ site.data.atlas.build.source_csv }}`, decoded as
`{{ site.data.atlas.build.source_encoding }}`, SHA-256
`{{ site.data.atlas.build.source_sha256 | slice: 0, 16 }}…`, built
{{ site.data.atlas.build.built_at }} by build_db.py {{ site.data.atlas.build.script_version }}.

{% if site.data.atlas.warnings and site.data.atlas.warnings.size > 0 %}
### Warnings raised during the last build

<div class="notice warn">
<ul>
{% for w in site.data.atlas.warnings %}<li>{{ w }}</li>{% endfor %}
</ul>
</div>
{% endif %}

## Dating

`Date.Start` and `Date.End` arrive already binned to quarter centuries;
`Date.Start.Orig` and `Date.End.Orig` keep the unbinned values and are carried
into SQLite untouched. Years are signed, negative for BC, and there is no year
zero. A BC quarter is written descending, so the bin whose left edge is -150
displays as 150–126 BC; AD quarters are written ascending. The timeline uses
these bins directly, one cell per quarter, laid out boustrophedon.

The corpus spans {{ site.data.atlas.stats.earliest }} to
{{ site.data.atlas.stats.latest }}.

## Codes that still need labels

`Alphabet`, `Inscription.Use` and `Alphabet.Direction` are stored as integers
with no key in the CSV. Until `scripts/codebook.yml` is filled in, the site
falls back to labels like *Alphabet 24* rather than guessing. Fill the file in
and run `python3 scripts/build_db.py --force`; the labels propagate to the map
legend, the facet rail, the SQLite lookup tables and every place page.

The column named `Pleiades` holds the values 0, 1, 2 and 11, so it is a small
code set rather than a Pleiades place identifier. It is carried through as
`inscriptions.pleiades` and is not used to link out. If you have real Pleiades
URIs elsewhere, adding them to the CSV would let `enrich_geo.py` join findspots
to the gazetteer directly instead of matching on the bounding box.

## Sources and licences

Corpus: your own compilation, with references as recorded in the CSV.

Base maps and geography, all fetched at run time by the visitor's browser:
OpenTopoMap (CC-BY-SA, data © OpenStreetMap contributors),
Esri World Shaded Relief and World Physical Map,
OpenStreetMap standard tiles,
AWS Terrain Tiles for relief (Mapzen, SRTM, GMTED).

Enrichment: Natural Earth 10m (public domain), the Pleiades gazetteer
(CC-BY, Institute for the Study of the Ancient World), Wikidata (CC0), and
optionally OpenStreetMap via Overpass (ODbL).

Before publishing, check that the tile services you keep are ones you are
entitled to use at your expected traffic. OpenTopoMap in particular asks that
heavy users run their own tile server.
