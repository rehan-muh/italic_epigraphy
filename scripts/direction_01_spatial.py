import sqlite3, json, math, numpy as np, pandas as pd
np.random.seed(0)
ROOT = r"C:\Users\rehanmuh\Desktop\epigraphic_atlas"
con = sqlite3.connect(ROOT + r"\db\atlas.sqlite")

df = pd.read_sql("""
select i.pk, l.name lang, d.code dir, p.name place, p.id place_id, r.name region,
       i.latitude lat, i.longitude lon, i.date_start ds, i.date_end de, i.century,
       o.name objtype, u.label use
from inscriptions i
join directions d on d.id=i.direction_id
left join languages l on l.id=i.language_id
left join places p on p.id=i.place_id
left join regions r on r.id=p.region_id
left join object_types o on o.id=i.object_type_id
left join uses u on u.id=i.use_id
where i.latitude is not null""", con)
df["ltr"] = (df["dir"] == "1").astype(int)
df["width"] = df["de"] - df["ds"]
df["mid"] = (df["ds"] + df["de"]) / 2
print("coded+coords:", len(df))

# ---- equirectangular projection to km, centred on Italy
LAT0 = math.radians(42.5)
def xy(lat, lon):
    return np.c_[111.32 * np.cos(LAT0) * np.asarray(lon), 110.57 * np.asarray(lat)]
P = xy(df.lat.values, df.lon.values)
df["x"], df["y"] = P[:, 0], P[:, 1]

# ---- geographic covariates from the ingested layers
def load_coords(path):
    g = json.load(open(ROOT + r"\assets\data\layers\\" + path, encoding="utf-8"))
    pts = []
    def walk(c):
        if isinstance(c, list):
            if c and isinstance(c[0], (int, float)):
                pts.append(c[:2])
            else:
                for k in c: walk(k)
    for f in g["features"]:
        walk(f["geometry"]["coordinates"])
    a = np.array(pts, float)
    return xy(a[:, 1], a[:, 0])

def mindist(A, B, chunk=400):
    out = np.empty(len(A))
    for i in range(0, len(A), chunk):
        a = A[i:i+chunk]
        d = np.sqrt(((a[:, None, :] - B[None, :, :]) ** 2).sum(-1))
        out[i:i+chunk] = d.min(1)
    return out

for name, fn in [("d_road", "itinere-roads.geojson"), ("d_coast", "awmc-shoreline.geojson"),
                 ("d_river", "ne-rivers.geojson"), ("d_urban", "awmc-urban.geojson")]:
    try:
        df[name] = mindist(P, load_coords(fn))
    except Exception as e:
        print("skip", name, e)

# distance to nearest Greek-language and Latin-language findspot (contact proxies)
allp = pd.read_sql("""select p.id, p.latitude lat, p.longitude lon, l.name lang, count(*) n
from inscriptions i join places p on p.id=i.place_id left join languages l on l.id=i.language_id
where p.latitude is not null group by p.id, l.name""", con)
for lg, col in [("Greek", "d_greek"), ("Latin", "d_latin")]:
    sub = allp[(allp.lang == lg) & (allp.n >= 3)]
    df[col] = mindist(P, xy(sub.lat.values, sub.lon.values))
ROME = xy([41.8931], [12.4828])
df["d_rome"] = mindist(P, ROME)

print(df[["d_road","d_coast","d_greek","d_latin","d_rome"]].describe().round(1).to_string())

etr = df[df.lang == "Etruscan"].copy()
print("\n=== ETRUSCAN n=%d, LtR share %.3f ===" % (len(etr), etr.ltr.mean()))

# ---- place-level rates and Moran's I
def morans_I(g, val, n, bw=40.0, perms=999):
    Pp = xy(g.lat.values, g.lon.values)
    d = np.sqrt(((Pp[:, None, :] - Pp[None, :, :]) ** 2).sum(-1))
    W = np.exp(-(d / bw) ** 2); np.fill_diagonal(W, 0)
    W = W * np.sqrt(np.outer(n, n))          # weight by evidence at each place
    z = val - np.average(val, weights=n)
    num = (W * np.outer(z, z)).sum()
    I = len(val) / W.sum() * num / (z ** 2).sum()
    null = []
    for _ in range(perms):
        p = np.random.permutation(len(val)); zz = z[p]
        null.append(len(val) / W.sum() * (W * np.outer(zz, zz)).sum() / (zz ** 2).sum())
    null = np.array(null)
    return I, (1 + (null >= I).sum()) / (perms + 1)

for label, sub in [("Etruscan", etr), ("all langs", df)]:
    g = sub.groupby("place_id").agg(lat=("lat","first"), lon=("lon","first"),
                                    n=("ltr","size"), rate=("ltr","mean"),
                                    place=("place","first")).query("n>=8")
    I, p = morans_I(g, g.rate.values, g.n.values)
    print(f"{label}: places n>=8 = {len(g)},  Moran's I = {I:.3f}, perm p = {p:.4f}")

g = etr.groupby("place_id").agg(place=("place","first"), region=("region","first"),
                                n=("ltr","size"), rate=("ltr","mean")).query("n>=15").sort_values("rate", ascending=False)
print("\nEtruscan places n>=15, highest LtR share:")
print(g.head(12).to_string())
print("\n...lowest:")
print(g.tail(6).to_string())

# ---- logistic regression (IRLS), Etruscan only
def logit(X, y, names, ridge=1e-6):
    X = np.c_[np.ones(len(X)), X]
    b = np.zeros(X.shape[1])
    for _ in range(60):
        eta = X @ b; mu = 1/(1+np.exp(-eta)); w = np.clip(mu*(1-mu), 1e-9, None)
        H = X.T @ (X * w[:, None]) + ridge*np.eye(X.shape[1])
        b = b + np.linalg.solve(H, X.T @ (y - mu))
    se = np.sqrt(np.diag(np.linalg.inv(H)))
    print(f"{'term':<16}{'beta':>9}{'se':>8}{'z':>8}   OR")
    for nm, bb, ss in zip(["intercept"]+names, b, se):
        print(f"{nm:<16}{bb:>9.3f}{ss:>8.3f}{bb/ss:>8.2f}   {math.exp(bb):.3f}")
    return b

e = etr.dropna(subset=["d_road","d_coast","d_greek","d_latin","d_rome"]).copy()
e = e[e.century.notna()]
z = lambda s: (s - s.mean())/s.std()
cols = ["century", "ld_road", "ld_coast", "ld_greek", "ld_latin", "ld_rome"]
for c in ["d_road","d_coast","d_greek","d_latin","d_rome"]:
    e["l"+c] = np.log1p(e[c])
X = np.c_[tuple(z(e[c]).values for c in cols)]
print("\nEtruscan LtR ~ time + geography (standardised, n=%d):" % len(e))
logit(X, e.ltr.values, cols)

# narrow-dated only
en = e[e.width <= 100]
Xn = np.c_[tuple(z(en[c]).values for c in cols)]
print("\nsame, narrow-dated (<=100y) only, n=%d:" % len(en))
logit(Xn, en.ltr.values, cols)

# ---- entropy of direction by language x century (narrow-dated)
print("\n=== direction entropy (bits) by language x century, narrow-dated ===")
nd = df[df.width <= 100]
tab = nd.pivot_table(index=["lang","century"], columns="dir", values="pk", aggfunc="count").fillna(0)
tab["n"] = tab.sum(1)
pr = tab.drop(columns="n").div(tab["n"], axis=0)
tab["H"] = -(pr*np.log2(pr.where(pr > 0, 1))).sum(1)
print(tab[tab.n >= 20].round(3).to_string())
