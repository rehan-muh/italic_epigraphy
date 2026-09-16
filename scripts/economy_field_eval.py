#!/usr/bin/env python3
"""
Downstream evaluation of the latent economic field E(s,t).

Loads db/economy_field_<version>_basis.npz (written by economy_field.py) and
evaluates the continuous posterior field at arbitrary (lon, lat, year)
points. `EconomyField()` defaults to version "v03" — the six-source field the
adoption analyses and the JAS paper were computed with; pass
`EconomyField(version="v04")` for the eleven-source Project MERCURY build
(Pleiades, CHRR hoards, OXREP presses, refreshed wrecks). Re-run any
downstream analysis explicitly if you switch; the two fields are on the
same sd scale but are not interchangeable point-for-point.

It — no raster lookup, no date midpointing. This implements handoff
section 18: for an inscription i at location s_i with chronological
probability p_i(t), use

    E_bar_i = integral E(s_i, t) p_i(t) dt

with posterior uncertainty propagated through the Laplace covariance of the
basis coefficients.

Typical use for the alphabet-adoption analysis:

    from economy_field_eval import EconomyField
    f = EconomyField()                            # or EconomyField(version="v04")
    m, sd = f.at(lon, lat, year)                  # pointwise posterior
    m, sd = f.integrated(lon, lat, years, probs)  # date-distribution average
    draws = f.sample(lon, lat, year, n=200)       # posterior draws

All years are astronomical (negative = BCE); the field is defined on
700 BCE - 1 BCE over the Italian grid box (lon 6.5-19, lat 35.5-47.5).
Values outside the data support revert toward the prior mean with large SD —
check E_sd (or the in_support flag in the CSV export) before interpreting.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_VERSION = "v03"


def basis_path(version=DEFAULT_VERSION):
    return ROOT / "db" / f"economy_field_{version}_basis.npz"


class EconomyField:
    def __init__(self, path=None, version=DEFAULT_VERSION):
        self.version = version
        z = np.load(path or basis_path(version))
        self.beta = z["beta_E"]
        self.L = z["cov_chol"]                     # cov = L @ L.T
        self.offset = float(z["E_mean_offset"])
        self.lat0 = math.radians(float(z["lat0_deg"]))
        self.boundary = float(z["boundary"])
        s = {k[5:]: z[k] for k in z.files if k.startswith("spec_")}
        self.ls, self.lt = float(s["ls"]), float(s["lt"])
        self.cx, self.cy, self.ct = float(s["cx"]), float(s["cy"]), float(s["ct"])
        self.hx, self.hy, self.ht = float(s["hx"]), float(s["hy"]), float(s["ht"])
        self.mx, self.my, self.mt = int(s["mx"]), int(s["my"]), int(s["mt"])

    # ---- basis -----------------------------------------------------------
    def _dim(self, x, half, centre, m):
        L = self.boundary * half
        j = np.arange(1, m + 1)
        freq = math.pi * j / (2 * L)
        return np.sqrt(1.0 / L) * np.sin(freq[None, :] * (x[:, None] - centre + L)), freq

    def _spec(self, omega, ls, d, nu=1.5):
        from math import gamma, pi
        c = (2 ** d * pi ** (d / 2) * gamma(nu + d / 2) * (2 * nu) ** nu
             / (gamma(nu) * ls ** (2 * nu)))
        return c * (2 * nu / ls ** 2 + omega ** 2) ** (-(nu + d / 2))

    def basis(self, lon, lat, year):
        lon = np.atleast_1d(np.asarray(lon, float))
        lat = np.atleast_1d(np.asarray(lat, float))
        year = np.atleast_1d(np.asarray(year, float))
        px = 111.32 * math.cos(self.lat0) * lon
        py = 110.57 * lat
        phix, fx = self._dim(px, self.hx, self.cx, self.mx)
        phiy, fy = self._dim(py, self.hy, self.cy, self.my)
        psit, ft = self._dim(year, self.ht, self.ct, self.mt)
        omega_s = np.sqrt(fx[:, None] ** 2 + fy[None, :] ** 2).ravel()
        w_s = np.sqrt(self._spec(omega_s, self.ls, 2))
        w_t = np.sqrt(self._spec(ft, self.lt, 1))
        phi_s = np.einsum("ni,nj->nij", phix, phiy).reshape(len(px), -1) * w_s[None, :]
        psit = psit * w_t[None, :]
        return np.einsum("ns,nt->nst", phi_s, psit).reshape(len(px), -1)

    # ---- evaluation ------------------------------------------------------
    def at(self, lon, lat, year):
        """Posterior mean and sd of E at points (vectorised)."""
        Phi = self.basis(lon, lat, year)
        mean = Phi @ self.beta - self.offset
        sd = np.sqrt(np.maximum(((Phi @ self.L) ** 2).sum(axis=1), 0.0))
        return mean, sd

    def integrated(self, lon, lat, years, probs):
        """E integrated over one point's date distribution p(t).

        years, probs: quadrature nodes and weights of p_i(t) (probs need not
        be normalised). Returns (mean, sd) of the p-weighted average of E,
        with spatiotemporal posterior covariance handled exactly.
        """
        probs = np.asarray(probs, float)
        probs = probs / probs.sum()
        n = len(probs)
        Phi = self.basis(np.full(n, lon), np.full(n, lat), np.asarray(years, float))
        v = probs @ Phi                       # averaging functional
        mean = float(v @ self.beta - self.offset)
        sd = float(np.sqrt(max(((v @ self.L) ** 2).sum(), 0.0)))
        return mean, sd

    def sample(self, lon, lat, year, n=200, seed=1):
        """Posterior draws of E at points, for full uncertainty propagation."""
        rng = np.random.default_rng(seed)
        Phi = self.basis(lon, lat, year)
        eps = rng.standard_normal((self.L.shape[1], n))
        return (Phi @ self.beta - self.offset)[:, None] + Phi @ (self.L @ eps)


if __name__ == "__main__":
    import sys
    f = EconomyField(version=sys.argv[1] if len(sys.argv) > 1 else DEFAULT_VERSION)
    print(f"field version {f.version}: ls={f.ls:.0f} km, lt={f.lt:.0f} yr")
    for name, lo, la in [("Rome", 12.48, 41.89), ("Tarquinia", 11.76, 42.25),
                         ("Taranto", 17.24, 40.47), ("inland Apennine", 13.8, 42.3)]:
        for yr in (-600, -300, -100):
            m, s = f.at(lo, la, yr)
            print(f"{name:16s} {yr:5d}: E = {m[0]:+.2f} +/- {s[0]:.2f}")
    m, s = f.integrated(12.48, 41.89, np.linspace(-350, -150, 21), np.ones(21))
    print(f"Rome, uniform date 350-150 BCE: E_bar = {m:+.2f} +/- {s:.2f}")
