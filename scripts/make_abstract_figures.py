#!/usr/bin/env python3
"""
Emit the three figures used in the QDLCA27 abstract, in both output formats.

Numbers come straight from db/direction_model.json (the model ladder and the
fitted trajectories) and db/direction_unit.json (the observed rates by language
and alphabet), so the figures cannot drift from the analysis. Run
scripts/direction_model.py first. Each panel is built once as a list of drawing
primitives in
centimetres; one backend writes it as TikZ (for the LaTeX/PDF version), the
other rasterises it with Pillow (for the .docx the call for papers asks for),
so the two versions of the abstract cannot drift from each other either.
pgfplots is not in this TinyTeX install, so the axes are drawn by hand.

    py -3.12 scripts/make_abstract_figures.py
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "db" / "direction_unit.json"
# `--model PATH` builds the figures from another run's output, so a provisional
# fit can be drawn without touching the file the finished run will write.
MODEL = Path(sys.argv[sys.argv.index("--model") + 1]) if "--model" in sys.argv \
    else ROOT / "db" / "direction_model.json"
OUT = ROOT / "abstract" / "figures.tex"
PNG = [ROOT / "abstract" / f"fig{i}.png" for i in (1, 2, 3)]

THIN = " "

# Figure 1, top: the models scored, and whether each is fitted on the
# georeferenced subset (dagger) rather than on all 12,646 records.
SCORE = [("nothing", "M0 intercept", False),
         ("time", "M1 + century", False),
         ("space and time", "M1b space-time kernel", False),
         ("language", "M2 + language", False),
         ("alphabet", "M2a alphabet, no language", False),
         ("language + alphabet", "M3 + alphabet", False),
         ("+ alphabet × time", "M4 + alphabet x time", False),
         ("+ findspot", "M5 + findspot", False),
         ("+ geography, space-time", "M7 + space-time kernel", False)]
COLS = [("bits", "log_loss_bits", "{:.3f}"), ("prec.", "precision", "{:.3f}"),
        ("recall", "recall", "{:.3f}"), ("F1", "f1", "{:.3f}"),
        ("PR-AUC", "pr_auc", "{:.3f}")]

# Figure 1, bottom: where the variance of the linear predictor sits. No
# "time curve" row: the final model carries no global curve — time lives in
# the per-alphabet curves and in the field.
VAR = [("alphabet", "alphabet"), ("language", "language"),
       ("alphabet × time", "alphabet x time"), ("findspot", "findspot"),
       ("geography", "geography"), ("space-time field", "space-time field")]

# Figure 3: every alphabet with a fitted curve worth printing, and "" — the
# corpus pooled, which is the curve the handbooks describe.
POOLED = ("", "all inscriptions")

LN2 = 0.6931471805599453

EW, ERH = 6.4, 0.34      # figure 1 axis width and row height, cm
W, H = 7.4, 3.3          # figure 2 plotting box, cm
BW = 6.9                 # figure 3 axis width, cm
ROWH, GROUPGAP = 0.30, 0.22
PT = 8.0                 # \scriptsize in an 11pt document, in points


# --------------------------------------------------------------------------
# colour: every mark's colour is the norm it holds, so the two backends have
# to agree on what "rtl!30!mid" resolves to in RGB.
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Col:
    spec: str                      # TikZ colour expression
    rgb: tuple[int, int, int]


def _hex(h):
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


RTL = Col("rtl", _hex("A8452A"))
LTR = Col("ltr", _hex("1F74A8"))
MID = Col("mid", _hex("8D918B"))
RULE = Col("rule", _hex("C9CCC6"))
INK = Col("ink", _hex("2B2B2B"))       # the pooled curve, outside the palette
WHITE = (255, 255, 255)

# Categorical palette for figure 3, validated with the dataviz six-checks
# script (all hard gates pass; the contrast WARN is relieved by the legend's
# ink labels). Ten alphabets exceed the 8-hue cap, so hue encodes the script
# family and the two-member families (Greek, Etruscan) take a light and a deep
# step of one hue, adjacent in the legend so the lightness gap separates them.
CAT = [("Republican Latin alphabet", "Latin", Col("catA", _hex("2A78D6"))),
       ("Lepontic alphabet", "Lepontic", Col("catB", _hex("EB6834"))),
       ("Greek alphabet (blue type)", "Greek, blue type", Col("catC", _hex("1BAF7A"))),
       ("Greek alphabet (red type)", "Greek, red type", Col("catD", _hex("046A41"))),
       ("Faliscan alphabet", "Faliscan", Col("catE", _hex("EDA100"))),
       ("Southern Etruscan alphabet", "S. Etruscan", Col("catF", _hex("E34948"))),
       ("Northern Etruscan alphabet", "N. Etruscan", Col("catG", _hex("8F2322"))),
       ("Magrè alphabet", "Magrè", Col("catH", _hex("E87BA4"))),
       ("Sanzeno alphabet", "Sanzeno", Col("catI", _hex("4A3AA7"))),
       ("Messapic alphabet", "Messapic", Col("catJ", _hex("008300")))]

DEFS = [RTL, LTR, MID, RULE, INK] + [c for _, _, c in CAT]


def mix(a: Col, pct: int, b: Col | None = None) -> Col:
    """TikZ `a!pct!b`; with b omitted, `a!pct` — that is, pct% a on white."""
    other = b.rgb if b else WHITE
    f = pct / 100
    rgb = tuple(round(f * x + (1 - f) * y) for x, y in zip(a.rgb, other))
    return Col(f"{a.spec}!{pct}!{b.spec}" if b else f"{a.spec}!{pct}", rgb)


def colour(p):
    """Diverging: copper at p=0, neutral at 0.5, lapis at 1 — the mark's colour is its norm."""
    if p < 0.5:
        return mix(RTL, round(100 - p * 2 * 100), MID)
    return mix(LTR, round((p - 0.5) * 2 * 100), MID)


# --------------------------------------------------------------------------
# drawing primitives, in centimetres, y up
# --------------------------------------------------------------------------
@dataclass
class Rect:
    x0: float; y0: float; x1: float; y1: float; col: Col; fill: bool = False


@dataclass
class Line:
    pts: list[tuple[float, float]]; col: Col
    width: float | None = None; dashed: bool = False; opacity: float = 1.0


@dataclass
class Poly:
    pts: list[tuple[float, float]]; col: Col


@dataclass
class Dot:
    x: float; y: float; r: float; col: Col


@dataclass
class Text:
    x: float; y: float; s: str
    anchor: str = "left"            # side of the point the text sits on
    sep: float = 2.0                # inner sep, points
    col: Col | None = None
    rotate: int = 0
    bold: bool = False


@dataclass
class Canvas:
    ops: list = field(default_factory=list)

    def add(self, op):
        self.ops.append(op)
        return op


# --------------------------------------------------------------------------
# backend 1: TikZ
# --------------------------------------------------------------------------
def esc(s: str) -> str:
    s = s.replace("%", r"\%").replace("è", r"\`e").replace(THIN, r"\,")
    s = s.replace("×", r"$\times$").replace("†", r"$\dagger$")
    return s.replace("BCE", r"\textsc{bce}")


def tikz(canvas: Canvas, name: str) -> list[str]:
    L = [f"\\newcommand{{\\{name}}}{{%",
         "\\begin{tikzpicture}[x=1cm,y=1cm,font=\\scriptsize]"]
    for op in canvas.ops:
        if isinstance(op, Rect):
            L.append(f"  \\{'fill' if op.fill else 'draw'}[{op.col.spec}] "
                     f"({op.x0:g},{op.y0:g}) rectangle ({op.x1:g},{op.y1:g});")
        elif isinstance(op, Line):
            o = [op.col.spec]
            if op.dashed:
                o.append("dashed")
            if op.width:
                o.append(f"line width={op.width}pt")
            if op.opacity < 1:
                o.append(f"opacity={op.opacity:g}")
            path = " -- ".join(f"({x:.3f},{y:.3f})" for x, y in op.pts)
            L.append(f"  \\draw[{','.join(o)}] {path};")
        elif isinstance(op, Poly):
            path = " -- ".join(f"({x:.3f},{y:.3f})" for x, y in op.pts)
            L.append(f"  \\fill[{op.col.spec}] {path} -- cycle;")
        elif isinstance(op, Dot):
            L.append(f"  \\fill[{op.col.spec}] ({op.x:.3f},{op.y:.3f}) "
                     f"circle ({op.r:.3f});")
        elif isinstance(op, Text):
            o = ([f"rotate={op.rotate}"] if op.rotate else []) + [op.anchor]
            o.append(f"inner sep={op.sep:g}pt")
            if op.col:
                o.append(f"text={op.col.spec}")
            body = esc(op.s)
            if op.bold:
                body = f"\\textbf{{{body}}}"
            L.append(f"  \\node[{','.join(o)}] at ({op.x:.3f},{op.y:.3f}) {{{body}}};")
    L.append("\\end{tikzpicture}}")
    return L


# --------------------------------------------------------------------------
# backend 2: PNG, for the .docx
# --------------------------------------------------------------------------
DPI, SS = 300, 3         # render at 3x and downsample; Pillow does not antialias
FONTS = [Path(r"C:\Windows\Fonts\times.ttf"), Path(r"C:\Windows\Fonts\timesbd.ttf")]


def render(canvas: Canvas, path: Path):
    from PIL import Image, ImageDraw, ImageFont

    scale = DPI / 2.54 * SS                       # px per cm
    fpx = round(PT / 72 * DPI * SS)
    font = ImageFont.truetype(str(FONTS[0]), fpx)
    fontb = ImageFont.truetype(str(FONTS[1]), fpx)
    pad = 0.05                                    # cm of air around everything

    def metrics(op: Text):
        f = fontb if op.bold else font
        w = f.getlength(op.s) / scale
        h = (f.getbbox("Ag")[3] - f.getbbox("Ag")[1]) / scale
        if op.rotate:
            w, h = h, w
        sep = op.sep / 72 * 2.54
        if op.anchor == "left":
            x0, y0 = op.x - sep - w, op.y - h / 2
        elif op.anchor == "right":
            x0, y0 = op.x + sep, op.y - h / 2
        elif op.anchor == "below":
            x0, y0 = op.x - w / 2, op.y - sep - h
        else:                                      # above
            x0, y0 = op.x - w / 2, op.y + sep
        if op.rotate:                              # rotate=90 + above reads up the axis
            x0, y0 = op.x - sep - w, op.y - h / 2
        return x0, y0, w, h

    # pass 1: bounding box in cm
    xs, ys = [], []
    for op in canvas.ops:
        if isinstance(op, Rect):
            xs += [op.x0, op.x1]; ys += [op.y0, op.y1]
        elif isinstance(op, (Line, Poly)):
            xs += [p[0] for p in op.pts]; ys += [p[1] for p in op.pts]
        elif isinstance(op, Dot):
            xs += [op.x - op.r, op.x + op.r]; ys += [op.y - op.r, op.y + op.r]
        elif isinstance(op, Text):
            x0, y0, w, h = metrics(op)
            xs += [x0, x0 + w]; ys += [y0, y0 + h]
    minx, maxx, miny, maxy = min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad

    def X(x): return (x - minx) * scale
    def Y(y): return (maxy - y) * scale

    img = Image.new("RGB", (round((maxx - minx) * scale), round((maxy - miny) * scale)),
                    WHITE)
    d = ImageDraw.Draw(img)

    def rgb(col: Col, opacity=1.0):
        if opacity >= 1:
            return col.rgb
        return tuple(round(opacity * c + (1 - opacity) * 255) for c in col.rgb)

    def seg(p, q, col, wpt, dashed, opacity):
        wpx = max(1, round(wpt / 72 * DPI * SS))
        c = rgb(col, opacity)
        if not dashed:
            d.line([X(p[0]), Y(p[1]), X(q[0]), Y(q[1])], fill=c, width=wpx)
            return
        dash = 3 / 72 * 2.54                       # TikZ `dashed`: 3pt on, 3pt off
        dx, dy = q[0] - p[0], q[1] - p[1]
        length = (dx * dx + dy * dy) ** 0.5
        n, t = max(1, int(length / dash)), 0.0
        for i in range(n):
            if i % 2 == 0:
                a = (p[0] + dx * t / length, p[1] + dy * t / length)
                e = min(t + dash, length)
                b = (p[0] + dx * e / length, p[1] + dy * e / length)
                d.line([X(a[0]), Y(a[1]), X(b[0]), Y(b[1])], fill=c, width=wpx)
            t += dash

    for op in canvas.ops:
        if isinstance(op, Rect):
            if op.fill:
                d.rectangle([X(op.x0), Y(op.y1), X(op.x1), Y(op.y0)], fill=rgb(op.col))
            else:
                for p, q in (((op.x0, op.y0), (op.x1, op.y0)), ((op.x1, op.y0), (op.x1, op.y1)),
                             ((op.x1, op.y1), (op.x0, op.y1)), ((op.x0, op.y1), (op.x0, op.y0))):
                    seg(p, q, op.col, 0.4, False, 1.0)
        elif isinstance(op, Line):
            for p, q in zip(op.pts, op.pts[1:]):
                seg(p, q, op.col, op.width or 0.4, op.dashed, op.opacity)
        elif isinstance(op, Poly):
            d.polygon([(X(x), Y(y)) for x, y in op.pts], fill=rgb(op.col))
        elif isinstance(op, Dot):
            r = op.r * scale
            d.ellipse([X(op.x) - r, Y(op.y) - r, X(op.x) + r, Y(op.y) + r],
                      fill=rgb(op.col))
        elif isinstance(op, Text):
            f = fontb if op.bold else font
            c = rgb(op.col) if op.col else (26, 26, 26)
            x0, y0, w, h = metrics(op)
            if op.rotate:
                tw, th = round(f.getlength(op.s)) + 4, round(fpx * 1.4)
                tile = Image.new("RGBA", (tw, th), (255, 255, 255, 0))
                ImageDraw.Draw(tile).text((2, th / 2), op.s, font=f, fill=c, anchor="lm")
                tile = tile.rotate(op.rotate, expand=True)
                img.paste(tile, (round(X(x0)), round(Y(y0 + h))), tile)
            else:
                d.text((X(x0), Y(y0 + h / 2)), op.s, font=f, fill=c, anchor="lm")

    img = img.resize((img.width // SS, img.height // SS), Image.LANCZOS)
    img.save(path, dpi=(DPI, DPI))
    return img.size


# --------------------------------------------------------------------------
# the panels
# --------------------------------------------------------------------------
def panel_scorecard(ladder, var) -> Canvas:
    """Held-out scores for each model, and where the fitted variance sits."""
    scores = {k: v["metrics"] for k, v in ladder.items()}
    c = Canvas()
    RH, COLW, X0 = 0.32, 1.02, 0.0
    top = len(SCORE) * RH
    xs = [X0 + (i + 1) * COLW for i in range(len(COLS))]

    for x, (head, _, _) in zip(xs, COLS):
        c.add(Text(x, top, head, "above", 3))
    c.add(Line([(X0 - 0.05, top), (xs[-1] + 0.05, top)], mix(RULE, 80)))

    y = top
    for label, key, sub in SCORE:
        m = scores[key]
        y -= RH
        cy = y + RH / 2
        c.add(Text(X0, cy, label + ("†" if sub else ""), "left", 4))
        for x, (_, field_, fmt) in zip(xs, COLS):
            c.add(Text(x, cy, fmt.format(m[field_]), "left", 3))
    c.add(Line([(X0 - 0.05, 0), (xs[-1] + 0.05, 0)], mix(RULE, 80)))

    # variance shares, on the same left edge as the table
    VW = 3.4
    y -= 0.62
    c.add(Text(X0, y, "share of fitted variance", "left", 4, bold=True))
    y -= 0.18                                # clear of the first bar's label
    # Shares are covariance-allocated, so they sum to 100% and a block that
    # leans against the fit can carry a (small) negative share.
    for label, key in VAR:
        v = var.get(key, 0.0)
        y -= 0.30
        cy = y + 0.15
        c.add(Text(X0, cy, label, "left", 4))
        if v > 0.001:
            c.add(Rect(X0 + 0.12, cy - 0.075, X0 + 0.12 + v * VW, cy + 0.075,
                       mix(MID, 45), fill=True))
        c.add(Text(X0 + 0.18 + max(v * VW, 0.0), cy,
                   f"{v*100:+.1f}%" if v < 0 else f"{v*100:.1f}%", "right", 2))
    return c


def panel_field(grid) -> Canvas:
    """The field as a continuous surface over Italy, before and after the
    language and alphabet terms. Each cell of the raster is coloured by the
    field's log-odds through the norm scale; the mask to within 55 km of a
    findspot keeps extrapolation off the page and traces the coastline."""
    import math
    c = Canvas()
    PW, GAP, LAB = 2.0, 0.14, 1.55
    xs, ys = grid["x"], grid["y"]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    ph = PW * (y1 - y0) / (x1 - x0)
    half = grid["cell_km"] / (x1 - x0) * PW * 0.56     # slight overlap, no seams

    for r, (tag, name) in enumerate([("alone", "space and time alone"),
                                     ("full", "after language and alphabet")]):
        ytop = (1 - r) * (ph + 0.55)
        c.add(Text(LAB - 0.12, ytop + ph / 2, name, "left", 3, bold=True))
        for i, slice_ in enumerate(grid[tag]):
            ox = LAB + i * (PW + GAP)
            for gx, gy, v in zip(xs, ys, slice_["logodds"]):
                px = ox + (gx - x0) / (x1 - x0) * PW
                py = ytop + (gy - y0) / (y1 - y0) * ph
                p = 1.0 / (1.0 + math.exp(-v))
                c.add(Rect(px - half, py - half, px + half, py + half,
                           colour(p), fill=True))
            if r == 0:
                c.add(Text(ox + PW / 2, ytop + ph, f"{-slice_['year']} BCE",
                           "above", 3))
    return c


def panel_ladder(ladder) -> Canvas:
    """What each layer of the model buys, held out and blocked by findspot."""
    c = Canvas()
    HE = len(LADDER) * ERH
    bar = mix(MID, 45)
    for v in (0, .25, .5, .75, 1):
        x = v * EW
        c.add(Line([(x, 0), (x, HE)], RULE if v == 1 else mix(RULE, 60), dashed=(v == 1)))
        c.add(Text(x, HE, f"{v:g}", "above", 2))
    y = HE
    for label, key in LADDER:
        v = ladder[key]["cv_neg_loglik"] / LN2          # nats per record -> bits
        y -= ERH
        cy = y + ERH / 2
        c.add(Text(0, cy, label, "left", 3))
        c.add(Rect(0, cy - 0.085, v * EW, cy + 0.085, bar, fill=True))
        c.add(Text(v * EW, cy, f"{v:.3f}", "right", 3))
    c.add(Text(EW / 2, 0, "held-out bits per inscription", "below", 3))
    return c


def panel_fitted(traj, quarters) -> Canvas:
    """Fitted P(dextroverse) per alphabet through time, with bootstrap bands."""
    c = Canvas()
    years = [-700, -25]

    def px(year): return (year - years[0]) / (years[1] - years[0]) * W

    c.add(Rect(0, 0, W, H, RULE))
    for v in (0, .25, .5, .75, 1):
        yy = v * H
        c.add(Line([(0, yy), (W, yy)], RULE if v == .5 else mix(RULE, 60),
                   dashed=(v == .5)))
        c.add(Text(0, yy, f"{int(v*100)}%", "left", 2))
    for year in (-700, -600, -500, -400, -300, -200, -100):
        c.add(Text(px(year), 0, str(-year), "below", 2))
    c.add(Text(W / 2, 0, "BCE", "below", 13))
    c.add(Text(0, H / 2, "% dextroverse", "above", 24, rotate=90))

    # Colour is identity, from the validated categorical palette; bands stay
    # faint so ten of them can overlap without mud. Pooled goes on top in ink.
    series = [(key, label, col, False) for key, label, col in CAT]
    series.append((POOLED[0], "all inscriptions", INK, True))
    drawn = []
    for key, label, col, pooled in series:
        rows = traj.get(key) or []
        if not rows:
            continue
        if rows[0].get("p_lo") is not None:
            band = [(px(r["year"]), r["p_lo"] * H) for r in rows]
            band += [(px(r["year"]), r["p_hi"] * H) for r in reversed(rows)]
            c.add(Poly(band, mix(col, 10 if pooled else 13)))
        drawn.append((rows, col, pooled))
    for rows, col, pooled in sorted(drawn, key=lambda d: d[2]):
        c.add(Line([(px(r["year"]), r["p"] * H) for r in rows], col,
                   width=1.5 if pooled else 0.9))

    # legend: swatch + ink label, fixed order — no matching lines to labels
    ly = H + 0.05
    for key, label, col, pooled in [series[-1]] + series[:-1]:
        ly -= 0.295
        c.add(Line([(W + 0.25, ly), (W + 0.60, ly)], col,
                   width=1.8 if pooled else 1.3))
        c.add(Text(W + 0.66, ly, label, "right", 1, bold=pooled))
    return c


def main():
    m = json.loads(MODEL.read_text(encoding="utf-8"))
    panels = [panel_scorecard(m["ladder"], m["variance_share_full_model"]),
              panel_field(m["field_grid"]),
              panel_fitted(m["trajectories"], m["quarter_edges"])]

    L = ["% Generated by scripts/make_abstract_figures.py — do not edit by hand."]
    L += [f"\\definecolor{{{c.spec}}}{{HTML}}{{{'%02X%02X%02X' % c.rgb}}}" for c in DEFS]
    L.append("")
    # Each panel is wrapped as a command so the caller can put it in its own
    # float with its own caption; emitting bare pictures detaches the captions.
    L += tikz(panels[0], "panelScorecard") + [""]
    # The field raster is ~21k cells; as TikZ paths that exceeds TeX's main
    # memory, and a raster is what it is anyway — the PDF embeds the same
    # 300 dpi PNG the .docx uses, rendered by the same backend.
    L += ["\\newcommand{\\panelField}{\\includegraphics[width=0.98\\linewidth]{fig2.png}}",
          ""]
    L += tikz(panels[2], "panelFitted")
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {OUT}  ({len(L)} lines)")

    try:
        import PIL  # noqa: F401
    except ImportError:
        print("Pillow not installed — skipped the PNGs the .docx needs")
        return
    for canvas, path in zip(panels, PNG):
        print(f"wrote {path}  ({render(canvas, path)} px)")


if __name__ == "__main__":
    main()
