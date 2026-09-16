#!/usr/bin/env python3
"""
Model-free descriptives on the strictly binary direction outcome.

0 = sinistroverse, 1 = dextroverse; codes 2, 3 and "both" excluded. Reports,
per tradition and century, the dextroverse share p and the directional entropy
H(p) = -p log2 p - (1-p) log2 (1-p), both on the narrow-dated subset and under
aoristic weighting over the full coded corpus, with findspot-clustered
bootstrap intervals.

    py -3.12 scripts/direction_descriptive.py
"""
from __future__ import annotations

import json, sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "db" / "direction_descriptive.json"
B = 800


def h_bits(p):
    p = np.clip(np.asarray(p, float), 1e-12, 1 - 1e-12)
    return -(p * np.log2(p) + (1 - p) * np.log2(1 - p))


def main():
    con = sqlite3.connect(ROOT / "db" / "atlas.sqlite")
    df = pd.read_sql("""
        select i.pk, l.name lang, d.code dir, p.id place_id, i.century,
               i.date_start ds, i.date_end de, i.q_ord_start qs, i.q_ord_end qe
        from inscriptions i
        join directions d on d.id = i.direction_id
        left join languages l on l.id = i.language_id
        left join places p on p.id = i.place_id
    """, con)
    edges = pd.read_sql("select ord, edge from quarters order by ord", con).set_index("ord").edge
    con.close()

    n_coded = len(df)
    n_messy = int((~df.dir.isin(["0", "1"])).sum())
    df = df[df.dir.isin(["0", "1"])].copy()
    df["y"] = (df.dir == "1").astype(float)
    df["w"] = df.de - df.ds

    res = {"n_coded": n_coded, "n_messy_excluded": n_messy, "n_binary": int(len(df))}

    # ---- aoristic expansion over centuries --------------------------------
    ao = []
    sub = df.dropna(subset=["qs", "qe"])
    for pk, lang, place, y, a, b in zip(sub.pk, sub.lang, sub.place_id, sub.y,
                                        sub.qs.astype(int), sub.qe.astype(int)):
        ords = np.arange(a, b + 1)
        cen = np.floor(edges.reindex(ords).values / 100).astype(int)
        wt = 1.0 / len(ords)
        for c in cen:
            ao.append((pk, lang, place, y, c, wt))
    ao = pd.DataFrame(ao, columns=["pk", "lang", "place_id", "y", "century", "w"])

    nd = df[(df.w <= 100) & df.century.notna()].copy()
    nd["century"] = nd.century.astype(int)

    rng = np.random.default_rng(3)

    def boot(frame, weighted):
        """Findspot-clustered bootstrap of p, mapped through H."""
        groups = frame.groupby("place_id").indices
        keys = list(groups)
        if not keys:
            return None, None, None, None
        ps = []
        for _ in range(B):
            pick = rng.integers(0, len(keys), len(keys))
            idx = np.concatenate([groups[keys[j]] for j in pick])
            s = frame.iloc[idx]
            if weighted:
                tot = s.w.sum()
                ps.append(float((s.y * s.w).sum() / tot) if tot > 0 else np.nan)
            else:
                ps.append(float(s.y.mean()))
        ps = np.array(ps, float)
        ps = ps[np.isfinite(ps)]
        return (float(np.percentile(ps, 2.5)), float(np.percentile(ps, 97.5)),
                float(np.percentile(h_bits(ps), 2.5)), float(np.percentile(h_bits(ps), 97.5)))

    out = {}
    for label, frame, weighted, floor in (("narrow", nd, False, 15),
                                          ("aoristic", ao, True, 15)):
        rows = {}
        for (lang, cen), g in frame.groupby([frame.lang.fillna("unknown"), "century"]):
            if cen < -8 or cen > -1:
                continue
            n_eff = g.w.sum() if weighted else len(g)
            if n_eff < floor:
                continue
            p = float((g.y * g.w).sum() / g.w.sum()) if weighted else float(g.y.mean())
            plo, phi, hlo, hhi = boot(g, weighted)
            rows.setdefault(lang, []).append({
                "century": int(cen), "n": round(float(n_eff), 1),
                "p": round(p, 4), "p_lo": round(plo, 4), "p_hi": round(phi, 4),
                "H": round(float(h_bits(p)), 4),
                "H_lo": round(hlo, 4), "H_hi": round(hhi, 4)})
        for lang in rows:
            rows[lang].sort(key=lambda r: r["century"])
        out[label] = rows
    res["by_tradition"] = out

    # ---- pooled, for the composition argument ------------------------------
    pooled = []
    for cen, g in nd.groupby("century"):
        if cen < -8 or cen > -1:
            continue
        comp = g.lang.fillna("unknown").value_counts(normalize=True)
        pooled.append({"century": int(cen), "n": int(len(g)),
                       "p": round(float(g.y.mean()), 4),
                       "top": [[k, round(float(v), 4)] for k, v in comp.head(3).items()]})
    res["pooled_narrow"] = pooled

    res["raw_rates"] = {k: {"n": int(v["n"]), "p": round(float(v["p"]), 4)}
                        for k, v in df.groupby(df.lang.fillna("unknown"))
                        .agg(n=("y", "size"), p=("y", "mean")).iterrows()}

    OUT.write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(f"binary records {res['n_binary']} (excluded {n_messy} messy codes)\n")
    for label in ("narrow", "aoristic"):
        print(f"== {label} ==")
        for lang, rows in res["by_tradition"][label].items():
            cells = "  ".join(f"{r['century']}c p={r['p']:.3f} H={r['H']:.2f}"
                              f"[{r['H_lo']:.2f},{r['H_hi']:.2f}] n={r['n']:.0f}"
                              for r in rows)
            print(f"  {lang:<18}{cells}")
        print()
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
