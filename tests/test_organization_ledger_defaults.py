"""Defaults pending owner confirmation on the transition-request intake (B1-B9).

Each default is listed in data/organization-role-exemption-register.json
defaults_pending_owner_confirmation with its revert path. The ledger here is
temporary; the published fixture bytes are read, never rewritten.
"""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

import importlib.util

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "transition_request_cases_defaults", ROOT / "tests/test_organization_ledger_transition_requests.py")
_cases = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cases)
LIVE, LIVE_ORG_HEAD, NAMESPACE = _cases.LIVE, _cases.LIVE_ORG_HEAD, _cases.NAMESPACE
LedgerRepo, _EnvCase, _module = _cases.LedgerRepo, _cases._EnvCase, _cases._module
request, tt_basis_digest = _cases.request, _cases.tt_basis_digest
transition = _module("organization_ledger_transition_defaults", "resident-runtime/organization_ledger_transition.py")
readback = _module("organization_ledger_readback_defaults", "resident-runtime/organization_ledger_readback.py")
DECLARATION = ROOT / ".stegverse/declarations/SV011-ORG-STATE-0001.request.json"
EXPECTATION = ROOT / ".stegverse/build-expectations/sv011-first-cycle.json"
REGISTER = ROOT / "data/organization-role-exemption-register.json"
SIX = ("failure_code", "failed_predicate", "required_evidence_or_repair",
       "retry_entrypoint", "owning_existing_goal", "next_attempt")


def refused_predicate(document):
    with unittest.TestCase().assertRaises(transition.TransitionRequestRefused) as caught:
        transition.validate_request(document)
    return caught.exception.failed_predicate


class ConstraintSetB1(unittest.TestCase):
    def test_genesis_node_standing_is_accepted_and_kept(self):
        validated = transition.validate_request(request())
        self.assertEqual(validated["constraint_set"],
                         {"node_standing": {"mode": "ESTABLISH_GENESIS", "node_ref": "unit-test", "predecessor": None}})
        self.assertNotIn("standing", validated)

    def test_legacy_top_level_standing_is_refused(self):
        document = request()
        document["standing"] = {"mode": "ESTABLISH_GENESIS", "node_ref": "unit-test"}
        self.assertEqual(refused_predicate(document), "TRANSITION_REQUEST_STANDING_REPLACED_BY_CONSTRAINT_SET")

    def test_verify_existing_is_fail_closed_on_the_absent_predecessor_binding(self):
        document = request(constraint_set={"node_standing": {
            "mode": "VERIFY_EXISTING", "node_ref": "unit-test",
            "predecessor": {"generation": 1, "manifest_sha256": "a" * 64, "result_sha256": "b" * 64,
                            "heartbeat_epoch": 32}}})
        self.assertEqual(refused_predicate(document), "PREDECESSOR_BINDING_PRIMITIVE_NOT_PRESENT")
        result = transition._refusal(transition.TransitionRequestRefused(
            "PREDECESSOR_BINDING_PRIMITIVE_NOT_PRESENT", "x"), document)
        self.assertEqual(result["disposition"], "FAIL_CLOSED")
        for name in SIX:
            self.assertTrue(result[name], name)

    def test_predecessor_key_and_genesis_null_and_mode_vocabulary(self):
        cases = {
            "NODE_STANDING_PREDECESSOR_KEY_PRESENT": {"mode": "ESTABLISH_GENESIS", "node_ref": "n"},
            "NODE_STANDING_GENESIS_PREDECESSOR_IS_NULL": {"mode": "ESTABLISH_GENESIS", "node_ref": "n",
                                                          "predecessor": {"generation": 1}},
            "NODE_STANDING_MODE_IS_DECLARED": {"mode": "synthetic", "node_ref": "n", "predecessor": None},
            "TRANSITION_REQUEST_FIELD_OUTSIDE_THE_CONTRACT": {"mode": "ESTABLISH_GENESIS", "node_ref": "n",
                                                              "predecessor": None, "extra": 1},
        }
        for predicate, standing in cases.items():
            self.assertEqual(refused_predicate(request(constraint_set={"node_standing": standing})), predicate)

    def test_declared_block_is_recorded_as_declared_only(self):
        base = request()["constraint_set"]
        good = dict(base, declared={"authority": "does_not_satisfy", "time": "satisfies"})
        self.assertEqual(transition.validate_request(request(constraint_set=good))["constraint_set"]["declared"],
                         {"authority": "does_not_satisfy", "time": "satisfies"})
        for bad in ({"authority": "yes"}, {"scope": "satisfies"}, "satisfies"):
            self.assertEqual(refused_predicate(request(constraint_set=dict(base, declared=bad))),
                             "CONSTRAINT_SET_DECLARED_VALUES")

    def test_a_top_level_field_outside_the_contract_is_refused(self):
        self.assertEqual(refused_predicate(request(unexpected=True)), "TRANSITION_REQUEST_FIELD_OUTSIDE_THE_CONTRACT")


class ManifestedRouteB5(unittest.TestCase):
    def test_route_is_required_and_must_equal_the_binding(self):
        missing = request()
        del missing["manifested_route"]
        self.assertEqual(refused_predicate(missing), "REQUEST_NAMES_A_MANIFESTED_CAPABILITY_AND_ROUTE")
        other = copy.deepcopy(request())
        other["manifested_route"]["destination_repository"] = "SV-011/entity"
        self.assertEqual(refused_predicate(other), "ROUTE_NOT_BOUND_IN_THIS_ORGANIZATION")
        extra = copy.deepcopy(request())
        extra["manifested_route"]["host"] = "x"
        self.assertEqual(refused_predicate(extra), "TRANSITION_REQUEST_FIELD_OUTSIDE_THE_CONTRACT")

    def test_binding_is_declared_in_the_ledger_contract(self):
        contract = json.loads((ROOT / ".stegverse/transition-ledger/org-contract.json").read_text(encoding="utf-8"))
        intake = contract["transition_request_intake"]
        self.assertEqual(intake["manifested_route_binding"], transition.bound_route())
        self.assertEqual(intake["manifested_route_binding_status"], "DEFAULT_PENDING_OWNER_CONFIRMATION_B5")
        self.assertEqual(intake["manifested_route_binding"]["destination_ref"], contract["organization_ledger_ref"])


class ObservationSchemaB3(unittest.TestCase):
    def test_readback_uses_its_own_observation_id(self):
        self.assertEqual(readback.OBSERVATION_SCHEMA, "stegverse.organization-ledger-observation/v1")
        source = (ROOT / "resident-runtime/organization_ledger_readback.py").read_text(encoding="utf-8")
        self.assertEqual(source.count('"schema": OBSERVATION_SCHEMA'), 3)
        self.assertNotIn('"schema": "stegverse.organization-ledger-readback/v1"', source)


class FirstCurrentStateB2(_EnvCase):
    def declaration(self):
        return json.loads(DECLARATION.read_text(encoding="utf-8"))

    def test_declaration_sits_outside_the_intake_and_validates(self):
        self.assertFalse(list((ROOT / ".stegverse/transition-requests").glob("*.json")))
        document = self.declaration()
        validated = transition.validate_request(document)
        state = validated["current_state"]
        self.assertEqual(state["state_id"], "SV011-ORG-STATE-0001")
        self.assertEqual(state["declared_transition_element"],
                         {"transition_id": "T-181", "fixture_ref": "fixtures/transition-elements/t-181.receipt-record.json"})
        self.assertEqual(state["declared_role_escalation"],
                         {"role_id": "sv011.context-to-evidence", "from_role": "context", "to_role": "evidence"})
        self.assertEqual(state["satisfied_scope_ids"], [])
        self.assertEqual(document["predecessor_state_sha256"], LIVE_ORG_HEAD)
        self.assertEqual(document["successor_state_sha256"], "sha256:" + tt_basis_digest(state))
        self.assertIn("DEFAULT pending owner confirmation", document["purpose"])

    def test_freshness_window_is_seven_days(self):
        from datetime import datetime, timedelta
        state = self.declaration()["current_state"]
        as_of = datetime.strptime(state["as_of"], "%Y-%m-%dT%H:%M:%SZ")
        fresh = datetime.strptime(state["fresh_until"], "%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(fresh - as_of, timedelta(days=7))

    def test_applied_to_the_published_ledger_it_meets_the_build_expectation(self):
        """B2 and B9 together, on a temporary copy of the published ref."""
        ledger = LedgerRepo(self.base, seed=LIVE)
        self.bind(ledger)
        result = transition.apply_request(self.declaration())
        self.assertEqual(result["disposition"], "ALLOW")
        code, report = self.readback_cli(ledger.org, transition_result=result)
        self.assertEqual(code, 0, report)
        self.assertEqual(report["schema"], "stegverse.organization-ledger-observation/v1")
        expected = json.loads(EXPECTATION.read_text(encoding="utf-8"))["expected"]
        self.assertEqual(report["chain_length"], 1 + expected["organization_receipts_added"])
        self.assertIs(report["basis_established"], expected["basis_established"])
        self.assertEqual(report["current_state_reason"], expected["current_state_reason"])
        state = report["current_state"]
        self.assertEqual(state["state_id"], expected["head_current_state_state_id"])
        self.assertEqual(state["declared_transition_element"]["transition_id"],
                         expected["head_current_state_transition_id"])
        self.assertEqual(state["basis_digest"], expected["head_current_state_basis_digest"])
        head = json.loads(ledger.show("receipts/" + result["organization_receipt_sha256"].split(":")[1] + ".json"))
        self.assertEqual(head["previous_receipt_sha256"], expected["head_previous_receipt_sha256"])
        self.assertEqual(head["evidence"]["manifested_route"], transition.bound_route())
        repo_receipt = json.loads(ledger.show(
            NAMESPACE + "/receipts/" + result["repository_receipt_sha256"].split(":")[1] + ".json"))
        self.assertEqual(repo_receipt["evidence"]["constraint_set"]["node_standing"]["mode"], "ESTABLISH_GENESIS")
        # The published receipt is unchanged and still readable.
        self.assertEqual(ledger.show("receipts/" + LIVE_ORG_HEAD.split(":")[1] + ".json"),
                         (LIVE / ("receipts/" + LIVE_ORG_HEAD.split(":")[1] + ".json")).read_bytes())


class BuildExpectationB9(unittest.TestCase):
    def test_artifact_is_declaration_only_with_six_field_open_legs(self):
        doc = json.loads(EXPECTATION.read_text(encoding="utf-8"))
        self.assertEqual(doc["status"], "DEFAULT_PENDING_OWNER_CONFIRMATION_B9")
        self.assertEqual(doc["authority_effect"], "NONE_DECLARATION_ONLY")
        self.assertEqual(doc["applies_to_request_id"], json.loads(DECLARATION.read_text(encoding="utf-8"))["request_id"])
        self.assertEqual(doc["delta_definition"]["entry_tags"], ["VERIFIED_NULL", "OBSERVED_NO_CHANGE", "UNOBSERVED"])
        self.assertIs(doc["delta_definition"]["empty_delta_is_a_result"], False)
        for leg in (doc["delta_computation"], doc["return_path"]["sdk_leg"]):
            self.assertEqual(leg["disposition"], "FAIL_CLOSED")
            for name in SIX:
                self.assertTrue(leg[name], name)


class DefaultsAreListed(unittest.TestCase):
    def test_each_applied_default_is_listed_with_a_revert_path(self):
        register = json.loads(REGISTER.read_text(encoding="utf-8"))
        self.assertEqual(register["defaults_status"], "DEFAULT_PENDING_OWNER_CONFIRMATION")
        listed = {entry["item"]: entry for entry in register["defaults_pending_owner_confirmation"]}
        self.assertEqual(set(listed), {"B1", "B2", "B3", "B5", "B9"})
        for entry in listed.values():
            self.assertTrue(entry["revert"])
            for surface in entry["surfaces"]:
                self.assertTrue((ROOT / surface.split("::")[0].split(" ")[0]).exists(), surface)
        self.assertNotIn("N11", {entry["audit_item"] for entry in register["exemptions"]})
        doc = (ROOT / "docs/ORGANIZATION_ROLE_CONFORMANCE.md").read_text(encoding="utf-8")
        self.assertIn("DEFAULT pending owner confirmation", doc)

    def test_new_files_use_constraint_wording(self):
        needles = ("g" + "rant", "authori" + "z", "per" + "mit", "allow" + "ed", "dele" + "gat", " m" + "ay ")
        for path in (DECLARATION, EXPECTATION, Path(__file__), ROOT / ".stegverse/declarations/README.md"):
            text = path.read_text(encoding="utf-8").lower()
            for needle in needles:
                self.assertNotIn(needle, text, path.name + ": " + needle.strip())


if __name__ == "__main__":
    unittest.main()
