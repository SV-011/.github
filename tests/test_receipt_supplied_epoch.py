"""A receipt writer references the heartbeat epoch its caller supplies.

Neither ledger writer derives an epoch from a clock. An absent or invalid
``hb_epoch`` is FAIL_CLOSED with six fields before the store is opened, and
nothing is written. A frame consumed at the capability address hands its
carrier epoch to both writers. Both ledger roots and the mesh are redirected.
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
INVALID = ("33.0", True, 31, "+33", 32.0, " 33", "3_3")


def _module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


kernel = _module("kernel", "org-kernel/kernel.py")
emit = _module("repo_transition_emit", ".stegverse/transition-ledger/emit.py")
aggregate = _module("aggregate_repo_transition", "resident-runtime/aggregate_repo_transition.py")
ingress = _module("intr_destination_ingress", "resident-runtime/intr_destination_ingress.py")
egress = _module("organization_egress_boundary", "resident-runtime/organization_egress_boundary.py")

REGISTRY = json.loads((ROOT / "org-boundary/registry/services.json").read_text(encoding="utf-8"))
GENESIS = {"mode": "ESTABLISH_GENESIS", "node_ref": "supplied-epoch-test", "predecessor": None}
ADDRESS = "sv-011.intr-destination"
SOURCE = {"receipt_sha256": "sha256:" + "0" * 64}


def diagnostic(identifier):
    return {"message_class": "ecosystem.communication", "communication_id": identifier,
            "subject": "purpose-neutral mesh diagnostic",
            "body": {"schema": "stegverse.org-intr-destination-diagnostic/v1",
                     "probe": "diagnostic-self-closure"}}


class _ClockRead(AssertionError):
    pass


class _NoClock:
    """Stands in for ``datetime`` in a kernel copy: reading the clock fails the test."""

    @staticmethod
    def now(*_args, **_kwargs):
        raise _ClockRead("a receipt writer read the clock")


class _NoStore:
    """Stands in for ``ledger_store.open_store``: opening a store fails the test."""

    def __init__(self):
        self.opened = 0

    def __call__(self, *_args, **_kwargs):
        self.opened += 1
        raise AssertionError("the store was opened before the epoch was checked")


class Redirected(unittest.TestCase):
    ROOTS = ("STEGVERSE_REPO_LEDGER_ROOT", "STEGVERSE_ORG_LEDGER_ROOT")

    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        previous = {name: os.environ.get(name) for name in self.ROOTS}
        for name in self.ROOTS:
            os.environ[name] = str(Path(work.name) / name.lower())
        self.mesh = Path(work.name) / "mesh"

        def restore():
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        self.addCleanup(restore)

    def no_clock(self, *modules):
        for module in modules:
            saved = module.kernel.datetime
            module.kernel.datetime = _NoClock
            self.addCleanup(setattr, module.kernel, "datetime", saved)

    def no_store(self, module):
        guard, saved = _NoStore(), module.ledger_store.open_store
        module.ledger_store.open_store = guard
        self.addCleanup(setattr, module.ledger_store, "open_store", saved)
        return guard

    def written(self):
        return [path for name in self.ROOTS for path in Path(os.environ[name]).rglob("*.json")]

    def assertSixFieldRefusal(self, refused, code, entrypoint):
        refusal = refused.exception.refusal
        self.assertEqual(refusal["disposition"], "FAIL_CLOSED")
        for field in SIX_FIELDS:
            self.assertTrue(refusal.get(field), field)
        self.assertEqual(refusal["failure_code"], code)
        self.assertEqual(refusal["failed_predicate"], "SUBMISSION_CARRIES_THE_SUPPLIED_HB_EPOCH")
        self.assertIn(entrypoint, refusal["retry_entrypoint"])
        self.assertIs(refusal["derived_from_clock"], False)
        self.assertIs(refusal["consequence_committed"], False)


class WriterRefusals(Redirected):
    def test_the_repository_writer_refuses_an_absent_epoch_before_the_store_opens(self):
        self.no_clock(emit)
        guard = self.no_store(emit)
        with self.assertRaises(ValueError) as refused:
            emit.append("t:absent", "T", "sha256:a", "sha256:b", {}, "NONE")
        self.assertSixFieldRefusal(refused, "HB_EPOCH_NOT_SUPPLIED", "emit.py::append")
        self.assertEqual(guard.opened, 0)

    def test_the_repository_writer_refuses_an_invalid_epoch(self):
        self.no_clock(emit)
        guard = self.no_store(emit)
        for epoch in INVALID:
            with self.subTest(epoch=epoch), self.assertRaises(ValueError) as refused:
                emit.append("t:invalid", "T", "sha256:a", "sha256:b", {}, "NONE", hb_epoch=epoch)
            self.assertSixFieldRefusal(refused, "HB_EPOCH_INVALID", "emit.py::append")
        self.assertEqual(guard.opened, 0)

    def test_the_organization_writer_refuses_an_invalid_epoch(self):
        self.no_clock(aggregate)
        guard = self.no_store(aggregate)
        for epoch in INVALID:
            with self.subTest(epoch=epoch), self.assertRaises(ValueError) as refused:
                aggregate.append(SOURCE, "REPO_STATE_PROPAGATION", "sha256:a", "sha256:b", {}, "NONE",
                                 hb_epoch=epoch)
            self.assertSixFieldRefusal(refused, "HB_EPOCH_INVALID", "aggregate_repo_transition.py::append")
        self.assertEqual(guard.opened, 0)

    def test_a_supplied_epoch_records_no_carried_provenance(self):
        receipt = emit.append("t:no-provenance", "T", "sha256:a", "sha256:b", {}, "NONE", hb_epoch=40)
        self.assertNotIn("source", receipt["hb_reference"])
        self.assertNotIn("head_receipt_sha", receipt["hb_reference"])

    def test_carried_provenance_cannot_rewrite_the_epoch(self):
        guard = self.no_store(emit)
        for provenance in ({"source": "ORGANIZATION_LEDGER_HEAD"},
                           {"source": "X", "head_receipt_sha": "sha256:1", "epoch": 99},
                           {"source": "X", "head_receipt_sha": "sha256:1", "derived_from_clock": False},
                           {"source": "", "head_receipt_sha": "sha256:1"}):
            with self.subTest(provenance=provenance), self.assertRaises(ValueError):
                emit.append("t:prov", "T", "sha256:a", "sha256:b", {}, "NONE", hb_epoch=40,
                            hb_provenance=provenance)
        self.assertEqual(guard.opened, 0)

    def test_ascii_digits_are_a_supplied_epoch(self):
        self.no_clock(emit)
        receipt = emit.append("t:digits", "T", "sha256:a", "sha256:b", {}, "NONE", hb_epoch="33")
        self.assertEqual(receipt["hb_reference"]["epoch"], 33)
        self.assertIs(receipt["hb_reference"]["derived_from_clock"], False)

    def test_the_organization_writer_refuses_an_absent_epoch_before_the_store_opens(self):
        self.no_clock(aggregate)
        guard = self.no_store(aggregate)
        with self.assertRaises(ValueError) as refused:
            aggregate.append(SOURCE, "REPO_STATE_PROPAGATION", "sha256:a", "sha256:b", {}, "NONE")
        self.assertSixFieldRefusal(refused, "HB_EPOCH_NOT_SUPPLIED", "aggregate_repo_transition.py::append")
        self.assertEqual(guard.opened, 0)

    def test_a_supplied_epoch_is_referenced_and_no_clock_is_read(self):
        self.no_clock(emit, aggregate)
        repository = emit.append("t:supplied", "T", "sha256:a", "sha256:b", {}, "NONE", hb_epoch=40)
        organization = aggregate.append(repository, "REPO_STATE_PROPAGATION",
                                        "sha256:a", "sha256:b", {}, "NONE", hb_epoch=40)
        for receipt in (repository, organization):
            self.assertEqual(receipt["hb_reference"]["epoch"], 40)
            self.assertIs(receipt["hb_reference"]["derived_from_clock"], False)

    def test_the_ingress_without_an_epoch_writes_nothing(self):
        self.no_clock(ingress.repository_ledger, ingress.organization_ledger)
        with self.assertRaises(ValueError) as refused:
            ingress.receive(diagnostic("no-epoch"), registry=REGISTRY, packet_id="no-epoch")
        self.assertSixFieldRefusal(refused, "HB_EPOCH_NOT_SUPPLIED", "emit.py::append")
        self.assertEqual(self.written(), [])

    def test_the_ingress_with_an_invalid_epoch_writes_nothing(self):
        self.no_clock(ingress.repository_ledger, ingress.organization_ledger)
        for epoch in INVALID:
            with self.subTest(epoch=epoch), self.assertRaises(ValueError) as refused:
                ingress.receive(diagnostic("bad-epoch"), registry=REGISTRY, packet_id="bad-epoch",
                                hb_epoch=epoch)
            self.assertSixFieldRefusal(refused, "HB_EPOCH_INVALID", "emit.py::append")
        self.assertEqual(self.written(), [])


class CapabilityAddressCarriesTheEpoch(Redirected):
    def publish(self, packet_id, **stamp):
        packet = kernel.build_packet(
            origin_org="StegVerse-org", origin_service="stegverse-org.intr-destination",
            destination_org="SV-011", destination_service=ADDRESS,
            payload=diagnostic(packet_id), standing=GENESIS,
            transition_reference="intr:transition:" + packet_id,
            authority_effect="NONE", packet_id=packet_id)
        kernel.publish_packet(packet, root=self.mesh, **stamp)

    def consume(self):
        return kernel.consume_addressed_frames(
            ROOT, mesh_root=self.mesh,
            repo_ledger_root=os.environ["STEGVERSE_REPO_LEDGER_ROOT"],
            org_ledger_root=os.environ["STEGVERSE_ORG_LEDGER_ROOT"])

    def test_a_clock_derived_carrier_epoch_is_refused_and_nothing_is_written(self):
        self.no_clock(ingress.repository_ledger, ingress.organization_ledger)
        # The sender stamped the frame from a clock sample: derived_from_clock is true.
        self.publish("clock-stamped", now_ns=kernel.HB_ANCHOR_UNIX_NS + 5 * kernel.HB_PERIOD_NS)
        result, = self.consume()
        marker = result["result"]
        self.assertEqual(marker["status"], "REFUSED")
        self.assertEqual(marker["failure_code"], "FRAME_REFUSED_QUARANTINED")
        # Narrow S1: a clock-flagged frame is consumed but lends no epoch, so the writer refuses NOT_SUPPLIED
        self.assertEqual(marker["refusal_failure_code"], "HB_EPOCH_NOT_SUPPLIED")
        self.assertIn("emit.py::append", marker["retry_entrypoint"])
        for field in SIX_FIELDS:
            self.assertTrue(marker.get(field), field)
        self.assertEqual(self.written(), [])

    def test_a_consumed_frame_hands_its_carrier_epoch_to_both_writers(self):
        self.no_clock(ingress.repository_ledger, ingress.organization_ledger)
        self.publish("carried-epoch", epoch=37)
        result, = self.consume()
        operation = result["result"]["execution_result"]["application_result"]["receiving_operation_result"]
        self.assertEqual(operation["disposition"], "ALLOW", operation.get("detail"))
        receipts = [json.loads(path.read_text(encoding="utf-8")) for path in self.written()]
        referenced = [item["hb_reference"] for item in receipts if "hb_reference" in item]
        self.assertEqual(len(referenced), 2)
        for reference in referenced:
            self.assertEqual(reference["epoch"], 37)
            self.assertIs(reference["derived_from_clock"], False)


class EgressAndIngestRefuseFirst(Redirected):
    """An epoch-taking path refuses an absent or invalid epoch before it builds or writes."""

    REFUSED = ((None, "HB_EPOCH_NOT_SUPPLIED"),) + tuple((epoch, "HB_EPOCH_INVALID") for epoch in INVALID)

    def frames(self):
        directory = self.mesh / "frames.d"
        return sorted(directory.glob("*.json")) if directory.is_dir() else []

    def emit(self, epoch, identifier="egress-epoch"):
        return egress.emit("StegVerse-org", diagnostic(identifier), standing=GENESIS,
                           capability="intr-destination", mesh_root=self.mesh, hb_epoch=epoch)

    def test_egress_refuses_before_it_publishes_or_records(self):
        self.no_clock(egress, egress.repository_ledger, egress.organization_ledger)
        for epoch, code in self.REFUSED:
            with self.subTest(epoch=epoch), self.assertRaises(ValueError) as refused:
                self.emit(epoch)
            self.assertSixFieldRefusal(refused, code, "organization_egress_boundary.py::emit")
        self.assertEqual(self.frames(), [])
        self.assertEqual(self.written(), [])

    def test_egress_publishes_one_frame_per_packet_id(self):
        """V-P11 F-REPUB (egress): a retry with a different epoch is a six-field EMIT_PACKET_ID_COLLISION before
        anything is published or written; a byte-identical retry is a no-op. Exactly one frame either way."""
        self.no_clock(egress, egress.repository_ledger, egress.organization_ledger)
        first = self.emit(4096, "egress-once")
        self.assertEqual(first["disposition"], "ALLOW", first.get("detail"))
        frames, written = self.frames(), sorted(str(p) for p in self.written())
        self.assertEqual(len(frames), 1)
        replay = self.emit(4096, "egress-once")
        self.assertEqual(replay["disposition"], "ALLOW", replay.get("detail"))
        self.assertEqual(replay["frame_sha256"], first["frame_sha256"])
        self.assertEqual(self.frames(), frames)
        self.assertEqual(sorted(str(p) for p in self.written()), written)
        for epoch in (4097, "4100"):
            with self.subTest(epoch=epoch):
                refused = self.emit(epoch, "egress-once")
                self.assertEqual(refused["disposition"], "FAIL_CLOSED")
                self.assertEqual(refused["failure_code"], "EMIT_PACKET_ID_COLLISION")
                self.assertEqual(refused["failed_predicate"], "ONE_PACKET_ID_PUBLISHES_ONE_FRAME")
                self.assertIs(refused["emitted"], False)
                self.assertIs(refused["terminal"], True)
                for field in SIX_FIELDS:
                    self.assertTrue(refused.get(field), field)
                self.assertEqual(self.frames(), frames)  # still exactly one frame
                self.assertEqual(sorted(str(p) for p in self.written()), written)
        # the frame alone (its receipt not yet written) also binds the packet id
        for path in self.written():
            path.unlink()
        refused = self.emit(4097, "egress-once")
        self.assertEqual(refused["failure_code"], "EMIT_PACKET_ID_COLLISION")
        self.assertEqual(self.frames(), frames)
        # and the emission receipt alone (its frame gone from the mesh) binds it too
        self.assertEqual(self.emit(4096, "egress-receipt")["disposition"], "ALLOW")
        for path in set(self.frames()) - set(frames):
            path.unlink()
        refused = self.emit(4097, "egress-receipt")
        self.assertEqual(refused["failure_code"], "EMIT_PACKET_ID_COLLISION")
        self.assertEqual(self.frames(), frames)

    def test_a_torn_write_in_egress_emit_is_six_fields_and_resumes_once(self):
        """V-P11 F-WRITER-TRACE: tearing the frame publish or any of the 4 ledger puts in emit() is a six-field
        EMIT_WRITE_FAILED, terminal false, not a traceback; the same emission retried completes once."""
        self.no_clock(egress, egress.repository_ledger, egress.organization_ledger)
        roots = [Path(os.environ[name]).resolve() for name in self.ROOTS]
        frames_dir = (self.mesh / "frames.d")
        real_bytes, real_text = Path.write_bytes, Path.write_text
        for step in range(5):  # 0: the frame publish; 1-4: repo receipt, repo HEAD, org receipt, org HEAD
            with self.subTest(step=step):
                identifier = "egress-torn-%d" % step
                writes = []

                def tear(path, data, _real, *args, **kwargs):
                    target = Path(path).resolve()
                    ledger = any(root in target.parents for root in roots)
                    frame = target.parent == frames_dir.resolve()
                    if (step == 0 and frame) or (step and ledger and len(writes) + 1 == step):
                        _real(path, data[: len(data) // 2], *args, **kwargs)
                        raise OSError(28, "No space left on device")
                    if ledger:
                        writes.append(path)
                    return _real(path, data, *args, **kwargs)
                Path.write_bytes = lambda path, data, _r=real_bytes: tear(path, data, _r)
                Path.write_text = lambda path, data, *a, _r=real_text, **k: tear(path, data, _r, *a, **k)
                try:
                    failed = self.emit(4096, identifier)
                finally:
                    Path.write_bytes, Path.write_text = real_bytes, real_text
                self.assertEqual(failed["failure_code"], "EMIT_WRITE_FAILED")
                self.assertEqual(failed["disposition"], "FAIL_CLOSED")
                self.assertIs(failed["terminal"], False)
                self.assertIs(failed["emitted"], False)
                for field in SIX_FIELDS:
                    self.assertTrue(failed.get(field), field)
                again = self.emit(4096, identifier)
                self.assertEqual(again["disposition"], "ALLOW", again.get("detail"))
                ours = [f for f in self.frames()
                        if json.loads(f.read_text(encoding="utf-8"))["packet_id"] == again["packet_id"]]
                self.assertEqual(len(ours), 1)
                receipts = [json.loads(p.read_text(encoding="utf-8")) for p in self.written()]
                emitted = [r for r in receipts if r.get("transition_id") == "ORGANIZATION_EGRESS_EMITTED:"
                           + again["packet_id"]]
                self.assertEqual(len(emitted), 1)
                bound = [r for r in receipts if r.get("repo_receipt_sha256") == emitted[0]["receipt_sha256"]]
                self.assertEqual(len(bound), 1)

    def test_a_torn_ledger_put_in_the_ingress_cli_is_six_fields_and_resumes_once(self):
        """V-P11 F-WRITER-TRACE: the workflow CLI (--self-closure --hb-epoch) tearing any of the 4 ledger puts
        prints a six-field INTR_INGRESS_WRITE_FAILED and exits non-zero without a traceback; re-run completes once."""
        driver = (
            "import os, pathlib, runpy, sys\n"
            "roots = [pathlib.Path(os.environ[n]).resolve() for n in ('STEGVERSE_REPO_LEDGER_ROOT',"
            " 'STEGVERSE_ORG_LEDGER_ROOT')]\n"
            "step = int(os.environ['TEAR_AT']); seen = []; real = pathlib.Path.write_bytes\n"
            "def tear(path, data):\n"
            "    if any(r in pathlib.Path(path).resolve().parents for r in roots):\n"
            "        seen.append(path)\n"
            "        if len(seen) == step:\n"
            "            real(path, data[: len(data) // 2]); raise OSError(28, 'No space left on device')\n"
            "    return real(path, data)\n"
            "pathlib.Path.write_bytes = tear\n"
            "sys.argv = sys.argv[1:]\n"
            "runpy.run_path(sys.argv[0], run_name='__main__')\n")
        for step in (1, 2, 3, 4):
            with self.subTest(step=step):
                identifier = "cli-torn-%d" % step
                command = [str(ROOT / "resident-runtime/intr_destination_ingress.py"), "--self-closure",
                           "--communication-id", identifier, "--packet-id", identifier,
                           "--registry", str(ROOT / "org-boundary/registry/services.json"), "--hb-epoch", "4096"]
                environment = {k: v for k, v in os.environ.items() if k != "STEGVERSE_HB_EPOCH"}
                torn = subprocess.run([sys.executable, "-c", driver, *command], capture_output=True, text=True,
                                      env={**environment, "TEAR_AT": str(step)}, cwd=ROOT)
                self.assertEqual(torn.returncode, 3, torn.stderr)
                self.assertNotIn("Traceback", torn.stderr)
                refusal = json.loads(torn.stdout.strip().splitlines()[-1])
                self.assertEqual(refusal["failure_code"], "INTR_INGRESS_WRITE_FAILED")
                self.assertEqual(refusal["failed_predicate"], "DISPATCH_WRITES_THIS_REPOSITORYS_STATE:OSError")
                self.assertIs(refusal["terminal"], False)
                for field in SIX_FIELDS:
                    self.assertTrue(refusal.get(field), field)
                rerun = subprocess.run([sys.executable, *command], capture_output=True, text=True,
                                       env=environment, cwd=ROOT)
                self.assertEqual(rerun.returncode, 0, rerun.stdout + rerun.stderr)
                receipts = [json.loads(p.read_text(encoding="utf-8")) for p in self.written()]
                ours = [r for r in receipts if str(r.get("transition_id", "")).endswith(":" + identifier)]
                self.assertEqual(len(ours), 1)
                bound = [r for r in receipts if r.get("repo_receipt_sha256") == ours[0]["receipt_sha256"]]
                self.assertEqual(len(bound), 1)

    def test_egress_takes_a_supplied_digit_string_epoch(self):
        self.no_clock(egress, egress.repository_ledger, egress.organization_ledger)
        emitted = self.emit("4096", "egress-4096")
        self.assertEqual(emitted["disposition"], "ALLOW", emitted.get("detail"))
        frame, = (json.loads(path.read_text(encoding="utf-8")) for path in self.frames())
        self.assertEqual(frame["heartbeat_reference"]["epoch"], 4096)
        self.assertIs(frame["heartbeat_reference"]["derived_from_clock"], False)
        receipts = [json.loads(path.read_text(encoding="utf-8")) for path in self.written()]
        referenced = [item["hb_reference"] for item in receipts if "hb_reference" in item]
        self.assertEqual(len(referenced), 2)
        for reference in referenced:
            self.assertEqual(reference["epoch"], 4096)
            self.assertIs(reference["derived_from_clock"], False)

    def test_ingest_refuses_an_absent_or_invalid_carrier_epoch_with_six_fields(self):
        self.no_clock(ingress.repository_ledger, ingress.organization_ledger)
        packet = kernel.build_packet(
            origin_org="StegVerse-org", origin_service="stegverse-org.intr-destination",
            destination_org="SV-011", destination_service=ADDRESS,
            payload=diagnostic("ingest-epoch"), standing=GENESIS,
            transition_reference="intr:transition:ingest-epoch",
            authority_effect="NONE", packet_id="ingest-epoch")
        stamped = kernel.carrier_frame(packet, epoch=37)
        for epoch, code in self.REFUSED:
            frame = json.loads(json.dumps(stamped))
            if epoch is None:
                del frame["heartbeat_reference"]["epoch"]
            else:
                frame["heartbeat_reference"]["epoch"] = epoch
            body = {key: value for key, value in frame.items() if key != "frame_sha256"}
            frame["frame_sha256"] = kernel.sha(body)
            with self.subTest(epoch=epoch), self.assertRaises(ValueError) as refused:
                kernel.ingest_frame(ROOT, frame, mesh_root=self.mesh)
            self.assertSixFieldRefusal(refused, code, "kernel.py::ingest_frame")
        self.assertEqual(self.written(), [])

    def test_publish_packet_checks_a_supplied_epoch_before_it_publishes(self):
        packet = kernel.build_packet(
            origin_org="SV-011", origin_service="sv-011.intr-destination",
            destination_org="StegVerse-org", destination_service="stegverse-org.intr-destination",
            payload=diagnostic("publish-epoch"), standing=GENESIS,
            transition_reference="intr:transition:publish-epoch",
            authority_effect="NONE", packet_id="publish-epoch")
        for epoch in ("33.0", True, 31, "+33", " 33", "\uff13\uff13", "abc", 32.0, "3_3"):
            with self.subTest(epoch=epoch), self.assertRaises(ValueError) as refused:
                kernel.publish_packet(packet, root=self.mesh, epoch=epoch)
            self.assertSixFieldRefusal(refused, "HB_EPOCH_INVALID", "kernel.py::publish_packet")
        self.assertEqual(self.frames(), [])
        published = kernel.publish_packet(packet, root=self.mesh, epoch="4096")
        self.assertEqual(published["frame"]["heartbeat_reference"]["epoch"], 4096)
        self.assertIs(published["frame"]["heartbeat_reference"]["derived_from_clock"], False)
        self.assertEqual(len(self.frames()), 1)

    def digit_string_frame(self, text):
        packet = kernel.build_packet(
            origin_org="StegVerse-org", origin_service="stegverse-org.intr-destination",
            destination_org="SV-011", destination_service=ADDRESS,
            payload=diagnostic("string-epoch-" + text), standing=GENESIS,
            transition_reference="intr:transition:string-epoch",
            authority_effect="NONE", packet_id="string-epoch-" + text)
        frame = kernel.carrier_frame(packet, epoch=4096)
        frame["heartbeat_reference"]["epoch"] = text
        frame["heartbeat_reference"]["generation"] = text
        body = {key: value for key, value in frame.items() if key != "frame_sha256"}
        frame["frame_sha256"] = kernel.sha(body)
        return frame

    def test_ingest_refuses_a_carrier_reference_without_a_clock_marker(self):
        self.no_clock(ingress.repository_ledger, ingress.organization_ledger)
        frame = self.digit_string_frame("4096")
        del frame["heartbeat_reference"]["derived_from_clock"]
        body = {key: value for key, value in frame.items() if key != "frame_sha256"}
        frame["frame_sha256"] = kernel.sha(body)
        with self.assertRaises(ValueError) as refused:
            kernel.ingest_frame(ROOT, frame, mesh_root=self.mesh)
        self.assertSixFieldRefusal(refused, "HB_EPOCH_PROVENANCE_UNMARKED", "kernel.py::ingest_frame")
        self.assertEqual(self.written(), [])

    def test_the_shared_rule_is_the_pinned_final_text(self):
        import hashlib
        import inspect
        self.assertEqual(hashlib.sha256(inspect.getsource(kernel.supplied_epoch_failure).encode()).hexdigest(),
                         kernel.SHARED_VALIDATOR_SHA256)
        self.assertEqual(kernel.SHARED_VALIDATOR_SHA256,
                         "ad8ff52936d5bcd9a2ad6caceb2d9a637a099518989b34bbb3370301c7a4cce2")

    def test_the_final_rule_marker_cases(self):
        rule = kernel.supplied_epoch_failure
        self.assertEqual(rule(4096, {"epoch": 4096, "sampled_unix_ns": 1})[0], "HB_EPOCH_INVALID")
        self.assertEqual(rule(4096, {"epoch": 4096, "source": "ORGANIZATION_LEDGER_HEAD",
                                     "head_receipt_sha": "sha256:" + "f" * 64})[0],
                         "HB_EPOCH_PROVENANCE_UNMARKED")
        self.assertEqual(rule(4096, {"epoch": 4096, "derived_from_clock": False, "sampled_unix_ns": 1})[0],
                         "HB_EPOCH_INVALID")
        self.assertIsNone(rule(4096, {"epoch": 4096, "derived_from_clock": False, "source": "X"}))

    def test_the_shared_rule_refuses_an_unmarked_reference(self):
        self.assertEqual(kernel.supplied_epoch_failure(40, {"epoch": 40})[0], "HB_EPOCH_PROVENANCE_UNMARKED")
        self.assertIsNone(kernel.supplied_epoch_failure(40, {"epoch": 40, "derived_from_clock": False}))
        # a carrier cannot self-assert CARRIED: source + sha without the flag is still unmarked
        self.assertEqual(kernel.supplied_epoch_failure(
            40, {"epoch": 40, "source": "ORGANIZATION_LEDGER_HEAD", "head_receipt_sha": "sha256:1"})[0],
            "HB_EPOCH_PROVENANCE_UNMARKED")
        self.assertEqual(kernel.supplied_epoch_failure(
            40, {"epoch": 40, "sampled_unix_ns": 1, "derived_from_clock": False})[0], "HB_EPOCH_INVALID")
        self.assertIsNone(kernel.supplied_epoch_failure(
            40, {"epoch": 40, "derived_from_clock": False, "source": "ORGANIZATION_LEDGER_HEAD",
                 "head_receipt_sha": "sha256:1"}))
        self.assertEqual(kernel.supplied_epoch_failure(40, {"epoch": 40, "derived_from_clock": True})[0],
                         "HB_EPOCH_INVALID")

    def fabricated_carried_frame(self, **marker):
        frame = self.digit_string_frame("4096")
        reference = frame["heartbeat_reference"]
        reference.pop("derived_from_clock")
        reference.update({"source": "ORGANIZATION_LEDGER_HEAD", "head_receipt_sha": "sha256:" + "f" * 64}, **marker)
        body = {key: value for key, value in frame.items() if key != "frame_sha256"}
        frame["frame_sha256"] = kernel.sha(body)
        return frame

    def assertCarriedNotAccepted(self, refused):
        refusal = refused.exception.refusal
        self.assertEqual(refusal["disposition"], "FAIL_CLOSED")
        for field in SIX_FIELDS:
            self.assertTrue(refusal.get(field), field)
        self.assertEqual(refusal["failure_code"], "CARRIED_NOT_ACCEPTED_BY_DESTINATION")
        self.assertEqual(refusal["failed_predicate"], "DESTINATION_ACCEPTS_CARRIED_CROSSINGS")
        self.assertEqual(refusal["required_evidence_or_repair"], "re-send with the SUPPLIED epoch")
        self.assertIn("the sender's hop", refusal["retry_entrypoint"])
        self.assertNotIn("CARRIED_PROVENANCE_UNVERIFIED", json.dumps(refusal))  # not judged forged
        self.assertIs(refusal["derived_from_clock"], False)
        self.assertIs(refusal["consequence_committed"], False)

    def assertCarriedUnverified(self, refused):
        refusal = refused.exception.refusal
        self.assertEqual(refusal["disposition"], "FAIL_CLOSED")
        for field in SIX_FIELDS:
            self.assertTrue(refusal.get(field), field)
        self.assertEqual(refusal["failure_code"], "CARRIED_PROVENANCE_UNVERIFIED")
        self.assertEqual(refusal["failed_predicate"], "CARRIED_EPOCH_IS_THE_VERIFIED_ORGANIZATION_LEDGER_HEAD_EPOCH")
        self.assertIs(refusal["derived_from_clock"], False)

    def test_a_carrier_cannot_self_assert_carried_provenance(self):
        self.no_clock(ingress.repository_ledger, ingress.organization_ledger)
        for marker, code in (({}, "HB_EPOCH_PROVENANCE_UNMARKED"),
                             # bare-sampled is CLOCK (08:10Z); its forged CARRIED claim is still unverified
                             ({"sampled_unix_ns": kernel.HB_ANCHOR_UNIX_NS}, "CARRIED_PROVENANCE_UNVERIFIED"),
                             ({"sampled_unix_ns": kernel.HB_ANCHOR_UNIX_NS, "derived_from_clock": False},
                              "HB_EPOCH_INVALID"),
                             ({"derived_from_clock": False}, "CARRIED_PROVENANCE_UNVERIFIED")):
            with self.subTest(marker=marker), self.assertRaises(ValueError) as refused:
                kernel.ingest_frame(ROOT, self.fabricated_carried_frame(**marker), mesh_root=self.mesh)
            if code == "CARRIED_PROVENANCE_UNVERIFIED":
                self.assertCarriedUnverified(refused)
            else:
                self.assertSixFieldRefusal(refused, code, "kernel.py::ingest_frame")
        self.assertEqual(self.written(), [])

    def test_a_real_carried_frame_is_not_accepted_and_is_not_called_forged(self):
        """09:03Z option (1): the manifest declares accepts_carried_crossings false for inbound frames. A frame
        whose CARRIED claim names this organization's real ledger head is CARRIED_NOT_ACCEPTED_BY_DESTINATION
        (re-send SUPPLIED), never CARRIED_PROVENANCE_UNVERIFIED; a forged, non-current or tampered one stays
        CARRIED_PROVENANCE_UNVERIFIED. Nothing is written either way."""
        manifest = json.loads((ROOT / "org-runtime/interlock-intr.json").read_text(encoding="utf-8"))
        sources = [source for operation in manifest["ingress"]["capability_endpoint_bindings"]
                   for source in operation["receiving_operation"]["hb_epoch_binding"]["sources"]
                   if source["from"] == "carrier_frame"]
        self.assertTrue(sources)
        for source in sources:
            self.assertIs(source["accepts_carried_crossings"], False)
            self.assertEqual(source["carried_refusal"], "CARRIED_NOT_ACCEPTED_BY_DESTINATION")
        self.no_clock(ingress.repository_ledger, ingress.organization_ledger)
        ingress._recorded("ALLOW", {"communication_id": "head", "body": 1}, "head-seed", 4096)
        head = json.loads((Path(os.environ["STEGVERSE_ORG_LEDGER_ROOT"]) / "HEAD.json").read_text(encoding="utf-8"))
        before = sorted(str(p) for p in self.written())
        frame = self.digit_string_frame("4096")
        frame["heartbeat_reference"].update(derived_from_clock=False, source="ORGANIZATION_LEDGER_HEAD",
                                            head_receipt_sha=head["receipt_sha256"])
        frame["frame_sha256"] = kernel.sha({k: v for k, v in frame.items() if k != "frame_sha256"})
        with self.assertRaises(ValueError) as refused:
            kernel.ingest_frame(ROOT, frame, mesh_root=self.mesh)
        self.assertCarriedNotAccepted(refused)
        self.assertEqual(sorted(str(p) for p in self.written()), before)
        # forged, non-current or tampered claims stay CARRIED_PROVENANCE_UNVERIFIED
        ingress._recorded("ALLOW", {"communication_id": "head", "body": 2}, "head-next", 4096)
        stale = head["receipt_sha256"]  # no longer the head
        current = json.loads((Path(os.environ["STEGVERSE_ORG_LEDGER_ROOT"]) / "HEAD.json").read_text(
            encoding="utf-8"))["receipt_sha256"]
        before = sorted(str(p) for p in self.written())
        for case, change in (("non-current head", {"head_receipt_sha": stale}),
                             ("forged head", {"head_receipt_sha": "sha256:" + "f" * 64}),
                             ("other epoch", {"head_receipt_sha": current, "epoch": 4097, "generation": 4097}),
                             ("wrong source", {"head_receipt_sha": current, "source": "SOMEWHERE_ELSE"}),
                             ("clock flag absent", {"head_receipt_sha": current, "derived_from_clock": None})):
            with self.subTest(case=case):
                claim = self.digit_string_frame("4096")
                claim["heartbeat_reference"].update(derived_from_clock=False, source="ORGANIZATION_LEDGER_HEAD")
                claim["heartbeat_reference"].update({k: v for k, v in change.items() if v is not None})
                if "derived_from_clock" in change:
                    claim["heartbeat_reference"].pop("derived_from_clock")
                    claim["heartbeat_reference"]["sampled_unix_ns"] = kernel.HB_ANCHOR_UNIX_NS
                claim["frame_sha256"] = kernel.sha({k: v for k, v in claim.items() if k != "frame_sha256"})
                with self.assertRaises(ValueError) as refused:
                    kernel.ingest_frame(ROOT, claim, mesh_root=self.mesh)
                self.assertCarriedUnverified(refused)
        self.assertEqual(sorted(str(p) for p in self.written()), before)

    def test_a_forged_carried_frame_writes_nothing_on_the_consume_route(self):
        """B-P36k: a forged CARRIED claim is never stripped and accepted as SUPPLIED on any route."""
        self.no_clock(ingress.repository_ledger, ingress.organization_ledger)
        forged = self.fabricated_carried_frame(derived_from_clock=False)
        name = kernel.publish_frame(forged, root=self.mesh).name
        result, = kernel.consume_addressed_frames(ROOT, mesh_root=self.mesh)
        self.assertEqual(result["result"]["failure_code"], "FRAME_REFUSED_QUARANTINED")
        self.assertEqual(result["result"]["refusal_failure_code"], "CARRIED_PROVENANCE_UNVERIFIED")
        self.assertEqual(self.written(), [])  # no receipt at either ledger level
        files = sorted(str(p.relative_to(self.mesh)) for p in self.mesh.rglob("*") if p.is_file())
        self.assertEqual(files, ["frames.d/" + name, "quarantine.d/" + name])
        self.assertEqual(kernel.consume_addressed_frames(ROOT, mesh_root=self.mesh), [])

    def test_an_inconsistent_heartbeat_reference_is_checked_at_ingest(self):
        """B-P36o (08:35Z): recover_packet always runs validate_hb_reference, so a reference whose
        generation, heartbeat_id, frequency or progression disagrees with its epoch is HB_REFERENCE_INVALID."""
        self.no_clock(ingress.repository_ledger, ingress.organization_ledger)
        for key, value in (("generation", 4097), ("generation", "4097"), ("heartbeat_id", "HB:4097"),
                           ("frequency_hz", 99), ("progression_dependency", "CLOCK")):
            frame = self.digit_string_frame("4096")
            frame["heartbeat_reference"]["epoch"] = 4096
            frame["heartbeat_reference"]["generation"] = 4096
            frame["heartbeat_reference"][key] = value
            frame["frame_sha256"] = kernel.sha({k: v for k, v in frame.items() if k != "frame_sha256"})
            with self.subTest(key=key, value=value), self.assertRaises(ValueError) as refused:
                kernel.ingest_frame(ROOT, frame, mesh_root=self.mesh)
            self.assertEqual(refused.exception.refusal["failure_code"], "HB_REFERENCE_INVALID")
            for field in SIX_FIELDS:
                self.assertTrue(refused.exception.refusal.get(field), field)
        self.assertEqual(self.written(), [])

    def test_ingest_takes_a_digit_string_carrier_epoch(self):
        self.no_clock(ingress.repository_ledger, ingress.organization_ledger)
        result = kernel.ingest_frame(ROOT, self.digit_string_frame("4096"), mesh_root=self.mesh)
        operation = result["execution_result"]["application_result"]["receiving_operation_result"]
        self.assertEqual(operation["disposition"], "ALLOW", operation.get("detail"))
        receipts = [json.loads(path.read_text(encoding="utf-8")) for path in self.written()]
        referenced = [item["hb_reference"] for item in receipts if "hb_reference" in item]
        self.assertEqual(len(referenced), 2)
        for reference in referenced:
            self.assertEqual(reference["epoch"], 4096)
            self.assertIs(reference["derived_from_clock"], False)

    def test_ingest_refuses_a_coerced_string_carrier_epoch_with_six_fields(self):
        self.no_clock(ingress.repository_ledger, ingress.organization_ledger)
        for text in ("+4096", " 4096", "4096.0", "\uff14\uff10\uff19\uff16"):
            with self.subTest(epoch=text), self.assertRaises(ValueError) as refused:
                kernel.ingest_frame(ROOT, self.digit_string_frame(text), mesh_root=self.mesh)
            self.assertSixFieldRefusal(refused, "HB_EPOCH_INVALID", "kernel.py::ingest_frame")
        self.assertEqual(self.written(), [])


class CliEpochIsTheSharedRule(Redirected):
    """``--hb-epoch`` is text checked by the shared rule, not argparse ``int()``."""

    def run_cli(self, epoch, env_epoch=None):
        command = [sys.executable, str(ROOT / "resident-runtime/intr_destination_ingress.py"),
                   "--self-closure", "--communication-id", "cli-epoch", "--packet-id", "cli-epoch",
                   "--registry", str(ROOT / "org-boundary/registry/services.json")]
        if epoch is not None:
            command += ["--hb-epoch", epoch]
        environment = {key: value for key, value in os.environ.items() if key != "STEGVERSE_HB_EPOCH"}
        if env_epoch is not None:
            environment["STEGVERSE_HB_EPOCH"] = env_epoch
        return subprocess.run(command, capture_output=True, text=True, env=environment, cwd=ROOT)

    def test_a_coerced_or_absent_cli_epoch_refuses_with_six_fields_and_writes_nothing(self):
        for epoch, code in ((None, "HB_EPOCH_NOT_SUPPLIED"), ("+33", "HB_EPOCH_INVALID"),
                            ("\uff13\uff13", "HB_EPOCH_INVALID"), (" 33", "HB_EPOCH_INVALID"),
                            ("True", "HB_EPOCH_INVALID"), ("abc", "HB_EPOCH_INVALID"),
                            ("33.7", "HB_EPOCH_INVALID")):
            with self.subTest(epoch=epoch):
                ran = self.run_cli(epoch)
                self.assertNotEqual(ran.returncode, 0)
                refusal = json.loads(ran.stdout.strip().splitlines()[-1])
                self.assertEqual(refusal["disposition"], "FAIL_CLOSED")
                self.assertEqual(refusal["failure_code"], code)
                self.assertEqual(refusal["failed_predicate"], "SUBMISSION_CARRIES_THE_SUPPLIED_HB_EPOCH")
                for field in SIX_FIELDS:
                    self.assertTrue(refusal.get(field), field)
                self.assertIn("intr_destination_ingress.py --hb-epoch", refusal["retry_entrypoint"])
        self.assertEqual(self.written(), [])

    def test_an_empty_environment_epoch_is_supplied_and_invalid(self):
        for argument, env_epoch in ((None, ""), ("", None)):
            with self.subTest(argument=argument, env_epoch=env_epoch):
                ran = self.run_cli(argument, env_epoch=env_epoch)
                self.assertNotEqual(ran.returncode, 0)
                refusal = json.loads(ran.stdout.strip().splitlines()[-1])
                self.assertEqual(refusal["failure_code"], "HB_EPOCH_INVALID")
                for field in SIX_FIELDS:
                    self.assertTrue(refusal.get(field), field)
        self.assertEqual(self.written(), [])

    def test_two_supplied_epochs_must_agree(self):
        """B-P36l: an explicit invalid epoch is INVALID; two that differ are HB_EPOCH_CONFLICT; one is supplied."""
        for argument, env_epoch, code in (("+33", "4096", "HB_EPOCH_INVALID"),
                                          ("4096", "4097", "HB_EPOCH_CONFLICT"),
                                          ("4096", "abc", "HB_EPOCH_CONFLICT"),
                                          ("4096", "", "HB_EPOCH_CONFLICT")):
            with self.subTest(argument=argument, env_epoch=env_epoch):
                ran = self.run_cli(argument, env_epoch=env_epoch)
                self.assertNotEqual(ran.returncode, 0)
                refusal = json.loads(ran.stdout.strip().splitlines()[-1])
                self.assertEqual(refusal["failure_code"], code)
                for field in SIX_FIELDS:
                    self.assertTrue(refusal.get(field), field)
        self.assertEqual(self.written(), [])
        agreed = self.run_cli("4096", env_epoch="4096")
        self.assertEqual(agreed.returncode, 0, agreed.stdout + agreed.stderr)
        only_env = self.run_cli(None, env_epoch="4096")
        self.assertEqual(only_env.returncode, 0, only_env.stdout + only_env.stderr)

    def test_a_digit_string_cli_epoch_is_accepted(self):
        ran = self.run_cli("4096")
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        self.assertEqual(json.loads(ran.stdout)["disposition"], "ALLOW")
        receipts = [json.loads(path.read_text(encoding="utf-8")) for path in self.written()]
        referenced = [item["hb_reference"] for item in receipts if "hb_reference" in item]
        self.assertEqual(len(referenced), 2)
        for reference in referenced:
            self.assertEqual(reference["epoch"], 4096)
            self.assertIs(reference["derived_from_clock"], False)


class SecondWriterTakesTheSuppliedEpoch(Redirected):
    """The organization receipt references the supplied epoch, never one read back from a receipt.

    A repository receipt is seeded with no organization receipt, as an
    interrupted run leaves it, then the call is replayed with "4096".
    - Seeded at a different supplied epoch (40): the organization receipt
      references 4096, not the read-back 40.
    - Seeded with a clock-derived reference: the organization writer refuses
      SOURCE_RECEIPT_IS_CLOCK_PROVENANCE with six fields and writes nothing; a
      clock epoch is never relabelled derived_from_clock false.
    """

    FIXED_NS = kernel.HB_ANCHOR_UNIX_NS + 7 * kernel.HB_PERIOD_NS
    ORG_SCHEMA = "stegverse.organization-transition-receipt/v1"

    def seed_repository_receipt(self, writer, organization_ledger, call, reference):
        honest = writer.kernel.supplied_hb_reference
        writer.kernel.supplied_hb_reference = lambda *_a, **_k: reference(writer.kernel)
        appended = organization_ledger.append

        def interrupted(*_a, **_k):
            raise RuntimeError("interrupted before the organization receipt")
        organization_ledger.append = interrupted
        try:
            with self.assertRaises(RuntimeError):
                call()
        finally:
            writer.kernel.supplied_hb_reference = honest
            organization_ledger.append = appended

    def receipts(self):
        return [json.loads(path.read_text(encoding="utf-8")) for path in self.written()]

    def organization_receipts(self):
        return [r for r in self.receipts() if r.get("schema") == self.ORG_SCHEMA]

    def assertForwardsTheSuppliedEpoch(self):
        organization, = self.organization_receipts()
        self.assertEqual(organization["hb_reference"]["epoch"], 4096)
        self.assertIs(organization["hb_reference"]["derived_from_clock"], False)

    def assertClockSourceRefused(self, refused):
        refusal = refused.exception.refusal
        self.assertEqual(refusal["failure_code"], "SOURCE_RECEIPT_IS_CLOCK_PROVENANCE")
        for field in SIX_FIELDS:
            self.assertTrue(refusal.get(field), field)
        self.assertIn("CARRIED:LEGACY_CLOCK_RECEIPT", refusal["required_evidence_or_repair"])
        self.assertEqual(self.organization_receipts(), [])
        for receipt in self.receipts():
            reference = receipt.get("hb_reference")
            if reference is not None and reference.get("derived_from_clock") is False:
                self.fail("a receipt claims a supplied epoch it was not given: " + repr(reference))

    def ingress_call(self):
        return lambda: ingress.receive(diagnostic("laundered"), registry=REGISTRY,
                                       packet_id="laundered", hb_epoch="4096")

    def egress_call(self):
        record = {"intended_action": "EMIT", "disposition": "ALLOW", "crossed_an_organization_boundary": True,
                  "frame_sha256": "sha256:" + "1" * 64, "payload_sha256": "sha256:" + "2" * 64}
        return lambda: egress._record("EGRESS:laundered", egress.EMITTED_CLASS, record["payload_sha256"],
                                      kernel.sha(record), record, 4096)

    def test_ingress_replay_forwards_the_supplied_epoch_not_a_read_back_one(self):
        call = self.ingress_call()
        self.seed_repository_receipt(ingress.repository_ledger, ingress.organization_ledger, call,
                                     lambda k: k.hb_reference(epoch=40))
        self.assertEqual(call()["disposition"], "ALLOW")
        self.assertForwardsTheSuppliedEpoch()

    def test_egress_record_replay_forwards_the_supplied_epoch_not_a_read_back_one(self):
        call = self.egress_call()
        self.seed_repository_receipt(egress.repository_ledger, egress.organization_ledger, call,
                                     lambda k: k.hb_reference(epoch=40))
        call()
        self.assertForwardsTheSuppliedEpoch()

    def test_ingress_refuses_to_bind_a_clock_provenance_source(self):
        call = self.ingress_call()
        self.seed_repository_receipt(ingress.repository_ledger, ingress.organization_ledger, call,
                                     lambda k: k.hb_reference(self.FIXED_NS))
        with self.assertRaises(ValueError) as refused:
            call()
        self.assertClockSourceRefused(refused)

    def test_egress_record_refuses_to_bind_a_clock_provenance_source(self):
        call = self.egress_call()
        self.seed_repository_receipt(egress.repository_ledger, egress.organization_ledger, call,
                                     lambda k: k.hb_reference(self.FIXED_NS))
        with self.assertRaises(ValueError) as refused:
            call()
        self.assertClockSourceRefused(refused)

    def test_the_organization_writer_refuses_an_unmarked_source(self):
        guard_free = {"receipt_sha256": "sha256:" + "3" * 64, "hb_reference": {"epoch": 40}}
        with self.assertRaises(ValueError) as refused:
            aggregate.append(guard_free, "REPO_STATE_PROPAGATION", "sha256:a", "sha256:b", {}, "NONE",
                             hb_epoch=4096)
        self.assertEqual(refused.exception.refusal["failure_code"], "SOURCE_RECEIPT_IS_CLOCK_PROVENANCE")
        self.assertEqual(self.organization_receipts(), [])

    def test_an_absent_epoch_writes_nothing_even_with_a_fixed_clock(self):
        fixed = self.FIXED_NS
        for module in (ingress.repository_ledger, ingress.organization_ledger):
            saved = module.kernel.datetime

            class Fixed:
                @staticmethod
                def now(*_a, **_k):
                    from datetime import datetime, timezone
                    return datetime.fromtimestamp(fixed / 1e9, timezone.utc)
            module.kernel.datetime = Fixed
            self.addCleanup(setattr, module.kernel, "datetime", saved)
        with self.assertRaises(ValueError):
            ingress.receive(diagnostic("absent-fixed"), registry=REGISTRY, packet_id="absent-fixed")
        self.assertEqual(self.written(), [])


class CarriedProvenanceIsVerified(Redirected):
    """A writer given CARRIED provenance verifies it against the organization ledger head.

    head_receipt_sha must be the current head, the head's seal must verify, and its
    epoch must be supplied-rule, non-clock and equal to the carried epoch. Anything
    else is six-field CARRIED_PROVENANCE_UNVERIFIED with nothing written.
    """

    SOURCE = {"receipt_sha256": "sha256:" + "4" * 64,
              "hb_reference": {"epoch": 40, "derived_from_clock": False}}

    def seed_head(self):
        return aggregate.append(self.SOURCE, "REPO_STATE_PROPAGATION", "sha256:a", "sha256:b", {}, "NONE",
                                hb_epoch=40)

    def provenance(self, head_sha):
        return {"source": "ORGANIZATION_LEDGER_HEAD", "head_receipt_sha": head_sha}

    def assertUnverified(self, refused, before):
        refusal = refused.exception.refusal
        self.assertEqual(refusal["failure_code"], "CARRIED_PROVENANCE_UNVERIFIED")
        for field in SIX_FIELDS:
            self.assertTrue(refusal.get(field), field)
        self.assertEqual(sorted(self.written()), before)

    def test_an_invented_head_sha_is_refused_by_both_writers(self):
        self.seed_head()
        before = sorted(self.written())
        invented = self.provenance("sha256:" + "9" * 64)
        with self.assertRaises(ValueError) as refused:
            emit.append("t:invented", "T", "sha256:a", "sha256:b", {}, "NONE", hb_epoch="4096",
                        hb_provenance=invented)
        self.assertUnverified(refused, before)
        source = {"receipt_sha256": "sha256:" + "5" * 64,
                  "hb_reference": {"epoch": 4096, "derived_from_clock": False}}
        with self.assertRaises(ValueError) as refused:
            aggregate.append(source, "REPO_STATE_PROPAGATION", "sha256:a", "sha256:b", {}, "NONE",
                             hb_epoch="4096", hb_provenance=invented)
        self.assertUnverified(refused, before)

    def test_a_carried_epoch_other_than_the_head_epoch_is_refused(self):
        head = self.seed_head()
        before = sorted(self.written())
        with self.assertRaises(ValueError) as refused:
            emit.append("t:mismatch", "T", "sha256:a", "sha256:b", {}, "NONE", hb_epoch=41,
                        hb_provenance=self.provenance(head["receipt_sha256"]))
        self.assertUnverified(refused, before)

    def test_a_tampered_head_receipt_is_refused(self):
        head = self.seed_head()
        path, = [p for p in Path(os.environ["STEGVERSE_ORG_LEDGER_ROOT"]).rglob("*.json")
                 if head["receipt_sha256"][7:] in p.name]
        tampered = json.loads(path.read_text(encoding="utf-8"))
        tampered["evidence"] = {"tampered": True}
        path.write_text(json.dumps(tampered), encoding="utf-8")
        before = sorted(self.written())
        with self.assertRaises(ValueError) as refused:
            emit.append("t:tampered", "T", "sha256:a", "sha256:b", {}, "NONE", hb_epoch=40,
                        hb_provenance=self.provenance(head["receipt_sha256"]))
        self.assertUnverified(refused, before)

    def test_the_real_head_is_carried_by_both_writers(self):
        head = self.seed_head()
        carried = self.provenance(head["receipt_sha256"])
        repository = emit.append("t:carried", "T", "sha256:a", "sha256:b", {}, "NONE", hb_epoch=40,
                                 hb_provenance=carried)
        organization = aggregate.append(repository, "REPO_STATE_PROPAGATION", "sha256:a", "sha256:b", {},
                                        "NONE", hb_epoch=40, hb_provenance=carried)
        for receipt in (repository, organization):
            self.assertEqual(receipt["hb_reference"]["source"], "ORGANIZATION_LEDGER_HEAD")
            self.assertEqual(receipt["hb_reference"]["head_receipt_sha"], head["receipt_sha256"])
            self.assertEqual(receipt["hb_reference"]["epoch"], 40)
            self.assertIs(receipt["hb_reference"]["derived_from_clock"], False)


class ConsumedRouteNeverReachesAClockDefault(Redirected):
    """B-P36g (07:10Z) row 2: a CONSUMED frame route reads no clock at all.

    The capability ingress loads its receiving operation (and that operation's
    writers and kernel copy) afresh on every dispatch, so patching an already
    loaded copy is not enough: the ``datetime`` module itself is replaced for the
    duration, and any copy loaded during the call binds a ``datetime`` whose
    ``now`` raises.
    """

    def test_a_consumed_frame_makes_zero_clock_calls(self):
        import datetime as real
        import sys
        import types
        calls = []

        class Raising(real.datetime):
            @classmethod
            def now(cls, *_a, **_k):
                calls.append("now")
                raise AssertionError("a CONSUMED route read the clock")
        shim = types.ModuleType("datetime")
        shim.__dict__.update({name: getattr(real, name) for name in dir(real) if not name.startswith("__")})
        shim.datetime = Raising
        sys.modules["datetime"] = shim
        self.addCleanup(sys.modules.__setitem__, "datetime", real)
        saved = kernel.datetime
        kernel.datetime = Raising
        self.addCleanup(setattr, kernel, "datetime", saved)
        packet = kernel.build_packet(
            origin_org="StegVerse-org", origin_service="stegverse-org.intr-destination",
            destination_org="SV-011", destination_service=ADDRESS,
            payload=diagnostic("zero-clock"), standing=GENESIS,
            transition_reference="intr:transition:zero-clock", authority_effect="NONE", packet_id="zero-clock")
        kernel.publish_packet(packet, root=self.mesh, epoch="4096")
        result, = kernel.consume_addressed_frames(
            ROOT, mesh_root=self.mesh,
            repo_ledger_root=os.environ["STEGVERSE_REPO_LEDGER_ROOT"],
            org_ledger_root=os.environ["STEGVERSE_ORG_LEDGER_ROOT"])
        self.assertEqual(result["result"]["status"], "CONSUMED")
        operation = result["result"]["execution_result"]["application_result"]["receiving_operation_result"]
        self.assertEqual(operation["disposition"], "ALLOW", operation.get("detail"))
        self.assertEqual(calls, [])
        referenced = [json.loads(p.read_text(encoding="utf-8")).get("hb_reference") for p in self.written()]
        referenced = [r for r in referenced if r is not None]
        self.assertEqual(len(referenced), 2)
        for reference in referenced:
            self.assertEqual(reference["epoch"], 4096)
            self.assertIs(reference["derived_from_clock"], False)


class MixedBatchQuarantinesTheRefusedFrame(Redirected):
    """B-P36j: a refused frame in a mixed batch is terminal and never blocks a valid one.

    The refused frame gets one write-once six-field FRAME_REFUSED_QUARANTINED
    marker (not a receipt) and is marked seen; the valid frame in the same batch
    is consumed whichever of the two sorts first; a later cycle, even with a
    fresh seen set, neither re-refuses nor re-consumes. No clock is read.
    """

    def frame(self, packet_id, **stamp):
        packet = kernel.build_packet(
            origin_org="StegVerse-org", origin_service="stegverse-org.intr-destination",
            destination_org="SV-011", destination_service=ADDRESS,
            payload=diagnostic(packet_id), standing=GENESIS,
            transition_reference="intr:transition:" + packet_id, authority_effect="NONE", packet_id=packet_id)
        if "now_ns" in stamp:
            return kernel.carrier_frame(packet, now_ns=stamp["now_ns"])
        return kernel.carrier_frame(packet, epoch=stamp["epoch"])

    def name(self, frame):
        return kernel._frame_path(self.mesh, frame).name

    def pair(self, refused_first):
        """A clock-stamped frame and a valid one whose file names sort in the requested order."""
        for index in range(64):
            bad = self.frame("mixed-bad-%d" % index, now_ns=kernel.HB_ANCHOR_UNIX_NS + 3 * kernel.HB_PERIOD_NS)
            good = self.frame("mixed-good-%d" % index, epoch=4096)
            if (self.name(bad) < self.name(good)) == refused_first:
                return bad, good
        self.fail("no pair in the requested sort order")

    def consume(self, seen=None):
        return kernel.consume_addressed_frames(
            ROOT, mesh_root=self.mesh, seen=seen,
            repo_ledger_root=os.environ["STEGVERSE_REPO_LEDGER_ROOT"],
            org_ledger_root=os.environ["STEGVERSE_ORG_LEDGER_ROOT"])

    def run_batch(self, refused_first):
        import datetime as real
        import sys
        import types
        calls = []

        class Raising(real.datetime):
            @classmethod
            def now(cls, *_a, **_k):
                calls.append("now")
                raise AssertionError("the batch read the clock")
        shim = types.ModuleType("datetime")
        shim.__dict__.update({n: getattr(real, n) for n in dir(real) if not n.startswith("__")})
        shim.datetime = Raising
        bad, good = self.pair(refused_first)
        for frame in (bad, good):
            kernel.publish_frame(frame, root=self.mesh)
        sys.modules["datetime"] = shim
        try:
            seen = set()
            results = self.consume(seen)
            again = self.consume()  # a later cycle with a fresh seen set
        finally:
            sys.modules["datetime"] = real
        self.assertEqual(calls, [])
        self.assertEqual([r["path"].rsplit("/", 1)[1] for r in results], sorted([self.name(bad), self.name(good)]))
        by_name = {r["path"].rsplit("/", 1)[1]: r["result"] for r in results}
        marker = by_name[self.name(bad)]
        self.assertEqual(marker["status"], "REFUSED")
        self.assertEqual(marker["failure_code"], "FRAME_REFUSED_QUARANTINED")
        self.assertEqual(marker["refusal_failure_code"], "HB_EPOCH_NOT_SUPPLIED")  # consumed, no epoch lent
        self.assertIs(marker["terminal"], True)
        self.assertIs(marker["is_a_receipt"], False)
        for field in SIX_FIELDS:
            self.assertTrue(marker.get(field), field)
        on_disk = json.loads((self.mesh / "quarantine.d" / self.name(bad)).read_text(encoding="utf-8"))
        self.assertEqual(on_disk, marker)
        consumed = by_name[self.name(good)]
        self.assertEqual(consumed["status"], "CONSUMED")
        operation = consumed["execution_result"]["application_result"]["receiving_operation_result"]
        self.assertEqual(operation["disposition"], "ALLOW", operation.get("detail"))
        self.assertEqual(seen, {self.name(bad), self.name(good)})
        # the next cycle does not re-refuse the quarantined frame; only the consumed one is offered again
        self.assertEqual([r["path"].rsplit("/", 1)[1] for r in again], [self.name(good)])
        receipts = [json.loads(p.read_text(encoding="utf-8")) for p in self.written()]
        for reference in [r["hb_reference"] for r in receipts if "hb_reference" in r]:
            self.assertEqual(reference["epoch"], 4096)
            self.assertIs(reference["derived_from_clock"], False)

    def malformed_clock_frame(self, packet_id, epoch):
        frame = self.frame(packet_id, now_ns=kernel.HB_ANCHOR_UNIX_NS + 3 * kernel.HB_PERIOD_NS)
        if epoch is None:
            del frame["heartbeat_reference"]["epoch"]
        else:
            frame["heartbeat_reference"]["epoch"] = epoch
        frame["frame_sha256"] = kernel.sha({k: v for k, v in frame.items() if k != "frame_sha256"})
        return frame

    def test_a_malformed_clock_flagged_frame_is_quarantined_in_either_order(self):
        """F-S1a: a derived-true frame with a bad epoch is six-field INVALID and quarantined, never aborts."""
        for bad_epoch in ("abc", None, 5):
            for refused_first in (True, False):
                with self.subTest(epoch=bad_epoch, refused_first=refused_first):
                    for directory in ("frames.d", "quarantine.d"):
                        for path in (self.mesh / directory).glob("*.json") if (self.mesh / directory).is_dir() else []:
                            path.unlink()
                    for index in range(64):
                        bad = self.malformed_clock_frame("malformed-%s-%d" % (bad_epoch, index), bad_epoch)
                        good = self.frame("malformed-good-%s-%d" % (bad_epoch, index), epoch=4096)
                        if (self.name(bad) < self.name(good)) == refused_first:
                            break
                    for frame in (bad, good):
                        kernel.publish_frame(frame, root=self.mesh)
                    results = self.consume(set())
                    by_name = {r["path"].rsplit("/", 1)[1]: r["result"] for r in results}
                    marker = by_name[self.name(bad)]
                    self.assertEqual(marker["failure_code"], "FRAME_REFUSED_QUARANTINED")
                    self.assertEqual(marker["refusal_failure_code"],
                                     "HB_EPOCH_NOT_SUPPLIED" if bad_epoch is None else "HB_EPOCH_INVALID")
                    for field in SIX_FIELDS:
                        self.assertTrue(marker.get(field), field)
                    self.assertEqual(by_name[self.name(good)]["status"], "CONSUMED")
                    self.assertEqual([r["path"].rsplit("/", 1)[1] for r in self.consume()], [self.name(good)])

    def bare_sampled_frame(self, packet_id, epoch="keep"):
        frame = self.frame(packet_id, now_ns=kernel.HB_ANCHOR_UNIX_NS + 3 * kernel.HB_PERIOD_NS)
        reference = frame["heartbeat_reference"]
        del reference["derived_from_clock"]
        if epoch is None:
            del reference["epoch"]
        elif epoch != "keep":
            reference["epoch"] = epoch
        frame["frame_sha256"] = kernel.sha({k: v for k, v in frame.items() if k != "frame_sha256"})
        return frame

    def test_a_bare_sampled_frame_is_clock_consumed_and_quarantined(self):
        """08:10Z: a bare-sampled frame is CLOCK — consumed, no epoch lent, NOT_SUPPLIED, quarantined;
        a malformed one is INVALID / NOT_SUPPLIED, quarantined; the valid frame is answered either way."""
        for bad_epoch, code in (("keep", "HB_EPOCH_NOT_SUPPLIED"), ("abc", "HB_EPOCH_INVALID"),
                                (5, "HB_EPOCH_INVALID"), (None, "HB_EPOCH_NOT_SUPPLIED")):
            for refused_first in (True, False):
                with self.subTest(epoch=bad_epoch, refused_first=refused_first):
                    for directory in ("frames.d", "quarantine.d"):
                        if (self.mesh / directory).is_dir():
                            for path in (self.mesh / directory).glob("*.json"):
                                path.unlink()
                    for index in range(64):
                        bad = self.bare_sampled_frame("bare-%s-%d" % (bad_epoch, index), bad_epoch)
                        good = self.frame("bare-good-%s-%d" % (bad_epoch, index), epoch=4096)
                        if (self.name(bad) < self.name(good)) == refused_first:
                            break
                    for frame in (bad, good):
                        kernel.publish_frame(frame, root=self.mesh)
                    by_name = {r["path"].rsplit("/", 1)[1]: r["result"] for r in self.consume(set())}
                    marker = by_name[self.name(bad)]
                    self.assertEqual(marker["failure_code"], "FRAME_REFUSED_QUARANTINED")
                    self.assertEqual(marker["refusal_failure_code"], code)
                    for field in SIX_FIELDS:
                        self.assertTrue(marker.get(field), field)
                    self.assertEqual(by_name[self.name(good)]["status"], "CONSUMED")
                    self.assertEqual([r["path"].rsplit("/", 1)[1] for r in self.consume()], [self.name(good)])

    def test_an_unrecoverable_frame_is_quarantined_in_either_order(self):
        """B-P36o: a valid epoch with a bad heartbeat reference, a tampered hash, or an adapter error is a
        six-field refusal and a terminal quarantine — 0 clock reads, 0 ledger files, the batch continues."""
        def generation(frame):
            frame["heartbeat_reference"]["generation"] = 12345
        def heartbeat_id(frame):
            frame["heartbeat_reference"]["heartbeat_id"] = "HB:1"
        def packet_hash(frame):
            frame["packet_sha256"] = "sha256:" + "0" * 64
        cases = ((generation, True, "HB_REFERENCE_INVALID"), (heartbeat_id, True, "HB_REFERENCE_INVALID"),
                 (packet_hash, True, "FRAME_INTEGRITY_INVALID"), (None, False, "FRAME_INTEGRITY_INVALID"))
        for mutate, reseal, code in cases:
            for refused_first in (True, False):
                with self.subTest(code=code, mutate=getattr(mutate, "__name__", "frame_sha256"),
                                  refused_first=refused_first):
                    for directory in ("frames.d", "quarantine.d"):
                        if (self.mesh / directory).is_dir():
                            for path in (self.mesh / directory).glob("*.json"):
                                path.unlink()
                    for index in range(64):
                        bad = self.frame("unrecoverable-%s-%d" % (code, index), epoch=4096)
                        if mutate is not None:
                            mutate(bad)
                        if reseal:
                            bad["frame_sha256"] = kernel.sha({k: v for k, v in bad.items() if k != "frame_sha256"})
                        else:
                            bad["frame_sha256"] = "sha256:" + "e" * 64
                        good = self.frame("unrecoverable-good-%s-%d" % (code, index), epoch=4096)
                        if (self.name(bad) < self.name(good)) == refused_first:
                            break
                    for frame in (bad, good):
                        kernel.publish_frame(frame, root=self.mesh)
                    by_name = {r["path"].rsplit("/", 1)[1]: r["result"] for r in self.consume(set())}
                    marker = by_name[self.name(bad)]
                    self.assertEqual(marker["failure_code"], "FRAME_REFUSED_QUARANTINED")
                    self.assertEqual(marker["refusal_failure_code"], code)
                    for field in SIX_FIELDS:
                        self.assertTrue(marker.get(field), field)
                    self.assertEqual(by_name[self.name(good)]["status"], "CONSUMED")
                    self.assertEqual([r["path"].rsplit("/", 1)[1] for r in self.consume()], [self.name(good)])

    def test_an_unreadable_frame_file_is_quarantined_in_either_order(self):
        """F-SCAN / B-P36p: a frames.d entry that cannot be read, is not UTF-8, is not JSON, is not an object, or
        exceeds FRAME_MAX_BYTES is quarantined by name with a six-field
        FRAME_INGEST_FAILED naming the exception class; the valid frame is consumed whichever sorts first."""
        oversized = '{"pad": "' + "x" * kernel.FRAME_MAX_BYTES + '"}'
        for content, exception in (("{not json", "JSONDecodeError"), ("[1,2,3]", "TypeError"), ('"x"', "TypeError"),
                                   (b"\xff\xfe{}", "UnicodeDecodeError"), (oversized, "FrameOversized"),
                                   (None, "IsADirectoryError")):
            for refused_first in (True, False):
                with self.subTest(content=content, refused_first=refused_first):
                    for directory in ("frames.d", "quarantine.d"):
                        if (self.mesh / directory).is_dir():
                            for path in (self.mesh / directory).glob("*.json"):
                                path.rmdir() if path.is_dir() else path.unlink()
                    good = self.frame("unreadable-good-%s" % refused_first, epoch=4096)
                    kernel.publish_frame(good, root=self.mesh)
                    name = ("0" if refused_first else "z") * 8 + ".json"
                    self.assertEqual(name < self.name(good), refused_first)
                    entry = self.mesh / "frames.d" / name
                    if content is None:
                        entry.mkdir()  # an entry named *.json that cannot be read as a file
                    elif isinstance(content, bytes):
                        entry.write_bytes(content)
                    else:
                        entry.write_text(content, encoding="utf-8")
                    results = self.consume(set())
                    self.assertEqual([r["path"].rsplit("/", 1)[1] for r in results], sorted([name, self.name(good)]))
                    by_name = {r["path"].rsplit("/", 1)[1]: r["result"] for r in results}
                    marker = by_name[name]
                    self.assertEqual(marker["failure_code"], "FRAME_REFUSED_QUARANTINED")
                    self.assertEqual(marker["refusal_failure_code"], "FRAME_INGEST_FAILED")
                    self.assertEqual(marker["failed_predicate"], "FRAME_INGESTS_WITHOUT_ERROR:" + exception)
                    self.assertIs(marker["is_a_receipt"], False)
                    for field in SIX_FIELDS:
                        self.assertTrue(marker.get(field), field)
                    self.assertTrue((self.mesh / "quarantine.d" / name).is_file())
                    self.assertEqual(by_name[self.name(good)]["status"], "CONSUMED")
                    self.assertEqual([r["path"].rsplit("/", 1)[1] for r in self.consume()], [self.name(good)])

    def test_an_unreadable_frame_file_alone_writes_nothing_and_reads_no_clock(self):
        import datetime as real
        import sys
        import types
        calls = []

        class Raising(real.datetime):
            @classmethod
            def now(cls, *_a, **_k):
                calls.append("now")
                raise AssertionError("read the clock")
        shim = types.ModuleType("datetime")
        shim.__dict__.update({n: getattr(real, n) for n in dir(real) if not n.startswith("__")})
        shim.datetime = Raising
        (self.mesh / "frames.d").mkdir(parents=True, exist_ok=True)
        (self.mesh / "frames.d" / "00000000.json").write_text("{not json", encoding="utf-8")
        before = self.written()
        sys.modules["datetime"] = shim
        try:
            results = self.consume(set())
        finally:
            sys.modules["datetime"] = real
        self.assertEqual(calls, [])
        self.assertEqual(self.written(), before)
        self.assertEqual(results[0]["result"]["refusal_failure_code"], "FRAME_INGEST_FAILED")
        self.assertEqual(self.consume(), [])

    def test_a_mistyped_frame_field_is_quarantined_in_either_order(self):
        """F-TYPE: a resealed frame whose string field is 123, null, a list or a dict is a six-field
        FRAME_INGEST_FAILED and a terminal quarantine; the valid frame is consumed whichever sorts first."""
        for field in kernel.CARRIER_FRAME_STRING_FIELDS:
            for value in (123, None, [1], {"a": 1}):
                for refused_first in (True, False):
                    with self.subTest(field=field, value=value, refused_first=refused_first):
                        for directory in ("frames.d", "quarantine.d"):
                            if (self.mesh / directory).is_dir():
                                for path in (self.mesh / directory).glob("*.json"):
                                    path.unlink()
                        for index in range(64):
                            packet_id = "mistyped-%s-%d" % (field, index)
                            bad = self.frame(packet_id, epoch=4096)
                            bad[field] = value
                            bad["frame_sha256"] = kernel.sha({k: v for k, v in bad.items() if k != "frame_sha256"})
                            bad_name = hashlib.sha256((packet_id + "|" + bad["frame_sha256"]).encode()).hexdigest() + ".json"
                            good = self.frame("mistyped-good-%s-%d" % (field, index), epoch=4096)
                            if (bad_name < self.name(good)) == refused_first:
                                break
                        kernel.publish_frame(good, root=self.mesh)
                        (self.mesh / "frames.d" / bad_name).write_text(json.dumps(bad), encoding="utf-8")
                        results = self.consume(set())
                        by_name = {r["path"].rsplit("/", 1)[1]: r["result"] for r in results}
                        if field == "destination_org":
                            self.assertNotIn(bad_name, by_name)  # addressed to no organization: not offered here
                        else:
                            marker = by_name[bad_name]
                            self.assertEqual(marker["failure_code"], "FRAME_REFUSED_QUARANTINED")
                            self.assertEqual(marker["refusal_failure_code"], "FRAME_INGEST_FAILED")
                            self.assertEqual(marker["failed_predicate"], "FRAME_INGESTS_WITHOUT_ERROR:TypeError")
                            for key in SIX_FIELDS:
                                self.assertTrue(marker.get(key), key)
                        self.assertEqual(by_name[self.name(good)]["status"], "CONSUMED")
                        self.assertEqual([r["path"].rsplit("/", 1)[1] for r in self.consume()], [self.name(good)])

    def test_recover_packet_type_checks_before_decoding(self):
        frame = self.frame("typed", epoch=4096)
        for value in (123, None, [1], {"a": 1}):
            with self.subTest(value=value):
                bad = dict(frame, packet_base64=value)
                with self.assertRaises(TypeError):
                    kernel.recover_packet(bad)

    def test_an_io_fault_on_our_ledger_leaves_the_frame_pending_and_resumes_once(self):
        """V-P11 (OSError ruling): an OSError writing this repository's ledger is FRAME_DISPATCH_IO_FAILED,
        non-terminal: no marker, not seen, nothing consumed. The next cycle consumes the frame once,
        resuming a partial write without a duplicate receipt."""
        roots = [Path(os.environ[name]).resolve() for name in self.ROOTS]
        real_write = Path.write_bytes
        # 1: the repository receipt; 2: its HEAD; 3: the organization receipt, after the repository chain
        # landed; 4: the organization HEAD, after its content-addressed receipt landed.
        # torn (B-P36q): part of the document reaches the disk before the fault.
        for fail_at, torn in [(step, torn) for torn in (False, True) for step in (1, 2, 3, 4)]:
            with self.subTest(fail_at=fail_at, torn=torn):
                for directory in ("frames.d", "quarantine.d"):
                    if (self.mesh / directory).is_dir():
                        for path in (self.mesh / directory).glob("*.json"):
                            path.unlink()
                good = self.frame("io-fault-%d-%s" % (fail_at, torn), epoch=4096)
                kernel.publish_frame(good, root=self.mesh)
                writes = []

                def failing(path, data, _real=real_write, _torn=torn):
                    if any(root in Path(path).resolve().parents for root in roots):
                        writes.append(path)
                        if len(writes) == fail_at:
                            if _torn:
                                _real(path, data[: len(data) // 2])
                            raise OSError(28, "No space left on device")
                    return _real(path, data)
                seen = set()
                Path.write_bytes = failing
                try:
                    faulted = self.consume(seen)
                finally:
                    Path.write_bytes = real_write
                self.assertEqual(len(writes), fail_at)
                [row] = faulted
                refusal = row["result"]
                self.assertEqual(refusal["failure_code"], "FRAME_DISPATCH_IO_FAILED")
                self.assertEqual(refusal["failed_predicate"], "DISPATCH_WRITES_THIS_REPOSITORYS_STATE:OSError")
                self.assertIs(refusal["terminal"], False)
                for field in SIX_FIELDS:
                    self.assertTrue(refusal.get(field), field)
                self.assertEqual(seen, set())
                self.assertFalse((self.mesh / "quarantine.d" / self.name(good)).exists())
                [again] = self.consume(set())
                self.assertEqual(again["result"]["status"], "CONSUMED")
                operation = again["result"]["execution_result"]["application_result"]["receiving_operation_result"]
                self.assertEqual(operation["disposition"], "ALLOW", operation.get("detail"))
                after = sorted(str(p) for p in self.written())
                self.consume(set())  # one more cycle writes nothing new
                self.assertEqual(sorted(str(p) for p in self.written()), after)
                receipts = [json.loads(p.read_text(encoding="utf-8")) for p in self.written()]
                ids = [r["transition_id"] for r in receipts
                       if "transition_id" in r and r["transition_id"].endswith("io-fault-%d-%s" % (fail_at, torn))]
                self.assertEqual(len(ids), len(set(ids)), "a duplicate repository receipt was written")
                self.assertEqual(len(ids), 1)
                [repository] = [r for r in receipts if r.get("transition_id") in ids]
                bound = [r for r in receipts if r.get("repo_receipt_sha256") == repository["receipt_sha256"]]
                self.assertEqual(len(bound), 1, "a duplicate organization receipt was written")
                # torn write (09:29Z): HEAD is consistent and every receipt is on the chain; a later frame is unaffected
                self.assertConsistentChains()
                later = self.frame("io-fault-later-%d-%s" % (fail_at, torn), epoch=4096)
                kernel.publish_frame(later, root=self.mesh)
                consumed = {r["path"].rsplit("/", 1)[1]: r["result"] for r in self.consume(set())}
                self.assertEqual(consumed[self.name(later)]["status"], "CONSUMED")
                self.assertConsistentChains()

    def assertConsistentChains(self):
        """HEAD names a sealed receipt, and walking previous_receipt_sha256 from it reaches every receipt."""
        for name in self.ROOTS:
            root = Path(os.environ[name])
            files = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in (root / "receipts").glob("*.json")}
            head = json.loads((root / "HEAD.json").read_text(encoding="utf-8"))["receipt_sha256"]
            walked, digest = set(), head
            while digest is not None:
                receipt = files[digest.split(":", 1)[1]]
                self.assertEqual(receipt["receipt_sha256"], digest)
                self.assertEqual(kernel.sha({k: v for k, v in receipt.items() if k != "receipt_sha256"}), digest)
                walked.add(digest)
                digest = receipt.get("previous_receipt_sha256")
            self.assertEqual(walked, {r["receipt_sha256"] for r in files.values()}, name + ": an orphan receipt")

    def test_a_torn_frame_publish_leaves_no_partial_frame_and_republishes_once(self):
        """B-P36q: publish_frame is staged and renamed. A fault mid-write leaves no frame, so the re-publish
        completes once instead of raising federation_frame_write_once_collision forever."""
        real_write = Path.write_text
        frame = self.frame("torn-publish", epoch=4096)
        frames = (self.mesh / "frames.d").resolve()

        def torn(path, data, *args, _real=real_write, **kwargs):
            if Path(path).resolve().parent == frames:
                _real(path, data[: len(data) // 2], *args, **kwargs)
                raise OSError(28, "No space left on device")
            return _real(path, data, *args, **kwargs)
        Path.write_text = torn
        try:
            with self.assertRaises(OSError):
                kernel.publish_frame(frame, root=self.mesh)
        finally:
            Path.write_text = real_write
        self.assertEqual(sorted(p.name for p in frames.iterdir()), [])  # no partial frame, no staged file
        path = kernel.publish_frame(frame, root=self.mesh)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), frame)
        self.assertEqual(kernel.publish_frame(frame, root=self.mesh), path)  # write-once replay
        self.assertEqual(len(list(frames.glob("*.json"))), 1)
        [row] = self.consume(set())
        self.assertEqual(row["result"]["status"], "CONSUMED")

    def test_an_io_fault_on_the_quarantine_marker_does_not_abort_the_batch(self):
        """F-REPUB scope (V-P11): the consume path publishes nothing, so no response can be re-published. A fault
        writing a refused frame's marker is FRAME_DISPATCH_IO_FAILED: no partial marker, not seen, and the batch
        goes on; the next cycle quarantines the frame once."""
        real_write = Path.write_text
        for refused_first in (True, False):
            with self.subTest(refused_first=refused_first):
                for directory in ("frames.d", "quarantine.d"):
                    if (self.mesh / directory).is_dir():
                        for path in (self.mesh / directory).iterdir():
                            path.unlink()
                bad, good = self.pair(refused_first)
                for frame in (bad, good):
                    kernel.publish_frame(frame, root=self.mesh)
                quarantine = (self.mesh / "quarantine.d").resolve()

                def failing(path, data, *args, _real=real_write, **kwargs):
                    if Path(path).resolve().parent == quarantine:
                        _real(path, data[:7], *args, **kwargs)  # a partial write, then the fault
                        raise OSError(28, "No space left on device")
                    return _real(path, data, *args, **kwargs)
                seen = set()
                Path.write_text = failing
                try:
                    results = self.consume(seen)
                finally:
                    Path.write_text = real_write
                by_name = {r["path"].rsplit("/", 1)[1]: r["result"] for r in results}
                self.assertEqual(by_name[self.name(bad)]["failure_code"], "FRAME_DISPATCH_IO_FAILED")
                self.assertIs(by_name[self.name(bad)]["terminal"], False)
                self.assertEqual(by_name[self.name(good)]["status"], "CONSUMED")
                self.assertEqual(seen, {self.name(good)})
                self.assertFalse((self.mesh / "quarantine.d" / self.name(bad)).exists())
                again = {r["path"].rsplit("/", 1)[1]: r["result"] for r in self.consume(set())}
                self.assertEqual(again[self.name(bad)]["failure_code"], "FRAME_REFUSED_QUARANTINED")
                marker = json.loads((self.mesh / "quarantine.d" / self.name(bad)).read_text(encoding="utf-8"))
                self.assertEqual(marker, again[self.name(bad)])
                self.assertEqual([r["path"].rsplit("/", 1)[1] for r in self.consume()], [self.name(good)])

    def test_a_lost_comparison_on_our_ledger_leaves_the_frame_pending_and_resumes_once(self):
        """V-P11 F-TRANSIENT-RT: a writer's lost compare-and-swap is LedgerWriteFault, FRAME_DISPATCH_WRITE_FAILED,
        non-terminal: no marker, not seen. The next cycle consumes the frame once: 1 repository receipt,
        1 organization receipt, no orphan."""
        import importlib.machinery
        real_exec = importlib.machinery.SourceFileLoader.exec_module
        cases = [(fault, fail_at) for fault in ("lost_comparison", "store_collision") for fail_at in (1, 2)]
        for fault, fail_at in cases:  # fail_at 1: the repository append; 2: the organization append, after it
            with self.subTest(fault=fault, fail_at=fail_at):
                for directory in ("frames.d", "quarantine.d"):
                    if (self.mesh / directory).is_dir():
                        for path in (self.mesh / directory).glob("*.json"):
                            path.unlink()
                tag = "%s-%d" % (fault, fail_at)
                good = self.frame("lost-cas-" + tag, epoch=4096)
                kernel.publish_frame(good, root=self.mesh)
                appends = []

                def exec_module(loader, module, _real=real_exec):
                    # Each writer loads its own ledger_store copy; lose the comparison on the fail_at-th append.
                    _real(loader, module)
                    if module.__name__ == "ledger_store":
                        original = module.Store.append_transaction

                        def append_transaction(store, *args, _original=original, **kwargs):
                            appends.append(store.root)
                            if len(appends) == fail_at:
                                if fault == "store_collision":
                                    raise module.LedgerStoreCollision("ledger_receipt_collision")
                                return False
                            return _original(store, *args, **kwargs)
                        module.Store.append_transaction = append_transaction
                seen = set()
                importlib.machinery.SourceFileLoader.exec_module = exec_module
                try:
                    faulted = self.consume(seen)
                finally:
                    importlib.machinery.SourceFileLoader.exec_module = real_exec
                self.assertEqual(len(appends), fail_at)
                [row] = faulted
                refusal = row["result"]
                self.assertEqual(refusal["failure_code"], "FRAME_DISPATCH_WRITE_FAILED")
                self.assertEqual(refusal["failed_predicate"], "DISPATCH_WRITES_THIS_REPOSITORYS_STATE:"
                                 + ("LedgerWriteFault" if fault == "lost_comparison" else "LedgerStoreCollision"))
                self.assertIn("ledger_append_lost_comparison" if fault == "lost_comparison"
                              else "ledger_receipt_collision", refusal["detail"])
                self.assertIs(refusal["terminal"], False)
                for field in SIX_FIELDS:
                    self.assertTrue(refusal.get(field), field)
                self.assertEqual(seen, set())
                self.assertFalse((self.mesh / "quarantine.d" / self.name(good)).exists())
                [again] = self.consume(set())
                self.assertEqual(again["result"]["status"], "CONSUMED")
                operation = again["result"]["execution_result"]["application_result"]["receiving_operation_result"]
                self.assertEqual(operation["disposition"], "ALLOW", operation.get("detail"))
                receipts = [json.loads(p.read_text(encoding="utf-8")) for p in self.written()]
                ids = [r["transition_id"] for r in receipts
                       if "transition_id" in r and r["transition_id"].endswith("lost-cas-" + tag)]
                self.assertEqual(len(ids), 1)
                [repository] = [r for r in receipts if r.get("transition_id") in ids]
                bound = [r for r in receipts if r.get("repo_receipt_sha256") == repository["receipt_sha256"]]
                self.assertEqual(len(bound), 1, "the repository receipt is orphaned or bound twice")

    def test_a_content_collision_stays_a_recorded_refusal(self):
        """One packet id bound to two different diagnostics is the content's defect: a recorded FAIL_CLOSED,
        not a write fault, and the first receipt is untouched."""
        first = ingress._recorded("ALLOW", {"communication_id": "c-1", "body": 1}, "collide", 4096)
        before = sorted(str(p) for p in self.written())
        second = ingress._recorded("ALLOW", {"communication_id": "c-1", "body": 2}, "collide", 4096)
        self.assertEqual(first["disposition"], "ALLOW")
        # (b) six fields, named for the collision; terminal
        self.assertEqual(second["disposition"], "FAIL_CLOSED")
        self.assertEqual(second["failure_code"], "PACKET_ID_RECEIPT_COLLISION")
        self.assertEqual(second["failed_predicate"], "ONE_PACKET_ID_BINDS_ONE_DIAGNOSTIC")
        self.assertIs(second["terminal"], True)
        for field in SIX_FIELDS:
            self.assertTrue(second.get(field), field)
        # (a) refused before anything is written: no orphan
        self.assertEqual(sorted(str(p) for p in self.written()), before)
        # (c) a byte-identical replay is idempotent: the recorded receipt, not a refusal, and no write
        replay = ingress._recorded("ALLOW", {"communication_id": "c-1", "body": 1}, "collide", 4096)
        self.assertEqual(replay["disposition"], "ALLOW")
        self.assertIs(replay["transition_replayed"], True)
        self.assertNotIn("failure_code", replay)
        self.assertEqual(sorted(str(p) for p in self.written()), before)

    def test_every_ledger_store_write_fault_is_a_ledger_write_fault(self):
        import ast
        for relative in ("resident-runtime/ledger_store.py", ".stegverse/transition-ledger/emit.py",
                         "resident-runtime/aggregate_repo_transition.py"):
            tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
                    name = ast.unparse(node.exc.func)
                    self.assertNotEqual(name, "RuntimeError", relative + ":" + str(node.lineno))
                    if relative != ".stegverse/transition-ledger/emit.py" and node.exc.args:
                        # only emit's idempotent_on mismatch is a content collision; a store collision is ours
                        self.assertFalse(name == "ValueError" and ast.unparse(node.exc.args[0])
                                         == "'ledger_receipt_collision'", relative + ":" + str(node.lineno))

    def test_an_unrecoverable_frame_alone_writes_nothing_and_reads_no_clock(self):
        import datetime as real
        import sys
        import types
        calls = []

        class Raising(real.datetime):
            @classmethod
            def now(cls, *_a, **_k):
                calls.append("now")
                raise AssertionError("read the clock")
        shim = types.ModuleType("datetime")
        shim.__dict__.update({n: getattr(real, n) for n in dir(real) if not n.startswith("__")})
        shim.datetime = Raising
        cases = ((lambda f: f["heartbeat_reference"].__setitem__("generation", 12345), True, "HB_REFERENCE_INVALID"),
                 (lambda f: f.__setitem__("packet_sha256", "sha256:" + "0" * 64), True, "FRAME_INTEGRITY_INVALID"),
                 (lambda f: None, False, "FRAME_INTEGRITY_INVALID"))
        for index, (mutate, reseal, code) in enumerate(cases):
            with self.subTest(code=code, index=index):
                frame = self.frame("alone-%d" % index, epoch=4096)
                mutate(frame)
                frame["frame_sha256"] = (kernel.sha({k: v for k, v in frame.items() if k != "frame_sha256"})
                                         if reseal else "sha256:" + "e" * 64)
                sys.modules["datetime"] = shim
                try:
                    with self.assertRaises(ValueError) as refused:
                        kernel.ingest_frame(ROOT, frame, mesh_root=self.mesh)
                finally:
                    sys.modules["datetime"] = real
                refusal = refused.exception.refusal
                self.assertEqual(refusal["failure_code"], code)
                for field in SIX_FIELDS:
                    self.assertTrue(refusal.get(field), field)
        self.assertEqual(calls, [])
        self.assertEqual(self.written(), [])

    def test_the_refused_frame_sorting_first_does_not_block_the_batch(self):
        self.run_batch(refused_first=True)

    def test_the_refused_frame_sorting_second_is_quarantined(self):
        self.run_batch(refused_first=False)


class NoUnmarkedClockOnAnyRoute(Redirected):
    """B-P36g rows 4 and 6: a route that writes nothing reads no clock, and every clock value is marked.

    The kernel's clock is fixed (not raised) so that a route which does read it
    shows what it writes: a clock-derived reference must say derived_from_clock
    true and carry sampled_unix_ns. No ledger receipt is written without an epoch.
    """

    FIXED_NS = kernel.HB_ANCHOR_UNIX_NS + 11 * kernel.HB_PERIOD_NS

    def setUp(self):
        super().setUp()
        self.calls = []
        fixed, calls = self.FIXED_NS, self.calls

        class Fixed:
            @staticmethod
            def now(*_a, **_k):
                from datetime import datetime, timezone
                calls.append("now")
                return datetime.fromtimestamp(fixed / 1e9, timezone.utc)
        for module in (kernel, ingress.repository_ledger, ingress.organization_ledger,
                       egress, egress.repository_ledger, egress.organization_ledger):
            saved = module.kernel.datetime if hasattr(module, "kernel") else module.datetime
            target = module.kernel if hasattr(module, "kernel") else module
            target.datetime = Fixed
            self.addCleanup(setattr, target, "datetime", saved)

    def frame_to(self, destination_org, service, epoch=37):
        packet = kernel.build_packet(
            origin_org="StegVerse-org", origin_service="stegverse-org.intr-destination",
            destination_org=destination_org, destination_service=service,
            payload=diagnostic("route-" + service), standing=GENESIS,
            transition_reference="intr:transition:route", authority_effect="NONE",
            packet_id="route-" + service)
        return kernel.carrier_frame(packet, epoch=epoch)

    def test_routes_that_write_nothing_read_no_clock(self):
        ignored = kernel.ingest_frame(ROOT, self.frame_to("Some-Other-Org", "some-other-org.intr-destination"),
                                      mesh_root=self.mesh)
        self.assertEqual(ignored["status"], "IGNORED_NOT_ADDRESSED")
        with self.assertRaises(ValueError) as refused:
            kernel.ingest_frame(ROOT, self.frame_to("SV-011", "sv-011.boundary-diagnostic"), mesh_root=self.mesh)
        self.assertIn("endpoint_adapter_not_installed", str(refused.exception))
        for absent in (lambda: ingress.receive(diagnostic("r"), registry=REGISTRY, packet_id="r"),
                       lambda: egress.emit("StegVerse-org", diagnostic("e"), standing=GENESIS,
                                           capability="intr-destination", mesh_root=self.mesh)):
            with self.assertRaises(ValueError):
                absent()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.written(), [])

    def test_the_only_clock_value_is_a_marked_frame_reference(self):
        packet = kernel.build_packet(
            origin_org="SV-011", origin_service="sv-011.intr-destination",
            destination_org="StegVerse-org", destination_service="stegverse-org.intr-destination",
            payload=diagnostic("frame-only"), standing=GENESIS,
            transition_reference="intr:transition:frame-only", authority_effect="NONE", packet_id="frame-only")
        reference = kernel.publish_packet(packet, root=self.mesh)["frame"]["heartbeat_reference"]
        self.assertEqual(self.calls, ["now"])
        self.assertIs(reference["derived_from_clock"], True)
        self.assertLess(abs(reference["sampled_unix_ns"] - self.FIXED_NS), 1000)  # float clock sample
        # the frame-only default writes no receipt. Under narrow S1 its clock-flagged frame is consumed but
        # lends no epoch, so the epoch-needing writer refuses NOT_SUPPLIED before writing; an UNMARKED
        # copy of the same frame is still refused at ingest.
        self.assertEqual(self.written(), [])
        frame = self.frame_to("SV-011", ADDRESS)
        clocked = kernel.carrier_frame(kernel.recover_packet(frame), now_ns=self.FIXED_NS)
        with self.assertRaises(ValueError) as refused:
            kernel.ingest_frame(ROOT, clocked, mesh_root=self.mesh)
        self.assertEqual(refused.exception.refusal["failure_code"], "HB_EPOCH_NOT_SUPPLIED")
        self.assertEqual(self.written(), [])
        # bare-sampled (no flag, sampled_unix_ns): CLOCK at ingest (08:10Z), consumed with no epoch lent
        bare = json.loads(json.dumps(clocked))
        del bare["heartbeat_reference"]["derived_from_clock"]
        bare["frame_sha256"] = kernel.sha({k: v for k, v in bare.items() if k != "frame_sha256"})
        with self.assertRaises(ValueError) as refused:
            kernel.ingest_frame(ROOT, bare, mesh_root=self.mesh)
        self.assertEqual(refused.exception.refusal["failure_code"], "HB_EPOCH_NOT_SUPPLIED")
        self.assertEqual(self.written(), [])
        # UNMARKED (no flag and no sample) is still refused at ingest
        unmarked = json.loads(json.dumps(bare))
        del unmarked["heartbeat_reference"]["sampled_unix_ns"]
        unmarked["frame_sha256"] = kernel.sha({k: v for k, v in unmarked.items() if k != "frame_sha256"})
        with self.assertRaises(ValueError) as refused:
            kernel.ingest_frame(ROOT, unmarked, mesh_root=self.mesh)
        self.assertEqual(refused.exception.refusal["failure_code"], "HB_EPOCH_PROVENANCE_UNMARKED")
        self.assertIn("kernel.py::ingest_frame", refused.exception.refusal["retry_entrypoint"])
        self.assertEqual(self.written(), [])


class ManifestEpochBindings(unittest.TestCase):
    """B-P36n: every epoch-requiring entrypoint the manifest declares has a declared epoch binding,
    and every declared dispatcher actually passes it."""

    MANIFEST = ROOT / "org-runtime/interlock-intr.json"

    def operations(self):
        document = json.loads(self.MANIFEST.read_text(encoding="utf-8"))
        receiving = [entry["receiving_operation"] for entry in document["ingress"]["capability_endpoint_bindings"]]
        return receiving + [document["egress"]["emitting_operation"]]

    def test_every_epoch_requiring_entrypoint_is_bound(self):
        for operation in self.operations():
            with self.subTest(operation=operation["operation_id"]):
                binding = operation.get("hb_epoch_binding")
                self.assertIsInstance(binding, dict, "MANIFEST_ENTRYPOINT_LACKS_EPOCH_BINDING")
                self.assertIs(binding["required"], True)
                self.assertEqual(binding["rule"], "org-kernel/kernel.py::supplied_epoch_failure")
                self.assertTrue(binding["sources"])
                for source in binding["sources"]:
                    self.assertIn(source["from"], ("carrier_frame", "dispatch_input", "caller_argument"))

    def test_each_declared_dispatcher_passes_the_epoch(self):
        for operation in self.operations():
            for source in operation["hb_epoch_binding"]["sources"]:
                if source["from"] != "dispatch_input":
                    continue
                with self.subTest(dispatcher=source["dispatcher"]):
                    workflow = (ROOT / source["dispatcher"]).read_text(encoding="utf-8")
                    self.assertIn("      " + source["input"] + ":\n", workflow)
                    self.assertIn(source["env"] + ": ${{ inputs." + source["input"] + " }}", workflow)
                    self.assertIn("python3 " + source["entrypoint"], workflow)

    def test_each_caller_argument_is_a_real_parameter(self):
        import inspect
        for operation in self.operations():
            for source in operation["hb_epoch_binding"]["sources"]:
                if source["from"] != "caller_argument":
                    continue
                for entrypoint in source["entrypoints"]:
                    path, name = entrypoint.split("::")
                    module = _module("manifest_" + name, path)
                    with self.subTest(entrypoint=entrypoint):
                        self.assertIn(source["parameter"], inspect.signature(getattr(module, name)).parameters)

    def dispatches(self):
        for operation in self.operations():
            binding = operation["hb_epoch_binding"]
            if "dispatch" in binding:
                yield operation["operation_id"], binding["dispatch"], binding
            for source in binding["sources"]:
                if "dispatch" in source:
                    yield operation["operation_id"], source["dispatch"], source

    def non_test_sources(self):
        import subprocess
        names = subprocess.run(["git", "-C", str(ROOT), "ls-files", "*.py", "*.yml", "*.yaml"],
                               capture_output=True, text=True, check=True).stdout.split()
        return {name: (ROOT / name).read_text(encoding="utf-8") for name in names if not name.startswith("tests/")}

    def test_every_epoch_source_has_an_in_tree_dispatcher_or_is_a_library_api(self):
        """B-P36n (08:32Z): no null dispatcher. A declared dispatcher is a workflow_dispatch in the tree;
        a LIBRARY_API is not an entrypoint and nothing outside tests calls it."""
        operations = {operation["operation_id"]: operation for operation in self.operations()}
        for operation_id, operation in operations.items():
            binding = operation["hb_epoch_binding"]
            self.assertNotIn("in_tree_dispatcher", binding)
            for source in binding["sources"]:
                self.assertTrue("dispatch" in source or "dispatch" in binding, operation_id)
        sources = self.non_test_sources()
        for operation_id, dispatch, owner in self.dispatches():
            with self.subTest(operation=operation_id, kind=dispatch["kind"]):
                self.assertIn(dispatch["kind"], ("IN_TREE_DISPATCHER", "LIBRARY_API"))
                if dispatch["kind"] == "IN_TREE_DISPATCHER":
                    self.assertIs(dispatch["entrypoint"], True)
                    workflow = (ROOT / owner["dispatcher"]).read_text(encoding="utf-8")
                    self.assertIn("\non:\n  " + dispatch["trigger"] + ":", workflow)
                    continue
                self.assertIs(dispatch["entrypoint"], False)
                self.assertEqual(dispatch["in_tree_callers"], [])
                # 08:45Z: a library API without an in-tree dispatcher carries the explicit pending finding
                self.assertEqual(dispatch["dispatcher"], {"status": "PENDING_IN_TREE", "pending": "SV-011#11"})
                self.assertTrue(dispatch["dispatcher_pending"].startswith("SV-011/.github#11 "))
                api = dispatch["api"]
                module_path, _, function = api.partition("::")
                callers = []
                for name, text in sources.items():
                    if name == module_path:
                        continue
                    if name.endswith((".yml", ".yaml")):
                        invoked = "python3 " + module_path in text  # a paths: filter is not a caller
                    elif function:
                        invoked = function + "(" in text
                    else:
                        invoked = '"' + module_path + '"' in text or "import " + Path(module_path).stem in text
                    if invoked:
                        callers.append(name)
                self.assertEqual(callers, [], api + " is called in tree; declare that caller as its dispatcher")

    def test_each_carrier_frame_source_is_the_ingest_path(self):
        for operation in self.operations():
            for source in operation["hb_epoch_binding"]["sources"]:
                if source["from"] == "carrier_frame":
                    self.assertEqual(source["via"], "org-kernel/kernel.py::ingest_frame")
                    self.assertTrue(callable(kernel.ingest_frame))


if __name__ == "__main__":
    unittest.main()
