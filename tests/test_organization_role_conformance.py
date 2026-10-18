"""Every non-ALLOW outcome carries the six fields of the conformance standard.

Standard: ``StegVerse-org/.github@c483d441``
``docs/ORGANIZATION_ROLE_RUNTIME_REALITY_DEPLOYMENT.md``, Conformance standard
item 5. A non-ALLOW result carries ``failure_code``, ``failed_predicate``,
``required_evidence_or_repair``, ``retry_entrypoint``, ``owning_existing_goal``
and ``next_attempt``.

Ledger roots and the mesh are temporary. Nothing here is an authentic
production receipt. A disposition is evidence that a transition occurred;
Authority and Time stay constraint variables.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = os.environ.get("PYTHON", "python3")
SIX = ("failure_code", "failed_predicate", "required_evidence_or_repair",
       "retry_entrypoint", "owning_existing_goal", "next_attempt")
LEDGER_GOAL = "SV011-TT-CAPABILITY-MAP-OBSERVATION-001"
BOUNDARY_GOAL = "ORG-FED-SV-011-001"
LIVE = ROOT / "tests/fixtures/organization-ledger-2cc58cec"
LIVE_BUNDLE = ROOT / "tests/fixtures/organization-ledger-2cc58cec.bundle"
LEDGER_REF = "refs/stegverse/organization-ledger"


def live_clone(base):
    """A git clone of the published ref from the 2cc58cec bundle (C6 convergence a)."""
    path = Path(base) / "live-clone.git"
    subprocess.run(["git", "init", "-q", "--bare", str(path)], check=True)
    subprocess.run(["git", "--git-dir", str(path), "fetch", "-q", str(LIVE_BUNDLE),
                    "+" + LEDGER_REF + ":" + LEDGER_REF], check=True)
    return "git+" + str(path.resolve()) + "#" + LEDGER_REF
NAMESPACE = "repository-ledger/SV-011/.github"
WORKFLOW = ROOT / ".github/workflows/organization-ledger-transition.yml"
REGISTER = ROOT / "data/organization-role-exemption-register.json"


def _module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


non_allow = _module("non_allow_conformance", "resident-runtime/non_allow.py")
transition = _module("transition_conformance", "resident-runtime/organization_ledger_transition.py")
readback = _module("readback_conformance", "resident-runtime/organization_ledger_readback.py")
ingress = _module("ingress_conformance", "resident-runtime/intr_destination_ingress.py")
egress = _module("egress_conformance", "resident-runtime/organization_egress_boundary.py")
REGISTRY = json.loads((ROOT / "org-boundary/registry/services.json").read_text(encoding="utf-8"))
GENESIS = {"mode": "ESTABLISH_GENESIS", "node_ref": "conformance-test", "predecessor": None}


def request(**overrides):
    document = {
        "schema": "stegverse.organization-transition-request/v1",
        "request_id": "CONFORMANCE-REQUEST-001",
        "owning_existing_goal": "SYNTHETIC-TEST-GOAL",
        "constraint_set": {"node_standing": {"mode": "ESTABLISH_GENESIS", "node_ref": "unit-test", "predecessor": None}},
        "manifested_route": {
            "capability_profile_id": "organization-ledger-transition-request",
            "route_id": "stegverse.route.sv011.organization-ledger-request.v1",
            "destination_repository": "SV-011/.github",
            "destination_operation": "resident-runtime/organization_ledger_transition.py::apply_request",
            "destination_ref": "refs/stegverse/organization-ledger",
        },
        "transition_id": "conformance-transition-001",
        "transition_class": "SYNTHETIC_TRANSITION_REQUEST",
        "org_transition_class": "REPO_STATE_PROPAGATION",
        "predecessor_state_sha256": "sha256:" + "0" * 64,
        "successor_state_sha256": "sha256:" + "1" * 64,
        "authority_constraint": "NONE",
        "evidence": {},
        "hb_epoch": 32,
    }
    document.update(overrides)
    return document


def diagnostic(communication_id="conformance-diagnostic"):
    return {"message_class": "ecosystem.communication", "communication_id": communication_id,
            "subject": "purpose-neutral mesh diagnostic",
            "body": {"schema": "stegverse.org-intr-destination-diagnostic/v1", "probe": "diagnostic-crossing"}}


class SixFields(unittest.TestCase):
    def assert_six(self, document, goal=None):
        for name in SIX:
            self.assertIsInstance(document.get(name), str, (name, document))
            self.assertTrue(document[name].strip(), (name, document))
        self.assertEqual(non_allow.missing(document), [])
        if goal is not None:
            self.assertEqual(document["owning_existing_goal"], goal)
        for bare in ("BLOCKED", "NOT_OBSERVED"):
            self.assertNotEqual(document["failure_code"], bare)


class Workspace(SixFields):
    ROOTS = ("STEGVERSE_REPO_LEDGER_ROOT", "STEGVERSE_ORG_LEDGER_ROOT")

    def setUp(self):
        self._work = tempfile.TemporaryDirectory()
        self.addCleanup(self._work.cleanup)
        self.base = Path(self._work.name)
        previous = {name: os.environ.get(name) for name in self.ROOTS}
        for name in self.ROOTS:
            os.environ[name] = str(self.base / name.lower())
        self.mesh = self.base / "mesh"

        def restore():
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        self.addCleanup(restore)

    def run_cli(self, *argv):
        completed = subprocess.run([PYTHON, *map(str, argv)], capture_output=True, text=True, env=dict(os.environ))
        return completed.returncode, json.loads(completed.stdout)


class TheStandardsFieldList(SixFields):
    def test_the_six_fields_are_the_standards_list_in_order(self):
        self.assertEqual(non_allow.SIX_FIELDS, SIX)
        self.assertEqual(non_allow.NON_ALLOW_DISPOSITIONS, ("DENY", "FAIL_CLOSED"))

    def test_an_empty_field_or_an_allow_is_refused(self):
        complete = {name: "x" for name in SIX}
        for name in SIX:
            with self.assertRaises(ValueError):
                non_allow.fields(**{**complete, name: " "})
        with self.assertRaises(ValueError):
            non_allow.result("ALLOW", **complete)
        with self.assertRaises(ValueError):
            non_allow.result("PENDING", **complete)
        self.assert_six(non_allow.result("FAIL_CLOSED", **complete))


class TransitionRequestNonAllow(Workspace):
    """N2/N3: an append is ALLOW evidence; every refusal is FAIL_CLOSED with six fields."""

    def apply(self, document):
        path = self.base / "request.json"
        path.write_text(document if isinstance(document, str) else json.dumps(document), encoding="utf-8")
        return self.run_cli(ROOT / "resident-runtime/organization_ledger_transition.py", "--request", path)

    def test_every_refused_request_is_fail_closed_with_six_fields(self):
        cases = [
            (request(manifest={}), "SDK_MANIFEST_BUILDER_NOT_PRESENT", "SYNTHETIC-TEST-GOAL"),
            (request(schema="not-the-schema"), "TRANSITION_REQUEST_SCHEMA", "SYNTHETIC-TEST-GOAL"),
            (request(evidence={"disposition": "ALLOW"}),
             "TRANSITION_REQUEST_DOES_NOT_DECLARE_ITS_OWN_DISPOSITION", "SYNTHETIC-TEST-GOAL"),
            (request(predecessor_state_sha256="0"), "TRANSITION_REQUEST_STATE_IS_A_SHA256_DIGEST",
             "SYNTHETIC-TEST-GOAL"),
            (request(current_state={"state_id": "s"}), "TRANSITION_REQUEST_CURRENT_STATE_IS_A_BASIS",
             "SYNTHETIC-TEST-GOAL"),
            (request(owning_existing_goal=""), "TRANSITION_REQUEST_FIELD_PRESENT", LEDGER_GOAL),
            ("{not json", "TRANSITION_REQUEST_APPLIES", LEDGER_GOAL),
            ([], "TRANSITION_REQUEST_IS_AN_OBJECT", LEDGER_GOAL),
        ]
        for document, predicate, goal in cases:
            code, refused = self.apply(document)
            self.assertEqual(code, 1, refused)
            self.assertEqual(refused["disposition"], "FAIL_CLOSED")
            self.assertEqual(refused["failure_code"], "ORGANIZATION_TRANSITION_REQUEST_REFUSED")
            self.assertEqual(refused["failed_predicate"], predicate)
            self.assert_six(refused, goal)
            # Refused requests are recorded (ORRD Conformance item 1); a non-object leaves no receipt.
            recorded = not isinstance(document, (str, list))
            self.assertIs(refused["refusal_recorded"], recorded, refused)
            self.assertIs(refused["disposition_is_transition_evidence"], recorded)
            self.assertIs(refused["consequence_committed"], False)
            if not recorded:
                self.assertEqual(refused["refusal_recorded_reason"], "REQUEST_IS_NOT_A_JSON_OBJECT_NO_RECEIPT_WRITTEN")

    def test_a_missing_request_path_and_a_refused_tree_carry_six_fields(self):
        code, refused = self.run_cli(ROOT / "resident-runtime/organization_ledger_transition.py")
        self.assertEqual(code, 1)
        self.assert_six(refused, LEDGER_GOAL)
        tree = self.base / "requests"
        tree.mkdir()
        (tree / "bad.json").write_text(json.dumps(request(constraint_set={})), encoding="utf-8")
        code, refused = self.run_cli(ROOT / "resident-runtime/organization_ledger_transition.py",
                                     "--validate-tree", tree)
        self.assertEqual(code, 1)
        self.assertEqual(refused["failed_predicate"], "TRANSITION_REQUEST_CONSTRAINT_SET_PRESENT")
        self.assertIn("bad.json", refused["required_evidence_or_repair"])
        self.assert_six(refused, LEDGER_GOAL)

    def test_an_applied_request_records_allow_as_evidence_on_both_receipts(self):
        applied = transition.apply_request(request())
        self.assertEqual(applied["disposition"], "ALLOW")
        self.assertEqual(applied["owning_existing_goal"], "SYNTHETIC-TEST-GOAL")
        self.assertIs(applied["disposition_is_transition_evidence"], True)
        found = {}
        for name in self.ROOTS:
            for path in Path(os.environ[name]).rglob("*.json"):
                record = json.loads(path.read_text(encoding="utf-8"))
                if record.get("receipt_sha256") in (applied["repository_receipt_sha256"],
                                                    applied["organization_receipt_sha256"]):
                    found[record["receipt_sha256"]] = record
        self.assertEqual(len(found), 2)
        for record in found.values():
            self.assertEqual(record["evidence"]["disposition"], "ALLOW")
            self.assertEqual(record["authority_constraint"], "NONE")
            self.assertEqual(record["time_constraint"], "HEARTBEAT_EPOCH")
        self.assertEqual(transition.apply_request(request()), applied)


class ReadbackNonAllow(Workspace):
    """N4/N5: refused readbacks carry FAIL_CLOSED and six fields; unestablished bases carry six fields.

    DEFAULT in force (fail-closed) (C6 convergence c): a refusal carries a
    top-level disposition FAIL_CLOSED; a successful read carries none.
    """

    def test_a_refused_readback_carries_fail_closed_and_six_fields(self):
        script = ROOT / "resident-runtime/organization_ledger_readback.py"
        for extra, goal in ((["--ingress-result", LIVE / "HEAD.json"], BOUNDARY_GOAL),):
            code, refused = self.run_cli(script, "--location", self.base / "absent",
                                         "--repository-namespace", NAMESPACE, *extra)
            self.assertEqual(code, 1, refused)
            self.assertEqual(refused["disposition"], "FAIL_CLOSED")
            self.assertEqual(refused["failure_code"], "ORGANIZATION_LEDGER_HEAD_ABSENT")
            self.assertEqual(refused["failed_predicate"], "ORGANIZATION_LEDGER_HEAD_PRESENT")
            self.assert_six(refused, goal)
        code, refused = self.run_cli(script, "--location", self.base / "absent",
                                     "--repository-namespace", "wrong-namespace")
        self.assertEqual(refused["failed_predicate"], "REPOSITORY_NAMESPACE_MATCHES_THE_CONTRACT")
        self.assert_six(refused, LEDGER_GOAL)

    def test_a_refusal_answers_to_the_requests_own_goal(self):
        path = self.base / "transition.json"
        path.write_text(json.dumps({"request_id": "R", "owning_existing_goal": "SYNTHETIC-TEST-GOAL",
                                    "repository_receipt_sha256": "sha256:" + "2" * 64,
                                    "organization_receipt_sha256": "sha256:" + "3" * 64}), encoding="utf-8")
        code, refused = self.run_cli(ROOT / "resident-runtime/organization_ledger_readback.py",
                                     "--location", live_clone(self.base), "--repository-namespace", NAMESPACE,
                                     "--transition-result", path)
        self.assertEqual(code, 1, refused)
        self.assertEqual(refused["failed_predicate"], "REQUEST_REPOSITORY_RECEIPT_IS_IN_THE_REPOSITORY_LEDGER")
        self.assert_six(refused, "SYNTHETIC-TEST-GOAL")

    def test_the_live_head_basis_absent_carries_six_fields(self):
        result = readback.readback(live_clone(self.base), None, NAMESPACE)
        self.assertEqual(result["disposition"], "FAIL_CLOSED")
        self.assertEqual(result["failure_code"], "DECLARED_LEGACY_SEGMENT_NOT_SOURCE_RETAINED")
        self.assertEqual(result["current_state_reason"], "BASIS_ABSENT")
        finding = result["current_state_finding"]
        self.assertEqual(finding["failure_code"], "BASIS_ABSENT")
        self.assert_six(finding, LEDGER_GOAL)
        self.assertIn("HEAD_RECEIPT_DECLARES_NO_CURRENT_STATE", finding["required_evidence_or_repair"])

    def test_the_empty_ledger_refusal_carries_six_fields(self):
        empty = self.base / "empty"
        empty.mkdir()
        code, refused = self.run_cli(ROOT / "resident-runtime/organization_ledger_readback.py",
                                     "--location", empty, "--repository-namespace", NAMESPACE)
        self.assertEqual(code, 1, refused)
        self.assertEqual(refused["disposition"], "FAIL_CLOSED")
        self.assertEqual(refused["failure_code"], "ORGANIZATION_LEDGER_HEAD_ABSENT")
        self.assert_six(refused, LEDGER_GOAL)

    def test_every_basis_code_carries_six_fields_and_an_established_basis_carries_none(self):
        good = {
            "state_id": "s", "as_of": "2026-10-17T00:00:00Z", "fresh_until": "2026-10-18T00:00:00Z",
            "declared_transition_element": {"transition_id": "T-001",
                                            "fixture_ref": "fixtures/transition-elements/t-001.json"},
            "declared_role_escalation": {"role_id": "r", "from_role": "context", "to_role": "evidence"},
            "satisfied_scope_ids": [],
        }
        cases = {
            "BASIS_ABSENT": {"current_state": {k: v for k, v in good.items() if k != "state_id"}},
            "BASIS_UNKNOWN": {"current_state": {**good, "state_id": "has spaces"}},
            "BASIS_STALE": {"current_state": {**good, "as_of": "2026-10-19T00:00:00Z"}},
            "BASIS_CONTRADICTORY": {"current_state": {**good, "declared_role_escalation": {
                "role_id": "r", "from_role": "context", "to_role": "context"}}},
        }
        for code, receipt in cases.items():
            projected = readback.project_current_state(receipt)
            self.assertEqual(projected["current_state_reason"], code)
            self.assertEqual(projected["current_state_finding"]["failure_code"], code)
            self.assert_six(projected["current_state_finding"], LEDGER_GOAL)
        established = readback.project_current_state({"current_state": good})
        self.assertIs(established["basis_established"], True)
        self.assertIsNone(established["current_state_finding"])


class BoundaryNonAllow(Workspace):
    """Ingress and egress DENY / FAIL_CLOSED results; N12 the absent answer."""

    def test_an_ingress_refusal_and_a_collision_carry_six_fields(self):
        denied = ingress.receive({"message_class": "ecosystem.work.request", "communication_id": "c",
                                  "body": {"schema": "other/v1"}},
                                 registry=REGISTRY, packet_id="denied", hb_epoch=32)
        self.assertEqual(denied["disposition"], "DENY")
        self.assert_six(denied, BOUNDARY_GOAL)
        not_a_map = ingress.receive(diagnostic(), registry=[], packet_id="not-a-map", hb_epoch=32)
        self.assertEqual(not_a_map["disposition"], "DENY")
        self.assert_six(not_a_map, BOUNDARY_GOAL)
        acknowledged = ingress.receive(diagnostic(), registry=REGISTRY, packet_id="bound", hb_epoch=32)
        self.assertEqual(acknowledged["disposition"], "ALLOW")
        self.assertEqual(non_allow.missing(acknowledged), list(SIX))
        collided = ingress.receive(diagnostic("another"), registry=REGISTRY, packet_id="bound", hb_epoch=32)
        self.assertEqual(collided["disposition"], "FAIL_CLOSED")
        self.assertIs(collided["refusal_recorded"], False)
        self.assert_six(collided, BOUNDARY_GOAL)

    def test_an_emit_refusal_carries_six_fields_in_the_result_and_the_record(self):
        refused = egress.emit("Not-A-Declared-Peer", diagnostic(), standing=GENESIS,
                              capability="intr-destination", mesh_root=self.mesh, hb_epoch=32)
        self.assertEqual(refused["disposition"], "DENY")
        self.assertEqual(refused["failed_predicate"], "DESTINATION_IS_A_DECLARED_PEER_OF_THIS_ORGANIZATION")
        self.assert_six(refused, BOUNDARY_GOAL)
        self.assert_six(refused["emission_record"], BOUNDARY_GOAL)

    def test_no_answer_is_fail_closed_with_six_fields_and_awaits_nothing(self):
        emitted = egress.emit("StegVerse-org", diagnostic(), standing=GENESIS,
                              capability="intr-destination", mesh_root=self.mesh, hb_epoch=32)
        self.assertEqual(emitted["disposition"], "ALLOW")
        absent = egress.close("StegVerse-org", emitted["packet_id"], "conformance-diagnostic",
                              mesh_root=self.mesh, hb_epoch=33)
        self.assertEqual(absent["disposition"], "FAIL_CLOSED")
        self.assertNotEqual(absent["disposition"], "PENDING")
        self.assertEqual(absent["failure_code"], "FAR_SIDE_ANSWER_NOT_MATERIALIZED")
        self.assert_six(absent, BOUNDARY_GOAL)
        self.assertEqual(absent["receiver_unavailable_disposition"],
                         "DURABLE_QUEUE_OR_EVENT_EPHEMERAL_MATERIALIZATION")
        self.assertIs(absent["always_on_receiver_required"], False)
        self.assertIs(absent["awaits_the_receiver"], False)
        self.assertIs(absent["closure_recorded"], False)
        self.assertIs(absent["disposition_is_transition_evidence"], False)
        source = (ROOT / "resident-runtime/organization_egress_boundary.py").read_text(encoding="utf-8")
        self.assertNotIn('"PENDING"', source)
        self.assertNotIn("time.sleep", source)

    def test_a_refused_closure_record_carries_six_fields(self):
        resolved = egress.resolve_destination("StegVerse-org")
        record = egress.closure_record(resolved, "SV-011", "p", "c", {"organization": "Other"},
                                       ["RESPONSE_CAME_FROM_A_DIFFERENT_ORGANIZATION:Other"], [])
        self.assertEqual(record["disposition"], "DENY")
        self.assert_six(record, BOUNDARY_GOAL)
        verified = egress.closure_record(resolved, "SV-011", "p", "c", {"organization": "StegVerse-org"}, [], ["r"])
        self.assertEqual(verified["disposition"], "ALLOW")
        self.assertEqual(non_allow.missing(verified), list(SIX))

    def test_the_boundary_declares_the_receiver_unavailable_disposition(self):
        boundary = json.loads((ROOT / "org-runtime/interlock-intr.json").read_text(encoding="utf-8"))
        emitting = boundary["egress"]["emitting_operation"]
        self.assertEqual(emitting["receiver_unavailable_disposition"],
                         "DURABLE_QUEUE_OR_EVENT_EPHEMERAL_MATERIALIZATION")
        self.assertIs(emitting["always_on_receiver_required"], False)
        self.assertIs(emitting["awaits_the_receiver"], False)
        self.assertEqual(emitting["absent_response_disposition"], "FAIL_CLOSED")
        self.assertEqual(emitting["non_allow_result_fields"], list(SIX))


class WorkflowNonAllow(SixFields):
    """N6: every FAIL_CLOSED the workflow prints carries six fields."""

    def test_every_fail_closed_line_carries_the_six_fields(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        starts = [match.start() for match in re.finditer(r'"disposition":\s*"(FAIL_CLOSED|DENY)"', text)]
        self.assertGreaterEqual(len(starts), 11)
        for start in starts:
            window = text[start:text.index("}", start)]
            for name in SIX:
                self.assertIn('"' + name + '"', window, window[:120])
        self.assertIsNone(re.search(r'FAIL_CLOSED: ', text))
        self.assertNotIn('"detail":', text)
        for line in text.splitlines():
            if "LEDGER_READBACK_UNAVAILABLE" in line:
                for name in SIX:
                    self.assertIn('"' + name + '"', line)

    def test_the_workflow_awaits_no_receiver(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        for needle in ("sleep ", "until ", "while true", "--wait", "timeout-minutes"):
            self.assertNotIn(needle, text)


class ExemptionRegister(SixFields):
    """N10: the register names each surface with an actionable non-ALLOW record."""

    def test_register_shape_and_entries(self):
        register = json.loads(REGISTER.read_text(encoding="utf-8"))
        self.assertEqual(register["schema"], "stegverse.organization-role-exemption-register/v1")
        self.assertEqual(register["organization"], "SV-011")
        self.assertIs(register["silent_non_conformance_is_conforming"], False)
        self.assertIs(register["exemption_may_satisfy_a_terminal_predicate"], False)
        self.assertEqual(register["authority_effect"], "NONE_REGISTER_ONLY")
        self.assertEqual(register["non_allow_result_fields"], list(SIX))
        prohibited = [item.lower().replace("_", " ") for item in register["prohibited_justifications"]]
        self.assertEqual({entry["audit_item"] for entry in register["exemptions"]}, {"N1", "N8", "M1"})
        for entry in register["exemptions"]:
            for name in register["required_fields"]:
                self.assertIn(name, entry, (name, entry["surface"]))
            self.assertIn(entry["disposition"], ("DENY", "FAIL_CLOSED"))
            self.assert_six(entry)
            self.assertRegex(entry["expires_or_review_on"], r"^2026-1[12]-[0-3][0-9]$")
            self.assertIn(entry["owning_existing_goal"], (LEDGER_GOAL, BOUNDARY_GOAL))
            why = entry["why_manifest_bound_state_transition_is_not_yet_possible"].lower()
            for phrase in prohibited:
                self.assertNotIn(phrase, why)
        self.assertNotIn("g" + "rant", REGISTER.read_text(encoding="utf-8").lower())

    def test_files_this_change_adds_use_constraint_wording(self):
        needles = ("g" + "rant", "authori" + "z", "permit", "allow" + "ed", "delegat", " may ")
        for relative in ("resident-runtime/non_allow.py", "docs/ORGANIZATION_ROLE_CONFORMANCE.md",
                         "data/organization-role-exemption-register.json",
                         ".stegverse/transition-requests/README.md",
                         "resident-runtime/organization_egress_boundary.py",
                         "org-runtime/interlock-intr.json"):
            text = (ROOT / relative).read_text(encoding="utf-8").lower()
            for needle in needles:
                self.assertNotIn(needle, text, relative + ": " + needle.strip())

    def test_conformance_doc_names_the_register_and_the_six_fields(self):
        doc = (ROOT / "docs/ORGANIZATION_ROLE_CONFORMANCE.md").read_text(encoding="utf-8")
        self.assertIn("data/organization-role-exemption-register.json", doc)
        for name in SIX:
            self.assertIn("`" + name + "`", doc)


if __name__ == "__main__":
    unittest.main()
