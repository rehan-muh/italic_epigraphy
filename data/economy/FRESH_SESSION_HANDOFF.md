# FRESH-SESSION HANDOFF: Ancient Italy Economic Activity, 700 BCE–0 CE

## 0. Purpose of this handoff

This file is intended to be given, together with the accompanying CSVs and any manually downloaded missing datasets, to a fresh ChatGPT session.

The goal is **not** to preserve the current weighted-sum surface as the final model. The goal is to preserve every extracted observation and every transformation already performed so that the next session can build a **continuous spatiotemporal latent economic-activity field using a Gaussian process (GP)**.

The current v0.2 raster is exploratory and should be treated as a diagnostic/initialization product only.

The final model should:

1. use the original point/site/network/raster observations wherever possible;
2. integrate chronological uncertainty rather than midpoint dates;
3. infer a continuous latent field \(E(s,t)\) over Italy from 700 BCE to the 1 BCE/CE boundary;
4. use source-specific observation models;
5. estimate proxy loadings/reliabilities rather than assign arbitrary fixed weights;
6. represent preservation, survey intensity, chronology quality, and source-specific noise;
7. propagate uncertainty into the final economic field;
8. produce static maps, CSV predictions, posterior uncertainty maps, diagnostics, and leave-one-source-out sensitivity results.

---

# 1. What is already extracted and available

## 1.1 Hanson / Oxford Roman Economy Project Cities

**Status:** integrated/extracted.

**Records used:** 372 urban sites in modern Italy.

**Role:** urbanization / settlement concentration.

**Available information in the package:**
- coordinates;
- city/site name and source ID;
- catalogue start date;
- time-varying activation probability after correcting for strongly heaped catalogue dates.

**Important methodological decision already made:**
Do **not** back-project Hanson's later urban-area estimates into the Archaic or Republican period unless a city-specific dated size estimate is independently available. The current model uses dated urban presence, not anachronistic area.

**Date-heaping correction:**
Rounded catalogue dates (especially very frequent values such as 400 BCE) are not treated as exact historical events. They are converted to uncertain start intervals. The aoristic half-width used in v0.2 is:

- >=100 sites sharing the same start year: +/-75 years
- >=20: +/-60 years
- >=5: +/-40 years
- otherwise a rounded 50-year date: +/-30 years
- rounded 10-year date: +/-20 years
- apparently specific date: +/-10 years

These are pragmatic uncertainty intervals, not sacred values. In the final GP they should be sensitivity-tested or replaced with source-specific dating uncertainty if better metadata are available.

**Relevant file:**
`urban_city_time_weights_25yr.csv`

Rows: 6,316.

Columns:
- `bin_start_year`
- `bin_end_year`
- `source_id`
- `name`
- `longitude`
- `latitude`
- `catalogue_start_year`
- `start_uncertainty_halfwidth_years`
- `active_probability`

For the GP, preferably reconstruct the continuous start-date distribution from the catalogue date + uncertainty width rather than using the 25-year bins as the final temporal representation.

---

## 1.2 AIDA Italian radiocarbon archive

**Status:** integrated/extracted.

**Usable sites in the 700 BCE–0 interval:** 185.

**Role:** archaeological occupation / demographic-settlement activity.

**Treatment already used:**
- radiocarbon dates were calibrated using IntCal20 in the v0.1 construction;
- calibrated probability was allocated through time;
- multiple dates from the same site were not intended to count as independent economic events.

**Critical caveat:**
Radiocarbon sampling intensity is not economic activity. It is affected by excavation, research history, preservation, and chronology practice. In the final latent model this source needs:
- site-level clustering/random effects;
- source-specific dispersion/bias;
- ideally a survey/excavation intensity term if one can be constructed.

**Relevant file:**
`base_nonurban_source_time_weights_25yr.csv`

Filter `source_type == archaeological_occupation`.

There are 3,248 site × time-weight rows in the inherited temporal-weight file.

---

## 1.3 Oxford Roman Economy Project Shipwrecks

**Status:** integrated/extracted.

**Usable Italy-related wrecks overlapping 700 BCE–0:** 98.

**Role:** maritime trade / exchange intensity.

**Treatment already used:**
Broad date ranges are distributed probabilistically over time rather than midpointed.

**Critical caveats:**
- shipwreck discovery is spatially heterogeneous;
- preservation and diving intensity vary;
- wrecks measure maritime traffic, not total economic activity;
- a wreck offshore should inform a coastal/maritime exchange field, not inland activity equally.

In the final model, this source should ideally be a **coast/maritime-constrained log-Gaussian Cox process** or another point-process likelihood.

**Relevant file:**
`base_nonurban_source_time_weights_25yr.csv`

Filter `source_type == maritime_shipwreck`.

There are 377 wreck × time-weight rows.

---

## 1.4 Oxford Roman Economy Project Mines

**Status:** extracted but extremely sparse.

**Usable dated mines overlapping the target period:** 4.

**Role:** production/extractive activity.

**Treatment already used:** dated overlap.

**Important decision:**
Mining was **not** allowed to carry equal weight in the default v0.2 index because four observations cannot define production intensity across Italy.

In the final model mines should be retained as a separate production proxy with very large source uncertainty / weakly identified loading.

**Relevant file:**
`base_nonurban_source_time_weights_25yr.csv`

Filter `source_type == mining_production`.

There are 67 mine × time-weight rows.

---

## 1.5 Ancient Ports and Harbours / de Graauw catalogue

**Status:** integrated/extracted from the current downloadable catalogue.

**Italian catalogue records exported:** 720.

**Pre-1 CE records with a usable foundation date:** 531.

**Role:** coastal connectivity / infrastructure / exchange capacity.

**Available variables:**
- port ID;
- ancient name;
- modern name;
- region/country field;
- longitude/latitude;
- foundation year;
- cultural flags;
- count of infrastructure attributes;
- identifiers/links for Pleiades, DARE, Trismegistos, ToposText where available.

**Current temporal treatment:**
Port activity is represented as a probability of being established by time \(t\), with the same general date-heaping correction principle used for cities.

Very early catalogue dates (well before the modeled period) are treated as already established by 700 BCE.

**Critical caveat:**
A port is not a direct measure of realized trade volume. It is better interpreted as economic/connectivity infrastructure. Port founding is also endogenous to economic activity. The final model should therefore test both:
- ports as a measurement proxy for a connectivity domain; and
- ports as a predictor/covariate of economic activity.

**Files:**
- `ports_italy.csv` — 720 rows.
- `ports_time_weights_25yr.csv` — 25-year activation probabilities.

---

## 1.6 CEIPAC-derived amphora stamp data

**Status:** integrated/extracted.

**Italy/Sicily/Sardinia stamps exported:** 7,175.

**Stamps belonging to forms with a chronology used in the provisional temporal model:** 5,785.

**Unique trade-site points in the harmonized source file:** 128.

**Role:** material exchange / distribution / consumption.

**Files:**
- `amphora_stamps_italy.csv` — 7,175 rows.
- `amphora_site_type_summary.csv` — 230 site × type rows.
- `amphora_time_weights_25yr.csv` — 894 site/type × time rows.
- `amphora_type_chronology.csv` — the chronology assumptions used.

**Current treatment:**
- records aggregated by site × amphora type;
- abundance transformed as `log1p(stamp_count)` to prevent huge stamped assemblages from dominating;
- evidence is distributed across a broad chronology window for the amphora type.

**Current conservative chronology windows used:**
These were adopted only to get a defensible provisional temporal allocation and must be independently checked before publication:
- Greco-Italic: broad Republican support window;
- Dressel 1 / 1A / 1B / 1C: late Republican support;
- Brindisian amphorae;
- Lamboglia 2;
- Dressel 6A / 6B;
- Pascual 1;
- Dressel 2–4 and Catalan Dressel 2–4 only where they overlap the target interval.

See the exact numeric ranges in `amphora_type_chronology.csv`.

**Critical caveats:**
- stamped amphorae are a selected subset of amphora circulation;
- assemblage size depends strongly on excavation and publication;
- many records are not individually dated;
- individual stamps from the same site/context are not independent.

The final GP should use site/context aggregation and a Negative Binomial or marked-point-process observation model, with site-level sampling effects.

---

# 2. Harmonized observations already in one file

`source_points_all.csv`

Total harmonized source points: **1,318**.

Breakdown:
- 531 ancient ports
- 372 urban cities
- 185 archaeological-occupation sites
- 128 amphora trade sites
- 98 maritime shipwrecks
- 4 mining-production sites

Columns:
- `source_type`
- `source_id`
- `name`
- `longitude`
- `latitude`
- `date_start`
- `date_end`
- `count_or_weight`
- `notes`

This file is convenient for plotting and checking coverage, but the final model should use the richer source-specific tables whenever available.

---

# 3. Existing exploratory raster — DO NOT mistake for the final GP

`economic_activity_grid_25yr_v02.csv`

Rows: **69,972**.

Structure:
- 28 time intervals;
- 25-year slices;
- 2,499 spatial grid cells per slice;
- 0.25-degree grid.

Columns:
- `bin_start_year`
- `bin_end_year`
- `longitude`
- `latitude`
- `urban_raw`
- `archaeological_occupation_raw`
- `shipwreck_trade_raw`
- `amphora_trade_raw`
- `port_connectivity_raw`
- `mining_production_raw`
- normalized versions of all six;
- `settlement_domain`
- `exchange_domain`
- `connectivity_domain`
- `production_domain`
- `economic_activity_default`
- `economic_activity_plus_production`
- `evidence_diversity`
- `coverage_gap_proxy`

The provisional default index was:

\[
E_{\mathrm{old}}(s,t)
=
0.45\,S(s,t)
+
0.35\,X(s,t)
+
0.20\,C(s,t),
\]

where:
- \(S\) = mean of normalized urban + archaeological-occupation signals;
- \(X\) = maritime shipwreck signal, or mean(shipwreck, amphora) when the amphora chronology is applicable;
- \(C\) = port-connectivity signal.

Mining only entered a sensitivity index.

Raw point evidence was Gaussian-kernel smoothed with a fixed sigma of 50 km, then globally `log1p` normalized against the 99th percentile over the full 700–0 period.

**This weighting and 50-km smoothing are exploratory only.**
They should not be carried forward as the inferential model.

The final analysis should infer spatial range, temporal range, proxy loadings, and source noise from the data.

---

# 4. Existing diagnostics / static figures

No interactive HTML should be produced unless explicitly requested.

Current static figures:
- `figures/economic_activity_700BCE.png`
- `figures/economic_activity_600BCE.png`
- `figures/economic_activity_500BCE.png`
- `figures/economic_activity_400BCE.png`
- `figures/economic_activity_300BCE.png`
- `figures/economic_activity_200BCE.png`
- `figures/economic_activity_100BCE.png`
- `figures/economic_activity_25BCE.png`
- `figures/component_trends_700BCE_to_0.png`
- `figures/evidence_diversity_100BCE.png`

These are useful diagnostics of the extracted data, not final posterior GP maps.

---

# 5. Important datasets identified but NOT yet integrated

The fresh session must not say these are integrated until the user supplies the actual files and they are read successfully.

## 5.1 Palmisano, Bevan & Shennan central-Italy settlement archive

**Status:** open and identified, but the previous runtime blocked the ZIP materialization.

**Known size:** 7,383 archaeological sites and 10,971 occupation phases.

**Coverage:** central Italy, especially highly relevant to Etruria, Latium, Tuscany, western Umbria.

**License:** CC0/open.

**Priority:** EXTREMELY HIGH.

**Why it matters:**
This is probably the strongest direct settlement/demographic dataset for the early part of the target period and substantially improves the weak nationwide pre-Republican coverage.

**What the user should manually add:**
Preferably the complete extracted archive, not screenshots. Supply the original CSV/SHP/GeoJSON tables plus metadata/readme.

**Required fields to preserve:**
- site ID;
- coordinates / geometry;
- occupation phase start/end;
- site class/type;
- site size if available;
- survey/source identifier;
- any confidence/chronology field.

**Do not collapse occupation phases to midpoints.**

---

## 5.2 Coin Hoards of the Roman Republic (CHRR)

**Status:** identified/open; bulk output could not be materialized in the previous runtime.

**Main chronological relevance:** especially approximately 155 BCE–2 CE.

**Role:** monetization / monetary circulation.

**Priority:** HIGH for the last ~150 years of the model.

**What the user should manually add:**
A full CSV/GeoJSON/KML/API export with:
- hoard ID;
- findspot coordinates;
- earliest/latest deposition date or terminus post quem;
- coin count / hoard size if available;
- composition;
- certainty;
- source metadata.

**Critical warning:**
Coin hoarding is confounded by war, insecurity, concealment, and recovery behavior. Do not treat raw hoard density as a pure economic-activity measure.

Recommended final-model treatment:
- a monetization proxy with a deliberately uncertain loading;
- ideally include war/conflict intensity as a nuisance predictor;
- otherwise use a source-specific residual/bias process and weak prior on its loading.

---

## 5.3 Itiner-e road network

**Status:** identified/open; large dataset could not be materialized by the previous runtime.

**Known size:** 14,769 road segments empire-wide.

**License:** CC BY 4.0.

**Role:** terrestrial accessibility / integration.

**Priority:** HIGH, but must be used chronologically.

**What the user should manually add:**
Full road GeoJSON/GeoPackage/Shapefile with all segment attributes, especially:
- segment geometry;
- route name;
- certainty;
- major/minor classification;
- chronological start/end or historical source fields;
- provenance.

**Critical warning:**
Do not simply back-project an imperial road network to 700 BCE.

Only segments with evidence for use before 0 CE should directly enter the pre-0 time-varying connectivity field. Undated or later-imperial segments must be excluded, given probabilistic onset dates, or used only in a sensitivity analysis.

**Recommended derived quantities:**
- local dated road density;
- travel-time accessibility;
- closeness/access to urban centers;
- network betweenness/flow potential;
- distance to active road;
- accessibility to ports.

Roads are probably better used as **predictors of economic activity / a connectivity latent domain** than as a direct equal-weight economic observation.

---

## 5.4 HYDE 3.3 historical population / land-use grids

**Status:** identified but ancient spatial raster files were not materialized.

**Role:** reconstructed population density, cropland, pasture / agricultural baseline.

**Priority:** MEDIUM-HIGH.

**What the user should manually add:**
Relevant spatial raster slices covering Italy and the target period, with metadata.

Preserve:
- exact HYDE version;
- time stamp of each raster;
- variable;
- units;
- resolution;
- reconstruction uncertainty if supplied.

**Critical warning:**
HYDE is itself a model-based reconstruction. It must **not** be treated as an independent archaeological observation with the same status as settlement finds.

Recommended treatment:
- use as a prior mean / broad demographic covariate;
- or run the model with and without HYDE;
- do not let it dominate the latent field.

---

# 6. Additional data that should be sought after the four missing high-priority sources

These are categories for a fresh search, not claims that a specific complete database has already been acquired.

Highest-value additions would be:

1. regional archaeological survey datasets for northern and southern Italy before 100 BCE, to reduce the central-Italy coverage advantage;
2. settlement/site-size estimates outside the Palmisano region;
3. Republican **single coin finds** as well as hoards, if a spatially explicit open dataset can be obtained;
4. amphora consumption/distribution datasets beyond stamps;
5. amphora production centers, kilns, workshops, quarries, and other production installations with dates;
6. dated warehouses, markets, emporia, harbors, and commercial infrastructure;
7. agricultural-production proxies;
8. conflict/war-event surfaces, principally to separate monetary hoarding from economic activity;
9. explicit archaeological-survey intensity / publication-intensity layers;
10. period-specific population estimates independent of HYDE where available.

Every new source must be entered in a source ledger with:
- provenance;
- license;
- spatial coverage;
- temporal coverage;
- observation unit;
- known sampling biases;
- chronological uncertainty;
- whether it is raw evidence, an infrastructure predictor, or an already modeled reconstruction.

---

# 7. The final model should be a continuous spatiotemporal GP

## 7.1 Core latent field

Let

\[
E(s,t)
\]

be latent economic activity at spatial location \(s\) and continuous historical time \(t\), for:

\[
t\in[-700,0].
\]

Use projected coordinates in kilometers, **not raw longitude/latitude Euclidean distance**.

A good initial coordinate system for Italy is a suitable equal-area/metre projection such as ETRS89 / LAEA Europe (EPSG:3035), with sensitivity to projection choice negligible at this scale.

A reasonable baseline prior is:

\[
E(s,t)=m(t)+G(s,t),
\]

where \(m(t)\) is a smooth overall temporal trend and \(G(s,t)\) is a zero-mean spatiotemporal GP.

Baseline covariance:

\[
\operatorname{Cov}\{G(s,t),G(s',t')\}
=
\sigma_E^2
K_s(\|s-s'\|;\rho_s,\nu_s)
K_t(|t-t'|;\rho_t,\nu_t),
\]

using Matérn kernels in space and time.

Do **not** fix the spatial range at 50 km.

Infer:
- \(\rho_s\): spatial correlation range;
- \(\rho_t\): temporal correlation range;
- \(\sigma_E\): field variance.

A separable Matérn GP is the first model. Compare it to a nonseparable spatiotemporal covariance (e.g. Gneiting-type) as a sensitivity model if computation permits.

A sparse Matérn/SPDE representation is entirely acceptable: SPDE is a computational representation of a GP, not a different conceptual model.

---

# 8. Do not choose proxy weights by hand

The old 0.45 / 0.35 / 0.20 weights are provisional visualization weights only.

The final analysis should estimate how strongly each proxy measures economic activity.

## 8.1 Recommended hierarchical latent-measurement structure

Group evidence into interpretable domains:

### Settlement / demography
- cities;
- Palmisano settlements;
- AIDA occupation;
- possibly HYDE as prior/covariate, not raw evidence.

### Exchange / market integration
- shipwrecks;
- amphorae;
- possibly coin circulation data.

### Connectivity / infrastructure
- ports;
- dated roads / network accessibility.

### Production
- mines;
- kilns;
- quarries;
- production sites.

### Monetization
- CHRR;
- preferably single coin finds if obtainable.

For domain \(d\), define a latent domain field:

\[
F_d(s,t)
=
a_d+\lambda_d E(s,t)+\delta_d(s,t),
\]

where:
- \(E(s,t)\) is the shared overall economic field;
- \(\lambda_d>0\) is the domain loading;
- \(\delta_d(s,t)\) is a smaller domain-specific residual GP.

For proxy \(k\) belonging to domain \(d(k)\):

\[
g_k\{\mu_k(s,t)\}
=
\alpha_k
+
\rho_k F_{d(k)}(s,t)
+
B_k(s,t)
+
X_k(s,t)\beta_k.
\]

Here:
- \(g_k\) is the appropriate link for that data type;
- \(\rho_k>0\) is the proxy loading;
- \(B_k\) is source-specific sampling/preservation bias;
- \(X_k\beta_k\) contains known exposure/survey/confound variables.

This is the key replacement for arbitrary weights.

The model learns:
- how strongly each domain covaries with overall activity;
- how strongly each proxy reflects its domain;
- how noisy each source is;
- how much signal is shared vs source-specific.

---

# 9. Identifiability and priors for the loading model

A latent factor requires a scale/sign convention.

Recommended:

1. constrain all economic loadings to be positive;
2. standardize the shared GP:
   \[
   \operatorname{Var}(E)=1
   \]
   or fix one highly interpretable loading;
3. use weakly informative positive priors such as:
   \[
   \log \lambda_d \sim N(0,0.5^2)
   \]
   and
   \[
   \log \rho_k \sim N(0,0.75^2),
   \]
   adjusted after prior predictive checks.

Do not force all proxies to have equal variance before modeling merely to manufacture equal weights.

Normalization can be useful for diagnostics, but the probabilistic observation model should retain the information in:
- counts;
- presences;
- mark sizes;
- exposure;
- sampling uncertainty.

---

# 10. Source-specific likelihoods

A single Gaussian regression for all proxies would be inappropriate.

## Cities / settlements / ports / mines
Prefer a point-process formulation where feasible:

\[
\log \Lambda_k(s,t)
=
\alpha_k+\rho_k F_d(s,t)+B_k(s,t).
\]

A log-Gaussian Cox process is particularly natural for spatial archaeological occurrence data.

For settlements with size marks, use a marked point process or separate size model.

## Amphora counts
At site/context \(i\):

\[
y_i \sim \operatorname{NegBin}(\mu_i,\phi_{\text{amph}}),
\]

with:

\[
\log\mu_i
=
\alpha_{\text{amph}}
+
\rho_{\text{amph}}F_{\text{exchange}}(s_i,t_i)
+
u_{\text{site}}
+
\log(\text{exposure}_i)
\]

if an exposure variable can be constructed.

If exposure is unavailable, aggregate heavily enough that repeated records from one context do not masquerade as independent observations.

## Shipwrecks
Use a maritime/coastal point-process support rather than a fully isotropic inland likelihood.

## Radiocarbon occupation
Use calibrated chronological probability and a site-level observation process; do not treat every radiocarbon determination as an independent economic event.

## Coin hoards
Use a point/count model with:
- monetization loading;
- conflict/insecurity nuisance effect if possible;
- broad residual variance.

## HYDE
Prefer:
\[
m_E(s,t)
=
\beta_0+\beta_{\text{HYDE}}\log(\text{population}+c)+\ldots
\]
or a prior mean for the demographic domain.

Do not enter HYDE as thousands of pseudo-observations with tiny noise.

## Roads
Prefer a dated accessibility covariate/domain rather than a raw observation count.

---

# 11. Chronological uncertainty must be integrated into the GP

For an observation dated to interval \([a_i,b_i]\), do not evaluate the field only at:

\[
(a_i+b_i)/2.
\]

Instead integrate:

\[
p(y_i\mid E)
=
\int_{a_i}^{b_i}
p(y_i\mid E(s_i,t))p_i(t)\,dt.
\]

Possible \(p_i(t)\):
- calibrated radiocarbon density;
- uniform distribution if all that is known is an interval;
- triangular/normal/aoristic distribution when catalogue chronology justifies it.

Numerically use:
- quadrature;
- temporal basis integration;
- or sufficiently fine temporal knots.

The final output can be mapped at 10-, 25-, or 50-year intervals, but **prediction intervals are not the temporal resolution of the latent model**.

---

# 12. How to derive sensible “weights” after fitting

Do not begin by assigning weights.

After fitting, report several posterior summaries.

## 12.1 Domain loading weights

A descriptive normalized domain contribution:

\[
w_d=
\frac{\lambda_d^2}
{\sum_j\lambda_j^2}.
\]

## 12.2 Within-domain proxy weights

\[
w_{k\mid d}
=
\frac{\rho_k^2}
{\sum_{j\in d}\rho_j^2}.
\]

These are descriptive posterior summaries, not fixed likelihood weights.

## 12.3 Reliability / communality

For source \(k\), calculate a measure such as:

\[
R_k
=
\frac{\rho_k^2\operatorname{Var}(F_d)}
{\rho_k^2\operatorname{Var}(F_d)+\sigma_k^2},
\]

adapted to the relevant likelihood.

This gives an interpretable answer to:
“How much of this proxy is shared economic signal versus source-specific noise?”

## 12.4 Leave-one-source-out influence

Refit or approximate the posterior with each source removed and calculate:

\[
\Delta_k(s,t)
=
E_{\text{all}}(s,t)-E_{-k}(s,t).
\]

Map and summarize this.

A source should not be trusted merely because it has thousands of records.

---

# 13. Preventing the largest dataset from mechanically dominating

The 7,175 amphora stamps must not automatically outweigh four mines or 185 occupation sites simply because there are more rows.

Use the model structure, not arbitrary equal-source weights:

1. aggregate dependent records at site/context level;
2. use site/context random effects;
3. model Negative Binomial overdispersion;
4. represent source-specific sampling bias;
5. estimate source loadings hierarchically;
6. use domain-specific residual GPs;
7. perform leave-one-source-out sensitivity;
8. use blocked spatial-temporal validation.

Only consider explicit likelihood tempering if a well-specified observation model still produces pathological dominance. If tempering is used, report it transparently as a sensitivity analysis.

---

# 14. Survey / preservation bias is essential

The archaeological record is an observation process.

A useful conceptual model is:

\[
\text{Observed proxy intensity}
=
\text{true economic signal}
\times
\text{preservation}
\times
\text{survey/discovery intensity}.
\]

At minimum include:
- source-specific intercepts;
- site/study random effects;
- broad source-specific spatial bias fields;
- publication/survey identifiers where available.

For Palmisano, preserve the original survey/source identity so that surveys with different intensity can have separate observation effects.

Do not interpret low archaeological density in poorly surveyed areas as confidently low economic activity.

Posterior uncertainty should increase in low-coverage areas.

---

# 15. Suggested computational implementations

Preferred scalable options:

## Option A — R / INLA / inlabru
Good if using:
- Matérn SPDE GP;
- log-Gaussian Cox processes;
- multiple likelihoods;
- point patterns;
- large spatial datasets.

This is conceptually a continuous GP even though computation uses a mesh.

## Option B — Stan
Use a sparse/low-rank or inducing-point spatiotemporal GP.
Maximum flexibility for the hierarchical measurement model, but more implementation work.

## Option C — PyMC
Use HSGP/low-rank GP components and multiple likelihoods.
Good for iterative prototyping.

The first serious model should favor statistical correctness and tractability over a fully dense GP covariance matrix.

---

# 16. Model comparison / validation plan

Use blocked validation, not random row-wise CV.

Required validation schemes:

1. **Spatial block holdout** — hold out regions of Italy.
2. **Temporal block holdout** — hold out historical intervals.
3. **Spatiotemporal block holdout** — hold out region × period combinations.
4. **Leave-one-proxy-out** — test whether the remaining proxies predict the omitted one.
5. **Leave-one-domain-out** — evaluate robustness of the latent field.
6. **Prior predictive checks**.
7. **Posterior predictive checks** for each likelihood.

Compare:
- separable Matérn GP;
- nonseparable spatiotemporal GP if feasible;
- model with/without HYDE;
- model with roads as predictor vs measurement domain;
- model with ports as predictor vs proxy;
- model with/without coin-hoard evidence;
- model with varying source-bias field complexity.

---

# 17. Desired final outputs

The next session should ultimately produce:

## Data
- harmonized observation CSV for every source;
- source metadata/ledger;
- chronology-probability tables;
- GP prediction grid in a projected coordinate system plus lon/lat;
- posterior mean;
- posterior SD;
- lower/upper credible intervals;
- domain-specific latent fields;
- effective loading/reliability summaries.

## Static figures only
At minimum:
- posterior mean maps every 50 or 100 years;
- posterior SD / uncertainty maps;
- settlement-domain maps;
- exchange-domain maps;
- connectivity-domain maps;
- production/monetization maps where supported;
- temporal trajectory with credible interval;
- posterior loading plot;
- source reliability plot;
- leave-one-source-out influence plots;
- spatial coverage map;
- maps comparing early, middle, and late periods using the **same scale**.

No interactive output unless explicitly requested.

## Reproducibility
Include:
- one-click Windows `.bat` or `.cmd` pipeline;
- all R/Python scripts;
- `requirements.txt` / R package manifest;
- deterministic random seeds;
- automatic output folders;
- saved model diagnostics.

---

# 18. How this will eventually join to the inscription data

For inscription \(i\) with location \(s_i\) and uncertain date distribution \(p_i(t)\), do not use nearest raster cell and date midpoint.

Use:

\[
\bar E_i
=
\int E(s_i,t)p_i(t)\,dt.
\]

Propagate posterior uncertainty in \(E\) into the downstream model.

Also extract separately:
- settlement/demography field;
- exchange field;
- connectivity field;
- production/monetization field;
- data-coverage uncertainty.

This is important because the substantive hypothesis may concern **contact/exchange** rather than general economic mass.

---

# 19. Files in the package and their role

## Core evidence
- `source_points_all.csv`
- `urban_city_time_weights_25yr.csv`
- `base_nonurban_source_time_weights_25yr.csv`
- `ports_italy.csv`
- `ports_time_weights_25yr.csv`
- `amphora_stamps_italy.csv`
- `amphora_site_type_summary.csv`
- `amphora_type_chronology.csv`
- `amphora_time_weights_25yr.csv`

## Exploratory derived products
- `economic_activity_grid_25yr_v02.csv`
- `time_slice_summary_v02.csv`
- `figures/*.png`

## Provenance
- `data_sources_v02.csv`
- `README.md`
- `FRESH_SESSION_HANDOFF.md`
- `build_manifest.csv`

---

# 20. Exact instructions for the fresh session

When this package and the manually downloaded missing files are provided, the fresh session should:

1. read this handoff first;
2. inventory every supplied file and preserve raw originals;
3. verify coordinates/CRS and chronological conventions;
4. integrate Palmisano;
5. integrate CHRR;
6. integrate dated Itiner-e roads;
7. integrate HYDE only as a modeled prior/covariate;
8. search for additional open regional settlement/economic datasets, especially north/south Italy;
9. rebuild chronology distributions at the observation level;
10. construct the hierarchical domain/proxy model above;
11. fit a continuous spatiotemporal Matérn GP;
12. infer proxy/domain loadings and source-specific noise;
13. run blocked validation and leave-one-source-out analyses;
14. export posterior CSVs and static figures;
15. create a one-click Windows run script.

Do **not** simply add the new layers to `economic_activity_default`.
Do **not** retain the arbitrary 0.45/0.35/0.20 weights as the inferential model.
Do **not** treat the existing 50-km kernel bandwidth as known.
Do **not** treat absence of archaeological observations as true economic absence.
Do **not** midpoint uncertain dates.
Do **not** back-project imperial roads or urban sizes without chronological justification.

The current package is the evidence/provenance handoff; the next stage is the actual latent spatiotemporal economic reconstruction.
