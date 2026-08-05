# Epigraphic Atlas of Ancient Italy
#
#   make serve    rebuild what is stale, then run Jekyll at localhost:4000
#   make db       CSV -> SQLite -> SQL dump -> JSON, only if the CSV changed
#   make rebuild  same, unconditionally
#   make geo      fetch rivers, relief names and ancient places (needs network)
#   make watch    rebuild automatically whenever the CSV changes
#   make build    production build into _site/
#   make clean    delete every generated file

# python3 on macOS and Linux, python on Windows. Override with: make db PY=py
PY  ?= $(shell command -v python3 2>/dev/null || command -v python 2>/dev/null || echo python)
CSV := data/database_preliminary.csv
GEN := _data/atlas.yml assets/data/corpus.json assets/data/places.geojson

.PHONY: all db rebuild pages geo serve watch build clean check install

all: db

install:
	$(PY) -m pip install pyyaml
	bundle install

$(GEN): $(CSV) scripts/build_db.py scripts/codebook.yml
	$(PY) scripts/build_db.py --site-pages

db: $(GEN)

rebuild:
	$(PY) scripts/build_db.py --force --site-pages

pages:
	$(PY) scripts/build_db.py --force --site-pages

check:
	$(PY) scripts/build_db.py --check

geo:
	$(PY) scripts/enrich_geo.py

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
