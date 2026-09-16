#!/usr/bin/env python3
"""
Check every number asserted in the abstract's prose against its source.

The figures are generated, so they cannot drift; the sentences are typed, so
they can. Each claim below is re-derived from db/direction_model.json and
compared with what the .tex actually says. A claim the abstract no longer makes
shows up as MISSING rather than passing silently, so rewording the prose forces
the check to be rewritten with it.

    py -3.12 scripts/check_abstract_numbers.py        # exit 1 on any mismatch
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
TEX = ROOT / "abstract" / "qdlca27-abstract.tex"
MODEL_PATH = Path(sys.argv[sys.argv.index("--model") + 1]) if "--model" in sys.argv \
    else ROOT / "db" / "direction_model.json"
MODEL = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
TEXT = TEX.read_text(encoding="utf-8")

WORDS = {"five": 5, "seven": 7, "eight": 8, "seventeen": 17}


def m(key, field):
    return MODEL["ladder"][key]["metrics"][field]


def var(key):
    return MODEL["variance_share_full_model"][key] * 100


def cells(prefix):
    return [c["p"] for k, c in MODEL["cells"].items() if k.startswith(prefix)]


def etruscan_alphabets():
    """Fitted rate across the Etruscan alphabets, dual script and Latin out."""
    v = [c["p"] for k, c in MODEL["cells"].items()
         if k.startswith("Etruscan | ") and "Etruscan" in k.split("|")[1]
         and "dual" not in k and "Latin" not in k]
    return round(min(v) * 100), round(max(v) * 100)


def window(alphabet, since=None):
    rows = [r for r in MODEL["trajectories"][alphabet]
            if since is None or r["year"] >= since]
    return round(min(r["p"] for r in rows) * 100), round(max(r["p"] for r in rows) * 100)


def century(alphabet, c):
    """Mass-weighted mean of one curve over one century BCE, as a percentage.

    The same statistic for the pooled curve and for a single alphabet, so the
    numbers in the text are comparable with each other. Endpoints of a sparse
    series are not: red-type Greek opens on three inscriptions.
    """
    rows = [r for r in MODEL["trajectories"][alphabet]
            if (abs(r["year"]) - 1) // 100 + 1 == c]
    return round(100 * sum(r["p"] * r["mass"] for r in rows)
                 / sum(r["mass"] for r in rows))


CHECKS = [
    ("modelled records", r"([\d{},]+) dated,? (?:and )?located",
     lambda: f"{MODEL['n_modelled']:,}".replace(",", "{,}")),
    ("languages", r"for ([a-z]+) languages", lambda: len(MODEL["languages"])),
    ("alphabets", r"([a-z]+) alphabets and", lambda: len(MODEL["alphabets"])),
    ("findspots", r"([\d{},]+)\s+findspots", lambda: f"{MODEL['n_findspots']:,}".replace(",", "{,}")),
    ("kernel", r"cross-validated: (\S+) 3/2",
     lambda: "Matérn" if MODEL["st_kernel"]["kernel"] == "m32" else "squared-exponential"),
    ("length scale", r"3/2 at (\d+)\\,km", lambda: round(MODEL["st_kernel"]["length_scale_km"])),
    ("space-time F1", r"predict direction well \(F1 \$([\d.]+)\$\)",
     lambda: f"{m('M1b space-time kernel', 'f1'):.2f}"),
    ("alphabet F1", r"F1 \$([\d.]+)\$ against",
     lambda: f"{m('M2a alphabet, no language', 'f1'):.2f}"),
    ("language F1", r"F1 \$[\d.]+\$ against\s*\n?\$([\d.]+)\$",
     lambda: f"{m('M2 + language', 'f1'):.2f}"),
    ("alphabet-language delta", r"and \$([\d.]+)\$ bits per record",
     lambda: f"{-MODEL['cv_pairs']['alphabet alone vs language alone']['delta_bits']:.3f}"),
    ("delta CI low", r"95\\% CI \$([\d.]+)\$--",
     lambda: f"{-MODEL['cv_pairs']['alphabet alone vs language alone']['hi']:.3f}"),
    ("delta CI high", r"95\\% CI \$[\d.]+\$--\$([\d.]+)\$",
     lambda: f"{-MODEL['cv_pairs']['alphabet alone vs language alone']['lo']:.3f}"),
    ("adding language, |delta|", r"moves \$([\d.]+)\$ bits",
     lambda: f"{abs(MODEL['cv_pairs']['adding language to alphabet']['delta_bits']):.3f}"),
    ("adding language CI", r"moves \$[\d.]+\$ bits \[\$(-[\d.]+),\s+\+[\d.]+\$\]",
     lambda: f"{MODEL['cv_pairs']['adding language to alphabet']['lo']:.3f}"),
    ("field+geo, |delta|", r"beyond the rest \$([\d.]+)\$",
     lambda: f"{abs(MODEL['cv_pairs']['geography + field beyond M5']['delta_bits']):.3f}"),
    ("field+geo CI", r"beyond the rest \$[\d.]+\$ \[\$(-[\d.]+), \+[\d.]+\$\]",
     lambda: f"{MODEL['cv_pairs']['geography + field beyond M5']['lo']:.3f}"),
    ("alphabet variance", r"alphabet holds\s*\n?\$(\d+)\\%\$ of the fitted variance",
     lambda: round(var("alphabet"))),
    ("alphabet total variance", r"\(\$(\d+)\\%\$ with its curves\)",
     lambda: round(MODEL["variance_share_full_model"]["alphabet total (levels + curves)"] * 100)),
    ("language variance", r"language \$(\d+)\\%\$",
     lambda: round(var("language"))),
    ("field variance", r"the\s*\n?field \$(\d+)\\%\$",
     lambda: round(var("space-time field"))),
    ("pooled, 6th c.", r"(\d+)\\% dextroverse in the sixth", lambda: century("", 6)),
    ("pooled, 2nd c.", r"(\d+)\\% in the second", lambda: century("", 2)),
    ("pooled, 1st c.", r"(\d+)\\% in the first\.", lambda: century("", 1)),
    ("Lepontic, 3rd c.", r"Lepontic \((\d+)\\%", lambda: century("Lepontic alphabet", 3)),
    ("Lepontic, 1st c.", r"third\s+century to (\d+)\\%", lambda: century("Lepontic alphabet", 1)),
    ("Greek red, 6th c.", r"red-type Greek \((\d+)\\%",
     lambda: century("Greek alphabet (red type)", 6)),
    ("Greek red, 5th c.", r"in the sixth to\s*\n?(\d+)\\%",
     lambda: century("Greek alphabet (red type)", 5)),
    ("Etruscan in Latin letters", r"fitted at (\d+)\\% dextroverse",
     lambda: round(MODEL["cells"]["Etruscan | Republican Latin alphabet"]["p"] * 100)),
    ("Etruscan alphabets, low", r"against\s*\n?(\d+)--\d+\\%", lambda: etruscan_alphabets()[0]),
    ("Etruscan alphabets, high", r"against\s*\n?\d+--(\d+)\\%", lambda: etruscan_alphabets()[1]),
    ("contact, near", r"epigraphy is ([\d.]+)\\% dextroverse",
     lambda: f"{MODEL['contact_test']['fitted_rate']['p_near']*100:.1f}"),
    ("contact, far", r"dextroverse against ([\d.]+)\\% far",
     lambda: f"{MODEL['contact_test']['fitted_rate']['p_far']*100:.1f}"),
]


def main():
    bad = 0
    for name, pattern, derive in CHECKS:
        mt = re.search(pattern, TEXT)
        if not mt:
            print(f"  MISSING  {name:<26} no match for {pattern!r}")
            bad += 1
            continue
        said, want = mt.group(1), str(derive())
        ok = said == want or str(WORDS.get(said, said)) == str(WORDS.get(want, want))
        print(f"  {'ok ' if ok else 'BAD'}      {name:<26} abstract {said!r}"
              f"{'' if ok else f'   source {want!r}'}")
        bad += 0 if ok else 1

    prev = MODEL["ladder"]["M0 intercept"]["metrics"]["prevalence"]
    ok = 0.15 <= prev <= 0.19 and "one inscription in six" in TEXT
    print(f"  {'ok ' if ok else 'BAD'}      {'one in six dextroverse':<26} {prev}")
    bad += 0 if ok else 1

    print(f"\n{'all numbers agree' if not bad else f'{bad} MISMATCH(ES)'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
