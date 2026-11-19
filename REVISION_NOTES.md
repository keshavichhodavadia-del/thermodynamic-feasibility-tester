# Revision notes — thermodynamic balance and exergy upgrade

## Implemented changes

- **Model 3 (steady control volume):** added physical flow-exergy accounting and a reversible shaft-work ceiling. Requires environment temperature `T0_K` and dead-state enthalpy/entropy for each inlet and outlet stream. Optional kinetic/potential terms are included consistently when enabled.
- **Model 4 (closed system):** added non-flow exergy and a reversible useful-work ceiling. Requires `T0_K`, `environment_pressure_kPa`, and `delta_V_m3`. The comparison uses useful work after subtracting displacement work against ambient pressure.
- **Model 8 (mechanical conversion):** now requires measured `heat_rejected_kw` to close the First Law. If heat rejection is positive, `heat_rejection_temperature_K` is required for the entropy balance. The unused mechanical resource is not silently treated as generator heat loss. An output above the mechanical resource ceiling is still rejected immediately.
- **Model 9 (solar radiation):** added explicit First Law, Petela radiation exergy, entropy generation and exergy-destruction calculations. This remains an ideal undiluted-blackbody upper-bound model and assumes all unconverted incident energy is rejected as heat at ambient temperature.
- **Model 10 (electrochemical):** added reaction enthalpy and entropy balances alongside the Gibbs reversible-work ceiling. Requires `delta_H_kj_per_mol_rxn`, `reaction_temperature_K`, `heat_into_reactor_kw`, and `heat_boundary_temperature_K` in addition to the prior reaction direction, Gibbs energy, rate and output. The simplified model requires heat transfer at the reaction temperature.

## User interface and input handling

- Updated the manual forms, explanatory notes, automatic analyzer handoff, CLI prompts, scenario input extraction, JSON templates and static GitHub Pages build.
- When the automatic analyzer identifies a supported model but the required exergy reference data or balance terms are missing, the website now offers the matching manual form and pre-fills quantities it safely extracted.
- Missing data is reported as **INSUFFICIENT DATA / MODEL REQUIRED**, not silently replaced with guessed values.

## Scientific scope notes

- Model 3 includes physical flow exergy only; chemical and mixing exergy are not included.
- The compact website Model 3 form supports one inlet and one outlet of the same fluid. The Python/JSON engine supports multiple streams.
- The compact website Model 4 form exposes one heat interaction. The Python/JSON engine supports multiple heat interactions.
- Model 8 treats the supplied mechanical power as the resource available at the converter boundary and requires measured heat loss for energy closure.
- Model 9 is not a detailed photovoltaic model; the ideal radiation assumptions must not be interpreted as real panel performance.
- Model 10 is an isothermal, isobaric Gibbs-limit model; a heat-boundary temperature different from the reaction temperature needs a more detailed reactor exergy model.
- Passing a thermodynamic check does not establish engineering buildability, safety, economic viability or commercial performance.

## Verification performed

- `python -m unittest -v` — 63 tests passed.
- `python thermodynamic_feasibility_tester_MASTER.py --self-test` — 27 checks passed.
- `node --test website/test_ui.cjs` — 2 tests passed.
- Python source compilation and JavaScript syntax checks passed.
