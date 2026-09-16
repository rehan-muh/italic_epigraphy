# What to fix before publishing

Working notes for whoever maintains the corpus. None of this belongs on the
published site: the About page carries what a reader needs to interpret the
data, and this file carries what an editor needs to improve it.

For how the build works at all, see [README.md](README.md).

---

## Read the build warnings

`build_db.py` prints a warning for every judgement it had to make. Run it and
read the output:

```bash
python3 scripts/build_db.py --force
```

They are also written to `_data/atlas.yml` under `warnings:`, which is the
quickest way to see them without a rebuild. On the current data they cover the
four items below.

## Codes that still have no labels

`Alphabet`, `Inscription.Use` and `Alphabet.Direction` are stored as bare
integers with no key in the CSV, and `Object.Type` is partly coded the same
way. Until `scripts/codebook.yml` is filled in the site prints the code itself,
for example *Alphabet 24*, rather than guessing at a name.

Currently unlabelled: 10 use codes and 31 object-type codes.

```bash
# fill in scripts/codebook.yml, then
python3 scripts/build_db.py --force
```

The labels propagate on the next build to the map legend, the facet rail, the
SQLite lookup tables and every findspot page. Nothing else needs changing.

Note that `Inscription.Use` and `Object.Type` each hold two coding schemes at
once: bare integers from one annotation pass, and English strings such as
*cippus* or *prob. votive* from another. They are kept apart by a `scheme`
column rather than merged into one vocabulary, so a label is only ever needed
for the integer scheme.

## Strings the encoding repair cannot recover

Most of the mixed-encoding damage in the CSV is repaired field by field, 667
fields on the current data. A handful of strings had their non-ASCII bytes
replaced by U+FFFD *before* the file was written, so no decoder can recover
them; they have to be corrected by hand.

Currently unrecovered: 1 field, in the string `a e v <?> c`.

```bash
python3 scripts/textfix.py          # lists anything still broken
```

Add the correct form to `scripts/text_repairs.yml` and rebuild.

## Authority runs still pending

`link_authorities.py` has only been run against Pleiades so far.

```bash
python3 scripts/link_authorities.py --only wikidata
python3 scripts/link_authorities.py --only idai
python3 scripts/link_authorities.py --periods
python3 scripts/build_db.py --force --site-pages
```

Results land in `data/links.csv` for review before the next build folds them
in. Every row carries the method, the score and the distance in kilometres, so
a weak match is visible as a weak match: correct or delete rows in that file
rather than editing the corpus.

Until a run happens, no findspot page shows an authority record, so the
authority table on those pages is untested against real rows.

## Records with a surrogate id

74 rows had an empty `ID_Internal` and were given surrogate ids so they could
be keyed at all. They are real records and are published normally, but they
cannot be cited by their original identifier because they never had one.

## Before the site goes public

- **Tile services.** OpenTopoMap, the two Esri layers, OpenStreetMap standard
  and the AWS terrain tiles are all fetched at run time by the visitor's
  browser. Check each one's usage policy against your expected traffic before
  publishing; OpenTopoMap in particular asks heavy users to run their own tile
  server. To swap in your own, edit `atlas.basemaps` in `_config.yml`.
- **Payload.** `assets/data/` is about 8 MB, most of it the SQLite file the SQL
  console downloads on the first query. Drop `assets/data/atlas.sqlite`, or
  build with `--no-web-copy`, if that is too much for the host; everything
  except the SQL console keeps working.
- **URL and baseurl.** Set both in `_config.yml`. For a GitHub Pages project
  site the baseurl is `/repository-name`.
- **The economy page.** `/economy/` (`pages/economy.html`,
  `_includes/economy-app.html`, `assets/js/economy.js`, `assets/css/economy.scss`)
  fetches `assets/data/economy_field_v04.json` (0.7 MB, only on that page).
  `make economy` regenerates the JSON from the fitted field in
  `data/economy/field_v04/`; the fit itself (`scripts/economy_field.py`, ~45 min)
  and its raw caches, checkpoints and basis files are git-ignored and excluded
  from the site, as are the manuscripts under `abstract/`, `paper/`,
  `overleaf/` and `report/`.
- **Generated files.** They are in `.gitignore` on the assumption that you
  build on deploy. If you deploy from a branch instead, remove those lines so
  the artefacts are committed.
