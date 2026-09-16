# Latent spatiotemporal economic field, Italy 700 BCE – 1 BCE (v0.3)

Built by `scripts/economy_field.py` from the v0.2 evidence package in
`data/economy/`. This replaces the exploratory v0.2 weighted raster
(`economic_activity_grid_25yr_v02.csv`, fixed 0.45/0.35/0.20 weights and a
fixed 50-km kernel) as the product to use in downstream analyses.

## Model

Latent-factor spatiotemporal model per `FRESH_SESSION_HANDOFF.md`:

    log lambda_k(s,t) = alpha_k + rho_k * E(s,t) + delta_d(k)(s,t)

- `E(s,t)`: shared latent economic field, continuous in projected km and
  calendar years, reduced-rank Matérn-3/2 HSGP (Solin & Särkkä basis, the
  same construction as `scripts/direction_hsgp.py`), standardised to
  Var(E) = 1 for identification.
- `rho_k = exp(theta_k) > 0`: per-source loadings, inferred, with the
  handoff's prior `theta ~ N(log 0.5, 0.75^2)`. No hand-picked weights.
- `delta_d`: domain-specific residual GPs (settlement, exchange,
  connectivity, production) at twice the length scales, prior sd 0.5 —
  what a source family shows *beyond* the shared economic signal.
- Observation model: weighted-Poisson grid quadrature of a log-Gaussian Cox
  process per source. The existing aoristic / calibrated 25-year temporal
  probability allocations are the chronological-uncertainty integration
  (no date midpointing). Occupation site×bin weights are clipped at 1 so
  multiple ¹⁴C dates from one site never count as independent events.
- Spatial support per source: land sources on the Italian land mask (AWMC
  60 BCE province rings that contain Hanson cities); ports within 30 km of
  the Italian shoreline; wrecks on sea/coastal cells within 80 km of the
  *Italian* shoreline only (foreign coasts are excluded so that the absence
  of records in Italy-focused catalogues cannot masquerade as low activity);
  amphora likelihood restricted to its chronology window (-400 onward).
- Spatial and temporal ranges selected by 5-block spatial cross-validation
  (islands / south / centre / north-centre / Po), not fixed.
- Quasi-Poisson dispersion estimated per source; overdispersed sources are
  likelihood-downweighted by 1/phi so row-rich sources cannot mechanically
  dominate (handoff section 13).
- Uncertainty: Laplace posterior covariance of the field coefficients,
  conditional on loadings. Leave-one-source-out refits quantify influence.

Fitted loadings, normalized weights, reliabilities, dispersion, CV table and
LOSO summaries: `db/economy_field_v03.json`.

## Deviations from the full handoff spec

Chosen to match this repo's hand-rolled penalised-likelihood stack
(no PyMC/Stan/INLA in the environment):

- empirical-Bayes penalised ML + Laplace approximation, not full MCMC —
  posterior SDs are conditional on the fitted loadings and length scales;
- amphora evidence enters as log1p site/type pseudo-counts (as in v0.2) with
  dispersion downweighting, not a NegBin site-random-effect model;
- Itiner-e roads are excluded: the local layer carries no segment
  chronology and the handoff forbids back-projecting undated imperial roads;
- Palmisano, CHRR and HYDE remain unintegrated (files never materialised);
  the settlement field before ~400 BCE therefore still leans on Hanson
  cities and 185 ¹⁴C sites, and there is no monetization domain yet.

## Files

- `economic_field_25yr_v03.csv` — posterior field on the v0.2-compatible
  0.25° × 25-year grid (69,972 rows). Columns: `bin_start_year`,
  `bin_end_year`, `longitude`, `latitude`, `E_mean`, `E_sd` (posterior mean
  and SD of the shared field, sd units), `settlement_field`,
  `exchange_field`, `connectivity_field`, `production_field` (domain fields
  `rho_bar_d·E + delta_d`), `is_land`, `dist_coast_km`, `in_support`.
  **Filter to `is_land` (or `in_support`) before analysis**; open-sea and
  edge cells are prior-dominated extrapolation.
- `../../../db/economy_field_v03.json` — loadings, weights, reliability,
  dispersion, CV, LOSO, provenance of exclusions.
- `../../../db/economy_field_v03_basis.npz` — basis spec + posterior mean and
  covariance Cholesky of the field coefficients: evaluate the *continuous*
  field at any (lon, lat, year) with uncertainty, no raster lookup.
- `figures/` — posterior mean maps (700–25 BCE, shared colour scale),
  posterior SD maps, domain-field maps, national trajectory with 95%
  interval, loading/reliability plots, LOSO influence.

## Downstream use (e.g. alphabet-adoption analysis)

Use `scripts/economy_field_eval.py`, which implements handoff section 18:

    from economy_field_eval import EconomyField
    f = EconomyField()
    m, sd = f.at(lon, lat, year)                  # pointwise
    m, sd = f.integrated(lon, lat, years, probs)  # over a date distribution
    draws = f.sample(lon, lat, year, n=200)       # posterior draws

For each inscription, integrate over its dating distribution rather than
midpointing, and carry `sd` (or draws) into the downstream model. For
hypotheses about contact/trade specifically, prefer `exchange_field` /
`connectivity_field` over the composite `E`.

## Caveats to carry into any interpretation

- Low posterior mean in poorly surveyed regions is partly a survey effect;
  `E_sd` grows where evidence is thin, use it.
- Pre-400 BCE coverage is weakest (no Palmisano yet); treat early-period
  contrasts cautiously.
- The ¹⁴C source's low loading (see JSON) means radiocarbon sampling
  intensity shares little spatial signal with the trade-weighted composite;
  that is a statement about the sources, not about the places.
