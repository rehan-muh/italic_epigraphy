import sqlite3, json, math, numpy as np, pandas as pd
np.random.seed(2)
ROOT = r"C:\Users\rehanmuh\Desktop\epigraphic_atlas"
con = sqlite3.connect(ROOT + r"\db\atlas.sqlite")
df = pd.read_sql("""
select i.pk, l.name lang, d.code dir, p.name place, p.id place_id, r.name region,
       i.latitude lat, i.longitude lon, i.date_start ds, i.date_end de, i.century
from inscriptions i join directions d on d.id=i.direction_id
left join languages l on l.id=i.language_id left join places p on p.id=i.place_id
left join regions r on r.id=p.region_id where i.latitude is not null""", con)
df["ltr"] = (df["dir"] == "1").astype(int); df["width"] = df.de - df.ds

# --- Roman territory, 60 BC (AWMC) : point-in-polygon by ray casting
g = json.load(open(ROOT + r"\assets\data\layers\awmc-provinces-60bc.geojson", encoding="utf-8"))
rings = []
for f in g["features"]:
    geom = f["geometry"]; cs = geom["coordinates"]
    polys = cs if geom["type"] == "MultiPolygon" else [cs]
    for poly in polys:
        for ring in poly:
            rings.append(np.array(ring, float)[:, :2])
print("Roman-territory-60BC rings:", len(rings),
      "| names:", [f["properties"].get("name") or f["properties"].get("NAME") for f in g["features"]][:8])

def inside(pts, rings):
    res = np.zeros(len(pts), bool)
    for R in rings:
        x, y = pts[:, 0], pts[:, 1]
        x1, y1 = R[:-1, 0], R[:-1, 1]; x2, y2 = R[1:, 0], R[1:, 1]
        cross = ((y1[None] > y[:, None]) != (y2[None] > y[:, None]))
        with np.errstate(divide="ignore", invalid="ignore"):
            xint = (x2-x1)[None]*(y[:, None]-y1[None])/(y2-y1)[None] + x1[None]
        res ^= (cross & (x[:, None] < xint)).sum(1) % 2 == 1
    return res

pts = df[["lon", "lat"]].values
df["in_rome60"] = inside(pts, rings)

# distance (km) to the boundary of Roman territory, signed: + outside
LAT0 = math.radians(42.5)
def xy(lon, lat): return np.c_[111.32*math.cos(LAT0)*np.asarray(lon), 110.57*np.asarray(lat)]
B = np.vstack([xy(R[:, 0], R[:, 1]) for R in rings])
P = xy(df.lon.values, df.lat.values)
d = np.empty(len(P))
for i in range(0, len(P), 500):
    a = P[i:i+500]
    d[i:i+500] = np.sqrt(((a[:, None, :]-B[None, :, :])**2).sum(-1)).min(1)
df["d_rome60"] = np.where(df.in_rome60, -d, d)

print("\n== late (2nd-1st c. BCE) non-Latin traditions: LtR by position vs Roman territory 60 BC ==")
late = df[(df.century.isin([-2, -1])) & (~df.lang.isin(["Latin", "Greek", "Latin/Greek"]))]
print(late.groupby([late.lang, late.in_rome60]).agg(n=("ltr", "size"), ltr=("ltr", "mean")).round(3).to_string())

print("\n== same, all periods, Cisalpine Celtic + Raetic (the northern frontier) ==")
nor = df[df.lang.isin(["Cisalpine Celtic", "Raetic", "Camunic"])]
print(nor.groupby([nor.century, nor.in_rome60]).agg(n=("ltr", "size"), ltr=("ltr", "mean")).round(3).to_string())

def logit(X, y, names, ridge=1.0):
    X = np.c_[np.ones(len(X)), X]; b = np.zeros(X.shape[1])
    for _ in range(80):
        mu = 1/(1+np.exp(-(X@b))); w = np.clip(mu*(1-mu), 1e-9, None)
        H = X.T@(X*w[:, None]) + ridge*np.eye(X.shape[1]); b = b + np.linalg.solve(H, X.T@(y-mu))
    se = np.sqrt(np.diag(np.linalg.inv(H)))
    for nm, bb, ss in zip(["intercept"]+names, b, se):
        print(f"    {nm:<18}{bb:>8.3f}  se {ss:.3f}  z {bb/ss:>6.2f}  OR {math.exp(bb):.3f}")

print("\n== LtR ~ century + distance to Roman frontier (60 BC), non-Latin/Greek, narrow-dated ==")
m = df[(~df.lang.isin(["Latin", "Greek", "Latin/Greek"])) & df.century.notna() & (df.width <= 150)].copy()
z = lambda s: (s-s.mean())/s.std()
X = np.c_[z(m.century).values, z(np.sign(m.d_rome60)*np.log1p(m.d_rome60.abs())).values]
print("  n=%d" % len(m)); logit(X, m.ltr.values, ["century", "signed log d_frontier"])
print("\n  Etruscan only, n=%d:" % (m.lang == "Etruscan").sum())
me = m[m.lang == "Etruscan"]
logit(np.c_[z(me.century).values, z(np.sign(me.d_rome60)*np.log1p(me.d_rome60.abs())).values],
      me.ltr.values, ["century", "signed log d_frontier"])
