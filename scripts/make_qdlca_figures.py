#!/usr/bin/env python3
"""
The four figures of the QDLCA27 abstract (abstract/qdacl/).

    fig1_sources.png           the evidence behind E(s,t): one map per source
                               (points coloured by earliest attested bin) and
                               a strip of evidence per 25-year bin per source
    fig2_economic_field.png    the latent field E(s,t) at four moments
    fig3_contrasts.png         economy contrasts vs Etruscan, language model
    fig4_time.png              (a) per-century alignment of Latin/Greek with
                               E; (b) Hovmoller of the fitted Latin-language
                               share (both language model)

Figures 3 and 4 use the LANGUAGE model only (user request, 7 Sep 2026); the
alphabet model is not drawn. Everything is read from
db/adoption_multinomial_lang{.json,_grid.npz}, data/economy/ (source points and 25-year time weights, exactly the
tables scripts/economy_field.py fits) and data/economy/field_v03.

All labels are in Title Case and every background is white so the panels
sit flat on the page.

    python scripts/make_qdlca_figures.py      (matplotlib -> py 3.14)
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

OUT = ROOT / "abstract" / "qdacl"
ECON = ROOT / "data" / "economy"
FIELD_CSV = ECON / "field_v03" / "economic_field_25yr_v03.csv"

INK, MUTED, GRID, SURF, RULE = "#0b0b0b", "#898781", "#e1e0d9", "#ffffff", "#c3c2b7"
LAND = "#efeeea"                              # land mask behind point maps
ORANGE, BLUE = "#eb6834", "#2a78d6"          # Latin, Greek (validated pair)
CMAP_E = LinearSegmentedColormap.from_list(
    "ediv", ["#104281", "#3987e5", "#b7d3f6", "#f0efec",
             "#f2b3b2", "#e34948", "#8c1d1c"])
CMAP_LAT = LinearSegmentedColormap.from_list(
    "latseq", ["#fcf5f1", "#fde3d7", "#f5a983", "#eb6834", "#b84515",
               "#6e2a0c"])
# ordinal blue ramp (palette steps 250 -> 700): earliest bin light, latest dark
CMAP_DATE = LinearSegmentedColormap.from_list(
    "dateord", ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#104281",
                "#0d366b"])

NICE = {"latin": "Latin", "greek": "Greek", "messapic": "Messapic",
        "oscan": "Oscan", "venetic": "Venetic", "raetic": "Raetic",
        "lepontic": "Lepontic / Cisalpine Celtic"}
# script-family key -> language-model key
LANG_KEY = {"lepontic": "cisalpine_celtic"}

# source key (economy_field.py) -> panel label
SOURCE_LABEL = {
    "urban_city": "Cities",
    "ancient_port": "Ports",
    "maritime_shipwreck": "Shipwrecks",
    "amphora_trade": "Amphora Sites",
    "mining_production": "Mines",
    "archaeological_occupation": "Radiocarbon Sites",
}
BINS = np.arange(-700, 0, 25)

plt.rcParams.update({"font.family": "DejaVu Sans", "axes.titleweight": "normal"})


def style(ax, grid=False):
    ax.set_facecolor(SURF)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(RULE)
    ax.tick_params(colors=MUTED, labelsize=7.5)
    if grid:
        ax.grid(True, color=GRID, lw=0.5)
        ax.set_axisbelow(True)


def map_ax(ax):
    style(ax)
    ax.set_aspect(1.34)
    ax.set_xlim(6.4, 19.1)
    ax.set_ylim(36.4, 47.2)
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ("left", "bottom"):
        ax.spines[s].set_visible(False)


def colorbar(fig, mappable, ax, label, **kw):
    cb = fig.colorbar(mappable, ax=ax, **kw)
    cb.set_label(label, fontsize=7.5, color=MUTED)
    cb.ax.tick_params(labelsize=7, colors=MUTED)
    cb.outline.set_visible(False)
    return cb


def land_mask():
    """Land cells of the field grid, as (lons, lats, Z) for pcolormesh."""
    g = pd.read_csv(FIELD_CSV, usecols=["bin_start_year", "longitude",
                                        "latitude", "is_land"])
    g = g[g.bin_start_year == -700]
    lons = np.arange(6.5, 19.01, 0.25)
    lats = np.arange(35.5, 47.51, 0.25)
    Z = np.full((len(lats), len(lons)), np.nan)
    ii = np.rint((g.latitude.values - 35.5) / 0.25).astype(int)
    jj = np.rint((g.longitude.values - 6.5) / 0.25).astype(int)
    Z[ii, jj] = np.where(g.is_land.values, 1.0, np.nan)
    return lons, lats, Z


def source_tables():
    """Per source: points (lon, lat, earliest attested bin) and the total
    evidence weight per 25-year bin, from the tables the field is fitted on."""
    pts, tot = {}, {}

    def first_bin(df, key, col, thr):
        d = df[(df.bin_start_year >= -700) & (df.bin_start_year < 0)
               & (df[col] > thr)]
        f = d.groupby(key).agg(lon=("longitude", "first"),
                               lat=("latitude", "first"),
                               t0=("bin_start_year", "min"))
        return f.reset_index(drop=True)

    def per_bin(df, col):
        s = df[(df.bin_start_year >= -700) & (df.bin_start_year < 0)] \
            .groupby("bin_start_year")[col].sum()
        return s.reindex(BINS, fill_value=0.0).values

    up = pd.read_csv(ECON / "urban_city_time_weights_25yr.csv")
    pts["urban_city"] = first_bin(up, "source_id", "active_probability", 0.5)
    tot["urban_city"] = per_bin(up, "active_probability")

    pt = pd.read_csv(ECON / "ports_time_weights_25yr.csv")
    pts["ancient_port"] = first_bin(pt, "port_id", "active_probability", 0.5)
    tot["ancient_port"] = per_bin(pt, "active_probability")

    am = pd.read_csv(ECON / "amphora_time_weights_25yr.csv",
                     encoding="latin-1")
    pts["amphora_trade"] = first_bin(am, "site", "weighted_evidence", 0)
    tot["amphora_trade"] = per_bin(am, "weighted_evidence")

    sp = pd.read_csv(ECON / "source_points_all.csv")
    xy = sp.assign(sid=sp.source_id.astype(str)) \
        .set_index(["source_type", "sid"])[["longitude", "latitude"]]
    nb = pd.read_csv(ECON / "base_nonurban_source_time_weights_25yr.csv")
    nb["sid"] = nb.source_id.astype(str)
    nb = nb.join(xy, on=["source_type", "sid"])
    occ = nb.source_type == "archaeological_occupation"
    nb.loc[occ, "weight"] = nb.loc[occ, "weight"].clip(upper=1.0)
    for st in ("maritime_shipwreck", "mining_production",
               "archaeological_occupation"):
        d = nb[nb.source_type == st]
        pts[st] = first_bin(d, "sid", "weight", 0)
        tot[st] = per_bin(d, "weight")
    return pts, tot


# ------------------------------------------------------------- figure 1
def fig_sources():
    pts, tot = source_tables()
    lons, lats, land = land_mask()
    keys = list(SOURCE_LABEL)
    fig = plt.figure(figsize=(5.6, 7.0), facecolor=SURF)
    gs = fig.add_gridspec(2, 3, left=0.02, right=0.98, top=0.97, bottom=0.36,
                          wspace=0.02, hspace=0.12)
    gs2 = fig.add_gridspec(1, 1, left=0.17, right=0.9, top=0.22, bottom=0.06)
    sc = None
    for n, k in enumerate(keys):
        ax = fig.add_subplot(gs[n // 3, n % 3])
        ax.pcolormesh(lons, lats, np.ma.masked_invalid(land),
                      cmap=LinearSegmentedColormap.from_list("land", [LAND, LAND]),
                      shading="nearest", rasterized=True)
        p = pts[k]
        sc = ax.scatter(p.lon, p.lat, c=p.t0, cmap=CMAP_DATE, vmin=-700,
                        vmax=-25, s=7 if len(p) < 200 else 4.5, lw=0.25,
                        edgecolors=SURF, zorder=3)
        map_ax(ax)
        ax.set_title(f"{SOURCE_LABEL[k]} (n = {len(p)})", fontsize=8,
                     color=INK, pad=2)
    cax = fig.add_axes([0.3, 0.305, 0.4, 0.014])
    cb = fig.colorbar(sc, cax=cax, orientation="horizontal")
    cb.set_ticks([-700, -500, -300, -100])
    cb.set_ticklabels(["700", "500", "300", "100"])
    cb.set_label("Earliest Attested Bin (Year BCE)", fontsize=7.5, color=MUTED)
    cb.ax.tick_params(labelsize=7, colors=MUTED, length=2)
    cb.outline.set_visible(False)
    # evidence per bin, each source scaled to its own maximum
    ax = fig.add_subplot(gs2[0, 0])
    H = np.vstack([tot[k] / max(tot[k].max(), 1e-12) for k in keys])
    pm = ax.pcolormesh(BINS + 12.5, np.arange(len(keys)), H, cmap=CMAP_LAT,
                       vmin=0, vmax=1, shading="nearest", edgecolors=SURF,
                       lw=0.4)
    style(ax)
    ax.invert_yaxis()
    ax.set_yticks(range(len(keys)), [SOURCE_LABEL[k] for k in keys],
                  fontsize=7, color=INK)
    ax.tick_params(axis="y", length=0)
    ax.set_xticks(range(-700, 0, 100), [f"{-c}" for c in range(-700, 0, 100)])
    ax.set_xlim(-712.5, 12.5)
    ax.set_xlabel("Year BCE", fontsize=8, color=MUTED)
    for s in ("left", "bottom"):
        ax.spines[s].set_visible(False)
    colorbar(fig, pm, ax, "Evidence per Bin\n(Share of Source Maximum)",
             pad=0.02, fraction=0.05)
    fig.savefig(OUT / "fig1_sources.png", dpi=200, bbox_inches="tight",
                pad_inches=0.04, facecolor=SURF)
    plt.close(fig)
    return {k: len(pts[k]) for k in keys}


# ------------------------------------------------------------- figure 2
def fig_field():
    g = pd.read_csv(FIELD_CSV)
    g = g[g.is_land & g.in_support]
    times = [-600, -400, -200, -50]
    fig, axes = plt.subplots(2, 2, figsize=(5.6, 5.6), facecolor=SURF)
    lons = np.arange(6.5, 19.01, 0.25)
    lats = np.arange(35.5, 47.51, 0.25)
    for ax, t in zip(axes.ravel(), times):
        m = g[g.bin_start_year == t]
        Z = np.full((len(lats), len(lons)), np.nan)
        ii = np.rint((m.latitude.values - 35.5) / 0.25).astype(int)
        jj = np.rint((m.longitude.values - 6.5) / 0.25).astype(int)
        Z[ii, jj] = m.E_mean.values
        sc = ax.pcolormesh(lons, lats, np.ma.masked_invalid(Z), cmap=CMAP_E,
                           vmin=-2.2, vmax=2.2, shading="nearest")
        map_ax(ax)
        ax.set_title(f"{-t} BCE", fontsize=9.5, color=INK, pad=3)
    fig.subplots_adjust(left=0.01, right=0.86, top=0.95, bottom=0.02,
                        wspace=0.02, hspace=0.12)
    cax = fig.add_axes([0.885, 0.2, 0.025, 0.6])
    cb = fig.colorbar(sc, cax=cax)
    cb.set_label("E (Posterior Mean, SD Units)", fontsize=7.5, color=MUTED)
    cb.ax.tick_params(labelsize=7, colors=MUTED)
    cb.outline.set_visible(False)
    fig.savefig(OUT / "fig2_economic_field.png", dpi=200)
    plt.close(fig)


# ------------------------------------------------------------- figure 3
def fig_contrasts():
    """Language model only: contrast of each language vs Etruscan."""
    lng = json.loads((ROOT / "db" / "adoption_multinomial_lang.json").read_text())
    keys = ["latin", "greek", "messapic", "oscan", "venetic", "lepontic",
            "raetic"]
    keys = sorted(keys, key=lambda k: -lng["beta_E"][LANG_KEY.get(k, k)]
                  ["contrast_vs_etruscan"])
    fig, ax = plt.subplots(figsize=(5.6, 3.6), facecolor=SURF)
    style(ax)
    ax.grid(True, axis="x", color=GRID, lw=0.5)
    ax.set_axisbelow(True)
    rows = []
    for i, k in enumerate(keys):
        m = lng["beta_E"][LANG_KEY.get(k, k)]
        c, se = m["contrast_vs_etruscan"], m["contrast_se"]
        col = ORANGE if k == "latin" else BLUE if k == "greek" else INK
        ax.plot([c - 1.96 * se, c + 1.96 * se], [-i, -i], color=col, lw=1.6,
                solid_capstyle="round")
        ax.plot(c, -i, "o", ms=6, mfc=col, mec=col, mew=1.4)
        rows.append((k, "language", c, se))
    ax.axvline(0, color=RULE, lw=1)
    ax.set_yticks([-i for i in range(len(keys))], [NICE[k] for k in keys],
                  fontsize=8.5, color=INK)
    ax.set_ylim(-len(keys) + 0.5, 0.5)
    ax.set_xlabel("Contrast vs Etruscan: Change in Log-Odds per 1 SD of E "
                  "(95% CI)", fontsize=8, color=MUTED)
    fig.tight_layout()
    fig.savefig(OUT / "fig3_contrasts.png", dpi=200)
    plt.close(fig)
    return rows


# ------------------------------------------------------------- figure 4
def fig_time():
    """Language model only: (a) per-century r for Latin and Greek,
    (b) Hovmoller of the fitted Latin-language share."""
    lng = json.loads((ROOT / "db" / "adoption_multinomial_lang.json").read_text())
    g = np.load(ROOT / "db" / "adoption_multinomial_lang_grid.npz")
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(5.6, 5.4), facecolor=SURF,
                                  gridspec_kw={"height_ratios": [1, 1.05],
                                               "hspace": 0.5})
    # (a) per-century correlation, Latin and Greek
    style(ax, grid=True)
    corr = lng["grid"]["logshare_vs_E_correlation"]
    last = {}
    for k, col in [("latin", ORANGE), ("greek", BLUE)]:
        per = corr[k]["by_century"]
        cents = sorted(int(c) for c in per)
        ax.plot([c + 50 for c in cents], [per[str(c)] for c in cents],
                color=col, lw=1.8, marker="o", ms=4, mfc=SURF, mew=1.3,
                mec=col)
        last[k] = per[str(cents[-1])]
    ax.axhline(0, color=RULE, lw=1)
    ax.set_xticks(range(-700, 0, 100), [f"{-c}s" for c in range(-700, 0, 100)])
    ax.set_xlim(-720, 40)
    ax.set_ylabel("r, Fitted Log-Share vs E", fontsize=8, color=MUTED)
    ax.set_title("(a)  Alignment of Latin and Greek with the Economy, by Century",
                 fontsize=8.5, color=INK, loc="left")
    handles = [Line2D([], [], color=ORANGE, lw=1.8, label="Latin"),
               Line2D([], [], color=BLUE, lw=1.8, label="Greek")]
    ax.legend(handles=handles, frameon=False, fontsize=7, loc="lower left")
    ax.text(-38, last["latin"], "Latin", color=ORANGE, fontsize=7.5,
            va="center", ha="left")
    ax.text(-38, last["greek"], "Greek", color=BLUE, fontsize=7.5,
            va="center", ha="left")
    # (b) Hovmoller of the fitted Latin-language share
    bins = np.unique(g["bin_start"])
    lats = np.unique(g["lat"])
    H = np.full((len(lats), len(bins)), np.nan)
    for j, b in enumerate(bins):
        m = g["bin_start"] == b
        for i, la in enumerate(lats):
            mm = m & (g["lat"] == la)
            if mm.any():
                H[i, j] = g["p_latin"][mm].mean()
    pm = ax2.pcolormesh(bins + 12.5, lats, H, cmap=CMAP_LAT, vmin=0, vmax=1,
                        shading="nearest")
    style(ax2)
    ax2.axvline(-200, color=INK, lw=0.8, ls=(0, (2, 2)))
    ax2.set_xticks(range(-700, 0, 100), [f"{-c}" for c in range(-700, 0, 100)])
    ax2.set_xlim(-720, 40)
    ax2.set_xlabel("Year BCE", fontsize=8, color=MUTED)
    ax2.set_ylabel("Latitude (°N)", fontsize=8, color=MUTED)
    ax2.set_title("(b)  Fitted Latin-Language Share, Mean Along Each Latitude",
                  fontsize=8.5, color=INK, loc="left")
    colorbar(fig, pm, ax2, "Latin Share", shrink=0.9, pad=0.02)
    fig.savefig(OUT / "fig4_time.png", dpi=200, bbox_inches="tight",
                facecolor=SURF)
    plt.close(fig)


# ------------------------------------------------------------- figure 5
def fig_saturation():
    """Language model: fitted Latin-language share on the map at six
    moments, and its mean over the grid per 25-year bin (the saturation
    curve). Not yet placed in the tex."""
    g = np.load(ROOT / "db" / "adoption_multinomial_lang_grid.npz")
    lons_m, lats_m, land = land_mask()
    lons = np.arange(6.5, 19.01, 0.25)
    lats = np.arange(35.5, 47.51, 0.25)
    times = [-300, -200, -150, -100, -50, -25]
    fig = plt.figure(figsize=(5.6, 6.4), facecolor=SURF)
    gs = fig.add_gridspec(2, 3, left=0.02, right=0.98, top=0.97, bottom=0.43,
                          wspace=0.02, hspace=0.12)
    gs2 = fig.add_gridspec(1, 1, left=0.12, right=0.86, top=0.24, bottom=0.07)
    pm = None
    for n, t in enumerate(times):
        ax = fig.add_subplot(gs[n // 3, n % 3])
        ax.pcolormesh(lons_m, lats_m, np.ma.masked_invalid(land),
                      cmap=LinearSegmentedColormap.from_list("land", [LAND, LAND]),
                      shading="nearest", rasterized=True)
        m = g["bin_start"] == t
        Z = np.full((len(lats), len(lons)), np.nan)
        ii = np.rint((g["lat"][m] - 35.5) / 0.25).astype(int)
        jj = np.rint((g["lon"][m] - 6.5) / 0.25).astype(int)
        Z[ii, jj] = g["p_latin"][m]
        pm = ax.pcolormesh(lons, lats, np.ma.masked_invalid(Z), cmap=CMAP_LAT,
                           vmin=0, vmax=1, shading="nearest", zorder=2)
        map_ax(ax)
        ax.set_title(f"{-t} BCE", fontsize=8.5, color=INK, pad=2)
    cax = fig.add_axes([0.3, 0.375, 0.4, 0.014])
    cb = fig.colorbar(pm, cax=cax, orientation="horizontal")
    cb.set_label("Fitted Latin-Language Share", fontsize=7.5, color=MUTED)
    cb.ax.tick_params(labelsize=7, colors=MUTED, length=2)
    cb.outline.set_visible(False)
    # saturation curve: mean share over the grid, per bin
    ax = fig.add_subplot(gs2[0, 0])
    style(ax, grid=True)
    bins = np.unique(g["bin_start"])
    mean = [g["p_latin"][g["bin_start"] == b].mean() for b in bins]
    q10 = [np.quantile(g["p_latin"][g["bin_start"] == b], 0.1) for b in bins]
    q90 = [np.quantile(g["p_latin"][g["bin_start"] == b], 0.9) for b in bins]
    x = bins + 12.5
    ax.fill_between(x, q10, q90, color=ORANGE, alpha=0.18, lw=0)
    ax.plot(x, mean, color=ORANGE, lw=1.8)
    ax.axvline(-200, color=INK, lw=0.8, ls=(0, (2, 2)))
    ax.set_xticks(range(-700, 0, 100), [f"{-c}" for c in range(-700, 0, 100)])
    ax.set_xlim(-712.5, 12.5)
    ax.set_ylim(0, 1)
    ax.set_yticks([0, 0.5, 1])
    ax.set_xlabel("Year BCE", fontsize=8, color=MUTED)
    ax.set_ylabel("Latin Share", fontsize=8, color=MUTED)
    ax.set_title("Mean Over the Grid (Band: 10th to 90th Percentile of Cells)",
                 fontsize=8, color=INK, loc="left", pad=4)
    fig.savefig(OUT / "fig5_latin_saturation.png", dpi=200,
                bbox_inches="tight", pad_inches=0.04, facecolor=SURF)
    plt.close(fig)
    return dict(zip(bins.tolist(), [round(float(v), 3) for v in mean]))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for old in ("fig1_economic_field.png", "fig2_contrasts.png",
                "fig3_time.png"):
        (OUT / old).unlink(missing_ok=True)
    counts = fig_sources()
    fig_field()
    rows = fig_contrasts()
    fig_time()
    sat = fig_saturation()
    print("sources:", ", ".join(f"{SOURCE_LABEL[k]} {n}" for k, n in counts.items()))
    print("mean Latin-language share:",
          ", ".join(f"{-b} BCE {v:.2f}" for b, v in sat.items() if b % 100 == 0))
    for k, lab, c, se in rows:
        print(f"{k:10s} {lab:9s} {c:+.2f} ({se:.2f})")
    print(f"wrote 5 figures to {OUT} (fig5 not yet in the tex)")


if __name__ == "__main__":
    main()
