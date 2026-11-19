# CL 202 Thermodynamic Feasibility Tester


The GitHub Pages version runs the original Python engine in the visitor's browser using Pyodide. Its first calculation downloads the Python runtime from jsDelivr. The project requires no API key for calculations.

To rebuild the static site after editing the engine or website, run `python build_pages.py`. Publish the `docs` directory through GitHub Pages. The calculation code and downloaded JSON remain available in the shared version.

## One engine, nine numbered models

`thermodynamic_feasibility_tester_MASTER.py` is the single calculation engine for **both** the terminal program and the website, so the model numbers are the same everywhere. Each model has a **stable internal key** (used in saved JSON cases and the API) and a **display number** (shown on the website and in the terminal menu); renumbering never changes the keys. Demo cases, JSON templates and the self-tests live in `feasibility_examples.py`.

| # | Model (engine key) | Typical technologies | Main checks |
|---|---|---|---|
| 1 | Fixed-temperature reservoir cyclic heat engine (`fixed_reservoir_engine`) | Heat engines with given source and sink temperatures | Carnot limit, First Law, S_gen |
| 2 | Finite hot + cold stream, work produced (`finite_stream_engine`) | Waste-heat engines, data-centre and exhaust-gas heat | Exact W_max (S_gen = 0), First Law; missing cold outlet from the First Law; limit-only when no claim |
| 3 | General steady control volume (`steady_control_volume`) | Turbines, compressors, pumps, nozzles | Mass, steady-flow energy and entropy balances (each when its data exist) |
| 4 | Closed system (`closed_system`) | Piston-cylinder, sealed tank, complete cycles | Q − W = ΔE (and U₂), S_gen = ΔS − ΣQ/T |
| 5 | Refrigerator / heat pump (`refrigeration_cycle`) | Refrigerators, freezers, air conditioners, heat pumps | Q_H = Q_C + W_in, COP vs Carnot COP, S_gen = Q_H/T_H − Q_C/T_C |
| 6 | Heat exchanger, no shaft work (`finite_stream_heat_recovery`) | Water–water, oil–water, data-centre heat recovery | W = 0; energy closure (insulated or with heat loss), S_gen, temperature cross, exergy destroyed; one missing outlet derived |
| 7 | Mechanical energy harvesting (`mechanical_power`) | Walking (J/step or F·d), pedalling (τω), wind (Betz), hydro (ρgQH), no-input claims | Output ≤ recoverable mechanical input |
| 8 | Solar radiation conversion (`solar_blackbody`) | Solar converters | Petela–Landsberg limit |
| 9 | Electrochemical conversion (`reaction_gibbs`) | Fuel cells | Output ≤ −ΔG × rate (ΔG sign checked) |

Two helpers are **not** selectable models: the property calculator (`property_change`: Δh, Δs, T_eff, latent heat, totals for a given mass — used inside the models and for “calculate the enthalpy change” questions) and the fallback (`unsupported_domain`), which returns MODEL REQUIRED for processes with no validated model (transient conduction, tidal energy) instead of forcing an unrelated model. Old saved cases with these keys still run.

Every result carries a `check_table`: one row per balance or limit (check, equation, calculated value, criterion, PASS / FAIL / NOT EVALUATED / NOT APPLICABLE), built by the engine and shown on the website as a table.

Verdicts: `FEASIBLE / NOT REJECTED`, `NOT FEASIBLE` (or a model-specific NOT FEASIBLE …), `INSUFFICIENT DATA / MODEL REQUIRED` (missing data; shown as “Model required” only for the fallback), `LIMIT CALCULATED — NO CLAIM TO TEST` (Model 2 asked for its maximum work), `SECOND-LAW ADMISSIBLE / NOT REJECTED` (heat exchanger) and `PROPERTY CALCULATION ONLY` (property calculator).

Tests: `python -m unittest` (Python, including `test_upgrade.py` and the 63 cases of the team test-case PDF in `website_test_cases.py`), `node --test website/test_ui.cjs` (website: model list, example order, every example run through the real engine, checks table) and `python thermodynamic_feasibility_tester_MASTER.py --self-test`.

## Automatic analysis from a plain-English description

Paste a problem as it would appear in a textbook and click **Analyze scenario**. `auto_analyzer.py` then runs five steps:

1. **Scan.** It finds every number with a unit and converts it to engine units. It handles K, °C, °F, kW, MW, kJ/min, Btu/h, kJ per cycle, kg/s, L/min, kJ/(kg K), W/m², m/s, mol/s, g/s and steps/min.
2. **Assign roles.** It reads the words around each quantity and decides what the quantity is. Examples: heat input, rejected heat, claimed work, source or sink temperature, stream inlet or outlet, wind speed, head, ΔG.
3. **Map source and sink.** It groups hot and cold streams and separates them from fixed-temperature reservoirs. Then it chooses the domain: thermal, mechanical, wind, hydro, solar or chemical.
4. **Infer.** Values that the equations fix uniquely are calculated (by the engine) — a cold-stream or exchanger outlet, work = Q_H − Q_C, the third of Q_C, Q_H, W_in, ΔE = Q − W, F·d, τω, ½ρAv³, ρgQH. When a problem is underdetermined it lists exactly what is missing. Any default (Cp of water, air density, sun 5778 K, ambient 298.15 K) is labelled in the report.
5. **Solve.** It builds the standard engine case and runs `run_case`. The report also says **why** that model was chosen and which words pointed to it.

The result shows the following:
- Each extracted value next to the phrase it came from.
- The source and sink mapping.
- Every calculated value and assumption.

If something essential is missing or ambiguous, for example a claimed output given as a range, the site lists exactly what to add instead of guessing.

The thermal mappings work as follows:

| Description contains | Model used |
|---|---|
| Two fixed temperatures, heat and work | Carnot limit, First Law, S_gen = Q_C/T_C − Q_H/T_H |
| A hot stream (inlet and outlet) and a sink temperature | T_H,eff = \|Δh\|/\|Δs\|, then the Carnot limit. This is exact: W_max = ΔH − T₀ΔS |
| A hot stream and a cold stream | Finite-stream engine: S_gen = ṁ_hΔs_h + ṁ_cΔs_c and the T_eff reference |
| Cp(T) = a + bT, or latent heat with entropy of fusion | Linear-Cp integrals; phase-change sink T = Δh/Δs |
| One reservoir only ("converts it entirely into work") | Cycle entropy balance, which gives S_gen < 0 (Kelvin–Planck) |

The other domains use these limits:

| Domain | Limit |
|---|---|
| Wind | Betz limit (16/27)·½ρAv³ |
| Hydro | ρgQH |
| Solar | Petela–Landsberg |
| Fuel cells | −ΔG·ṅ, with standard ΔG for H₂, CH₄, methanol and ethanol if not given |
| No energy input | Zero (perpetual-motion claim) |

Refrigerators and heat pumps are reported as outside the useful-work scope.

From the terminal:

```powershell
python auto_analyzer.py "An inventor claims his engine takes 100 kW of heat from a source at 600 K, rejects heat to surroundings at 300 K and produces 60 kW of work."
python auto_analyzer.py --json "A wind turbine with 40 m rotor diameter in 10 m/s wind claims 800 kW."
```

`test_auto_analyzer.py` checks the five M3 validation cases written as prose, and more than 15 textbook-style scenarios across all domains.

## Interactive website

Double-click `Start_ThermoLab.cmd` to open the local website on an available port. Keep the terminal open while using it. You can also start it from this folder with:

```powershell
python website/serve.py --port 0 --open
```

Paste a complete supported scenario and click **Analyze scenario**. The local parser selects a model, extracts quantities with their original source phrases, converts units, and runs the Python solver directly. **Review automatically filled values** opens the populated form. If data is missing or ambiguous, the form retains the recognized values for completion.

Automatic local extraction supports explicitly stated quantities for walking generators, fixed-reservoir engines, constant-heat-capacity hot and cold streams, ideal blackbody radiation, signed Gibbs-energy reactions. For other phrasing or models, use the numeric forms. Values are never filled from unrelated worked-example defaults.

Every result also shows visual summaries built only from values the engine calculated:
- an **energy path** (heat in, work, heat out on one scale);
- the **First-Law** (and mass-balance) **closure** with its residual;
- an **entropy-generation gauge** (negative = violation, positive = admissible);
- a **temperature profile** in K and °C.

**Explore how an input changes the verdict** runs a sensitivity sweep: pick one input, a range and 3–7 points, and the same model is re-run at each value, showing the claim, the limit and the verdict. The **Dark / Light** button in the header switches the theme and remembers the choice.

Expand **View calculation code** beneath a result to show the actual Python calculation functions and verdict logic. Download the full calculation engine, the analyzer or the parser from that panel. The **Engine input JSON** panel lets your professor reproduce the same case directly with `python thermodynamic_feasibility_tester_MASTER.py --input case.json`.

For walking, enter mechanical input and claimed electrical output in watts. Alternatively, choose the energy-per-step model and enter joules per step, steps per minute, and electrical output in watts.

The walking model always checks the input energy limit and reports the power left after electrical output. For a steady generator that rejects all remaining power as heat, supply the optional heat-rejection temperature to compute entropy generation. Supplying measured heat loss adds an independent First Law balance. Inferred heat loss is labeled in the result.

The website runs entirely on your computer using the original Python engine. All website assets are local. The website serves fresh assets on each page load.

## CL 202 assignment runner (terminal only)

The two CL 202 coffee assignments are not part of the website, the submitted data or the manuscript. Their runner is kept for the terminal and for the existing tests. Run it with Python 3.9 or newer. No packages need to be installed.

```powershell
python run_mock_assignments.py 100 --output-dir generated_cases
```

Replace `100` with the last three digits of your roll number. Base accepts `000` through `399`. Modified accepts `001` through `400`. The command runs both where their ranges overlap. Use `--case base` or `--case modified` to select one, and omit `--output-dir` for a terminal report alone.

The output directory contains one Base input and two Modified inputs. For Modified, the gas and wax property results feed the equivalent-temperature engine calculation. You can run any generated input directly through the original engine:

```powershell
python thermodynamic_feasibility_tester_MASTER.py --input generated_cases/base_x100.json
```

Use the assignment runner for the strict Base ceiling. It solves the cold outlet at zero entropy generation with the same hot-stream duty and cold-stream inlet and flow. The standalone engine also reports an effective-temperature reference using the cold outlet implied by the claim.

The Modified result assumes the wax sink can absorb heat at the melting temperature. For a separate supply-side check, pass a server heat budget in kW. The runner rejects a scenario whose specified gas states release more than the supplied budget.

```powershell
python run_mock_assignments.py 100 --case modified --server-heat-kw 12
```

## Verification

```powershell
python -m unittest -v
python thermodynamic_feasibility_tester_MASTER.py --self-test
```

At `X = 100`, Base requires 1.000 kW against a strict ceiling of 1.153 kW, and Modified requires 1.333 kW against a ceiling of 2.584 kW. The default cases assume no separate Modified server heat budget. Base first fails at `291`. Modified first passes at `012`.

## Step 2 model coverage

The original CLI, its interactive menu, and the Python `run_case` entry point use the same routes. Generate editable examples and try the walking case:

```powershell
python thermodynamic_feasibility_tester_MASTER.py --make-templates
python thermodynamic_feasibility_tester_MASTER.py --input thermo_case_templates/mechanical_power.json
```

Every power claim uses `W_out_kw`. The routes below compare it with an ideal resource ceiling. A full energy or entropy balance requires additional boundary data.

| Route | Other JSON inputs | Scope |
|---|---|---|
| `mechanical_power` | `recoverable_mechanical_power_kw` **or** `recoverable_energy_per_event_j` and `events_per_s` | Recoverable work at the converter input, averaged over the same interval as the electrical output. The example uses illustrative step energy. |
| `solar_blackbody` | `T_source_K`, `T_ambient_K`, `irradiance_W_m2`, `collection_area_m2` | Ideal Petela–Landsberg radiative exergy bound. Irradiance and effective area refer to the same beam at the aperture. |
| `reaction_gibbs` | `reaction_direction`, `delta_G_kj_per_mol_rxn`, `reaction_rate_mol_rxn_s` | Isothermal, isobaric forward reaction with signed Gibbs energy supplied at the operating conditions. The energy and rate use the same stoichiometric mole of reaction. Positive Gibbs energy requires external work input. |

Missing model inputs return **INSUFFICIENT DATA / MODEL REQUIRED** and name the missing quantities. For scenarios outside these routes, use `unsupported_domain`. Examples include wind without a supplied recoverable work limit, solar radiation needing a diffuse or spectral model, and chemical reactions without a Gibbs energy model.

The [implementation flowchart](FLOWCHART.md) shows the balance checks, derivations, and verdict paths. The ideal radiation and signed cell-work relations follow [Petela's radiation exergy analysis](https://www.sciencedirect.com/science/article/pii/S0038092X03002263) and the [IUPAC definition of standard electromotive force](https://goldbook.iupac.org/terms/view/S05913).

## Step 3 plain-language input

Start the offline terminal demo:

```powershell
python scenario_input.py "Can I generate electricity by walking?" --interactive
```

When asked for details, enter `Recoverable energy is 4 J per step, at 120 steps per minute. Electrical output is 5 W.` The tester quotes the quantities it found, shows the normalized inputs, and compares the 5 W claim with an 8 W ceiling. The step energy is illustrative. For a single scripted run, use `--reply` followed by the additional details. Add `--output-json walking_case.json` to save the case for the original tester.

Use `--json` with a scenario argument for a machine-readable response. The response contains the selected route, original quotes, follow-up questions, normalized engine cases, and the result. If input is missing, it returns `needs_input` and leaves `cases` empty. Use repeated `--reply` options to supply additional details. The same interface is available in Python through `analyze_scenario(text, replies=...).as_dict()`.

```powershell
python scenario_input.py --json "Generating electricity by walking" --reply "Recoverable energy is 4 J per step at 120 steps per minute. Electrical output is 5 W."
```

The offline parser recognizes the walking demo and explicit CL 202 Assignment 1 Base or Modified requests with a suffix, such as `Run CL 202 Assignment 1 Modified for suffix 100.` If it cannot interpret a phrase, label the quantities explicitly or use the original structured inputs.

The website uses numeric forms after suggesting a model. The command-line text parser above remains available for labeled demo inputs.

## Manuscript results, data files and figures

The manuscript (ACS *Industrial & Engineering Chemistry Research* format, in the main submission folder) validates every model against literature data. These commands, run from this folder, regenerate its numbers, figures and the whole **Data** folder:

```powershell
python data_export/validation_against_literature.py     # Table 1: code vs literature for Models 1-9
python manuscript_figures/make_figures.py figures         # Figures 2-4 (PDF and PNG) + figure_data.json
python data_export/export_data.py ../../Data              # one CSV + one results TXT per model, validation, test logs
```

`data_export/website_examples.json` lists the 43 website examples. It is extracted from `website/dist/app.js` with `node data_export/dump_examples.cjs website/dist data_export/website_examples.json`; re-run that after changing the examples. `validate_benchmarks.py` keeps the earlier benchmark set (including the assignment runner) for the existing tests.
