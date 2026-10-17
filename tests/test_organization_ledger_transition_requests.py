"""Transition requests on the organization ledger layout already published.

The ledger in these tests is temporary. ``tests/fixtures/organization-ledger-2cc58cec``
holds the bytes of ``refs/stegverse/organization-ledger`` at commit
``2cc58cec07117909750a760933641725b86a204b`` (the intr-destination self-closure
appended by the 2026-10-17T02:55Z workflow run). They are read, never rewritten.

Authority and time are constraint variables. A disposition is evidence that a
transition occurred. ``production_receipt_claimed`` stays false.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "tests/fixtures/organization-ledger-2cc58cec"
LIVE_ORG_HEAD = "sha256:3db9008d0326bdfa687796308fd61dc16e11e299e1c3762e03e577cd1f23fe51"
LIVE_REPO_HEAD = "sha256:f8134042b274ab036fc022ea6d10d679d0284cfffe62bf3b3dba7f9c93881cd7"
LIVE_COMMUNICATION_ID = "intr-selfclose-SV-011-20261017"
REF = "refs/stegverse/organization-ledger"
NAMESPACE = "repository-ledger/SV-011/.github"
PYTHON = os.environ.get("PYTHON", "python3")

# The TT engine's own patterns and digest rule (Admissible-Existence/TT@ba3cca71,
# engine/sv011_next_required_capability.py), restated here so the test does
# not import the readback's copy of them.
TT_REQUIRED = {"state_id", "as_of", "fresh_until", "basis_digest",
               "declared_transition_element", "declared_role_escalation", "satisfied_scope_ids"}
TT_IDENT = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
TT_HEX64 = re.compile(r"^[0-9a-f]{64}$")
TT_TRANSITION_ID = re.compile(r"^T-[0-9]{3}$")
TT_FIXTURE_REF = re.compile(r"^fixtures/transition-elements/[a-z0-9.-]+\.json$")


def tt_basis_digest(current_state):
    body = {key: value for key, value in current_state.items() if key != "basis_digest"}
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _module(name, relative):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


readback = _module("organization_ledger_readback_requests", "resident-runtime/organization_ledger_readback.py")
transition = _module("organization_ledger_transition_requests", "resident-runtime/organization_ledger_transition.py")


def current_state(**overrides):
    state = {
        "state_id": "sv011.synthetic.state-1",
        "as_of": "2026-10-17T00:00:00Z",
        "fresh_until": "2026-10-18T00:00:00Z",
        "declared_transition_element": {
            "transition_id": "T-001",
            "fixture_ref": "fixtures/transition-elements/t-001.passive-observation.json",
        },
        "declared_role_escalation": {"role_id": "synthetic-role", "from_role": "context", "to_role": "evidence"},
        "satisfied_scope_ids": [],
    }
    state.update(overrides)
    return state


def request(request_id="SYNTHETIC-REQUEST-001", state=None, **overrides):
    document = {
        "schema": "stegverse.organization-transition-request/v1",
        "request_id": request_id,
        "owning_existing_goal": "SYNTHETIC-TEST-GOAL",
        "standing": {"mode": "synthetic", "node_ref": "unit-test"},
        "transition_id": "synthetic-transition-" + request_id,
        "transition_class": "SYNTHETIC_TRANSITION_REQUEST",
        "org_transition_class": "REPO_STATE_PROPAGATION",
        "predecessor_state_sha256": "sha256:" + "0" * 64,
        "successor_state_sha256": "sha256:" + "1" * 64,
        "authority_constraint": "NONE",
        "evidence": {},
        "hb_epoch": 32,
    }
    if state is not None:
        document["current_state"] = state
    document.update(overrides)
    return document


class LedgerRepo:
    """A temporary git repository holding the ledger ref."""

    def __init__(self, base: Path, seed: Path | None = None) -> None:
        self.path = base / "ledger"
        subprocess.run(["git", "init", "-q", str(self.path)], check=True)
        if seed is not None:
            self._seed(base, seed)
        self.org = "git+" + str(self.path.resolve()) + "#" + REF
        self.repo = self.org + ":" + NAMESPACE

    def _seed(self, base: Path, seed: Path) -> None:
        tree_dir = base / "seed"
        shutil.copytree(seed, tree_dir)
        env = {**os.environ, "GIT_INDEX_FILE": str(base / "seed.index"),
               "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
               "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.invalid"}
        git = ["git", "-C", str(self.path), "--work-tree", str(tree_dir)]
        subprocess.run(git + ["add", "-A", "."], check=True, env=env)
        tree = subprocess.check_output(git + ["write-tree"], env=env, text=True).strip()
        commit = subprocess.check_output(
            ["git", "-C", str(self.path), "commit-tree", tree, "-m", "seed"], env=env, text=True).strip()
        subprocess.run(["git", "-C", str(self.path), "update-ref", REF, commit], check=True)

    def tip(self) -> str:
        return subprocess.check_output(["git", "-C", str(self.path), "rev-parse", REF], text=True).strip()

    def show(self, path: str) -> bytes:
        return subprocess.check_output(["git", "-C", str(self.path), "show", REF + ":" + path])


class _EnvCase(unittest.TestCase):
    def setUp(self):
        self._work = tempfile.TemporaryDirectory()
        self.addCleanup(self._work.cleanup)
        self.base = Path(self._work.name)
        saved = {name: os.environ.get(name) for name in ("STEGVERSE_ORG_LEDGER_ROOT", "STEGVERSE_REPO_LEDGER_ROOT")}

        def restore():
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        self.addCleanup(restore)

    def bind(self, ledger: LedgerRepo) -> None:
        os.environ["STEGVERSE_ORG_LEDGER_ROOT"] = ledger.org
        os.environ["STEGVERSE_REPO_LEDGER_ROOT"] = ledger.repo

    def apply_cli(self, ledger: LedgerRepo, document) -> tuple[int, dict]:
        path = self.base / ("request-" + str(abs(hash(json.dumps(document, sort_keys=True)))) + ".json")
        path.write_text(json.dumps(document), encoding="utf-8")
        completed = subprocess.run(
            [PYTHON, str(ROOT / "resident-runtime/organization_ledger_transition.py"), "--request", str(path)],
            capture_output=True, text=True,
            env={**os.environ, "STEGVERSE_ORG_LEDGER_ROOT": ledger.org, "STEGVERSE_REPO_LEDGER_ROOT": ledger.repo})
        return completed.returncode, json.loads(completed.stdout)

    def readback_cli(self, location: str, transition_result=None, ingress_result=None) -> tuple[int, dict]:
        argv = [PYTHON, str(ROOT / "resident-runtime/organization_ledger_readback.py"),
                "--location", location, "--repository-namespace", NAMESPACE]
        if transition_result is not None:
            path = self.base / "transition-result.json"
            path.write_text(json.dumps(transition_result), encoding="utf-8")
            argv += ["--transition-result", str(path)]
        if ingress_result is not None:
            path = self.base / "ingress-result.json"
            path.write_text(json.dumps(ingress_result), encoding="utf-8")
            argv += ["--ingress-result", str(path)]
        completed = subprocess.run(argv, capture_output=True, text=True)
        return completed.returncode, json.loads(completed.stdout)


class LiveReceiptReadbackTests(_EnvCase):
    """(a) The receipt already on the published ref reads back on this layout."""

    def test_fixture_bytes_are_the_published_blobs(self):
        expected = {
            "HEAD.json": "11289bd60b1c1938eb6e94303dcaffb52d12b37d",
            "receipts/3db9008d0326bdfa687796308fd61dc16e11e299e1c3762e03e577cd1f23fe51.json":
                "f4350af8e75810634dd4594dc031780880705454",
            NAMESPACE + "/HEAD.json": "4027fcd1fcd43ac679cb7dee86f4e7c2138cad45",
            NAMESPACE + "/receipts/f8134042b274ab036fc022ea6d10d679d0284cfffe62bf3b3dba7f9c93881cd7.json":
                "4b6a6d76f2bc79bb8e5dc1ba1ece2b31c5808036",
        }
        for relative, blob in expected.items():
            data = (LIVE / relative).read_bytes()
            observed = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
            self.assertEqual(observed, blob, relative)

    def _assert_live_basis_absent(self, result):
        self.assertEqual(result["head_receipt_sha256"], LIVE_ORG_HEAD)
        self.assertEqual(result["chain_length"], 1)
        self.assertEqual(result["genesis_receipt_sha256"], LIVE_ORG_HEAD)
        self.assertIs(result["every_receipt_verifies_against_its_body"], True)
        self.assertEqual(result["repository_ledger"]["head_receipt_sha256"], LIVE_REPO_HEAD)
        self.assertEqual(result["repository_ledger"]["chain_length"], 1)
        self.assertIs(result["ledger_empty"], False)
        self.assertIs(result["head_receipt_declares_current_state"], False)
        self.assertIs(result["basis_established"], False)
        self.assertIsNone(result["current_state"])
        self.assertEqual(result["current_state_reason"], "BASIS_ABSENT")
        self.assertEqual(result["current_state_detail"], "HEAD_RECEIPT_DECLARES_NO_CURRENT_STATE")
        self.assertEqual(sorted(result["current_state_fields"]), sorted(readback.BASIS_INPUT_FIELDS))
        self.assertNotIn("failed_predicate", result)
        self.assertIs(result["production_receipt_claimed"], False)

    def test_directory_copy_of_the_live_ref_reads_back_with_basis_absent(self):
        copy = self.base / "live"
        shutil.copytree(LIVE, copy)
        self._assert_live_basis_absent(readback.readback(str(copy), None, NAMESPACE))

    def test_git_ref_with_the_live_tree_reads_back_and_the_closure_still_correlates(self):
        ledger = LedgerRepo(self.base, LIVE)
        closure = {"disposition": "ALLOW", "communication_id": LIVE_COMMUNICATION_ID,
                   "repository_receipt_sha256": LIVE_REPO_HEAD, "organization_receipt_sha256": LIVE_ORG_HEAD}
        code, result = self.readback_cli(ledger.org, ingress_result=closure)
        self.assertEqual(code, 0, result)
        self._assert_live_basis_absent(result)
        self.assertIs(result["ingress_organization_receipt_in_chain"], True)
        self.assertIs(result["communication_id_on_repository_receipt"], True)

    def test_a_request_appended_after_the_live_receipt_keeps_it_and_becomes_the_basis(self):
        ledger = LedgerRepo(self.base, LIVE)
        live_bytes = ledger.show("receipts/3db9008d0326bdfa687796308fd61dc16e11e299e1c3762e03e577cd1f23fe51.json")
        code, applied = self.apply_cli(ledger, request(state=current_state()))
        self.assertEqual(code, 0, applied)
        self.assertEqual(ledger.show("receipts/3db9008d0326bdfa687796308fd61dc16e11e299e1c3762e03e577cd1f23fe51.json"),
                         live_bytes)
        code, result = self.readback_cli(ledger.org, transition_result=applied)
        self.assertEqual(code, 0, result)
        self.assertEqual(result["chain_length"], 2)
        self.assertEqual(result["genesis_receipt_sha256"], LIVE_ORG_HEAD)
        self.assertEqual(result["repository_ledger"]["chain_length"], 2)
        self.assertEqual(result["repository_ledger"]["genesis_receipt_sha256"], LIVE_REPO_HEAD)
        self.assertIs(result["request_receipts_in_chain"], True)
        self.assertIs(result["request_receipt_is_head"], True)
        self.assertIs(result["basis_established"], True)

    def test_an_empty_location_is_reported_as_an_empty_ledger(self):
        empty = self.base / "empty"
        empty.mkdir()
        result = readback.readback(str(empty), None, NAMESPACE)
        self.assertIs(result["ledger_empty"], True)
        self.assertEqual(result["chain_length"], 0)
        self.assertIsNone(result["current_state"])
        self.assertEqual(result["current_state_reason"], "BASIS_ABSENT")
        self.assertEqual(result["current_state_detail"], "ORGANIZATION_LEDGER_EMPTY")


class RequestIntakeTests(_EnvCase):
    """(b) intake -> readback yields a TT-conformant current_state."""

    def test_synthetic_request_reads_back_a_tt_conformant_current_state(self):
        ledger = LedgerRepo(self.base)
        code, applied = self.apply_cli(ledger, request(state=current_state()))
        self.assertEqual(code, 0, applied)
        self.assertIs(applied["organization_receipt_preserves_repository_receipt"], True)
        self.assertIs(applied["disposition_is_transition_evidence"], True)
        self.assertNotIn("basis_digest", applied["current_state_recorded"])

        fresh = self.base / "fresh.git"
        subprocess.run(["git", "init", "--bare", "-q", str(fresh)], check=True)
        subprocess.run(["git", "--git-dir", str(fresh), "fetch", "-q", str(ledger.path), "+" + REF + ":" + REF],
                       check=True)
        code, result = self.readback_cli("git+" + str(fresh.resolve()) + "#" + REF, transition_result=applied)
        self.assertEqual(code, 0, result)
        self.assertIs(result["basis_established"], True)
        self.assertEqual(result["current_state_reason"], "HEAD_RECEIPT_CURRENT_STATE")
        state = result["current_state"]
        self.assertEqual(set(state), TT_REQUIRED)
        self.assertTrue(TT_HEX64.fullmatch(state["basis_digest"]))
        self.assertEqual(state["basis_digest"], tt_basis_digest(state))
        self.assertTrue(TT_IDENT.fullmatch(state["state_id"]))
        self.assertEqual(set(state["declared_transition_element"]), {"transition_id", "fixture_ref"})
        self.assertTrue(TT_TRANSITION_ID.fullmatch(state["declared_transition_element"]["transition_id"]))
        self.assertTrue(TT_FIXTURE_REF.fullmatch(state["declared_transition_element"]["fixture_ref"]))
        self.assertEqual(set(state["declared_role_escalation"]), {"role_id", "from_role", "to_role"})
        self.assertLessEqual(state["as_of"], state["fresh_until"])
        expected = dict(current_state())
        expected["basis_digest"] = tt_basis_digest(expected)
        self.assertEqual(state, expected)
        self.assertEqual(result["request_id"], "SYNTHETIC-REQUEST-001")

    def test_reapplying_the_same_request_returns_the_recorded_receipts(self):
        ledger = LedgerRepo(self.base)
        _, first = self.apply_cli(ledger, request(state=current_state()))
        tip = ledger.tip()
        code, second = self.apply_cli(ledger, request(state=current_state()))
        self.assertEqual(code, 0, second)
        self.assertEqual(second["organization_receipt_sha256"], first["organization_receipt_sha256"])
        self.assertEqual(second["repository_receipt_sha256"], first["repository_receipt_sha256"])
        self.assertEqual(ledger.tip(), tip)

    def test_a_request_without_current_state_appends_and_reads_back_basis_absent(self):
        ledger = LedgerRepo(self.base)
        code, applied = self.apply_cli(ledger, request())
        self.assertEqual(code, 0, applied)
        code, result = self.readback_cli(ledger.org, transition_result=applied)
        self.assertEqual(code, 0, result)
        self.assertEqual(result["current_state_reason"], "BASIS_ABSENT")
        self.assertEqual(result["current_state_detail"], "HEAD_RECEIPT_DECLARES_NO_CURRENT_STATE")

    def test_an_sdk_manifest_is_refused_and_nothing_is_appended(self):
        ledger = LedgerRepo(self.base)
        for key in ("manifest", "build_manifest"):
            code, refused = self.apply_cli(ledger, request(**{key: {}}))
            self.assertEqual(code, 1)
            self.assertEqual(refused["failed_predicate"], "SDK_MANIFEST_BUILDER_NOT_PRESENT")
            self.assertIs(refused["consequence_committed"], False)
        self.assertNotEqual(subprocess.run(["git", "-C", str(ledger.path), "rev-parse", "--verify", "--quiet", REF],
                                           capture_output=True).returncode, 0)

    def test_a_current_state_that_is_not_a_basis_is_refused_before_any_append(self):
        ledger = LedgerRepo(self.base)
        code, refused = self.apply_cli(ledger, request(state=current_state(as_of="2026-10-19T00:00:00Z")))
        self.assertEqual(code, 1)
        self.assertEqual(refused["failed_predicate"], "TRANSITION_REQUEST_CURRENT_STATE_IS_A_BASIS")
        self.assertTrue(refused["required_evidence_or_repair"].startswith("BASIS_STALE"))
        self.assertNotEqual(subprocess.run(["git", "-C", str(ledger.path), "rev-parse", "--verify", "--quiet", REF],
                                           capture_output=True).returncode, 0)

    def test_the_committed_request_tree_validates(self):
        self.assertIsInstance(transition.validate_tree(ROOT / ".stegverse/transition-requests"), list)


class BasisProjectionTests(unittest.TestCase):
    """(c) fixture_ref and the other basis codes follow the TT contract."""

    def project(self, state):
        return readback.project_current_state({"current_state": state})

    def test_fixture_ref_uses_the_tt_engine_pattern_and_is_not_opened_here(self):
        accepted = self.project(current_state())
        self.assertIs(accepted["basis_established"], True)
        self.assertIs(accepted["fixture_ref_resolution"]["fixture_opened"], False)
        self.assertEqual(accepted["fixture_ref_resolution"]["checked_here"], "PATH_PATTERN_ONLY")
        self.assertEqual(accepted["fixture_ref_resolution"]["missing_fixture_at_that_root"], "BASIS_UNKNOWN")
        for fixture_ref in ("fixtures/transition-elements/T-001.json",
                            "fixtures/transition-elements/t_001.json",
                            "fixtures/other/t-001.json",
                            "fixtures/transition-elements/../t-001.json",
                            "construction/first-transition-element.json"):
            element = {"transition_id": "T-001", "fixture_ref": fixture_ref}
            projected = self.project(current_state(declared_transition_element=element))
            self.assertEqual(projected["current_state_reason"], "BASIS_UNKNOWN", fixture_ref)
            self.assertEqual(projected["current_state_fields"], ["declared_transition_element"])
            self.assertEqual(bool(TT_FIXTURE_REF.fullmatch(fixture_ref)), False, fixture_ref)
        for element in ({"transition_id": "T-1", "fixture_ref": "fixtures/transition-elements/t-001.json"},
                        {"transition_id": "T-001"},
                        {"transition_id": "T-001", "fixture_ref": "fixtures/transition-elements/t-001.json", "x": 1}):
            self.assertEqual(self.project(current_state(declared_transition_element=element))["current_state_reason"],
                             "BASIS_UNKNOWN")

    def test_basis_codes(self):
        cases = [
            ({"state_id": None}, "BASIS_ABSENT"),
            ({"extra": 1}, "BASIS_UNKNOWN"),
            ({"state_id": "has space"}, "BASIS_UNKNOWN"),
            ({"as_of": "2026-10-17 00:00:00"}, "BASIS_UNKNOWN"),
            ({"satisfied_scope_ids": ["something_else"]}, "BASIS_UNKNOWN"),
            ({"as_of": "2026-10-19T00:00:00Z"}, "BASIS_STALE"),
            ({"declared_role_escalation": {"role_id": "r", "from_role": "a", "to_role": "a"}}, "BASIS_CONTRADICTORY"),
            ({"satisfied_scope_ids": ["role_escalation"]}, "BASIS_CONTRADICTORY"),
            ({"satisfied_scope_ids": ["transition_element", "transition_element"]}, "BASIS_CONTRADICTORY"),
            ({"basis_digest": "0" * 64}, "BASIS_CONTRADICTORY"),
            ({"basis_digest": "sha256:" + "0" * 64}, "BASIS_UNKNOWN"),
        ]
        for overrides, code in cases:
            state = current_state(**overrides)
            if overrides.get("state_id", "") is None:
                state.pop("state_id")
            projected = self.project(state)
            self.assertEqual(projected["current_state_reason"], code, overrides)
            self.assertIsNone(projected["current_state"])
            self.assertIs(projected["basis_established"], False)
        self.assertEqual(readback.project_current_state({})["current_state_detail"],
                         "HEAD_RECEIPT_DECLARES_NO_CURRENT_STATE")
        self.assertEqual(readback.project_current_state({"current_state": []})["current_state_reason"],
                         "BASIS_UNKNOWN")

    def test_a_matching_declared_digest_and_full_scope_prefixes_are_accepted(self):
        for scopes in ([], ["transition_element"], ["transition_element", "role_escalation"]):
            state = current_state(satisfied_scope_ids=scopes)
            state["basis_digest"] = tt_basis_digest(state)
            projected = self.project(state)
            self.assertIs(projected["basis_established"], True, scopes)
            self.assertEqual(projected["current_state"]["basis_digest"], state["basis_digest"])


class ContractAndWordingTests(unittest.TestCase):
    TOUCHED = (
        ".stegverse/transition-ledger/org-contract.json",
        ".stegverse/transition-requests/README.md",
        "resident-runtime/aggregate_repo_transition.py",
        "resident-runtime/organization_ledger_readback.py",
        "resident-runtime/organization_ledger_transition.py",
        ".github/workflows/organization-ledger-transition.yml",
    )

    def test_the_contract_declares_the_intake_and_the_basis_source(self):
        contract = json.loads((ROOT / ".stegverse/transition-ledger/org-contract.json").read_text(encoding="utf-8"))
        intake = contract["transition_request_intake"]
        self.assertEqual(intake["directory"], ".stegverse/transition-requests/")
        self.assertEqual(intake["write_mode"], "MANIFEST_DIRECTED_APPEND")
        self.assertEqual(intake["sdk_manifest"], "REFUSED_SDK_MANIFEST_BUILDER_NOT_PRESENT")
        basis = contract["current_state_basis"]
        self.assertEqual(basis["source_commit"], "ba3cca71b6928cf739ab6b321c939860fd1edbb1")
        self.assertEqual(basis["source_blob"], "8f7d4cc2127325b155e1a91edfe531b5737f7c75")
        self.assertEqual(tuple(basis["stored_fields"]), readback.BASIS_INPUT_FIELDS)
        self.assertEqual(contract["optional_fields"], ["current_state"])
        self.assertNotIn("current_state", contract["required_fields"])
        located = readback.designated_locators(ROOT)
        self.assertEqual(located["namespace"], NAMESPACE)

    def test_touched_files_use_constraint_wording(self):
        needles = ("g" + "rant", "authori" + "z", "permit", "allow" + "ed", "delegat", " may ")
        for relative in self.TOUCHED:
            text = (ROOT / relative).read_text(encoding="utf-8").lower()
            for needle in needles:
                self.assertNotIn(needle, text, relative + ": " + needle.strip())


if __name__ == "__main__":
    unittest.main()
