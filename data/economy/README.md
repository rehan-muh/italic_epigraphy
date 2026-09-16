# Ancient Italy economic activity, v0.2 static build

Target interval: 700 BCE to the 1 BCE/CE boundary, in 25-year slices.

## Default composite

The default index is:

economic_activity = 0.45 * settlement + 0.35 * exchange + 0.20 * coastal_connectivity

where:
- settlement = mean(urban-city signal, calibrated archaeological-occupation signal)
- exchange = shipwreck trade before amphora chronology becomes applicable; thereafter mean(shipwreck, amphora)
- coastal_connectivity = dated ancient-port intensity
- production (mines) is exported separately and included only in an alternate sensitivity index because mine coverage is very sparse

Every raw spatial component is Gaussian-kernel smoothed with sigma = 50 km.
Normalization is global over the full 700–0 interval: log1p(raw intensity), divided by the global 99th percentile, clipped to 0–1.
Time slices are therefore comparable; each period is NOT independently rescaled.

## Newly integrated in v0.2

Ancient Ports:
- 720 Italian catalogue records exported
- 531 have a foundation date <= 1 BCE and enter the temporal model
- a port is treated as persistent from its catalogue foundation date onward
- infrastructure flags are retained in ports_italy.csv but are not used to inflate the default port weight

CEIPAC-derived amphora stamps:
- 7175 stamps in Italia, Sicilia, and Sardinia exported
- 5785 stamps belong to forms with a conservative chronology window used in the default model
- abundance is aggregated by site/type and transformed as log1p(count)
- because individual stamps are not all tightly dated, each site/type signal is distributed uniformly across the eligible 25-year bins inside its broad type chronology


## Date-heaping correction

The v0.2 final build does **not** treat rounded catalogue foundation/start years as exact events.
Both Hanson city starts and Ancient Ports foundation dates are converted to aoristic activation probabilities.

The half-width of the uniform start-date interval increases with the frequency of a repeated catalogue year:
- >=100 records at the same year: +/-75 years
- >=20: +/-60 years
- >=5: +/-40 years
- otherwise rounded 50-year dates: +/-30 years
- rounded 10-year dates: +/-20 years
- specific dates: +/-10 years

This is specifically intended to prevent catalogue period-boundaries (notably the large 400 BCE city heap and 30 BCE port heap) from becoming artificial instantaneous economic shocks.
The complete time weights are exported in urban_city_time_weights_25yr.csv and ports_time_weights_25yr.csv.

## Coverage variable

evidence_diversity is the proportion of applicable component datasets that provide non-trivial local support (>0.02 after global normalization).
coverage_gap_proxy = 1 - evidence_diversity.

This is a data-coverage diagnostic, NOT a Bayesian posterior uncertainty estimate.

## Important incompleteness

The following high-value sources were identified but could not be materialized in this runtime:
- Palmisano, Bevan & Shennan central-Italy settlement archive: 7,383 sites / 10,971 occupation phases, CC0. The server returns the file but this runtime blocks application/zip materialization.
- Coin Hoards of the Roman Republic (CHRR): open ODbL API/geographic outputs, mainly 155 BCE–2 CE. Bulk serialization could not be materialized here.
- Itiner-e: 14,769 road segments, CC BY 4.0. Large GeoJSON/ZIP materialization was blocked.
- HYDE 3.3 spatial population/land-use grids: identified, but ancient 5-arc-minute grid files were not directly materializable.

They are therefore explicitly marked as not integrated in data_sources_v02.csv and are NOT represented by invented proxy values.

## Files

- economic_activity_grid_25yr_v02.csv — master 0.25-degree x 25-year grid
- time_slice_summary_v02.csv — summary by period
- source_points_all.csv — harmonized source points
- ports_italy.csv — full Italian port extract
- ports_time_weights_25yr.csv — dated active port records by period
- amphora_stamps_italy.csv — Italian CEIPAC-derived stamp records
- amphora_site_type_summary.csv — aggregated stamp evidence
- amphora_type_chronology.csv — chronology policy
- amphora_time_weights_25yr.csv — temporal allocation used in model
- data_sources_v02.csv — integrated/unintegrated source ledger
- figures/*.png — static figures only; no interactive HTML is produced


## Fresh-session continuation

See `FRESH_SESSION_HANDOFF.md` for the complete data inventory, missing-data checklist, and specification for replacing the exploratory weighted raster with a continuous latent spatiotemporal GP.

`base_nonurban_source_time_weights_25yr.csv` preserves the inherited AIDA, shipwreck, and mine temporal weights; the old urban rows were excluded because `urban_city_time_weights_25yr.csv` supersedes them with a date-heaping correction.

## v0.4 (2026-09-16): Project MERCURY sources

`scripts/economy_sources_v04.py` extracts the Project MERCURY datasets that
can be materialised for Italy 700-1 BCE (Pleiades period-attested places,
CHRR coin hoards, OXREP presses, refreshed OXREP shipwrecks; ledger in
`data_sources_v04.csv`, raw cache in `raw_v04/`). `scripts/economy_field.py`
now builds the v0.4 field in `field_v04/` (see its README); `--v03`
reproduces v0.3. Downstream code still defaults to v0.3 via
`EconomyField()`; pass `version="v04"` to switch.
