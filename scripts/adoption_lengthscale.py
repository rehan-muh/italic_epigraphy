#!/usr/bin/env python3
"""
Bayesian inference for the space-time kernel hyperparameters of the
multinomial (logit) adoption model.

The headline model's Matern-3/2 HSGP field previously used FIXED length
scales (110 km x 8 quarters, inherited from the direction analysis's CV)
with only the amplitude tuned by CV.  Here the three kernel
hyperparameters get priors with mass concentrated at shorter scales

    ls   spatial length scale        log ls  ~ N(log 70 km, 0.45^2)
    lt   temporal length scale       log lt  ~ N(log 100 yr, 0.50^2)
    tau  field amplitude (ridge=1/tau^2)   log tau ~ N(log 3, 0.60^2)

and are inferred on a log-spaced grid by Laplace-approximated marginal
likelihood.  The penalty IS the Gaussian-process prior (HSGP columns are
scaled by the root spectral density, so ridge 1/tau^2 = amplitude-tau GP),
hence each node's penalised fit is a MAP estimate and

    log ML(theta) ~= loglik(Bhat) - 0.5 * quad(Bhat)
                     - K * J * log(tau) - 0.5 * log|H_reduced|

with H the exact (p*K x p*K) posterior Hessian assembled in K x K blocks
X' diag(p_k d_kl - p_k p_l) X + prior, and "reduced" meaning the two
softmax-invariance null directions (intercept and E rows constant across
categories) are projected out.  Node weights

    w(theta)  propto  exp(log ML) * prior(log theta) * cell log-volume

give a posterior over (ls, lt, tau); beta_E is reported per node (the
length-scale sensitivity analysis) and model-averaged over the grid (BMA,
with between-node variance as the hyperparameter-uncertainty term).

Short length scales need more basis functions than the fitter affords, so
every node keeps the J_MAX tensor-product terms with the largest prior
variance (spectral truncation); kept_prior_var is recorded per node.

    py -3.12 scripts/adoption_lengthscale.py [--smoke]
                                             [--script-only|--language-only]

Outputs: db/adoption_lengthscale.json, db/adoption_lengthscale_lang.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adoption_probit as A                                        # noqa: E402
from adoption_multinomial import (build_response,                  # noqa: E402
                                  build_response_language,
                                  fit_multinomial, softmax)

OUT = {"script": A.ROOT / "db" / "adoption_lengthscale.json",
       "language": A.ROOT / "db" / "adoption_lengthscale_lang.json"}
CKPT = A.ROOT / "db" / "adoption_lengthscale_ckpt.jsonl"

GRID_LS = (40.0, 56.0, 79.0, 110.0, 154.0)      # km, log-spaced, incl. old 110
GRID_LT = (2.0, 4.0, 8.0, 16.0, 32.0)           # quarters: 50--800 yr; 32 q
                                                # exceeds the 700-yr window,
                                                # i.e. a quasi-static field
GRID_TAU = (2.0, 4.0, 8.0, 16.0)                # tau=4 <-> old CV ridge 0.0625
J_MAX = 1000
M_CAPS = (28, 30, 16)
SEED = 7
FIT_TOL = 0.05          # penalised-deviance sweep tolerance for grid nodes:
                        # score differences across nodes are O(10^1..10^3)


def norm_lpdf(u, mu, sig):
    return -0.5 * ((u - mu) / sig) ** 2 - math.log(sig) \
        - 0.5 * math.log(2 * math.pi)


def log_prior_u(ls, lt_q, tau):
    """Prior density of u = log(theta) (normal in log space)."""
    return (norm_lpdf(math.log(ls), math.log(70.0), 0.45)
            + norm_lpdf(math.log(25.0 * lt_q), math.log(100.0), 0.50)
            + norm_lpdf(math.log(tau), math.log(3.0), 0.60))


def log_widths(vals):
    """Cell widths in log space (midpoint rule, edge cells extended)."""
    lv = np.log(np.asarray(vals, float))
    if len(lv) == 1:
        return np.array([1.0])
    edges = np.r_[lv[0] - (lv[1] - lv[0]) / 2,
                  (lv[1:] + lv[:-1]) / 2,
                  lv[-1] + (lv[-1] - lv[-2]) / 2]
    return np.diff(edges)


def laplace_logdet(X, P, pen, free_rows):
    """log|H| of the softmax posterior Hessian, null directions removed.

    H_kl = X' diag(p_k d_kl - p_k p_l) X + d_kl diag(pen), assembled
    densely; the likelihood is exactly invariant to adding a constant
    across categories in an unpenalised row, so those directions are
    replaced by unit-scale ridges whose contribution is subtracted.
    """
    n, p = X.shape
    K = P.shape[1]
    H = np.zeros((p * K, p * K))
    for k in range(K):
        Ak = X * P[:, k][:, None]
        Dk = X.T @ Ak
        for lidx in range(k, K):
            Ckl = Ak.T @ (X * P[:, lidx][:, None])
            if lidx == k:
                blk = Dk - Ckl + np.diag(pen)
            else:
                blk = -Ckl
            H[k * p:(k + 1) * p, lidx * p:(lidx + 1) * p] = blk
            if lidx != k:
                H[lidx * p:(lidx + 1) * p, k * p:(k + 1) * p] = blk.T
    c = float(np.median(np.diag(H)))
    for j in free_rows:
        v = np.zeros(p * K)
        v[j::p] = 1.0 / math.sqrt(K)
        H += c * np.outer(v, v)
    try:
        L = np.linalg.cholesky(H)
        logdet = 2.0 * float(np.sum(np.log(np.diag(L))))
    except np.linalg.LinAlgError:
        sign, logdet = np.linalg.slogdet(H)
        if sign <= 0:
            raise RuntimeError("Hessian not PD at MAP")
    return logdet - len(free_rows) * math.log(c)


def node_score(X, Y, B, pen, free_rows, ridge, j_field):
    K = Y.shape[1]
    P = softmax(X @ B)
    ll = float(np.sum(Y * np.log(np.clip(P, 1e-12, None))))
    quad = 0.5 * float(np.sum(pen[None, :].T * B * B))
    logdet = laplace_logdet(X, P, pen, free_rows)
    # 0.5*sum(log pen) over penalised coords: trend rows have pen=1 (-> 0),
    # field rows pen=1/tau^2 -> -K*J*log(tau); constants across nodes drop
    log_ml = ll - quad + 0.5 * K * j_field * math.log(ridge) - 0.5 * logdet
    bits = -ll / math.log(2) / len(Y)
    return log_ml, round(bits, 4)


def load_ckpt(tag):
    """Checkpointed nodes from earlier runs: {(ls, lt): {tau: node}}.

    Later lines override earlier ones, so re-written cells dedupe."""
    done = {}
    if CKPT.exists():
        for line in CKPT.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("tag") != tag:
                continue
            cell = done.setdefault((rec["ls"], rec["lt"]), {})
            for nd in rec["nodes"]:
                cell[nd["tau"]] = nd
    return done


def run_response(tag, df, Y, names, Ebar, args, t0):
    n, K = Y.shape
    n_ord = A.ORD_HI - A.ORD_LO + 1
    i_etr = names.index("etruscan")
    grid_ls = (56.0, 110.0) if args.smoke else GRID_LS
    grid_lt = (4.0,) if args.smoke else GRID_LT
    # the language model's amplitude posterior ran to the tau=16 edge,
    # so its ladder is extended one octave further
    grid_tau = ((2.0, 8.0) if args.smoke
                else GRID_TAU + ((32.0,) if tag == "language" else ()))
    j_max = 300 if args.smoke else J_MAX
    m_caps = (16, 18, 10) if args.smoke else M_CAPS

    done = {} if args.smoke else load_ckpt(tag)
    nodes = []
    for ls in grid_ls:
        for lt in grid_lt:
            saved = done.get((ls, lt), {})
            need = [t for t in grid_tau if t not in saved]
            if not need:
                nodes.extend(saved[t] for t in grid_tau)
                print(f"[{tag}] ls={ls:.0f}km lt={lt * 25:.0f}yr: "
                      f"resumed from checkpoint", flush=True)
                continue
            bas = A.Bases(df, n_ord, ls_km=ls, lt_q=lt,
                          m_caps=m_caps, j_max=j_max)
            Xtr_ = bas.trend(df.t0.values, df.t1.values)
            Xst = bas.st(df.px.values, df.py.values,
                         df.t0.values, df.t1.values)
            X = np.hstack([np.ones((n, 1)), Xtr_, Ebar[:, None], Xst])
            iE = 1 + Xtr_.shape[1]
            free_rows = np.array([0, iE])
            jf = Xst.shape[1]
            print(f"[{tag}] ls={ls:.0f}km lt={lt * 25:.0f}yr: "
                  f"{jf} field cols (pool {bas.n_pool}, kept "
                  f"{bas.kept_prior_var:.3f}) [{time.time() - t0:.0f}s]",
                  flush=True)
            cell_nodes = []
            B = None
            for tau in grid_tau:
                if tau in saved:
                    cell_nodes.append(saved[tau])
                    continue
                ridge = 1.0 / tau ** 2
                pen = np.zeros(X.shape[1])
                pen[1:1 + Xtr_.shape[1]] = 1.0
                pen[iE + 1:] = ridge
                B = fit_multinomial(X, Y, pen, free_rows, B0=B,
                                    max_sweeps=40, tol=FIT_TOL)
                log_ml, bits = node_score(X, Y, B, pen, free_rows,
                                          ridge, jf)
                beta = B[iE]
                cell_nodes.append({
                    "ls_km": ls, "lt_q": lt, "lt_yr": lt * 25.0,
                    "tau": tau, "ridge": ridge, "n_field_cols": jf,
                    "pool": bas.n_pool,
                    "kept_prior_var": round(bas.kept_prior_var, 4),
                    "log_ml": round(log_ml, 2),
                    "log_prior_u": round(log_prior_u(ls, lt, tau), 4),
                    "insample_bits": bits,
                    "beta_E_centred": {nm: round(float(beta[i]), 4)
                                      for i, nm in enumerate(names)},
                })
                print(f"  tau={tau:.0f}: log_ml {log_ml:.1f}, "
                      f"{bits} bits, beta_latin "
                      f"{beta[names.index('latin')]:+.3f} "
                      f"[{time.time() - t0:.0f}s]", flush=True)
            nodes.extend(cell_nodes)
            if not args.smoke:
                with open(CKPT, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(
                        {"tag": tag, "ls": ls, "lt": lt,
                         "grid_tau": list(grid_tau),
                         "nodes": cell_nodes}) + "\n")

    # ---- posterior over the grid ------------------------------------
    wls = dict(zip(grid_ls, log_widths(grid_ls)))
    wlt = dict(zip(grid_lt, log_widths(grid_lt)))
    wtau = dict(zip(grid_tau, log_widths(grid_tau)))
    lw = np.array([nd["log_ml"] + nd["log_prior_u"]
                   + math.log(wls[nd["ls_km"]]) + math.log(wlt[nd["lt_q"]])
                   + math.log(wtau[nd["tau"]]) for nd in nodes])
    w = np.exp(lw - lw.max())
    w /= w.sum()
    for nd, wi in zip(nodes, w):
        nd["posterior_weight"] = round(float(wi), 5)

    def marg(key):
        out = {}
        for nd, wi in zip(nodes, w):
            out[nd[key]] = out.get(nd[key], 0.0) + float(wi)
        return {str(k): round(v, 4) for k, v in sorted(out.items())}

    i_map = int(np.argmax(w))
    betas = np.array([[nd["beta_E_centred"][nm] for nm in names]
                      for nd in nodes])
    bma = betas.T @ w
    var_b = ((betas - bma[None, :]) ** 2).T @ w
    post_mean_ls = math.exp(float(np.log([nd["ls_km"] for nd in nodes]) @ w))
    post_mean_lt = math.exp(float(np.log([nd["lt_yr"] for nd in nodes]) @ w))
    post_mean_tau = math.exp(float(np.log([nd["tau"] for nd in nodes]) @ w))

    res = {
        "response": tag, "n": n, "categories": names,
        "grid_ls_km": list(grid_ls), "grid_lt_yr": [q * 25 for q in grid_lt],
        "grid_tau": list(grid_tau), "j_max": j_max,
        "prior": {"ls": "logN(median 70 km, sd 0.45)",
                  "lt": "logN(median 100 yr, sd 0.50)",
                  "tau": "logN(median 3, sd 0.60)"},
        "nodes": nodes,
        "posterior": {
            "map_node": {k: nodes[i_map][k] for k in
                         ("ls_km", "lt_yr", "tau", "ridge",
                          "posterior_weight", "log_ml")},
            "marginal_ls_km": marg("ls_km"),
            "marginal_lt_yr": marg("lt_yr"),
            "marginal_tau": marg("tau"),
            "post_geomean_ls_km": round(post_mean_ls, 1),
            "post_geomean_lt_yr": round(post_mean_lt, 1),
            "post_geomean_tau": round(post_mean_tau, 2),
        },
        "bma_beta_E": {nm: {"mean": round(float(bma[i]), 4),
                            "sd_between_nodes": round(float(
                                math.sqrt(var_b[i])), 4),
                            "contrast_vs_etruscan": round(float(
                                bma[i] - bma[i_etr]), 4)}
                       for i, nm in enumerate(names)},
        "note": ("beta_E_centred per node: sensitivity of the economy "
                 "coefficient to the kernel hyperparameters. bma_beta_E: "
                 "posterior-weight-averaged over the grid; add "
                 "sd_between_nodes in quadrature to the MAP refit's "
                 "clustered+Rubin SE for the full uncertainty."),
    }
    OUT[tag].write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(f"[{tag}] MAP ls={nodes[i_map]['ls_km']:.0f} km, "
          f"lt={nodes[i_map]['lt_yr']:.0f} yr, tau={nodes[i_map]['tau']:.0f}"
          f"; post geomeans ls {post_mean_ls:.0f} km, lt {post_mean_lt:.0f} "
          f"yr, tau {post_mean_tau:.1f}; wrote {OUT[tag]} "
          f"[{time.time() - t0:.0f}s]", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--script-only", action="store_true")
    ap.add_argument("--language-only", action="store_true")
    args = ap.parse_args()
    t0 = time.time()
    rng = np.random.default_rng(SEED)

    df, counts = A.load()
    if args.smoke:
        df = df.sample(3500, random_state=SEED).reset_index(drop=True)
    Ebar, _, _, _ = A.economy_covariate(df, 1, rng)
    print(f"n = {len(df)} [{time.time() - t0:.0f}s]", flush=True)

    if not args.language_only:
        Y, names, _ = build_response(df)
        run_response("script", df, Y, names, Ebar, args, t0)
    if not args.script_only:
        Y, names, _ = build_response_language(df)
        run_response("language", df, Y, names, Ebar, args, t0)
    if not (args.smoke or args.script_only or args.language_only):
        CKPT.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
