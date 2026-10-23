"""Governance-by-design tests for the SV-011 experiment-1 entity package (stdlib unittest)."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG))

from sv011_entity import bootstrap, entity, record  # noqa: E402
from sv011_entity.dispositions import SIX  # noqa: E402

EX = PKG / "examples"


def load_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.pkg = self.tmp / "pkg"
        shutil.copytree(PKG, self.pkg, ignore=shutil.ignore_patterns("tests", "__pycache__"))
        self.rec = self.tmp / "record"
        self.req_dir = self.tmp / "req"
        self.req_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def req(self, name: str, **changes) -> Path:
        body = load_json(EX / f"{name}-request.json")
        body.update(changes)
        path = self.req_dir / f"{name}-{len(list(self.req_dir.iterdir()))}.json"
        path.write_text(json.dumps(body), encoding="utf-8")
        return path

    def run_entity(self, request: Path | None):
        return entity.run(self.pkg, self.rec, request)

    def assertNonAllow(self, out, disposition, code=None):
        self.assertEqual(out["disposition"], disposition, out)
        for field in SIX:
            self.assertTrue(out.get(field), f"{field} missing in {out}")
        if code:
            self.assertEqual(out["failure_code"], code, out)

    def receipts(self):
        d = self.rec / "receipts"
        return sorted(d.iterdir()) if d.exists() else []


class CleanRunAndIdempotence(Base):
    def test_genesis_then_update_then_idempotent_rerun(self):
        self.assertEqual(self.run_entity(self.req("genesis"))["transition"], "APPLIED")
        update = self.req("update")
        self.assertEqual(self.run_entity(update)["transition"], "APPLIED")
        snapshot = {p.name: p.read_bytes() for p in self.receipts()}
        again = self.run_entity(update)
        self.assertEqual((again["disposition"], again["transition"]), ("ALLOW", "NONE"))
        self.assertEqual(self.run_entity(self.req("genesis"))["transition"], "NONE")
        self.assertEqual(snapshot, {p.name: p.read_bytes() for p in self.receipts()})
        self.assertEqual(self.run_entity(self.req("verify"))["chain_length"], 2)

    def test_receipt_carries_ae_contract_fields(self):
        self.run_entity(self.req("genesis"))
        contract = load_json(self.pkg / "components/Admissible-Existence/AE/.stegverse/transition-ledger/contract.json")
        r = load_json(self.receipts()[0])
        for f in contract["required_fields"]:
            self.assertIn(f, r)
        self.assertEqual(r["schema"], contract["receipt_schema"])
        self.assertIsNone(r["previous_receipt_sha256"])


class ProposeNeverTransitions(Base):
    def test_propose_writes_nothing(self):
        out = self.run_entity(self.req("genesis", action="PROPOSE"))
        self.assertEqual((out["disposition"], out["transitions_taken"]), ("ALLOW", 0))
        self.assertFalse(self.rec.exists())

    def test_predicate_failure_denies_and_writes_nothing(self):
        bad = load_json(EX / "genesis-request.json")["evidence"]
        bad["standing_results"] = []
        out = self.run_entity(self.req("genesis", evidence=bad))
        self.assertNonAllow(out, "DENY", "SV011_ENTITY_PREDICATE_NOT_SATISFIED")
        self.assertFalse(out["predicate_results"]["GTG-A3"])
        self.assertFalse(self.rec.exists())

    def test_stale_state_fails_closed(self):
        self.run_entity(self.req("genesis"))
        out = self.run_entity(self.req("update", predecessor_state_sha256=record.NULL_STATE_SHA256))
        self.assertNonAllow(out, "FAIL_CLOSED", "SV011_ENTITY_STATE_STALE")
        self.assertEqual(len(self.receipts()), 1)


class UndeclaredActions(Base):
    def test_undeclared_action_denied(self):
        self.assertNonAllow(self.run_entity(self.req("genesis", action="DELETE_RECORD")), "DENY", "SV011_ENTITY_ACTION_UNDECLARED")

    def test_undeclared_transition_class_denied(self):
        self.assertNonAllow(self.run_entity(self.req("genesis", transition_class="SELF_MODIFY")), "DENY",
                            "SV011_ENTITY_TRANSITION_CLASS_UNDECLARED")

    def test_undeclared_request_field_fails_closed(self):
        self.assertNonAllow(self.run_entity(self.req("genesis", extra_power=True)), "FAIL_CLOSED",
                            "SV011_ENTITY_ACTION_REQUEST_SHAPE")

    def test_no_request_fails_closed(self):
        self.assertNonAllow(self.run_entity(None), "FAIL_CLOSED", "SV011_ENTITY_ACTION_REQUEST_ABSENT")
        self.assertFalse(self.rec.exists())


class TamperDetected(Base):
    def setUp(self):
        super().setUp()
        self.run_entity(self.req("genesis"))
        self.run_entity(self.req("update"))

    def test_edited_receipt(self):
        p = self.receipts()[0]
        r = load_json(p)
        r["observed_at"] = "1999-01-01T00:00:00Z"
        p.write_text(json.dumps(r), encoding="utf-8")
        self.assertNonAllow(self.run_entity(self.req("verify")), "FAIL_CLOSED", "SV011_ENTITY_RECORD_HASH_MISMATCH")

    def test_resealed_receipt_breaks_link(self):
        p = self.receipts()[0]
        r = load_json(p)
        r["observed_at"] = "1999-01-01T00:00:00Z"
        p.write_text(json.dumps(record.seal(r)), encoding="utf-8")
        self.assertNonAllow(self.run_entity(self.req("verify")), "FAIL_CLOSED", "SV011_ENTITY_RECORD_LINK_BROKEN")

    def test_deleted_receipt(self):
        self.receipts()[0].unlink()
        self.assertNonAllow(self.run_entity(self.req("verify")), "FAIL_CLOSED", "SV011_ENTITY_RECORD_GAP")

    def test_tampered_record_blocks_transition(self):
        p = self.receipts()[1]
        p.write_text(p.read_text(encoding="utf-8").replace("ENTITY_STATE_UPDATE", "GENESIS"), encoding="utf-8")
        nxt = self.req("update", transition_id="u2", predecessor_state_sha256="sha256:x")
        self.assertNonAllow(self.run_entity(nxt), "FAIL_CLOSED", "SV011_ENTITY_RECORD_HASH_MISMATCH")


class MinimalityEachComponentRequired(Base):
    """Removing or altering any one declared component stops the entity (FAIL_CLOSED), never ungoverned."""

    def manifest(self):
        return load_json(self.pkg / bootstrap.MANIFEST_NAME)

    def write_manifest(self, m):
        (self.pkg / bootstrap.MANIFEST_NAME).write_text(json.dumps(m), encoding="utf-8")

    def assertStopped(self, code):
        gov, bad = bootstrap.load(self.pkg)
        self.assertIsNone(gov)
        self.assertNonAllow(bad, "FAIL_CLOSED", code)
        self.assertNonAllow(self.run_entity(self.req("genesis")), "FAIL_CLOSED", code)
        self.assertFalse(self.rec.exists())

    def test_full_manifest_loads(self):
        gov, bad = bootstrap.load(self.pkg)
        self.assertIsNone(bad)
        self.assertEqual(sorted(gov["manifest"]["components"][i]["role"] for i in range(3)), sorted(bootstrap.REQUIRED_ROLES))

    def test_drop_each_component_from_manifest(self):
        original = self.manifest()
        for role in bootstrap.REQUIRED_ROLES:
            with self.subTest(role=role):
                m = json.loads(json.dumps(original))
                m["components"] = [c for c in m["components"] if c["role"] != role]
                self.write_manifest(m)
                self.assertStopped("SV011_ENTITY_COMPONENT_UNDECLARED")
                self.write_manifest(original)

    def test_delete_each_component_file(self):
        for comp in self.manifest()["components"]:
            with self.subTest(role=comp["role"]):
                path = self.pkg / comp["local_path"]
                saved = path.read_bytes()
                path.unlink()
                self.assertStopped("SV011_ENTITY_COMPONENT_ABSENT")
                path.write_bytes(saved)

    def test_alter_each_component_file(self):
        for comp in self.manifest()["components"]:
            with self.subTest(role=comp["role"]):
                path = self.pkg / comp["local_path"]
                saved = path.read_bytes()
                path.write_bytes(saved + b"\n")
                self.assertStopped("SV011_ENTITY_COMPONENT_DIGEST_MISMATCH")
                path.write_bytes(saved)

    def test_undeclared_extra_component_rejected(self):
        m = self.manifest()
        m["components"].append(dict(m["components"][0], role="extra_unused"))
        self.write_manifest(m)
        self.assertStopped("SV011_ENTITY_COMPONENT_UNUSED")

    def test_missing_manifest(self):
        (self.pkg / bootstrap.MANIFEST_NAME).unlink()
        self.assertStopped("SV011_ENTITY_MANIFEST_ABSENT")


class Independence(unittest.TestCase):
    def test_no_intr_or_org_ledger_imports(self):
        for py in (PKG / "sv011_entity").glob("*.py"):
            text = py.read_text(encoding="utf-8")
            for banned in ("resident_runtime", "resident-runtime", "org_boundary", "org-boundary", "org_kernel",
                           "intr_path", "organization_ledger", "ledger_store", "import requests"):
                self.assertNotIn(banned, text, f"{py.name} references {banned}")


if __name__ == "__main__":
    unittest.main()
