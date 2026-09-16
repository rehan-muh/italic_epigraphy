#!/usr/bin/env python3
"""
Which unit carries a directional norm: the language, or the alphabet?

Direction is binary here (0 = sinistroverse, 1 = dextroverse; codes 2, 3 and
"both" are not reliably coded and are excluded). For a binary outcome the
directional entropy

    H(p) = -p log2 p - (1-p) log2 (1-p)

is the same estimate as p on a scale indifferent to *which* norm is held: 0
bits means practice is fixed, at p = 0 and p = 1 alike; 1 bit means a coin
flip. That makes conditional entropy the natural test of what a norm belongs
to. If script communities hold it,

    H(direction | alphabet)  <  H(direction | language),

and conditioning further on date should buy little. If instead direction drifts
through time across the peninsula, date is what should matter.

Aoristic throughout: a record dated to an interval spanning n quarter-centuries
contributes 1/n of a unit to each. The weights come from the corpus, not from a
model. Intervals are respected rather than point-binned or discarded.

    py -3.12 scripts/direction_unit.py
"""
from __future__ import annotations

import json, sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "db" / "direction_unit.json"
B = 600
MIN_CELL = 20.0


def h_bits(p):
    p = np.clip(np.asarray(p, float), 1e-12, 1 - 1e-12)
    return -(p * np.log2(p) + (1 - p) * np.log2(1 - p))


def load():
    con = sqlite3.connect(ROOT / "db" / "atlas.sqlite")
    df = pd.read_sql("""
        select i.pk, l.name lang, a.label alph, d.code dir, p.id place_id,
               i.q_ord_start qs, i.q_ord_end qe
        from inscriptions i
        join directions d on d.id = i.direction_id
        left join languages l on l.id = i.language_id
        left join alphabets a on a.id = i.alphabet_id
        left join places    p on p.id = i.place_id
    """, con)
    edges = pd.read_sql("select ord, edge from quarters order by ord",
                        con).set_index("ord").edge
    con.close()
    n_coded = len(df)
    n_messy = int((~df.dir.isin(["0", "1"])).sum())
    df = df[df.dir.isin(["0", "1"])].dropna(subset=["qs", "qe"]).copy()
    df["y"] = (df.dir == "1").astype(float)
    df["qs"] = df.qs.astype(int)
    df["qe"] = df.qe.astype(int)
    df["alph"] = df.alph.fillna("(alphabet unrecorded)")
    df["lang"] = df.lang.fillna("(language unrecorded)")
    df["place_id"] = df.place_id.fillna(-1).astype(int)
    return df.reset_index(drop=True), edges, n_coded, n_messy


class Expansion:
    """Aoristic (record, century) expansion held as flat arrays.

    Built once. A bootstrap draw of records is then a gather over precomputed
    row offsets rather than a rebuild, which is what makes 600 resamples cheap.
    """

    def __init__(self, df, edges):
        counts, cens = [], []
        for a, b in zip(df.qs.values, df.qe.values):
            ords = np.arange(a, b + 1)
            c = np.floor(edges.reindex(ords).values / 100).astype(np.int64)
            counts.append(len(c))
            cens.append(c)
        self.counts = np.asarray(counts, np.int64)
        self.starts = np.concatenate([[0], np.cumsum(self.counts)[:-1]])
        self.century = np.concatenate(cens)
        self.w = np.repeat(1.0 / self.counts, self.counts)
        self.rec = np.repeat(np.arange(len(df)), self.counts)
        self.y = df.y.values[self.rec]

    def rows_for(self, sel):
        """Expanded-row indices for a (possibly repeated) selection of records."""
        c = self.counts[sel]
        total = int(c.sum())
        off = np.repeat(np.cumsum(c) - c, c)
        return np.repeat(self.starts[sel], c) + (np.arange(total) - off)


def codes_for(df, exp, keys):
    """Integer group code per expanded row for a conditioning scheme."""
    parts = []
    for k in keys:
        if k == "century":
            parts.append(exp.century)
        else:
            parts.append(pd.factorize(df[k])[0][exp.rec])
    code = parts[0].astype(np.int64)
    for p in parts[1:]:
        code = code * (int(p.max()) + 2) + p
    return pd.factorize(code)[0]


def cond_entropy(code, y, w, n_groups=None):
    n = (code.max() + 1) if n_groups is None else n_groups
    m = np.bincount(code, weights=w, minlength=n)
    s = np.bincount(code, weights=w * y, minlength=n)
    ok = m > 0
    p = np.zeros_like(m)
    p[ok] = s[ok] / m[ok]
    share = m / m.sum()
    return float((share[ok] * h_bits(p[ok])).sum())


def cv_cond_entropy(code, y, w, fold_of_row, n_folds, alpha=2.0):
    """Findspot-blocked cross-validated conditional entropy, in bits.

    The plug-in estimator above is biased downward as cells fragment, so it can
    appear to *improve* when a useless variable is added. This version fits the
    cell probabilities on the training folds, with backoff to the global rate,
    and scores held-out records. It is the held-out coding cost of the outcome
    given the conditioning variable, in bits per unit of aoristic mass, so a
    conditioning variable that only fragments the data is penalised.
    """
    n = int(code.max()) + 1
    total = 0.0
    mass = 0.0
    for f in range(n_folds):
        tr = fold_of_row != f
        te = ~tr
        if not te.any() or not tr.any():
            continue
        gp = float((w[tr] * y[tr]).sum() / w[tr].sum())
        m = np.bincount(code[tr], weights=w[tr], minlength=n)
        s = np.bincount(code[tr], weights=w[tr] * y[tr], minlength=n)
        p = (s + alpha * gp) / (m + alpha)
        p = np.clip(p, 1e-6, 1 - 1e-6)
        pt = p[code[te]]
        ll = np.where(y[te] > 0.5, np.log2(pt), np.log2(1 - pt))
        total += float((w[te] * ll).sum())
        mass += float(w[te].sum())
    return -total / mass


def main():
    df, edges, n_coded, n_messy = load()
    exp = Expansion(df, edges)
    res = {"n_coded": n_coded, "n_messy_excluded": n_messy, "n_binary": int(len(df)),
           "n_alphabets": int(df.alph.nunique()), "n_languages": int(df.lang.nunique())}
    print(f"{len(df)} binary records, {df.lang.nunique()} languages, "
          f"{df.alph.nunique()} alphabets; {n_messy} messy codes excluded\n")

    # bootstrap machinery: clustered on findspot
    rng = np.random.default_rng(5)
    groups = df.groupby("place_id").indices
    keys_pl = list(groups)
    draws = [np.concatenate([groups[keys_pl[j]] for j in
                             rng.integers(0, len(keys_pl), len(keys_pl))])
             for _ in range(B)]

    schemes = [("unconditional", []),
               ("| century", ["century"]),
               ("| findspot", ["place_id"]),
               ("| language", ["lang"]),
               ("| language, century", ["lang", "century"]),
               ("| alphabet", ["alph"]),
               ("| alphabet, century", ["alph", "century"])]

    # findspot-blocked folds, assigned per record then broadcast to expanded rows
    n_folds = 10
    pl = df.place_id.unique()
    rng.shuffle(pl)
    fold_of_place = {p: i % n_folds for i, p in enumerate(pl)}
    fold_of_row = df.place_id.map(fold_of_place).values[exp.rec]

    p0 = float((exp.y * exp.w).sum() / exp.w.sum())
    table = {}
    print("entropy of writing direction, bits (aoristically weighted)")
    print(f"{'conditioned on':<24}{'plug-in':>9}{'held-out':>10}   95% CI on held-out")
    for name, keys in schemes:
        if not keys:
            h = hcv = float(h_bits(p0))
            lo = hi = None
        else:
            code = codes_for(df, exp, keys)
            h = cond_entropy(code, exp.y, exp.w)
            hcv = cv_cond_entropy(code, exp.y, exp.w, fold_of_row, n_folds)
            vals = []
            for sel in draws:
                r = exp.rows_for(sel)
                vals.append(cv_cond_entropy(pd.factorize(code[r])[0], exp.y[r],
                                            exp.w[r], fold_of_row[r], n_folds))
            v = np.array(vals)
            lo, hi = float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))
        table[name] = {"H_plugin": round(h, 4), "H_heldout": round(hcv, 4),
                       "lo": round(lo, 4) if lo is not None else None,
                       "hi": round(hi, 4) if hi is not None else None,
                       "info_gain_heldout": round(float(h_bits(p0)) - hcv, 4)}
        ci = f"   [{lo:.3f}, {hi:.3f}]" if lo is not None else ""
        print(f"  {name:<22}{h:>9.4f}{hcv:>10.4f}{ci}")
    res["conditional_entropy"] = table

    # ---- same language, different script ----------------------------------
    print(f"\nlanguages attested in more than one alphabet (cells >= {MIN_CELL:.0f}):")
    lang_arr = df.lang.values[exp.rec]
    alph_arr = df.alph.values[exp.rec]
    splits = {}
    for lang in df.lang.unique():
        m = lang_arr == lang
        if not m.any():
            continue
        cells = {}
        for al in np.unique(alph_arr[m]):
            mm = m & (alph_arr == al)
            mass = exp.w[mm].sum()
            if mass >= MIN_CELL:
                cells[al] = (float(mass), float((exp.y[mm] * exp.w[mm]).sum() / mass))
        if len(cells) < 2:
            continue
        tot = sum(v[0] for v in cells.values())
        within = sum(v[0] / tot * h_bits(v[1]) for v in cells.values())
        p_all = float((exp.y[m] * exp.w[m]).sum() / exp.w[m].sum())
        rows = sorted(({"alphabet": a, "mass": round(v[0], 1),
                        "p_dextroverse": round(v[1], 4),
                        "H": round(float(h_bits(v[1])), 4)}
                       for a, v in cells.items()), key=lambda r: -r["mass"])
        splits[lang] = {"p_overall": round(p_all, 4),
                        "H_at_language_level": round(float(h_bits(p_all)), 4),
                        "H_within_alphabet": round(float(within), 4), "cells": rows}
        print(f"\n  {lang}:  H(language) {h_bits(p_all):.3f}"
              f"  ->  H(within alphabet) {within:.3f}")
        for r in rows:
            print(f"      {r['alphabet']:<42} mass {r['mass']:>7.1f}"
                  f"   p {r['p_dextroverse']:.3f}   H {r['H']:.3f}")
    res["language_by_alphabet"] = splits

    # ---- trajectories for the figure --------------------------------------
    def trajectories(key_arr, floor_total=60.0):
        out = {}
        for k in np.unique(key_arr):
            m = key_arr == k
            if exp.w[m].sum() < floor_total:
                continue
            rows = []
            for cen in range(-8, 0):
                mm = m & (exp.century == cen)
                mass = exp.w[mm].sum()
                if mass < MIN_CELL:
                    continue
                p = float((exp.y[mm] * exp.w[mm]).sum() / mass)
                rows.append({"century": cen, "mass": round(float(mass), 1),
                             "p": round(p, 4), "H": round(float(h_bits(p)), 4)})
            if len(rows) >= 2:
                out[str(k)] = rows
        return out

    res["alphabet_trajectories"] = trajectories(alph_arr)
    res["language_trajectories"] = trajectories(lang_arr)

    print("\ntrajectories by alphabet (p dextroverse, 8th c. -> 1st c. BCE):")
    for a, rows in sorted(res["alphabet_trajectories"].items(),
                          key=lambda kv: -sum(r["mass"] for r in kv[1])):
        span = f"{rows[0]['century']}..{rows[-1]['century']}"
        print(f"  {a:<42} {span:<8} " + " ".join(f"{r['p']:.2f}" for r in rows))

    OUT.write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
