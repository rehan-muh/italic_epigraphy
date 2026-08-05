#!/usr/bin/env python3
"""
shapefile_to_layer.py - turn a shapefile into one of the local layers that
enrich_geo.py picks up from .cache/.

Some publishers put their data behind a form and ship it as a shapefile rather
than serving GeoJSON at a stable URL. HydroBASINS and the Carta Geologica
d'Italia are both like this. Rather than add a shapefile reader to the geography
pipeline for two datasets, this converts once, clipped to the corpus bounding
box so a continental file becomes a few hundred kilobytes.

    pip install pyshp

    # straight out of the download, no unzipping: name the member inside it
    python3 scripts/shapefile_to_layer.py hybas_eu_lev01-12_v1c.zip local-hydrobasins \\
        --member hybas_eu_lev06_v1c --fields HYBAS_ID PFAF_ID SUB_AREA UP_AREA NEXT_DOWN

    # or an already-unzipped shapefile
    python3 scripts/shapefile_to_layer.py hybas_eu_lev06_v1c.shp local-hydrobasins

    # keep a couple of attributes and see what else is available
    python3 scripts/shapefile_to_layer.py hybas_eu_lev06_v1c.shp local-hydrobasins \\
        --fields HYBAS_ID SUB_AREA --list-fields

    # the file may be inside the zip; point at the .shp after unzipping
    python3 scripts/shapefile_to_layer.py CartaGeologica.shp local-geology \\
        --fields DESCR --name-field DESCR

Then build the layer as usual:

    python3 scripts/enrich_geo.py --only local-hydrobasins
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import enrich_geo as geo  # noqa: E402  (bounding box and geometry helpers)

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("shapefile", type=Path,
                    help="a .shp file, or a .zip containing one")
    ap.add_argument("--member",
                    help="which shapefile inside the zip, without the extension; "
                         "omit to list what is in there")
    ap.add_argument("layer_id", help="a local-* id declared in scripts/geosources.yml")
    ap.add_argument("--fields", nargs="*", default=[],
                    help="attribute columns to carry through")
    ap.add_argument("--name-field", help="column to use as the feature name")
    ap.add_argument("--list-fields", action="store_true",
                    help="print the available columns and a sample row, then stop")
    ap.add_argument("--bbox", nargs=4, type=float,
                    metavar=("MINX", "MINY", "MAXX", "MAXY"))
    ap.add_argument("--all", action="store_true",
                    help="keep every feature instead of clipping to the corpus box")
    opts = ap.parse_args()

    try:
        import shapefile  # pyshp
    except ImportError:
        sys.exit("pyshp is required: python -m pip install pyshp")

    if not opts.shapefile.exists():
        sys.exit(f"not found: {opts.shapefile}")

    handles = []
    if opts.shapefile.suffix.lower() == ".zip":
        # HydroBASINS ships twelve levels in one 345 MB archive, so read the
        # members straight out of it rather than making anyone unpack the lot
        import zipfile
        zf = zipfile.ZipFile(opts.shapefile)
        stems = sorted({Path(n).stem for n in zf.namelist()
                        if n.lower().endswith(".shp")})
        if not stems:
            sys.exit(f"no shapefile inside {opts.shapefile.name}")
        if not opts.member:
            print(f"{opts.shapefile.name} contains {len(stems)} shapefile(s):")
            for stem in stems:
                print(f"  {stem}")
            print("\nRe-run with --member <one of these>.")
            return 0
        if opts.member not in stems:
            sys.exit(f"{opts.member!r} is not in the archive. Available: "
                     + ", ".join(stems))
        parts = {}
        for suffix in ("shp", "dbf", "shx"):
            name = next((n for n in zf.namelist()
                         if Path(n).stem == opts.member
                         and n.lower().endswith("." + suffix)), None)
            if name is None:
                if suffix == "shx":
                    continue           # pyshp can manage without the index
                sys.exit(f"the archive has no .{suffix} for {opts.member}")
            handle = zf.open(name)
            handles.append(handle)
            parts[suffix] = handle
        reader = shapefile.Reader(**parts)
    else:
        reader = shapefile.Reader(str(opts.shapefile))

    columns = [f[0] for f in reader.fields[1:]]

    if opts.list_fields:
        print("columns:", ", ".join(columns))
        first = next(iter(reader.iterRecords()), None)
        if first is not None:
            print("first row:")
            for key, value in zip(columns, list(first)):
                print(f"  {key:<16} {value!r}")
        print(f"\n{len(reader)} features, "
              f"shape type {shapefile.SHAPETYPE_LOOKUP.get(reader.shapeType, reader.shapeType)}")
        return 0

    bbox = tuple(opts.bbox) if opts.bbox else geo.corpus_bbox()
    if opts.all:
        print("keeping every feature; this can be very large", file=sys.stderr)
    else:
        print(f"clipping to {bbox[0]:.3f} {bbox[1]:.3f} {bbox[2]:.3f} {bbox[3]:.3f}",
              file=sys.stderr)

    features, total = [], 0
    for shape_record in reader.iterShapeRecords():
        total += 1
        geom = shape_record.shape.__geo_interface__
        if not geom or not geom.get("coordinates"):
            continue
        if not opts.all:
            gb = geo.bbox_of(geom)
            if not gb or not geo.intersects(gb, bbox):
                continue
        record = dict(zip(columns, list(shape_record.record)))
        props = {}
        for field in opts.fields:
            if field in record and record[field] not in (None, ""):
                props[field.lower()] = record[field]
        if opts.name_field and record.get(opts.name_field) not in (None, ""):
            props["name"] = str(record[opts.name_field])
        features.append({"type": "Feature", "geometry": geom, "properties": props})

    CACHE.mkdir(exist_ok=True)
    out = CACHE / f"{opts.layer_id}.geojson"
    out.write_text(json.dumps({"type": "FeatureCollection", "features": features},
                              ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    print(f"{len(features)} of {total} features -> {out.relative_to(ROOT)} "
          f"({out.stat().st_size / 1e6:.1f} MB)", file=sys.stderr)
    print(f"now run: python3 scripts/enrich_geo.py --only {opts.layer_id}", file=sys.stderr)
    for handle in handles:
        handle.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
