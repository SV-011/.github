"""SV-011 organization ledger ref, intake, and current-state readback.

Temporary repositories only. Nothing here publishes
``refs/stegverse/organization-ledger`` on the remote. Authority and time stay
constraint variables. A disposition is transition evidence, and the readback
does not mint one.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
REF = "refs/stegverse/organization-ledger"
NAMESPACE = "repository-ledger/SV-011/.github"
BEFORE = "sha256:" + "a" * 64
AFTER = "sha256:" + "b" * 64
EPOCH = 32
STATE = {
    "state_id": "S0",
    "as_of": "2026-10-16T00:00:00Z",
    "fresh_until": "2026-10-17T00:00:00Z",
    "declared_transition_element": {
        "transition_id": "T-001",
        "fixture_ref": "fixtures/transition-elements/t-001.json",
    },
    "declared_role_escalation": {
        "role_id": "role.resident",
        "from_role": "role.a",
        "to_role": "role.b",
    },
    "satisfied_scope_ids": ["transition_element"],
}
BASIS_INPUT_FIELDS = (
    "state_id",
    "as_of",
    "fresh_until",
    "declared_transition_element",
    "declared_role_escalation",
    "satisfied_scope_ids",
)
UTC_TIMESTAMP = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"


def _load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ledger_store = _load("sv011_ledger_store", "resident-runtime/ledger_store.py")
readback = _load("sv011_ledger_readback", "resident-runtime/organization_ledger_readback.py")
transition = _load("sv011_ledger_transition", "resident-runtime/organization_ledger_transition.py")
repository = _load("sv011_repo_emit", ".stegverse/transition-ledger/emit.py")
organization = _load("sv011_org_append", "resident-runtime/aggregate_repo_transition.py")


def _request(**overrides):
    document = {
        "schema": "stegverse.organization-transition-request/v1",
        "request_id": "sv011-ledger-001",
        "owning_existing_goal": "SV-011-ORG-LEDGER",
        "standing": {"mode": "ESTABLISH_GENESIS", "node_ref": "sv011-ledger-test", "predecessor": None},
        "transition_id": "sv011-ledger-transition-001",
        "transition_class": "TEST",
        "org_transition_class": "REPO_STATE_PROPAGATION",
        "predecessor_state_sha256": BEFORE,
        "successor_state_sha256": AFTER,
        "authority_constraint": "NONE",
        "evidence": {"probe": "ledger"},
        "hb_epoch": EPOCH,
    }
    document.update(overrides)
    return document


def _contract_basis_digest(state):
    """The contract digest: lowercase hex sha256 of the six fields, no prefix."""
    subject = {name: state[name] for name in BASIS_INPUT_FIELDS}
    preimage = json.dumps(subject, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(preimage.encode("utf-8")).hexdigest()


class LedgerFixture(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.repo = Path(self._dir.name) / "checkout"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True, capture_output=True)
        self.org_location = "git+" + str(self.repo) + "#" + REF
        self.repo_location = self.org_location + ":" + NAMESPACE
        self.env = {
            "STEGVERSE_ORG_LEDGER_ROOT": self.org_location,
            "STEGVERSE_REPO_LEDGER_ROOT": self.repo_location,
        }

    def apply(self, document):
        with mock.patch.dict(os.environ, self.env):
            return transition.apply_request(document)

    def read(self, location=None, namespace=NAMESPACE):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = readback.main([
                "--location", location or self.org_location,
                "--repository-namespace", namespace,
            ])
        return code, json.loads(buffer.getvalue())


class EmptyLedgerTests(LedgerFixture):
    def test_an_empty_ledger_readback_emits_no_basis(self):
        code, report = self.read()
        self.assertEqual(code, 0, report)
        self.assertIs(report["ledger_empty"], True)
        self.assertIs(report["head_present"], False)
        self.assertEqual(report["chain_length"], 0)
        self.assertIs(report["chain_verifies"], False)
        self.assertIsNone(report["current_state"])
        self.assertIs(report["basis_established"], False)
        self.assertEqual(report["current_state_reason"], "ORGANIZATION_LEDGER_EMPTY")
        self.assertIs(report["disposition_is_transition_evidence"], False)
        self.assertNotIn("disposition", report)
        text = json.dumps(report)
        for invented in (
            "state_id", "as_of", "fresh_until", "satisfied_scope_ids", "basis_digest",
            "declared_transition_element", "declared_role_escalation", "sha256:",
        ):
            self.assertNotIn(invented, text, invented)
        self.assertIsNone(readback.recorded_for(self.org_location, "a" * 64))
        self.assertFalse((self.repo / ".git" / "refs" / "stegverse").exists())


class CurrentStateTests(LedgerFixture):
    def test_a_head_receipt_without_current_state_does_not_invent_one(self):
        applied = self.apply(_request())
        self.assertIsNone(applied["current_state_recorded"])
        code, report = self.read()
        self.assertEqual(code, 0, report)
        self.assertIs(report["ledger_empty"], False)
        self.assertIs(report["chain_verifies"], True)
        self.assertEqual(report["chain_length"], 1)
        self.assertIsNone(report["current_state"])
        self.assertIs(report["basis_established"], False)
        self.assertEqual(report["current_state_reason"], "BASIS_ABSENT")
        self.assertEqual(report["current_state_fields_absent"], list(BASIS_INPUT_FIELDS))
        self.assertNotIn("basis_digest", json.dumps(report["current_state"]))

    def test_current_state_is_copied_from_the_head_receipt_and_digested(self):
        self.apply(_request(current_state=dict(STATE)))
        code, report = self.read()
        self.assertEqual(code, 0, report)
        self.assertIs(report["basis_established"], True)
        state = report["current_state"]
        self.assertEqual(set(state), {
            "state_id", "as_of", "fresh_until", "basis_digest",
            "declared_transition_element", "declared_role_escalation", "satisfied_scope_ids",
        })
        self.assertEqual(state["state_id"], STATE["state_id"])
        self.assertEqual(state["as_of"], STATE["as_of"])
        self.assertEqual(state["fresh_until"], STATE["fresh_until"])
        self.assertEqual(state["declared_transition_element"], {
            "transition_id": "T-001",
            "fixture_ref": "fixtures/transition-elements/t-001.json",
        })
        self.assertEqual(set(state["declared_transition_element"]), {"transition_id", "fixture_ref"})
        self.assertEqual(state["declared_role_escalation"], {
            "role_id": "role.resident",
            "from_role": "role.a",
            "to_role": "role.b",
        })
        self.assertEqual(set(state["declared_role_escalation"]), {"role_id", "from_role", "to_role"})
        self.assertEqual(state["satisfied_scope_ids"], ["transition_element"])
        self.assertRegex(state["as_of"], UTC_TIMESTAMP)
        self.assertRegex(state["fresh_until"], UTC_TIMESTAMP)
        self.assertEqual(state["basis_digest"], _contract_basis_digest(STATE))
        self.assertEqual(readback.basis_digest(STATE), state["basis_digest"])

    def test_basis_digest_recomputes_exactly_per_the_contract(self):
        self.apply(_request(current_state=dict(STATE)))
        code, report = self.read()
        self.assertEqual(code, 0, report)
        state = report["current_state"]
        without_digest = {key: value for key, value in state.items() if key != "basis_digest"}
        self.assertEqual(set(without_digest), set(BASIS_INPUT_FIELDS))
        preimage = json.dumps(without_digest, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(preimage.encode("utf-8")).hexdigest()
        self.assertEqual(state["basis_digest"], digest)
        self.assertEqual(digest, digest.lower())
        self.assertEqual(len(digest), 64)
        self.assertNotIn("sha256:", state["basis_digest"])
        self.assertNotIn("\\u", preimage)
        prefixed = hashlib.sha256(("sha256:" + preimage).encode("utf-8")).hexdigest()
        self.assertNotEqual(state["basis_digest"], "sha256:" + digest)
        self.assertNotEqual(state["basis_digest"], prefixed)

    def test_a_missing_declared_field_is_not_fabricated(self):
        incomplete = dict(STATE)
        del incomplete["declared_role_escalation"]
        projected = readback.project_current_state({"current_state": incomplete})
        self.assertIs(projected["basis_established"], False)
        self.assertIsNone(projected["current_state"])
        self.assertEqual(projected["current_state_reason"], "BASIS_ABSENT")
        self.assertEqual(projected["current_state_fields_absent"], ["declared_role_escalation"])
        text = json.dumps(projected)
        self.assertNotIn("role.a", text)
        self.assertNotIn("role_id", text)
        self.assertNotIn("basis_digest", json.dumps(projected["current_state"]))

    def test_a_timestamp_that_is_not_utc_seconds_is_not_a_basis(self):
        bad = dict(STATE)
        bad["as_of"] = "HB:32"
        projected = readback.project_current_state({"current_state": bad})
        self.assertIs(projected["basis_established"], False)
        self.assertIsNone(projected["current_state"])
        self.assertEqual(projected["current_state_reason"], "BASIS_UNKNOWN")
        self.assertEqual(projected["current_state_fields_absent"], ["as_of"])

    def test_a_calendar_date_strptime_rejects_is_unknown(self):
        bad = dict(STATE)
        bad["fresh_until"] = "2026-02-31T00:00:00Z"
        projected = readback.project_current_state({"current_state": bad})
        self.assertIs(projected["basis_established"], False)
        self.assertEqual(projected["current_state_reason"], "BASIS_UNKNOWN")
        self.assertEqual(projected["current_state_fields_absent"], ["fresh_until"])

    def test_a_later_as_of_is_stale(self):
        stale = dict(STATE)
        stale["as_of"] = "2026-10-18T00:00:00Z"
        projected = readback.project_current_state({"current_state": stale})
        self.assertIs(projected["basis_established"], False)
        self.assertIsNone(projected["current_state"])
        self.assertEqual(projected["current_state_reason"], "BASIS_STALE")

    def test_equal_timestamps_are_fresh(self):
        fresh = dict(STATE)
        fresh["fresh_until"] = fresh["as_of"]
        projected = readback.project_current_state({"current_state": fresh})
        self.assertIs(projected["basis_established"], True)
        self.assertEqual(projected["current_state"]["as_of"], projected["current_state"]["fresh_until"])

    def test_indistinguishable_roles_are_contradictory(self):
        bad = dict(STATE)
        bad["declared_role_escalation"] = {
            "role_id": "role.resident",
            "from_role": "role.a",
            "to_role": "role.a",
        }
        projected = readback.project_current_state({"current_state": bad})
        self.assertIs(projected["basis_established"], False)
        self.assertEqual(projected["current_state_reason"], "BASIS_CONTRADICTORY")
        self.assertEqual(projected["current_state_fields_absent"], ["declared_role_escalation"])

    def test_a_satisfaction_list_that_is_not_a_prefix_is_contradictory(self):
        for scopes in (["role_escalation"], ["transition_element", "transition_element"], ["scope.intr-destination"]):
            with self.subTest(scopes=scopes):
                bad = dict(STATE)
                bad["satisfied_scope_ids"] = scopes
                projected = readback.project_current_state({"current_state": bad})
                self.assertIs(projected["basis_established"], False)
                self.assertEqual(projected["current_state_reason"], "BASIS_CONTRADICTORY")
                self.assertIn("satisfied_scope_ids", projected["current_state_fields_absent"])

    def test_each_manifest_scope_prefix_is_a_basis(self):
        for scopes in ([], ["transition_element"], ["transition_element", "role_escalation"]):
            with self.subTest(scopes=scopes):
                declared = dict(STATE)
                declared["satisfied_scope_ids"] = list(scopes)
                projected = readback.project_current_state({"current_state": declared})
                self.assertIs(projected["basis_established"], True, projected)
                self.assertEqual(projected["current_state"]["satisfied_scope_ids"], list(scopes))

    def test_a_transition_id_that_is_not_t_plus_three_digits_is_unknown(self):
        bad = dict(STATE)
        bad["declared_transition_element"] = dict(STATE["declared_transition_element"])
        bad["declared_transition_element"]["transition_id"] = "sv011-ledger-transition-001"
        projected = readback.project_current_state({"current_state": bad})
        self.assertIs(projected["basis_established"], False)
        self.assertEqual(projected["current_state_reason"], "BASIS_UNKNOWN")
        self.assertEqual(projected["current_state_fields_absent"], ["declared_transition_element"])

    def test_an_older_receipt_does_not_supply_the_current_basis(self):
        self.apply(_request(current_state=dict(STATE)))
        self.apply(_request(
            request_id="sv011-ledger-002",
            transition_id="sv011-ledger-transition-002",
            predecessor_state_sha256="sha256:" + "c" * 64,
            successor_state_sha256="sha256:" + "d" * 64,
        ))
        code, report = self.read()
        self.assertEqual(code, 0, report)
        self.assertEqual(report["chain_length"], 2)
        self.assertIsNone(report["current_state"])
        self.assertIs(report["basis_established"], False)
        self.assertEqual(report["current_state_reason"], "BASIS_ABSENT")

    def test_a_declared_digest_that_does_not_match_the_fields_is_not_a_basis(self):
        projected = readback.project_current_state({
            "current_state": {**STATE, "basis_digest": "0" * 64},
        })
        self.assertIs(projected["basis_established"], False)
        self.assertIsNone(projected["current_state"])
        self.assertEqual(projected["current_state_reason"], "BASIS_CONTRADICTORY")
        self.assertEqual(projected["current_state_fields_absent"], ["basis_digest"])

    def test_a_prefixed_digest_is_unknown(self):
        projected = readback.project_current_state({
            "current_state": {**STATE, "basis_digest": "sha256:" + "0" * 64},
        })
        self.assertIs(projected["basis_established"], False)
        self.assertEqual(projected["current_state_reason"], "BASIS_UNKNOWN")
        self.assertEqual(projected["current_state_fields_absent"], ["basis_digest"])


class ChainTests(LedgerFixture):
    def test_both_ledgers_share_one_ref_and_the_source_is_retained(self):
        applied = self.apply(_request(current_state=dict(STATE)))
        again = self.apply(_request(current_state=dict(STATE)))
        self.assertEqual(again["organization_receipt_sha256"], applied["organization_receipt_sha256"])
        self.assertIs(applied["source_retained"], True)
        code, report = self.read()
        self.assertEqual(code, 0, report)
        self.assertEqual(report["chain_length"], 1)
        self.assertIs(report["every_source_receipt_retained_and_verifies"], True)
        repository_chain = report["repository_ledger"]
        self.assertEqual(repository_chain["chain_length"], 1)
        self.assertIs(repository_chain["every_receipt_retained_in_organization_ledger"], True)
        self.assertIs(repository_chain["replay_read_organization_receipts"], False)
        listed = subprocess.run(
            ["git", "rev-list", REF], cwd=self.repo, check=True, capture_output=True, text=True)
        self.assertEqual(len(listed.stdout.split()), 2)
        self.assertEqual(
            readback.recorded_for(self.org_location, "a" * 64),
            applied["organization_receipt_sha256"])

    def test_a_namespace_cannot_shadow_a_ledger_key(self):
        for namespace in ("", "HEAD.json", "receipts", "source-receipts", "..", "a/../b"):
            with self.subTest(namespace):
                with self.assertRaises(ledger_store.LedgerLocatorInvalid):
                    ledger_store.parse_locator(self.org_location + ":" + namespace)

    def test_the_repository_identity_on_a_receipt_is_sv011(self):
        with mock.patch.dict(os.environ, self.env):
            receipt = repository.append("identity", "TEST", BEFORE, AFTER, {}, "NONE", hb_epoch=EPOCH)
        self.assertEqual(receipt["repository"], "SV-011/.github")
        self.assertEqual(receipt["authority_constraint"], "NONE")
        self.assertEqual(receipt["time_constraint"], "HEARTBEAT_EPOCH")
        self.assertIs(receipt["disposition_is_transition_evidence"], True)
        self.assertIs(receipt["production_receipt_claimed"], False)
        self.assertNotIn("authority_effect", receipt)


class IntakeTests(unittest.TestCase):
    def test_an_sdk_manifest_request_is_refused(self):
        for extra in ({"build_manifest": {"process": "x"}}, {"manifest": {"schema": "x"}}):
            with self.subTest(extra=sorted(extra)):
                with self.assertRaises(transition.TransitionRequestRefused) as refused:
                    transition.validate_request(_request(**extra))
                self.assertEqual(refused.exception.failed_predicate, "SDK_MANIFEST_BUILDER_NOT_PRESENT")

    def test_a_partial_current_state_is_refused(self):
        partial = dict(STATE)
        del partial["fresh_until"]
        with self.assertRaises(ValueError) as refused:
            transition.validate_request(_request(current_state=partial))
        self.assertIn("fresh_until", str(refused.exception))

    def test_a_missing_declared_field_is_refused_at_intake(self):
        partial = dict(STATE)
        del partial["declared_transition_element"]
        with self.assertRaises(ValueError) as refused:
            transition.validate_request(_request(current_state=partial))
        self.assertIn("declared_transition_element", str(refused.exception))

    def test_a_non_utc_timestamp_is_refused_at_intake(self):
        bad = dict(STATE)
        bad["fresh_until"] = "HB:40"
        with self.assertRaises(ValueError) as refused:
            transition.validate_request(_request(current_state=bad))
        self.assertIn("fresh_until", str(refused.exception))
        self.assertIn("BASIS_UNKNOWN", str(refused.exception))

    def test_intake_refuses_a_contradictory_role_escalation(self):
        bad = dict(STATE)
        bad["declared_role_escalation"] = {
            "role_id": "role.resident",
            "from_role": "role.a",
            "to_role": "role.a",
        }
        with self.assertRaises(ValueError) as refused:
            transition.validate_request(_request(current_state=bad))
        self.assertIn("BASIS_CONTRADICTORY", str(refused.exception))

    def test_the_intake_tree_validator_accepts_an_empty_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(transition.validate_tree(Path(directory)), [])


class ContractAndWorkflowTests(unittest.TestCase):
    def test_the_contracts_bind_the_ledger_ref_to_sv011(self):
        org = json.loads((ROOT / ".stegverse/transition-ledger/org-contract.json").read_text(encoding="utf-8"))
        repo = json.loads((ROOT / ".stegverse/transition-ledger/contract.json").read_text(encoding="utf-8"))
        self.assertEqual(org["organization"], "SV-011")
        self.assertEqual(org["organization_ledger_repository"], "SV-011/.github")
        self.assertEqual(org["organization_ledger_ref"], REF)
        location = repo["repository_ledger_location"]
        self.assertEqual(location["repository"], "SV-011/.github")
        self.assertEqual(location["ref"], REF)
        self.assertEqual(location["namespace"], NAMESPACE)
        self.assertIs(location["new_ledger_root"], False)
        self.assertEqual(repo["repository"], "SV-011/.github")
        self.assertNotIn("authority_effect", org)
        self.assertNotIn("authority_effect", repo)
        self.assertNotIn("StegVerse-org", json.dumps(org))
        self.assertNotIn("StegVerse-org", json.dumps(repo))
        self.assertIn("starts empty", org["organization_ledger_ref_designation_source"])

    def test_the_transition_workflow_publishes_only_the_sv011_ledger_ref(self):
        workflow = (ROOT / ".github/workflows/organization-ledger-transition.yml").read_text(encoding="utf-8")
        self.assertIn('LEDGER_REF: refs/stegverse/organization-ledger', workflow)
        self.assertIn('assert contract["organization_ledger_repository"] == "SV-011/.github"', workflow)
        self.assertIn('export STEGVERSE_REPO_LEDGER_ROOT="git+$GITHUB_WORKSPACE#$LEDGER_REF:$REPO_LEDGER_NAMESPACE"', workflow)
        self.assertIn("organization_ledger_transition.py", workflow)
        self.assertEqual(workflow.count('git push origin "$LEDGER_REF:$LEDGER_REF"'), 1)
        self.assertNotIn("StegVerse-org/.github", workflow)
        self.assertNotIn("StegVerse-SDK", workflow)
        self.assertNotIn("MESH_PUBLISH_REF", workflow)
        self.assertNotIn("build_manifest", workflow)
        lowered = workflow.lower()
        self.assertNotIn("authorized", lowered)
        self.assertNotIn("granted", lowered)
        for line in workflow.splitlines():
            if line.strip() == "permissions:":
                continue
            self.assertNotIn("permission", line.lower())


class WordingTests(unittest.TestCase):
    def test_ported_modules_do_not_add_grant_style_fields(self):
        needles = ("authorized", "granted", "permission")
        paths = [
            "resident-runtime/ledger_store.py",
            "resident-runtime/organization_ledger_readback.py",
            "resident-runtime/organization_ledger_transition.py",
            "resident-runtime/aggregate_repo_transition.py",
            ".stegverse/transition-ledger/emit.py",
            ".stegverse/transition-ledger/org-contract.json",
            ".stegverse/transition-ledger/contract.json",
        ]
        for relative in paths:
            text = (ROOT / relative).read_text(encoding="utf-8").lower()
            for needle in needles:
                self.assertNotIn(needle, text, relative + " contains " + needle)


if __name__ == "__main__":
    unittest.main()
