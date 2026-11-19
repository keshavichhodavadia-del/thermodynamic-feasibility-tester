import unittest

from thermodynamic_feasibility_tester_MASTER import ThermoInputError, run_case


class ResourceModelTests(unittest.TestCase):
    def test_mechanical_requires_entropy_boundary_when_heat_is_rejected(self):
        case = {"model": "mechanical_power", "recoverable_mechanical_power_kw": .008, "W_out_kw": .005}
        incomplete = run_case(case)
        self.assertEqual(incomplete.verdict.status, "INSUFFICIENT DATA / MODEL REQUIRED")
        self.assertEqual(incomplete.checks["first_law_balance"], "NOT EVALUATED — measured heat rejection required")
        heat_only = run_case(dict(case, heat_rejected_kw=.003))
        self.assertEqual(heat_only.verdict.status, "INSUFFICIENT DATA / MODEL REQUIRED")
        self.assertEqual(heat_only.checks["first_law_balance"], "PASS")
        self.assertEqual(heat_only.checks["entropy_balance"], "NOT EVALUATED — boundary temperature required")
        complete = run_case(dict(case, heat_rejected_kw=.003, heat_rejection_temperature_K=300))
        self.assertEqual(complete.verdict.status, "FEASIBLE / NOT REJECTED")
        self.assertAlmostEqual(complete.calculations["heat_rejected_kw"], .003)
        self.assertAlmostEqual(complete.calculations["S_gen_dot_kw_per_k"], .00001)

    def test_mechanical_energy_balance_is_independently_checked(self):
        case = {"model": "mechanical_power", "recoverable_mechanical_power_kw": .008,
                "W_out_kw": .005, "heat_rejected_kw": .004, "heat_rejection_temperature_K": 300}
        result = run_case(case)
        self.assertEqual(result.verdict.status, "NOT FEASIBLE")
        self.assertEqual(result.checks["first_law_balance"], "FAIL")
        self.assertNotIn("S_gen_dot_kw_per_k", result.calculations)

    def test_mechanical_claim_above_input_is_rejected(self):
        result = run_case({"model": "mechanical_power", "recoverable_mechanical_power_kw": .008,
                           "W_out_kw": .009, "heat_rejection_temperature_K": 300})
        self.assertEqual(result.verdict.status, "NOT FEASIBLE")

    def test_model3_flow_exergy_limit(self):
        stream_in = {"mass_flow_kg_s": 1, "specific_enthalpy_kj_kg": 3231.7,
                     "specific_entropy_kj_kg_k": 6.9235,
                     "specific_enthalpy_dead_state_kj_kg": 104.9,
                     "specific_entropy_dead_state_kj_kg_k": .367}
        stream_out = {"mass_flow_kg_s": 1, "specific_enthalpy_kj_kg": 2675,
                      "specific_entropy_kj_kg_k": 7.3589,
                      "specific_enthalpy_dead_state_kj_kg": 104.9,
                      "specific_entropy_dead_state_kj_kg_k": .367}
        case = {"model": "steady_control_volume", "inlets": [stream_in], "outlets": [stream_out],
                "heat": [], "W_out_kw": 556.7, "T0_K": 298.15}
        result = run_case(case)
        self.assertEqual(result.verdict.status, "FEASIBLE / NOT REJECTED")
        self.assertAlmostEqual(result.calculations["reversible_shaft_work_limit_kw"],
                               3231.7 - 2675 + 298.15 * (7.3589 - 6.9235))
        self.assertAlmostEqual(result.calculations["exergy_destruction_kw"],
                               298.15 * result.calculations["S_gen_dot_kw_per_k"])

    def test_model3_without_dead_state_data_is_not_certified(self):
        case = {"model": "steady_control_volume",
                "inlets": [{"mass_flow_kg_s": 1, "specific_enthalpy_kj_kg": 100,
                            "specific_entropy_kj_kg_k": 1}],
                "outlets": [{"mass_flow_kg_s": 1, "specific_enthalpy_kj_kg": 90,
                             "specific_entropy_kj_kg_k": 1.1}], "heat": [], "W_out_kw": 10}
        result = run_case(case)
        self.assertEqual(result.verdict.status, "INSUFFICIENT DATA / MODEL REQUIRED")
        self.assertTrue(result.checks["first_law"]["passed"])
        self.assertFalse(result.checks["reversible_work_limit"])

    def test_model3_rejects_claim_above_reversible_limit(self):
        stream_in = {"mass_flow_kg_s": 1, "specific_enthalpy_kj_kg": 100,
                     "specific_entropy_kj_kg_k": 1, "specific_enthalpy_dead_state_kj_kg": 0,
                     "specific_entropy_dead_state_kj_kg_k": 0}
        stream_out = {"mass_flow_kg_s": 1, "specific_enthalpy_kj_kg": 90,
                      "specific_entropy_kj_kg_k": 1.1, "specific_enthalpy_dead_state_kj_kg": 0,
                      "specific_entropy_dead_state_kj_kg_k": 0}
        result = run_case({"model": "steady_control_volume", "inlets": [stream_in], "outlets": [stream_out],
                           "heat": [], "W_out_kw": 11, "T0_K": 300})
        self.assertEqual(result.verdict.status, "NOT FEASIBLE")

    def test_model4_nonflow_exergy_limit(self):
        case = {"model": "closed_system", "delta_E_kj": 10, "delta_S_system_kj_K": .06,
                "heat": [{"heat_into_system_kj": 15, "boundary_temperature_K": 300}],
                "W_out_kj": 5, "T0_K": 300, "environment_pressure_kPa": 101.325, "delta_V_m3": 0}
        result = run_case(case)
        self.assertEqual(result.verdict.status, "FEASIBLE / NOT REJECTED")
        self.assertAlmostEqual(result.calculations["reversible_useful_work_limit_kj"], 8)
        self.assertAlmostEqual(result.calculations["useful_work_out_kj"], 5)
        self.assertAlmostEqual(result.calculations["exergy_destruction_kj"], 3)

    def test_model4_missing_reference_environment_stays_insufficient(self):
        case = {"model": "closed_system", "delta_E_kj": 10, "delta_S_system_kj_K": .06,
                "heat": [{"heat_into_system_kj": 15, "boundary_temperature_K": 300}], "W_out_kj": 5}
        result = run_case(case)
        self.assertEqual(result.verdict.status, "INSUFFICIENT DATA / MODEL REQUIRED")
        self.assertTrue(result.checks["first_law"]["passed"])

    def test_solar_model_closes_energy_and_entropy_balances(self):
        case = {"model": "solar_blackbody", "T_source_K": 600, "T_ambient_K": 300,
                "irradiance_W_m2": 1000, "collection_area_m2": 2, "W_out_kw": .7}
        result = run_case(case)
        self.assertAlmostEqual(result.calculations["incident_radiation_kw"], 2)
        self.assertAlmostEqual(result.calculations["reversible_power_limit_kw"], 17 / 24)
        self.assertEqual(result.checks["first_law_balance"], "PASS")
        self.assertEqual(result.checks["entropy_balance"], "PASS")
        self.assertEqual(result.verdict.status, "FEASIBLE / NOT REJECTED")
        self.assertEqual(run_case(dict(case, W_out_kw=.75)).verdict.status, "NOT FEASIBLE")

    def test_electrochemical_model_closes_energy_entropy_and_gibbs_limit(self):
        case = {"model": "reaction_gibbs", "reaction_direction": "H2 + 1/2 O2 -> H2O(l)",
                "delta_G_kj_per_mol_rxn": -237.13, "delta_H_kj_per_mol_rxn": -285.83,
                "reaction_rate_mol_rxn_s": .01, "reaction_temperature_K": 298.15,
                "W_out_kw": 1.5, "heat_into_reactor_kw": -1.3583, "heat_boundary_temperature_K": 298.15}
        result = run_case(case)
        self.assertEqual(result.verdict.status, "FEASIBLE / NOT REJECTED")
        self.assertEqual(result.checks["first_law_balance"], "PASS")
        self.assertEqual(result.checks["entropy_balance"], "PASS")
        self.assertAlmostEqual(result.calculations["reversible_power_limit_kw"], 2.3713)
        self.assertAlmostEqual(result.calculations["first_law_residual_kw"], 0)

    def test_electrochemical_claim_above_gibbs_limit_is_rejected(self):
        case = {"model": "reaction_gibbs", "reaction_direction": "A to B", "delta_G_kj_per_mol_rxn": -200,
                "delta_H_kj_per_mol_rxn": -250, "reaction_rate_mol_rxn_s": .01,
                "reaction_temperature_K": 300, "W_out_kw": 2.1,
                "heat_into_reactor_kw": -.4, "heat_boundary_temperature_K": 300}
        result = run_case(case)
        self.assertEqual(result.verdict.status, "NOT FEASIBLE")

    def test_gibbs_model_requires_isothermal_heat_boundary(self):
        case = {"model": "reaction_gibbs", "reaction_direction": "A to B", "delta_G_kj_per_mol_rxn": -200,
                "delta_H_kj_per_mol_rxn": -250, "reaction_rate_mol_rxn_s": .01,
                "reaction_temperature_K": 300, "W_out_kw": 1,
                "heat_into_reactor_kw": -.5, "heat_boundary_temperature_K": 290}
        with self.assertRaises(ThermoInputError):
            run_case(case)

    def test_invalid_resource_values(self):
        invalid_cases = [
            {"model": "mechanical_power", "recoverable_mechanical_power_kw": -.01, "W_out_kw": 0},
            {"model": "mechanical_power", "recoverable_mechanical_power_kw": True, "W_out_kw": 0},
            {"model": "mechanical_power", "recoverable_mechanical_power_kw": float("inf"), "W_out_kw": 0},
            {"model": "solar_blackbody", "T_source_K": 299, "T_ambient_K": 300,
             "irradiance_W_m2": 1000, "collection_area_m2": 2, "W_out_kw": .1},
            {"model": "reaction_gibbs", "reaction_direction": " ", "delta_G_kj_per_mol_rxn": -200,
             "reaction_rate_mol_rxn_s": .01, "W_out_kw": 1},
        ]
        for case in invalid_cases:
            with self.subTest(case=case), self.assertRaises(ThermoInputError):
                run_case(case)


if __name__ == "__main__":
    unittest.main()
