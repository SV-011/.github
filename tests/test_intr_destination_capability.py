"""``intr-destination`` is a universal ingress capability, not a purpose route.

Every organization can advertise it. A peer address is derived as
``organization_slug + "." + "intr-destination"``. Peer capability services are
not written into ``federation.json``. Purpose-route bindings are not added.

Resolution does not prove a peer serves the capability. Closure, when a
fabricated far side answers, shows a responded request class and the
``communication_id``. It does not show which organization ran the boundary.

Authority and time are constraint variables. A disposition is transition
evidence. Both ledger roots and the mesh are redirected. Nothing here is an
authentic production receipt or a live peer installation.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PROFILE_ID = "intr-destination"
PROFILE_NAME = "InTr:Destination"
OPERATION = "DIAGNOSTIC_CROSSING"
OPERATION_ID = "ORGANIZATION_INTR_DESTINATION_INGRESS"
ADDRESS = "sv-011." + PROFILE_ID
PAYLOAD_SCHEMA = "stegverse.org-intr-destination-diagnostic/v1"
PEER = "StegVerse-org"
THIS = "SV-011"

BYTE_COPIES = {
    "org-boundary/runtime/intr_transport.py":
        "0f48bb46617aeba98b750eecd406d9ab157eb9e473aae583105dafd52ed7205c",
    "org-boundary/runtime/process_boundary.py":
        "a5af99cbf166422e1c40c72eaafb64342c731398a6d434a055ea485aa3782d0e",
    "resident-runtime/org_boundary_runtime.py":
        "1fedfdf46e854935e60260dab839ebd17d23df538ddfd1d0948828e1a978fa0b",
}


def _module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


kernel = _module("kernel", "org-kernel/kernel.py")
capability = _module("capability_ingress", "org-boundary/runtime/capability_ingress.py")
egress = _module("organization_egress_boundary",
                 "resident-runtime/organization_egress_boundary.py")
ingress = _module("intr_destination_ingress", "resident-runtime/intr_destination_ingress.py")
runtime_boundary = _module("runtime_boundary", "org-runtime/runtime_boundary.py")

REGISTRY = json.loads((ROOT / "org-boundary/registry/services.json").read_text(encoding="utf-8"))
BOUNDARY = json.loads((ROOT / "org-runtime/interlock-intr.json").read_text(encoding="utf-8"))
FEDERATION = json.loads((ROOT / "org-boundary/registry/federation.json").read_text(encoding="utf-8"))
GENESIS = {"mode": "ESTABLISH_GENESIS", "node_ref": "intr-destination-test", "predecessor": None}


def binding(profile_id, document=None):
    matches = [entry for entry in (document or BOUNDARY)["ingress"]["capability_endpoint_bindings"]
               if entry.get("profile_id") == profile_id]
    if len(matches) != 1:
        raise AssertionError(profile_id)
    return matches[0]


def row(service_id):
    return next(item for item in REGISTRY["services"] if item["service_id"] == service_id)


def diagnostic(communication_id="intr-destination-diagnostic-001", schema=PAYLOAD_SCHEMA):
    return {
        "message_class": "ecosystem.communication",
        "communication_id": communication_id,
        "subject": "purpose-neutral mesh diagnostic",
        "body": {"schema": schema, "probe": "diagnostic-crossing"},
    }


class DeclarationTests(unittest.TestCase):
    def test_canonical_boundary_byte_copies_are_unchanged(self):
        for relative, digest in BYTE_COPIES.items():
            observed = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            self.assertEqual(observed, digest, relative)

    def test_the_overlay_advertises_the_capability_and_selects_nothing(self):
        declared = binding(PROFILE_ID)
        self.assertEqual(declared["profile_name"], PROFILE_NAME)
        self.assertEqual(declared["operation"], OPERATION)
        self.assertEqual(declared["payload_schema"], PAYLOAD_SCHEMA)
        self.assertEqual(declared["submission_reader"], capability.COMMUNICATION_READER)
        self.assertEqual(declared["authority_constraint"], "NONE_BINDING_ONLY")
        self.assertEqual(declared["time_constraint"], "HEARTBEAT_EPOCH")
        self.assertEqual(declared["disposition_role"], "TRANSITION_EVIDENCE_ONLY")
        for flag in ("selects_routing", "selects_admission", "selects_execution",
                     "environment_selected_ingress"):
            self.assertIs(declared[flag], False, flag)
        receiving = declared["receiving_operation"]
        self.assertEqual(receiving["operation_id"], OPERATION_ID)
        self.assertEqual(receiving["addressed_service"], ADDRESS)
        self.assertEqual(receiving["addressed_service_boundary_role"], capability.ROLE)
        self.assertIs(receiving["selects_processing_at_the_capability_address"], False)
        self.assertIs(receiving["host_required"], False)
        self.assertIs(receiving["environment_url_required"], False)
        self.assertTrue((ROOT / receiving["operation"]).is_file())

    def test_the_registry_row_is_an_address_and_not_an_admission_constraint(self):
        served = row(ADDRESS)
        self.assertEqual(served["boundary_role"], "BOUNDARY_LOCAL_CAPABILITY_INGRESS")
        self.assertEqual(served["capability_profile_id"], PROFILE_ID)
        self.assertIs(served["address_constrains_admission"], False)
        self.assertIs(served["selects_processing_at_this_address"], False)
        self.assertNotIn("endpoint_adapter", served)
        self.assertIn("ecosystem.communication", served["accepts"])
        self.assertIn(PAYLOAD_SCHEMA, served["accepts"])

    def test_the_one_served_address_is_named_and_no_peer_address_is(self):
        proven = BOUNDARY["egress"]["peer_capability_resolution"]["proven_on_this_organization"]
        served = {entry["receiving_operation"]["addressed_service"]
                  for entry in BOUNDARY["ingress"]["capability_endpoint_bindings"]}
        self.assertEqual(proven, ADDRESS)
        self.assertEqual(served, {ADDRESS})
        self.assertNotIn("stegverse-org.intr-destination", str(proven))
        resolution = BOUNDARY["egress"]["peer_capability_resolution"]
        self.assertIs(resolution["serve_proven_only_at_closure"], True)
        self.assertEqual(resolution["closure_establishes"],
                         ["responded_request_class", "communication_id"])
        report = runtime_boundary.validate()
        self.assertIs(report["valid"], True, report["checks"])
        self.assertIs(report["checks"]["capability_addresses_resolve"], True)
        self.assertIs(report["checks"]["peer_capability_addresses_derive"], True)

    def test_a_single_served_address_may_be_named_as_a_string_or_a_one_item_list(self):
        self.assertIs(runtime_boundary.peer_capability_addresses_derive(BOUNDARY), True)
        document = json.loads(json.dumps(BOUNDARY))
        document["egress"]["peer_capability_resolution"]["proven_on_this_organization"] = [ADDRESS]
        self.assertIs(runtime_boundary.peer_capability_addresses_derive(document), True)

    def test_naming_only_one_of_two_served_addresses_is_refused(self):
        document = json.loads(json.dumps(BOUNDARY))
        extra = json.loads(json.dumps(binding(PROFILE_ID)))
        extra["profile_id"] = "other-capability"
        extra["receiving_operation"]["addressed_service"] = "sv-011.other-capability"
        document["ingress"]["capability_endpoint_bindings"].append(extra)
        document["egress"]["peer_capability_resolution"]["proven_on_this_organization"] = ADDRESS
        self.assertIs(runtime_boundary.peer_capability_addresses_derive(document), False)

    def test_a_peer_address_in_the_proven_field_is_refused(self):
        document = json.loads(json.dumps(BOUNDARY))
        document["egress"]["peer_capability_resolution"]["proven_on_this_organization"] = [
            ADDRESS, "stegverse-org.intr-destination"]
        self.assertIs(runtime_boundary.peer_capability_addresses_derive(document), False)

    def test_a_diagnostic_operation_cannot_inherit_the_manifest_reader(self):
        document = json.loads(json.dumps(BOUNDARY))
        document["ingress"]["capability_endpoint_bindings"][0]["submission_reader"] = "MANIFEST"
        self.assertIs(runtime_boundary.capability_addresses_resolve(document), False)

    def test_peer_capability_services_are_not_written_into_the_federation_directory(self):
        self.assertIs(FEDERATION["capability_services_enumerated"], False)
        for entry in FEDERATION["organizations"]:
            for key in ("org_control_service", "addressed_service"):
                self.assertNotIn(PROFILE_ID, str(entry.get(key) or ""))

    def test_purpose_route_bindings_are_not_added(self):
        self.assertNotIn("capability_destination_bindings", BOUNDARY["egress"])

    def test_boundary_documents_do_not_use_a_transfer_verb(self):
        needle = "g" + "rant"
        for relative in ("org-runtime/interlock-intr.json", "org-boundary/registry/services.json",
                         "org-boundary/registry/federation.json"):
            self.assertNotIn(needle, (ROOT / relative).read_text(encoding="utf-8").lower(), relative)


class OverlayAuthorityTests(unittest.TestCase):
    def test_resolution_names_the_overlay_and_the_bound_operation(self):
        resolved = capability.resolve(ROOT, row(ADDRESS))
        self.assertEqual(resolved["resolution_source"], "ORGANIZATION_CAPABILITY_OVERLAY")
        self.assertIs(resolved["resolved_from_the_addressed_row_alone"], False)
        self.assertEqual(resolved["receiving_operation_id"], OPERATION_ID)
        self.assertEqual(resolved["capability_operation"], OPERATION)
        self.assertEqual(resolved["authority_constraint"], "NONE_RESOLUTION_ONLY")
        self.assertEqual(ingress.OPERATION_ID, OPERATION_ID)

    def test_a_row_the_overlay_does_not_name_resolves_nothing(self):
        with self.assertRaises(SystemExit) as refused:
            capability.resolve(ROOT, {"service_id": "sv-011.not-bound-anywhere"})
        self.assertIn("ADDRESS_SERVES_A_CAPABILITY_THE_OVERLAY_BINDS_HERE", str(refused.exception))

    def test_a_module_that_is_not_the_bound_operation_is_refused(self):
        resolved = dict(capability.resolve(ROOT, row(ADDRESS)))
        resolved["receiving_operation_path"] = ROOT / "resident-runtime/organization_egress_boundary.py"
        with self.assertRaises(SystemExit) as refused:
            capability.load_operation(resolved)
        self.assertIn("MODULE_IDENTIFIES_ITSELF_AS_THE_BOUND_OPERATION", str(refused.exception))

    def test_an_operation_outside_this_organization_is_refused(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        (root / "org-runtime").mkdir(parents=True)
        document = json.loads(json.dumps(BOUNDARY))
        receiving = binding(PROFILE_ID, document)["receiving_operation"]
        receiving["operation"] = "../outside.py"
        (root / "org-runtime/interlock-intr.json").write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaises(SystemExit) as refused:
            capability.resolve(root, row(ADDRESS))
        self.assertIn("RECEIVING_OPERATION_IS_OWNED_INSIDE_THIS_ORGANIZATION", str(refused.exception))

    def test_the_readers_do_not_cross(self):
        manifest_binding = {"operation": "SUBMIT_MANIFEST"}
        with self.assertRaises(SystemExit) as manifest_refused:
            capability.submission_for(manifest_binding, diagnostic())
        self.assertIn("SUBMISSION_CARRIES_A_MANIFEST", str(manifest_refused.exception))
        with self.assertRaises(SystemExit) as diagnostic_refused:
            capability.submission_for(binding(PROFILE_ID), {"manifest": {"probe": True}})
        self.assertIn("SUBMISSION_DECLARES_A_RESPONDED_REQUEST_CLASS",
                      str(diagnostic_refused.exception))

    def test_a_diagnostic_body_under_another_schema_is_refused(self):
        with self.assertRaises(SystemExit) as refused:
            capability.submission_for(
                binding(PROFILE_ID), diagnostic(schema="stegverse.org-not-a-destination-diagnostic/v1"))
        self.assertIn("SUBMISSION_BODY_DECLARES_THE_BOUND_PAYLOAD_SCHEMA", str(refused.exception))


class RedirectedLedgers(unittest.TestCase):
    LEDGER_ROOTS = ("STEGVERSE_REPO_LEDGER_ROOT", "STEGVERSE_ORG_LEDGER_ROOT")

    def setUp(self):
        self._work = tempfile.TemporaryDirectory()
        self.addCleanup(self._work.cleanup)
        previous = {name: os.environ.get(name) for name in self.LEDGER_ROOTS}
        for name in self.LEDGER_ROOTS:
            os.environ[name] = str(Path(self._work.name) / name.lower())
        self.mesh = Path(self._work.name) / "mesh"

        def restore():
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        self.addCleanup(restore)

    def receipts(self, variable, schema, transition_class=None):
        found = []
        root = Path(os.environ[variable])
        if not root.exists():
            return found
        for path in root.rglob("*.json"):
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("schema") != schema:
                continue
            if transition_class is not None and record.get("transition_class") != transition_class:
                continue
            found.append(record)
        return found


class OperationReceiptTests(RedirectedLedgers):
    def test_a_diagnostic_is_acknowledged_at_both_ledger_levels(self):
        result = ingress.receive(diagnostic(), registry=REGISTRY, packet_id="direct-diagnostic",
                                 hb_epoch=32)
        self.assertEqual(result["disposition"], "ALLOW", result.get("detail"))
        self.assertIs(result["disposition_is_transition_evidence"], True)
        self.assertIs(result["received"], True)
        self.assertIs(result["communication_acknowledged"], True)
        self.assertIs(result["repository_receipt_observed"], True)
        self.assertIs(result["organization_receipt_observed"], True)
        self.assertIs(result["organization_receipt_preserves_repository_receipt"], True)
        self.assertEqual(result["authority_constraint"], "NONE_DIAGNOSTIC_ACK_ONLY")
        self.assertEqual(result["time_constraint"], "HEARTBEAT_EPOCH")
        self.assertIs(result["production_receipt_claimed"], False)
        for flag in ("selects_routing", "selects_admission", "selects_execution",
                     "selects_processing", "purpose_specific_route", "standing_rechecked_here"):
            self.assertIs(result[flag], False, flag)
        repository, = self.receipts("STEGVERSE_REPO_LEDGER_ROOT",
                                    "stegverse.repo-transition-receipt/v1", OPERATION_ID)
        self.assertEqual(repository["evidence"]["profile_id"], PROFILE_ID)
        self.assertEqual(repository["evidence"]["receiving_operation"], OPERATION_ID)
        self.assertIs(repository["production_receipt_claimed"], False)
        organization, = self.receipts("STEGVERSE_ORG_LEDGER_ROOT",
                                      "stegverse.organization-transition-receipt/v1")
        self.assertEqual(organization["repo_receipt_sha256"], repository["receipt_sha256"])

    def test_a_replay_of_the_same_diagnostic_does_not_mint_a_second_pair(self):
        first = ingress.receive(diagnostic(), registry=REGISTRY, packet_id="replayed-diagnostic",
                                hb_epoch=32)
        second = ingress.receive(diagnostic(), registry=REGISTRY, packet_id="replayed-diagnostic",
                                 hb_epoch=32)
        self.assertIs(second["transition_replayed"], True)
        self.assertEqual(first["repository_receipt_sha256"], second["repository_receipt_sha256"])
        self.assertEqual(len(self.receipts("STEGVERSE_REPO_LEDGER_ROOT",
                                           "stegverse.repo-transition-receipt/v1", OPERATION_ID)), 1)

    def test_a_payload_that_is_not_the_diagnostic_is_refused_and_recorded(self):
        result = ingress.receive(
            {"message_class": "ecosystem.work.request", "communication_id": "not-a-diagnostic",
             "body": {"schema": "stegverse.org-not-a-destination-diagnostic/v1"}},
            registry=REGISTRY, packet_id="refused-diagnostic", hb_epoch=32)
        self.assertEqual(result["disposition"], "DENY")
        self.assertIs(result["communication_acknowledged"], False)
        self.assertIs(result["refusal_recorded"], True)
        self.assertEqual(result["failed_predicate"], "SUBMISSION_DECLARES_A_RESPONDED_REQUEST_CLASS")
        self.assertEqual(result["authority_constraint"], "NONE_REFUSAL_ONLY")
        self.assertEqual(len(self.receipts(
            "STEGVERSE_REPO_LEDGER_ROOT", "stegverse.repo-transition-receipt/v1",
            ingress.REFUSED_CLASS)), 1)
        self.assertEqual(self.receipts(
            "STEGVERSE_REPO_LEDGER_ROOT", "stegverse.repo-transition-receipt/v1", OPERATION_ID), [])


class AddressedAndClosedTests(RedirectedLedgers):
    def publish(self, payload, service, packet_id, origin=PEER):
        packet = kernel.build_packet(
            origin_org=origin,
            origin_service=kernel.organization_slug(origin) + "." + PROFILE_ID,
            destination_org=THIS, destination_service=service,
            payload=payload, standing=GENESIS,
            transition_reference="intr:transition:" + packet_id,
            authority_effect="NONE", packet_id=packet_id)
        kernel.publish_packet(packet, root=self.mesh, epoch=32)
        return packet

    def consume(self):
        return kernel.consume_addressed_frames(
            ROOT, mesh_root=self.mesh,
            repo_ledger_root=os.environ["STEGVERSE_REPO_LEDGER_ROOT"],
            org_ledger_root=os.environ["STEGVERSE_ORG_LEDGER_ROOT"])

    def frames_to(self, organization):
        directory = self.mesh / "frames.d"
        if not directory.is_dir():
            return []
        frames = []
        for path in directory.glob("*.json"):
            frame = json.loads(path.read_text(encoding="utf-8"))
            if frame.get("destination_org") == organization:
                frames.append(frame)
        return frames

    def test_the_mesh_locator_fails_closed_without_a_supplied_root(self):
        with self.assertRaises(ValueError) as refused:
            kernel.resolve_federation_root(None)
        self.assertIn("mesh_location_required_from_materializer", str(refused.exception))
        info = kernel.node_state_provenance(self.mesh)
        self.assertEqual(info["mesh_provenance"], "SUPPLIED")
        self.assertTrue(info["mesh_locator"])

    def test_a_peer_frame_at_the_address_is_received_and_selects_nothing(self):
        self.publish(diagnostic("addressed-diagnostic"), ADDRESS, "addressed-diagnostic")
        results = self.consume()
        self.assertEqual(len(results), 1)
        execution = results[0]["result"]["execution_result"]
        self.assertEqual(results[0]["result"]["status"], "CONSUMED")
        self.assertEqual(execution["service_id"], ADDRESS)
        self.assertEqual(execution["processing_selection"], "BOUNDARY_LOCAL_NO_PROCESSOR_SELECTED")
        application = execution["application_result"]
        self.assertIs(application["capability_received"], True)
        self.assertEqual(application["capability_profile_id"], PROFILE_ID)
        self.assertEqual(application["receiving_operation_id"], OPERATION_ID)
        self.assertIs(application["declared_capability_processed_at_this_address"], False)
        self.assertIs(application["address_constrains_admission"], False)
        operation = application["receiving_operation_result"]
        self.assertEqual(operation["disposition"], "ALLOW", operation.get("detail"))
        self.assertIs(operation["disposition_is_transition_evidence"], True)
        self.assertIs(operation["communication_acknowledged"], True)
        self.assertEqual(operation["authority_constraint"], "NONE_DIAGNOSTIC_ACK_ONLY")
        self.assertIs(operation["organization_receipt_preserves_repository_receipt"], True)
        self.assertIs(results[0]["organization_record"], None)
        repository, = self.receipts("STEGVERSE_REPO_LEDGER_ROOT",
                                    "stegverse.repo-transition-receipt/v1", OPERATION_ID)
        matched = [record for record in self.receipts(
            "STEGVERSE_ORG_LEDGER_ROOT", "stegverse.organization-transition-receipt/v1")
            if record.get("repo_receipt_sha256") == repository["receipt_sha256"]]
        self.assertEqual(len(matched), 1)

    def test_the_phase5_diagnostic_role_is_not_this_capability(self):
        self.publish(diagnostic("phase5-role"), "sv-011.boundary-diagnostic", "phase5-role")
        with self.assertRaises(ValueError) as refused:
            self.consume()
        self.assertIn("endpoint_adapter_not_installed", str(refused.exception))

    def test_a_body_under_another_schema_is_refused_at_the_address(self):
        self.publish(diagnostic("other-schema", schema="stegverse.org-not-a-destination-diagnostic/v1"),
                     ADDRESS, "other-schema")
        with self.assertRaises(ValueError) as refused:
            self.consume()
        self.assertIn("SUBMISSION_BODY_DECLARES_THE_BOUND_PAYLOAD_SCHEMA", str(refused.exception))

    def test_outbound_resolution_derives_the_peer_address_and_does_not_prove_it_serves(self):
        resolved = egress.resolve_destination(PEER, capability=PROFILE_ID)
        self.assertEqual(resolved["destination_service"],
                         kernel.organization_slug(PEER) + "." + PROFILE_ID)
        self.assertNotEqual(resolved["destination_service"],
                            resolved["destination_org_control_service"])
        self.assertIs(resolved["peer_serves_this_capability_is_proven_here"], False)
        self.assertIs(resolved["destination_capability_declared_by_the_peer_directory"], False)
        self.assertEqual(egress.peer_capability_service(THIS, PROFILE_ID), ADDRESS)

    def test_an_undeclared_capability_is_still_refused(self):
        with self.assertRaises(egress.EgressRefused) as refused:
            egress.resolve_destination(PEER, capability="not-a-declared-capability")
        self.assertEqual(refused.exception.failed_predicate,
                         "CAPABILITY_IS_DECLARED_IN_THIS_ORGANIZATIONS_OVERLAY")

    def test_an_undeclared_peer_is_still_refused(self):
        with self.assertRaises(egress.EgressRefused) as refused:
            egress.resolve_destination("Not-A-Declared-Peer", capability=PROFILE_ID)
        self.assertEqual(refused.exception.failed_predicate,
                         "DESTINATION_IS_A_DECLARED_PEER_OF_THIS_ORGANIZATION")

    def answer(self, packet):
        """Answer as a boundary does: the kernel's own receipt chain and control response."""
        chain, previous = [], None
        for kind in egress.FAR_SIDE_RECEIPT_KINDS:
            item = kernel.receipt(kind, packet["packet_id"], packet["destination"]["service"],
                                  previous, {"payload_hash": kernel.sha(packet["payload"])})
            chain.append(item["receipt_id"])
            previous = item["receipt_id"]
        response = kernel.build_control_response(
            packet, {"application_result": {
                "schema": ingress.RESULT_SCHEMA, "disposition": "ALLOW",
                "authority_constraint": "NONE_DIAGNOSTIC_ACK_ONLY"},
                "reconstruction": {"terminal_receipt_id": chain[-1]}})
        kernel.publish_packet(response, root=self.mesh, epoch=40)
        return chain[-1]

    def test_a_diagnostic_crossing_closes_only_when_the_far_side_answers(self):
        emitted = egress.emit(PEER, diagnostic("closable-diagnostic"), standing=GENESIS,
                              capability=PROFILE_ID, mesh_root=self.mesh, hb_epoch=32)
        self.assertEqual(emitted["disposition"], "ALLOW", emitted.get("detail"))
        self.assertIs(emitted["emitted"], True)
        self.assertEqual(emitted["destination_service"], "stegverse-org.intr-destination")
        self.assertIs(emitted["peer_serves_this_capability_is_proven_here"], False)
        self.assertEqual(emitted["emission_record"]["far_side_service_id"],
                         emitted["destination_service"])
        pending = egress.close(PEER, emitted["packet_id"], "closable-diagnostic",
                               mesh_root=self.mesh, hb_epoch=33)
        self.assertEqual(pending["disposition"], "FAIL_CLOSED")
        self.assertEqual(pending["failure_code"],
                         "DURABLE_QUEUE_OR_EVENT_EPHEMERAL_MATERIALIZATION")
        self.assertIs(pending["closed"], False)
        self.assertIs(pending["closure_recorded"], False)
        self.assertIs(pending["absence_is_not_a_transition"], True)
        self.assertEqual(self.receipts("STEGVERSE_REPO_LEDGER_ROOT",
                                       "stegverse.repo-transition-receipt/v1",
                                       egress.CLOSED_CLASS), [])

        frame, = self.frames_to(PEER)
        packet = kernel.recover_packet(frame)
        self.assertEqual(packet["destination"]["service"], "stegverse-org.intr-destination")
        self.assertEqual(packet["payload"]["message_class"], "ecosystem.communication")
        terminal = self.answer(packet)
        closed = egress.close(PEER, emitted["packet_id"], "closable-diagnostic",
                              mesh_root=self.mesh, hb_epoch=34)
        self.assertEqual(closed["disposition"], "ALLOW", closed.get("closure_findings"))
        self.assertIs(closed["closed"], True)
        self.assertEqual(closed["far_side_terminal_receipt"], terminal)
        self.assertEqual(closed["responded_request_class"], "ecosystem.communication.ack")
        self.assertEqual(closed["communication_id"], "closable-diagnostic")
        self.assertIs(closed["closure_record"]["serve_proven_only_at_closure"], True)
        self.assertEqual(closed["closure_record"]["responded_request_class"],
                         "ecosystem.communication.ack")
        self.assertEqual(closed["closure_record"]["communication_id"], "closable-diagnostic")
        self.assertIs(closed["closure_record"]["reconstruction_proves_the_boundary_ran_this_packet"], True)
        self.assertIs(closed["closure_record"]["reconstruction_proves_who_the_far_side_is"], False)
        self.assertIs(closed["closure_record"]["peer_serves_this_capability_is_proven_here"], False)
        self.assertIs(emitted["peer_serves_this_capability_is_proven_here"], False)
        self.assertEqual(len(self.receipts("STEGVERSE_REPO_LEDGER_ROOT",
                                           "stegverse.repo-transition-receipt/v1",
                                           egress.CLOSED_CLASS)), 1)
        self.assertTrue(self.receipts("STEGVERSE_ORG_LEDGER_ROOT",
                                      "stegverse.organization-transition-receipt/v1"))


class CloseResponseAbsentFailsClosed(unittest.TestCase):
    """`close` with no response in the mesh fails closed with the six fields.

    Function-level port of StegVerse-org/.github#186's
    tests/test_egress_close_response_absent_fail_closed.py onto this
    organization's module. An absent response is the protocol's
    receiver-unavailable disposition, DURABLE_QUEUE_OR_EVENT_EPHEMERAL_MATERIALIZATION,
    returned FAIL_CLOSED with the six fields; nothing is written at either
    ledger level. The supplied epoch is checked first. Both ledger roots and the
    mesh are temporary; a passing test is not an authentic receipt.
    """
    SIX_FIELDS = ("failure_code", "failed_predicate", "required_evidence_or_repair",
                  "retry_entrypoint", "owning_existing_goal", "next_attempt")
    ENTRYPOINT = "resident-runtime/organization_egress_boundary.py::close"
    LEDGER_ENV = ("STEGVERSE_REPO_LEDGER_ROOT", "STEGVERSE_ORG_LEDGER_ROOT")

    def setUp(self):
        self._work = tempfile.TemporaryDirectory()
        self.addCleanup(self._work.cleanup)
        work = Path(self._work.name)
        self.mesh = work / "mesh"
        self.mesh.mkdir()
        self.repo_ledger = work / "repo-ledger"
        self.org_ledger = work / "org-ledger"
        previous = {name: os.environ.get(name) for name in self.LEDGER_ENV}
        os.environ["STEGVERSE_REPO_LEDGER_ROOT"] = str(self.repo_ledger)
        os.environ["STEGVERSE_ORG_LEDGER_ROOT"] = str(self.org_ledger)

        def restore():
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        self.addCleanup(restore)

    @staticmethod
    def _files(root):
        if not root.exists():
            return []
        return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())

    def close(self, hb_epoch="40"):
        return egress.close(PEER, "packet-response-absent", "comm-response-absent",
                            mesh_root=self.mesh, hb_epoch=hb_epoch)

    def assert_six_fields(self, result):
        self.assertEqual(result["disposition"], "FAIL_CLOSED")
        for field in self.SIX_FIELDS:
            self.assertIsInstance(result.get(field), str, field)
            self.assertTrue(result[field].strip(), field)
        self.assertEqual(result["retry_entrypoint"], self.ENTRYPOINT)
        self.assertEqual(result["owning_existing_goal"],
                         "INTERLOCK-INTR-RUNTIME-INDEPENDENT-PROTOCOL-001")
        self.assertEqual(result["owning_existing_goal"], egress.INTR_PROTOCOL_GOAL)
        self.assertIs(result["closed"], False)
        self.assertIs(result["closure_recorded"], False)
        self.assertIs(result["refusal_recorded"], False)
        self.assertEqual(result["authority_constraint"], "NONE_REFUSAL_ONLY")
        self.assertEqual(result["request_packet_id"], "packet-response-absent")
        self.assertEqual(result["communication_id"], "comm-response-absent")

    def assert_response_absent_refusal(self, result):
        self.assert_six_fields(result)
        self.assertNotEqual(result["disposition"], "PENDING")
        self.assertEqual(result["failure_code"], egress.RESPONSE_ABSENT_FAILURE_CODE)
        self.assertEqual(result["failure_code"],
                         "DURABLE_QUEUE_OR_EVENT_EPHEMERAL_MATERIALIZATION")
        self.assertEqual(result["failed_predicate"],
                         "EGRESS_RESPONSE_DURABLY_MATERIALIZED_BEFORE_CLOSE")
        self.assertIn("comm-response-absent", result["required_evidence_or_repair"])
        self.assertIn(self.ENTRYPOINT, result["next_attempt"])
        self.assertIs(result["absence_is_not_a_transition"], True)
        self.assertEqual(result["destination_organization"], PEER)
        self.assertNotIn("closure_repository_receipt_sha256", result)
        self.assertNotIn("closure_organization_receipt_sha256", result)

    def assert_epoch_refusal(self, result, failure_code):
        self.assert_six_fields(result)
        self.assertEqual(result["failure_code"], failure_code)
        self.assertEqual(result["failed_predicate"], "SUBMISSION_CARRIES_THE_SUPPLIED_HB_EPOCH")

    def assert_nothing_appended(self):
        self.assertEqual(self._files(self.repo_ledger), [])
        self.assertEqual(self._files(self.org_ledger), [])

    def test_no_response_fails_closed_with_the_six_fields(self):
        self.assert_response_absent_refusal(self.close())
        self.assert_nothing_appended()

    def test_the_refusal_is_the_same_on_every_attempt_and_records_nothing(self):
        first, second = self.close("40"), self.close("41")
        self.assertEqual(first, second)
        self.assert_response_absent_refusal(second)
        self.assert_nothing_appended()

    def test_no_epoch_and_no_response_is_hb_epoch_not_supplied(self):
        self.assert_epoch_refusal(self.close(None), "HB_EPOCH_NOT_SUPPLIED")
        self.assert_nothing_appended()

    def test_an_invalid_epoch_and_no_response_is_hb_epoch_invalid(self):
        for invalid in ("31", "+33", " 33", "3_3", "33.0", True, 31):
            with self.subTest(hb_epoch=invalid):
                self.assert_epoch_refusal(self.close(invalid), "HB_EPOCH_INVALID")
        self.assert_nothing_appended()

    def test_a_supplied_epoch_and_no_response_is_the_response_absence(self):
        for supplied in ("32", "0033", 40):
            with self.subTest(hb_epoch=supplied):
                self.assert_response_absent_refusal(self.close(supplied))
        self.assert_nothing_appended()

    def test_the_epoch_is_checked_before_the_destination_or_the_mesh_is_read(self):
        from unittest import mock
        with mock.patch.object(egress, "resolve_destination",
                               side_effect=AssertionError("destination read")), \
                mock.patch.object(egress.kernel, "collect_ecosystem_responses",
                                  side_effect=AssertionError("mesh read")):
            self.assert_epoch_refusal(self.close(None), "HB_EPOCH_NOT_SUPPLIED")
        self.assert_nothing_appended()

    def test_the_declared_precedence_puts_inputs_before_state_and_the_epoch_first(self):
        order = egress.CLOSE_REFUSAL_PRECEDENCE
        self.assertEqual(order[0], ("SUBMISSION_CARRIES_THE_SUPPLIED_HB_EPOCH", "INPUT"))
        kinds = [kind for _, kind in order]
        self.assertEqual(kinds, sorted(kinds, key=lambda kind: kind != "INPUT"))
        self.assertIn(("EGRESS_RESPONSE_DURABLY_MATERIALIZED_BEFORE_CLOSE", "STATE"), order)

    def test_the_owning_organization_is_unchanged(self):
        self.assertEqual(egress.OWNER_REPOSITORY, "SV-011/.github")

    def test_an_unattested_emission_reports_no_recorded_attestation(self):
        self.assertIsNone(egress._recorded_attestation(None))
        self.assertIsNone(egress._recorded_attestation({"origin_attestation_state": "NOT_PROVEN"}))


if __name__ == "__main__":
    unittest.main()
