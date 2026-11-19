"""THERMODYNAMIC FEASIBILITY TESTER — calculation engine (terminal program and website).

Idea: draw a boundary around the device, then check
  1. First Law  — does the energy balance close?
  2. Second Law — is entropy generation S_gen >= 0?
  3. Limit      — is the claimed work below a valid reversible limit (if one exists)?
and give one verdict: FEASIBLE / NOT FEASIBLE / INSUFFICIENT DATA (or a model-specific label).

Models (same numbers on the website and in the terminal menu)
   1 fixed_reservoir_engine        heat engine between two fixed temperatures (Carnot)
   2 finite_stream_engine          hot stream + cold stream, work produced
   3 steady_control_volume         open steady device (turbine, compressor ...)
   4 closed_system                 fixed mass (piston, tank, full cycle)
   5 property_change               Delta h, Delta s, T_eff only (utility, no verdict)
   6 unsupported_domain            fallback when no validated model exists
   7 finite_stream_heat_recovery   heat exchanger, no shaft work (+ exergy destroyed)
   8 mechanical_power              mechanical harvesting: output <= mechanical input
   9 solar_blackbody               Petela-Landsberg radiation limit
  10 reaction_gibbs                fuel cell: output <= -Delta G x reaction rate

Units: K, kW (rates), kJ (totals), kg/s, kJ/kg, kJ/(kg K).
Signs: heat INTO the boundary and work OUT of it are positive; stream changes are outlet - inlet.
Standard library only (Python 3.9+). Demo cases and self-tests live in feasibility_examples.py.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

ABS_TOL, REL_TOL, S_GEN_TOL = 1.0e-8, 1.0e-6, 1.0e-8

# ============================================================================
# 1. Model registry: number, name, role, guidance and governing equations
# ============================================================================
MODELS: Dict[str, Dict[str, Any]] = {
    "fixed_reservoir_engine": dict(number=1, role="core", name="Fixed-temperature reservoir cyclic heat engine",
        guidance="Use for a cyclic engine exchanging heat with fixed-temperature reservoirs.",
        equations=["First Law: Qdot_H - Wdot_out - Qdot_C = 0", "Carnot efficiency: eta_C = 1 - T_C/T_H",
                   "Second Law: Sdot_gen = Qdot_C/T_C - Qdot_H/T_H"]),
    "finite_stream_engine": dict(number=2, role="core", name="Finite hot stream + finite cold stream (work produced)",
        guidance="Use for two finite streams with specified inlet/outlet states; current model checks balances but does not claim a universal reversible-work limit.",
        equations=["Stream enthalpy rate: Delta Hdot = mdot * Delta h", "Stream entropy rate: Delta Sdot = mdot * Delta s",
                   "Adiabatic overall First Law: Qdot_hot - Wdot_out - Qdot_cold = 0",
                   "Adiabatic overall Second Law: Sdot_gen = Delta Sdot_hot + Delta Sdot_cold"]),
    "steady_control_volume": dict(number=3, role="core", name="General steady control volume",
        guidance="Use for steady open systems when all mass streams, heat interactions and shaft work are identified; the reversible shaft-work limit also requires T0 and per-stream dead-state h0/s0 (physical flow exergy).",
        equations=["Mass: sum(mdot_in) - sum(mdot_out) = 0",
                   "Energy: Qdot - Wdot_out + sum_in[mdot(h+V^2/2+gz)] - sum_out[mdot(h+V^2/2+gz)] = 0",
                   "Entropy: Sdot_gen = sum_out(mdot*s) - sum_in(mdot*s) - sum_j(Qdot_j/T_b,j) >= 0",
                   "Flow exergy: psi = (h-h0) - T0(s-s0) + V^2/2 + gz",
                   "Reversible shaft-work limit: Wdot_rev = sum_in(mdot*psi) - sum_out(mdot*psi) + sum_j[Qdot_j(1-T0/T_b,j)]",
                   "Exergy destruction: Edot_destroyed = T0*Sdot_gen"]),
    "closed_system": dict(number=4, role="core", name="Closed system",
        guidance="Use for a closed system with total energy/entropy changes and boundary heat interactions; the reversible useful-work limit requires T0, p0 and DeltaV.",
        equations=["First Law: Q - W_out - Delta E = 0", "Entropy: S_gen = Delta S_system - sum_j(Q_j/T_b,j) >= 0",
                   "Non-flow exergy: B = E + p0*V - T0*S",
                   "Useful work: W_useful = W_total - p0*DeltaV",
                   "Reversible useful-work limit: W_useful,rev = -DeltaB + sum_j[Q_j(1-T0/T_b,j)]",
                   "Exergy destruction: B_destroyed = T0*S_gen"]),
    "property_change": dict(number=5, role="utility", name="Property changes only",
        guidance="Use for state-property changes only; this is not a complete device feasibility test.",
        equations=["Delta h = integral(Cp(T) dT)", "Delta s = integral(Cp(T)/T dT)"]),
    "unsupported_domain": dict(number=6, role="fallback", name="Unsupported / future domain",
        guidance="A dedicated domain model is required; do not substitute a Carnot model without justification.",
        equations=[]),
    "finite_stream_heat_recovery": dict(number=7, role="core", name="Finite-stream heat-recovery exchanger (no shaft work)",
        guidance="Use for an insulated two-stream heat exchanger with no shaft work; checks energy closure, entropy generation and optional exergy destruction.",
        equations=["Hot-side heat release: Qdot_hot = -mdot_hot * Delta h_hot",
                   "Cold-side heat absorption: Qdot_cold = mdot_cold * Delta h_cold",
                   "Insulated exchanger First Law: Qdot_hot - Qdot_cold = 0",
                   "Second Law: Sdot_gen = mdot_hot*Delta s_hot + mdot_cold*Delta s_cold >= 0",
                   "Exergy destruction: Edot_destroyed = T0*Sdot_gen"]),
    "mechanical_power": dict(number=8, role="extension", name="Mechanical energy harvesting",
        guidance="Use for mechanical-to-electrical conversion (walking, pedalling, wind via Betz, hydro via rho*g*Q*H); no Carnot temperature is used. Measured heat rejection is required for the First Law, and its boundary temperature for the Second Law when positive.",
        equations=["Recoverable input: P_mech = E_event * events_per_s (or supplied directly)",
                   "First Law: P_mech,in - P_electrical,out - Qdot_rejected = 0",
                   "Second Law for the lumped steady converter: Sdot_gen = Qdot_rejected/T_rejection >= 0",
                   "Resource ceiling: P_electrical,out <= P_mech,in"]),
    "solar_blackbody": dict(number=9, role="extension", name="Solar radiation conversion",
        guidance="Use for conversion of ideal blackbody (solar) radiation; the Petela-Landsberg bound is a generous upper limit for real sunlight.",
        equations=["Incident power: Qdot_rad = G * A",
                   "Petela-Landsberg fraction: eta_L = 1 - (4/3)(T0/Ts) + (1/3)(T0/Ts)^4",
                   "Radiation exergy input: Edot_rad = eta_L * Qdot_rad",
                   "First Law (ambient heat rejection): Qdot_rad - Wdot_electric - Qdot_rejected = 0",
                   "Second Law/exergy balance: Sdot_gen = (Edot_rad - Wdot_electric)/T0 >= 0",
                   "Limit: Wdot_electric <= Edot_rad"]),
    "reaction_gibbs": dict(number=10, role="extension", name="Electrochemical conversion (Gibbs energy)",
        guidance="Use for fuel cells and other electrochemical converters; in addition to the -Delta G work limit, the complete model requires reaction enthalpy, reaction temperature and the signed heat interaction at that same temperature.",
        equations=["Reaction entropy: DeltaS_rxn = (DeltaH_rxn - DeltaG_rxn)/T_reaction",
                   "First Law: Qdot_into - Wdot_electric,out - DeltaH_rxn*ndot_reaction = 0",
                   "Entropy generation: Sdot_gen = DeltaS_rxn*ndot_reaction - Qdot_into/T_reaction >= 0",
                   "Reversible non-expansion work: Wdot_rev = -DeltaG_rxn*ndot_reaction",
                   "Limit: Wdot_electric,out <= Wdot_rev (requires DeltaG < 0 for net output)"]),
}
MODEL_REGISTRY = [{"number": m["number"], "key": k, "name": m["name"], "role": m["role"]}
                  for k, m in sorted(MODELS.items(), key=lambda item: item[1]["number"])]
MODEL_NUMBER = {key: m["number"] for key, m in MODELS.items()}


def model_label(key: str) -> str:
    """'Model 7 · Finite-stream heat-recovery exchanger (no shaft work)'."""
    return f"Model {MODELS[key]['number']} · {MODELS[key]['name']}" if key in MODELS else key


# ============================================================================
# 2. Input validation and result objects
# ============================================================================
class ThermoInputError(ValueError):
    """Missing, invalid, or physically inconsistent input data."""


def number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ThermoInputError(f"{label} must be numeric, not a boolean.")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ThermoInputError(f"{label} must be numeric.") from exc
    if not math.isfinite(result):
        raise ThermoInputError(f"{label} must be finite.")
    return result


def positive(value: Any, label: str) -> float:
    if (result := number(value, label)) <= 0.0:
        raise ThermoInputError(f"{label} must be greater than zero.")
    return result


def positive_temperature(value: Any, label: str = "Temperature") -> float:
    if (result := number(value, label)) <= 0.0:
        raise ThermoInputError(f"{label} must be above 0 K. Enter absolute temperature in Kelvin.")
    return result


def is_close_to_zero(value: float, scale: float = 1.0) -> bool:
    return abs(value) <= ABS_TOL + REL_TOL * max(abs(scale), 1.0)


def within_limit(work: float, limit: float) -> bool:
    return work <= limit + ABS_TOL + REL_TOL * max(limit, 1.0)


def check_required(data: Dict[str, Any], fields: Sequence[str], context: str) -> None:
    missing = [name for name in fields if name not in data or data[name] is None]
    if missing:
        raise ThermoInputError(f"Missing {context} field(s): {', '.join(missing)}.")


@dataclass
class BalanceCheck:
    residual: float
    passed: bool
    equation: str
    units: str


def balance(residual: float, scale: float, equation: str, units: str) -> BalanceCheck:
    return BalanceCheck(residual, is_close_to_zero(residual, scale), equation, units)


@dataclass
class Verdict:
    status: str
    reasons: List[str]
    scope_note: str = ("Thermodynamic verdict within the stated model and supplied information; "
                       "this does not establish practical, economic, or engineering viability.")


@dataclass
class CaseResult:
    title: str
    model: str
    description: str
    inputs: Dict[str, Any]
    calculations: Dict[str, Any]
    checks: Dict[str, Any]
    verdict: Verdict
    assumptions: List[str]
    input_status: Dict[str, str] = field(default_factory=dict)
    derivation: List[Dict[str, Any]] = field(default_factory=list)
    missing_inputs: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {"title": self.title, "model": self.model, "description": self.description, "inputs": self.inputs,
                "calculations": self.calculations, "checks": self.checks, "verdict": asdict(self.verdict),
                "assumptions": self.assumptions, "input_status": self.input_status,
                "derivation": self.derivation, "missing_inputs": self.missing_inputs}


def result(case: Dict[str, Any], model: str, default_title: str, inputs, calculations, checks, verdict,
           assumptions) -> CaseResult:
    return CaseResult(case.get("title", default_title), model, case.get("description", ""), inputs, calculations,
                      checks, verdict, assumptions)


# ============================================================================
# 3. Verdict logic shared by all models
# ============================================================================
def entropy_generation_is_negative(sgen: float) -> bool:
    return sgen < -(ABS_TOL + REL_TOL * max(abs(sgen), 1.0))


def entropy_generation_is_reversible(sgen: float) -> bool:
    return abs(sgen) <= ABS_TOL + REL_TOL * max(abs(sgen), 1.0)


def second_law(sgen: float) -> str:
    return "FAIL" if entropy_generation_is_negative(sgen) else "PASS"


def process_classification(sgen: Optional[float], first_law: Optional[BalanceCheck] = None,
                           mass_balance: Optional[BalanceCheck] = None,
                           performance_limit_passed: Optional[bool] = None) -> str:
    """Reversible, irreversible, or impossible (and why)."""
    if first_law is not None and not first_law.passed:
        return "IMPOSSIBLE UNDER THE STATED MODEL"
    if mass_balance is not None and not mass_balance.passed:
        return "IMPOSSIBLE UNDER THE STATED STEADY-STATE MODEL"
    if sgen is not None and entropy_generation_is_negative(sgen):
        return "IMPOSSIBLE — SECOND LAW VIOLATION"
    if performance_limit_passed is False:
        return "IMPOSSIBLE — REVERSIBLE LIMIT EXCEEDED"
    if sgen is None:
        return "NOT CLASSIFIED — ENTROPY BALANCE NOT AVAILABLE"
    return "REVERSIBLE (LIMITING CASE)" if entropy_generation_is_reversible(sgen) else "IRREVERSIBLE (SECOND-LAW ADMISSIBLE)"


def make_verdict(first_law: Optional[BalanceCheck] = None, sgen: Optional[float] = None,
                 work_out: Optional[float] = None, work_limit: Optional[float] = None,
                 model_limit_valid: bool = False, extra_reasons: Optional[List[str]] = None,
                 mass_balance: Optional[BalanceCheck] = None,
                 work_limit_tolerance: Optional[float] = None) -> Verdict:
    """Any violation -> NOT FEASIBLE; else FEASIBLE if the model has a valid limit, else INSUFFICIENT DATA."""
    reasons, violations = list(extra_reasons or []), []
    if first_law is not None and not first_law.passed:
        violations.append(f"First Law consistency check failed: residual = {first_law.residual:.6g} {first_law.units}.")
    if mass_balance is not None and not mass_balance.passed:
        violations.append(f"Steady-state mass balance failed: residual = {mass_balance.residual:.6g} {mass_balance.units}.")
    if sgen is not None and entropy_generation_is_negative(sgen):
        violations.append(f"Entropy generation is negative ({sgen:.6g}); this violates the Second Law for the stated boundary/model.")
    if work_out is not None and work_limit is not None:
        tolerance = (ABS_TOL + REL_TOL * max(abs(work_limit), 1.0)) if work_limit_tolerance is None else work_limit_tolerance
        if work_out - work_limit > tolerance:
            violations.append(f"Claimed useful work ({work_out:.6g}) exceeds the modelled reversible upper limit ({work_limit:.6g}).")
    if violations:
        return Verdict("NOT FEASIBLE", violations + reasons)
    if sgen is not None:
        reasons.append("S_gen is approximately zero: reversible limiting case within numerical tolerance."
                       if entropy_generation_is_reversible(sgen) else
                       "S_gen is positive: irreversible, but Second-Law admissible for the stated model.")
    if first_law is not None and first_law.passed:
        reasons.append("First Law balance is satisfied within numerical tolerance.")
    if model_limit_valid:
        if work_out is not None and work_limit is not None:
            reasons.append("Claimed work does not exceed the reversible limit used by this model.")
        return Verdict("FEASIBLE / NOT REJECTED", reasons)
    reasons.append("The available balance checks do not establish a defensible reversible performance limit.")
    return Verdict("INSUFFICIENT DATA / MODEL REQUIRED", reasons)


# ============================================================================
# 4. Property calculations and numerical methods
# ============================================================================
def properties_constant_cp(T1: float, T2: float, cp: float) -> Tuple[float, float]:
    """Signed (Delta h, Delta s) for constant Cp: Cp(T2-T1), Cp ln(T2/T1)."""
    T1, T2 = positive_temperature(T1, "T1"), positive_temperature(T2, "T2")
    cp = positive(cp, "Cp")
    return cp * (T2 - T1), cp * math.log(T2 / T1)


def properties_linear_cp(T1: float, T2: float, a: float, b: float) -> Tuple[float, float]:
    """Exact integrals for Cp(T) = a + bT (Cp must stay positive)."""
    T1, T2 = positive_temperature(T1, "T1"), positive_temperature(T2, "T2")
    a, b = number(a, "Cp coefficient a"), number(b, "Cp coefficient b")
    if min(a + b * T1, a + b * T2) <= 0.0:
        raise ThermoInputError("Cp(T)=a+bT must remain positive over the entire temperature interval.")
    return a * (T2 - T1) + 0.5 * b * (T2**2 - T1**2), a * math.log(T2 / T1) + b * (T2 - T1)


def adaptive_simpson(function: Callable[[float], float], lower: float, upper: float,
                     tolerance: float = 1.0e-8, max_depth: int = 18) -> float:
    """Adaptive Simpson integration: split each interval until the error estimate is small."""
    if lower == upper:
        return 0.0
    if upper < lower:
        return -adaptive_simpson(function, upper, lower, tolerance, max_depth)
    if tolerance <= 0 or max_depth < 1:
        raise ThermoInputError("Integration tolerance and maximum depth must be positive.")
    simpson = lambda a, b, fa, fm, fb: (b - a) * (fa + 4.0 * fm + fb) / 6.0

    def refine(a, b, fa, fm, fb, whole, tol, depth):
        mid = 0.5 * (a + b)
        flm, frm = function(0.5 * (a + mid)), function(0.5 * (mid + b))
        left, right = simpson(a, mid, fa, flm, fm), simpson(mid, b, fm, frm, fb)
        delta = left + right - whole
        if depth <= 0 or abs(delta) <= 15.0 * tol:
            return left + right + delta / 15.0
        return refine(a, mid, fa, flm, fm, left, tol / 2.0, depth - 1) + refine(mid, b, fm, frm, fb, right, tol / 2.0, depth - 1)

    f_lo, f_mid, f_hi = function(lower), function(0.5 * (lower + upper)), function(upper)
    return refine(lower, upper, f_lo, f_mid, f_hi, simpson(lower, upper, f_lo, f_mid, f_hi), tolerance, max_depth)


def properties_numerical(T1: float, T2: float, cp_function: Callable[[float], float]) -> Tuple[float, float]:
    """Delta h = integral Cp dT and Delta s = integral Cp/T dT for any Cp(T)."""
    T1, T2 = positive_temperature(T1, "T1"), positive_temperature(T2, "T2")
    cp = lambda T: positive(cp_function(T), f"Cp({T:g} K)")
    return adaptive_simpson(cp, T1, T2), adaptive_simpson(lambda T: cp(T) / T, T1, T2)


def effective_temperature(delta_h: float, delta_s: float) -> float:
    """T_eff = |Delta h| / |Delta s| — an equivalent temperature, not by itself a Carnot limit."""
    dh, ds = number(delta_h, "Delta h"), number(delta_s, "Delta s")
    if abs(dh) <= ABS_TOL or abs(ds) <= ABS_TOL:
        raise ThermoInputError("T_eff requires non-zero Delta h and Delta s.")
    return positive_temperature(abs(dh) / abs(ds), "Effective temperature")


def phase_change_temperature(delta_h: float, delta_s: float) -> float:
    """Isothermal phase change: T = Delta h / Delta s."""
    dh, ds = number(delta_h, "Phase-change Delta h"), number(delta_s, "Phase-change Delta s")
    if abs(ds) <= ABS_TOL:
        raise ThermoInputError("Phase-change Delta s must be non-zero.")
    return positive_temperature(dh / ds, "Phase-change temperature")


def solve_temperature_bisection(target_delta_h: float, reference_temperature: float, lower_temperature: float,
                                upper_temperature: float, cp_function: Callable[[float], float],
                                tolerance: float = 1.0e-7, max_iterations: int = 150) -> float:
    """Find T such that integral(T_ref -> T) Cp dT = target, by bracketed bisection."""
    target = number(target_delta_h, "Target Delta h")
    reference = positive_temperature(reference_temperature, "Reference temperature")
    low = positive_temperature(lower_temperature, "Lower temperature bound")
    high = positive_temperature(upper_temperature, "Upper temperature bound")
    if high <= low:
        raise ThermoInputError("Upper temperature bound must exceed lower bound.")
    residual = lambda T: properties_numerical(reference, T, cp_function)[0] - target
    f_low, f_high = residual(low), residual(high)
    if abs(f_low) <= tolerance:
        return low
    if abs(f_high) <= tolerance:
        return high
    if f_low * f_high > 0:
        raise ThermoInputError("Bisection interval does not bracket a root.")
    for _ in range(max_iterations):
        mid = 0.5 * (low + high)
        f_mid = residual(mid)
        if abs(f_mid) <= tolerance or high - low <= tolerance:
            return mid
        if f_low * f_mid <= 0:
            high, f_high = mid, f_mid
        else:
            low, f_low = mid, f_mid
    raise ThermoInputError("Bisection did not converge within the iteration limit.")


def stream_properties(stream: Dict[str, Any]) -> Dict[str, float]:
    """Signed Delta h, Delta s and their rates (x mass flow) for one constant/linear-Cp stream."""
    if not isinstance(stream, dict):
        raise ThermoInputError("Each stream must be a JSON object/dictionary.")
    check_required(stream, ["name", "mass_flow_kg_s", "T_in_K", "T_out_K", "cp_model"], "stream")
    name = str(stream["name"])
    mass_flow = number(stream["mass_flow_kg_s"], f"{name} mass flow")
    if mass_flow <= 0:
        raise ThermoInputError(f"{name} mass flow must be positive.")
    t_in = positive_temperature(stream["T_in_K"], f"{name} inlet temperature")
    t_out = positive_temperature(stream["T_out_K"], f"{name} outlet temperature")
    cp_model = str(stream["cp_model"]).lower()
    if cp_model == "constant":
        check_required(stream, ["cp_kj_kg_k"], f"{name} constant-Cp model")
        dh, ds = properties_constant_cp(t_in, t_out, stream["cp_kj_kg_k"])
    elif cp_model == "linear":
        check_required(stream, ["a", "b"], f"{name} linear-Cp model")
        dh, ds = properties_linear_cp(t_in, t_out, stream["a"], stream["b"])
    else:
        raise ThermoInputError(f"{name}: cp_model must be 'constant' or 'linear'.")
    return {"mass_flow_kg_s": mass_flow, "T_in_K": t_in, "T_out_K": t_out, "delta_h_kj_kg": dh,
            "delta_s_kj_kg_k": ds, "delta_H_dot_kw": mass_flow * dh, "delta_S_dot_kw_k": mass_flow * ds}


# ============================================================================
# 5. Balance equations
# ============================================================================
def carnot_efficiency(T_hot: float, T_cold: float) -> float:
    """eta_Carnot = 1 - T_C/T_H."""
    th = positive_temperature(T_hot, "Hot reservoir temperature")
    tc = positive_temperature(T_cold, "Cold reservoir temperature")
    if th <= tc:
        raise ThermoInputError("A heat engine needs T_hot > T_cold for positive work output.")
    return 1.0 - tc / th


def actual_efficiency(useful_output: float, defined_input: float) -> float:
    output, energy_in = number(useful_output, "Useful output"), positive(defined_input, "Defined primary energy input")
    if output < 0:
        raise ThermoInputError("Useful work output cannot be negative in this engine model.")
    return output / energy_in


def fixed_reservoir_entropy_generation(Q_hot: float, T_hot: float, Q_cold_rejected: float, T_cold: float) -> float:
    """Clausius for a two-reservoir cycle: S_gen = Q_C/T_C - Q_H/T_H."""
    qh, qc = number(Q_hot, "Hot-side heat input"), number(Q_cold_rejected, "Cold-side rejected heat")
    th = positive_temperature(T_hot, "Hot reservoir temperature")
    tc = positive_temperature(T_cold, "Cold reservoir temperature")
    if qh < 0 or qc < 0:
        raise ThermoInputError("Heat magnitudes must be non-negative.")
    return qc / tc - qh / th


def _flow_sum(streams, label: str, prop: Optional[Callable[[Dict[str, Any]], float]] = None,
              need: Sequence[str] = ()) -> float:
    """Sum of mdot * prop(stream) over CV streams (prop=None sums mdot); mass flows must be >= 0."""
    total = 0.0
    for stream in streams:
        check_required(stream, ["mass_flow_kg_s", *need], label)
        mdot = number(stream["mass_flow_kg_s"], f"{label} mass flow")
        value = prop(stream) if prop else 1.0
        if mdot < 0:
            raise ThermoInputError(f"{label} mass flow cannot be negative.")
        total += mdot * value
    return total


def _heat_sum(heat, key: str, context: str, label: str, divide_by_T: bool) -> float:
    """sum(Q) or sum(Q/T_boundary) over the heat interactions."""
    total = 0.0
    for item in heat:
        check_required(item, [key], context)
        q = number(item[key], label)
        if q == 0 and item.get("boundary_temperature_K") is None:
            continue  # Explicitly zero heat carries zero entropy; no fabricated temperature.
        if divide_by_T:
            q /= positive_temperature(item["boundary_temperature_K"], "Heat boundary temperature")
        total += q
    return total


def steady_cv_mass_balance(inlets: Sequence[Dict[str, float]], outlets: Sequence[Dict[str, float]]) -> BalanceCheck:
    """sum(mdot_in) - sum(mdot_out) = 0."""
    m_in, m_out = _flow_sum(inlets, "Inlet stream"), _flow_sum(outlets, "Outlet stream")
    return balance(m_in - m_out, max(abs(m_in), abs(m_out), 1.0), "sum(mdot_in) - sum(mdot_out) = 0", "kg/s")


def steady_cv_energy_balance(inlets, outlets, heat, work_out_kw: float, include_kinetic_potential: bool = False) -> BalanceCheck:
    """Q_in - W_out + sum mdot(h + V^2/2 + gz)_in - sum mdot(...)_out = 0."""
    def energy(label):
        def e(stream):                                    # specific energy h + V^2/2 + g z  (kJ/kg)
            h = number(stream["specific_enthalpy_kj_kg"], f"{label} specific enthalpy")
            if not include_kinetic_potential:
                return h
            v = number(stream.get("velocity_m_s"), f"{label} velocity")
            z = number(stream.get("elevation_m"), f"{label} elevation")
            g = number(stream.get("g_m_s2", 9.80665), f"{label} gravity")
            return h + v**2 / 2000.0 + g * z / 1000.0
        return e
    need = ["specific_enthalpy_kj_kg"]
    e_in = _flow_sum(inlets, "Inlet stream", energy("Inlet stream"), need)
    e_out = _flow_sum(outlets, "Outlet stream", energy("Outlet stream"), need)
    q_in = _heat_sum(heat, "heat_into_cv_kw", "CV heat interaction", "Heat into CV", False)
    work = number(work_out_kw, "Work output")
    return balance(q_in - work + e_in - e_out, max(abs(q_in), abs(work), abs(e_in), abs(e_out), 1.0),
                   "Qdot_in - Wdot_out + sum(mdot*e_in) - sum(mdot*e_out) = 0", "kW")


def steady_cv_entropy_generation(inlets, outlets, heat) -> float:
    """S_gen = sum_out(mdot s) - sum_in(mdot s) - sum(Q/T_b)."""
    s = lambda label: lambda stream: number(stream["specific_entropy_kj_kg_k"], f"{label} specific entropy")
    need = ["specific_entropy_kj_kg_k"]
    s_in = _flow_sum(inlets, "Inlet stream", s("Inlet stream"), need)
    s_out = _flow_sum(outlets, "Outlet stream", s("Outlet stream"), need)
    return s_out - s_in - _heat_sum(heat, "heat_into_cv_kw", "CV heat interaction", "Heat into CV", True)


def closed_system_energy_balance(delta_energy_kj: float, heat, work_out_kj: float) -> BalanceCheck:
    """Q - W - Delta E = 0."""
    q = 0.0
    for item in heat:
        check_required(item, ["heat_into_system_kj"], "closed-system heat interaction")
        q += number(item["heat_into_system_kj"], "Heat into system")
        if item.get("boundary_temperature_K") is not None:
            positive_temperature(item["boundary_temperature_K"], "Heat boundary temperature")
    delta_e, work = number(delta_energy_kj, "Delta E"), number(work_out_kj, "Work out")
    return balance(q - work - delta_e, max(abs(q), abs(work), abs(delta_e), 1.0), "Q_into - W_out - Delta E = 0", "kJ")


def closed_system_entropy_generation(delta_entropy_system_kj_k: float, heat) -> float:
    """S_gen = Delta S_system - sum(Q/T_b)."""
    delta_s = number(delta_entropy_system_kj_k, "System Delta S")
    return delta_s - _heat_sum(heat, "heat_into_system_kj", "closed-system heat interaction", "Heat into system", True)


# ============================================================================
# 6. The ten models
# ============================================================================
def analyse_fixed_reservoir_engine(case: Dict[str, Any]) -> CaseResult:
    """Model 1: Carnot limit, First Law and S_gen = Q_C/T_C - Q_H/T_H."""
    check_required(case, ["T_hot_K", "T_cold_K", "Q_hot_kw", "W_out_kw"], "fixed-reservoir engine")
    th = positive_temperature(case["T_hot_K"], "Hot reservoir temperature")
    tc = positive_temperature(case["T_cold_K"], "Cold reservoir temperature")
    qh = positive(case["Q_hot_kw"], "Heat input")
    work = number(case["W_out_kw"], "Work output")
    if work < 0:
        raise ThermoInputError("Work output cannot be negative for the heat-engine model.")
    qc = None if case.get("Q_cold_rejected_kw") is None else number(case["Q_cold_rejected_kw"], "Rejected heat")
    if qc is not None and qc < 0:
        raise ThermoInputError("Rejected heat cannot be negative for this engine model.")
    qc_first_law, scale = qh - work, max(qh, work, qc or 0.0, 1.0)
    if qc is None and qc_first_law < 0 and not is_close_to_zero(qc_first_law, scale):
        energy = BalanceCheck(qc_first_law, False, "Qdot_H - Wdot_out >= 0 for Qdot_C >= 0", "kW")
        note, sgen = "Claimed work exceeds all supplied heat. No nonnegative rejected heat can close the balance.", None
    else:
        if qc is None:
            qc, note = max(0.0, qc_first_law), "Rejected heat was inferred from the First Law: Q_C = Q_H - W_out."
        else:
            note = "Rejected heat was supplied and independently checked against the First Law."
        energy = balance(qc_first_law - qc, scale, "Qdot_H - Wdot_out - Qdot_C = 0", "kW")
        sgen = fixed_reservoir_entropy_generation(qh, th, qc, tc)
    eta, eta_carnot = actual_efficiency(work, qh), carnot_efficiency(th, tc)
    limit = qh * eta_carnot
    return result(case, "fixed_reservoir_engine", "Fixed-reservoir cyclic heat engine",
        {"T_hot_K": th, "T_cold_K": tc, "Q_hot_kw": qh, "W_out_kw": work, "Q_cold_rejected_kw": qc},
        {"Q_cold_rejected_kw": qc, "actual_efficiency_fraction": eta, "actual_efficiency_percent": 100.0 * eta,
         "carnot_efficiency_fraction": eta_carnot, "carnot_efficiency_percent": 100.0 * eta_carnot,
         "efficiency_margin_below_carnot_fraction": eta_carnot - eta, "reversible_work_limit_kw": limit,
         "S_gen_dot_kw_per_k": sgen},
        {"first_law": asdict(energy), "energy_input_note": note,
         "second_law": "NOT EVALUATED" if sgen is None else second_law(sgen),
         "performance_limit": "PASS" if within_limit(work, limit) else "FAIL",
         "process_classification": process_classification(sgen, energy, performance_limit_passed=within_limit(work, limit))},
        make_verdict(energy, sgen, work, limit, True),
        ["Cyclic device; working-fluid state returns to its initial state over each cycle.",
         "Only two heat reservoirs are included in the model.",
         "Q_H and Q_C are positive heat-rate magnitudes; W_out is positive work rate.",
         "Kinetic and potential energy changes are neglected."])


def _two_streams(case: Dict[str, Any], context: str, hot_msg: str, cold_msg: str):
    """Shared by Models 2 and 7: hot stream must cool, cold stream must warm."""
    check_required(case, ["hot_stream", "cold_stream"], context)
    hot, cold = stream_properties(case["hot_stream"]), stream_properties(case["cold_stream"])
    if hot["delta_h_kj_kg"] >= 0:
        raise ThermoInputError(hot_msg)
    if cold["delta_h_kj_kg"] <= 0:
        raise ThermoInputError(cold_msg)
    return hot, cold, -hot["delta_H_dot_kw"], cold["delta_H_dot_kw"]


def reversible_cold_outlet_temperature(cold_stream: Dict[str, Any], entropy_to_absorb_kw_k: float) -> float:
    """Cold outlet at which the cold stream gains exactly this entropy rate (makes S_gen = 0)."""
    mass = positive(cold_stream["mass_flow_kg_s"], "Cold stream mass flow rate")
    t_in = positive_temperature(cold_stream["T_in_K"], "Cold stream inlet temperature")
    target = entropy_to_absorb_kw_k / mass
    if cold_stream.get("cp_model", "constant").lower() == "constant":
        return t_in * math.exp(target / positive(cold_stream["cp_kj_kg_k"], "Cold stream Cp"))
    entropy = lambda t: stream_properties(dict(cold_stream, T_out_K=t, mass_flow_kg_s=1.0))["delta_s_kj_kg_k"]
    lo, hi = t_in, t_in * 2.0
    while entropy(hi) < target:
        hi *= 2.0
        if hi > 1.0e6:
            raise ThermoInputError("Could not bracket the reversible cold-outlet temperature.")
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if entropy(mid) < target else (lo, mid)
    return 0.5 * (lo + hi)


def analyse_finite_stream_engine(case: Dict[str, Any]) -> CaseResult:
    """Model 2: two finite streams and a work-producing device inside one adiabatic boundary."""
    hot, cold, q_hot, q_cold = _two_streams(case, "finite-stream thermal engine",
        "The designated hot/source stream must cool: its outlet enthalpy must be lower than its inlet enthalpy.",
        "The designated cold/sink stream must warm: its outlet enthalpy must be higher than its inlet enthalpy.")
    work_balance = q_hot - q_cold
    if q_hot <= 0:
        raise ThermoInputError("The hot source must release a positive heat rate.")
    if work_balance < -ABS_TOL:
        raise ThermoInputError("The specified stream changes require net work input, not work production.")
    work_balance = 0.0 if abs(work_balance) <= ABS_TOL else work_balance
    claimed = case.get("W_out_kw")
    work = work_balance if claimed is None else number(claimed, "Claimed work output")
    if work < 0:
        raise ThermoInputError("Claimed work output cannot be negative in this engine model.")
    energy = balance(q_hot - work - q_cold, max(q_hot, work, q_cold, 1.0),
                     "Qdot_hot released - Wdot_out - Qdot_cold absorbed = 0 (adiabatic overall boundary)", "kW")
    sgen = hot["delta_S_dot_kw_k"] + cold["delta_S_dot_kw_k"]
    th_eff = effective_temperature(hot["delta_H_dot_kw"], hot["delta_S_dot_kw_k"])
    tc_eff = effective_temperature(cold["delta_H_dot_kw"], cold["delta_S_dot_kw_k"])
    eta = actual_efficiency(work, q_hot)
    # T_eff reference: shown for comparison only, never used for the verdict.
    use_teff, eta_ref, teff_ref = bool(case.get("apply_effective_temperature_limit", False)), None, None
    if th_eff > tc_eff:
        eta_ref = 1.0 - tc_eff / th_eff
        teff_ref = q_hot * eta_ref
        teff_note = ("Calculated as an effective-temperature reversible reference. It is a model assumption for this "
                     "thermal mapping, not a universal Carnot limit for all finite-stream processes.")
    else:
        teff_note = "No positive work-producing effective-temperature ordering: T_hot,eff must exceed T_cold,eff."
        if use_teff:
            raise ThermoInputError(teff_note)
    # Optional exact limit: the cold outlet that makes S_gen = 0 gives W_max = Q_hot - Q_cold,rev.
    use_strict, t_rev, strict_limit = bool(case.get("apply_strict_reversible_limit", True)), None, None
    if use_strict:
        t_rev = reversible_cold_outlet_temperature(case["cold_stream"], -hot["delta_S_dot_kw_k"])
        strict_limit = q_hot - stream_properties(dict(case["cold_stream"], T_out_K=t_rev))["delta_H_dot_kw"]
    notes = ["No independent work-output claim was supplied; the displayed work is inferred from stream enthalpy changes."] if claimed is None else []
    if use_strict:
        notes.append("Exact reversible limit applied: W_max = Q_hot - Q_cold,rev, where the cold outlet makes S_gen = 0 "
                     "for the stated hot stream and the stated cold-stream flow and inlet.")
    if use_teff and teff_ref is not None:
        notes.append("The optional effective-temperature reference is displayed for comparison only; it is not used as a universal reversible-work limit.")
    elif not use_strict:
        notes.append("No generally valid reversible-work limit is implemented for this finite-stream arrangement.")
    return result(case, "finite_stream_engine", "Finite hot-stream / cold-stream thermal device",
        {"hot_stream": dict(case["hot_stream"]), "cold_stream": dict(case["cold_stream"]), "claimed_W_out_kw": claimed,
         "apply_effective_temperature_limit": use_teff, "apply_strict_reversible_limit": use_strict},
        {"hot_stream_property_results": hot, "cold_stream_property_results": cold,
         "heat_released_by_hot_stream_kw": q_hot, "heat_absorbed_by_cold_stream_kw": q_cold,
         "work_from_stream_energy_balance_kw": work_balance, "work_used_for_assessment_kw": work,
         "actual_efficiency_relative_to_hot_stream_heat_fraction": eta,
         "actual_efficiency_relative_to_hot_stream_heat_percent": 100.0 * eta,
         "hot_effective_temperature_K": th_eff, "cold_effective_temperature_K": tc_eff,
         "effective_temperature_efficiency_reference": eta_ref, "effective_temperature_work_reference_kw": teff_ref,
         "S_gen_dot_kw_per_k": sgen, "reversible_cold_outlet_temperature_K": t_rev,
         "strict_reversible_work_limit_kw": strict_limit},
        {"first_law": asdict(energy), "second_law": second_law(sgen),
         "process_classification": process_classification(sgen, energy, performance_limit_passed=(
             None if strict_limit is None else within_limit(work, strict_limit))),
         "effective_temperature_reference_comparison": ("NOT AVAILABLE" if teff_ref is None else
             "BELOW REFERENCE" if within_limit(work, teff_ref) else "ABOVE REFERENCE"),
         "effective_temperature_limit_note": teff_note,
         "energy_balance_note": ("Work output was inferred from the two stream enthalpy changes." if claimed is None
                                 else "Claimed work was checked against the stream energy balance.")},
        make_verdict(energy, sgen, None if claimed is None else work, strict_limit, use_strict and claimed is not None, notes),
        ["Steady operation; both streams have the same inlet and outlet mass flow rates.",
         "The complete boundary encloses the device and both streams.",
         "The complete boundary is adiabatic to the surroundings; no external heat interaction is specified.",
         "Only sensible enthalpy changes represented by the selected Cp models are included.",
         "Kinetic and potential energy changes are neglected.",
         "T_eff=|Delta Hdot|/|Delta Sdot| is an equivalent stream descriptor.",
         ("The effective-temperature reference is displayed as an informational comparison only; it is not treated as a generally valid reversible-work limit."
          if use_teff else "The effective-temperature ratio is not used as a universal performance limit.")])


def analyse_steady_control_volume(case: Dict[str, Any]) -> CaseResult:
    """Model 3: steady mass/energy/entropy balances plus reversible flow-exergy work ceiling."""
    check_required(case, ["inlets", "outlets", "heat", "W_out_kw"], "steady control volume")
    inlets, outlets, heat = case["inlets"], case["outlets"], case["heat"]
    if not all(isinstance(x, list) for x in (inlets, outlets, heat)):
        raise ThermoInputError("inlets, outlets and heat must each be lists in the JSON input.")
    if not all(isinstance(item, dict) for item in inlets + outlets + heat):
        raise ThermoInputError("Every inlet, outlet and heat interaction must be a JSON object.")
    if not inlets and not outlets:
        raise ThermoInputError("At least one mass inlet or outlet is required for a control-volume model.")
    work, kepe = number(case["W_out_kw"], "Work output"), bool(case.get("include_kinetic_potential", False))
    mass = steady_cv_mass_balance(inlets, outlets)
    energy = steady_cv_energy_balance(inlets, outlets, heat, work, kepe)
    sgen = steady_cv_entropy_generation(inlets, outlets, heat)
    T0_raw = case.get("T0_K")
    missing_exergy = []
    if T0_raw is None:
        missing_exergy.append("T0_K")
    for side, streams in (("inlets", inlets), ("outlets", outlets)):
        for i, stream in enumerate(streams):
            for key in ("specific_enthalpy_dead_state_kj_kg", "specific_entropy_dead_state_kj_kg_k"):
                if stream.get(key) is None:
                    missing_exergy.append(f"{side}[{i}].{key}")
    work_limit = None
    exergy = {}
    if not missing_exergy:
        T0 = positive_temperature(number(T0_raw, "Environment temperature T0"), "Environment temperature T0")
        def flow_exergy(stream, label):
            mdot = positive(number(stream["mass_flow_kg_s"], f"{label} mass flow"), f"{label} mass flow")
            h = number(stream["specific_enthalpy_kj_kg"], f"{label} enthalpy")
            entropy = number(stream["specific_entropy_kj_kg_k"], f"{label} entropy")
            h0 = number(stream["specific_enthalpy_dead_state_kj_kg"], f"{label} dead-state enthalpy")
            s0 = number(stream["specific_entropy_dead_state_kj_kg_k"], f"{label} dead-state entropy")
            psi = h - h0 - T0 * (entropy - s0)
            if kepe:
                v = number(stream.get("velocity_m_s"), f"{label} velocity")
                z = number(stream.get("elevation_m"), f"{label} elevation")
                g = number(stream.get("g_m_s2", 9.80665), f"{label} gravity")
                psi += v**2 / 2000.0 + g * z / 1000.0
            return mdot * psi
        ex_in = sum(flow_exergy(x, "Inlet") for x in inlets)
        ex_out = sum(flow_exergy(x, "Outlet") for x in outlets)
        ex_heat = sum(number(x["heat_into_cv_kw"], "Heat into CV") *
                      (1.0 - T0 / positive_temperature(x["boundary_temperature_K"], "Heat boundary temperature"))
                      for x in heat if number(x["heat_into_cv_kw"], "Heat into CV") != 0)
        work_limit = ex_in - ex_out + ex_heat
        exergy = {"inlet_flow_exergy_kw": ex_in, "outlet_flow_exergy_kw": ex_out,
                  "heat_transfer_exergy_kw": ex_heat, "environment_temperature_K": T0,
                  "reversible_shaft_work_limit_kw": work_limit, "exergy_destruction_kw": T0 * sgen}
    verdict = make_verdict(energy, sgen, work, work_limit, model_limit_valid=work_limit is not None,
                           mass_balance=mass)
    return result(case, "steady_control_volume", "Steady control-volume balance",
        {"inlets": inlets, "outlets": outlets, "heat": heat, "W_out_kw": work,
         "include_kinetic_potential": kepe, "T0_K": T0_raw},
        {"S_gen_dot_kw_per_k": sgen, **exergy},
        {"mass_balance": asdict(mass), "first_law": asdict(energy), "second_law": second_law(sgen),
         "process_classification": process_classification(sgen, energy, mass),
         "performance_limit": ("Calculated from the steady-flow exergy balance." if work_limit is not None else
                               "Not calculated; provide " + ", ".join(missing_exergy) + "."),
         "reversible_work_limit": work_limit is not None},
        verdict,
        ["Steady state; all mass streams and heat interactions crossing the boundary must be listed.",
         "Heat is positive into the control volume; shaft work is positive out.",
         "Flow exergy psi = (h-h0) - T0(s-s0), plus kinetic/potential exergy when enabled.",
         "Reversible shaft-work ceiling = sum(mdot*psi)_in - sum(mdot*psi)_out + sum[Qdot*(1-T0/Tb)].",
         "Dead-state h0 and s0 must be supplied for every stream. Chemical/mixing exergy is not included."])

def analyse_closed_system(case: Dict[str, Any]) -> CaseResult:
    """Model 4: closed-system balances and reversible useful-work ceiling from non-flow exergy."""
    check_required(case, ["delta_E_kj", "delta_S_system_kj_K", "heat", "W_out_kj"], "closed system")
    heat = case["heat"]
    if not isinstance(heat, list) or not all(isinstance(item, dict) for item in heat):
        raise ThermoInputError("heat must be a list of JSON objects in the input.")
    energy = closed_system_energy_balance(case["delta_E_kj"], heat, case["W_out_kj"])
    sgen = closed_system_entropy_generation(case["delta_S_system_kj_K"], heat)
    required = ("T0_K", "environment_pressure_kPa", "delta_V_m3")
    missing = [key for key in required if case.get(key) is None]
    work_limit = useful_work = None
    exergy = {}
    if not missing:
        T0 = positive_temperature(number(case["T0_K"], "Environment temperature T0"), "Environment temperature T0")
        p0 = positive(number(case["environment_pressure_kPa"], "Environment pressure"), "Environment pressure")
        dV = number(case["delta_V_m3"], "Volume change")
        dE = number(case["delta_E_kj"], "Delta E")
        dS = number(case["delta_S_system_kj_K"], "Delta S")
        heat_exergy = sum(number(x["heat_into_system_kj"], "Heat into system") *
                          (1.0 - T0 / positive_temperature(x["boundary_temperature_K"], "Heat boundary temperature"))
                          for x in heat if number(x["heat_into_system_kj"], "Heat into system") != 0)
        work_limit = -(dE + p0 * dV - T0 * dS) + heat_exergy
        useful_work = number(case["W_out_kj"], "Work out") - p0 * dV
        exergy = {"environment_temperature_K": T0, "environment_pressure_kPa": p0,
                  "volume_change_m3": dV, "useful_work_out_kj": useful_work,
                  "heat_transfer_exergy_kj": heat_exergy,
                  "reversible_useful_work_limit_kj": work_limit,
                  "exergy_destruction_kj": T0 * sgen}
    verdict = make_verdict(energy, sgen, useful_work, work_limit, model_limit_valid=work_limit is not None)
    return result(case, "closed_system", "Closed-system energy and entropy balance",
        {"delta_E_kj": number(case["delta_E_kj"], "Delta E"),
         "delta_S_system_kj_K": number(case["delta_S_system_kj_K"], "System Delta S"),
         "heat": heat, "W_out_kj": number(case["W_out_kj"], "Work out"),
         **{k: case.get(k) for k in required}},
        {"S_gen_kj_per_k": sgen, **exergy},
        {"first_law": asdict(energy), "second_law": second_law(sgen),
         "process_classification": process_classification(sgen, energy),
         "performance_limit": ("Calculated from the non-flow exergy balance." if work_limit is not None else
                               "Not calculated; provide " + ", ".join(missing) + ".")},
        verdict,
        ["Closed system: no mass crosses the boundary; heat is positive in and total work is positive out.",
         "Non-flow exergy B = E + p0*V - T0*S.",
         "Useful work excludes displacement work against the environment: W_useful = W_total - p0*DeltaV.",
         "Reversible useful-work limit = -DeltaB + sum[Q*(1-T0/Tb)]."])

def analyse_property_change(case: Dict[str, Any]) -> CaseResult:
    """Model 5: Delta h, Delta s, T_eff and optional phase-change temperature (no verdict)."""
    check_required(case, ["T1_K", "T2_K", "cp_model"], "property calculation")
    cp_model = str(case["cp_model"]).lower()
    if cp_model == "constant":
        check_required(case, ["cp_kj_kg_k"], "constant-Cp property calculation")
        dh, ds = properties_constant_cp(case["T1_K"], case["T2_K"], case["cp_kj_kg_k"])
    elif cp_model == "linear":
        check_required(case, ["a", "b"], "linear-Cp property calculation")
        dh, ds = properties_linear_cp(case["T1_K"], case["T2_K"], case["a"], case["b"])
    else:
        raise ThermoInputError("Property cp_model must be 'constant' or 'linear'.")
    try:
        teff, teff_note = effective_temperature(dh, ds), "Equivalent temperature descriptor only; not by itself a reversible work limit."
    except ThermoInputError:
        teff, teff_note = None, "Not defined for zero Delta h or zero Delta s."
    phase_t = None
    if "phase_delta_h_kj_kg" in case or "phase_delta_s_kj_kg_k" in case:
        check_required(case, ["phase_delta_h_kj_kg", "phase_delta_s_kj_kg_k"], "phase-change property calculation")
        phase_t = phase_change_temperature(case["phase_delta_h_kj_kg"], case["phase_delta_s_kj_kg_k"])
    return result(case, "property_change", "Thermal property-change calculation",
        {k: v for k, v in case.items() if k not in {"model", "title", "description"}},
        {"delta_h_kj_kg": dh, "delta_s_kj_kg_k": ds, "effective_temperature_K": teff,
         "effective_temperature_note": teff_note, "phase_change_temperature_K": phase_t},
        {"property_model": "PASS"},
        Verdict("PROPERTY CALCULATION ONLY",
                ["This calculates state-property changes; it does not assess a complete device or establish S_gen."]),
        ["The selected Cp relation represents the material over the entire stated temperature interval."])


def analyse_unsupported_domain(case: Dict[str, Any]) -> CaseResult:
    """Model 6: no validated model — say so instead of inventing a Carnot temperature."""
    return result(case, "unsupported_domain", "Unsupported or incomplete thermodynamic model",
        {"domain": str(case.get("domain", "nonthermal/unspecified"))}, {},
        {"model_support": "NO APPLICABLE MODEL SELECTED"},
        Verdict("INSUFFICIENT DATA / MODEL REQUIRED",
                [str(case.get("reason", "A domain-specific reversible model and complete required data have not been provided."))]),
        ["No Carnot temperatures are invented for a nonthermal process.",
         "The assessment requires a supported route and its physical input data."])


def exchanger_minimum_approach(hot, cold, duty):
    """Exact counterflow minimum for constant/linear Cp, including an internal pinch.

    Along heat coordinate q from hot outlet/cold inlet, C(q)^2 =
    [mdot Cp(T_start)]^2 + 2 mdot b q. Stationary temperature difference
    therefore has at most one root, where the two capacity rates are equal.
    """
    def curve(stream, start):
        mass = positive(stream["mass_flow_kg_s"], "Mass flow")
        if stream.get("cp_model") == "linear":
            a, b = number(stream["a"], "Cp intercept"), number(stream["b"], "Cp slope")
        else:
            a, b = positive(stream["cp_kj_kg_k"], "Cp"), 0.0
        cp = positive(a + b*start, "Cp at profile start")
        def temperature(q):
            discriminant = cp*cp + 2*b*q/mass
            if discriminant <= 0:
                raise ThermoInputError("The exchanger profile leaves the positive-Cp range.")
            return start + 2*q/mass/(cp + math.sqrt(discriminant))
        return (mass*cp)**2, 2*mass*b, temperature
    h0, h1, ht = curve(hot, hot["T_out_K"])
    c0, c1, ct = curve(cold, cold["T_in_K"])
    candidates = [0.0, duty]
    if h1 != c1:
        root = (c0-h0)/(h1-c1)
        if 0 < root < duty:
            candidates.append(root)
    return min((ht(q)-ct(q), q) for q in candidates)


def analyse_finite_stream_heat_recovery(case: Dict[str, Any]) -> CaseResult:
    """Model 7: insulated exchanger — energy closure, S_gen >= 0, no temperature cross, exergy destroyed."""
    hot, cold, q_hot, q_cold = _two_streams(case, "finite-stream heat-recovery exchanger",
        "Hot-side stream must cool (outlet enthalpy below inlet enthalpy).",
        "Cold-side stream must warm (outlet enthalpy above inlet enthalpy).")
    residual, duty = q_hot - q_cold, max(q_hot, q_cold, 1.0)
    data_tol = case.get("energy_relative_tolerance")          # optional allowance for rounded textbook data
    if data_tol is not None:
        data_tol = number(data_tol, "Energy relative tolerance")
        if not 0.0 <= data_tol < 0.05:
            raise ThermoInputError("energy_relative_tolerance must be between 0 and 0.05 (5 %).")
    strict_pass = is_close_to_zero(residual, duty)
    rounding_pass = data_tol is not None and abs(residual) <= data_tol * duty
    energy = BalanceCheck(residual, strict_pass or rounding_pass,
                          "Qdot_hot released - Qdot_cold absorbed = 0 (insulated exchanger; no shaft work)", "kW")
    hs, cs = case["hot_stream"], case["cold_stream"]
    cross = [text for text, bad in (
        ("cold outlet is hotter than the hot inlet", number(cs["T_out_K"], "Cold outlet") > number(hs["T_in_K"], "Hot inlet")),
        ("hot outlet is colder than the cold inlet", number(hs["T_out_K"], "Hot outlet") < number(cs["T_in_K"], "Cold inlet")),
        ("cold inlet is not colder than the hot inlet", number(cs["T_in_K"], "Cold inlet") >= number(hs["T_in_K"], "Hot inlet"))) if bad]
    arrangement = case.get("flow_arrangement", "counterflow")
    if arrangement not in {"counterflow", "parallel"}:
        raise ThermoInputError("flow_arrangement must be counterflow or parallel.")
    if arrangement == "parallel" and cs["T_out_K"] > hs["T_out_K"]:
        cross.append("cold outlet exceeds hot outlet in parallel flow")
    approach = min(hs["T_in_K"] - (cs["T_out_K"] if arrangement == "counterflow" else cs["T_in_K"]),
                   hs["T_out_K"] - (cs["T_in_K"] if arrangement == "counterflow" else cs["T_out_K"]))
    heat_limit = min(-stream_properties(dict(hs, T_out_K=cs["T_in_K"]))["delta_H_dot_kw"],
                     stream_properties(dict(cs, T_out_K=hs["T_in_K"]))["delta_H_dot_kw"])
    profile_approach, pinch_duty = (approach, None)
    if arrangement == "counterflow" and energy.passed:
        profile_approach, pinch_duty = exchanger_minimum_approach(hs, cs, min(q_hot, q_cold))
        if profile_approach < -ABS_TOL:
            cross.append("temperature profiles cross inside the exchanger")
    sgen = hot["delta_S_dot_kw_k"] + cold["delta_S_dot_kw_k"]
    t0 = case.get("T0_K")
    exergy = None if t0 is None else positive_temperature(t0, "Reference environment temperature") * sgen
    if not energy.passed:
        verdict = Verdict("NOT FEASIBLE UNDER THE STATED INSULATED-EXCHANGER MODEL", [
            f"Energy mismatch is {residual:.6g} kW. An external heat/work interaction or corrected stream data is required."])
    elif entropy_generation_is_negative(sgen):
        verdict = Verdict("NOT FEASIBLE — SECOND LAW VIOLATION", [
            f"The complete exchanger boundary gives negative entropy generation ({sgen:.6g} kW/K)."])
    elif cross:
        verdict = Verdict("NOT FEASIBLE — TEMPERATURE CROSS", [
            "Temperature cross: " + " and ".join(cross) + ". Heat cannot flow from a colder to a hotter "
            "fluid in any exchanger arrangement, even though the overall S_gen is non-negative."])
    else:
        verdict = Verdict("SECOND-LAW ADMISSIBLE / NOT REJECTED", ([
            f"Energy mismatch {residual:.4g} kW ({100 * abs(residual) / duty:.4g} % of the duty) is within the "
            f"{100 * data_tol:g} % allowance for rounded input data."] if rounding_pass and not strict_pass else []) + [
            "The insulated-exchanger energy balance closes within tolerance.",
            "Entropy generation is non-negative for the complete two-stream boundary.",
            "This establishes thermodynamic admissibility for the stated exchanger model, not practical, economic, or optimal performance."])
    classification = ("IMPOSSIBLE UNDER THE STATED MODEL — ENERGY MISMATCH" if not energy.passed else
                      "IMPOSSIBLE — SECOND LAW VIOLATION" if entropy_generation_is_negative(sgen) else
                      "IMPOSSIBLE — TEMPERATURE CROSS" if cross else
                      "IRREVERSIBLE / SECOND-LAW ADMISSIBLE" if sgen > S_GEN_TOL else "REVERSIBLE LIMITING CASE")
    return result(case, "finite_stream_heat_recovery", "Finite-stream heat-recovery exchanger",
        {"hot_stream": dict(hs), "cold_stream": dict(cs), "T0_K": t0, "energy_relative_tolerance": data_tol},
        {"heat_released_by_hot_stream_kw": q_hot, "heat_absorbed_by_cold_stream_kw": q_cold,
         "energy_balance_residual_kw": residual,
         "hot_stream_entropy_rate_change_kw_per_k": hot["delta_S_dot_kw_k"],
         "cold_stream_entropy_rate_change_kw_per_k": cold["delta_S_dot_kw_k"],
         "S_gen_dot_kw_per_k": sgen, "exergy_destroyed_kw": exergy,
         "heat_transfer_upper_limit_kw": heat_limit, "minimum_terminal_approach_K": approach,
         "minimum_profile_approach_K": profile_approach, "pinch_heat_coordinate_kw": pinch_duty,
         "exergy_destruction_note": ("T0*S_gen; available only when the reference environment temperature T0 is supplied."
                                     if t0 is not None else "Not calculated: supply T0_K to calculate exergy destruction.")},
        {"first_law": asdict(energy), "second_law": second_law(sgen),
         "terminal_temperature_check": "PASS" if not cross else "FAIL: " + "; ".join(cross),
         "flow_arrangement": arrangement,
         "limiting_case": "Zero terminal approach is an ideal infinite-area limit" if approach == 0 else "Finite terminal approach",
         "process_classification": classification,
         "performance_limit": "Not assessed: this is a heat-recovery exchanger, not a work-producing engine."},
        verdict,
        ["Steady operation with no mass accumulation in either stream.",
         "The selected boundary encloses both streams and the heat exchanger.",
         "The exchanger is insulated from the surroundings and has no shaft-work interaction.",
         "The supplied Cp model represents each stream over its full temperature interval.",
         "Kinetic and potential energy changes and pressure-drop effects are neglected.",
         "The model checks thermodynamic admissibility; it does not predict heat-transfer area, pressure drop, cost, fouling or exchanger effectiveness."])


# --- Models 8-10: claimed output compared with a resource limit -------------
def resource_result(case, inputs, calculations, assumptions, missing: Sequence[str] = (),
                    rejection: Optional[str] = None) -> CaseResult:
    """Common result for Models 8-10: missing data, a direct rejection, or limit comparison."""
    if missing:
        verdict, comparison = Verdict("INSUFFICIENT DATA / MODEL REQUIRED", [f"Provide {', '.join(missing)} to evaluate this claim."]), "NOT EVALUATED"
    elif rejection:
        verdict, comparison = Verdict("NOT FEASIBLE", [rejection]), "FAIL"
    else:
        verdict = make_verdict(work_out=calculations["claimed_electrical_output_kw"],
                               work_limit=calculations["reversible_power_limit_kw"], model_limit_valid=True,
                               work_limit_tolerance=1e-9 * calculations["reversible_power_limit_kw"])
        comparison = "PASS" if verdict.status == "FEASIBLE / NOT REJECTED" else "FAIL"
    return CaseResult(case.get("title", case["model"].replace("_", " ").capitalize()), case["model"].lower(),
                      case.get("description", ""), inputs, calculations,
                      {"performance_limit": comparison,
                       "first_law_balance": "NOT EVALUATED — complete energy flows unavailable",
                       "entropy_balance": "NOT EVALUATED — entropy generation unavailable"}, verdict, assumptions)


def resource_number(case: Dict[str, Any], name: str, minimum: Optional[float] = 0.0) -> float:
    if isinstance(case[name], bool):
        raise ThermoInputError(f"{name} must be numeric.")
    value = number(case[name], name)
    if minimum is not None and value < minimum:
        raise ThermoInputError(f"{name} must be at least {minimum:g}.")
    return value


def _present(case, fields):
    inputs = {name: case[name] for name in fields if case.get(name) is not None}
    return inputs, [name for name in fields if name not in inputs]


def analyse_mechanical_power(case: Dict[str, Any]) -> CaseResult:
    """Model 8: resource ceiling plus measured First/Second Law converter balances."""
    power_field, event_fields = "recoverable_mechanical_power_kw", ("recoverable_energy_per_event_j", "events_per_s")
    direct, events = case.get(power_field) is not None, any(case.get(f) is not None for f in event_fields)
    if direct and events:
        raise ThermoInputError("Supply mechanical power or event energy and rate, using one input basis.")
    required = ("W_out_kw", *event_fields) if events else ("W_out_kw", power_field)
    assumptions = ["The supplied recoverable mechanical power is the upper-bound mechanical resource available to the converter.",
                   "Input and output use the same time interval.",
                   "A complete First Law check requires independently supplied/measured heat_rejected_kw; the unconverted resource remainder is not automatically assumed to be converter heat loss.",
                   "For positive heat rejection, its boundary temperature is required for the entropy balance.",
                   "The lumped steady converter has no mass flow or entropy accumulation and one heat-rejection boundary."]
    inputs, missing = _present(case, required)
    if not direct and not events:
        missing[-1] = f"{power_field} (or both {event_fields[0]} and {event_fields[1]})"
    if missing:
        return resource_result(case, inputs, {}, assumptions, missing)
    inputs = {name: resource_number(case, name) for name in required}
    power = (inputs[event_fields[0]] * inputs[event_fields[1]] / 1000.0 if events else inputs[power_field])
    claimed = inputs["W_out_kw"]
    tol = ABS_TOL + REL_TOL * max(power, 1.0)
    limit_ok = claimed <= power + tol
    heat_raw = case.get("heat_rejected_kw")
    temp_raw = case.get("heat_rejection_temperature_K")
    if heat_raw is None:
        verdict = make_verdict(work_out=claimed, work_limit=power, model_limit_valid=False,
                               work_limit_tolerance=tol)
        if verdict.status != "NOT FEASIBLE":
            verdict = Verdict("INSUFFICIENT DATA / MODEL REQUIRED", [*verdict.reasons,
                             "Provide measured heat_rejected_kw to close the converter First Law balance."])
        return CaseResult(case.get("title", "Mechanical energy converter"), "mechanical_power", case.get("description", ""),
            {**inputs, "heat_rejected_kw": None, "heat_rejection_temperature_K": temp_raw},
            {"recoverable_mechanical_input_kw": power, "claimed_electrical_output_kw": claimed,
             "reversible_power_limit_kw": power},
            {"input_energy_limit": "PASS" if limit_ok else "FAIL",
             "first_law_balance": "NOT EVALUATED — measured heat rejection required",
             "entropy_balance": "NOT EVALUATED — First Law boundary data missing",
             "performance_limit": "PASS" if limit_ok else "FAIL"}, verdict, assumptions)
    heat = resource_number(case, "heat_rejected_kw")
    energy = balance(power - claimed - heat, max(power, claimed, heat, 1.0),
                     "P_mechanical,in - P_electrical,out - Qdot_rejected = 0", "kW")
    sgen = None
    missing_entropy = heat > tol and temp_raw is None
    temp = None
    if temp_raw is not None:
        temp = positive_temperature(resource_number(case, "heat_rejection_temperature_K"), "Heat rejection temperature")
        if energy.passed and limit_ok:
            sgen = heat / temp
    elif abs(heat) <= tol and energy.passed and limit_ok:
        sgen = 0.0
    verdict = make_verdict(first_law=energy, sgen=sgen, work_out=claimed, work_limit=power,
                           model_limit_valid=not missing_entropy and sgen is not None, work_limit_tolerance=tol)
    if missing_entropy and verdict.status != "NOT FEASIBLE":
        verdict = Verdict("INSUFFICIENT DATA / MODEL REQUIRED", [*verdict.reasons,
                         "Provide heat_rejection_temperature_K to evaluate the entropy balance."])
    calculations = {"recoverable_mechanical_input_kw": power, "claimed_electrical_output_kw": claimed,
                    "reversible_power_limit_kw": power, "heat_rejected_kw": heat,
                    "first_law_residual_kw": energy.residual}
    if sgen is not None:
        calculations["S_gen_dot_kw_per_k"] = sgen
    return CaseResult(case.get("title", "Mechanical energy converter"), "mechanical_power", case.get("description", ""),
        {**inputs, "heat_rejected_kw": heat, "heat_rejection_temperature_K": temp}, calculations,
        {"input_energy_limit": "PASS" if limit_ok else "FAIL",
         "first_law_balance": "PASS" if energy.passed and limit_ok else "FAIL",
         "entropy_balance": "PASS" if sgen is not None and not entropy_generation_is_negative(sgen) else
                            ("NOT EVALUATED — boundary temperature required" if sgen is None and energy.passed else "FAIL"),
         "second_law": second_law(sgen) if sgen is not None else "NOT EVALUATED",
         "process_classification": process_classification(sgen, energy),
         "performance_limit": "PASS" if limit_ok else "FAIL"}, verdict, assumptions)

def analyse_solar_blackbody(case: Dict[str, Any]) -> CaseResult:
    """Model 9: Petela radiation exergy plus First/Second Law checks for a lumped ambient-rejecting converter."""
    fields = ("T_source_K", "T_ambient_K", "irradiance_W_m2", "collection_area_m2", "W_out_kw")
    assumptions = ["Ideal undiluted blackbody radiation with a blackbody reference environment.",
                   "Irradiance and collection area describe the same incident beam at the aperture.",
                   "All incident energy not converted to electricity is assumed rejected as heat at ambient T0.",
                   "Under this lumped boundary, S_gen = (radiation exergy input - electrical work)/T0.",
                   "This is an ideal upper-bound model, not a detailed PV or solar-thermal component model."]
    inputs, missing = _present(case, fields)
    if missing:
        return resource_result(case, inputs, {}, assumptions, missing)
    inputs = {name: resource_number(case, name) for name in fields}
    source = positive_temperature(inputs["T_source_K"], "Radiation source temperature")
    ambient = positive_temperature(inputs["T_ambient_K"], "Ambient temperature")
    if source < ambient:
        raise ThermoInputError("This solar model requires a source at least as warm as the environment.")
    incident = (inputs["irradiance_W_m2"] / 1000.0) * positive(inputs["collection_area_m2"], "Collection area")
    r = ambient / source
    fraction = (1-r)**2 * (r**2 + 2*r + 3) / 3
    exergy_in = incident * fraction
    claimed = inputs["W_out_kw"]
    heat_rejected = (incident - claimed if case.get("heat_rejected_kw") is None
                     else resource_number(case, "heat_rejected_kw"))
    energy = balance(incident - claimed - heat_rejected, max(incident, claimed, abs(heat_rejected), 1.0),
                     "P_radiation,in - P_electrical,out - Qdot_rejected = 0", "kW")
    sgen = (exergy_in - claimed) / ambient
    verdict = make_verdict(first_law=energy, sgen=sgen, work_out=claimed, work_limit=exergy_in,
                           model_limit_valid=True)
    return CaseResult(case.get("title", "Solar radiation converter"), "solar_blackbody", case.get("description", ""),
        inputs, {"incident_radiation_kw": incident, "petela_landsberg_exergy_fraction": fraction,
                 "radiation_exergy_input_kw": exergy_in, "claimed_electrical_output_kw": claimed,
                 "heat_rejected_to_ambient_kw": heat_rejected, "reversible_power_limit_kw": exergy_in,
                 "S_gen_dot_kw_per_k": sgen, "exergy_destruction_kw": ambient*sgen,
                 "first_law_residual_kw": energy.residual},
        {"first_law_balance": "PASS" if energy.passed and heat_rejected >= -ABS_TOL else "FAIL",
         "second_law": second_law(sgen), "entropy_balance": "PASS" if not entropy_generation_is_negative(sgen) else "FAIL",
         "performance_limit": "PASS" if claimed <= exergy_in + ABS_TOL + REL_TOL*max(exergy_in,1.0) else "FAIL",
         "process_classification": process_classification(sgen, energy)}, verdict, assumptions)

def analyse_reaction_gibbs(case: Dict[str, Any]) -> CaseResult:
    """Model 10: Gibbs work ceiling plus reaction enthalpy energy and reaction entropy balances."""
    fields = ("reaction_direction", "delta_G_kj_per_mol_rxn", "reaction_rate_mol_rxn_s", "W_out_kw",
              "delta_H_kj_per_mol_rxn", "reaction_temperature_K", "heat_into_reactor_kw", "heat_boundary_temperature_K")
    if case.get("reaction_direction") is not None and (not isinstance(case["reaction_direction"], str) or not case["reaction_direction"].strip()):
        raise ThermoInputError("reaction_direction must describe the direction used for Delta G and Delta H.")
    assumptions = ["Reaction Gibbs and enthalpy refer to the stated direction and operating conditions.",
                   "Molar quantities use moles of reaction extent for the same balanced stoichiometry.",
                   "Steady reactor: heat is positive in and electrical work is positive out.",
                   "DeltaS_rxn = (DeltaH_rxn - DeltaG_rxn)/T_reaction."]
    inputs, missing = _present(case, fields)
    # A known Gibbs limit can already reject an overclaim even if the full energy/entropy data is missing,
    # but only when the reaction direction is explicit.
    if all(case.get(k) is not None for k in ("reaction_direction", "delta_G_kj_per_mol_rxn", "reaction_rate_mol_rxn_s", "W_out_kw")) and str(case.get("reaction_direction", "")).strip():
        dg_partial = resource_number(case, "delta_G_kj_per_mol_rxn", minimum=None)
        rate_partial = positive(resource_number(case, "reaction_rate_mol_rxn_s"), "Reaction rate")
        claim_partial = resource_number(case, "W_out_kw")
        limit_partial = -dg_partial * rate_partial
        if dg_partial > 0 or claim_partial > max(0.0, limit_partial) + ABS_TOL + REL_TOL * max(abs(limit_partial), 1.0):
            reason = ("The specified forward reaction requires external non-expansion work at the stated conditions."
                      if dg_partial > 0 else
                      f"Claimed electrical output ({claim_partial:.6g} kW) exceeds the Gibbs reversible limit ({max(0.0, limit_partial):.6g} kW).")
            return CaseResult(case.get("title", "Electrochemical converter"), "reaction_gibbs", case.get("description", ""),
                inputs, {"signed_reversible_nonexpansion_work_kw": limit_partial,
                         "reversible_power_limit_kw": max(0.0, limit_partial),
                         "claimed_electrical_output_kw": claim_partial},
                {"performance_limit": "FAIL", "first_law_balance": "NOT EVALUATED — reaction enthalpy/heat data missing",
                 "entropy_balance": "NOT EVALUATED — reaction entropy/heat data missing"},
                Verdict("NOT FEASIBLE", [reason]), assumptions)
    if missing:
        return resource_result(case, inputs, {}, assumptions, missing)
    inputs["reaction_direction"] = inputs["reaction_direction"].strip()
    dg = resource_number(case, "delta_G_kj_per_mol_rxn", minimum=None)
    dh = resource_number(case, "delta_H_kj_per_mol_rxn", minimum=None)
    rate = positive(resource_number(case, "reaction_rate_mol_rxn_s"), "Reaction rate")
    claimed = resource_number(case, "W_out_kw")
    T = positive_temperature(resource_number(case, "reaction_temperature_K"), "Reaction temperature")
    qin = resource_number(case, "heat_into_reactor_kw", minimum=None)
    Tb = positive_temperature(resource_number(case, "heat_boundary_temperature_K"), "Heat boundary temperature")
    if not math.isclose(T, Tb, rel_tol=1e-6, abs_tol=1e-6):
        raise ThermoInputError("This Gibbs-limit model assumes heat transfer at the reaction temperature; set heat_boundary_temperature_K equal to reaction_temperature_K or use a more detailed reactor exergy model.")
    inputs.update({"delta_G_kj_per_mol_rxn": dg, "delta_H_kj_per_mol_rxn": dh,
                   "reaction_rate_mol_rxn_s": rate, "W_out_kw": claimed,
                   "reaction_temperature_K": T, "heat_into_reactor_kw": qin,
                   "heat_boundary_temperature_K": Tb})
    dh_rate = dh * rate
    energy = balance(qin - claimed - dh_rate, max(abs(qin), abs(claimed), abs(dh_rate), 1.0),
                     "Qdot_into - Wdot_electrical,out - DeltaH_rxn*reaction_rate = 0", "kW")
    ds = (dh - dg) / T
    sgen = ds * rate - qin / Tb
    work_limit = -dg * rate
    verdict = make_verdict(first_law=energy, sgen=sgen, work_out=claimed, work_limit=work_limit,
                           model_limit_valid=True)
    if dg > 0 and verdict.status != "NOT FEASIBLE":
        verdict = Verdict("NOT FEASIBLE", ["The specified forward reaction requires external non-expansion work at the stated conditions."])
    return CaseResult(case.get("title", "Electrochemical converter"), "reaction_gibbs", case.get("description", ""),
        inputs, {"reaction_enthalpy_rate_kw": dh_rate, "reaction_entropy_change_kj_per_mol_k": ds,
                 "heat_into_reactor_kw": qin, "claimed_electrical_output_kw": claimed,
                 "signed_reversible_nonexpansion_work_kw": work_limit,
                 "minimum_external_work_input_kw": max(0.0, -work_limit),
                 "reversible_power_limit_kw": max(0.0, work_limit), "S_gen_dot_kw_per_k": sgen,
                 "first_law_residual_kw": energy.residual},
        {"first_law_balance": "PASS" if energy.passed else "FAIL", "second_law": second_law(sgen),
         "entropy_balance": "PASS" if not entropy_generation_is_negative(sgen) else "FAIL",
         "performance_limit": "PASS" if claimed <= max(0.0,work_limit)+ABS_TOL+REL_TOL*max(abs(work_limit),1.0) else "FAIL",
         "process_classification": process_classification(sgen, energy)}, verdict, assumptions)

ANALYSERS = {"fixed_reservoir_engine": analyse_fixed_reservoir_engine,
             "finite_stream_engine": analyse_finite_stream_engine,
             "finite_stream_heat_recovery": analyse_finite_stream_heat_recovery,
             "steady_control_volume": analyse_steady_control_volume, "closed_system": analyse_closed_system,
             "property_change": analyse_property_change, "unsupported_domain": analyse_unsupported_domain,
             "mechanical_power": analyse_mechanical_power, "solar_blackbody": analyse_solar_blackbody,
             "reaction_gibbs": analyse_reaction_gibbs}
REQUIRED = {"fixed_reservoir_engine": ["T_hot_K", "T_cold_K", "Q_hot_kw", "W_out_kw"],
            "finite_stream_engine": ["hot_stream", "cold_stream"], "finite_stream_heat_recovery": ["hot_stream", "cold_stream"],
            "steady_control_volume": ["inlets", "outlets", "heat", "W_out_kw", "T0_K"],
            "closed_system": ["delta_E_kj", "delta_S_system_kj_K", "heat", "W_out_kj", "T0_K", "environment_pressure_kPa", "delta_V_m3"],
            "property_change": ["T1_K", "T2_K", "cp_model"], "unsupported_domain": [], "mechanical_power": ["W_out_kw"],
            "solar_blackbody": ["T_source_K", "T_ambient_K", "irradiance_W_m2", "collection_area_m2", "W_out_kw"],
            "reaction_gibbs": ["reaction_direction", "delta_G_kj_per_mol_rxn", "delta_H_kj_per_mol_rxn",
                               "reaction_rate_mol_rxn_s", "reaction_temperature_K", "heat_into_reactor_kw",
                               "heat_boundary_temperature_K", "W_out_kw"]}


def _resolve_case_inputs(case: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """Compatibility wrapper for the shared derivation engine."""
    from input_derivation import resolve_inputs
    resolved, evidence, _ = resolve_inputs(case)
    return resolved, {row["field"]: row["source"].upper() for row in evidence}


def _exchanger_steps(case: Dict[str, Any], c: Dict[str, Any]) -> List[str]:
    """Numbered calculation steps printed for Model 7."""
    hs, cs = case["hot_stream"], case["cold_stream"]
    cp = lambda s: (f"Cp={number(s['cp_kj_kg_k'], 'Cp'):.6g}" if s.get("cp_model", "constant").lower() == "constant"
                    else f"Cp(T)={number(s['a'], 'a'):.6g}+{number(s['b'], 'b'):.6g}T")
    side = lambda name, s: (f"{name} side: mdot={number(s['mass_flow_kg_s'], name.lower() + ' mass flow'):.6g} kg/s; "
                            f"Tin={number(s['T_in_K'], name.lower() + ' Tin'):.6g} K; Tout={number(s['T_out_K'], name.lower() + ' Tout'):.6g} K; {cp(s)} kJ/(kg K).")
    method = ("For constant Cp, each stream uses mdot*Cp*ln(Tout/Tin)."
              if all(s.get("cp_model", "constant").lower() == "constant" for s in (hs, cs))
              else "Each stream entropy change is integrated using its selected Cp(T)/T relation.")
    return [side("Hot", hs), side("Cold", cs),
            f"Hot-side heat release = mdot*Delta h = {c['heat_released_by_hot_stream_kw']:.6g} kW.",
            f"Cold-side heat absorption = mdot*Delta h = {c['heat_absorbed_by_cold_stream_kw']:.6g} kW.",
            f"First-Law residual = Qdot_hot - Qdot_cold = {c['energy_balance_residual_kw']:.6g} kW.",
            f"Entropy generation = sum of the two stream entropy-rate changes. {method} Result = {c['S_gen_dot_kw_per_k']:.8g} kW/K.",
            (f"Exergy destruction = T0*Sdot_gen = {c['exergy_destroyed_kw']:.8g} kW." if c['exergy_destroyed_kw'] is not None
             else "Exergy destruction not evaluated because T0_K was not supplied.")]



def analyse_case(case: Dict[str, Any]) -> CaseResult:
    """Validate the model name, tag the inputs, run the model and attach its equations."""
    if not isinstance(case, dict):
        raise ThermoInputError("A case must be supplied as a JSON object/dictionary.")
    check_required(case, ["model"], "case")
    model = str(case["model"]).lower()
    if model not in ANALYSERS:
        raise ThermoInputError(f"Unknown model '{model}'. Supported model keys: {', '.join(ANALYSERS)}.")
    from input_derivation import resolve_inputs, missing_inputs
    resolved, evidence, conflicts = resolve_inputs(case)
    # Exergy reference data is optional for detecting violations; the model
    # reports an incomplete verdict if it cannot establish the work ceiling.
    optional = {"T0_K", "environment_pressure_kPa", "delta_V_m3"}
    required = [k for k in REQUIRED.get(model, []) if k not in optional]
    missing = missing_inputs(resolved, required)
    if missing and model not in {"mechanical_power", "solar_blackbody", "reaction_gibbs"}:
        res = result(resolved, model, MODELS[model]["name"], resolved, {}, {},
                     Verdict("INSUFFICIENT DATA / MODEL REQUIRED",
                             ["Cannot uniquely derive: " + ", ".join(missing) + ". Supply independent measurements or state data."]), [])
    else:
        res = ANALYSERS[model](resolved)
    res.derivation = evidence
    res.missing_inputs = missing
    res.input_status = {row["field"]: row["source"].upper() for row in evidence}
    res.input_status.update({key: "REQUIRED — MISSING" for key in missing})
    res.checks["model_selection_guidance"] = MODELS[model]["guidance"]
    res.checks["model_number"] = model_label(model)
    res.calculations["governing_equations"] = MODELS[model]["equations"]
    derived = [row["field"] for row in evidence if row["source"] == "derived"]
    if derived:
        res.checks["derivation_note"] = ("Balances used to derive an input close by construction; they are not independent validation. "
                                          "The entropy and limiting-value checks still apply.")
    if conflicts:
        res.verdict = Verdict("NOT FEASIBLE", res.verdict.reasons + conflicts)
    if model == "finite_stream_heat_recovery" and not missing:
        res.calculations["calculation_steps"] = _exchanger_steps(resolved, res.calculations)

    return res


run_case = analyse_case


# ============================================================================
# 8. Terminal program: interactive input, report, JSON files
# ============================================================================
def ask_text(prompt: str, default: Optional[str] = None) -> str:
    value = input(f"{prompt}{f' [{default}]' if default is not None else ''}: ").strip()
    return value if value else (default if default is not None else "")


def _ask_number(prompt, default, cast, error):
    while True:
        raw = input(f"{prompt}{f' [{default}]' if default is not None else ''}: ").strip()
        if not raw and default is not None:
            return cast(default)
        try:
            return cast(raw)
        except ValueError:
            print(error)


def ask_float(prompt: str, default: Optional[float] = None) -> float:
    return _ask_number(prompt, default, float, "Please enter a numeric value.")


def ask_int(prompt: str, default: Optional[int] = None) -> int:
    return _ask_number(prompt, default, int, "Please enter a whole number.")


def ask_yes_no(prompt: str, default: bool = False) -> bool:
    raw = input(f"{prompt} ({'Y/n' if default else 'y/N'}): ").strip().lower()
    return default if not raw else raw in {"y", "yes", "true", "1"}


def ask_count(prompt: str, default: int, what: str) -> int:
    count = ask_int(prompt, default)
    if count < 0:
        raise ThermoInputError(f"The number of {what} cannot be negative.")
    return count


def ask_fields(spec) -> Dict[str, Any]:
    """spec = [(key, prompt, default)]; text if the default is a string, otherwise a number."""
    return {key: ask_text(p, d) if isinstance(d, str) else ask_float(p, d) for key, p, d in spec}


def ask_cp(target: Dict[str, Any]) -> Dict[str, Any]:
    cp_model = target["cp_model"] = ask_text("Cp model: constant or linear", "constant").lower()
    if cp_model == "constant":
        target["cp_kj_kg_k"] = ask_float("Constant Cp (kJ/kg K)")
    elif cp_model == "linear":
        target.update(a=ask_float("Cp coefficient a"), b=ask_float("Cp coefficient b"))
    return target


def ask_stream(label: str, default_name: str, cp_choice: bool = True) -> Dict[str, Any]:
    print(f"\n{label} stream")
    stream = ask_fields([("name", "Stream name", default_name), ("mass_flow_kg_s", "Mass flow rate (kg/s)", None),
                         ("T_in_K", "Inlet temperature (K)", None), ("T_out_K", "Outlet temperature (K)", None)])
    if not cp_choice:
        return dict(stream, cp_model="constant", cp_kj_kg_k=ask_float("Constant Cp (kJ/kg K)"))
    stream = ask_cp(stream)
    if stream["cp_model"] not in ("constant", "linear"):
        print("Unknown Cp model; validation will report the issue.")
    return stream


def ask_heat(count_default: int, key: str, unit: str, into: str) -> List[Dict[str, float]]:
    heat = []
    for i in range(ask_count("Number of heat interactions", count_default, "heat interactions")):
        print(f"Heat interaction {i+1} (positive into {into})")
        heat.append({key: ask_float(f"Heat {'rate' if unit == 'kW' else 'transfer'} ({unit})"),
                     "boundary_temperature_K": ask_float("Boundary temperature (K)")})
    return heat


MENU = ["Fixed-temperature reservoir cyclic heat engine", "Finite hot stream + finite cold stream",
        "General steady control volume (energy + entropy balances)", "Closed system (energy + entropy balances)",
        "Property changes only (Delta h, Delta s, T_eff, optional phase change)",
        "Unsupported/future domain (returns insufficient model)", "Finite-stream heat-recovery exchanger (no shaft work)",
        "Mechanical energy harvesting (walking, pedalling, wind, hydro)", "Solar radiation conversion (ideal blackbody)",
        "Electrochemical conversion (fuel cell, Gibbs energy)"]


def interactive_case() -> Dict[str, Any]:
    """Ask for the model number, then only the inputs that model needs."""
    print("\nTHERMODYNAMIC FEASIBILITY TESTER — INPUT MODE")
    print("Enter data in SI/thermodynamic units shown. Temperatures must be in K.")
    print("This M3 version needs you to extract the data from the written problem first.")
    print("\nChoose the model:")
    for i, name in enumerate(MENU, 1):
        if i == 8:
            print("  -- domain extensions --")
        print(f"{i:3d}. {name}")
    choice = ask_text("Model number")
    case: Dict[str, Any] = {"title": ask_text("Short case title", "User-supplied case"),
                            "description": ask_text("Brief process description", "")}
    if choice == "1":
        case.update(model="fixed_reservoir_engine", **ask_fields([
            ("T_hot_K", "Hot reservoir temperature (K)", None), ("T_cold_K", "Cold reservoir temperature (K)", None),
            ("Q_hot_kw", "Heat supplied from hot reservoir (kW)", None), ("W_out_kw", "Claimed work output (kW)", None)]))
        if ask_yes_no("Do you have a separately specified rejected-heat rate?", False):
            case["Q_cold_rejected_kw"] = ask_float("Rejected heat rate (kW)")
    elif choice == "2":
        case.update(model="finite_stream_engine", hot_stream=ask_stream("Hot/source", "Hot source"),
                    cold_stream=ask_stream("Cold/sink", "Cold sink"))
        if ask_yes_no("Do you have a claimed work output to test?", True):
            case["W_out_kw"] = ask_float("Claimed work output (kW)")
        case["apply_effective_temperature_limit"] = ask_yes_no(
            "Use the effective-temperature ratio as a model-specific reversible reference? (not universal)", False)
    elif choice == "3":
        print("\nFor each stream, provide flow, h, s and the same-material dead-state h0/s0 reference.")
        case["model"] = "steady_control_volume"
        case["T0_K"] = ask_float("Environment/dead-state temperature T0 (K)")
        for label in ("inlet", "outlet"):
            streams = case[label + "s"] = []
            for i in range(ask_count(f"Number of {label} streams", 1, "streams")):
                print(f"{label.title()} stream {i+1}")
                item = ask_fields([("mass_flow_kg_s", "Mass flow (kg/s)", None),
                                   ("specific_enthalpy_kj_kg", "Specific enthalpy h (kJ/kg)", None),
                                   ("specific_entropy_kj_kg_k", "Specific entropy s (kJ/kg K)", None),
                                   ("specific_enthalpy_dead_state_kj_kg", "Dead-state enthalpy h0 (kJ/kg)", None),
                                   ("specific_entropy_dead_state_kj_kg_k", "Dead-state entropy s0 (kJ/kg K)", None)])
                if ask_yes_no("Include velocity/elevation for this stream?", False):
                    item.update(ask_fields([("velocity_m_s", "Velocity (m/s)", None), ("elevation_m", "Elevation (m)", None)]))
                streams.append(item)
        case.update(heat=ask_heat(0, "heat_into_cv_kw", "kW", "control volume"), W_out_kw=ask_float("Shaft work output (kW)"),
                    include_kinetic_potential=ask_yes_no("Include kinetic/potential energy terms?", False))
    elif choice == "4":
        case.update(model="closed_system", **ask_fields([("delta_E_kj", "System total energy change Delta E (kJ)", None),
                                                         ("delta_S_system_kj_K", "System entropy change Delta S (kJ/K)", None)]))
        case.update(heat=ask_heat(1, "heat_into_system_kj", "kJ", "system"), W_out_kj=ask_float("Total work output (kJ)"),
                    T0_K=ask_float("Environment temperature T0 (K)"),
                    environment_pressure_kPa=ask_float("Environment pressure p0 (kPa)"),
                    delta_V_m3=ask_float("System volume change Delta V (m3)"))
    elif choice == "5":
        case.update(model="property_change", **ask_fields([("T1_K", "Initial temperature T1 (K)", None),
                                                           ("T2_K", "Final temperature T2 (K)", None)]))
        ask_cp(case)
        if ask_yes_no("Include phase-change Delta h and Delta s?", False):
            case.update(ask_fields([("phase_delta_h_kj_kg", "Phase-change Delta h (kJ/kg)", None),
                                    ("phase_delta_s_kj_kg_k", "Phase-change Delta s (kJ/kg K)", None)]))
    elif choice == "6":
        case.update(model="unsupported_domain", **ask_fields([
            ("domain", "Domain (e.g. chemical, radiative, mechanical)", "unspecified"),
            ("reason", "What required model/data is missing?", "A domain-specific reversible/exergy model has not been supplied.")]))
    elif choice == "7":
        case.update(model="finite_stream_heat_recovery", hot_stream=ask_stream("Hot/source", "Server coolant", False),
                    cold_stream=ask_stream("Cold/process", "Process water", False),
                    T0_K=ask_float("Reference environment temperature T0 (K)"))
    elif choice == "8":
        resource = (ask_fields([("recoverable_mechanical_power_kw", "Recoverable mechanical power (kW)", None)])
                    if ask_yes_no("Is recoverable mechanical power already known?", True) else
                    ask_fields([("recoverable_energy_per_event_j", "Recoverable energy per event, e.g. per step (J)", None),
                                ("events_per_s", "Events per second", None)]))
        case.update(model="mechanical_power", W_out_kw=ask_float("Claimed electrical output (kW)"), **resource)
        if ask_yes_no("Do you have a measured heat-rejection rate? (required to close the First Law)", False):
            case["heat_rejected_kw"] = ask_float("Measured heat rejected (kW)")
            if case["heat_rejected_kw"] > 0:
                case["heat_rejection_temperature_K"] = ask_float("Heat-rejection boundary temperature (K; required for the entropy balance)")
    elif choice == "9":
        case.update(model="solar_blackbody", **ask_fields([
            ("T_source_K", "Blackbody source temperature (K)", 5778.0), ("T_ambient_K", "Ambient temperature (K)", 300.0),
            ("irradiance_W_m2", "Incident irradiance (W/m^2)", 1000.0),
            ("collection_area_m2", "Effective collection area (m^2)", None), ("W_out_kw", "Claimed electrical output (kW)", None)]))
    elif choice == "10":
        reaction = ask_fields([
            ("reaction_direction", "Reaction direction used for Delta G and Delta H", "H2 + 1/2 O2 -> H2O(l)"),
            ("delta_G_kj_per_mol_rxn", "Signed Delta G at operating conditions (kJ/mol reaction)", -237.1),
            ("delta_H_kj_per_mol_rxn", "Signed Delta H at operating conditions (kJ/mol reaction)", -285.8),
            ("reaction_rate_mol_rxn_s", "Forward reaction rate (mol reaction/s)", None),
            ("reaction_temperature_K", "Reaction temperature (K)", 298.15),
            ("W_out_kw", "Claimed electrical output (kW)", None),
            ("heat_into_reactor_kw", "Heat into reactor (kW; negative = rejected heat)", None),
            ("heat_boundary_temperature_K", "Heat boundary temperature (K; use reaction temperature for this model)", 298.15)])
        case.update(model="reaction_gibbs", **reaction)
    else:
        raise ThermoInputError("Choose a model number from 1 to 10.")
    return case


def _format_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.8g}"
    return json.dumps(value, indent=2, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)


def print_report(result: CaseResult) -> None:
    line = "=" * 78
    print(f"\n{line}\nTHERMODYNAMIC FEASIBILITY REPORT\n{line}")
    print(f"Case       : {result.title}\nModel      : {result.model}")
    if result.description:
        print(f"Description: {result.description}")
    print(f"\nQUICK VERDICT: {result.verdict.status}")
    if "process_classification" in result.checks:
        print(f"PROCESS CLASSIFICATION: {result.checks['process_classification']}")
    sections = [("INPUTS", result.inputs, None),
                ("INPUT DATA CLASSIFICATION", result.input_status, "No separate input provenance recorded."),
                ("CALCULATIONS / GOVERNING EQUATIONS", result.calculations, "No calculations available for this model."),
                ("CHECKS", result.checks, None)]
    for heading, items, empty in sections:
        print(f"\n{heading}")
        for key, value in items.items():
            print(f"  {key}: {value if heading == 'INPUT DATA CLASSIFICATION' else _format_value(value)}")
        if not items and empty:
            print(f"  {empty}")
    print("\nASSUMPTIONS / MODEL BOUNDARY")
    for item in result.assumptions:
        print(f"  - {item}")
    print(f"\nVERDICT\n  {result.verdict.status}")
    for reason in result.verdict.reasons:
        print(f"  - {reason}")
    print(f"  Note: {result.verdict.scope_note}\n{line}")


def load_case(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except FileNotFoundError as exc:
        raise ThermoInputError(f"Input JSON file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ThermoInputError(f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise ThermoInputError("The JSON root must be an object describing one case.")
    return data


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Thermodynamic Feasibility Tester: structured input, model selection, balances and verdicts.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--input", metavar="FILE.json", help="Analyse a structured JSON case file.")
    group.add_argument("--demo", action="store_true", help="Run built-in validation demonstrations.")
    group.add_argument("--prof-demo", action="store_true", help="Run the curated data-centre waste-heat recovery demonstration.")
    group.add_argument("--self-test", action="store_true", help="Run internal formula/logic tests only.")
    group.add_argument("--make-templates", action="store_true", help="Write editable JSON input templates.")
    args = parser.parse_args(argv)
    try:
        if args.self_test or args.demo or args.prof_demo or args.make_templates:
            import feasibility_examples as examples      # demo cases, templates and self-tests
            if args.self_test:
                tests = examples.self_test()
                print(f"SELF-TEST PASSED: {len(tests)} checks")
                for item in tests:
                    print(f"  PASS — {item}")
            elif args.demo:
                examples.run_demonstrations()
            elif args.prof_demo:
                print_report(run_case(examples.demonstration_cases()[0]))
            else:
                print(f"Editable input templates written to: {examples.write_templates().resolve()}")
            return 0
        print_report(run_case(load_case(args.input) if args.input else interactive_case()))
        return 0
    except (ThermoInputError, OSError) as error:
        print(f"\nINPUT / MODEL ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
