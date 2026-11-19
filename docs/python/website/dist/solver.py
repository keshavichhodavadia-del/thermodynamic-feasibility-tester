"""Shared website interface to the original Python models."""

import json
import re
import inspect
import thermodynamic_feasibility_tester_MASTER as engine
import run_mock_assignments as assignments
import auto_analyzer
from website.local_scenarios import extract_case

from run_mock_assignments import base_assignment, modified_assignment
from thermodynamic_feasibility_tester_MASTER import run_case


def calculation_code(route):
    names = {
        "mechanical_power": ["analyse_mechanical_power", "balance", "resource_number", "positive_temperature",
                             "process_classification", "second_law", "entropy_generation_is_negative", "make_verdict"],
        "fixed_reservoir_engine": ["analyse_fixed_reservoir_engine", "carnot_efficiency",
                                   "fixed_reservoir_entropy_generation", "make_verdict"],
        "finite_stream_engine": ["analyse_finite_stream_engine", "stream_properties", "reversible_cold_outlet_temperature",
                                 "properties_constant_cp", "properties_linear_cp", "effective_temperature", "make_verdict"],
        "steady_control_volume": ["analyse_steady_control_volume", "steady_cv_mass_balance", "steady_cv_energy_balance",
                                  "steady_cv_entropy_generation", "positive_temperature", "process_classification",
                                  "second_law", "make_verdict"],
        "closed_system": ["analyse_closed_system", "closed_system_energy_balance", "closed_system_entropy_generation",
                          "positive_temperature", "process_classification", "second_law", "make_verdict"],
        "property_change": ["analyse_property_change", "properties_constant_cp", "properties_linear_cp",
                            "effective_temperature", "phase_change_temperature"],
        "unsupported_domain": ["analyse_unsupported_domain"],
        "finite_stream_heat_recovery": ["analyse_finite_stream_heat_recovery", "stream_properties",
                                        "properties_constant_cp", "properties_linear_cp"],
        "solar_blackbody": ["analyse_solar_blackbody", "balance", "resource_number", "positive_temperature",
                            "process_classification", "second_law", "entropy_generation_is_negative", "make_verdict"],
        "reaction_gibbs": ["analyse_reaction_gibbs", "balance", "resource_number", "positive_temperature",
                           "process_classification", "second_law", "entropy_generation_is_negative", "make_verdict"],
        "assignment_base": ["analyse_finite_stream_engine", "reversible_cold_outlet_temperature",
                            "properties_constant_cp", "make_verdict"],
        "assignment_modified": ["analyse_property_change", "properties_linear_cp", "phase_change_temperature",
                                "analyse_fixed_reservoir_engine", "make_verdict"],
    }
    code = [{"name": name, "source": inspect.getsource(getattr(engine, name))}
            for name in names.get(route, [])]
    if route.startswith("assignment_"):
        function = assignments.base_assignment if route == "assignment_base" else assignments.modified_assignment
        code.insert(0, {"name": function.__name__, "source": inspect.getsource(function)})
    return code


def analysis_code(analysis):
    """Source of the analyzer steps that produced this mapping (shown on the website)."""
    if not analysis:
        return []
    title, domain, model = analysis.get("title") or "", analysis.get("domain"), analysis.get("model")
    names = ["scan_quantities", "ScenarioAnalyzer.assign_energy_roles", "ScenarioAnalyzer.assign_temperature_roles"]
    if domain == "thermal":
        names.append("ScenarioAnalyzer.route_thermal")
        if model == "steady_control_volume":
            names.append("ScenarioAnalyzer.try_steady_cv")
        elif model == "closed_system" and title.startswith("Closed system"):
            names.append("ScenarioAnalyzer.try_closed_system")
        elif model == "property_change":
            names.append("ScenarioAnalyzer.try_property_request")
        elif model == "finite_stream_engine":
            names.append("ScenarioAnalyzer.thermal_two_streams")
        elif model == "finite_stream_heat_recovery":
            names += ["ScenarioAnalyzer.thermal_two_streams", "ScenarioAnalyzer._exchanger_case"]
        elif "stream" in title.lower():
            names.append("ScenarioAnalyzer.thermal_stream_reservoir")
        elif "single reservoir" in title.lower():
            names.append("ScenarioAnalyzer._kelvin_planck")
        elif model == "closed_system":
            names.append("ScenarioAnalyzer._cycle_entropy")
        else:
            names.append("ScenarioAnalyzer.thermal_reservoirs")
    else:
        route = {"mechanical": "route_mechanical", "wind": "route_wind", "hydro": "route_hydro",
                 "solar": "route_solar", "chemical": "route_chemical", "no_input": "route_no_input",
                 "work_consuming": "route_work_consuming"}.get(domain)
        if route:
            names.append("ScenarioAnalyzer." + route)
    code = []
    for name in names:
        target = auto_analyzer
        for part in name.split("."):
            target = getattr(target, part)
        code.append({"name": "auto_analyzer." + name, "source": inspect.getsource(target)})
    return code


# Mapping layer: website technologies and routes -> engine model numbers (same numbers as the terminal).
ROUTE_MODEL_LABELS = {
    "assignment_base": "Model 2 · Finite hot stream + finite cold stream (CL 202 Base assignment)",
    "assignment_modified": "Model 5 → Model 1 · Property change, then fixed-reservoir engine (CL 202 Modified assignment)",
}


def model_label(route):
    if not route:
        return None
    return ROUTE_MODEL_LABELS.get(route) or engine.model_label(route)



def _manual_prefill(route, case):
    """Translate automatic analyzer cases into the compact website form's field names."""
    if not isinstance(case, dict):
        return {}
    if route == "steady_control_volume":
        ins, outs, heats = case.get("inlets", []), case.get("outlets", []), case.get("heat", [])
        if len(ins) != 1 or len(outs) != 1 or len(heats) > 1:
            return {}
        a, b = ins[0], outs[0]
        h = heats[0] if heats else {}
        return {"cv_m": a.get("mass_flow_kg_s"), "cv_h_in": a.get("specific_enthalpy_kj_kg"),
                "cv_s_in": a.get("specific_entropy_kj_kg_k"), "cv_h_out": b.get("specific_enthalpy_kj_kg"),
                "cv_s_out": b.get("specific_entropy_kj_kg_k"), "cv_q": h.get("heat_into_cv_kw"),
                "cv_tb": h.get("boundary_temperature_K"), "cv_T0": case.get("T0_K"),
                "cv_h0": a.get("specific_enthalpy_dead_state_kj_kg"),
                "cv_s0": a.get("specific_entropy_dead_state_kj_kg_k"), "W_out_kw": case.get("W_out_kw")}
    if route == "closed_system":
        heats = case.get("heat", [])
        if len(heats) > 1:
            return {}
        h = heats[0] if heats else {}
        return {"delta_E_kj": case.get("delta_E_kj"), "delta_S_system_kj_K": case.get("delta_S_system_kj_K"),
                "cs_q": h.get("heat_into_system_kj"), "cs_tb": h.get("boundary_temperature_K"),
                "W_out_kj": case.get("W_out_kj"), "cs_T0": case.get("T0_K"),
                "cs_p0": case.get("environment_pressure_kPa"), "cs_dV": case.get("delta_V_m3")}
    if route == "mechanical_power":
        values = dict(case)
        if values.get("recoverable_mechanical_power_kw") is None and values.get("recoverable_energy_per_event_j") is not None and values.get("events_per_s") is not None:
            values["recoverable_mechanical_power_kw"] = values["recoverable_energy_per_event_j"] * values["events_per_s"] / 1000.0
        return {k: values[k] for k in ("recoverable_mechanical_power_kw", "W_out_kw", "heat_rejected_kw",
                                      "heat_rejection_temperature_K") if values.get(k) is not None}
    if route in {"solar_blackbody", "reaction_gibbs"}:
        return {k: v for k, v in case.items() if k not in {"model", "title", "description"} and v is not None}
    return {}


def _turn_insufficient_into_input_request(response):
    """Offer the model form when the engine found a valid route but lacks balance/reference data."""
    if not isinstance(response, dict) or response.get("status") != "evaluated":
        return response
    result = response.get("result") or {}
    verdict = (result.get("verdict") or {}).get("status")
    if verdict != "INSUFFICIENT DATA / MODEL REQUIRED":
        return response
    route = response.get("route")
    case = (response.get("cases") or {}).get(route, {})
    analysis = response.get("analysis")
    if not isinstance(analysis, dict):
        return response
    missing = list(analysis.get("missing") or [])
    reasons = (result.get("verdict") or {}).get("reasons") or []
    checks = result.get("checks") or {}
    for item in reasons:
        if "provide" in item.lower() or "missing" in item.lower():
            missing.append(item)
    performance = checks.get("performance_limit")
    if isinstance(performance, str) and "not calculated" in performance.lower():
        missing.append(performance)
    analysis["missing"] = list(dict.fromkeys(missing)) or reasons
    response["prefill"] = _manual_prefill(route, case)
    response["status"] = "needs_values"
    return response

def solve(request):
    response = _solve(request)
    if isinstance(response, dict):
        response["model_label"] = model_label(response.get("route"))
        if response.get("analysis"):
            response["analysis"]["model_label"] = model_label(response["analysis"].get("model") or response.get("route"))
    return response


def _solve(request):
    if not isinstance(request, dict):
        raise ValueError("Supply a scenario or a model case.")
    if "case" in request:
        case = request["case"]
        if case.get("model") == "assignment_base":
            cases, summary = base_assignment(case["suffix"])
            return {"status": "evaluated", "route": case["model"], "cases": cases, "result": summary,
                    "calculation_code": calculation_code(case["model"])}
        if case.get("model") == "assignment_modified":
            cases, summary = modified_assignment(case["suffix"])
            return {"status": "evaluated", "route": case["model"], "cases": cases, "result": summary,
                    "calculation_code": calculation_code(case["model"])}
        result = run_case(case)
        return {"status": "evaluated", "route": result.model, "questions": [],
                "source_quotes": {}, "cases": {result.model: case}, "result": result.as_dict(),
                "calculation_code": calculation_code(result.model)}

    scenario = request.get("scenario")
    if not isinstance(scenario, str) or not 0 < len(scenario.strip()) <= 3000:
        raise ValueError("Enter between 1 and 3,000 characters.")
    route = None
    if re.search(r"\b(?:CL\s*202|Assignment\s*1|mock assignment)\b", scenario, re.IGNORECASE):
        variants = set(re.findall(r"\b(base|modified)\b", scenario.casefold()))
        if len(variants) == 1:
            route = "assignment_" + variants.pop()

    # 1. Automatic analysis: extract, map source/sink, choose the model and run the engine.
    auto = None
    if route is None:
        auto = auto_analyzer.analyze_scenario(scenario)
        if auto["status"] == "evaluated":
            auto["calculation_code"] = analysis_code(auto["analysis"]) + calculation_code(auto["route"])
            return _turn_insufficient_into_input_request(auto)
        if auto["status"] == "needs_values" and auto.get("route"):
            return auto

    # 2. Earlier labelled-phrase extractor (kept for the CL 202 assignments and as a fallback).
    if route is None:
        for candidate, pattern in (
            ("finite_stream_engine", r"\b(?:hot (?:thermal )?oil|hot water|hot stream|cold water|cooling water|cold tap water)\b"),
            ("solar_blackbody", r"\b(?:solar|sunlight|radiation)\b"),
            ("reaction_gibbs", r"\b(?:fuel cell|chemical|gibbs|reaction|battery)\b"),
            ("fixed_reservoir_engine", r"\b(?:heat engine|reservoir|carnot)\b"),
            ("mechanical_power", r"\b(?:walking|footsteps?|mechanical|pedal|pedaling)\b"),
        ):
            if re.search(pattern, scenario, re.IGNORECASE):
                route = candidate
                break
    case, quotes, values, issues = (extract_case(scenario, route) if route is not None else (None, {}, {}, []))
    if case is not None:
        response = solve({"case": case})
        response["source_quotes"] = quotes
        if auto is not None:
            response["analysis"] = auto["analysis"]
        return response
    if auto is not None:
        if auto["status"] == "error":
            return {"error": auto["error"], "analysis": auto["analysis"], "status": "error", "route": auto["route"]}
        route = auto["route"] or route
    return {"status": "needs_values", "route": route, "cases": {}, "result": None,
            "source_quotes": quotes or (auto or {}).get("source_quotes", {}), "prefill": values,
            "issues": issues + ((auto or {}).get("issues") or []),
            "analysis": (auto or {}).get("analysis")}


def solve_json(payload):
    try:
        return json.dumps(solve(json.loads(payload)), allow_nan=False)
    except (ValueError, OverflowError) as error:
        return json.dumps({"error": str(error)})
