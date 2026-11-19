# CL 202 Thermodynamic Feasibility Tester

The GitHub Pages version runs the original Python engine in the visitor's browser using Pyodide. Its first calculation downloads the Python runtime from jsDelivr. The project requires no API key for calculations.

To rebuild the static site after editing the engine or website, run `python build_pages.py`. Publish the `docs` directory through GitHub Pages. The calculation code and downloaded JSON remain available in the shared version.

## Auditable input completion

Unknown form fields may be left blank. For example, enter a source at 600 K,
 a sink at 300 K, 100 kW heat input and 40 kW rejected heat. The engine derives
60 kW work, then rejects it against the 50 kW Carnot ceiling. If neither work,
rejected heat nor efficiency is known, the result remains **insufficient data**.

| Model | Supported completion from independent data |
|---|---|
| Reservoir engine | Any one of heat input, work, rejected heat; input/output from efficiency |
| Finite streams / exchanger | Work, heat duties, constant-Cp flow/Cp/inlet/outlet; linear-Cp outlet inversion |
| Steady control volume | One missing flow from mass balance; work or one heat interaction from stream energy rates |
| Closed system | Work, energy change or one heat interaction from the First Law |
| Property utility | Constant-Cp temperature or heat capacity from enthalpy change; no feasibility verdict |
| Mechanical | Work from supplied input and measured heat; event energy × frequency basis |
| Solar | Incident power, irradiance or area; output from efficiency or measured rejected heat |
| Electrochemical | Work, heat or reaction enthalpy/rate from energy balance; Gibbs energy from supplied enthalpy/entropy/temperature |
| Unsupported domains | Explicit model-required outcome; no invented thermodynamic mapping |

Derivation runs to a fixed point and never overwrites a supplied value. Equations
with multiple unknowns or a zero divisor remain unresolved. Entropy generation
is never set to zero merely to complete a balance. A balance that determined an
input closes by construction and is labelled accordingly. Mechanical resource
ceilings are not treated as measured heat losses. Zero heat or zero cycle state
change is accepted only when explicitly supplied or implied by the selected boundary.

Results show model selection, extraction, input provenance, equation dependencies,
limiting values, balance checks, remaining inputs and the scoped verdict. The
natural-language parser supports the documented thermodynamic patterns, not arbitrary
engineering prose. Use the model form for quantities it cannot map unambiguously.
Feasibility is thermodynamic admissibility within the chosen model, not proof of
material, manufacturing, cost or operational viability.

Heat exchangers use strict numerical energy closure by default. An explicit JSON
`energy_relative_tolerance` may represent known data rounding. Counterflow is the
default boundary; `flow_arrangement: "parallel"` adds its outlet restriction.
Zero terminal approach is labelled as an ideal infinite-area limit.

## One engine, ten numbered models

`thermodynamic_feasibility_tester_MASTER.py` is the single calculation engine for **both** the terminal program and the website, so the model numbers are the same everywhere. Input completion lives in `input_derivation.py`; the master module owns physical validation, balances and verdicts. Demo cases, JSON templates and the 27 self-tests live in `feasibility_examples.py`.

| # | Model (engine key) | Typical technologies | Role |
|---|---|---|---|
| 1 | Fixed-temperature reservoir cyclic heat engine (`fixed_reservoir_engine`) | Heat engines with given source and sink temperatures | Core |
| 2 | Finite hot + cold stream, work produced (`finite_stream_engine`) | Waste-heat engines, CL 202 Base | Core |
| 3 | General steady control volume (`steady_control_volume`) | Turbines, compressors, nozzles, pumps | Core |
| 4 | Closed system (`closed_system`) | Piston-cylinder, sealed tank, multi-reservoir cycles | Core |
| 5 | Property changes only (`property_change`) | Δh, Δs, T_eff, phase-change temperature | Utility |
| 6 | Unsupported / future domain (`unsupported_domain`) | Refrigerators, heat pumps, tidal, transient | Fallback |
| 7 | Heat exchanger, no shaft work (`finite_stream_heat_recovery`) | Heat recovery, data-centre coolant | Core |
| 8 | Mechanical energy harvesting (`mechanical_power`) | Walking, pedalling, wind (Betz), hydro (ρgQH) | Extension |
| 9 | Solar radiation conversion (`solar_blackbody`) | Solar converters (Petela–Landsberg) | Extension |
| 10 | Electrochemical conversion (`reaction_gibbs`) | Fuel cells (−ΔG × rate) | Extension |

The website's technology examples and the plain-English analyzer are a **mapping layer**: they decide which numbered model applies, and every result is labelled "Model N · …". The terminal menu offers the same ten models (`python thermodynamic_feasibility_tester_MASTER.py`), and `--self-test` runs 27 checks.

Changes in this revision:
- Model 3 now calculates a reversible shaft-work ceiling from steady-flow physical exergy when every stream has dead-state h0/s0 and an environment temperature T0.
- Model 4 now calculates a reversible useful-work ceiling from non-flow exergy when T0, environment pressure p0 and ΔV are supplied.
- Model 8 now requires measured heat rejection to close the converter First Law and requires its boundary temperature whenever positive heat is rejected, so it can calculate S_gen rather than incorrectly treating all unharvested resource as converter heat loss.
- Model 9 now reports an energy balance, entropy generation and exergy destruction using an explicit ambient-heat-rejection assumption.
- Model 10 now requires reaction enthalpy and operating/heat-boundary data to calculate the reaction energy and entropy balances in addition to the Gibbs work limit.
- Models share an auditable input-completion layer; supplied measurements are preserved.
- Model 1: claimed work above heat input is a NOT FEASIBLE verdict instead of an input error.
- Model 2: `apply_strict_reversible_limit` (enabled by default) gives the exact maximum work (cold outlet chosen so that S_gen = 0). The T_eff ratio stays informational.
- Model 7: optional `energy_relative_tolerance` for rounded textbook data, and a temperature-cross check.

## Automatic analysis from a plain-English description

Paste a problem as it would appear in a textbook and click **Analyze scenario**. `auto_analyzer.py` then runs five steps:

1. **Scan.** It finds every number with a unit and converts it to engine units. It handles K, °C, °F, kW, MW, kJ/min, Btu/h, kJ per cycle, kg/s, L/min, kJ/(kg K), W/m², m/s, mol/s, g/s and steps/min.
2. **Assign roles.** It reads the words around each quantity and decides what the quantity is. Examples: heat input, rejected heat, claimed work, source or sink temperature, stream inlet or outlet, wind speed, head, ΔG.
3. **Map source and sink.** It groups hot and cold streams and separates them from fixed-temperature reservoirs. Then it chooses the domain: thermal, mechanical, wind, hydro, solar or chemical.
4. **Infer.** Repeatedly apply uniquely solvable conservation and constitutive equations. Each derived field includes its equation and dependency values. Missing physical properties, densities and boundary temperatures are requested rather than guessed. Explicit efficiency-only normalization bases are labelled; they are not predictions of absolute output.
5. **Solve.** It builds the standard engine case and runs the original `run_case`.

The result shows the following:
- Each extracted value next to the phrase it came from.
- The source and sink mapping.
- Every calculated value and assumption.

If something essential is missing or ambiguous, for example a claimed output given as a range, the site lists exactly what to add instead of guessing. If the automatic analyzer identifies a supported model but the thermodynamic solver still needs a reference state or balance term, the website opens the corresponding model form and pre-fills the quantities it safely extracted.

The thermal mappings work as follows:

| Description contains | Model used |
|---|---|
| Two fixed temperatures, heat and work | Carnot limit, First Law, S_gen = Q_C/T_C − Q_H/T_H |
| A hot stream (inlet and outlet) and a sink temperature | T_H,eff = \|Δh\|/\|Δs\|, then the Carnot limit. This is exact: W_max = ΔH − T₀ΔS |
| A hot stream and a cold stream | Finite-stream engine: S_gen = ṁ_hΔs_h + ṁ_cΔs_c and the exact reversible cold-outlet limit |
| Cp(T) = a + bT, or latent heat with entropy of fusion | Linear-Cp integrals; phase-change sink T = Δh/Δs |
| One reservoir only ("converts it entirely into work") | Cycle entropy balance, which gives S_gen < 0 (Kelvin–Planck) |

The other domains use these limits:

| Domain | Limit |
|---|---|
| Wind | Betz limit (16/27)·½ρAv³ |
| Hydro | ρgQH |
| Solar | Petela–Landsberg |
| Fuel cells | −ΔG·ṅ, with ΔG supplied at the stated conditions |
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

Automatic local extraction supports explicitly stated quantities for walking generators, fixed-reservoir engines, constant-heat-capacity hot and cold streams, ideal blackbody radiation, signed Gibbs-energy reactions, and the named CL 202 assignments. For other phrasing or models, use the numeric forms. Values are never filled from unrelated worked-example defaults.

Expand **View calculation code** beneath a result to show the actual Python calculation functions and verdict logic. Download the full calculation engine, parser, or assignment code from that panel. The **Engine input JSON** panel lets your professor reproduce the same case directly with `python thermodynamic_feasibility_tester_MASTER.py --input case.json`.

For walking, enter mechanical input and claimed electrical output in watts. Alternatively, choose the energy-per-step model and enter joules per step, steps per minute, and electrical output in watts. For the CL 202 coffee assignments, choose the Base or Modified example and enter your roll-number suffix.

The walking model always checks the input energy limit and reports the power left after electrical output. For a steady generator that rejects all remaining power as heat, supply the optional heat-rejection temperature to compute entropy generation. Supplying measured heat loss adds an independent First Law balance. Inferred heat loss is labeled in the result.

The website runs entirely on your computer using the original Python engine. All website assets are local. The website serves fresh assets on each page load.

Run the two mock assignments with Python 3.9 or newer. No packages need to be installed.

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

Every power claim uses `W_out_kw`. The routes below calculate a model-specific ceiling and the applicable balance checks. If a required boundary condition or reference state is missing, the engine returns INSUFFICIENT DATA rather than a premature feasibility verdict.

| Route | Other JSON inputs | Scope |
|---|---|---|
| `steady_control_volume` | Existing stream/heat data, plus `T0_K` and per-stream `specific_enthalpy_dead_state_kj_kg`, `specific_entropy_dead_state_kj_kg_k` | Flow exergy and reversible shaft-work limit. Dead-state properties must refer to the same stream material/composition; chemical and mixing exergy are not included. |
| `closed_system` | Existing `delta_E_kj`, `delta_S_system_kj_K`, `heat`, `W_out_kj`, plus `T0_K`, `environment_pressure_kPa`, `delta_V_m3` | Non-flow exergy and reversible useful-work limit. Useful work subtracts displacement work against the environment. |
| `mechanical_power` | `recoverable_mechanical_power_kw` **or** `recoverable_energy_per_event_j` and `events_per_s`; measured `heat_rejected_kw`; and `heat_rejection_temperature_K` if positive heat is rejected | Complete lumped First/Second Law checks. The unharvested resource remainder is not assumed to be converter heat loss. |
| `solar_blackbody` | `T_source_K`, `T_ambient_K`, `irradiance_W_m2`, `collection_area_m2`, `W_out_kw` | Ideal Petela–Landsberg radiative exergy bound, First Law and exergy-derived entropy generation. All unconverted incident energy is assumed rejected to ambient. |
| `reaction_gibbs` | `reaction_direction`, `delta_G_kj_per_mol_rxn`, `delta_H_kj_per_mol_rxn`, `reaction_rate_mol_rxn_s`, `reaction_temperature_K`, `heat_into_reactor_kw`, `heat_boundary_temperature_K`, `W_out_kw` | Reaction energy and entropy balances plus the Gibbs non-expansion-work ceiling. The simplified model assumes heat crosses at the reaction temperature. |

Missing model inputs return **INSUFFICIENT DATA / MODEL REQUIRED** and name the missing quantities. For scenarios outside these routes, use `unsupported_domain`. Examples include wind without a supplied recoverable work limit, solar radiation needing a diffuse or spectral model, and chemical reactions without a Gibbs energy model.

The website's manual Model 3 form is a one-inlet/one-outlet convenience form; the Python engine and JSON interface accept multiple streams. The manual Model 4 form exposes one heat interaction; the JSON interface accepts multiple heat interactions. For multi-stream/multi-heat cases, use structured JSON or extend the form before relying on it.

The [implementation flowchart](FLOWCHART.md) shows the balance checks, derivations, and verdict paths. The ideal radiation and signed cell-work relations follow [Petela's radiation exergy analysis](https://www.sciencedirect.com/science/article/pii/S0038092X03002263) and the [IUPAC definition of standard electromotive force](https://goldbook.iupac.org/terms/view/S05913).

## Step 3 plain-language input

Start the offline terminal demo:

```powershell
python scenario_input.py "Can I generate electricity by walking?" --interactive
```

When asked for details, enter `Recoverable energy is 4 J per step, at 120 steps per minute. Electrical output is 5 W. It rejects 3 W of heat at 25 °C.` The tester quotes the quantities it found, shows the normalized inputs, checks the 8 W mechanical input against the 5 W output and 3 W measured heat loss, and calculates entropy generation from the heat rejection. The step energy is illustrative. For a single scripted run, use `--reply` followed by the additional details. Add `--output-json walking_case.json` to save the case for the original tester.

Use `--json` with a scenario argument for a machine-readable response. The response contains the selected route, original quotes, follow-up questions, normalized engine cases, and the result. If input is missing, it returns `needs_input` and leaves `cases` empty. Use repeated `--reply` options to supply additional details. The same interface is available in Python through `analyze_scenario(text, replies=...).as_dict()`.

```powershell
python scenario_input.py --json "Generating electricity by walking" --reply "Recoverable energy is 4 J per step at 120 steps per minute. Electrical output is 5 W. It rejects 3 W of heat at 25 °C."
```

The offline parser recognizes the walking demo and explicit CL 202 Assignment 1 Base or Modified requests with a suffix, such as `Run CL 202 Assignment 1 Modified for suffix 100.` If it cannot interpret a phrase, label the quantities explicitly or use the original structured inputs.

The website uses numeric forms after suggesting a model. The command-line text parser above remains available for labeled demo inputs.

## Step 4 manuscript and benchmarks

The [Word manuscript](CL202_Thermodynamic_Feasibility_Manuscript.docx) includes the assumptions, coffee-case results, and independent or published comparisons for all four model domains. Reproduce its numerical comparison table with:

```powershell
python validate_benchmarks.py
```

The differences are computed as program value minus reference value. The mechanical row compares a calculated ideal ceiling with a measured output. Its difference is the margin below the ceiling.
