"""The committed request is bound to every receipt its self-closure produced.

The InTr ingress writes the repository and organization receipts unchanged;
resident-runtime/committed_request_binding.py appends one companion
stegverse.committed-request-binding-receipt/v1 on the same ledger that records
the committed request and the digest and parent of each bound receipt, and
verifies it from the ledger alone. Stdlib only.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIX_FIELDS = ("failure_code", "failed_predicate", "required_evidence_or_repair",
              "retry_entrypoint", "owning_existing_goal", "next_attempt")
LEDGER_ROOTS = ("STEGVERSE_REPO_LEDGER_ROOT", "STEGVERSE_ORG_LEDGER_ROOT")


def _module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


binding = _module("committed_request_binding_under_test", "resident-runtime/committed_request_binding.py")
ingress = _module("intr_destination_ingress_for_binding", "resident-runtime/intr_destination_ingress.py")
readback = _module("organization_ledger_readback_for_binding", "resident-runtime/organization_ledger_readback.py")
REGISTRY = json.loads((ROOT / "org-boundary/registry/services.json").read_text(encoding="utf-8"))
NAMESPACE = json.loads((ROOT / ".stegverse/transition-ledger/contract.json").read_text(
    encoding="utf-8"))["repository_ledger_location"]["namespace"]
REQUEST_FILES = {name: json.dumps({"schema": "stegverse.org-ledger-transition-request/v1", "communication_id": "comm-1",
                                    "packet_id": "packet-" + name, "hb_epoch": 33}).encode()
                 for name in ("a", "d", "e", "f")}


def request_pair(name):
    return {"path": ".stegverse/transition-ledger/requests/" + name + ".json",
            "sha256": "sha256:" + hashlib.sha256(REQUEST_FILES[name]).hexdigest()}


REQUEST, REQ_D, REQ_E, REQ_F = (request_pair(name) for name in ("a", "d", "e", "f"))


class Ledgers(unittest.TestCase):
    def setUp(self):
        self._work = tempfile.TemporaryDirectory()
        self.addCleanup(self._work.cleanup)
        self.work = Path(self._work.name)
        previous = {name: os.environ.get(name) for name in LEDGER_ROOTS + (binding.SOURCE_VARIABLE,)}
        self.addCleanup(lambda: [os.environ.pop(n, None) if v is None else os.environ.__setitem__(n, v)
                                 for n, v in previous.items()])
        self.use_directories()
        self.commit_requests()

    def commit_requests(self):
        """The repository the requests are committed in; bind and verify check digests against it."""
        source = self.work / "source"
        for name, content in REQUEST_FILES.items():
            path = source / ".stegverse/transition-ledger/requests" / (name + ".json")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        git = ("git", "-C", str(source))
        subprocess.run(git[:1] + ("init", "-q", str(source)), check=True)
        subprocess.run(git + ("add", "-A"), check=True)
        subprocess.run(git + ("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "r"),
                       check=True)
        revision = subprocess.run(git + ("rev-parse", "HEAD"), check=True, capture_output=True,
                                  text=True).stdout.strip()
        os.environ[binding.SOURCE_VARIABLE] = str(source) + "#" + revision

    def use_directories(self):
        os.environ["STEGVERSE_REPO_LEDGER_ROOT"] = str(self.work / "repo")
        os.environ["STEGVERSE_ORG_LEDGER_ROOT"] = str(self.work / "org")

    def closure(self, packet="packet-1"):
        result = ingress.self_closure("comm-1", registry=REGISTRY, packet_id=packet, hb_epoch=33)
        self.assertEqual(result["disposition"], "ALLOW", result)
        return result

    def files(self, schema):
        found = []
        for name in LEDGER_ROOTS:
            for path in Path(os.environ[name]).rglob("*.json"):
                try:
                    record = json.loads(path.read_text())
                except ValueError:
                    continue
                if record.get("schema") == schema:
                    found.append((path, record))
        return found

    def assert_refused(self, result, code):
        self.assertEqual((result["disposition"], result["failure_code"]), ("FAIL_CLOSED", code), result)
        self.assertIs(result["terminal"], True)
        for field in SIX_FIELDS:
            self.assertTrue(result.get(field), field)


class Binding(Ledgers):
    def test_the_ledger_holds_both_receipts_and_one_binding_with_matching_hashes(self):
        closure = self.closure()
        bound = binding.bind(REQUEST, closure, 33)
        self.assertEqual(bound["disposition"], "ALLOW", bound)
        (_, repository), = self.files("stegverse.repo-transition-receipt/v1")
        (_, organization), = self.files("stegverse.organization-transition-receipt/v1")
        (_, receipt), = self.files(binding.SCHEMA)
        self.assertEqual(receipt["committed_request"],
                         {**REQUEST, "hb_epoch_source": "COMMITTED_REQUEST"})
        self.assertEqual(receipt["bound_receipts"], [
            {"level": "repository", "receipt_sha256": repository["receipt_sha256"],
             "previous_receipt_sha256": repository.get("previous_receipt_sha256")},
            {"level": "organization", "receipt_sha256": organization["receipt_sha256"],
             "previous_receipt_sha256": organization.get("previous_receipt_sha256")},
        ])
        self.assertEqual(closure["repository_receipt_sha256"], repository["receipt_sha256"])
        self.assertEqual(closure["organization_receipt_sha256"], organization["receipt_sha256"])
        self.assertEqual(receipt["receipt_sha256"], binding.sha(
            {k: v for k, v in receipt.items() if k != "receipt_sha256"}))
        verified = binding.verify(REQUEST, closure)
        self.assertEqual((verified["disposition"], verified["binding_receipt_sha256"]),
                         ("ALLOW", receipt["receipt_sha256"]))

    def test_a_replay_returns_the_recorded_binding(self):
        closure = self.closure()
        first = binding.bind(REQUEST, closure, 33)
        second = binding.bind(REQUEST, closure, 33)
        self.assertIs(second["replayed"], True)
        self.assertEqual(first["binding_receipt_sha256"], second["binding_receipt_sha256"])
        self.assertEqual(len(self.files(binding.SCHEMA)), 1)

    def test_a_replay_under_a_different_request_is_refused(self):
        closure = self.closure()
        first = binding.bind(REQUEST, closure, 33)
        replayed = self.closure()  # same packet: the ingress answers with the recorded receipts
        self.assertIs(replayed["transition_replayed"], True)
        for other in (REQ_D, REQ_F):
            result = binding.bind(other, replayed, 33)
            self.assert_refused(result, "COMMITTED_REQUEST_REPLAY_MISMATCH")
            self.assertIn(first["binding_receipt_sha256"], result["detail"])
        self.assertEqual(len(self.files(binding.SCHEMA)), 1)

    def test_a_tampered_bound_receipt_fails_verification(self):
        closure = self.closure()
        binding.bind(REQUEST, closure, 33)
        (path, repository), = self.files("stegverse.repo-transition-receipt/v1")
        repository["evidence"]["communication_id"] = "comm-forged"
        path.write_text(json.dumps(repository))
        self.assert_refused(binding.verify(REQUEST, closure), "BOUND_RECEIPT_UNREADABLE")

    def test_a_tampered_binding_receipt_fails_verification(self):
        closure = self.closure()
        binding.bind(REQUEST, closure, 33)
        (path, receipt), = self.files(binding.SCHEMA)
        receipt["hb_epoch"] = 99
        path.write_text(json.dumps(receipt))
        self.assert_refused(binding.verify(REQUEST, closure), "COMMITTED_REQUEST_BINDING_CHAIN_INVALID")

    def test_each_binding_records_the_organization_head_at_append(self):
        closure = self.closure()
        receipt = binding.bind(REQUEST, closure, 33)["binding_receipt"]
        self.assertEqual(receipt["organization_head_sha256"], closure["organization_receipt_sha256"])

    def test_the_bindings_chain_is_hash_linked_and_append_only(self):
        first = binding.bind(REQUEST, self.closure("packet-1"), 33)
        other = REQ_E
        second_closure = self.closure("packet-2")
        second = binding.bind(other, second_closure, 33)
        self.assertEqual(second["binding_receipt"]["previous_binding_sha256"], first["binding_receipt_sha256"])
        self.assertEqual([digest for digest, _ in binding.chain(binding.stores()["organization"])],
                         [second["binding_receipt_sha256"], first["binding_receipt_sha256"]])
        self.assertEqual(binding.verify(other, second_closure)["bindings_chain_length"], 2)
        # an earlier binding rewritten in place breaks the chain for every later verify
        (path, older), = [(p, r) for p, r in self.files(binding.SCHEMA)
                          if r["receipt_sha256"] == first["binding_receipt_sha256"]]
        older["committed_request"]["path"] = "rewritten.json"
        path.write_text(json.dumps(older))
        self.assert_refused(binding.verify(other, second_closure), "COMMITTED_REQUEST_BINDING_CHAIN_INVALID")

    def test_receipts_without_a_binding_are_binding_absent_never_allow(self):
        closure = self.closure()
        self.assert_refused(binding.verify(REQUEST, closure), "COMMITTED_REQUEST_BINDING_ABSENT")

    def test_receipts_bound_to_another_request_are_binding_absent(self):
        closure = self.closure()
        binding.bind(REQUEST, closure, 33)
        other = REQ_F
        self.assert_refused(binding.verify(other, closure), "COMMITTED_REQUEST_BINDING_ABSENT")

    def test_a_binding_whose_organization_receipt_is_gone_is_detected(self):
        closure = self.closure()
        binding.bind(REQUEST, closure, 33)
        (path, _), = self.files("stegverse.organization-transition-receipt/v1")
        path.unlink()
        self.assert_refused(binding.verify(REQUEST, closure), "BOUND_RECEIPT_UNREADABLE")

    def test_a_malformed_or_half_set_request_is_terminal_and_writes_nothing(self):
        closure = self.closure()
        for pair in ({"path": REQUEST["path"]}, {"sha256": REQUEST["sha256"]},
                     {"path": "", "sha256": REQUEST["sha256"]}, {"path": "a\nb", "sha256": REQUEST["sha256"]},
                     {"path": REQUEST["path"], "sha256": "sha256:abc"}):
            self.assert_refused(binding.bind(pair, closure, 33), "COMMITTED_REQUEST_INVALID")
        self.assertEqual(self.files(binding.SCHEMA), [])

    def test_a_missing_or_unreadable_bound_receipt_is_terminal_and_writes_nothing(self):
        closure = self.closure()
        missing = {**closure, "organization_receipt_sha256": "sha256:" + "0" * 64}
        self.assert_refused(binding.bind(REQUEST, missing, 33), "BOUND_RECEIPT_UNREADABLE")
        self.assert_refused(binding.bind(REQUEST, {**closure, "repository_receipt_sha256": None}, 33),
                            "BOUND_RECEIPT_UNREADABLE")
        (path, repository), = self.files("stegverse.repo-transition-receipt/v1")
        path.write_text("{not json")
        self.assert_refused(binding.bind(REQUEST, closure, 33), "LEDGER_RECEIPT_CORRUPT")
        self.assertEqual(self.files(binding.SCHEMA), [])

    def test_a_non_finite_value_is_never_written(self):
        with self.assertRaises(ValueError):
            binding.canon({"x": float("nan")})


class ForgeryProbe(Ledgers):
    """A committed_request whose digest is not the committed file is COMMITTED_REQUEST_DIGEST_UNVERIFIED."""

    def test_a_forged_digest_is_never_bound(self):
        closure = self.closure()
        forged = {**REQUEST, "sha256": "sha256:" + hashlib.sha256(b'{"forged": true}').hexdigest()}
        for pair in (forged, {**REQ_D, "path": REQUEST["path"]},
                     {"path": ".stegverse/transition-ledger/requests/absent.json", "sha256": REQUEST["sha256"]},
                     {"path": "elsewhere/a.json", "sha256": REQUEST["sha256"]}):
            self.assert_refused(binding.bind(pair, closure, 33), "COMMITTED_REQUEST_DIGEST_UNVERIFIED")
        self.assertEqual(self.files(binding.SCHEMA), [])

    def test_a_rehashed_off_ledger_binding_with_a_forged_request_does_not_verify(self):
        closure = self.closure()
        genuine = binding.bind(REQUEST, closure, 33)["binding_receipt"]
        forged_request = {**REQUEST, "sha256": "sha256:" + hashlib.sha256(b"forged").hexdigest()}
        body = {k: v for k, v in genuine.items() if k != "receipt_sha256"}
        body["committed_request"] = {**forged_request, "hb_epoch_source": "COMMITTED_REQUEST"}
        body["previous_binding_sha256"] = genuine["receipt_sha256"]
        forged = {**body, "receipt_sha256": binding.sha(body)}  # rehashed, so it seals
        org = binding.stores()["organization"]
        binding._put(org, binding.PREFIX + forged["receipt_sha256"][7:] + ".json", forged)
        binding._put(org, binding.HEAD, {"schema": binding.HEAD_SCHEMA, "receipt_sha256": forged["receipt_sha256"]})
        self.assert_refused(binding.verify(forged_request, closure), "COMMITTED_REQUEST_DIGEST_UNVERIFIED")
        # and the genuine request no longer verifies silently: the newest binding names another request
        self.assert_refused(binding.verify(REQUEST, closure), "COMMITTED_REQUEST_BINDING_ABSENT")

    def test_no_source_means_no_binding(self):
        os.environ.pop(binding.SOURCE_VARIABLE)
        result = binding.bind(REQUEST, self.closure(), 33)
        self.assertEqual((result["failure_code"], result["terminal"]), ("LEDGER_ENVIRONMENT_UNAVAILABLE_RETRY", False))
        self.assertEqual(self.files(binding.SCHEMA), [])


class ValidatorRows(Ledgers):
    """V-P11 rows 5-7 (forgery probes) and row 3 (git unavailable), in this lane's code."""

    MODULE = ROOT / "resident-runtime/committed_request_binding.py"

    def cli(self, action, request_doc, ingress_doc=None, env=None, extra=()):
        request_path = self.work / ("request-" + action + ".json")
        request_path.write_text(json.dumps(request_doc))
        args = [sys.executable, str(self.MODULE), action, "--request-result", str(request_path),
                "--out", str(self.work / ("out-" + action + ".json")), *extra]
        if ingress_doc is not None:
            ingress_path = self.work / ("ingress-" + action + ".json")
            ingress_path.write_text(json.dumps(ingress_doc))
            args += ["--ingress-result", str(ingress_path)]
        completed = subprocess.run(args, capture_output=True, text=True, env={**os.environ, **(env or {})})
        return completed.returncode, json.loads(completed.stdout)

    def request_doc(self, pair=REQUEST, **extra):
        return {"disposition": "ALLOW", "request_path": pair["path"], "request_sha256": pair["sha256"],
                "hb_epoch": 33, **extra}

    def test_row5_committed_request_injected_into_evidence_json_at_any_depth_is_refused(self):
        closure = self.closure()
        forged = {"path": REQUEST["path"], "sha256": "sha256:" + "9" * 64}
        for request_doc, ingress_doc in (
                (self.request_doc(committed_request=forged), closure),
                (self.request_doc(), {**closure, "committed_request": forged}),
                (self.request_doc(), {**closure, "nested": [{"deeper": {"committed_request": forged}}]})):
            for action in ("bind", "verify"):
                code, result = self.cli(action, request_doc, ingress_doc)
                self.assertEqual(code, 1)
                self.assert_refused(result, "FORGED_COMMITTED_REQUEST_FROM_EVIDENCE_JSON")
        code, result = self.cli("precheck", self.request_doc(committed_request=forged))
        self.assert_refused(result, "FORGED_COMMITTED_REQUEST_FROM_EVIDENCE_JSON")
        self.assertEqual(self.files(binding.SCHEMA), [])

    def test_row6_the_request_is_refused_before_any_write_and_before_publish(self):
        forged = {**REQUEST, "sha256": "sha256:" + hashlib.sha256(b"forged").hexdigest()}
        code, result = self.cli("precheck", self.request_doc(forged))
        self.assertEqual(code, 1)
        self.assert_refused(result, "COMMITTED_REQUEST_DIGEST_UNVERIFIED")
        self.assertEqual(self.cli("precheck", self.request_doc())[1]["disposition"], "ALLOW")
        writer = (ROOT / ".github/workflows/organization-ledger-transition-writer.yml").read_text()
        order = [writer.index(marker) for marker in (
            "committed_request_binding.py precheck",       # before the ingress append writes anything
            "intr_destination_ingress.py --self-closure",
            "committed_request_binding.py bind",
            "committed_request_binding.py seal",
            "--out \"$RUNNER_TEMP/results/binding-verify-local.json\"",  # verify on the local ref
            "- name: Publish the ledger ref (fast-forward only)",     # then publish
        )]
        self.assertEqual(order, sorted(order))

    def test_row7_an_off_ledger_receipt_is_never_bound(self):
        closure = self.closure()
        repo_store = binding.stores()["repository"]
        (_, genuine), = self.files("stegverse.repo-transition-receipt/v1")
        body = {k: v for k, v in genuine.items() if k != "receipt_sha256"}
        body["evidence"] = {**body["evidence"], "communication_id": "comm-off-ledger"}
        off = {**body, "receipt_sha256": binding.sha(body)}  # rehashed: it seals, but no HEAD reaches it
        binding._put(repo_store, binding.ledger_store.receipt_key(off["receipt_sha256"]), off)
        result = binding.bind(REQUEST, {**closure, "repository_receipt_sha256": off["receipt_sha256"]}, 33)
        self.assert_refused(result, "COMMITTED_REQUEST_DIGEST_UNVERIFIED")
        self.assertEqual(result["failed_predicate"], "BOUND_RECEIPT_IS_ON_THE_LEDGER_REACHABLE_FROM_HEAD")
        self.assertEqual(self.files(binding.SCHEMA), [])

    def test_row7_a_bound_receipt_carrying_another_request_digest_is_never_bound(self):
        closure = self.closure()
        repo_store = binding.stores()["repository"]
        (_, genuine), = self.files("stegverse.repo-transition-receipt/v1")
        body = {k: v for k, v in genuine.items() if k != "receipt_sha256"}
        body["evidence"] = {**body["evidence"], "committed_request": {**REQ_D, "hb_epoch_source": "COMMITTED_REQUEST"}}
        body["previous_receipt_sha256"] = genuine["receipt_sha256"]
        forged = {**body, "receipt_sha256": binding.sha(body)}
        binding._put(repo_store, binding.ledger_store.receipt_key(forged["receipt_sha256"]), forged)
        head = repo_store.get(binding.ORG_HEAD)
        binding._put(repo_store, binding.ORG_HEAD, {**head, "receipt_sha256": forged["receipt_sha256"]})
        result = binding.bind(REQUEST, {**closure, "repository_receipt_sha256": forged["receipt_sha256"]}, 33)
        self.assert_refused(result, "COMMITTED_REQUEST_DIGEST_UNVERIFIED")
        self.assertEqual(result["failed_predicate"], "BOUND_RECEIPT_CARRIES_THIS_RUNS_REQUEST_DIGEST")
        self.assertEqual(self.files(binding.SCHEMA), [])

    def test_row2_an_unset_ledger_environment_is_a_non_terminal_retry_with_exit_75(self):
        closure = self.closure()
        for variable in ("STEGVERSE_ORG_LEDGER_ROOT", "STEGVERSE_REPO_LEDGER_ROOT", binding.SOURCE_VARIABLE):
            for action, ingress_doc in (("precheck", None), ("bind", closure), ("seal", closure), ("verify", closure)):
                env = {k: v for k, v in os.environ.items() if k != variable}
                args = [sys.executable, str(self.MODULE), action, "--request-result",
                        str(self.work / "r.json"), "--out", str(self.work / "o.json")]
                (self.work / "r.json").write_text(json.dumps(self.request_doc()))
                if ingress_doc is not None:
                    (self.work / "i.json").write_text(json.dumps(ingress_doc))
                    args += ["--ingress-result", str(self.work / "i.json")]
                completed = subprocess.run(args, capture_output=True, text=True, env=env)
                result = json.loads(completed.stdout)
                if variable != binding.SOURCE_VARIABLE and action == "precheck":
                    self.assertEqual(completed.returncode, 0)  # precheck reads no ledger
                    continue
                if variable == "STEGVERSE_REPO_LEDGER_ROOT" and action == "seal":
                    continue  # seal folds the organization ref only
                if variable == binding.SOURCE_VARIABLE and action == "seal":
                    continue  # seal reads no request
                self.assertEqual(completed.returncode, 75, (variable, action, result))
                self.assertEqual(result["failure_code"], "LEDGER_ENVIRONMENT_UNAVAILABLE_RETRY")
                self.assertIs(result["terminal"], False)
                self.assertNotIn("Traceback", completed.stderr)
        env = {k: v for k, v in os.environ.items() if k != "STEGVERSE_ORG_LEDGER_ROOT"}
        out = self.work / "tr.json"
        completed = subprocess.run([sys.executable, str(ROOT / "resident-runtime/transition_request.py"),
                                    "--before", "0" * 40, "--after", "1" * 40, "--out", str(out)],
                                   capture_output=True, text=True, env=env)
        self.assertEqual(completed.returncode, 75, completed.stdout + completed.stderr)
        result = json.loads(out.read_text())
        self.assertEqual((result["failure_code"], result["terminal"]), ("LEDGER_ENVIRONMENT_UNAVAILABLE_RETRY", False))
        for field in SIX_FIELDS:
            self.assertTrue(result.get(field), field)

    def test_an_unreadable_store_locator_writes_nothing_downstream(self):
        closure = {"repository_receipt_sha256": "sha256:" + "b" * 64,
                   "organization_receipt_sha256": "sha256:" + "c" * 64}
        not_a_dir = self.work / "not-a-dir"
        not_a_dir.write_text("x")
        locators = {
            "a regular file": (str(not_a_dir), str(not_a_dir)),
            "an absent directory": (str(self.work / "absent"), str(self.work / "absent")),
            "an absent git repository": ("git+" + str(self.work / "absent.git") + "#refs/x",
                                         "git+" + str(self.work / "absent.git") + "#refs/x:ns"),
            "a malformed git locator": ("git+" + str(self.work / "bad") + "#not-a-ref",
                                        "git+" + str(self.work / "bad") + "#not-a-ref"),
        }
        before = sorted(p.name for p in self.work.iterdir())
        for name, (org, repo) in locators.items():
            env = {"STEGVERSE_ORG_LEDGER_ROOT": org, "STEGVERSE_REPO_LEDGER_ROOT": repo}
            for action in ("bind", "seal", "verify"):
                code, result = self.cli(action, self.request_doc(), closure, env=env)
                self.assertNotEqual(code, 0, (name, action))
                self.assertEqual(result["disposition"], "FAIL_CLOSED", (name, action))
                for field in SIX_FIELDS:
                    self.assertTrue(result.get(field), (name, action, field))
        after = sorted(p.name for p in self.work.iterdir())
        created = set(after) - set(before) - {n for n in after if n.startswith(("request-", "out-", "ingress-"))}
        self.assertEqual(created, set())  # no ledger directory, repository or ref was created downstream

    def test_deep_cli_input_is_refused_six_field_not_a_traceback(self):
        deep = self.work / "deep.json"
        deep.write_text("[" * 200000 + "]" * 200000)
        for action, request_path, ingress_path in (("bind", deep, None), ("verify", None, deep),
                                                    ("precheck", deep, None)):
            request_file = request_path or self.work / "request-ok.json"
            if request_path is None:
                request_file.write_text(json.dumps(self.request_doc()))
            args = [sys.executable, str(self.MODULE), action, "--request-result", str(request_file),
                    "--out", str(self.work / "out-deep.json")]
            if action != "precheck":
                args += ["--ingress-result", str(ingress_path or self.work / "request-ok.json")]
            completed = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 1, completed.stderr)
            self.assertNotIn("Traceback", completed.stderr)
            self.assert_refused(json.loads(completed.stdout), "BOUND_RECEIPT_UNREADABLE")
        self.assertEqual(self.files(binding.SCHEMA), [])

    def test_row3_git_unavailable_is_a_non_terminal_retry_with_exit_75(self):
        closure = self.closure()
        scrubbed = {"PATH": str(self.work / "no-bin")}
        for action, ingress_doc in (("precheck", None), ("bind", closure), ("seal", closure), ("verify", closure)):
            code, result = self.cli(action, self.request_doc(), ingress_doc, env=scrubbed)
            self.assertEqual(code, 75, (action, result))
            self.assertEqual(result["failure_code"], "GIT_UNAVAILABLE_RETRY")
            self.assertIs(result["terminal"], False)
            for field in SIX_FIELDS:
                self.assertTrue(result.get(field), field)
        out = self.work / "tr.json"
        completed = subprocess.run([sys.executable, str(ROOT / "resident-runtime/transition_request.py"),
                                    "--before", "0" * 40, "--after", "1" * 40, "--out", str(out)],
                                   capture_output=True, text=True, env={**os.environ, **scrubbed})
        self.assertEqual(completed.returncode, 75, completed.stderr)
        result = json.loads(out.read_text())
        self.assertEqual((result["failure_code"], result["terminal"]), ("GIT_UNAVAILABLE_RETRY", False))
        self.assertNotIn("Traceback", completed.stderr)


class WriteFaultsAndTornReads(Ledgers):
    """F-WRITER-TRACE: a write fault is a non-terminal retry that completes exactly once; a torn read is refused."""

    def assert_retryable(self, result):
        self.assertEqual((result["disposition"], result["failure_code"]),
                         ("FAIL_CLOSED", "UNDETERMINED_RETRY_IS_IDEMPOTENT"), result)
        self.assertIs(result["terminal"], False)
        for field in SIX_FIELDS:
            self.assertTrue(result.get(field), field)

    def inject(self, fault, on_call):
        real, calls = binding._put, []

        def faulty(store, key, document):
            calls.append(key)
            if len(calls) == on_call:
                raise fault
            return real(store, key, document)
        binding._put = faulty
        self.addCleanup(setattr, binding, "_put", real)
        return lambda: setattr(binding, "_put", real)

    def assert_bound_once(self, closure):
        self.assertEqual(len(self.files(binding.SCHEMA)), 1)
        self.assertEqual(len(list(binding.chain(binding.stores()["organization"]))), 1)
        self.assertEqual(binding.verify(REQUEST, closure)["disposition"], "ALLOW")

    def test_a_fault_on_the_head_put_retries_to_exactly_one_binding(self):
        closure = self.closure()
        restore = self.inject(OSError("disk full"), on_call=2)
        self.assert_retryable(binding.bind(REQUEST, closure, 33))
        self.assertEqual(len(self.files(binding.SCHEMA)), 1)  # receipt written, HEAD not
        self.assertEqual(list(binding.chain(binding.stores()["organization"])), [])
        restore()
        retried = binding.bind(REQUEST, closure, 33)
        self.assertEqual((retried["disposition"], retried["replayed"]), ("ALLOW", False))
        self.assert_bound_once(closure)
        self.assertIs(binding.bind(REQUEST, closure, 33)["replayed"], True)
        self.assert_bound_once(closure)

    def test_a_fault_on_the_receipt_put_or_a_collision_retries_cleanly(self):
        closure = self.closure()
        for fault in (OSError("io"), RuntimeError("LedgerWriteFault: git update-ref failed"),
                      ValueError("ledger_receipt_collision")):
            restore = self.inject(fault, on_call=1)
            self.assert_retryable(binding.bind(REQUEST, closure, 33))
            self.assertEqual(self.files(binding.SCHEMA), [])
            restore()
        self.assertEqual(binding.bind(REQUEST, closure, 33)["disposition"], "ALLOW")
        self.assert_bound_once(closure)

    def test_a_torn_head_write_leaves_the_previous_head_and_never_a_partial(self):
        first = binding.bind(REQUEST, self.closure("packet-1"), 33)
        other = REQ_E
        second_closure = self.closure("packet-2")
        real_replace, calls = os.replace, []

        def torn(source, target):
            calls.append(target)
            if str(target).endswith("HEAD.json"):
                raise OSError("power lost before rename")
            return real_replace(source, target)
        os.replace = torn
        try:
            self.assert_retryable(binding.bind(other, second_closure, 33))
        finally:
            os.replace = real_replace
        org_root = Path(os.environ["STEGVERSE_ORG_LEDGER_ROOT"])
        self.assertEqual(list(org_root.rglob("*.partial")), [])
        head = json.loads((org_root / binding.HEAD).read_text())
        self.assertEqual(head["receipt_sha256"], first["binding_receipt_sha256"])
        retried = binding.bind(other, second_closure, 33)
        self.assertEqual(retried["disposition"], "ALLOW")
        self.assertEqual(len(list(binding.chain(binding.stores()["organization"]))), 2)
        self.assertEqual(binding.verify(other, second_closure)["disposition"], "ALLOW")

    def test_a_torn_or_partial_read_is_chain_invalid_not_a_traceback(self):
        closure = self.closure()
        bound = binding.bind(REQUEST, closure, 33)
        org_root = Path(os.environ["STEGVERSE_ORG_LEDGER_ROOT"])
        receipt_path = org_root / (binding.PREFIX + bound["binding_receipt_sha256"][7:] + ".json")
        head_path = org_root / binding.HEAD
        good_receipt, good_head = receipt_path.read_bytes(), head_path.read_bytes()
        for path, torn in ((head_path, good_head[:7]), (head_path, b"[]"), (receipt_path, good_receipt[:20])):
            path.write_bytes(torn)
            result = binding.verify(REQUEST, closure)
            self.assert_refused(result, "COMMITTED_REQUEST_BINDING_CHAIN_INVALID")
            self.assert_refused(binding.bind(REQUEST, closure, 33), "COMMITTED_REQUEST_BINDING_CHAIN_INVALID")
            path.write_bytes(good_receipt if path == receipt_path else good_head)
        self.assertEqual(binding.verify(REQUEST, closure)["disposition"], "ALLOW")


class SourceLedgerRows(Ledgers):
    """Rows 8 (designated source ledger) and 9 (corrupt receipt on the walk) on the binding."""

    REF = "refs/stegverse/organization-ledger"

    def git_locators(self, repository, ref=None, namespace=NAMESPACE):
        base = "git+" + str(repository) + "#" + (ref or self.REF)
        return base, base + ":" + namespace

    def test_row8_a_caller_chosen_repository_ledger_is_refused(self):
        designated = self.work / "ledger.git"
        org, repo = self.git_locators(designated)
        os.environ["STEGVERSE_ORG_LEDGER_ROOT"], os.environ["STEGVERSE_REPO_LEDGER_ROOT"] = org, repo
        closure = self.closure()
        attacker = self.work / "attacker.git"
        subprocess.run(["git", "init", "-q", "--bare", str(attacker)], check=True)
        (self.work / "link.git").symlink_to(attacker)
        (self.work / "same.git").symlink_to(designated)
        cases = {
            "attacker ledger": self.git_locators(attacker)[1],
            "symlink to an attacker ledger": self.git_locators(self.work / "link.git")[1],
            "another namespace (another repository's ledger)": self.git_locators(designated,
                                                                                  namespace="repository-ledger/X/y")[1],
            "another ref": self.git_locators(designated, ref="refs/stegverse/other")[1],
            "a directory beside a git organization ledger": str(self.work / "plain"),
        }
        for name, locator in cases.items():
            os.environ["STEGVERSE_REPO_LEDGER_ROOT"] = locator
            for result in (binding.bind(REQUEST, closure, 33), binding.verify(REQUEST, closure)):
                self.assert_refused(result, "SOURCE_LEDGER_NOT_DESIGNATED")
        os.environ["STEGVERSE_REPO_LEDGER_ROOT"] = self.git_locators(self.work / "same.git")[1]
        self.assertEqual(binding.bind(REQUEST, closure, 33)["disposition"], "ALLOW")  # a symlink to the designated repo

    def test_row8_a_receipt_from_another_repositorys_ledger_is_refused(self):
        closure = self.closure()
        (path, receipt), = self.files("stegverse.repo-transition-receipt/v1")
        body = {k: v for k, v in receipt.items() if k != "receipt_sha256"}
        body["repository"] = "Other-Org/.github"
        foreign = {**body, "receipt_sha256": binding.sha(body)}
        repo_store = binding.stores()["repository"]
        binding._put(repo_store, binding.ledger_store.receipt_key(foreign["receipt_sha256"]), foreign)
        result = binding.bind(REQUEST, {**closure, "repository_receipt_sha256": foreign["receipt_sha256"]}, 33)
        self.assert_refused(result, "SOURCE_LEDGER_NOT_DESIGNATED")
        self.assertEqual(self.files(binding.SCHEMA), [])

    def test_a_committed_fake_ledger_root_is_refused_through_the_cli(self):
        closure = self.closure()
        checkout = self.work / "checkout"
        subprocess.run(["git", "init", "-q", str(checkout)], check=True)
        import shutil
        shutil.copytree(os.environ["STEGVERSE_REPO_LEDGER_ROOT"], checkout / "fake-ledger")
        subprocess.run(["git", "-C", str(checkout), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(checkout), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        "commit", "-q", "-m", "fake ledger"], check=True)
        request = self.work / "request.json"
        request.write_text(json.dumps({"request_path": REQUEST["path"], "request_sha256": REQUEST["sha256"],
                                       "hb_epoch": 33}))
        ingress = self.work / "ingress.json"
        ingress.write_text(json.dumps(closure))
        env = {**os.environ, "STEGVERSE_REPO_LEDGER_ROOT": str(checkout / "fake-ledger")}
        for action in ("bind", "verify"):
            out = self.work / (action + ".json")
            completed = subprocess.run([sys.executable, str(ROOT / "resident-runtime/committed_request_binding.py"),
                                        action, "--request-result", str(request), "--ingress-result", str(ingress),
                                        "--out", str(out)], capture_output=True, text=True, env=env)
            self.assertEqual(completed.returncode, 1, completed.stderr)
            self.assert_refused(json.loads(out.read_text()), "LEDGER_ROOT_NOT_CONTRACT_DESIGNATED")
        self.assertEqual(self.files(binding.SCHEMA), [])
        status = subprocess.run(["git", "-C", str(checkout), "status", "--porcelain"], capture_output=True,
                                text=True, check=True).stdout
        self.assertEqual(status, "")  # 0 writes into the committed fake ledger

    def test_row9_a_corrupt_receipt_on_the_walk_is_terminal_and_a_read_fault_is_a_retry(self):
        closure = self.closure()
        repo_root = Path(os.environ["STEGVERSE_REPO_LEDGER_ROOT"])
        head = repo_root / "HEAD.json"
        good = head.read_bytes()
        for corrupt in (b"{truncated", b"[1, 2]", b"\xff\xfe"):
            head.write_bytes(corrupt)
            self.assert_refused(binding.bind(REQUEST, closure, 33), "LEDGER_RECEIPT_CORRUPT")
        head.write_bytes(good)
        real_get = binding.ledger_store.Store.get

        def faulty(store, key):
            if key == "HEAD.json" and str(store.root) == str(repo_root):
                raise PermissionError("injected read fault")
            return real_get(store, key)
        binding.ledger_store.Store.get = faulty
        try:
            result = binding.bind(REQUEST, closure, 33)
        finally:
            binding.ledger_store.Store.get = real_get
        self.assertEqual((result["failure_code"], result["terminal"]), ("LEDGER_READ_FAULT_RETRY", False))
        self.assertEqual(self.files(binding.SCHEMA), [])
        self.assertEqual(binding.bind(REQUEST, closure, 33)["disposition"], "ALLOW")


class InTrIngressUnchanged(unittest.TestCase):
    """INTR_PATH_FILE_BYTE_IDENTICAL_TO_MAIN_MODULO_GRADED_EXCEPTION_FIXES.

    The InTr ingress may differ from main only by exception-fix commits graded
    ALLOW by V-P11 and listed in the PR body (sha, row). SV-011 has none: no
    Claude commit touches this file on main, and this branch's 1cf198b was
    reverted by 716b511 (not a graded fix). The allowlist is empty.
    """

    #: sha256 of resident-runtime/intr_destination_ingress.py at main ecabf04 (InTr code, Grok Lane B).
    MAIN_SHA256 = "390343dc625dfe849f0dbce1bd75ae89bd4d6eccf932b985108e7ce2fe5d3eb7"
    #: (commit sha, V-P11 row, resulting file sha256) for each graded exception fix; empty on SV-011.
    GRADED_EXCEPTION_FIXES: tuple[tuple[str, str, str], ...] = ()

    def test_the_allowlist_is_empty(self):
        self.assertEqual(self.GRADED_EXCEPTION_FIXES, ())

    def test_the_ingress_is_byte_identical_to_main_modulo_graded_fixes(self):
        import hashlib
        data = (ROOT / "resident-runtime/intr_destination_ingress.py").read_bytes()
        allowed = {self.MAIN_SHA256} | {fix[2] for fix in self.GRADED_EXCEPTION_FIXES}
        self.assertIn(hashlib.sha256(data).hexdigest(), allowed)


class OnTheGitLedgerRef(Ledgers):
    REF = "refs/stegverse/organization-ledger"

    def use_directories(self):
        self.repository = self.work / "ledger.git"
        self.location = "git+" + str(self.repository) + "#" + self.REF
        os.environ["STEGVERSE_ORG_LEDGER_ROOT"] = self.location
        os.environ["STEGVERSE_REPO_LEDGER_ROOT"] = self.location + ":" + NAMESPACE

    def git(self, *args):
        return subprocess.run(("git", "--git-dir", str(self.repository)) + args, check=True,
                              capture_output=True, text=True).stdout.strip()

    def tip(self):
        result = subprocess.run(("git", "--git-dir", str(self.repository), "rev-parse", "--verify", "--quiet",
                                 self.REF), capture_output=True, text=True)
        return result.stdout.strip() or None

    def run_writer(self, packet="packet-1", request=REQUEST):
        published = self.tip()
        closure = self.closure(packet)
        self.assertEqual(binding.bind(request, closure, 33)["disposition"], "ALLOW")
        sealed = binding.seal(published, "ledger: committed request")
        self.assertEqual(sealed["disposition"], "ALLOW", sealed)
        return published, closure, sealed

    def test_the_binding_is_one_commit_with_its_receipts_and_the_readback_stays_allow(self):
        published, closure, sealed = self.run_writer()
        self.assertEqual(self.tip(), sealed["sealed_commit"])
        self.assertEqual(self.git("rev-list", "--count", self.REF), "1")
        added = set(self.git("show", "--format=", "--name-only", sealed["sealed_commit"]).splitlines())
        (_, receipt), = [(None, json.loads(self.git("show", self.REF + ":" + name)))
                         for name in added if name.startswith(binding.PREFIX) and not name.endswith("HEAD.json")]
        for level, digest in (("repository", closure["repository_receipt_sha256"]),
                              ("organization", closure["organization_receipt_sha256"])):
            prefix = NAMESPACE + "/" if level == "repository" else ""
            self.assertIn(prefix + "receipts/" + digest[7:] + ".json", added)
        verified = binding.verify(REQUEST, closure)
        self.assertEqual(verified["disposition"], "ALLOW", verified)
        self.assertEqual(verified["ledger_commit"], sealed["sealed_commit"])
        self.assertEqual(verified["bindings_head_sha256"], receipt["receipt_sha256"])
        self.assertEqual(verified["organization_head_sha256"], closure["organization_receipt_sha256"])
        result = readback.readback(self.location, closure, NAMESPACE)
        self.assertEqual(result["disposition"], "ALLOW", result)

    def test_a_second_run_seals_onto_the_published_tip(self):
        _, _, first = self.run_writer("packet-1")
        published, closure, second = self.run_writer("packet-2", REQ_E)
        self.assertEqual(published, first["sealed_commit"])
        self.assertEqual(self.git("rev-parse", second["sealed_commit"] + "^"), first["sealed_commit"])
        self.assertEqual(binding.verify(REQ_E, closure)["disposition"], "ALLOW")

    def test_a_seal_fault_is_retryable_and_the_retry_seals_once(self):
        published = self.tip()
        closure = self.closure()
        binding.bind(REQUEST, closure, 33)
        store_class = binding.ledger_store.GitLedgerStore
        real_git = store_class._git

        def faulty(store, *args, **kwargs):
            if args and args[0] == "update-ref":
                raise RuntimeError("git update-ref failed: injected")
            return real_git(store, *args, **kwargs)
        store_class._git = faulty
        try:
            result = binding.seal(published, "ledger: committed request")
        finally:
            store_class._git = real_git
        self.assertEqual(result["failure_code"], "UNDETERMINED_RETRY_IS_IDEMPOTENT", result)
        self.assertIs(result["terminal"], False)
        self.assertEqual(binding.seal(published, "ledger: committed request")["disposition"], "ALLOW")
        self.assertEqual(self.git("rev-list", "--count", self.REF), "1")
        self.assertEqual(binding.verify(REQUEST, closure)["disposition"], "ALLOW")

    def test_row1_an_identical_replay_is_an_allow_no_op_end_to_end(self):
        module = ROOT / "resident-runtime/committed_request_binding.py"
        request_path, ingress_path = self.work / "request.json", self.work / "ingress.json"
        request_path.write_text(json.dumps({"disposition": "ALLOW", "request_path": REQUEST["path"],
                                            "request_sha256": REQUEST["sha256"], "hb_epoch": 33}))
        tips = []
        for _ in range(2):
            published = self.tip()
            ingress_path.write_text(json.dumps(self.closure()))  # the second closure is the ingress replay
            for action in ("precheck", "bind", "seal", "verify"):
                completed = subprocess.run(
                    [sys.executable, str(module), action, "--published-tip", published or "",
                     "--request-result", str(request_path), "--ingress-result", str(ingress_path),
                     "--out", str(self.work / (action + ".json"))], capture_output=True, text=True)
                self.assertEqual(completed.returncode, 0, (action, completed.stdout, completed.stderr))
            tips.append(self.tip())
            result = readback.readback(self.location, json.loads(ingress_path.read_text()), NAMESPACE)
            self.assertEqual(result["disposition"], "ALLOW")
        self.assertEqual(tips[0], tips[1])  # the replay wrote and sealed nothing
        self.assertEqual(json.loads((self.work / "bind.json").read_text())["replayed"], True)
        self.assertIs(json.loads((self.work / "seal.json").read_text())["nothing_to_seal"], True)
        verified = json.loads((self.work / "verify.json").read_text())
        self.assertEqual((verified["disposition"], verified["bindings_chain_length"]), ("ALLOW", 1))

    def test_an_unsealed_binding_is_not_in_the_same_commit_as_its_receipts(self):
        closure = self.closure()
        binding.bind(REQUEST, closure, 33)
        result = binding.verify(REQUEST, closure)
        self.assert_refused(result, "COMMITTED_REQUEST_BINDING_CHAIN_INVALID")
        self.assertEqual(result["failed_predicate"], "BINDING_IS_COMMITTED_WITH_THE_RECEIPTS_IT_BINDS")


class ThroughTheBoundary(Ledgers):
    """Forgery probes run through the writer's CLI sequence, not the library API (lead 20:29Z (c)):
    precheck -> ingress -> bind -> seal -> verify (local) -> readback + verify (fresh fetch)."""

    REF = OnTheGitLedgerRef.REF
    use_directories = OnTheGitLedgerRef.use_directories
    tip = OnTheGitLedgerRef.tip

    def run_cli(self, script, *args, env=None):
        completed = subprocess.run([sys.executable, str(ROOT / script), *args], capture_output=True, text=True,
                                   env={**os.environ, **(env or {})})
        self.assertNotIn("Traceback", completed.stderr, completed.stderr)
        return completed.returncode, completed

    def results(self, name, document):
        path = self.work / name
        path.write_text(json.dumps(document))
        return path

    def step(self, action, request_path, ingress_path=None, published=""):
        out = self.work / (action + ".json")
        args = [action, "--request-result", str(request_path), "--out", str(out)]
        if ingress_path is not None:
            args += ["--ingress-result", str(ingress_path)]
        if action == "seal":
            args += ["--published-tip", published]
        code, _ = self.run_cli("resident-runtime/committed_request_binding.py", *args)
        return code, json.loads(out.read_text())

    def ingress(self, packet="packet-1"):
        out = self.work / "ingress.json"
        code, completed = self.run_cli(
            "resident-runtime/intr_destination_ingress.py", "--self-closure",
            "--registry", str(ROOT / "org-boundary/registry/services.json"), "--out", str(out),
            env={"STEGVERSE_COMMUNICATION_ID": "comm-1", "STEGVERSE_PACKET_ID": packet, "STEGVERSE_HB_EPOCH": "33"})
        self.assertEqual(code, 0, completed.stdout)
        return out

    def fresh(self):
        fresh = self.work / "fresh.git"
        subprocess.run(["git", "init", "-q", "--bare", str(fresh)], check=True)
        subprocess.run(["git", "--git-dir", str(fresh), "fetch", "-q", str(self.repository),
                        "+" + self.REF + ":" + self.REF], check=True)
        location = "git+" + str(fresh) + "#" + self.REF
        return location, {"STEGVERSE_ORG_LEDGER_ROOT": location,
                          "STEGVERSE_REPO_LEDGER_ROOT": location + ":" + NAMESPACE}

    def request_file(self, pair):
        return self.results("request.json", {"disposition": "ALLOW", "request_path": pair["path"],
                                             "request_sha256": pair["sha256"], "hb_epoch": 33})

    def genuine_run(self):
        request = self.request_file(REQUEST)
        self.assertEqual(self.step("precheck", request)[0], 0)
        ingress = self.ingress()
        for action in ("bind", "seal", "verify"):
            code, result = self.step(action, request, ingress)
            self.assertEqual(code, 0, (action, result))
        return request, ingress

    def test_a_forged_request_digest_stops_at_precheck_and_nothing_reaches_the_ledger(self):
        forged = {**REQUEST, "sha256": "sha256:" + hashlib.sha256(b"forged").hexdigest()}
        code, result = self.step("precheck", self.request_file(forged))
        self.assertEqual(code, 1)
        self.assert_refused(result, "COMMITTED_REQUEST_DIGEST_UNVERIFIED")
        self.assertIsNone(self.tip())  # the writer's step fails here: no append, bind, seal or publish

    def test_a_committed_request_injected_into_the_ingress_result_is_never_bound(self):
        request = self.request_file(REQUEST)
        self.assertEqual(self.step("precheck", request)[0], 0)
        ingress = json.loads(self.ingress().read_text())
        tip = self.tip()
        injected = self.results("ingress-injected.json", {**ingress, "committed_request": REQ_D})
        code, result = self.step("bind", request, injected)
        self.assertEqual(code, 1)
        self.assert_refused(result, "FORGED_COMMITTED_REQUEST_FROM_EVIDENCE_JSON")
        self.assertEqual(self.tip(), tip)  # nothing bound
        self.assertEqual(self.step("verify", request, injected)[0], 1)

    def test_a_forged_binding_on_the_ledger_fails_the_fresh_fetch_verify(self):
        request, ingress = self.genuine_run()
        # plant a rehashed binding for a forged request on top of the genuine one, as if pushed by someone else
        org = binding.stores()["organization"]
        (genuine_digest, genuine), = list(binding.chain(org))
        forged_pair = {**REQUEST, "sha256": "sha256:" + hashlib.sha256(b"forged").hexdigest()}
        body = {k: v for k, v in genuine.items() if k != "receipt_sha256"}
        body.update(committed_request={**forged_pair, "hb_epoch_source": "COMMITTED_REQUEST"},
                    previous_binding_sha256=genuine_digest)
        forged = {**body, "receipt_sha256": binding.sha(body)}
        binding._put(org, binding.PREFIX + forged["receipt_sha256"][7:] + ".json", forged)
        binding._put(org, binding.HEAD, {"schema": binding.HEAD_SCHEMA, "receipt_sha256": forged["receipt_sha256"]})
        location, fresh_env = self.fresh()
        for pair, code_expected in ((forged_pair, "COMMITTED_REQUEST_DIGEST_UNVERIFIED"),
                                    (REQUEST, "COMMITTED_REQUEST_BINDING_ABSENT")):
            out = self.work / "fresh-verify.json"
            code, _ = self.run_cli("resident-runtime/committed_request_binding.py", "verify",
                                   "--request-result", str(self.request_file(pair)),
                                   "--ingress-result", str(ingress), "--out", str(out), env=fresh_env)
            self.assertEqual(code, 1)
            self.assert_refused(json.loads(out.read_text()), code_expected)

    def test_a_forged_receipt_carrying_another_request_on_the_ledger_is_refused_through_the_boundary(self):
        request, ingress = self.genuine_run()
        repo = binding.stores()["repository"]
        closure = json.loads(ingress.read_text())
        genuine = repo.get(binding.ledger_store.receipt_key(closure["repository_receipt_sha256"]))
        body = {k: v for k, v in genuine.items() if k != "receipt_sha256"}
        body["evidence"] = {**body["evidence"], "committed_request": {**REQ_D, "hb_epoch_source": "COMMITTED_REQUEST"}}
        body["previous_receipt_sha256"] = genuine["receipt_sha256"]
        forged = {**body, "receipt_sha256": binding.sha(body)}
        repo.put(binding.ledger_store.receipt_key(forged["receipt_sha256"]), forged)
        repo.put(binding.ORG_HEAD, {**repo.get(binding.ORG_HEAD), "receipt_sha256": forged["receipt_sha256"]})
        forged_ingress = self.results("ingress-forged.json",
                                      {**closure, "repository_receipt_sha256": forged["receipt_sha256"]})
        for action in ("bind", "verify"):
            code, result = self.step(action, request, forged_ingress)
            self.assertEqual(code, 1, action)
            self.assertIn(result["failure_code"], ("COMMITTED_REQUEST_DIGEST_UNVERIFIED",
                                                   "COMMITTED_REQUEST_BINDING_ABSENT"), result)
            if action == "bind":
                self.assertEqual(result["failure_code"], "COMMITTED_REQUEST_DIGEST_UNVERIFIED")

    def test_the_genuine_sequence_passes_the_fresh_fetch_readback_and_verify(self):
        request, ingress = self.genuine_run()
        location, fresh_env = self.fresh()
        readback_out = self.work / "readback.json"
        code, completed = self.run_cli("resident-runtime/organization_ledger_readback.py", "--location", location,
                                       "--repository-namespace", NAMESPACE, "--ingress-result", str(ingress),
                                       "--out", str(readback_out))
        self.assertEqual(code, 0, completed.stdout)
        out = self.work / "fresh-verify.json"
        code, _ = self.run_cli("resident-runtime/committed_request_binding.py", "verify",
                               "--request-result", str(request), "--ingress-result", str(ingress),
                               "--out", str(out), env=fresh_env)
        self.assertEqual((code, json.loads(out.read_text())["disposition"]), (0, "ALLOW"))


if __name__ == "__main__":
    unittest.main()
