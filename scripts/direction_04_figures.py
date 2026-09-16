import sqlite3, json, numpy as np, pandas as pd
ROOT = r"C:\Users\rehanmuh\Desktop\epigraphic_atlas"
con = sqlite3.connect(ROOT + r"\db\atlas.sqlite")
df = pd.read_sql("""
select i.pk, l.name lang, d.code dir, i.century, i.date_start ds, i.date_end de,
       i.q_ord_start qs, i.q_ord_end qe, p.id place_id
from inscriptions i join directions d on d.id=i.direction_id
left join languages l on l.id=i.language_id left join places p on p.id=i.place_id""", con)
df["ltr"] = (df.dir == "1").astype(int); df["width"] = df.de - df.ds
nd = df[df.width <= 100]
out = {}

# pooled curve, narrow-dated
pooled = nd.groupby("century").agg(n=("ltr", "size"), ltr=("ltr", "mean"),
                                   bou=("dir", lambda s: (s == "2").mean())).loc[-8:-1]
out["pooled"] = pooled.round(4).to_dict("index")

# per-language curves + entropy
def H(p):
    p = p[p > 0]; return float(-(p*np.log2(p)).sum())
langs = ["Etruscan", "Greek", "Messapic", "Cisalpine Celtic", "Raetic", "Faliscan", "Latin"]
per = {}
for lg in langs:
    rows = {}
    for c in range(-8, 0):
        s = nd[(nd.lang == lg) & (nd.century == c)]
        if len(s) >= 15:
            rows[c] = dict(n=len(s), ltr=round(s.ltr.mean(), 4),
                           H=round(H(s.dir.value_counts(normalize=True).values), 4))
    per[lg] = rows
out["per_language"] = per
print(json.dumps(out, indent=1))
print("\ntotals: corpus=%d, direction-coded=%d, coded+coords=%d, narrow<=100y=%d" %
      (pd.read_sql("select count(*) c from inscriptions", con).c[0], len(df),
       pd.read_sql("select count(*) c from inscriptions where direction_id is not null and latitude is not null", con).c[0],
       len(nd)))
print("places:", pd.read_sql("select count(*) c from places", con).c[0],
      "| coded places:", df.place_id.nunique())
json.dump(out, open(r"C:\Users\rehanmuh\AppData\Local\Temp\claude\C--Users-rehanmuh-Desktop-epigraphic-atlas\c80d5db9-ef6e-45c2-b4d6-b3e0cbb80b72\scratchpad\figs.json", "w"), indent=1)
