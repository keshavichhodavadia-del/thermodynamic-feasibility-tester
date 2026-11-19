"""Deterministic, auditable input completion. Never infer a performance from its limit.

All numbers use the engine's SI-derived units. Equations run only with one
unknown and a unique solution. Supplied values are never overwritten.
"""
from copy import deepcopy
import math


def flatten(value, prefix=""):
    out = {}
    if isinstance(value, dict):
        for key, item in value.items():
            out.update(flatten(item, f"{prefix}.{key}" if prefix else key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            out.update(flatten(item, f"{prefix}.{index}"))
    else:
        out[prefix] = value
    return out


def resolve_inputs(case):
    import thermodynamic_feasibility_tester_MASTER as engine
    resolved = deepcopy(case)
    model = str(case.get("model", "")).lower()
    resolved["model"] = model
    for key in ("heat", "inlets", "outlets"):
        if resolved.get(key) is not None and (not isinstance(resolved[key], list)
                or not all(isinstance(item, dict) for item in resolved[key])):
            raise engine.ThermoInputError(f"{key} must be a list of objects.")
    values = flatten(resolved)
    evidence, conflicts = [], []
    for path, value in values.items():
        if value is not None and path not in {"model", "title", "description"}:
            evidence.append(dict(field=path, value=value, source="provided", equation=None, dependencies={}))

    def put(path, value, equation, dependencies):
        if values.get(path) is not None:
            return False
        if isinstance(value, (int, float)) and (isinstance(value, bool) or not math.isfinite(value)):
            raise engine.ThermoInputError(f"Derivation of {path} did not produce a finite number.")
        node = resolved
        parts = path.split(".")
        for part in parts[:-1]:
            if isinstance(node, list):
                node = node[int(part)]
            else:
                node = node.setdefault(part, {})
        node[int(parts[-1]) if isinstance(node, list) else parts[-1]] = value
        values[path] = value
        evidence.append(dict(field=path, value=value, source="derived", equation=equation,
                             dependencies={key: values[key] for key in dependencies}))
        return True

    def rule(target, dependencies, fn, equation):
        if values.get(target) is not None or not all(values.get(k) is not None for k in dependencies):
            return
        args = [engine.number(values[k], k) for k in dependencies]
        try:
            value = fn(*args)
        except ZeroDivisionError:
            return  # e.g. zero efficiency cannot determine input from output
        if value is not None:
            put(target, value, equation, dependencies)

    def linear(coefficients, equation):
        missing = [k for k in coefficients if values.get(k) is None]
        if len(missing) == 1:
            target = missing[0]
            if coefficients[target] != 0:
                deps = [k for k in coefficients if k != target]
                put(target, -sum(coefficients[k] * engine.number(values[k], k) for k in deps)
                    / coefficients[target], equation, deps)

    def product(output, a, b, scale, equation):
        rule(output, [a, b], lambda x, y: x*y*scale, equation)
        rule(a, [output, b], lambda x, y: x/(scale*y), equation)
        rule(b, [output, a], lambda x, y: x/(scale*y), equation)

    for _ in range(32):
        count = len(evidence)
        if model == "fixed_reservoir_engine":
            # A negative heat rejection is left to the engine's impossible-claim check.
            if not (values.get("Q_cold_rejected_kw") is None and values.get("Q_hot_kw") is not None
                    and values.get("W_out_kw") is not None and float(values["W_out_kw"]) > float(values["Q_hot_kw"])):
                linear({"Q_hot_kw": 1, "W_out_kw": -1, "Q_cold_rejected_kw": -1}, "Q_H = W_out + Q_C")
            product("W_out_kw", "Q_hot_kw", "efficiency_fraction", 1, "W_out = efficiency × Q_H")
        if model in {"finite_stream_engine", "finite_stream_heat_recovery"}:
            if model == "finite_stream_heat_recovery":
                put("W_out_kw", 0.0, "No shaft work: insulated heat-exchanger model", [])
            for side, sign, heat in (("hot_stream", -1, "Q_hot_kw"), ("cold_stream", 1, "Q_cold_rejected_kw")):
                stream = resolved.get(side)
                if not isinstance(stream, dict):
                    continue
                put(side + ".name", side.replace("_", " "), "Stream identifier", [])
                if stream.get("cp_model") is None and stream.get("cp_kj_kg_k") is not None:
                    put(side + ".cp_model", "constant", "A constant Cp was supplied", [side + ".cp_kj_kg_k"])
                m, cp, ti, to = [side + "." + key for key in ("mass_flow_kg_s", "cp_kj_kg_k", "T_in_K", "T_out_K")]
                if stream.get("cp_model") == "constant":
                    rule(heat, [m, cp, ti, to], lambda m,c,a,b: sign*m*c*(b-a), "Q = sign × mass_flow × Cp × (T_out − T_in)")
                    rule(to, [heat,m,cp,ti], lambda q,m,c,t: t + sign*q/(m*c), "T_out = T_in + sign × Q/(mass_flow × Cp)")
                    rule(ti, [heat,m,cp,to], lambda q,m,c,t: t - sign*q/(m*c), "T_in = T_out − sign × Q/(mass_flow × Cp)")
                    rule(m, [heat,cp,ti,to], lambda q,c,a,b: sign*q/(c*(b-a)), "mass_flow = sign × Q/[Cp × (T_out − T_in)]")
                    rule(cp, [heat,m,ti,to], lambda q,m,a,b: sign*q/(m*(b-a)), "Cp = sign × Q/[mass_flow × (T_out − T_in)]")
                elif stream.get("cp_model") == "linear":
                    a, b = side + ".a", side + ".b"
                    def outlet(q, mass, t, a, b):
                        dh = sign*q/mass
                        if b == 0:
                            return t + dh/a
                        disc = (a+b*t)**2 + 2*b*dh
                        if disc < 0 or a+b*t <= 0:
                            raise engine.ThermoInputError("No positive-Cp stream outlet satisfies the supplied energy balance.")
                        return t + 2*dh/(a+b*t+math.sqrt(disc))
                    rule(heat, [m,ti,to,a,b], lambda m,t,u,a,b: sign*m*engine.properties_linear_cp(t,u,a,b)[0], "Q = sign × mass_flow × integral(Cp(T) dT)")
                    rule(to, [heat,m,ti,a,b], outlet, "Invert Q = sign × mass_flow × [a ΔT + b(T_out²−T_in²)/2]; positive-Cp root")
            linear({"Q_hot_kw": 1, "W_out_kw": -1, "Q_cold_rejected_kw": -1}, "Q_hot = W_out + Q_cold")
        if model == "mechanical_power":
            # Event basis is retained; avoid passing two competing resource bases.
            power = "recoverable_mechanical_power_kw"
            if not any(values.get(k) is not None for k in ("recoverable_energy_per_event_j", "events_per_s")):
                rule("W_out_kw", [power, "heat_rejected_kw"], lambda p,q: p-q, "W_out = mechanical_input − measured_heat_rejection")
            else:
                rule("W_out_kw", ["recoverable_energy_per_event_j", "events_per_s", "heat_rejected_kw"],
                     lambda e,f,q: e*f/1000-q, "W_out = event_energy × event_rate / 1000 − measured_heat_rejection")
            # Recoverable resource is a ceiling, not necessarily actual power into
            # the converter: never turn its unused remainder into measured heat.
        if model == "solar_blackbody":
            product("incident_radiation_kw", "irradiance_W_m2", "collection_area_m2", .001, "P_incident = irradiance × area / 1000")
            product("W_out_kw", "incident_radiation_kw", "efficiency_fraction", 1, "W_out = efficiency × P_incident")
            linear({"incident_radiation_kw":1, "W_out_kw":-1, "heat_rejected_kw":-1}, "P_incident = W_out + Q_rejected (ambient rejection model)")
        if model == "reaction_gibbs":
            product("reaction_enthalpy_rate_kw", "delta_H_kj_per_mol_rxn", "reaction_rate_mol_rxn_s", 1, "DeltaH_rate = DeltaH_reaction × reaction_rate")
            linear({"heat_into_reactor_kw":1, "W_out_kw":-1, "reaction_enthalpy_rate_kw":-1}, "Q_into = W_out + DeltaH_reaction × reaction_rate")
            rule("delta_G_kj_per_mol_rxn", ["delta_H_kj_per_mol_rxn", "reaction_temperature_K", "delta_S_kj_per_mol_rxn_K"],
                 lambda h,t,s: h-t*s, "DeltaG = DeltaH − T × DeltaS at stated reaction conditions")
        if model in {"closed_system", "steady_control_volume"}:
            closed = model == "closed_system"
            heats = resolved.get("heat")
            qkey = "heat_into_system_kj" if closed else "heat_into_cv_kw"
            wkey = "W_out_kj" if closed else "W_out_kw"
            if isinstance(heats, list):
                coefficients = {f"heat.{i}.{qkey}": 1 for i in range(len(heats))}
                coefficients[wkey] = -1
                if closed:
                    coefficients["delta_E_kj"] = -1
                    linear(coefficients, "sum(Q_into) − W_out − DeltaE = 0")
                else:
                    ins, outs = resolved.get("inlets"), resolved.get("outlets")
                    if isinstance(ins, list) and isinstance(outs, list) and ins and outs:
                        mass_terms = {f"{side}.{i}.mass_flow_kg_s": sign for side, streams, sign in
                                      (("inlets", ins, 1), ("outlets", outs, -1)) for i in range(len(streams))}
                        linear(mass_terms, "sum(mass_flow_in) = sum(mass_flow_out)")
                        valid = True
                        for side, streams, sign in (("inlets",ins,1), ("outlets",outs,-1)):
                            for i, stream in enumerate(streams):
                                path = f"{side}.{i}"
                                deps = [path+".mass_flow_kg_s", path+".specific_enthalpy_kj_kg"]
                                kepe = resolved.get("include_kinetic_potential", False)
                                if kepe:
                                    put(path+".g_m_s2", 9.80665, "Standard gravitational acceleration (m/s²)", [])
                                    deps += [path+".velocity_m_s", path+".elevation_m", path+".g_m_s2"]
                                if not all(values.get(k) is not None for k in deps):
                                    valid = False
                                    continue
                                rule(path+".energy_rate_kw", deps,
                                     lambda m,h,*vp: m*(h + (vp[0]**2/2000 + vp[2]*vp[1]/1000 if vp else 0)),
                                     "Energy rate = mass_flow × (h + V²/2000 + g z/1000)")
                                coefficients[path+".energy_rate_kw"] = sign
                        if valid:
                            linear(coefficients, "sum(Q_into) + sum(energy_in) − sum(energy_out) − W_out = 0")
        if model == "property_change":
            if values.get("cp_kj_kg_k") is not None:
                put("cp_model", "constant", "A constant Cp was supplied", ["cp_kj_kg_k"])
            if resolved.get("cp_model") == "constant":
                rule("T2_K", ["T1_K", "delta_h_kj_kg", "cp_kj_kg_k"], lambda t,h,c: t+h/c, "T2 = T1 + DeltaH/Cp")
                rule("T1_K", ["T2_K", "delta_h_kj_kg", "cp_kj_kg_k"], lambda t,h,c: t-h/c, "T1 = T2 − DeltaH/Cp")
                rule("cp_kj_kg_k", ["delta_h_kj_kg", "T1_K", "T2_K"], lambda h,t,u: h/(u-t), "Cp = DeltaH/(T2 − T1)")
        if len(evidence) == count:
            break

    # Redundant efficiency is evidence too; do not ignore contradictory claims.
    if values.get("efficiency_fraction") is not None:
        eta = engine.number(values["efficiency_fraction"], "Efficiency")
        if not 0 <= eta <= 1:
            conflicts.append("Conversion efficiency must be between zero and one.")
        source = "Q_hot_kw" if model == "fixed_reservoir_engine" else "incident_radiation_kw"
        if values.get(source) is not None and values.get("W_out_kw") is not None:
            predicted = eta * engine.number(values[source], source)
            if not engine.is_close_to_zero(predicted-engine.number(values["W_out_kw"], "Work"), predicted):
                conflicts.append("Supplied efficiency contradicts the stated input and output powers.")
    return resolved, evidence, conflicts


def missing_inputs(case, required):
    """Nested physical fields, without requiring irrelevant zero-heat temperatures."""
    missing = [k for k in required if case.get(k) is None]
    model = case["model"]
    if model in {"finite_stream_engine", "finite_stream_heat_recovery"}:
        for side in ("hot_stream", "cold_stream"):
            s = case.get(side)
            if isinstance(s, dict):
                fields = ["mass_flow_kg_s", "T_in_K", "T_out_K", "cp_model"]
                fields += ["a", "b"] if s.get("cp_model") == "linear" else ["cp_kj_kg_k"]
                missing += [f"{side}.{k}" for k in fields if s.get(k) is None]
    if model == "property_change":
        fields = ["a", "b"] if case.get("cp_model") == "linear" else ["cp_kj_kg_k"]
        missing += [k for k in fields if case.get(k) is None]
    if model in {"closed_system", "steady_control_volume"}:
        closed = model == "closed_system"
        for i, heat in enumerate(case.get("heat") or []):
            if not isinstance(heat, dict):
                continue
            key = "heat_into_system_kj" if closed else "heat_into_cv_kw"
            if heat.get(key) is None:
                missing.append(f"heat.{i}.{key}")
            if heat.get(key) != 0 and heat.get("boundary_temperature_K") is None:
                missing.append(f"heat.{i}.boundary_temperature_K")
        if not closed:
            for side in ("inlets", "outlets"):
                for i, stream in enumerate(case.get(side) or []):
                    if not isinstance(stream, dict):
                        continue
                    fields = ["mass_flow_kg_s", "specific_enthalpy_kj_kg", "specific_entropy_kj_kg_k"]
                    if case.get("include_kinetic_potential"):
                        fields += ["velocity_m_s", "elevation_m"]
                    missing += [f"{side}.{i}.{k}" for k in fields if stream.get(k) is None]
    return list(dict.fromkeys(missing))
