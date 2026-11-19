const $ = (id) => document.getElementById(id);
const number = new Intl.NumberFormat(undefined, { maximumSignificantDigits: 5 });
const models = {
  mechanical_power: {
    number: 8, group: "extension",
    name: "Mechanical harvesting — mechanical power available",
    fields: [["recoverable_mechanical_power_kw", "Mechanical power available (W)", 8, 0, undefined, .001],
      ["W_out_kw", "Claimed electrical output (W)", 5, 0, undefined, .001],
      ["heat_rejected_kw", "Measured heat loss (W; required for First Law)", "", 0, undefined, .001],
      ["heat_rejection_temperature_K", "Heat rejection temperature (°C; required if heat is rejected)", "", -273.149, undefined, 1, 273.15]],
    note: "Provide measured heat rejection to close the First Law; the difference between available resource and electrical output is not automatically treated as converter heat loss. If positive heat is rejected, also provide its boundary temperature for S_gen = Q_rejected/T_boundary.",
  },
  mechanical_steps: {
    number: 8, group: "extension",
    name: "Mechanical harvesting — energy per step and walking rate",
    engineModel: "mechanical_power",
    fields: [["recoverable_energy_per_event_j", "Energy recovered per step (J)", 4, 0],
      ["events_per_s", "Walking rate (steps/min)", 120, 0, undefined, 1/60],
      ["W_out_kw", "Claimed electrical output (W)", 5, 0, undefined, .001],
      ["heat_rejected_kw", "Measured heat loss (W; required for First Law)", "", 0, undefined, .001],
      ["heat_rejection_temperature_K", "Heat rejection temperature (°C; required if heat is rejected)", "", -273.149, undefined, 1, 273.15]],
    note: "The input ceiling is recovered energy per step multiplied by steps per second. Provide measured converter heat loss to close the First Law, and its boundary temperature if positive, for the entropy balance.",
  },
  finite_stream_engine: {
    number: 2, group: "core",
    name: "Waste-heat engine — hot and cold streams with power output",
    fields: [
      ["hot_stream.mass_flow_kg_s", "Hot-stream flow (kg/s)", .3, .000001],
      ["hot_stream.T_in_K", "Hot-stream inlet (°C)", 57.746972594, -273.149, undefined, 1, 273.15],
      ["hot_stream.T_out_K", "Hot-stream outlet (°C)", 45, -273.149, undefined, 1, 273.15],
      ["hot_stream.cp_kj_kg_k", "Hot-stream heat capacity (kJ/kg K)", 4.184, .000001],
      ["cold_stream.mass_flow_kg_s", "Cold-stream flow (kg/s)", .6, .000001],
      ["cold_stream.T_in_K", "Cold-stream inlet (°C)", 25, -273.149, undefined, 1, 273.15],
      ["cold_stream.T_out_K", "Cold-stream outlet (°C)", 30.975201983, -273.149, undefined, 1, 273.15],
      ["cold_stream.cp_kj_kg_k", "Cold-stream heat capacity (kJ/kg K)", 4.184, .000001],
      ["W_out_kw", "Claimed electrical output (kW)", .999852941176, 0],
    ],
    note: "Constant heat capacities, steady adiabatic boundary around both streams and the device. The verdict uses the exact reversible limit (cold outlet chosen so that S_gen = 0). The effective-temperature ratio is shown for information only.",
  },
  fixed_reservoir_engine: {
    number: 1, group: "core",
    name: "Heat engine — fixed hot and cold temperatures",
    fields: [["T_hot_K", "Hot temperature (K)", 600, .001], ["T_cold_K", "Cold temperature (K)", 300, .001],
      ["Q_hot_kw", "Heat supplied (kW)", 100, .001], ["W_out_kw", "Claimed power (kW; leave blank to derive)", 30, 0],
      ["Q_cold_rejected_kw", "Rejected heat (kW; optional if heat and work are known)", "", 0],
      ["efficiency_fraction", "Efficiency (fraction, optional)", "", 0, 1]],
    note: "The Carnot ceiling uses absolute temperatures. Rejected heat is inferred from the First Law.",
  },
  assignment_base: {
    group: "assignment",
    name: "CL 202 Base — coffee from GPU waste heat (uses Model 2)",
    fields: [["suffix", "Roll-number suffix", 100, 0, 399]],
    note: "Computes both stream temperatures and the strict reversible ceiling for the coffee maker.",
  },
  assignment_modified: {
    group: "assignment",
    name: "CL 202 Modified — ORC and wax sink (uses Models 5 → 1)",
    fields: [["suffix", "Roll-number suffix", 100, 1, 400]],
    note: "Uses the gas heat-capacity model and an isothermal melting-wax sink.",
  },
  solar_blackbody: {
    number: 9, group: "extension",
    name: "Solar radiation conversion — ideal radiation limit",
    fields: [["T_source_K", "Source temperature (K)", 6000, .001], ["T_ambient_K", "Ambient temperature (K)", 300, .001],
      ["irradiance_W_m2", "Irradiance (W/m²)", 1000, 0], ["collection_area_m2", "Collection area (m²)", 2, .0001],
      ["W_out_kw", "Claimed power (kW)", 1.8, 0]],
    note: "Uses the Petela–Landsberg radiation exergy limit. For this lumped model, all incident energy not converted to electricity is rejected to ambient; the First Law and S_gen = (Exergy_in − W_out)/T0 are both checked.",
  },
  reaction_gibbs: {
    number: 10, group: "extension",
    name: "Electrochemical conversion — fuel cell (Gibbs energy)",
    fields: [["reaction_direction", "Reaction direction", "H₂ + ½O₂ → H₂O(l)"],
      ["delta_G_kj_per_mol_rxn", "ΔG (kJ/mol reaction)", -237.13],
      ["delta_H_kj_per_mol_rxn", "ΔH (kJ/mol reaction)", -285.83],
      ["reaction_rate_mol_rxn_s", "Forward rate (mol/s)", .01, .000001],
      ["reaction_temperature_K", "Reaction temperature (K)", 298.15, .001],
      ["W_out_kw", "Claimed electrical power (kW)", 1.5, 0],
      ["heat_into_reactor_kw", "Heat into reactor (kW; negative = heat rejected)", -1.3583],
      ["heat_boundary_temperature_K", "Heat boundary temperature (K)", 298.15, .001]],
    note: "Requires signed ΔG and ΔH, reaction rate and temperature, plus the measured heat interaction. The heat-boundary temperature must equal the reaction temperature in this simplified isothermal model. Checks the reaction energy balance, reaction entropy generation and the −ΔG reversible electrical-work ceiling.",
  },
  steady_control_volume: {
    number: 3, group: "core", review: false,
    name: "Steady control volume — turbine, compressor, nozzle (one inlet, one outlet)",
    fields: [["cv_m", "Mass flow rate (kg/s)", 1, .000001],
      ["cv_h_in", "Inlet enthalpy h (kJ/kg)", 3231.7], ["cv_s_in", "Inlet entropy s (kJ/kg K)", 6.9235],
      ["cv_h_out", "Outlet enthalpy h (kJ/kg)", 2675.0], ["cv_s_out", "Outlet entropy s (kJ/kg K)", 7.3589],
      ["cv_q", "Heat into the device (kW; enter 0 for adiabatic)", ""],
      ["cv_tb", "Boundary temperature of that heat (K, optional)", "", .001],
      ["cv_T0", "Environment/dead-state temperature T0 (K)", 298.15, .001],
      ["cv_h0", "Dead-state enthalpy h0 for this fluid (kJ/kg)", 104.9],
      ["cv_s0", "Dead-state entropy s0 for this fluid (kJ/kg K)", .367],
      ["W_out_kw", "Claimed shaft work output (kW)", 556.7]],
    build: (c) => ({model: c.model, T0_K: c.cv_T0,
      inlets: [{mass_flow_kg_s: c.cv_m, specific_enthalpy_kj_kg: c.cv_h_in, specific_entropy_kj_kg_k: c.cv_s_in,
        specific_enthalpy_dead_state_kj_kg: c.cv_h0, specific_entropy_dead_state_kj_kg_k: c.cv_s0}],
      outlets: [{mass_flow_kg_s: c.cv_m, specific_enthalpy_kj_kg: c.cv_h_out, specific_entropy_kj_kg_k: c.cv_s_out,
        specific_enthalpy_dead_state_kj_kg: c.cv_h0, specific_entropy_dead_state_kj_kg_k: c.cv_s0}],
      heat: c.cv_q === undefined ? undefined : [{heat_into_cv_kw: c.cv_q, boundary_temperature_K: c.cv_tb}],
      W_out_kw: c.W_out_kw}),
    note: "Provide h and s from property tables plus the dead-state h0,s0 and environment T0. If heat crosses the boundary, also provide its boundary temperature. Calculates flow exergy ψ=(h−h0)−T0(s−s0), the reversible shaft-work ceiling and exergy destruction, in addition to mass, energy and entropy balances. Chemical/mixing exergy is not included. This compact form assumes one inlet and one outlet of the same fluid.",
  },
  closed_system: {
    number: 4, group: "core", review: false,
    name: "Closed system — piston-cylinder, sealed tank, or a full cycle",
    fields: [["delta_E_kj", "Change in system energy ΔE (kJ)", 10], ["delta_S_system_kj_K", "Change in system entropy ΔS (kJ/K)", .06],
      ["cs_q", "Heat into the system (kJ; negative = heat out)", 15], ["cs_tb", "Boundary temperature of that heat (K)", 300, .001],
      ["cs_T0", "Environment temperature T0 (K)", 300, .001], ["cs_p0", "Environment pressure p0 (kPa)", 101.325, .001],
      ["cs_dV", "System volume change ΔV (m³)", 0],
      ["W_out_kj", "Claimed total work output (kJ)", 5]],
    build: (c) => ({model: c.model, delta_E_kj: c.delta_E_kj, delta_S_system_kj_K: c.delta_S_system_kj_K,
      T0_K: c.cs_T0, environment_pressure_kPa: c.cs_p0, delta_V_m3: c.cs_dV,
      heat: [{heat_into_system_kj: c.cs_q, boundary_temperature_K: c.cs_tb}], W_out_kj: c.W_out_kj}),
    note: "Adds the non-flow exergy balance B=E+p0V−T0S. The useful-work ceiling is −ΔB+ΣQ(1−T0/Tb); useful work excludes p0ΔV. Enter the reference environment and volume change.",
  },
  property_change: {
    number: 5, group: "core",
    name: "Property changes only — Δh, Δs, effective temperature",
    fields: [["T1_K", "Initial temperature (K)", 300, .001], ["T2_K", "Final temperature (K)", 350, .001],
      ["cp_kj_kg_k", "Heat capacity Cp (kJ/kg K)", 4.18, .000001],
      ["phase_delta_h_kj_kg", "Phase-change Δh (kJ/kg, optional)", ""], ["phase_delta_s_kj_kg_k", "Phase-change Δs (kJ/kg K, optional)", ""]],
    build: (c) => ({...c, cp_model: "constant"}),
    note: "A calculation utility, not a feasibility verdict. T_eff = |Δh|/|Δs| (log-mean temperature for constant Cp).",
  },
  unsupported_domain: {
    number: 6, group: "core",
    name: "Unsupported domain — no validated model yet",
    fields: [["domain", "Technology or domain", "tidal power"], ["reason", "What model or data is missing?", "A validated tidal-energy limit has not been implemented."]],
    note: "Fallback: returns “Model required” instead of forcing a Carnot temperature onto a non-thermal process.",
  },
  finite_stream_heat_recovery: {
    number: 7, group: "core",
    name: "Heat exchanger — hot stream heats a cold stream, no shaft work",
    fields: [
      ["hot_stream.mass_flow_kg_s", "Hot-stream flow (kg/s)", .5, .000001],
      ["hot_stream.T_in_K", "Hot-stream inlet (°C)", 80, -273.149, undefined, 1, 273.15],
      ["hot_stream.T_out_K", "Hot-stream outlet (°C)", 45, -273.149, undefined, 1, 273.15],
      ["hot_stream.cp_kj_kg_k", "Hot-stream heat capacity (kJ/kg K)", 4.18, .000001],
      ["cold_stream.mass_flow_kg_s", "Cold-stream flow (kg/s)", .5, .000001],
      ["cold_stream.T_in_K", "Cold-stream inlet (°C)", 25, -273.149, undefined, 1, 273.15],
      ["cold_stream.T_out_K", "Cold-stream outlet (°C)", 60, -273.149, undefined, 1, 273.15],
      ["cold_stream.cp_kj_kg_k", "Cold-stream heat capacity (kJ/kg K)", 4.18, .000001],
      ["T0_K", "Environment temperature T0 (°C)", 25, -273.149, undefined, 1, 273.15]],
    build: (c) => ({...c, flow_arrangement: "counterflow"}),
    note: "Counterflow exchanger: checks strict energy closure, S_gen ≥ 0 and both terminal temperature differences. Reports exergy destroyed = T0·S_gen.",
  },
};
const examples = [
  ["MODEL 1 · THERMAL", "Heat engine", "Compare a power claim with the Carnot ceiling.", "fixed_reservoir_engine"],
  ["MODEL 7 · HEAT RECOVERY", "Data-centre heat exchanger", "Server coolant preheats process water; exergy destroyed.", "finite_stream_heat_recovery"],
  ["MODEL 3 · OPEN SYSTEM", "Steam turbine", "Mass, energy and entropy balances from table values.", "steady_control_volume"],
  ["MODEL 2 · CL 202", "GPU waste heat", "Base assignment with two finite water streams.", "assignment_base"],
  ["MODELS 5 → 1 · CL 202", "ORC and wax sink", "Modified assignment with gas cooling and wax melting.", "assignment_modified"],
  ["MODEL 8 · MECHANICAL", "Walking harvester", "Recoverable energy per step and electrical output.", "mechanical_steps"],
  ["MODEL 9 · RADIATION", "Solar converter", "Find the upper limit for ideal blackbody radiation.", "solar_blackbody"],
  ["MODEL 10 · CHEMICAL", "Hydrogen fuel cell", "Compare output with the signed Gibbs energy limit.", "reaction_gibbs"],
];
const engineLabels = {
  claimed_electrical_output_kw: "Claimed electrical power (kW)", reversible_power_limit_kw: "Ideal power limit (kW)",
  reversible_work_limit_kw: "Reversible work limit (kW)", S_gen_dot_kw_per_k: "Entropy generation (kW/K)",
  petela_landsberg_exergy_fraction: "Radiation exergy fraction", incident_radiation_kw: "Incident radiation (kW)",
  first_law: "First Law", second_law: "Second Law", process_classification: "Process classification",
  performance_limit: "Useful power limit", first_law_balance: "Energy accounting",
  entropy_balance: "Entropy accounting", input_energy_limit: "Input energy limit",
  heat_rejection_basis: "Heat-loss data", reversible_shaft_work_limit_kw: "Reversible shaft-work limit (kW)",
  reversible_useful_work_limit_kj: "Reversible useful-work limit (kJ)", useful_work_out_kj: "Useful work output (kJ)",
  exergy_destruction_kw: "Exergy destruction (kW)", exergy_destruction_kj: "Exergy destruction (kJ)",
  radiation_exergy_input_kw: "Radiation exergy input (kW)", heat_rejected_to_ambient_kw: "Heat rejected to ambient (kW)",
  reaction_entropy_change_kj_per_mol_k: "Reaction entropy change (kJ/mol K)", reaction_enthalpy_rate_kw: "Reaction enthalpy rate (kW)",
  first_law_residual_kw: "First Law residual (kW)",
  strict_reversible_work_limit_kw: "Exact reversible work limit (kW)", exergy_destroyed_kw: "Exergy destroyed (kW)",
  terminal_temperature_check: "Temperature-cross check", model_number: "Model",
  heat_released_by_hot_stream_kw: "Heat released by hot stream (kW)", heat_absorbed_by_cold_stream_kw: "Heat absorbed by cold stream (kW)",
  energy_balance_residual_kw: "Energy mismatch (kW)",
};

let revision = 0;
const fromFile = location.protocol === "file:";
const hosted = location.hostname?.endsWith(".github.io") === true;
let browserWorker;
let browserRequest = 0;

function element(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
}

function makeFields(blank = false) {
  const model = models[$("model").value];
  $("fields").replaceChildren(...model.fields.map(([key, label, value, min, max]) => {
    const group = element("label", label);
    const input = element("input");
    input.name = key;
    input.value = blank ? "" : value;
    input.placeholder = String(value);
    const optional = value === "";
    input.required = key === "suffix";
    if (typeof value === "number" || optional) {
      input.type = "number";
      input.step = key === "suffix" ? "1" : "any";
      if (min !== undefined) input.min = min;
      if (max !== undefined) input.max = max;
    } else input.maxLength = 300;
    group.append(input);
    return group;
  }));
  $("model-note").textContent = model.note;
}

function setStatus(label, type = "") {
  $("status").textContent = label;
  $("status").className = "badge " + type;
}

function clearOutput() {
  revision++;
  setStatus("Ready");
  const empty = element("div", undefined, "empty");
  empty.append(element("span", "◎", "empty-icon"), element("h3", "Your results appear here"),
    element("p", "Run a scenario to compare claimed output with the ideal limit and see which checks apply."));
  $("result").replaceChildren(empty);
}

function setMode(isText, preserveResult = false) {
  $("text-form").hidden = !isText;
  $("model-form").hidden = isText;
  $("text-tab").setAttribute("aria-pressed", isText);
  $("model-tab").setAttribute("aria-pressed", !isText);
  if (!preserveResult) clearOutput();
}

function fillValues(route, values) {
  if (!models[route]) return;
  $("model").value = route;
  makeFields(true);
  for (const [key, , , , , scale = 1, offset = 0] of models[route].fields) {
    const value = key in values ? values[key] : key.split(".").reduce((item, part) => item?.[part], values);
    if (value === undefined || value === null) continue;
    const input = [...$("fields").querySelectorAll("input")].find((item) => item.name === key);
    if (input) input.value = typeof value === "number" ? Number(((value - offset) / scale).toPrecision(12)) : value;
  }
}

async function runEngine(request) {
  if (fromFile) throw new Error("Start the site with python website/serve.py, then open http://127.0.0.1:8000.");
  if (hosted) {
    browserWorker ??= new Worker("engine-worker.js?v=evidence-20261118");
    const worker = browserWorker;
    const id = ++browserRequest;
    return new Promise((resolve, reject) => {
      const finish = (action, value) => {
        clearTimeout(timer);
        worker.removeEventListener("message", receive);
        worker.removeEventListener("error", failed);
        action(value);
      };
      const receive = ({data}) => { if (data.id === id) finish(resolve, data.result); };
      const failed = () => {
        worker.terminate();
        if (browserWorker === worker) browserWorker = null;
        finish(reject, new Error("The browser Python engine did not start. Check your connection and retry."));
      };
      const timer = setTimeout(failed, 90000);
      worker.addEventListener("message", receive);
      worker.addEventListener("error", failed);
      worker.postMessage({id, request});
    });
  }
  const response = await fetch("./api/solve", {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(request), signal: AbortSignal.timeout(10000)});
  if (!response.headers.get("content-type")?.includes("application/json")) {
    throw new Error("Open the site using python website/serve.py. This page needs the local calculation server.");
  }
  return response.json();
}

function outputValues(result, cases) {
  const calc = result.calculations || result;
  const input = Object.values(cases).at(-1);
  const claimed = calc["Claimed electrical output (kW)"] ?? calc.claimed_electrical_output_kw ?? result.inputs?.W_out_kw ?? input?.W_out_kw;
  const limit = calc["Strict reversible power ceiling (kW)"] ?? calc["Reversible power ceiling (kW)"]
    ?? calc.reversible_power_limit_kw ?? calc.reversible_work_limit_kw ?? calc.strict_reversible_work_limit_kw;
  return { calc, claimed, limit };
}

function pretty(value) {
  if (typeof value === "number") return number.format(value);
  if (value === null || value === undefined) return "Unavailable";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function details(title, body) {
  const section = element("details");
  section.append(element("summary", title), body);
  return section;
}

function dataList(data) {
  const list = element("dl");
  for (const [name, value] of Object.entries(data)) {
    list.append(element("dt", engineLabels[name] || name.replaceAll("_", " ")), element("dd", pretty(value)));
  }
  return list;
}

const domainNames = {
  thermal: "Thermal — heat flows from a source to a sink", mechanical: "Mechanical — recoverable work",
  wind: "Wind — kinetic energy (Betz limit)", hydro: "Hydro — falling water (ρgQH limit)",
  solar: "Solar — radiation (Petela–Landsberg limit)", chemical: "Chemical — Gibbs energy limit",
  work_consuming: "Refrigerator / heat pump (consumes work)", no_input: "No energy input (perpetual-motion claim)",
  unknown: "Not recognised yet",
};

function listBlock(title, items, className) {
  const box = element("div", undefined, "interp-block " + (className || ""));
  box.append(element("h4", title));
  const list = element("ul");
  for (const item of items) list.append(element("li", item));
  box.append(list);
  return box;
}

function interpretation(analysis, open = true) {
  const body = element("div", undefined, "interp");
  const head = element("p", undefined, "interp-model");
  head.append(element("strong", analysis.model_label || domainNames[analysis.domain] || analysis.domain));
  if (analysis.title && !analysis.model_label) head.append(element("span", " · " + analysis.title));
  body.append(head);
  if (analysis.extracted?.length) {
    const table = element("table", undefined, "extracted");
    const headRow = element("tr");
    for (const label of ["Quantity", "Value used", "Found in text"]) headRow.append(element("th", label));
    table.append(headRow);
    for (const item of analysis.extracted) {
      const row = element("tr");
      const value = typeof item.value === "number" ? number.format(item.value) + (item.unit ? " " + item.unit : "") : String(item.value);
      row.append(element("td", item.quantity), element("td", value), element("td", "“" + item.quote + "”"));
      table.append(row);
    }
    const box = element("div", undefined, "interp-block");
    box.append(element("h4", "1 · Extracted from your description"), table);
    body.append(box);
  }
  if (analysis.mapping?.length) body.append(listBlock("2 · Source, sink and test selected", analysis.mapping));
  if (analysis.inferred?.length) body.append(listBlock("3 · Calculated from the extracted values", analysis.inferred));
  if (analysis.assumptions?.length) body.append(listBlock("Assumptions made (check these)", analysis.assumptions, "assume"));
  if (analysis.issues?.length) body.append(listBlock("Notes", analysis.issues, "assume"));
  const section = details("How the scenario was interpreted", body);
  section.open = open;
  section.className = "interp-details";
  return section;
}

function render(data) {
  const output = $("result");
  output.replaceChildren();
  if (data.error) {
    setStatus("Check input", "fail");
    const message = element("div", undefined, "verdict");
    message.append(element("h3", "Could not run the calculation"), element("p", data.error));
    output.append(message);
    if (data.analysis) output.append(interpretation(data.analysis));
    return;
  }
  if (data.status === "needs_values" && data.analysis && !data.result) {
    setStatus("Needs info", "wait");
    const message = element("div", undefined, "verdict");
    message.append(element("h3", "Almost there. Add the missing information"),
      element("p", "Add these details to your description in plain words, then click Analyze scenario again."));
    output.append(message);
    output.append(listBlock("Still needed", data.analysis.missing || [], "missing"));
    output.append(interpretation(data.analysis));
    const route = data.route;
    if (models[route]) {
      const manual = element("button", "Or enter the values in the form", "quiet");
      manual.type = "button";
      manual.addEventListener("click", () => {
        setMode(false, true);
        $("model").value = route;
        makeFields(true);
        fillValues(route, data.prefill || {});
      });
      output.append(manual);
    }
    $("scenario").focus({preventScroll: true});
    return;
  }
  if (data.status === "needs_values" && !data.result) {
    const route = data.route;
    setMode(false);
    if (models[route]) $("model").value = route;
    makeFields(true);
    fillValues(route, data.prefill || {});
    setStatus("Enter values", "wait");
    const message = element("div", undefined, "verdict");
    message.append(element("h3", "Enter the numbers for your scenario"),
      element("p", route ? "Suggested model: " + models[route].name + ". Review the model, fill in the labeled fields, and click Calculate result." : "Choose the model that matches your process, then enter its values."));
    output.replaceChildren(message);
    if (data.issues?.length) output.append(element("p", data.issues.join(" "), "form-note"));
    $("model").focus({preventScroll: true});
    return;
  }
  const { result, cases } = data;
  if (Object.keys(data.source_quotes || {}).length) {
    const populated = Object.values(cases).at(-1);
    let route = data.route;
    if (route === "mechanical_power" && populated?.events_per_s !== undefined) route = "mechanical_steps";
    if (data.route.startsWith("assignment_")) {
      fillValues(route, {suffix: Number(data.source_quotes.suffix)});
    } else fillValues(route, populated);
  }
  const code = result.verdict?.status || result.Verdict;
  const admissible = code === "SECOND-LAW ADMISSIBLE / NOT REJECTED";
  const feasible = code === "FEASIBLE / NOT REJECTED" || admissible;
  const rejected = typeof code === "string" && code.startsWith("NOT FEASIBLE");
  setStatus(admissible ? "Admissible" : feasible ? "Feasible" : rejected ? "Not feasible" : code === "PROPERTY CALCULATION ONLY" ? "Properties only" : "Model required",
    feasible ? "pass" : rejected ? "fail" : "wait");
  const verdict = element("div", undefined, "verdict");
  if (data.model_label) verdict.append(element("p", data.model_label, "model-tag"));
  verdict.append(element("h3", result.title || models[data.route]?.name || data.route.replaceAll("_", " ")));
  const reason = result.verdict?.reasons?.[0] || result.Reason;
  if (reason) verdict.append(element("p", reason));
  const more = (result.verdict?.reasons || []).slice(1);
  if (more.length) {
    const reasons = element("ul", undefined, "reasons");
    for (const line of more) reasons.append(element("li", line));
    verdict.append(reasons);
  }
  output.append(verdict);
  if (result.verdict?.scope_note) output.append(element("p", result.verdict.scope_note, "scope-note"));
  if (result.missing_inputs?.length) output.append(listBlock("Inputs that cannot yet be derived", result.missing_inputs, "missing"));
  if (data.analysis) output.append(interpretation(data.analysis));
  if (data.analysis?.missing?.length) output.append(listBlock("Still needed for a complete verdict", data.analysis.missing, "missing"));
  if (result.derivation?.length) {
    const section = element("section", undefined, "evidence-section");
    section.append(element("h4", "Input evidence · provided → calculated"),
      element("p", "Values retain their engine units (K, kW, kJ, kg/s). A balance used to calculate a missing input is not an independent measurement.", "form-note"));
    const table = element("table", undefined, "extracted evidence-table");
    const heading = element("tr");
    for (const label of ["Input", "Value", "Evidence"]) heading.append(element("th", label));
    table.append(heading);
    for (const item of result.derivation) {
      if (item.field.endsWith(".name") || item.field.endsWith("cp_model")) continue;
      const row = element("tr");
      const evidence = element("td");
      evidence.append(element("strong", item.source === "derived" ? "Calculated" : item.source === "mapped" ? "Interpreted from text" : "Provided"));
      if (item.equation) evidence.append(element("p", item.equation));
      if (Object.keys(item.dependencies || {}).length) evidence.append(element("small",
        Object.entries(item.dependencies).map(([key, value]) => key + " = " + pretty(value)).join("; ")));
      row.append(element("td", item.field.replaceAll("_", " ")), element("td", pretty(item.value)), evidence);
      table.append(row);
    }
    section.append(table);
    output.append(section);
  }
  if (Object.keys(data.source_quotes || {}).length && models[data.route] && models[data.route].review !== false) {
    const review = element("button", "Review automatically filled values", "quiet");
    review.type = "button";
    review.addEventListener("click", () => setMode(false, true));
    output.append(review);
  }
  const { calc, claimed, limit } = outputValues(result, cases);
  if (Number.isFinite(claimed) && Number.isFinite(limit)) {
    const watts = Math.max(Math.abs(claimed), Math.abs(limit)) < 1;
    const power = (value) => number.format(value * (watts ? 1000 : 1)) + (watts ? " W" : " kW");
    const metrics = element("div", undefined, "metrics");
    const derivedWork = result.derivation?.some((item) => item.field === "W_out_kw" && item.source === "derived");
    for (const [label, value] of [[derivedWork ? "Calculated output" : "Claimed output", claimed], ["Ideal ceiling", limit]]) {
      const card = element("div", label, "metric");
      card.append(element("strong", power(value)));
      metrics.append(card);
    }
    output.append(metrics);
    const chart = element("div", undefined, "comparison");
    for (const [label, value] of [["Ceiling", limit], ["Claim", claimed]]) {
      const row = element("div", undefined, "bar-row");
      const track = element("div", undefined, "track");
      const bar = element("i");
      bar.style.width = (100 * Math.max(0, value) / Math.max(claimed, limit, Number.MIN_VALUE)) + "%";
      track.append(bar);
      row.append(element("span", label), track, element("span", power(value)));
      chart.append(row);
    }
    output.append(chart);
  }
  if (result.checks) {
    const checks = details("Balance and limit checks", dataList(result.checks));
    checks.open = true;
    output.append(checks);
  }
  output.append(details("All calculated values", dataList(calc)));
  if (result.assumptions?.length) {
    const assumptions = element("ul");
    for (const assumption of result.assumptions) assumptions.append(element("li", assumption));
    output.append(details("Model assumptions", assumptions));
  }
  if (Object.keys(data.source_quotes || {}).length) output.append(details("What the tester extracted", dataList(data.source_quotes)));
  const jsonBody = element("div");
  const files = Object.entries(cases);
  jsonBody.append(element("pre", JSON.stringify(files.length === 1 ? files[0][1] : cases, null, 2)));
  for (const [name, content] of files) {
    const download = element("button", files.length === 1 ? "Download case JSON" : "Download " + name.replaceAll("_", " "), "quiet");
    download.type = "button";
    download.addEventListener("click", () => {
      const url = URL.createObjectURL(new Blob([JSON.stringify(content, null, 2)], {type: "application/json"}));
      const link = element("a");
      link.href = url;
      link.download = name + ".json";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
    jsonBody.append(download);
  }
  output.append(details("Engine input JSON", jsonBody));
  if (data.calculation_code?.length) {
    const codeBody = element("div");
    codeBody.append(element("p", "These functions come from the Python analyzer and engine. The auto_analyzer functions read and map the description. The engine functions calculate the verdict. The inputs are shown in Engine input JSON."));
    for (const item of data.calculation_code) {
      codeBody.append(element("h4", item.name), element("pre", item.source));
    }
    for (const [file, label] of [["analyzer", "Download automatic scenario analyzer"], ["engine", "Download full calculation engine"],
      ["parser", "Download local scenario parser"], ["assignments", "Download assignment calculations"]]) {
      const link = element("a", label, "quiet");
      const paths = {analyzer: "auto_analyzer.py", engine: "thermodynamic_feasibility_tester_MASTER.py",
        parser: "website/local_scenarios.py", assignments: "run_mock_assignments.py"};
      link.href = hosted ? "./python/" + paths[file] : "./api/source/" + file;
      link.download = "";
      codeBody.append(link);
    }
    output.append(details("View calculation code", codeBody));
  }
}

async function analyze(request) {
  const current = ++revision;
  const submits = document.querySelectorAll("form button[type=submit]");
  submits.forEach((button) => button.disabled = true);
  const originalLabels = [...submits].map((button) => button.innerHTML);
  submits.forEach((button) => button.textContent = "Analyzing…");
  setStatus("Calculating…", "wait");
  $("result").replaceChildren(element("p", hosted
    ? "Running Python in your browser. The first calculation downloads the engine and may take a moment."
    : "Running the thermodynamics engine…", "form-note"));
  try {
    const response = await runEngine(request);
    if (current === revision) render(response);
  } catch (error) {
    if (current === revision) {
      render({error: error.name === "TimeoutError" ? "The local server took too long to respond. Restart python website/serve.py and retry." : error.message});
    }
  } finally {
    submits.forEach((button, index) => { button.disabled = false; button.innerHTML = originalLabels[index]; });
  }
}

for (const [group, label] of [["core", "Core thermodynamic models (Models 1–7, same as the terminal program)"],
  ["extension", "Domain extensions (Models 8–10)"], ["assignment", "CL 202 assignments"]]) {
  const optgroup = element("optgroup");
  optgroup.label = label;
  const entries = Object.entries(models).filter(([, spec]) => spec.group === group)
    .sort(([, a], [, b]) => (a.number ?? 99) - (b.number ?? 99));
  for (const [id, spec] of entries) {
    const option = element("option", (spec.number ? `Model ${spec.number} · ` : "") + spec.name);
    option.value = id;
    optgroup.append(option);
  }
  $("model").append(optgroup);
}
if (!models[$("model").value]) $("model").value = "fixed_reservoir_engine";
makeFields(true);
function openModel(model) {
  setMode(false);
  $("model").value = model;
  makeFields();
  $("model").focus({ preventScroll: true });
  $("workbench").scrollIntoView();
}

for (const [kicker, title, description, model] of examples) {
  const card = element("button", undefined, "example");
  card.type = "button";
  card.append(element("span", kicker, "eyebrow"), element("strong", title), element("small", description));
  card.addEventListener("click", () => openModel(model));
  $("example-list").append(card);
}
const samples = [
  ["Inventor's engine", "An inventor claims his engine takes 100 kW of heat from a source at 600 K, rejects heat to surroundings at 300 K and produces 60 kW of work."],
  ["Waste-heat streams", "Exhaust gas at 2 kg/s enters at 500 °C and leaves at 150 °C, cp = 1.1 kJ/kg K. Cooling water at 5 kg/s enters at 20 °C. The engine claims 300 kW."],
  ["Flue-gas recovery", "Waste heat from flue gases at 3 kg/s cooling from 450 °C to 120 °C is used to generate 250 kW of electricity. The surroundings are at 25 °C."],
  ["Wind turbine", "A wind turbine with 40 m rotor diameter in 10 m/s wind claims 800 kW."],
  ["Fuel cell", "A methanol fuel cell uses 0.5 g/s of methanol and delivers 8 kW."],
  ["Walking shoe", "A shoe-mounted generator recovers 4 J per step at 120 steps per minute and claims to generate 5 W of electricity."],
];
if ($("samples")) {
  for (const [label, text] of samples) {
    const chip = element("button", label, "chip");
    chip.type = "button";
    chip.addEventListener("click", () => { $("scenario").value = text; analyze({scenario: text}); });
    $("samples").append(chip);
  }
}
$("text-tab").addEventListener("click", () => setMode(true));
$("model-tab").addEventListener("click", () => setMode(false));
$("model").addEventListener("change", () => { makeFields(true); clearOutput(); });
$("reset").addEventListener("click", () => { $("scenario").value = ""; clearOutput(); $("scenario").focus(); });
$("text-form").addEventListener("submit", (event) => {
  event.preventDefault();
  analyze({scenario: $("scenario").value.trim()});
});
$("model-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const model = $("model").value;
  const inputs = new FormData(event.target);
  const spec = models[model];
  const caseData = {model: spec.engineModel || model};
  for (const [key, , value, , , scale = 1, offset = 0] of spec.fields) {
    const raw = inputs.get(key);
    if (raw === null || String(raw).trim() === "") continue;
    const parsed = typeof value === "number" || value === "" ? Number(raw) * scale + offset : raw.trim();
    if (typeof parsed === "number" && !Number.isFinite(parsed)) {
      render({error: "Enter finite numbers for the supplied values."});
      return;
    }
    if (key.includes(".")) {
      const [group, field] = key.split(".");
      caseData[group] ??= {name: group.replace("_", " "), cp_model: "constant"};
      caseData[group][field] = parsed;
    } else caseData[key] = parsed;
  }
  if (model === "finite_stream_engine") {
    caseData.apply_effective_temperature_limit = true;
    caseData.apply_strict_reversible_limit = true;
  }
  analyze({case: spec.build ? spec.build(caseData) : caseData});
});
$("interface-loading").hidden = true;
$("analyze-button").disabled = false;
