import sqlite3, math, numpy as np, pandas as pd
np.random.seed(1)
ROOT = r"C:\Users\rehanmuh\Desktop\epigraphic_atlas"
con = sqlite3.connect(ROOT + r"\db\atlas.sqlite")
df = pd.read_sql("""
select i.pk, l.name lang, d.code dir, p.name place, p.id place_id, r.name region,
       i.reference, i.date_start ds, i.date_end de, i.century, i.q_ord_start qs, i.q_ord_end qe,
       o.name objtype, u.label use
from inscriptions i join directions d on d.id=i.direction_id
left join languages l on l.id=i.language_id left join places p on p.id=i.place_id
left join regions r on r.id=p.region_id left join object_types o on o.id=i.object_type_id
left join uses u on u.id=i.use_id""", con)
df["ltr"] = (df["dir"] == "1").astype(int)
df["width"] = df.de - df.ds
q = pd.read_sql("select ord, edge from quarters", con).set_index("ord").edge

print("== coverage of genre fields on the direction-coded set ==")
for c in ["objtype", "use"]:
    print(f"  {c}: {df[c].notna().sum()} / {len(df)} non-null; values:",
          df[c].value_counts().head(8).to_dict())

# ---------- 1. AORISTIC entropy: uses every coded record, weights by interval ----------
print("\n== aoristic vs narrow-dated: Etruscan LtR share by century ==")
d = df.dropna(subset=["qs", "qe"]).copy()
rows = []
for _, r in d.iterrows():
    qq = np.arange(int(r.qs), int(r.qe) + 1)
    if len(qq) == 0: continue
    yrs = q.reindex(qq).values
    cen = np.floor(yrs / 100).astype(int)
    w = 1.0 / len(qq)
    for cc in cen:
        rows.append((r.lang, cc, r.ltr, r.dir, w))
ao = pd.DataFrame(rows, columns=["lang", "century", "ltr", "dir", "w"])

def share(g):
    return pd.Series({"n_eff": g.w.sum(), "ltr": np.average(g.ltr, weights=g.w)})
for lg in ["Etruscan", "Greek", "Messapic", "Cisalpine Celtic"]:
    a = ao[ao.lang == lg].groupby("century").apply(share, include_groups=False)
    b = df[(df.lang == lg) & (df.width <= 100)].groupby("century").agg(n=("ltr", "size"), ltr=("ltr", "mean"))
    j = a.join(b, rsuffix="_narrow", how="outer").loc[-8:-1]
    print(f"\n  {lg}")
    print("   " + j.round(3).to_string().replace("\n", "\n   "))

# ---------- 2. bootstrap CI on entropy, clustered by place ----------
def H(p):
    p = p[p > 0]; return float(-(p * np.log2(p)).sum())
def ent_ci(sub, B=400):
    if len(sub) < 20: return None
    counts = sub.dir.value_counts(normalize=True).values
    h = H(counts)
    pl = sub.place_id.fillna(-1).values
    groups = {g: sub.index[pl == g] for g in np.unique(pl)}
    keys = list(groups)
    out = []
    for _ in range(B):
        pick = np.random.choice(len(keys), len(keys))
        idx = np.concatenate([groups[keys[k]] for k in pick])
        out.append(H(sub.loc[idx].dir.value_counts(normalize=True).values))
    return h, np.percentile(out, 2.5), np.percentile(out, 97.5), len(sub)

print("\n== entropy with place-clustered bootstrap 95% CI (narrow-dated <=100y) ==")
nd = df[df.width <= 100]
for lg in ["Etruscan", "Greek", "Messapic", "Cisalpine Celtic"]:
    for cen in range(-8, 0):
        r = ent_ci(nd[(nd.lang == lg) & (nd.century == cen)])
        if r: print(f"  {lg:<18}{cen:>4}c   H={r[0]:.3f}  [{r[1]:.3f}, {r[2]:.3f}]   n={r[3]}")

# ---------- 3. is the 7th-c Etruscan LtR driven by one site or one editor? ----------
print("\n== Etruscan 7th-c LtR: distribution over places and reference-series ==")
e7 = nd[(nd.lang == "Etruscan") & (nd.century == -7)]
print("  n=%d, LtR=%d across %d places, %d distinct sites with LtR" %
      (len(e7), e7.ltr.sum(), e7.place_id.nunique(), e7[e7.ltr == 1].place_id.nunique()))
print(e7[e7.ltr == 1].place.value_counts().head(10).to_string())
e7["series"] = e7.reference.str.extract(r"^([A-Za-zÀ-ÿ'\. ]+?\s*\d{4})")
print("  top reference-series among 7th-c LtR:",
      e7[e7.ltr == 1].series.value_counts().head(6).to_dict())

# ---------- 4. does the Etruscan century effect survive place fixed effects? ----------
print("\n== Etruscan LtR ~ century, with vs without place effects ==")
e = nd[(nd.lang == "Etruscan") & nd.century.notna()].copy()
def logit(X, y, names, ridge=1.0):
    X = np.c_[np.ones(len(X)), X]; b = np.zeros(X.shape[1])
    for _ in range(80):
        mu = 1/(1+np.exp(-(X @ b))); w = np.clip(mu*(1-mu), 1e-9, None)
        Hm = X.T @ (X*w[:, None]) + ridge*np.eye(X.shape[1])
        b = b + np.linalg.solve(Hm, X.T @ (y-mu))
    se = np.sqrt(np.diag(np.linalg.inv(Hm)))
    for nm, bb, ss in zip(["intercept"]+names, b, se):
        if nm in ("intercept",) or not nm.startswith("pl_"):
            print(f"    {nm:<14}{bb:>8.3f}  se {ss:.3f}  z {bb/ss:>6.2f}  OR {math.exp(bb):.3f}")
    return b
zc = (e.century - e.century.mean())/e.century.std()
logit(zc.values[:, None], e.ltr.values, ["century"])
big = e.place_id.value_counts(); big = big[big >= 15].index
ep = e[e.place_id.isin(big)].copy()
D = pd.get_dummies(ep.place_id.astype(int).astype(str), prefix="pl").astype(float).values
zc2 = ((ep.century-ep.century.mean())/ep.century.std()).values[:, None]
print("  within-place (%d places, n=%d):" % (len(big), len(ep)))
logit(np.c_[zc2, D], ep.ltr.values, ["century"]+["pl_%d" % i for i in range(D.shape[1])])
