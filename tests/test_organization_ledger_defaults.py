"""Defaults pending owner confirmation on the transition-request intake (B1-B9).

Each default is listed in data/organization-role-exemption-register.json
defaults_pending_owner_confirmation with its revert path. The ledger here is
temporary; the published fixture bytes are read, never rewritten.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

import importlib.util

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "transition_request_cases_defaults", ROOT / "tests/test_organization_ledger_transition_requests.py")
_cases = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cases)
LIVE, LIVE_ORG_HEAD, NAMESPACE = _cases.LIVE, _cases.LIVE_ORG_HEAD, _cases.NAMESPACE
LIVE_REPO_HEAD = _cases.LIVE_REPO_HEAD
LedgerRepo, _EnvCase, _module = _cases.LedgerRepo, _cases._EnvCase, _cases._module
request, tt_basis_digest = _cases.request, _cases.tt_basis_digest
segmentless_contract, live_ledger = _cases.segmentless_contract, _cases.live_ledger
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
        self.assertEqual(source.count('"schema": OBSERVATION_SCHEMA'), 2)
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
        # The declared legacy segment keeps the whole-ledger read FAIL_CLOSED (G4, C6 c).
        self.assertEqual(code, 1, report)
        self.assertEqual(report["disposition"], "FAIL_CLOSED")
        self.assertEqual(report["failure_code"], "DECLARED_LEGACY_SEGMENT_NOT_SOURCE_RETAINED")
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
        self.assertEqual(head["source_transition_sha256"], head["repo_receipt_sha256"])
        retained = json.loads(ledger.show("source-receipts/" + result["repository_receipt_sha256"].split(":")[1] + ".json"))
        self.assertEqual(retained, repo_receipt)
        retention = report["source_receipt_retention"]
        self.assertEqual(retention["retained"], [result["organization_receipt_sha256"]])
        self.assertEqual(retention["legacy_pre_retention"], expected["source_receipt_retention_legacy_pre_retention"])
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
        self.assertEqual(set(listed), {"B1", "B2", "B3", "B5", "B9", "H45", "4b-a"})
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


class SourceReceiptRetentionH45(_EnvCase):
    """H45 (P1 N5): forward retention under source-receipts/; the pre-retention segment is legacy."""

    def test_live_ledger_reports_its_receipt_as_legacy_with_six_fields(self):
        ledger = LedgerRepo(self.base, seed=LIVE)
        code, report = self.readback_cli(ledger.org)
        self.assertEqual(code, 1, report)
        retention = report["source_receipt_retention"]
        self.assertEqual(retention["legacy_pre_retention"], [LIVE_ORG_HEAD])
        self.assertEqual(retention["retained"], [])
        self.assertIs(retention["every_source_receipt_retained_and_verifies"], False)
        self.assertIs(retention["legacy_receipts_re_minted"], False)
        self.assertEqual(retention["legacy_segment"]["ledger_tip_commit"], "2cc58cec07117909750a760933641725b86a204b")
        self.assertIs(retention["legacy_segment"]["ledger_tip_commit_on_ref"], True)
        self.assertNotIn("legacy_segment_finding", retention)
        self.assertEqual(report["failure_code"], "DECLARED_LEGACY_SEGMENT_NOT_SOURCE_RETAINED")
        self.assertEqual(report["failed_predicate"], "SOURCE_RECEIPT_RETAINED_AND_VERIFIES")
        self.assertEqual(report["disposition"], "FAIL_CLOSED")
        for name in SIX:
            self.assertTrue(report[name], name)

    def test_a_ledger_from_genesis_retains_every_source_receipt(self):
        ledger = LedgerRepo(self.base)
        self.bind(ledger)
        result = transition.apply_request(request())
        code, report = self.readback_cli(ledger.org, transition_result=result,
                                         org_contract=segmentless_contract(self.base))
        self.assertEqual(code, 0, report)
        self.assertNotIn("disposition", report)
        retention = report["source_receipt_retention"]
        self.assertIs(retention["every_source_receipt_retained_and_verifies"], True)
        self.assertIsNone(retention["legacy_segment"])
        self.assertEqual(retention["retained"], [result["organization_receipt_sha256"]])

    def _chain(self):
        repo = {"schema": "stegverse.repo-transition-receipt/v1", "n": 1}
        repo["receipt_sha256"] = readback._body_sha(repo)
        org = {"repo_receipt_sha256": repo["receipt_sha256"], "source_transition_sha256": repo["receipt_sha256"]}
        org["receipt_sha256"] = readback._body_sha(org)
        return repo, org

    def test_a_receipt_without_its_source_is_refused_when_no_segment_is_declared(self):
        rule = json.loads(segmentless_contract(self.base).read_text(encoding="utf-8"))
        repo, org = self._chain()
        stores = {
            "missing": {},
            "different": {"source-receipts/" + repo["receipt_sha256"][7:] + ".json": dict(repo, n=2)},
        }
        for name, documents in stores.items():
            with self.assertRaises(readback.ReadbackRefused, msg=name) as caught:
                readback.source_receipt_retention(documents, [org], [repo], rule)
            self.assertEqual(caught.exception.failed_predicate, "SOURCE_RECEIPT_RETAINED_AND_VERIFIES")
        undeclared = {key: value for key, value in org.items() if key != "source_transition_sha256"}
        with self.assertRaises(readback.ReadbackRefused):
            readback.source_receipt_retention({}, [undeclared], [repo], rule)
        held = {"source-receipts/" + repo["receipt_sha256"][7:] + ".json": repo}
        self.assertEqual(readback.source_receipt_retention(held, [org], [repo], rule)["retained"],
                         [org["receipt_sha256"]])

    def test_item_4b_the_published_receipt_names_no_source_and_the_ref_retains_none(self):
        """Item 4b (H51): the cause of SOURCE_RECEIPT_RETAINED_AND_VERIFIES at tip 2cc58cec.

        The shared readback (StegVerse-Labs/.github resident-runtime/organization_ledger_readback.py)
        reads ``source_transition_sha256`` and ``source-receipts/<hex>.json``. Receipt 3db9008d
        was minted before forward retention: it names its source only as ``repo_receipt_sha256``
        (f8134042, which is on the repository chain and self-verifies), so the shared read finds
        ``source_transition_sha256`` absent (detail None). It is never re-minted.
        """
        org = json.loads((LIVE / ("receipts/" + LIVE_ORG_HEAD.split(":")[1] + ".json")).read_text(encoding="utf-8"))
        self.assertNotIn("source_transition_sha256", org)
        self.assertEqual(org["repo_receipt_sha256"], LIVE_REPO_HEAD)
        self.assertFalse((LIVE / "source-receipts").exists())
        repo = json.loads((LIVE / (NAMESPACE + "/receipts/" + LIVE_REPO_HEAD.split(":")[1] + ".json"))
                          .read_text(encoding="utf-8"))
        self.assertEqual(repo["receipt_sha256"], readback._body_sha(repo))

    def test_item_4b_a_forward_receipt_on_the_published_ledger_meets_the_shared_rule(self):
        """A receipt appended after 2cc58cec names its source and retains it at the shared key."""
        ledger = LedgerRepo(self.base, seed=LIVE)
        self.bind(ledger)
        result = transition.apply_request(request())
        head = json.loads(ledger.show("receipts/" + result["organization_receipt_sha256"].split(":")[1] + ".json"))
        source = head["source_transition_sha256"]
        self.assertEqual(source, result["repository_receipt_sha256"])
        retained = json.loads(ledger.show("source-receipts/" + source.split(":")[1] + ".json"))
        self.assertEqual(retained["receipt_sha256"], source)
        self.assertEqual(readback._body_sha(retained), source)
        self.assertEqual(head["previous_receipt_sha256"], LIVE_ORG_HEAD)

    def test_contract_names_the_segment_and_the_canonical_layout(self):
        org = json.loads((ROOT / ".stegverse/transition-ledger/org-contract.json").read_text(encoding="utf-8"))
        rule = org["source_receipt_retention"]
        self.assertEqual(rule["status"], "DEFAULT_PENDING_OWNER_CONFIRMATION_H45")
        self.assertEqual(rule["prefix"], "source-receipts/")
        self.assertEqual(rule["receipt_field"], "source_transition_sha256")
        self.assertEqual(rule["legacy_pre_retention_segment"]["receipts"], [LIVE_ORG_HEAD])
        self.assertIs(rule["legacy_pre_retention_segment"]["re_minted"], False)
        self.assertNotIn("source_transition_sha256", org["required_fields"])


class LegacySegmentDeclarationG4(_EnvCase):
    """G4/H45: the legacy-segment declaration follows the shared readback C6 rule."""

    def contract(self):
        return json.loads((ROOT / ".stegverse/transition-ledger/org-contract.json").read_text(encoding="utf-8"))

    def segment(self):
        return copy.deepcopy(self.contract()["source_receipt_retention"]["legacy_pre_retention_segment"])

    def refused_code(self, segment):
        with self.assertRaises(readback.ReadbackRefused) as caught:
            readback.validate_legacy_segment(segment)
        return caught.exception.failure_code

    def rebind(self, segment):
        segment.pop("declaration_sha256", None)
        segment["declaration_sha256"] = readback.legacy_declaration_digest(segment)
        return segment

    def test_declaration_digest_is_the_c6_digest(self):
        segment = self.segment()
        self.assertEqual(segment["declaration_sha256"],
                         "sha256:36c43b78e2c820d5448d7edb7f4bf7222d22faf9a91925ad18e3e50a54267375")
        self.assertEqual(readback.legacy_declaration_digest(segment), segment["declaration_sha256"])
        body = {key: value for key, value in segment.items() if key != "declaration_sha256"}
        text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        self.assertEqual("sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest(), segment["declaration_sha256"])
        escaped = json.dumps(body, sort_keys=True, separators=(",", ":"))
        self.assertNotEqual("sha256:" + hashlib.sha256(escaped.encode("utf-8")).hexdigest(),
                            segment["declaration_sha256"])
        self.assertIs(readback.validate_legacy_segment(segment), segment)

    def test_six_fields_are_the_h45_record(self):
        six = self.segment()["six_fields"]
        self.assertEqual(list(six), list(SIX))
        self.assertEqual(six["failure_code"], "SOURCE_RECEIPT_RETAINED_AND_VERIFIES")
        self.assertEqual(six["owning_existing_goal"], "SV011-TT-CAPABILITY-MAP-OBSERVATION-001 (#167 lane B, #68)")
        self.assertEqual(self.segment()["waived_predicate"], "SOURCE_RECEIPT_RETAINED_AND_VERIFIES")

    def test_the_pr9_shape_without_the_three_members_is_incomplete(self):
        segment = self.segment()
        for name in ("waived_predicate", "six_fields", "declaration_sha256"):
            segment.pop(name)
        self.assertEqual(self.refused_code(segment), "LEGACY_SEGMENT_DECLARATION_INCOMPLETE")
        for name in readback.LEGACY_SEGMENT_MEMBERS:
            partial = self.segment()
            partial.pop(name)
            self.assertEqual(self.refused_code(partial), "LEGACY_SEGMENT_DECLARATION_INCOMPLETE", name)

    def test_malformed_members_and_waivers_are_refused(self):
        cases = {
            "LEGACY_SEGMENT_WAIVES_SELF_HASH_OR_LINKAGE": ("waived_predicate", "ORGANIZATION_RECEIPT_VERIFIES_AGAINST_ITS_BODY"),
            "LEGACY_SEGMENT_WAIVES_A_NON_WAIVABLE_PREDICATE": ("waived_predicate", "RECEIPT_HAS_THE_CONTRACT_FIELDS"),
        }
        for code, (name, value) in cases.items():
            segment = self.segment()
            segment[name] = value
            self.assertEqual(self.refused_code(self.rebind(segment)), code)
        invalid = [("receipts", []), ("receipts", ["sha256:xyz"]), ("re_minted", True),
                   ("reported_as_source_retained", True), ("ledger_tip_commit", "2cc58cec")]
        for name, value in invalid:
            segment = self.segment()
            segment[name] = value
            self.assertEqual(self.refused_code(self.rebind(segment)), "LEGACY_SEGMENT_DECLARATION_INVALID", name)
        for field in SIX:
            segment = self.segment()
            segment["six_fields"][field] = " "
            self.assertEqual(self.refused_code(self.rebind(segment)), "LEGACY_SEGMENT_DECLARATION_INVALID", field)
        segment = self.segment()
        segment["six_fields"].pop("next_attempt")
        self.assertEqual(self.refused_code(self.rebind(segment)), "LEGACY_SEGMENT_DECLARATION_INVALID")

    def test_an_edit_after_digesting_is_refused(self):
        segment = self.segment()
        segment["verified_by"] = segment["verified_by"] + " (edited)"
        self.assertEqual(self.refused_code(segment), "LEGACY_SEGMENT_DECLARATION_DIGEST_MISMATCH")

    def test_the_tip_commit_is_bound_to_the_published_ref(self):
        ledger = LedgerRepo(self.base, seed=LIVE)
        code, report = self.readback_cli(ledger.org)
        self.assertEqual(code, 1, report)
        self.assertEqual(report["failure_code"], "DECLARED_LEGACY_SEGMENT_NOT_SOURCE_RETAINED")
        segment = report["source_receipt_retention"]["legacy_segment"]
        self.assertIs(segment["ledger_tip_commit_on_ref"], True)
        self.assertEqual(segment["declaration_digest_canonicalization"], readback.CANONICAL_JSON)
        self.assertEqual(segment["ledger_tip_commit"], _cases.LIVE_TIP_COMMIT)
        self.assertEqual([finding["receipt_sha256"] for finding in segment["findings"]], [LIVE_ORG_HEAD])
        for finding in segment["findings"]:
            self.assertIs(finding["self_hash_verified"], True)
            self.assertIs(finding["source_receipt_retained"], False)
        # The same bytes under a commit that is not 2cc58cec do not carry the declaration.
        copy_dir = self.base / "same-bytes"
        shutil.copytree(LIVE, copy_dir)
        (self.base / "other").mkdir()
        other = LedgerRepo(self.base / "other", seed=copy_dir)
        code, refused = self.readback_cli(other.org)
        self.assertEqual(code, 1)
        self.assertEqual(refused["failure_code"], "LEGACY_SEGMENT_TIP_COMMIT_NOT_ON_THE_LEDGER_REF")
        for name in SIX:
            self.assertTrue(refused[name], name)

    def test_a_directory_copy_is_refused_as_unverifiable(self):
        """C6 convergence (a), DEFAULT: a live copy is read only through git (a clone or the bundle)."""
        with self.assertRaises(readback.ReadbackRefused) as caught:
            readback.readback(str(LIVE), None, NAMESPACE)
        self.assertEqual(caught.exception.failure_code, "LEGACY_SEGMENT_TIP_COMMIT_UNVERIFIABLE")
        code, refused = self.readback_cli(str(LIVE))
        self.assertEqual(code, 1)
        self.assertEqual(refused["disposition"], "FAIL_CLOSED")
        self.assertEqual(refused["failure_code"], "LEGACY_SEGMENT_TIP_COMMIT_UNVERIFIABLE")
        for name in SIX:
            self.assertTrue(refused[name], name)
        self.assertEqual(readback.readback(live_ledger(self.base).org, None, NAMESPACE)["failure_code"],
                         "DECLARED_LEGACY_SEGMENT_NOT_SOURCE_RETAINED")

    def test_a_ledger_without_the_declared_receipts_is_refused(self):
        """C6 convergence (b), DEFAULT: a declared segment always applies; none of it in the chain refuses."""
        ledger = LedgerRepo(self.base)
        self.bind(ledger)
        result = transition.apply_request(request())
        code, refused = self.readback_cli(ledger.org, transition_result=result)
        self.assertEqual(code, 1, refused)
        self.assertEqual(refused["disposition"], "FAIL_CLOSED")
        self.assertEqual(refused["failure_code"], "LEGACY_SEGMENT_NAMES_A_RECEIPT_NOT_IN_THE_CHAIN")
        self.assertIn(LIVE_ORG_HEAD, refused["required_evidence_or_repair"])
        code, report = self.readback_cli(ledger.org, transition_result=result,
                                         org_contract=segmentless_contract(self.base))
        self.assertEqual(code, 0, report)
        self.assertNotIn("disposition", report)

    def synthetic(self):
        repo = {"schema": "stegverse.repo-transition-receipt/v1", "n": 1}
        repo["receipt_sha256"] = readback._body_sha(repo)
        first = {"repo_receipt_sha256": repo["receipt_sha256"], "previous_receipt_sha256": None}
        first["receipt_sha256"] = readback._body_sha(first)
        second = {"repo_receipt_sha256": repo["receipt_sha256"], "source_transition_sha256": repo["receipt_sha256"],
                  "previous_receipt_sha256": first["receipt_sha256"]}
        second["receipt_sha256"] = readback._body_sha(second)
        return repo, first, second

    def rule_for(self, receipts, tip=None):
        segment = self.segment()
        segment["receipts"] = receipts
        if tip is not None:
            segment["ledger_tip_commit"] = tip
        return {"source_receipt_retention": {"legacy_pre_retention_segment": self.rebind(segment)}}

    def synthetic_ledger(self, head):
        """A git ledger whose only commit has HEAD.json naming ``head``: a segment can end there."""
        tree = self.base / "synthetic-tree"
        tree.mkdir()
        (tree / "HEAD.json").write_text(json.dumps({"receipt_sha256": head}), encoding="utf-8")
        root = self.base / "synthetic"
        root.mkdir()
        return LedgerRepo(root, seed=tree)

    def test_prefix_and_overlap_follow_c6(self):
        repo, first, second = self.synthetic()
        held = {"source-receipts/" + repo["receipt_sha256"][7:] + ".json": repo}
        live = live_ledger(self.base).org
        cases = {
            "LEGACY_SEGMENT_NOT_A_CONTIGUOUS_PREFIX": [second["receipt_sha256"]],
            "LEGACY_SEGMENT_OVERLAPS_RETENTION": [first["receipt_sha256"], second["receipt_sha256"]],
            "LEGACY_SEGMENT_NAMES_A_RECEIPT_NOT_IN_THE_CHAIN": [first["receipt_sha256"], "sha256:" + "9" * 64],
        }
        for code, receipts in cases.items():
            with self.assertRaises(readback.ReadbackRefused, msg=code) as caught:
                readback.source_receipt_retention(held, [first, second], [repo], self.rule_for(receipts), live)
            self.assertEqual(caught.exception.failure_code, code)
        ledger = self.synthetic_ledger(first["receipt_sha256"])
        rule = self.rule_for([first["receipt_sha256"]], tip=ledger.tip())
        report = readback.source_receipt_retention(held, [first, second], [repo], rule, ledger.org)
        self.assertEqual(report["legacy_pre_retention"], [first["receipt_sha256"]])
        self.assertEqual(report["legacy_segment"]["fully_verified_receipts"], [second["receipt_sha256"]])
        self.assertEqual(report["legacy_segment_finding"]["failure_code"], "DECLARED_LEGACY_SEGMENT_NOT_SOURCE_RETAINED")
        with self.assertRaises(readback.ReadbackRefused) as caught:
            readback.source_receipt_retention({}, [first, second], [repo], rule, ledger.org)
        self.assertEqual(caught.exception.failure_code, "UNDECLARED_SOURCE_RECEIPT_RETENTION_FAILURE")
        with self.assertRaises(readback.ReadbackRefused) as caught:
            readback.source_receipt_retention(held, [first, second], [repo], rule, str(self.base))
        self.assertEqual(caught.exception.failure_code, "LEGACY_SEGMENT_TIP_COMMIT_UNVERIFIABLE")

    def test_the_legacy_segment_is_never_allow(self):
        source = (ROOT / "resident-runtime/organization_ledger_readback.py").read_text(encoding="utf-8")
        self.assertNotIn('"ALLOW"', source)
        self.assertIn('"disposition": "FAIL_CLOSED"', source)

    def test_the_workflow_holds_the_readback_fail_closed(self):
        workflow = (ROOT / ".github/workflows/organization-ledger-transition.yml").read_text(encoding="utf-8")
        self.assertEqual(workflow.count("- name: Hold the readback FAIL_CLOSED on a declared legacy segment"), 2)
        self.assertEqual(workflow.count("--out \"$RUNNER_TEMP/results/readback.json\" || status=1"), 1)
        self.assertEqual(workflow.count("--out \"$RUNNER_TEMP/results/$name.readback.json\" || status=1"), 1)
        self.assertEqual(workflow.count('if "disposition" in report:'), 2)
        self.assertEqual(workflow.count('"readback_step_passes_on_a_declared_legacy_segment": False'), 2)
        self.assertEqual(workflow.count("sys.exit(1 if held else 0)"), 2)
        self.assertNotIn("ingress_receipt_in_fully_verified_suffix", workflow)

    def test_the_hold_step_reads_the_disposition_and_the_status(self):
        script = _cases._workflow_step_script("Hold the readback FAIL_CLOSED on a declared legacy segment")
        results = self.base / "results"
        results.mkdir()

        def hold(report, status):
            path = results / "readback.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            status_path = results / "readback.status"
            if status is None:
                status_path.unlink(missing_ok=True)
            else:
                status_path.write_text(status + "\n", encoding="utf-8")
            completed = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                                       env={**os.environ, "RUNNER_TEMP": str(self.base)})
            return completed.returncode, completed.stdout

        observation = {"schema": readback.OBSERVATION_SCHEMA, "readback": "OBSERVATION_ONLY"}
        self.assertEqual(hold(observation, "0")[0], 0)
        self.assertEqual(hold(observation, "1")[0], 1)
        self.assertEqual(hold(observation, None)[0], 1)
        fail_closed = dict(observation, disposition="FAIL_CLOSED",
                           **{name: name.upper() for name in SIX})
        code, printed = hold(fail_closed, "1")
        self.assertEqual(code, 1)
        printed = json.loads(printed)
        self.assertEqual(printed["disposition"], "FAIL_CLOSED")
        for name in SIX:
            self.assertEqual(printed[name], name.upper())


C6_INTEGRITY_PREDICATES = {
    "ORGANIZATION_RECEIPT_VERIFIES_AGAINST_ITS_BODY", "ORGANIZATION_LEDGER_CHAIN_IS_COMPLETE",
    "ORGANIZATION_LEDGER_CHAIN_IS_ACYCLIC", "ORGANIZATION_LEDGER_HEAD_PRESENT",
    "REPOSITORY_CHAIN_VERIFIES", "REPOSITORY_LEDGER_HEAD_PRESENT",
    "RECEIPT_SELF_HASH", "CHAIN_LINKAGE", "SELF_HASH", "LINKAGE",
}


class PublishedRefReadbackJob4bA(_EnvCase):
    """Item 4b, ECOSYSTEM_CAPABILITY_ABSENT (a): a read of the published ref with no append."""

    def _job(self):
        lines = (ROOT / ".github/workflows/organization-ledger-transition.yml").read_text(encoding="utf-8").splitlines()
        start = lines.index("  readback-published-ref:")
        end = next(i for i in range(start + 1, len(lines))
                   if lines[i].startswith("  ") and not lines[i].startswith("   ") and lines[i].strip().endswith(":"))
        return "\n".join(lines[start:end])

    def test_the_job_reads_only(self):
        job = self._job()
        self.assertIn("if: github.event_name != 'workflow_dispatch'", job)
        self.assertIn("contents: read", job)
        self.assertNotIn("contents: write", job)
        for absent in ("git push", "update-ref", "intr_destination_ingress.py", "organization_ledger_transition.py",
                       "aggregate_repo_transition", "--ingress-result", "--transition-result"):
            self.assertNotIn(absent, job, absent)
        self.assertIn('--location "git+$fresh#$LEDGER_REF"', job)
        self.assertIn("LEDGER_REF: refs/stegverse/organization-ledger", job)

    def test_the_check_step_records_conformance_not_a_disposition(self):
        script = _cases._workflow_step_script("Check the published-ref observation conforms")
        results = self.base / "results"
        results.mkdir()

        def check(report, status):
            (results / "published.readback.json").write_text(json.dumps(report), encoding="utf-8")
            (results / "published.readback.status").write_text(status + "\n", encoding="utf-8")
            completed = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                                       env={**os.environ, "RUNNER_TEMP": str(self.base)})
            return completed.returncode, json.loads(completed.stdout)

        observation = {"schema": readback.OBSERVATION_SCHEMA, "readback": "OBSERVATION_ONLY"}
        code, line = check(observation, "0")
        self.assertEqual(code, 0)
        self.assertIsNone(line["ledger_disposition"])
        held = dict(observation, disposition="FAIL_CLOSED", undeclared_failures=[],
                    **{name: name.upper() for name in SIX})
        code, line = check(held, "1")
        self.assertEqual(code, 0)
        self.assertEqual(line["ledger_disposition"], "FAIL_CLOSED")
        self.assertIs(line["job_conclusion_is_not_the_ledger_disposition"], True)
        for name in SIX:
            self.assertEqual(line[name], name.upper())
        for report, status in ((dict(held, undeclared_failures=["x"]), "1"),
                               (dict(held, next_attempt=""), "1"),
                               (observation, "1"), (held, "0"), ({}, "1")):
            code, line = check(report, status)
            self.assertEqual(code, 1, (report, status))
            self.assertEqual(line["failure_code"], "PUBLISHED_REF_OBSERVATION_DOES_NOT_CONFORM")
            for name in SIX:
                self.assertTrue(line[name], name)


class C6ParityGaps(_EnvCase):
    """DEFAULT pending owner confirmation: the gaps P1's parity check of 8dcf892 against C6 ebf4aaf found."""

    def segment(self):
        org = json.loads((ROOT / ".stegverse/transition-ledger/org-contract.json").read_text(encoding="utf-8"))
        return copy.deepcopy(org["source_receipt_retention"]["legacy_pre_retention_segment"])

    def refused_code(self, waived):
        segment = self.segment()
        segment["waived_predicate"] = waived
        segment.pop("declaration_sha256")
        segment["declaration_sha256"] = readback.legacy_declaration_digest(segment)
        with self.assertRaises(readback.ReadbackRefused) as caught:
            readback.validate_legacy_segment(segment)
        return caught.exception.failure_code

    def test_1_the_never_waivable_set_is_c6s(self):
        self.assertEqual(set(readback.LEGACY_INTEGRITY_PREDICATES), C6_INTEGRITY_PREDICATES)
        for name in sorted(C6_INTEGRITY_PREDICATES):
            self.assertEqual(self.refused_code(name), "LEGACY_SEGMENT_WAIVES_SELF_HASH_OR_LINKAGE", name)
            self.assertEqual(self.refused_code(["SOURCE_RECEIPT_RETAINED_AND_VERIFIES", name]),
                             "LEGACY_SEGMENT_WAIVES_SELF_HASH_OR_LINKAGE", name)
        for name in ("RECEIPT_VERIFIES_AGAINST_ITS_BODY", "LEDGER_CHAIN_IS_COMPLETE", "RECEIPT_HAS_THE_CONTRACT_FIELDS"):
            self.assertEqual(self.refused_code(name), "LEGACY_SEGMENT_WAIVES_A_NON_WAIVABLE_PREDICATE", name)
        self.assertEqual(self.refused_code([]), "LEGACY_SEGMENT_WAIVES_SELF_HASH_OR_LINKAGE")

    def exported_ledger(self):
        ledger = LedgerRepo(self.base)
        self.bind(ledger)
        result = transition.apply_request(request())
        tree = self.base / "exported"
        tree.mkdir()
        archive = subprocess.run(["git", "-C", str(ledger.path), "archive", _cases.REF], capture_output=True, check=True)
        subprocess.run(["tar", "-x", "-C", str(tree)], input=archive.stdout, check=True)
        receipt = tree / "receipts" / (result["organization_receipt_sha256"][7:] + ".json")
        self.assertTrue(receipt.is_file())
        return tree, receipt

    def segmentless(self):
        return json.loads(segmentless_contract(self.base).read_text(encoding="utf-8"))

    def test_2_the_organization_chain_uses_c6s_predicate_names(self):
        tree, receipt = self.exported_ledger()
        self.assertIn("schema", readback.readback(str(tree), None, NAMESPACE, None, self.segmentless()))
        body = json.loads(receipt.read_text(encoding="utf-8"))
        body["hb_epoch"] = 999
        receipt.write_text(json.dumps(body), encoding="utf-8")
        with self.assertRaises(readback.ReadbackRefused) as caught:
            readback.readback(str(tree), None, NAMESPACE, None, self.segmentless())
        self.assertEqual(caught.exception.failed_predicate, "ORGANIZATION_RECEIPT_VERIFIES_AGAINST_ITS_BODY")
        receipt.unlink()
        with self.assertRaises(readback.ReadbackRefused) as caught:
            readback.readback(str(tree), None, NAMESPACE, None, self.segmentless())
        self.assertEqual(caught.exception.failed_predicate, "ORGANIZATION_LEDGER_CHAIN_IS_COMPLETE")
        source = (ROOT / "resident-runtime/organization_ledger_readback.py").read_text(encoding="utf-8")
        self.assertIn('"self_hash": "ORGANIZATION_RECEIPT_VERIFIES_AGAINST_ITS_BODY"', source)
        self.assertIn('"complete": "ORGANIZATION_LEDGER_CHAIN_IS_COMPLETE"', source)

    def test_3_an_empty_ledger_is_head_absent_with_six_fields(self):
        for contract in (None, segmentless_contract(self.base)):
            empty = self.base / ("empty-" + ("default" if contract is None else "segmentless"))
            empty.mkdir()
            code, refused = self.readback_cli(str(empty), org_contract=contract)
            self.assertEqual(code, 1, refused)
            self.assertEqual(refused["disposition"], "FAIL_CLOSED")
            self.assertEqual(refused["failure_code"], "ORGANIZATION_LEDGER_HEAD_ABSENT")
            self.assertEqual(refused["failed_predicate"], "ORGANIZATION_LEDGER_HEAD_PRESENT")
            for name in SIX:
                self.assertTrue(refused[name], name)
        self.assertFalse(hasattr(readback, "empty_ledger_report"))

    def test_4_a_successful_read_stays_b3_pending_rigel(self):
        """(4) is not applied: a successful read stays disposition-free; ALLOW under readback/v1 is Rigel's call."""
        ledger = LedgerRepo(self.base)
        self.bind(ledger)
        result = transition.apply_request(request())
        code, report = self.readback_cli(ledger.org, transition_result=result,
                                         org_contract=segmentless_contract(self.base))
        self.assertEqual(code, 0, report)
        self.assertNotIn("disposition", report)
        self.assertEqual(report["schema"], "stegverse.organization-ledger-observation/v1")
        doc = (ROOT / "docs/ORGANIZATION_ROLE_CONFORMANCE.md").read_text(encoding="utf-8")
        self.assertIn("C6 parity (4)", doc)


if __name__ == "__main__":
    unittest.main()
