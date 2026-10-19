"""A receipt writer references the heartbeat epoch its caller supplies.

Neither ledger writer derives an epoch from a clock. An absent or invalid
``hb_epoch`` is FAIL_CLOSED with six fields before the store is opened, and
nothing is written. A frame consumed at the capability address hands its
carrier epoch to both writers. Both ledger roots and the mesh are redirected.
"""
from __future__ import annotations

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
        with self.assertRaises(ValueError) as refused:
            self.consume()
        self.assertSixFieldRefusal(refused, "HB_EPOCH_INVALID", "kernel.py::ingest_frame")
        self.assertIn("derived_from_clock", refused.exception.refusal["detail"])
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

    def test_a_carrier_cannot_self_assert_carried_provenance(self):
        self.no_clock(ingress.repository_ledger, ingress.organization_ledger)
        for marker, code in (({}, "HB_EPOCH_PROVENANCE_UNMARKED"),
                             ({"sampled_unix_ns": kernel.HB_ANCHOR_UNIX_NS}, "HB_EPOCH_INVALID"),
                             ({"sampled_unix_ns": kernel.HB_ANCHOR_UNIX_NS, "derived_from_clock": False},
                              "HB_EPOCH_INVALID"),
                             ({"derived_from_clock": False}, "CARRIED_PROVENANCE_UNVERIFIED")):
            with self.subTest(marker=marker), self.assertRaises(ValueError) as refused:
                kernel.ingest_frame(ROOT, self.fabricated_carried_frame(**marker), mesh_root=self.mesh)
            self.assertSixFieldRefusal(refused, code, "kernel.py::ingest_frame")
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
        # the frame-only default writes no receipt, and its clock frame is refused at ingest
        self.assertEqual(self.written(), [])
        frame = self.frame_to("SV-011", ADDRESS)
        clocked = kernel.carrier_frame(kernel.recover_packet(frame), now_ns=self.FIXED_NS)
        with self.assertRaises(ValueError) as refused:
            kernel.ingest_frame(ROOT, clocked, mesh_root=self.mesh)
        self.assertEqual(refused.exception.refusal["failure_code"], "HB_EPOCH_INVALID")
        self.assertEqual(self.written(), [])


if __name__ == "__main__":
    unittest.main()
