"""Regression tests for missing data, redundant evidence and limiting boundaries."""
import copy
import unittest

from thermodynamic_feasibility_tester_MASTER import run_case, ThermoInputError
from website.dist.solver import solve, solve_json


class DerivationTests(unittest.TestCase):
    def test_missing_work_is_derived_and_carnot_still_rejects(self):
        case = dict(model="fixed_reservoir_engine", T_hot_K=600, T_cold_K=300,
                    Q_hot_kw=100, Q_cold_rejected_kw=40)
        original = copy.deepcopy(case)
        result = run_case(case)
        self.assertEqual(case, original)
        self.assertEqual(result.inputs["W_out_kw"], 60)
        self.assertEqual(result.verdict.status, "NOT FEASIBLE")
        row = next(r for r in result.derivation if r["field"] == "W_out_kw")
        self.assertEqual(row["source"], "derived")
        self.assertEqual(row["dependencies"], {"Q_hot_kw":100, "Q_cold_rejected_kw":40})

    def test_zero_work_is_preserved(self):
        result = run_case(dict(model="fixed_reservoir_engine", T_hot_K=600, T_cold_K=300,
                               Q_hot_kw=100, W_out_kw=0))
        self.assertEqual(result.inputs["W_out_kw"], 0)
        self.assertEqual(result.inputs["Q_cold_rejected_kw"], 100)
        self.assertEqual(result.input_status["W_out_kw"], "PROVIDED")

    def test_underdetermined_work_is_not_zero(self):
        result = run_case(dict(model="fixed_reservoir_engine", T_hot_K=600, T_cold_K=300, Q_hot_kw=100))
        self.assertTrue(result.verdict.status.startswith("INSUFFICIENT"))
        self.assertIn("W_out_kw", result.missing_inputs)
        self.assertNotIn("W_out_kw", result.inputs)

    def test_efficiency_chain_and_contradiction(self):
        base = dict(model="fixed_reservoir_engine", T_hot_K=600, T_cold_K=300, Q_hot_kw=100, efficiency_fraction=.3)
        result = run_case(base)
        self.assertEqual(result.inputs["W_out_kw"], 30)
        self.assertEqual(result.inputs["Q_cold_rejected_kw"], 70)
        result = run_case(dict(base, W_out_kw=40))
        self.assertEqual(result.verdict.status, "NOT FEASIBLE")
        self.assertEqual(result.inputs["W_out_kw"], 40)

    def test_carnot_boundary(self):
        for work, expected in ((50, "FEASIBLE"), (50.001, "NOT FEASIBLE")):
            r = run_case(dict(model="fixed_reservoir_engine", T_hot_K=600,T_cold_K=300,Q_hot_kw=100,W_out_kw=work))
            self.assertTrue(r.verdict.status.startswith(expected))

    def streams(self, model="finite_stream_engine"):
        return dict(model=model,
                    hot_stream=dict(mass_flow_kg_s=1, cp_kj_kg_k=4, T_in_K=600, T_out_K=400),
                    cold_stream=dict(mass_flow_kg_s=2, cp_kj_kg_k=4, T_in_K=300), W_out_kw=180)

    def test_stream_chain_resolves_outlet_and_computes_strict_limit(self):
        r = run_case(self.streams())
        self.assertAlmostEqual(r.inputs["cold_stream"]["T_out_K"], 377.5)
        self.assertAlmostEqual(r.calculations["strict_reversible_work_limit_kw"], 800-8*(300*(600/400)**.5-300))
        self.assertTrue(r.verdict.status.startswith("FEASIBLE"))

    def test_linear_cp_outlet_is_unique_positive_cp_root(self):
        case = self.streams()
        case["cold_stream"].update(cp_model="linear", a=3, b=.001)
        case["cold_stream"].pop("cp_kj_kg_k")
        r = run_case(case)
        t = r.inputs["cold_stream"]["T_out_K"]
        self.assertAlmostEqual(2*(3*(t-300)+.001*(t*t-300*300)/2), 620)

    def test_exchanger_catches_hot_outlet_below_cold_inlet(self):
        case = dict(model="finite_stream_heat_recovery",
            hot_stream=dict(mass_flow_kg_s=1,cp_kj_kg_k=1,T_in_K=600,T_out_K=290),
            cold_stream=dict(mass_flow_kg_s=10,cp_kj_kg_k=1,T_in_K=300,T_out_K=331))
        r = run_case(case)
        self.assertTrue(r.verdict.status.startswith("NOT FEASIBLE"))
        self.assertIn("hot outlet", r.checks["terminal_temperature_check"])

    def test_parallel_flow_has_stricter_terminal_condition(self):
        case = dict(model="finite_stream_heat_recovery", flow_arrangement="parallel",
            hot_stream=dict(mass_flow_kg_s=1,cp_kj_kg_k=1,T_in_K=500,T_out_K=350),
            cold_stream=dict(mass_flow_kg_s=1,cp_kj_kg_k=1,T_in_K=300,T_out_K=450))
        self.assertIn("TEMPERATURE CROSS", run_case(case).verdict.status)

    def test_closed_system_derives_work_but_not_entropy(self):
        case = dict(model="closed_system", delta_E_kj=10,delta_S_system_kj_K=.04,
                    heat=[dict(heat_into_system_kj=15,boundary_temperature_K=300)])
        r = run_case(case)
        self.assertEqual(r.inputs["W_out_kj"], 5)
        self.assertEqual(r.verdict.status, "NOT FEASIBLE")
        del case["delta_S_system_kj_K"]
        self.assertIn("delta_S_system_kj_K", run_case(case).missing_inputs)

    def test_cv_requires_enabled_kinetic_potential_data(self):
        case = dict(model="steady_control_volume", heat=[], include_kinetic_potential=True,
            inlets=[dict(mass_flow_kg_s=1,specific_enthalpy_kj_kg=100,specific_entropy_kj_kg_k=1)],
            outlets=[dict(mass_flow_kg_s=1,specific_enthalpy_kj_kg=50,specific_entropy_kj_kg_k=1.1)])
        r = run_case(case)
        self.assertIn("inlets.0.velocity_m_s", r.missing_inputs)
        self.assertTrue(r.verdict.status.startswith("INSUFFICIENT"))

    def test_mechanical_work_from_actual_input_and_measured_heat(self):
        r = run_case(dict(model="mechanical_power",recoverable_mechanical_power_kw=8,
                          heat_rejected_kw=3,heat_rejection_temperature_K=300))
        self.assertEqual(r.inputs["W_out_kw"],5)
        self.assertTrue(r.verdict.status.startswith("FEASIBLE"))

    def test_solar_missing_output_and_supplied_rejection(self):
        base = dict(model="solar_blackbody",T_source_K=6000,T_ambient_K=300,
                    irradiance_W_m2=1000,collection_area_m2=2,heat_rejected_kw=.5)
        self.assertEqual(run_case(base).inputs["W_out_kw"],1.5)
        self.assertEqual(run_case(dict(base,W_out_kw=1)).verdict.status,"NOT FEASIBLE")

    def test_reaction_work_is_derived_then_checked(self):
        r = run_case(dict(model="reaction_gibbs",reaction_direction="A -> B",delta_G_kj_per_mol_rxn=-200,
            delta_H_kj_per_mol_rxn=-250,reaction_rate_mol_rxn_s=.01,reaction_temperature_K=300,
            heat_into_reactor_kw=-.1,heat_boundary_temperature_K=300))
        self.assertAlmostEqual(r.inputs["W_out_kw"],2.4)
        self.assertEqual(r.verdict.status,"NOT FEASIBLE")

    def test_property_change_derives_temperature_without_feasibility_claim(self):
        r = run_case(dict(model="property_change",T1_K=300,delta_h_kj_kg=100,cp_kj_kg_k=2))
        self.assertEqual(r.inputs["T2_K"],350)
        self.assertEqual(r.verdict.status,"PROPERTY CALCULATION ONLY")

    def test_nonfinite_and_boolean_values_are_invalid(self):
        for v in (True,float("nan"),float("inf")):
            with self.subTest(v=v), self.assertRaises(ThermoInputError):
                run_case(dict(model="fixed_reservoir_engine",T_hot_K=600,T_cold_K=300,Q_hot_kw=100,W_out_kw=v))


class ScenarioEvidenceTests(unittest.TestCase):
    def test_missing_work_in_prose(self):
        r = solve({"scenario":"A heat engine receives 100 kW of heat from a source at 600 K and rejects 70 kW to a sink at 300 K."})
        self.assertAlmostEqual(r["result"]["inputs"]["W_out_kw"],30)

    def test_no_unstated_physical_properties_are_used(self):
        for text, missing in (
            ("A wind turbine with 40 m rotor diameter in 10 m/s wind claims 800 kW.","density"),
            ("A heat engine receives 100 kW from a source at 600 K and delivers 30 kW to surroundings.","sink"),
            ("A PV panel of 1.6 m² under 800 W/m² of sunlight produces 350 W.","temperature"),
            ("Waste heat from flue gases at 3 kg/s cooling from 450 °C to 120 °C generates 250 kW. Surroundings at 25 °C.","capacity")):
            with self.subTest(text=text):
                r = solve({"scenario":text})
                self.assertEqual(r["status"],"needs_values")
                self.assertIsNone(r["result"])
                self.assertIn(missing," ".join(r["analysis"]["missing"]).lower())

    def test_json_response_is_serializable_with_missing_inputs(self):
        import json
        r = json.loads(solve_json('{"case":{"model":"fixed_reservoir_engine","T_hot_K":600}}'))
        self.assertIn("Q_hot_kw",r["result"]["missing_inputs"])


if __name__ == "__main__":
    unittest.main()
