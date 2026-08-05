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
        +--> assets/data/timeline.json    start-bin and aoristic counts
        +--> assets/data/atlas.sqlite     the copy sql.js queries in the browser
        +--> _data/atlas.yml              facets and statistics for Liquid
        +--> _sites/*.md                  one page per located findspot

scripts/link_authorities.py --> data/links.csv
        findspots reconciled against Pleiades, Wikidata and iDAI,
        folded back into the place_links table on the next build

scripts/enrich_geo.py --> assets/data/layers/*.geojson + layers.json
        one file per layer, declared in scripts/geosources.yml,
        downloaded by the browser only when the reader switches it on
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

Two histograms are published, and the difference between them is not small.

**Start date** counts each record in the quarter its `Date.Start` falls in. This
is what epigraphic databases usually show, and it is badly misleading here:
{{ site.data.atlas.stats.dated }} records are dated, but a third of them carry
intervals of a century or more that nearly all begin on a round century edge, so
the bins at 400 BC, 300 BC and 200 BC swallow records that could belong anywhere
in the following four hundred years. The bin at 400–376 BC holds 5,540
inscriptions on this reckoning.

**Aoristic** spreads each record evenly across the quarters its interval covers,
so a record dated 400 to 1 BC contributes one sixteenth to each of sixteen bins
rather than a whole inscription to the first. The same 5,540 become 577. Both
histograms sum to the same corpus. The whisker on each bar is the standard
deviation of the Poisson binomial those weights imply, computed for whatever is
currently selected: it is how much of the bar's height is an artefact of the
dating intervals rather than a fact about the corpus.

The expansion is queryable in the SQL console as `v_aoristic` and
`v_timeline_aoristic`, against the `quarters` dimension table.

`Date.Start` and `Date.End` arrive already binned to quarter centuries;
`Date.Start.Orig` and `Date.End.Orig` keep the unbinned values and are carried
into SQLite untouched. Years are signed, negative for BC, and there is no year
zero. A BC quarter is written descending, so the bin whose left edge is -150
displays as 150–126 BC; AD quarters are written ascending. The timeline uses
these bins directly, one cell per quarter, laid out boustrophedon.

The corpus spans {{ site.data.atlas.stats.earliest }} to
{{ site.data.atlas.stats.latest }}.

## Findspots, and what counts as one

The CSV keys a findspot by its name string, which leaves two problems that this
build resolves.

The same place is recorded under several names. *Palestrina* and *Palestrina /
Praeneste* are one place; so are *Este*, *Ateste (Este)* and *Este, Italy*.
Records are merged when they sit on the same coordinate to four decimals, about
eleven metres, **and** their names share a normalised form. The discarded
spellings are kept as aliases and shown on the findspot page.

The corpus also mixes three levels of granularity as if they were one. Forty-two
of the {{ site.data.atlas.hierarchy.coordinate_sharing }} coordinate-sharing
records are individual Monterozzi tombs carrying the Tarquinia centroid. They now
have a parent: {{ site.data.atlas.hierarchy.children }} findspots are filed under
{{ site.data.atlas.hierarchy.roots }} parents, and the map folds them together
below zoom 10.5 rather than drawing forty-two dots on one pixel. Where every
member of a cluster was a sub-findspot of a site with no record of its own, the
parent is created empty rather than promoting an arbitrary tomb to stand for the
whole cemetery.

Two facts were also buried in the name string and are now columns:
`uncertain_place` for a trailing "(?)" and `found_written` for
"[found &amp; written]".

## Character encoding

The CSV is not in one encoding. Valid UTF-8, double-encoded UTF-8 and raw cp1252
occur in the same file and sometimes in the same row, so choosing an encoding for
the file corrupts whichever fields are not in it. Each field is repaired on its
own by `scripts/textfix.py`. A further handful of strings had their non-ASCII
bytes replaced by U+FFFD before this file was written; no decoder can recover
those, so they are substituted by hand from `scripts/text_repairs.yml` and
anything left over is reported in the warnings above.

## Linked authorities

`scripts/link_authorities.py` matches findspots to external gazetteers on name
similarity and distance, writing `data/links.csv` for review before the next
build folds it in. Every row records the method, the score and the distance, so a
weak match is visible as a weak match rather than passing as fact. Nothing is
written back into the corpus: correcting a match means editing that one file.

{% if site.data.atlas.links %}
<ul>
{% for l in site.data.atlas.links %}<li>{{ l[1] }} findspots linked to {{ l[0] }}</li>{% endfor %}
</ul>
{% endif %}

## Codes that still need labels

`Alphabet`, `Inscription.Use` and `Alphabet.Direction` are stored as integers
with no key in the CSV. Until `scripts/codebook.yml` is filled in, the site
falls back to labels like *Alphabet 24* rather than guessing. Fill the file in
and run `python3 scripts/build_db.py --force`; the labels propagate to the map
legend, the facet rail, the SQLite lookup tables and every place page.

`Inscription.Use` and `Object.Type` each hold two coding schemes at once: bare
integers from one annotation pass and English strings such as *cippus* or
*prob. votive* from another. They are kept apart by a `scheme` column rather than
presented as one vocabulary.

The column named `Pleiades` holds the values 0, 1, 2 and 11, so it is a flag of
some kind and not a Pleiades identifier. It is carried through under the honest
name `inscriptions.coord_flag`. Real Pleiades URIs come from
`link_authorities.py` instead.

## Sources and licences

Corpus: your own compilation, with references as recorded in the CSV.

Base maps and geography, all fetched at run time by the visitor's browser:
OpenTopoMap (CC-BY-SA, data © OpenStreetMap contributors),
Esri World Shaded Relief and World Physical Map,
OpenStreetMap standard tiles,
AWS Terrain Tiles for relief (Mapzen, SRTM, GMTED).

Enrichment: one file per layer, each with its own licence, kept apart so that a
share-alike obligation on one does not attach to the corpus or to the others.

<ul>
{% for l in site.data.layers.layers %}
<li><b>{{ l.name }}</b> ({{ l.group }}) &middot; {{ l.features }} features,
{{ l.bytes | divided_by: 1024 }} kB &middot; {{ l.attribution }} &middot;
<i>{{ l.licence }}</i></li>
{% endfor %}
</ul>

The AWMC and OpenStreetMap layers are ODbL, which is share-alike. Pleiades is
CC-BY, Itiner-e CC BY 4.0, Wikidata CC0, Natural Earth public domain.

Before publishing, check that the tile services you keep are ones you are
entitled to use at your expected traffic. OpenTopoMap in particular asks that
heavy users run their own tile server.
