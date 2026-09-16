# Epigraphic Atlas of Ancient Italy
#
#   make serve    rebuild what is stale, then run Jekyll at localhost:4000
#   make db       CSV -> SQLite -> SQL dump -> JSON, only if the CSV changed
#   make rebuild  same, unconditionally
#   make geo      fetch the default geography layers (needs network)
#   make geo-all  fetch every declared layer, including the slow OSM ones
#   make layers   list what is declared in scripts/geosources.yml
#   make links    reconcile findspots against Pleiades, Wikidata and iDAI
#   make economy  package the fitted economic field for /economy/ (needs the v0.4 fit)
#   make encoding audit the character encoding of the CSV
#   make watch    rebuild automatically whenever the CSV changes
#   make build    production build into _site/
#   make clean    delete every generated file

# python3 on macOS and Linux, python on Windows. Override with: make db PY=py
PY  ?= $(shell command -v python3 2>/dev/null || command -v python 2>/dev/null || echo python)
CSV := data/database_preliminary.csv
GEN := _data/atlas.yml assets/data/corpus.json assets/data/places.geojson

.PHONY: all db rebuild pages geo geo-all layers links encoding economy serve watch build clean check install

all: db

install:
	$(PY) -m pip install pyyaml certifi
	bundle install

$(GEN): $(CSV) scripts/build_db.py scripts/codebook.yml scripts/text_repairs.yml data/links.csv
	$(PY) scripts/build_db.py --site-pages

db: $(GEN)

rebuild:
	$(PY) scripts/build_db.py --force --site-pages

pages:
	$(PY) scripts/build_db.py --force --site-pages

check:
	$(PY) scripts/build_db.py --check

encoding:
	$(PY) scripts/textfix.py

geo:
	$(PY) scripts/enrich_geo.py

geo-all:
	$(PY) scripts/enrich_geo.py --all

layers:
	$(PY) scripts/enrich_geo.py --list

# needs db/atlas.sqlite, and writes data/links.csv for review; the next
# build_db.py run folds it into place_links
links:
	$(PY) scripts/link_authorities.py --periods

data/links.csv:
	@touch $@

# the field itself is fitted by scripts/economy_field.py (~45 min, checkpointed);
# this only packages the tracked grid CSV + basis for the site page
economy:
	$(PY) scripts/economy_field_animation.py

serve: db
	bundle exec jekyll serve --livereload --host 127.0.0.1 --port 4000

watch:
	$(PY) scripts/watch.py

build: db
	JEKYLL_ENV=production bundle exec jekyll build

clean:
	rm -rf _site .jekyll-cache _sites db/atlas.sqlite db/atlas.dump.sql \
	       db/schema.sql db/.build-stamp.json _data/atlas.yml \
	       assets/data/corpus.json assets/data/places.geojson \
	       assets/data/timeline.json assets/data/atlas.sqlite
