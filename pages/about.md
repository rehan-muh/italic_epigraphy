---
layout: page
permalink: /about/
nav: about
title: "About this atlas"
---

## Provenance of this build

Every figure on the site is generated from one source file, so any number here
can be traced back to an exact input.

<ul class="figure-line">
  <li><b>{% include number.html value=site.data.atlas.stats.inscriptions %}</b> inscriptions</li>
  <li><b>{% include number.html value=site.data.atlas.stats.dated %}</b> dated</li>
  <li><b>{% include number.html value=site.data.atlas.stats.located %}</b> located</li>
  <li><b>{{ site.data.atlas.stats.languages }}</b> languages</li>
  <li><b>{{ site.data.atlas.stats.alphabets }}</b> alphabet codes</li>
  <li><b>{{ site.data.atlas.stats.regions }}</b> regions</li>
</ul>

Source file `{{ site.data.atlas.build.source_csv }}`, SHA-256
`{{ site.data.atlas.build.source_sha256 | slice: 0, 16 }}…`, built
{{ site.data.atlas.build.built_at }} by build_db.py {{ site.data.atlas.build.script_version }}.
The corpus spans {{ site.data.atlas.stats.earliest }} to
{{ site.data.atlas.stats.latest }}.

## Dating

Two histograms are published, and the difference between them is not small.

**Start date** counts each record in the quarter its `Date.Start` falls in.
This is what epigraphic databases usually show, and it is badly misleading
here: {% include number.html value=site.data.atlas.stats.dated %} records are
dated, but a third of them carry intervals of a century or more that nearly all
begin on a round century edge, so the bins at 400 BC, 300 BC and 200 BC swallow
records that could belong anywhere in the following four hundred years. The bin
at 400–376 BC holds 5,540 inscriptions on this reckoning.

**Aoristic** spreads each record evenly across the quarters its interval
covers, so a record dated 400 to 1 BC contributes one sixteenth to each of
sixteen bins rather than a whole inscription to the first. The same 5,540
become 577. Both histograms sum to the same corpus. The whisker on each bar is
the standard deviation of the Poisson binomial those weights imply, computed
for whatever is currently selected: it is how much of the bar's height is an
artefact of the dating intervals rather than a fact about the corpus.

Years are signed, negative for BC, and there is no year zero. A BC quarter is
written descending, so the bin whose left edge is -150 displays as 150–126 BC;
AD quarters are written ascending. `Date.Start` and `Date.End` arrive already
binned to quarter centuries, and the unbinned values are carried through
untouched as `Date.Start.Orig` and `Date.End.Orig`.

The expansion is queryable in the SQL console as `v_aoristic` and
`v_timeline_aoristic`, against the `quarters` dimension table.

## Findspots, and what counts as one

The CSV keys a findspot by its name string, which leaves two problems that this
build resolves.

The same place is recorded under several names. *Palestrina* and *Palestrina /
Praeneste* are one place; so are *Este*, *Ateste (Este)* and *Este, Italy*.
Records are merged when they sit on the same coordinate to four decimals, about
eleven metres, **and** their names share a normalised form. The discarded
spellings are kept as aliases and shown on the findspot page.

The corpus also mixes three levels of granularity as if they were one.
Forty-two of the {{ site.data.atlas.hierarchy.coordinate_sharing }}
coordinate-sharing records are individual Monterozzi tombs carrying the
Tarquinia centroid. They now have a parent:
{{ site.data.atlas.hierarchy.children }} findspots are filed under
{{ site.data.atlas.hierarchy.roots }} parents, and the map folds them together
below zoom 10.5 rather than drawing forty-two dots on one pixel. Where every
member of a cluster was a sub-findspot of a site with no record of its own, the
parent is created empty rather than promoting an arbitrary tomb to stand for
the whole cemetery.

Two facts were also buried in the name string and are now columns:
`uncertain_place` for a trailing "(?)" and `found_written` for
"[found &amp; written]".

## Linked authorities

Findspots are matched to external gazetteers on name similarity and distance.
Every match records its method, its score and the distance in kilometres, so a
weak match is visible as a weak match rather than passing as fact. Trismegistos
publishes no reconciliation endpoint, so its rows are search links rather than
resolved identifiers and are labelled as such. Nothing is written back into the
corpus: the matches sit alongside it.

{% if site.data.atlas.links %}
<ul>
{% for l in site.data.atlas.links %}<li>{{ l[1] }} findspots linked to {{ l[0] }}</li>{% endfor %}
</ul>
{% endif %}

## What this edition does not settle

- **Some codes have no labels.** `Alphabet`, `Inscription.Use` and
  `Alphabet.Direction` are stored as bare integers with no key in the source,
  so where no label exists the site prints the code itself, as *Alphabet 24*,
  rather than guessing at a name.
- **A few characters are unrecoverable.** The source mixes several character
  encodings, and most of the damage is repaired field by field. A small number
  of strings had their non-ASCII bytes destroyed before the file was written;
  no decoder can recover those, and they are corrected by hand as they are
  found.
- **The column named `Pleiades` is not a Pleiades identifier.** It holds the
  values 0, 1, 2 and 11, so it is a flag of some kind, and it is carried
  through under the honest name `inscriptions.coord_flag`. Real Pleiades URIs
  come from the authority matching instead.
- **A handful of records are dated past the end of the axis**, almost certainly
  typographic errors in the source. They are clamped rather than allowed to
  stretch the timeline by forty near-empty bins, and the clamp is recorded in
  the build metadata.

## Sources and licences

Corpus: compiled by the author, with references as recorded in the source file.

Base maps and relief, all fetched at run time by the visitor's browser:
OpenTopoMap (CC-BY-SA, data © OpenStreetMap contributors), Esri World Shaded
Relief and World Physical Map, OpenStreetMap standard tiles, and AWS Terrain
Tiles for relief (Mapzen, SRTM, GMTED).

<div class="table-scroll">
<table>
  <caption class="visually-hidden">Geography layers available to the atlas, with licences.</caption>
  <thead>
    <tr>
      <th scope="col">Layer</th>
      <th scope="col">Group</th>
      <th scope="col" class="num">Features</th>
      <th scope="col" class="num">Size</th>
      <th scope="col">Licence</th>
    </tr>
  </thead>
  <tbody>
  {% for l in site.data.layers.layers %}
    <tr>
      <td>{{ l.name }}<span class="method">{{ l.attribution }}</span></td>
      <td>{{ l.group }}</td>
      <td class="num">{% include number.html value=l.features %}</td>
      <td class="num">{{ l.bytes | divided_by: 1024 }}&nbsp;kB</td>
      <td>{{ l.licence }}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>
</div>
