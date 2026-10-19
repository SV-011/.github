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
    """An organization receipt references the supplied epoch, not one read back from a receipt.

    A repository receipt is seeded with a clock-derived reference (as one written
    before this rule may carry) and no organization receipt. The replay supplies
    4096: the organization receipt must reference 4096 with derived_from_clock
    false, never the seeded clock epoch relabelled as supplied.
    """

    FIXED_NS = kernel.HB_ANCHOR_UNIX_NS + 7 * kernel.HB_PERIOD_NS

    def seed_clock_repository_receipt(self, writer, organization_ledger, call):
        honest = writer.kernel.supplied_hb_reference
        writer.kernel.supplied_hb_reference = lambda *_a, **_k: writer.kernel.hb_reference(self.FIXED_NS)
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

    def assertNothingLaundered(self):
        for receipt in self.receipts():
            reference = receipt.get("hb_reference")
            if reference is None:
                continue
            if reference["derived_from_clock"] is False:
                self.assertEqual(reference["epoch"], 4096, receipt.get("schema"))
        organization = [r for r in self.receipts()
                        if r.get("schema") == "stegverse.organization-transition-receipt/v1"]
        self.assertEqual(len(organization), 1)
        self.assertEqual(organization[0]["hb_reference"]["epoch"], 4096)
        self.assertIs(organization[0]["hb_reference"]["derived_from_clock"], False)

    def test_ingress_replay_does_not_forward_a_read_back_clock_epoch(self):
        call = lambda: ingress.receive(diagnostic("laundered"), registry=REGISTRY,
                                       packet_id="laundered", hb_epoch="4096")
        self.seed_clock_repository_receipt(ingress.repository_ledger, ingress.organization_ledger, call)
        seeded, = [r for r in self.receipts() if "hb_reference" in r]
        self.assertIs(seeded["hb_reference"]["derived_from_clock"], True)
        result = call()
        self.assertEqual(result["disposition"], "ALLOW", result.get("detail"))
        self.assertNothingLaundered()

    def test_egress_record_replay_does_not_forward_a_read_back_clock_epoch(self):
        record = {"intended_action": "EMIT", "disposition": "ALLOW", "crossed_an_organization_boundary": True,
                  "frame_sha256": "sha256:" + "1" * 64, "payload_sha256": "sha256:" + "2" * 64}
        call = lambda: egress._record("EGRESS:laundered", egress.EMITTED_CLASS, record["payload_sha256"],
                                      kernel.sha(record), record, 4096)
        self.seed_clock_repository_receipt(egress.repository_ledger, egress.organization_ledger, call)
        call()
        self.assertNothingLaundered()

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


if __name__ == "__main__":
    unittest.main()
