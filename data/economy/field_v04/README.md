# Latent spatiotemporal economic field, Italy 700 BCE – 1 BCE (v0.4)

Built 2026-09-16 by `scripts/economy_field.py` (default = v0.4; `--v03`
reproduces the previous build) from the v0.2/v0.3 evidence package plus the
Project MERCURY sources materialised by `scripts/economy_sources_v04.py`.
The model is the one described in `../field_v03/README.md` (latent-factor
HSGP log-Gaussian Cox process, penalised ML + Laplace, spatial-block CV,
dispersion downweighting, leave-one-source-out); this card records what
changed and what came out. v0.3 artifacts are untouched.

## What was added (from https://projectmercury.eu/datasets/)

Every dataset on the MERCURY inventory was checked; the full ledger is
`../data_sources_v04.csv`. Five sources were added and one refreshed:

| source | domain | records used | temporal treatment |
|---|---|---|---|
| `pleiades_settlement` | settlement | 2,315 places (754 with mass before 400 BCE) | period attestation → ramped presence (below) |
| `pleiades_production` | production | 899 (villa, estate, centuriation, production, quarry, mine, salt-pan, fishpond) | same |
| `pleiades_transport` | connectivity | 781 (bridge, road, station, canal, port, harbour, anchorage, lighthouse, shipshed, causeway, milestone) | same |
| `chrr_hoard` | **monetization** (new domain) | 189 dated hoards (closing dates 195 BCE – 14 CE) | deposition uniform on [closing−10, closing+15]; likelihood window from 200 BCE |
| `oil_wine_press` | production | 12 Italian OXREP press sites with structured phases | presence over dated phase × n presses |
| `maritime_shipwreck` (refreshed) | exchange | 118 dated Italian wrecks (was 98) | uniform over wreckage-after/before |

Pleiades period codes (archaic 750–550, classical 550–330,
hellenistic-republican 330–30, roman 30 BCE–300 CE, …) are treated exactly
like the heaped Hanson catalogue dates were in v0.2: a place's start is
taken as uniformly distributed within its first attested period and its end
within its last, so presence ramps linearly instead of stepping to 1 at a
period boundary (a place first attested "hellenistic-republican" is not
assumed to exist in 330 BCE). A single-period attestation gives
P(active at t) = 2(t−a)(b−t)/(b−a)². Consequence to keep in mind: the
700–650 BCE bins carry only the tail of the archaic ramp.

Not usable from the MERCURY list: AWMC shapefiles (URLs 404; the same
Barrington features enter via Pleiades *with* period attestation), OXREP
stone quarries (coordinates only, free-text dating), OXREP water technology
(Egyptian papyri), Orbis (undated imperial network — same objection as
Itiner-e), CRRO/OCRE/CHRE (type catalogues or post-30 BCE), web-only or
out-of-area databases. Palmisano–Bevan–Shennan and HYDE are still missing.

## Other changes vs v0.3

- **Spatial domain = modern Italy.** v0.3 used the AWMC 60 BCE province
  rings, which omit the Alpine arc, Friuli/Aquileia and several islands, and
  then extended the support cell-by-cell around any observed event — so stray
  points inside the grid box (Carthage, Istria, the Riviera, Corsica) entered
  as isolated cells with no zero-count surroundings. v0.4 uses the ISTAT
  province polygons (+ an approximate Valle d'Aosta outline, which the local
  extract lacks); the Italian shoreline is the AWMC shoreline within 8 km of
  them; land sources are supported on land plus a 20-km coastal band; events
  outside a source's support are dropped and reported (Pleiades: 2,516 of
  16,161 settlement place-bins, almost all the Carthage hinterland; the
  v0.3 sources lose < 0.2 %).
- **CV criterion capped against over-extrapolation.** Each source's held-out
  linear predictor is capped at the maximum fitted on its training points.
  Without this, one fold (Sardinia + Sicily predicted from the mainland) sent
  the tiny, steep-loading sources (presses, Pleiades rural sites) to exp(8)
  per cell and the criterion was dominated by < 1 % of the evidence.
- **Length-scale grid** extended to 50-yr temporal scale (the 300-km spatial
  candidates were dropped after 200 km lost to 140 km). Per-fold deviances are
  stored in the JSON.
- **Resumable run**: CV points, the full fit and each LOSO refit are
  checkpointed in `../raw_v04/checkpoints/`, keyed by a fingerprint of the
  evidence tables; `--fresh` ignores them, `--budget SECONDS` stops cleanly at
  the next checkpoint. Wall time for the whole build is ~45 min; the
  `runtime_s` in the JSON only measures the final resumed call.

## Results (`../../../db/economy_field_v04.json`)

Selected length scales: **140 km, 50 yr** (v0.3: 200 km, 80 yr). The
spatial optimum is interior (90 km: 99,893; 140 km: 87,822; 200 km: 91,084
held-out deviance at 50 yr). 50 yr is the finest temporal scale the capped
basis (12×12×8) can represent, so the temporal scale should be read as
"≤ 50 yr" rather than a converged estimate.

Loadings ρ (steepness of a source's response to E) and leave-one-source-out
influence (mean |ΔE| in sd units / correlation with the full field):

| source | ρ | LOSO |ΔE| | LOSO corr |
|---|---|---|---|
| oil_wine_press | 4.90 | 0.011 | 1.000 |
| pleiades_production | 3.16 | 0.098 | 0.991 |
| pleiades_transport | 3.05 | 0.150 | 0.983 |
| amphora_trade | 2.93 | 0.035 | 0.999 |
| urban_city | 2.73 | 0.181 | 0.972 |
| chrr_hoard | 2.35 | 0.013 | 1.000 |
| pleiades_settlement | 1.90 | **0.291** | **0.912** |
| maritime_shipwreck | 1.70 | 0.014 | 1.000 |
| ancient_port | 1.46 | 0.164 | 0.978 |
| mining_production | 0.92 | 0.026 | 0.999 |
| archaeological_occupation | 0.59 | 0.048 | 0.998 |

Read ρ and influence separately: a tiny, spatially concentrated source
(12 press sites) gets a steep loading — and therefore a large
`normalized_weights_rho2` share — while moving the field by nothing. The
field is now shaped mainly by Pleiades settlements, Hanson cities, ports and
Pleiades transport features, and no single source dominates (v0.3 lost half
its correlation without Hanson cities; the worst case here is 0.91).
Dispersion downweighting applies to ports (1/φ = 0.69), Pleiades transport
(0.84) and cities (0.94). ¹⁴C reliability rises from 0.01 to 0.58 — the
radiocarbon pattern shares signal with the denser settlement layer.

## v0.3 → v0.4 comparison (land cells common to both)

Correlation of posterior means 0.91 overall (0.73–0.89 per 25-yr bin);
posterior SD roughly halved (mean 0.34 → 0.18 at 700 BCE, 0.17 → 0.10 at
25 BCE). The intended pre-400 BCE gain is where the fields differ most:
Rome, Veii and Tarquinia at 600 BCE move from ≈ 0 sd (v0.3 had no dated
evidence there before the Hanson 400 BCE heap) to +0.9…+1.2 sd; the Po delta
(Adria) from −1.3 to +0.4. Eastern Sicily is relatively lower in v0.4
(Syracuse +0.5 vs +1.0) because the standardisation is now carried by the
much denser mainland settlement layer — compare contrasts, not levels,
across versions.

## Files

- `economic_field_25yr_v04.csv` — posterior field on the 0.25° × 25-yr grid
  (69,972 rows). Columns as v0.3 plus `monetization_field`. **Filter to
  `is_land` (532 cells, modern Italy) before analysis**; `in_support` adds the
  coastal band and wreck sea cells.
- `../../../db/economy_field_v04.json` — loadings, weights, reliability,
  dispersion, CV table with per-fold deviances, LOSO, source windows.
- `../../../db/economy_field_v04_basis.npz` — basis + posterior for continuous
  evaluation: `EconomyField(version="v04")` in `scripts/economy_field_eval.py`.
  The default remains `"v03"` so that the adoption analyses and the JAS
  paper numbers do not change silently; switching downstream work to v0.4 is
  a deliberate re-run.
- Interactive animation: the site page `/economy/` (`pages/economy.html` +
  `_includes/economy-app.html`, `assets/js/economy.js`, `assets/css/economy.scss`,
  fetching `assets/data/economy_field_v04.json`). `scripts/economy_field_animation.py`
  writes that JSON and, from the same include/CSS/JS, the self-contained
  `economic_field_v04_animation.html` here (git-ignored; published copy:
  https://claude.ai/artifact/MKiXVNcErSLUSeKpLpzDpS).
- `figures/` — posterior mean maps (700–25 BCE, shared colour scale), SD
  maps, domain fields at 500 and 100 BCE, national trajectory, loadings /
  reliability, LOSO influence.

## Caveats carried forward

- Pleiades density is Barrington-Atlas editorial density; like Hanson it is
  a knowledge gazetteer, not a survey. Low E in poorly mapped regions is
  partly a mapping effect — use `E_sd`.
- Coin hoards also record crisis (the 90–30 BCE peak); the loading is
  inferred, and the source barely moves E, but the `monetization_field`
  should not be read as a pure prosperity index.
- 27 of the 118 wrecks use v0.1 geocoded coordinates because OXREP gives
  "Coordinates: None given" (flagged in `shipwrecks_oxrep_italy_v04.csv`).
- Pre-750 BCE evidence is outside the Pleiades period scheme; the first two
  bins remain the weakest.
