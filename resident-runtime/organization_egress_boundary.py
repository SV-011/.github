#!/usr/bin/env python3
"""Emit and close an outbound organization crossing.

A peer capability address is derived: ``organization_slug(peer) + "." + profile_id``.
The federation directory names peer organizations and their control services.
It does not list capability services. Resolution does not show that the peer
installed the receiving operation. An absent answer stays ``PENDING`` and is
not a transition.

Closure, when a far-side terminal receipt recomputes, shows that a boundary
ran this packet. What that closure establishes is the responded request class
and the ``communication_id``. It does not show which organization that boundary
belongs to, and it does not show that the far side persisted the chain.

Authority and time are constraint variables. A disposition is transition
evidence. Ledger roots are caller-supplied. These receipts are not authentic
production receipts.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]

OPERATION_ID = "ORGANIZATION_INTER_ORG_EGRESS"
OWNER_REPOSITORY = "SV-011/.github"
RESULT_SCHEMA = "stegverse.organization-egress-result/v1"
EMISSION_SCHEMA = "stegverse.organization-egress-emission-record/v1"
CLOSURE_SCHEMA = "stegverse.organization-egress-closure-record/v1"
TRANSPORT_PROFILE = "stegverse.intr.org-boundary.v1"
BOUNDARY_RELATIVE = "org-runtime/interlock-intr.json"
CAPABILITY_ADDRESS_FORM = "ORGANIZATION_SLUG_DOT_CAPABILITY_PROFILE_ID"
ADDRESSED_SERVICE_FIELD = "addressed_service"
DESTINATION_BINDINGS_FIELD = "capability_destination_bindings"

INTENDED_ACTION_EMIT = "CROSS_AN_ORGANIZATION_BOUNDARY_OUTBOUND"
INTENDED_ACTION_CLOSE = "OBSERVE_THE_FAR_SIDE_CLOSURE_OF_AN_OUTBOUND_CROSSING"

EMITTED_CLASS = "ORGANIZATION_EGRESS_EMITTED"
EMIT_REFUSED_CLASS = "ORGANIZATION_EGRESS_REFUSED"
CLOSED_CLASS = "ORGANIZATION_EGRESS_CLOSED"
CLOSURE_REFUSED_CLASS = "ORGANIZATION_EGRESS_CLOSURE_REFUSED"

ALLOW = "ALLOW"
DENY = "DENY"
PENDING = "PENDING"
FAIL_CLOSED = "FAIL_CLOSED"

#: An absent response is the protocol's receiver-unavailable disposition
#: (org-contract `propagation_receiver_unavailable_disposition`), returned with
#: the six fields and recorded nowhere -- never a bare in-flight state. Ported
#: from StegVerse-org/.github#186; `PENDING` stays defined for the other
#: callers in this module, and `close` no longer returns it.
RESPONSE_ABSENT_FAILURE_CODE = "DURABLE_QUEUE_OR_EVENT_EPHEMERAL_MATERIALIZATION"
RESPONSE_ABSENT_PREDICATE = "EGRESS_RESPONSE_DURABLY_MATERIALIZED_BEFORE_CLOSE"

#: The shared supplied-epoch rule (consumers Q3; P1 #163; TVC#517). A closure
#: writes both ledger levels, so it takes the epoch it is given and never reads
#: a clock: none supplied is HB_EPOCH_NOT_SUPPLIED, anything other than ASCII
#: digits at or above the HB32 anchor is HB_EPOCH_INVALID. Both fail closed
#: with nothing appended.
HB_EPOCH_NOT_SUPPLIED = "HB_EPOCH_NOT_SUPPLIED"
HB_EPOCH_INVALID = "HB_EPOCH_INVALID"
HB_EPOCH_PREDICATE = "SUBMISSION_CARRIES_THE_SUPPLIED_HB_EPOCH"
HB_ANCHOR_EPOCH = 32
INTR_PROTOCOL_GOAL = "INTERLOCK-INTR-RUNTIME-INDEPENDENT-PROTOCOL-001"

#: The declared order of `close`'s refusals. Input refusals take precedence over
#: state refusals, and within inputs the supplied-epoch check comes first: a
#: call missing both an epoch and a response is HB_EPOCH_NOT_SUPPLIED, not the
#: response absence. No refusal on this list appends anything.
CLOSE_REFUSAL_PRECEDENCE = (
    (HB_EPOCH_PREDICATE, "INPUT"),
    ("DESTINATION_IS_A_DECLARED_PEER_OF_THIS_ORGANIZATION", "INPUT"),
    (RESPONSE_ABSENT_PREDICATE, "STATE"),
)

ACK_CLASSES = frozenset({"ecosystem.monitor.response", "ecosystem.work.ack",
                         "ecosystem.communication.ack"})

FAR_SIDE_RECEIPT_KINDS = ("INGRESS_ACCEPTED", "DISPATCHED", "CONSUMED",
                          "RESULT_BOUND", "EGRESS_EMITTED")


def _module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


kernel = _module("org_kernel", "org-kernel/kernel.py")
repository_ledger = _module("repo_transition_emit", ".stegverse/transition-ledger/emit.py")
organization_ledger = _module("aggregate_repo_transition",
                              "resident-runtime/aggregate_repo_transition.py")
ledger_store = _module("ledger_store", "resident-runtime/ledger_store.py")


class EgressRefused(Exception):
    def __init__(self, failed_predicate: str, reason: str) -> None:
        super().__init__(reason)
        self.failed_predicate = failed_predicate
        self.reason = reason


def canon(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def sha(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canon(value)).hexdigest()


def this_organization(root: Path | None = None) -> str:
    return kernel.load_registry(root or ROOT)["organization"]


def declared_capabilities(root: Path | None = None) -> dict[str, dict[str, Any]]:
    boundary = json.loads((Path(root or ROOT) / BOUNDARY_RELATIVE).read_text(encoding="utf-8"))
    declared = (boundary.get("ingress") or {}).get("capability_endpoint_bindings") or []
    return {entry["profile_id"]: entry for entry in declared
            if isinstance(entry, dict) and entry.get("profile_id")}


def destination_bindings(root: Path | None = None) -> dict[str, dict[str, Any]]:
    boundary = json.loads((Path(root or ROOT) / BOUNDARY_RELATIVE).read_text(encoding="utf-8"))
    declared = (boundary.get("egress") or {}).get(DESTINATION_BINDINGS_FIELD) or []
    return {entry["profile_id"]: entry for entry in declared
            if isinstance(entry, dict) and entry.get("profile_id")}


def peer_capability_service(organization: str, capability: str) -> str:
    """Derive ``slug.profile_id``. The directory does not list this service."""
    return kernel.organization_slug(organization) + "." + capability


def resolve_destination(organization: str, *, capability: str | None = None,
                        root: Path | None = None) -> dict[str, Any]:
    """Resolve a peer from this organization's directory.

    With no capability, the destination is the peer's control service or the
    addressed service the directory already names. With a capability, the
    destination is derived and ``peer_serves_this_capability_is_proven_here``
    stays false.
    """
    directory = kernel.load_federation_directory(root or ROOT)
    outbound = destination_bindings(root) if capability is not None else {}
    if (capability is not None and capability not in outbound
            and capability not in declared_capabilities(root)):
        raise EgressRefused(
            "CAPABILITY_IS_DECLARED_IN_THIS_ORGANIZATIONS_OVERLAY",
            "no capability_endpoint_binding declares profile_id " + str(capability))
    bound_to = (outbound.get(capability) or {}).get("destination_organization")
    if bound_to is not None and bound_to != organization:
        raise EgressRefused(
            "CAPABILITY_IS_SENT_TO_THE_ORGANIZATION_THAT_OWNS_IT",
            "capability " + str(capability) + " is bound to " + str(bound_to)
            + ", not " + str(organization))
    for row in directory.get("organizations", []):
        if row.get("organization") != organization:
            continue
        profile = row.get("transport_profile")
        if profile != TRANSPORT_PROFILE:
            raise EgressRefused(
                "DESTINATION_SPEAKS_THIS_BOUNDARY_TRANSPORT_PROFILE",
                "peer declares transport_profile " + str(profile)
                + "; this boundary speaks " + TRANSPORT_PROFILE)
        control = row.get("org_control_service")
        addressed = row.get(ADDRESSED_SERVICE_FIELD) or control
        service = addressed if capability is None else peer_capability_service(organization, capability)
        if not service:
            raise EgressRefused(
                "DESTINATION_DECLARES_A_SERVICE_THIS_DIRECTORY_CAN_ADDRESS",
                "peer declares neither " + ADDRESSED_SERVICE_FIELD
                + " nor org_control_service: " + str(organization))
        return {
            "destination_organization": organization,
            "destination_repository": row.get("repository"),
            "destination_org_control_service": control,
            "destination_addressed_service": addressed,
            "destination_addressed_service_role": row.get("addressed_service_role"),
            "destination_serves_an_organization_control_service": bool(control),
            "destination_kernel_required": row.get("kernel_required"),
            "transport_profile": profile,
            "destination_resolution_source": "ORGANIZATION_FEDERATION_DIRECTORY",
            "destination_resolved_from_caller_argument": False,
            "destination_capability_profile_id": capability,
            "destination_service": service,
            "destination_capability_address_form": CAPABILITY_ADDRESS_FORM,
            "destination_capability_declared_by_the_peer_directory": False,
            "peer_serves_this_capability_is_proven_here": False,
            "unserved_capability_is_observable_as_an_unclosed_crossing": True,
            "destination_kernel_generation_is_proven_here": False,
            "destination_kernel_required_is_checked_at_resolution": False,
            "authority_constraint": "NONE_RESOLUTION_ONLY",
            "time_constraint": "HEARTBEAT_EPOCH",
        }
    raise EgressRefused(
        "DESTINATION_IS_A_DECLARED_PEER_OF_THIS_ORGANIZATION",
        "organization not in org-boundary/registry/federation.json: " + str(organization))


def closability_refusal(payload: Mapping[str, Any]) -> tuple[str, str] | None:
    """Why this payload could never close, or None when it can.

    Closure correlates a responded request class and a communication id. Both
    are readable in this payload. A peer's installation of the capability is
    not readable here.
    """
    declared = payload.get("message_class")
    if declared not in kernel.RESPONDED_REQUEST_CLASSES:
        return ("PAYLOAD_DECLARES_A_REQUEST_CLASS_THE_BOUNDARY_ANSWERS",
                "message_class " + repr(declared) + " is answered by no "
                "acknowledgement; the profile answers "
                + ", ".join(sorted(kernel.RESPONDED_REQUEST_CLASSES)))
    identifier = payload.get("communication_id")
    if not isinstance(identifier, str) or not identifier.strip():
        return ("PAYLOAD_CARRIES_THE_COMMUNICATION_ID_CLOSURE_CORRELATES_ON",
                "closure collects a response by communication_id and this "
                "payload declares " + repr(identifier))
    return None


def _constraint_fields() -> dict[str, Any]:
    return {
        "origin_attestation_state": "NOT_PROVEN",
        "origin_is_attested_by_the_credential_authority": False,
        "authority_constraint": "NONE_RECORD_ONLY",
        "time_constraint": "HEARTBEAT_EPOCH",
        "disposition_is_transition_evidence": True,
        "production_receipt_claimed": False,
    }


def emission_record(resolved: Mapping[str, Any], origin: str, packet: Mapping[str, Any],
                    frame: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": EMISSION_SCHEMA,
        "operation": OPERATION_ID,
        "owner_repository": OWNER_REPOSITORY,
        "intended_action": INTENDED_ACTION_EMIT,
        "disposition": ALLOW,
        "transition_is_the_disposition_of_the_intended_action": True,
        "origin_organization": origin,
        **dict(resolved),
        "packet_id": packet["packet_id"],
        "frame_sha256": frame.get("frame_sha256"),
        "payload_sha256": sha(dict(payload)),
        "far_side_payload_hash": kernel.sha(dict(payload)),
        "far_side_service_id": resolved["destination_service"],
        "transition_reference": (packet.get("transition") or {}).get("reference"),
        "crossed_an_organization_boundary": True,
        "interlock_intr_involved": True,
        **_constraint_fields(),
    }


def emit_refusal_record(failed_predicate: str, reason: str, organization: Any,
                        origin: str, payload: Any) -> dict[str, Any]:
    return {
        "schema": EMISSION_SCHEMA,
        "operation": OPERATION_ID,
        "owner_repository": OWNER_REPOSITORY,
        "intended_action": INTENDED_ACTION_EMIT,
        "disposition": DENY,
        "transition_is_the_disposition_of_the_intended_action": True,
        "origin_organization": origin,
        "requested_destination_organization": organization if isinstance(organization, str) else None,
        "failed_predicate": failed_predicate,
        "refusal_reason": reason,
        "refusal_is_verbatim": True,
        "payload_sha256": sha(dict(payload) if isinstance(payload, Mapping) else payload),
        "packet_emitted": False,
        "crossed_an_organization_boundary": False,
        "disposition_is_transition_evidence": True,
        "authority_constraint": "NONE_RECORD_ONLY",
        "time_constraint": "HEARTBEAT_EPOCH",
        "production_receipt_claimed": False,
    }


def closure_record(resolved: Mapping[str, Any], origin: str, packet_id: str,
                   communication_id: str, response: Mapping[str, Any],
                   findings: list[str], recomputed: list[str]) -> dict[str, Any]:
    verified = not findings
    return {
        "schema": CLOSURE_SCHEMA,
        "operation": OPERATION_ID,
        "owner_repository": OWNER_REPOSITORY,
        "intended_action": INTENDED_ACTION_CLOSE,
        "disposition": ALLOW if verified else DENY,
        "transition_is_the_disposition_of_the_intended_action": True,
        "origin_organization": origin,
        "destination_organization": resolved["destination_organization"],
        "request_packet_id": packet_id,
        "communication_id": communication_id,
        "responding_organization": response.get("organization"),
        "response_packet_id": response.get("response_packet_id"),
        "response_message_class": response.get("message_class"),
        "responded_request_class": response.get("message_class"),
        "response_frame_sha256": response.get("frame_sha256"),
        "far_side_terminal_receipt": response.get("receipt_terminal"),
        "far_side_terminal_receipt_recomputed": recomputed[-1] if recomputed else None,
        "far_side_receipt_chain_recomputed": recomputed,
        "far_side_receipt_reconstructed_here": True,
        "far_side_chain_recomputed_from_emitter_held_inputs": True,
        "serve_proven_only_at_closure": True,
        "peer_serves_this_capability_is_proven_here": False,
        "reconstruction_proves_the_boundary_ran_this_packet": True,
        "reconstruction_proves_who_the_far_side_is": False,
        "reconstruction_proves_the_far_side_persisted_its_chain": False,
        "closure_findings": findings,
        "crossed_an_organization_boundary": True,
        "interlock_intr_involved": True,
        **_constraint_fields(),
    }


def _record(transition_id: str, transition_class: str, predecessor: str, successor: str,
            record: Mapping[str, Any], hb_epoch: int | None) -> dict[str, Any]:
    idempotent = hb_epoch is not None
    repository_receipt = repository_ledger.append(
        transition_id, transition_class, predecessor, successor,
        dict(record), "NONE", hb_epoch=hb_epoch,
        idempotent_on=("frame_sha256", "payload_sha256") if idempotent else None)
    organization_receipt = organization_ledger.append(
        repository_receipt, "REPO_STATE_PROPAGATION", predecessor, successor,
        {"operation": OPERATION_ID,
         "intended_action": record["intended_action"],
         "disposition": record["disposition"],
         "crossed_an_organization_boundary": record["crossed_an_organization_boundary"]},
        "NONE", hb_epoch=repository_receipt["hb_reference"]["epoch"] if idempotent else hb_epoch,
        idempotent=idempotent)
    return {"repository_receipt": repository_receipt,
            "organization_receipt": organization_receipt}


def _refusal_result(record: Mapping[str, Any], appended: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": RESULT_SCHEMA,
        "operation": OPERATION_ID,
        "disposition": DENY,
        "disposition_is_transition_evidence": True,
        "emitted": False,
        "failure_code": EMIT_REFUSED_CLASS,
        "failed_predicate": record["failed_predicate"],
        "detail": record["refusal_reason"],
        "refusal_recorded": True,
        "emission_transition_class": EMIT_REFUSED_CLASS,
        "emission_record": dict(record),
        "emission_repository_receipt_sha256": appended["repository_receipt"]["receipt_sha256"],
        "emission_organization_receipt_sha256": appended["organization_receipt"]["receipt_sha256"],
        "authority_constraint": "NONE_RECORD_ONLY",
        "time_constraint": "HEARTBEAT_EPOCH",
        "production_receipt_claimed": False,
    }


def emit(destination_organization: Any, payload: Mapping[str, Any], *,
         standing: Mapping[str, Any], capability: str | None = None,
         transition_reference: str = "ecosystem.transition.interorg.v1",
         root: Path | None = None, mesh_root: Path | None = None,
         hb_epoch: int | None = None) -> dict[str, Any]:
    """Emit an outbound crossing to a declared peer and record the disposition."""
    origin = this_organization(root)
    try:
        unclosable = closability_refusal(payload or {})
        if unclosable is not None:
            raise EgressRefused(*unclosable)
        resolved = resolve_destination(destination_organization, capability=capability, root=root)
        packet = kernel.build_packet(
            origin_org=origin,
            origin_service=peer_capability_service(origin, capability) if capability
            else kernel.organization_slug(origin) + ".org-control",
            destination_org=resolved["destination_organization"],
            destination_service=resolved["destination_service"],
            payload=dict(payload), standing=dict(standing),
            transition_reference=transition_reference, authority_effect="NONE")
        published = kernel.publish_packet(packet, root=mesh_root, epoch=hb_epoch)
    except EgressRefused as exc:
        record = emit_refusal_record(exc.failed_predicate, exc.reason,
                                     destination_organization, origin, payload)
        appended = _record(EMIT_REFUSED_CLASS + ":" + sha(record)[7:23], EMIT_REFUSED_CLASS,
                           record["payload_sha256"], sha(record), record, hb_epoch)
        return _refusal_result(record, appended)
    except (ValueError, KeyError, TypeError) as exc:
        record = emit_refusal_record("PACKET_IS_BUILDABLE_AND_PUBLISHABLE_AS_DECLARED",
                                     str(exc), destination_organization, origin, payload)
        appended = _record(EMIT_REFUSED_CLASS + ":" + sha(record)[7:23], EMIT_REFUSED_CLASS,
                           record["payload_sha256"], sha(record), record, hb_epoch)
        return _refusal_result(record, appended)

    record = emission_record(resolved, origin, packet, published["frame"], payload)
    appended = _record(EMITTED_CLASS + ":" + packet["packet_id"], EMITTED_CLASS,
                       record["payload_sha256"], sha(record), record, hb_epoch)
    return {
        "schema": RESULT_SCHEMA,
        "operation": OPERATION_ID,
        "disposition": ALLOW,
        "disposition_is_transition_evidence": True,
        "emitted": True,
        "packet_id": packet["packet_id"],
        "communication_id": (payload or {}).get("communication_id"),
        **{key: record[key] for key in (
            "destination_organization", "destination_repository",
            "destination_org_control_service", "destination_service",
            "destination_capability_profile_id",
            "peer_serves_this_capability_is_proven_here",
            "transport_profile", "destination_resolution_source", "frame_sha256",
            "crossed_an_organization_boundary",
            "origin_attestation_state",
            "origin_is_attested_by_the_credential_authority")},
        "emission_record": record,
        "emission_transition_class": EMITTED_CLASS,
        "emission_repository_receipt_sha256": appended["repository_receipt"]["receipt_sha256"],
        "emission_organization_receipt_sha256": appended["organization_receipt"]["receipt_sha256"],
        "authority_constraint": "NONE_RECORD_ONLY",
        "time_constraint": "HEARTBEAT_EPOCH",
        "production_receipt_claimed": False,
    }


def reconstruct_far_side_chain(packet_id: str, service_id: str, payload_hash: str) -> list[str]:
    chain: list[str] = []
    previous = None
    for kind in FAR_SIDE_RECEIPT_KINDS:
        item = kernel.receipt(kind, packet_id, service_id, previous, {"payload_hash": payload_hash})
        chain.append(item["receipt_id"])
        previous = item["receipt_id"]
    return chain


def recorded_emission(packet_id: str) -> dict[str, Any] | None:
    store = ledger_store.open_store(repository_ledger.lr())
    head = store.get(ledger_store.HEAD_KEY)
    cursor = (head or {}).get("receipt_sha256") if isinstance(head, dict) else None
    wanted = EMITTED_CLASS + ":" + packet_id
    while cursor:
        receipt = store.get(ledger_store.receipt_key(cursor))
        if receipt is None:
            return None
        if (receipt.get("transition_class") == EMITTED_CLASS
                and receipt.get("transition_id") == wanted):
            evidence = receipt.get("evidence")
            return dict(evidence) if isinstance(evidence, Mapping) else None
        cursor = receipt.get("previous_receipt_sha256")
    return None


def verify_closure(resolved: Mapping[str, Any], packet_id: str, response: Mapping[str, Any],
                   emission: Mapping[str, Any] | None = None) -> list[str]:
    findings = []
    if response.get("request_packet_id") != packet_id:
        findings.append("RESPONSE_ANSWERS_A_DIFFERENT_PACKET:" + str(response.get("request_packet_id")))
    if response.get("organization") != resolved["destination_organization"]:
        findings.append("RESPONSE_CAME_FROM_A_DIFFERENT_ORGANIZATION:" + str(response.get("organization")))
    if response.get("message_class") not in ACK_CLASSES:
        findings.append("RESPONSE_CARRIES_NO_ACKNOWLEDGEMENT_CLASS:" + str(response.get("message_class")))
    terminal = response.get("receipt_terminal")
    if not isinstance(terminal, str) or not terminal.strip():
        findings.append("RESPONSE_CARRIES_NO_FAR_SIDE_TERMINAL_RECEIPT")
        return findings
    if emission is None:
        findings.append("THIS_ORGANIZATION_HAS_NO_EMISSION_RECORD_FOR_THIS_PACKET")
        return findings
    recomputed = reconstruct_far_side_chain(
        packet_id, emission["far_side_service_id"], emission["far_side_payload_hash"])
    if terminal != recomputed[-1]:
        findings.append("FAR_SIDE_TERMINAL_RECEIPT_DOES_NOT_RECOMPUTE:" + terminal)
    return findings


def supplied_epoch_failure(hb_epoch: Any, reference: Any = None) -> tuple[str, str] | None:
    """The shared supplied-epoch rule (SUBMISSION_CARRIES_THE_SUPPLIED_HB_EPOCH).

    Ported from StegVerse-org/.github#186 (the same rule as org#185 and
    StegVerse-Labs/.github#3212 and #3213). `hb_epoch` absent (None) is
    HB_EPOCH_NOT_SUPPLIED. Otherwise it must be ASCII [0-9]+ only -- an int is
    read as its decimal text; a bool, a float or anything coerced ('+33',
    ' 33', '3_3', '33.0') is refused -- with integer value >= 32 (the HB32
    anchor). `reference`, the carrier frame's heartbeat reference when there is
    one, must be an object, and a `derived_from_clock` it carries must be
    exactly false. Any failure is HB_EPOCH_INVALID. Returns (failure_code,
    detail), or None when the epoch is supplied. No clock is read.
    """
    if reference is not None and not isinstance(reference, dict):
        return "HB_EPOCH_INVALID", "the carrier's heartbeat reference is not an object"
    if hb_epoch is None:
        return "HB_EPOCH_NOT_SUPPLIED", "no hb_epoch was supplied and no clock is read"
    if isinstance(hb_epoch, bool):
        text = None
    elif isinstance(hb_epoch, int):
        text = str(hb_epoch)
    elif isinstance(hb_epoch, str):
        text = hb_epoch
    else:
        text = None
    if text is None or not text.isascii() or not text.isdigit():
        return "HB_EPOCH_INVALID", "hb_epoch " + repr(hb_epoch) + " is not ASCII [0-9]+"
    if int(text) < 32:
        return "HB_EPOCH_INVALID", "hb_epoch " + text + " is below the HB32 anchor (32)"
    if reference is not None and "derived_from_clock" in reference \
            and reference["derived_from_clock"] is not False:
        return "HB_EPOCH_INVALID", ("the carrier's derived_from_clock is not exactly false; "
                                    "a clock-derived epoch is not a supplied one")
    return None


def epoch_refusal(hb_epoch: Any, entrypoint: str) -> dict[str, Any] | None:
    """The six-field FAIL_CLOSED refusal of an epoch not supplied or invalid, or None."""
    failure = supplied_epoch_failure(hb_epoch)
    if failure is None:
        return None
    code, detail = failure
    return {
        "schema": RESULT_SCHEMA,
        "operation": OPERATION_ID,
        "disposition": FAIL_CLOSED,
        "emitted": False,
        "failure_code": code,
        "failed_predicate": HB_EPOCH_PREDICATE,
        "detail": detail,
        "required_evidence_or_repair": (
            "supply hb_epoch from the carrier as ASCII digits with value >= " + str(HB_ANCHOR_EPOCH)
            + "; it is never derived from a clock"),
        "retry_entrypoint": entrypoint,
        "owning_existing_goal": INTR_PROTOCOL_GOAL,
        "next_attempt": "call " + entrypoint + " again with the supplied epoch; nothing was "
                        "published and nothing was appended",
        "refusal_recorded": False,
        "authority_constraint": "NONE_REFUSAL_ONLY",
        "production_receipt_claimed": False,
    }


def _recorded_attestation(emission: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The attestation this organization already recorded for this packet.

    Origin attestation is not installed on this boundary, so every emission
    record here carries `origin_attestation_state` NOT_PROVEN and this returns
    None: the closure reports an unattested origin exactly as the emission did.
    Nothing is reconstructed on the closure's behalf. Ported from
    StegVerse-org/.github@0f9481af, reading only this organization's own
    emission record.
    """
    if not emission or emission.get("origin_attestation_state") != "PROVEN":
        return None
    carried = {field: value for field, value in emission.items()
               if field.startswith(("origin_attestation", "attested_", "signature_"))}
    return {**carried,
            "attestation_read_from_this_organizations_own_emission_record": True}


def close(destination_organization: str, packet_id: str, communication_id: str, *,
          root: Path | None = None, mesh_root: Path | None = None,
          hb_epoch: int | None = None) -> dict[str, Any]:
    """Observe the far side's closure of an emitted crossing, and record it.

    Fails closed with DURABLE_QUEUE_OR_EVENT_EPHEMERAL_MATERIALIZATION and the
    six fields, and records nothing, when no response is in the mesh. An absence
    is not an arrival: nothing crossed the boundary, so there is no disposition
    to record, and appending a refusal per attempt would record the attempts
    rather than the crossing. The refusal says what a later attempt needs
    instead of leaving the caller to wait.

    Refusals follow `CLOSE_REFUSAL_PRECEDENCE`: the supplied epoch first, then
    the destination, then the response absence. Function-level port of
    StegVerse-org/.github#186.
    """
    # Input refusals precede state refusals; within inputs the epoch is first.
    refused_epoch = epoch_refusal(hb_epoch, "resident-runtime/organization_egress_boundary.py::close")
    if refused_epoch is not None:
        return {**{k: v for k, v in refused_epoch.items() if k != "emitted"},
                "closed": False, "closure_recorded": False,
                "communication_id": communication_id, "request_packet_id": packet_id}
    hb_epoch = int(str(hb_epoch))  # admitted as ASCII digits
    origin = this_organization(root)
    resolved = resolve_destination(destination_organization, root=root)
    collected = kernel.collect_ecosystem_responses(origin, communication_id, mesh_root=mesh_root)
    responses = [row for row in collected.get("organizations", [])
                 if row.get("organization") == resolved["destination_organization"]]
    if not responses:
        return {
            "schema": RESULT_SCHEMA,
            "operation": OPERATION_ID,
            "disposition": FAIL_CLOSED,
            "failure_code": RESPONSE_ABSENT_FAILURE_CODE,
            "failed_predicate": RESPONSE_ABSENT_PREDICATE,
            "detail": ("no response from " + str(resolved["destination_organization"])
                       + " for communication_id " + str(communication_id)
                       + " is materialized in the mesh"),
            "required_evidence_or_repair": (
                "a response frame from " + str(resolved["destination_organization"])
                + " correlated by communication_id " + str(communication_id)
                + ", durably materialized in the mesh (durable queue or event-ephemeral "
                "materialization) before close reads it; no caller waits for it"),
            "retry_entrypoint": "resident-runtime/organization_egress_boundary.py::close",
            "owning_existing_goal": INTR_PROTOCOL_GOAL,
            "next_attempt": ("call resident-runtime/organization_egress_boundary.py::close "
                             "again with the same packet_id, communication_id and a "
                             "supplied epoch once the response is materialized; nothing "
                             "was appended at either ledger level"),
            "refusal_recorded": False,
            "disposition_is_transition_evidence": True,
            "closed": False,
            "closure_recorded": False,
            "absence_is_not_a_transition": True,
            "communication_id": communication_id,
            "request_packet_id": packet_id,
            "destination_organization": resolved["destination_organization"],
            "authority_constraint": "NONE_REFUSAL_ONLY",
            "time_constraint": "HEARTBEAT_EPOCH",
            "production_receipt_claimed": False,
        }
    response = responses[0]
    emission = recorded_emission(packet_id)
    findings = verify_closure(resolved, packet_id, response, emission)
    recomputed = reconstruct_far_side_chain(
        packet_id, emission["far_side_service_id"], emission["far_side_payload_hash"]
    ) if emission else []
    record = closure_record(resolved, origin, packet_id, communication_id, response, findings, recomputed)
    transition_class = CLOSED_CLASS if not findings else CLOSURE_REFUSED_CLASS
    appended = _record(transition_class + ":" + packet_id, transition_class,
                       sha({"packet_id": packet_id, "communication_id": communication_id}),
                       sha(record), record, hb_epoch)
    return {
        "schema": RESULT_SCHEMA,
        "operation": OPERATION_ID,
        "disposition": record["disposition"],
        "disposition_is_transition_evidence": True,
        "closed": not findings,
        "closure_recorded": True,
        "closure_findings": findings,
        "communication_id": communication_id,
        "request_packet_id": packet_id,
        "destination_organization": resolved["destination_organization"],
        "responding_organization": response.get("organization"),
        "responded_request_class": response.get("message_class"),
        "far_side_terminal_receipt": response.get("receipt_terminal"),
        "far_side_receipt_reconstructed_here": True,
        "far_side_terminal_receipt_recomputed": recomputed[-1] if recomputed else None,
        "far_side_application_result": response.get("application_result"),
        "closure_record": record,
        "recorded_origin_attestation": _recorded_attestation(emission),
        "closure_transition_class": transition_class,
        "closure_repository_receipt_sha256": appended["repository_receipt"]["receipt_sha256"],
        "closure_organization_receipt_sha256": appended["organization_receipt"]["receipt_sha256"],
        "authority_constraint": "NONE_RECORD_ONLY",
        "time_constraint": "HEARTBEAT_EPOCH",
        "serve_proven_only_at_closure": True,
        "production_receipt_claimed": False,
    }
