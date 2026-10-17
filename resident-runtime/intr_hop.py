#!/usr/bin/env python3
"""One-way InTr hop to a peer's ``<slug>.intr-destination``, in three phases.

A hop crosses from this organization (the sender) to a destination
organization, and back as an acknowledgement:

``send``
    Resolve the destination from the destination's own advertisement
    (``org-runtime/interlock-intr.json`` plus its service row), build the
    ``ecosystem.communication`` diagnostic, and record
    ``ORGANIZATION_EGRESS_EMITTED`` ("sent") in this organization's repository
    and organization ledgers. The carrier frame is the output.

``receive``
    Run at the destination, on the destination's checkout. The frame is
    recovered by the destination kernel and handed to the receiving operation
    the destination overlay binds to its ``intr-destination`` address
    (``resident-runtime/intr_destination_ingress.py``), which records
    ``ORGANIZATION_INTR_DESTINATION_INGRESS`` ("arrived") in the destination's
    ledgers. An acknowledged diagnostic is answered with the responded request
    class, ``ecosystem.communication.ack``. A refusal is recorded by the
    operation and is not answered.

``close``
    Back at the sender. The answer is verified against the recorded emission
    (same packet, the resolved destination, the acknowledgement class, and a
    far-side terminal receipt that recomputes) and ``ORGANIZATION_EGRESS_CLOSED``
    ("closed") is recorded. A closure is recorded only for the responded
    request class carrying the hop's ``communication_id``.

``federation.json`` is not read. Peers are not enumerated anywhere here: the
destination slug arrives with the dispatch and the advertisement is read from
the destination's own checkout.

Time is a constraint variable supplied by the caller as ``hb_epoch``. Every
phase requires it, and no phase reads a clock to fill it in. Authority is a
constraint variable too. A disposition is transition evidence only.

This module does not decide where receipts land. The ledger location already
selected for emission (``STEGVERSE_REPO_LEDGER_ROOT`` /
``STEGVERSE_ORG_LEDGER_ROOT``, or the published ref by default) is used.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import importlib.util
import inspect
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

ROOT = Path(__file__).resolve().parents[1]

SCHEMA = "stegverse.org-intr-hop/v1"
PROFILE_ID = "intr-destination"
REQUEST_CLASS = "ecosystem.communication"
ACK_CLASS = "ecosystem.communication.ack"
PAYLOAD_SCHEMA = "stegverse.org-intr-destination-diagnostic/v1"
TRANSITION_REFERENCE = "intr-destination-hop.v1"
CAPABILITY_INGRESS_ROLE = "BOUNDARY_LOCAL_CAPABILITY_INGRESS"
INGRESS_OPERATION = "ORGANIZATION_INTR_DESTINATION_INGRESS"
OVERLAY_RELATIVE = "org-runtime/interlock-intr.json"
REGISTRY_RELATIVE = "org-boundary/registry/services.json"
CAPABILITY_INGRESS_RELATIVE = "org-boundary/runtime/capability_ingress.py"
ADDRESS_FORM = "ORGANIZATION_SLUG_DOT_CAPABILITY_PROFILE_ID"
LEDGER_REF = "refs/stegverse/organization-ledger"
ALLOW = "ALLOW"
DENY = "DENY"
FAIL_CLOSED = "FAIL_CLOSED"
CONSTRAINT = "CONSTRAINT_VARIABLE"
CONSTRAINTS = {
    "authority_constraint": CONSTRAINT,
    "time_constraint": CONSTRAINT,
    "time_source": "SUPPLIED_HB_EPOCH",
    "disposition_role": "TRANSITION_EVIDENCE_ONLY",
    "federation_directory_read": False,
}
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
HOP_STANDING = {"mode": "ESTABLISH_GENESIS", "node_ref": "intr-destination-hop", "predecessor": None}


class HopRefused(Exception):
    def __init__(self, failed_predicate: str, detail: str) -> None:
        super().__init__(failed_predicate + ": " + detail)
        self.failed_predicate = failed_predicate
        self.detail = detail


def _module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_LOADED: dict[str, Any] = {}


def egress():
    """This organization's egress boundary. Its kernel and ledgers are the ones used here."""
    if "egress" not in _LOADED:
        _LOADED["egress"] = _module("intr_hop_egress", ROOT / "resident-runtime/organization_egress_boundary.py")
    return _LOADED["egress"]


def kernel():
    return egress().kernel


def canon(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canon(value)).hexdigest()


def organization_slug(organization: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in organization).strip("-")


def call_named(fn: Callable[..., Any], **available: Any) -> Any:
    """Call ``fn`` with the arguments its signature names.

    Checkouts differ in optional tails (``attested``, ``standing``); every
    parameter without a default must be available.
    """
    kwargs = {}
    for name, parameter in inspect.signature(fn).parameters.items():
        if name in available:
            kwargs[name] = available[name]
        elif parameter.default is inspect.Parameter.empty and parameter.kind not in (
                inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            raise TypeError(fn.__name__ + " needs " + name)
    return fn(**kwargs)


def require_epoch(hb_epoch: Any) -> int:
    if isinstance(hb_epoch, bool) or not isinstance(hb_epoch, int) or hb_epoch < 0:
        raise HopRefused("HB_EPOCH_IS_SUPPLIED_AS_A_NON_NEGATIVE_INTEGER", repr(hb_epoch))
    return hb_epoch


def require_communication_id(value: Any) -> str:
    if (not isinstance(value, str) or not value or len(value) > 160
            or not all(33 <= ord(ch) <= 126 for ch in value)):
        raise HopRefused("COMMUNICATION_ID_IS_PRINTABLE_ASCII_WITHOUT_SPACES", repr(value))
    return value


def require_slug(value: Any, field: str) -> str:
    if not isinstance(value, str) or not SLUG.match(value):
        raise HopRefused(field.upper() + "_IS_AN_ORGANIZATION_SLUG", repr(value))
    return value


def frame_for(packet: Mapping[str, Any], hb_epoch: int) -> dict[str, Any]:
    """The kernel's carrier frame at the supplied epoch, never at a sampled clock."""
    k = kernel()
    parameters = inspect.signature(k.carrier_frame).parameters
    if "epoch" in parameters:
        return k.carrier_frame(dict(packet), epoch=hb_epoch)
    now_ns = k.HB_ANCHOR_UNIX_NS + (hb_epoch - k.HB_ANCHOR_EPOCH) * k.HB_PERIOD_NS
    return k.carrier_frame(dict(packet), now_ns=now_ns)


def encode_frame(frame: Mapping[str, Any]) -> str:
    """gzip + base64 of the canonical frame, small enough for a dispatch input."""
    return base64.b64encode(gzip.compress(canon(frame), mtime=0)).decode("ascii")


def decode_frame(text: str) -> dict[str, Any]:
    try:
        frame = json.loads(gzip.decompress(base64.b64decode(text.strip().encode("ascii"), validate=True)))
    except (ValueError, OSError, UnicodeError) as exc:
        raise HopRefused("FRAME_IS_GZIP_BASE64_JSON", type(exc).__name__) from None
    if not isinstance(frame, dict):
        raise HopRefused("FRAME_IS_GZIP_BASE64_JSON", "not an object")
    return frame


def this_organization(root: Path = ROOT) -> str:
    return json.loads((root / REGISTRY_RELATIVE).read_text(encoding="utf-8"))["organization"]


def advertised_destination(peer_root: Path, destination_slug: str) -> dict[str, Any]:
    """Resolve the destination from its own overlay and service row.

    The advertisement is the only source. The slug must name the organization
    that advertises, and that organization must bind exactly one
    ``intr-destination`` address, ``<slug>.intr-destination``, to a capability
    ingress row.
    """
    peer_root = Path(peer_root)
    try:
        overlay = json.loads((peer_root / OVERLAY_RELATIVE).read_text(encoding="utf-8"))
        registry = json.loads((peer_root / REGISTRY_RELATIVE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HopRefused("DESTINATION_ADVERTISEMENT_IS_READABLE", type(exc).__name__ + ": " + str(exc)[:200]) from None
    organization = registry.get("organization") if isinstance(registry, dict) else None
    if not isinstance(organization, str) or organization_slug(organization) != destination_slug:
        raise HopRefused("DESTINATION_SLUG_NAMES_THE_ADVERTISING_ORGANIZATION",
                         repr(organization) + " does not derive " + destination_slug)
    address = destination_slug + "." + PROFILE_ID
    bindings = [entry for entry in ((overlay.get("ingress") or {}).get("capability_endpoint_bindings") or [])
                if isinstance(entry, dict) and entry.get("profile_id") == PROFILE_ID]
    if len(bindings) != 1:
        raise HopRefused("DESTINATION_OVERLAY_BINDS_ONE_INTR_DESTINATION", str(len(bindings)))
    receiving = bindings[0].get("receiving_operation") or {}
    if receiving.get("addressed_service") != address:
        raise HopRefused("DESTINATION_OVERLAY_ADDRESSES_THE_DERIVED_SERVICE",
                         repr(receiving.get("addressed_service")) + " != " + address)
    rows = [row for row in registry.get("services") or []
            if isinstance(row, dict) and row.get("service_id") == address]
    if len(rows) != 1 or rows[0].get("boundary_role") != CAPABILITY_INGRESS_ROLE \
            or rows[0].get("capability_profile_id") != PROFILE_ID:
        raise HopRefused("DESTINATION_SERVICE_ROW_IS_A_CAPABILITY_INGRESS", address)
    control = destination_slug + ".org-control"
    control_row = next((row for row in registry.get("services") or []
                        if isinstance(row, dict) and row.get("service_id") == control), None)
    return {
        "destination_organization": organization,
        "destination_repository": rows[0].get("repository") or organization + "/.github",
        "destination_org_control_service": control if control_row is not None else None,
        "destination_serves_an_organization_control_service": control_row is not None,
        "destination_addressed_service": address,
        "destination_service": address,
        "destination_kernel_required": None,
        "destination_kernel_generation_is_proven_here": False,
        "destination_kernel_required_is_checked_at_resolution": False,
        "declared_kernel_version_does_not_establish_the_enforcement_it_implies": True,
        "transport_profile": getattr(egress(), "TRANSPORT_PROFILE", None),
        "transport_profile_source": "THIS_BOUNDARY_CARRIER",
        "destination_capability_profile_id": PROFILE_ID,
        "addressed_capability": PROFILE_ID,
        "destination_capability_address_form": ADDRESS_FORM,
        "destination_resolution_source": "DESTINATION_ADVERTISEMENT",
        "destination_advertisement_sha256": sha({"binding": bindings[0], "service": rows[0]}),
        "destination_receiving_operation_id": receiving.get("operation_id"),
        "destination_resolved_from_caller_argument": False,
        "destination_slug_supplied_with_the_dispatch": destination_slug,
        "destination_capability_declared_by_the_peer_directory": False,
        "destination_capability_declared_by_the_peer_advertisement": True,
        "peer_serves_this_capability_is_proven_here": False,
        "unserved_capability_is_observable_as_an_unclosed_crossing": True,
    }


def hop_payload(origin_slug: str, destination_slug: str, communication_id: str) -> dict[str, Any]:
    return {
        "message_class": REQUEST_CLASS,
        "communication_id": communication_id,
        "subject": "purpose-neutral one-way hop diagnostic",
        "body": {
            "schema": PAYLOAD_SCHEMA,
            "probe": "one-way-hop",
            "origin_slug": origin_slug,
            "destination_slug": destination_slug,
        },
    }


def _refusal(phase: str, exc: HopRefused, **extra: Any) -> dict[str, Any]:
    return {"schema": SCHEMA, "phase": phase, "disposition": FAIL_CLOSED,
            "failed_predicate": exc.failed_predicate, "detail": exc.detail,
            "receipts_appended": False, **extra, **CONSTRAINTS}


def send(destination_slug: str, communication_id: str, hb_epoch: int, *, peer_root: Path) -> dict[str, Any]:
    """Record "sent" and return the frame addressed to ``<destination_slug>.intr-destination``."""
    try:
        require_slug(destination_slug, "destination_slug")
        require_communication_id(communication_id)
        require_epoch(hb_epoch)
        origin = this_organization()
        origin_slug = organization_slug(origin)
        if destination_slug == origin_slug:
            raise HopRefused("ONE_WAY_HOP_CROSSES_AN_ORGANIZATION_BOUNDARY",
                             "destination is this organization; the self-closure covers that")
        resolved = advertised_destination(peer_root, destination_slug)
    except HopRefused as exc:
        return _refusal("send", exc, communication_id=communication_id)
    e, k = egress(), kernel()
    payload = hop_payload(origin_slug, destination_slug, communication_id)
    packet_id = "intr-hop-" + hashlib.sha256(canon({
        "origin": origin, "destination": resolved["destination_organization"],
        "communication_id": communication_id})).hexdigest()[:24]
    packet = call_named(
        k.build_packet, origin_org=origin, origin_service=origin_slug + "." + PROFILE_ID,
        destination_org=resolved["destination_organization"],
        destination_service=resolved["destination_service"], payload=payload,
        standing=dict(HOP_STANDING), transition_reference=TRANSITION_REFERENCE,
        authority_effect="NONE", packet_id=packet_id)
    # A kernel whose packet has no standing field still sends the standing the
    # caller supplies, so a destination that carries standing onto its answer
    # has the request's standing to carry.
    packet.setdefault("standing", dict(HOP_STANDING))
    frame = frame_for(packet, hb_epoch)
    prior = recorded_repository_receipt(e.EMITTED_CLASS + ":" + packet["packet_id"], e.EMITTED_CLASS)
    if prior is not None:
        evidence = prior.get("evidence") or {}
        if evidence.get("payload_sha256") != e.sha(dict(payload)) or evidence.get("destination_service") != resolved["destination_service"]:
            return _refusal("send", HopRefused("ONE_PACKET_ID_BINDS_ONE_HOP", packet["packet_id"]),
                            communication_id=communication_id)
        # Sent before. The recorded receipt stands and its epoch is the time of
        # the crossing; the frame is re-derived at that epoch.
        recorded_epoch = (prior.get("hb_reference") or {}).get("epoch")
        frame = frame_for(packet, recorded_epoch if isinstance(recorded_epoch, int) else hb_epoch)
        return _sent(origin, origin_slug, resolved, destination_slug, communication_id, packet, frame,
                     prior.get("hb_reference", {}).get("epoch", hb_epoch), prior["receipt_sha256"], None,
                     e.EMITTED_CLASS, replayed=True)
    emission = call_named(e.emission_record, resolved=resolved, origin=origin, packet=packet,
                          frame=frame, payload=payload, standing=dict(HOP_STANDING))
    appended = e._record(e.EMITTED_CLASS + ":" + packet["packet_id"], e.EMITTED_CLASS,
                         emission["payload_sha256"], e.sha(emission), emission, hb_epoch)
    return _sent(origin, origin_slug, resolved, destination_slug, communication_id, packet, frame, hb_epoch,
                 appended["repository_receipt"]["receipt_sha256"],
                 appended["organization_receipt"]["receipt_sha256"], e.EMITTED_CLASS, replayed=False)


def _sent(origin, origin_slug, resolved, destination_slug, communication_id, packet, frame, hb_epoch,
          repository_digest, organization_digest, transition_class, *, replayed: bool) -> dict[str, Any]:
    return {
        "schema": SCHEMA, "phase": "send", "disposition": ALLOW,
        "organization": origin, "origin_slug": origin_slug,
        "destination_organization": resolved["destination_organization"],
        "destination_repository": resolved["destination_repository"],
        "destination_slug": destination_slug, "destination_service": resolved["destination_service"],
        "communication_id": communication_id, "packet_id": packet["packet_id"], "hb_epoch": hb_epoch,
        "frame_sha256": frame.get("frame_sha256"), "frame": encode_frame(frame),
        "repository_receipt_sha256": repository_digest,
        "organization_receipt_sha256": organization_digest,
        "transition_class": transition_class, "receipts_appended": not replayed,
        "transition_replayed": replayed,
        "peer_serves_this_capability_is_proven_here": False, **CONSTRAINTS,
    }


def emitter():
    """This repository's own receipt emitter, the module every operation here appends through."""
    if "emitter" not in _LOADED:
        _LOADED["emitter"] = _module("intr_hop_emit", ROOT / ".stegverse/transition-ledger/emit.py")
    return _LOADED["emitter"]


def repository_store():
    """The repository ledger store, as this repository's emitter selects it."""
    emit = emitter()
    if hasattr(emit, "repository_store"):
        return emit.repository_store()
    return emit.ledger_store.open_store(emit.lr())


def recorded_repository_receipt(transition_id: str, transition_class: str) -> dict[str, Any] | None:
    """The repository receipt already recorded for this transition, walked back from HEAD.

    A phase that is dispatched again is answered from this receipt rather than
    appended a second time.
    """
    emit = emitter()
    store = repository_store()
    head = store.get(emit.ledger_store.HEAD_KEY)
    cursor = head.get("receipt_sha256") if isinstance(head, dict) else None
    seen: set[str] = set()
    while isinstance(cursor, str) and cursor not in seen:
        seen.add(cursor)
        receipt = store.get(emit.ledger_store.receipt_key(cursor))
        if not isinstance(receipt, dict):
            return None
        if receipt.get("transition_class") == transition_class and receipt.get("transition_id") == transition_id:
            return receipt
        cursor = receipt.get("previous_receipt_sha256")
    return None


def recorded_emission(packet_id: str) -> dict[str, Any] | None:
    e = egress()
    receipt = recorded_repository_receipt(e.EMITTED_CLASS + ":" + packet_id, e.EMITTED_CLASS)
    evidence = (receipt or {}).get("evidence")
    return dict(evidence) if isinstance(evidence, Mapping) else None


def far_side_chain(packet_id: str, service_id: str, payload_hash: str) -> list[str]:
    """The receipt chain this boundary's dispatch mints for a packet at an address."""
    e, k = egress(), kernel()
    if hasattr(e, "reconstruct_far_side_chain"):
        return list(e.reconstruct_far_side_chain(packet_id, service_id, payload_hash))
    return list(k.far_side_receipt_chain(packet_id, service_id, payload_hash))


def receive(frame: Mapping[str, Any], hb_epoch: int, *, origin_slug: str | None = None) -> dict[str, Any]:
    """Record "arrived" through this organization's intr-destination, and answer it."""
    k = kernel()
    try:
        require_epoch(hb_epoch)
        if origin_slug is not None:
            require_slug(origin_slug, "origin_slug")
        try:
            packet = k.recover_packet(dict(frame))
        except (ValueError, KeyError, TypeError) as exc:
            raise HopRefused("FRAME_RECOVERS_TO_ITS_PACKET", str(exc)[:200]) from None
        registry = json.loads((ROOT / REGISTRY_RELATIVE).read_text(encoding="utf-8"))
        organization = registry["organization"]
        address = organization_slug(organization) + "." + PROFILE_ID
        if (packet.get("destination") or {}).get("org") != organization:
            raise HopRefused("FRAME_IS_ADDRESSED_TO_THIS_ORGANIZATION", str((packet.get("destination") or {}).get("org")))
        if (packet.get("destination") or {}).get("service") != address:
            raise HopRefused("FRAME_IS_ADDRESSED_TO_THIS_INTR_DESTINATION", str((packet.get("destination") or {}).get("service")))
        sender = (packet.get("origin") or {}).get("org")
        if not isinstance(sender, str) or organization_slug(sender) == organization_slug(organization):
            raise HopRefused("FRAME_CROSSED_AN_ORGANIZATION_BOUNDARY", repr(sender))
        if origin_slug is not None and organization_slug(sender) != origin_slug:
            raise HopRefused("FRAME_ORIGIN_IS_THE_DISPATCHING_PEER", organization_slug(sender) + " != " + origin_slug)
        payload = packet.get("payload") or {}
        if payload.get("message_class") != REQUEST_CLASS:
            raise HopRefused("FRAME_CARRIES_THE_RESPONDED_REQUEST_CLASS", repr(payload.get("message_class")))
        service = next(row for row in registry["services"] if row.get("service_id") == address)
    except HopRefused as exc:
        return _refusal("receive", exc)
    except StopIteration:
        return _refusal("receive", HopRefused("THIS_ORGANIZATION_SERVES_ITS_INTR_DESTINATION", "no service row"))
    prior = recorded_repository_receipt(INGRESS_OPERATION + ":" + str(packet["packet_id"]), INGRESS_OPERATION)
    if prior is not None and (prior.get("evidence") or {}).get("communication_id") == payload.get("communication_id"):
        # Arrived before. The recorded receipt is the arrival; it is answered
        # again from that receipt rather than received a second time.
        evidence = prior.get("evidence") or {}
        recorded_epoch = (prior.get("hb_reference") or {}).get("epoch")
        result = {"disposition": evidence.get("disposition"), "communication_id": evidence.get("communication_id"),
                  "communication_acknowledged": evidence.get("disposition") == ALLOW,
                  "repository_receipt_sha256": prior["receipt_sha256"], "organization_receipt_sha256": None,
                  "transition_replayed": True, "receiving_operation": INGRESS_OPERATION,
                  "profile_id": evidence.get("profile_id")}
        return _answer(packet, organization, sender, address, payload, result,
                       recorded_epoch if isinstance(recorded_epoch, int) else hb_epoch, replayed=True)
    # The same resolution the kernel's capability ingress performs: the overlay
    # binding for this address names the receiving operation, and the
    # submission is read the way that binding says. The supplied epoch is then
    # passed to that operation, so its receipts carry it.
    ci = _module("intr_hop_capability_ingress", ROOT / CAPABILITY_INGRESS_RELATIVE)
    try:
        resolved = ci.resolve(ROOT, service)
        binding = ci.binding_for_service(ROOT, service.get("service_id"))
        submission = ci.submission_for(binding, payload)
        operation = ci.load_operation(resolved)
    except SystemExit as refused:
        return _refusal("receive", HopRefused("ADDRESS_RESOLVES_TO_THE_BOUND_RECEIVING_OPERATION", str(refused)[:300]))
    if getattr(operation, "OPERATION_ID", None) != INGRESS_OPERATION:
        return _refusal("receive", HopRefused("ADDRESS_RESOLVES_TO_THE_BOUND_RECEIVING_OPERATION",
                                              repr(getattr(operation, "OPERATION_ID", None))))
    result = operation.receive(submission, registry=registry, standing=packet.get("standing"),
                               packet_id=str(packet["packet_id"]), hb_epoch=hb_epoch, mesh_root=None)
    return _answer(packet, organization, sender, address, payload, result, hb_epoch, replayed=False)


def _answer(packet, organization, sender, address, payload, result, hb_epoch, *, replayed: bool) -> dict[str, Any]:
    k = kernel()
    base = {
        "schema": SCHEMA, "phase": "receive", "organization": organization,
        "origin_organization": sender, "origin_slug": organization_slug(sender),
        "destination_service": address, "communication_id": payload.get("communication_id"),
        "packet_id": packet["packet_id"], "hb_epoch": hb_epoch,
        "ingress_disposition": result.get("disposition"),
        "repository_receipt_sha256": result.get("repository_receipt_sha256"),
        "organization_receipt_sha256": result.get("organization_receipt_sha256"),
        "transition_class": INGRESS_OPERATION if result.get("disposition") == ALLOW else result.get("transition_class"),
        "receipts_appended": bool(result.get("repository_receipt_sha256")) and not replayed,
        "transition_replayed": replayed, **CONSTRAINTS,
    }
    if result.get("disposition") != ALLOW or not result.get("communication_acknowledged", True):
        # A refusal is recorded by the operation. It is not answered, so the
        # sender's crossing stays observable as unclosed.
        return {**base, "disposition": result.get("disposition") or DENY, "answered": False,
                "failed_predicate": result.get("failed_predicate"), "detail": result.get("detail")}
    chain = far_side_chain(packet["packet_id"], address, k.sha(packet["payload"]))
    execution = {
        "application_result": {
            "schema": "stegverse.org-intr-destination-result/v1",
            "receiving_operation_id": INGRESS_OPERATION,
            "capability_profile_id": PROFILE_ID,
            "receiving_operation_result": result,
        },
        "reconstruction": {"same_execution_required": True, "status": "RECONSTRUCTED",
                           "terminal_receipt_id": chain[-1]},
    }
    response = k.build_control_response(packet, execution)
    response_frame = frame_for(response, hb_epoch)
    return {**base, "disposition": ALLOW, "answered": True,
            "response_message_class": (response.get("payload") or {}).get("message_class"),
            "response_packet_id": response.get("packet_id"), "far_side_terminal_receipt": chain[-1],
            "response_frame_sha256": response_frame.get("frame_sha256"),
            "frame": encode_frame(response_frame)}


def close(response_frame: Mapping[str, Any], destination_slug: str, communication_id: str,
          hb_epoch: int) -> dict[str, Any]:
    """Record "closed" for the responded request class carrying ``communication_id``."""
    e, k = egress(), kernel()
    try:
        require_slug(destination_slug, "destination_slug")
        require_communication_id(communication_id)
        require_epoch(hb_epoch)
        origin = this_organization()
        try:
            response = k.recover_packet(dict(response_frame))
        except (ValueError, KeyError, TypeError) as exc:
            raise HopRefused("FRAME_RECOVERS_TO_ITS_PACKET", str(exc)[:200]) from None
        if (response.get("destination") or {}).get("org") != origin:
            raise HopRefused("ANSWER_IS_ADDRESSED_TO_THIS_ORGANIZATION", str((response.get("destination") or {}).get("org")))
        payload = response.get("payload") or {}
        body = payload.get("body") or {}
        if payload.get("message_class") != ACK_CLASS:
            raise HopRefused("ANSWER_CARRIES_THE_RESPONDED_REQUEST_CLASS", repr(payload.get("message_class")))
        if payload.get("communication_id") != communication_id:
            raise HopRefused("ANSWER_CARRIES_THE_HOP_COMMUNICATION_ID", repr(payload.get("communication_id")))
        responder = body.get("responding_organization") or (response.get("origin") or {}).get("org")
        if not isinstance(responder, str) or organization_slug(responder) != destination_slug:
            raise HopRefused("ANSWER_CAME_FROM_THE_DISPATCHED_DESTINATION", repr(responder))
        packet_id = body.get("request_packet_id")
        emission = recorded_emission(packet_id) if isinstance(packet_id, str) else None
        if not emission:
            raise HopRefused("THIS_ORGANIZATION_RECORDED_THE_EMISSION_IT_CLOSES", repr(packet_id))
        if emission.get("communication_id") not in (None, communication_id) or emission.get("payload_sha256") != sha(
                hop_payload(organization_slug(origin), destination_slug, communication_id)):
            raise HopRefused("ANSWER_CARRIES_THE_HOP_COMMUNICATION_ID", "the recorded emission carried another communication")
    except HopRefused as exc:
        return _refusal("close", exc, communication_id=communication_id)
    for recorded_class in (e.CLOSED_CLASS, e.CLOSURE_REFUSED_CLASS):
        prior = recorded_repository_receipt(recorded_class + ":" + packet_id, recorded_class)
        if prior is not None:
            evidence = prior.get("evidence") or {}
            closed = recorded_class == e.CLOSED_CLASS
            return {
                "schema": SCHEMA, "phase": "close", "organization": origin,
                "disposition": evidence.get("disposition"), "closed": closed,
                "closure_findings": evidence.get("closure_findings"), "communication_id": communication_id,
                "packet_id": packet_id, "destination_organization": evidence.get("destination_organization"),
                "destination_slug": destination_slug, "response_message_class": evidence.get("response_message_class"),
                "hb_epoch": (prior.get("hb_reference") or {}).get("epoch"),
                "repository_receipt_sha256": prior["receipt_sha256"], "organization_receipt_sha256": None,
                "transition_class": recorded_class, "receipts_appended": False, "transition_replayed": True,
                "peer_serves_this_capability_is_proven_here": closed, **CONSTRAINTS,
            }
    resolved = dict(emission)
    row = {
        "organization": responder,
        "message_class": payload.get("message_class"),
        "request_packet_id": packet_id,
        "application_result": body.get("application_result"),
        "receipt_terminal": body.get("receipt_terminal"),
        "response_packet_id": response.get("packet_id"),
        "frame_sha256": response_frame.get("frame_sha256"),
    }
    findings = list(e.verify_closure(resolved, packet_id, row, emission))
    operation_result = ((body.get("application_result") or {}).get("receiving_operation_result") or {})
    if operation_result.get("disposition") != ALLOW or operation_result.get("communication_id") != communication_id:
        findings.append("FAR_SIDE_INTR_DESTINATION_ACKNOWLEDGED_THIS_COMMUNICATION")
    recomputed = far_side_chain(packet_id, emission["far_side_service_id"], emission["far_side_payload_hash"])
    closure = call_named(e.closure_record, resolved=resolved, origin=origin, packet_id=packet_id,
                         communication_id=communication_id, response=row, findings=findings,
                         recomputed=recomputed)
    transition_class = e.CLOSED_CLASS if not findings else e.CLOSURE_REFUSED_CLASS
    appended = e._record(transition_class + ":" + packet_id, transition_class,
                         e.sha({"packet_id": packet_id, "communication_id": communication_id}),
                         e.sha(closure), closure, hb_epoch)
    return {
        "schema": SCHEMA, "phase": "close", "organization": origin,
        "disposition": closure.get("disposition"), "closed": not findings,
        "closure_findings": findings, "communication_id": communication_id, "packet_id": packet_id,
        "destination_organization": responder, "destination_slug": destination_slug,
        "response_message_class": row["message_class"], "hb_epoch": hb_epoch,
        "far_side_terminal_receipt": row["receipt_terminal"],
        "far_side_terminal_receipt_recomputed": recomputed[-1] if recomputed else None,
        "repository_receipt_sha256": appended["repository_receipt"]["receipt_sha256"],
        "organization_receipt_sha256": appended["organization_receipt"]["receipt_sha256"],
        "transition_class": transition_class, "receipts_appended": True, "transition_replayed": False,
        "peer_serves_this_capability_is_proven_here": not findings, **CONSTRAINTS,
    }


def readback(location: str, namespace: str, result: Mapping[str, Any]) -> dict[str, Any]:
    """Find this phase's repository and organization receipts on a freshly fetched ref."""
    store_module = _module("intr_hop_ledger_store", ROOT / "resident-runtime/ledger_store.py")

    def chain(locator: str) -> list[dict[str, Any]]:
        store = store_module.open_store(store_module.parse_locator(locator))
        head = store.get(store_module.HEAD_KEY)
        cursor = head.get("receipt_sha256") if isinstance(head, dict) else None
        out, seen = [], set()
        while isinstance(cursor, str) and cursor not in seen:
            seen.add(cursor)
            receipt = store.get(store_module.receipt_key(cursor))
            if not isinstance(receipt, dict):
                break
            out.append(receipt)
            cursor = receipt.get("previous_receipt_sha256")
        return out

    repo_chain = chain(location + ":" + namespace)
    org_chain = chain(location)
    repo_digest = result.get("repository_receipt_sha256")
    org_digest = result.get("organization_receipt_sha256")
    repo_receipt = next((item for item in repo_chain if item.get("receipt_sha256") == repo_digest), None)
    org_receipt = next((item for item in org_chain if item.get("repo_receipt_sha256") == repo_digest), None)
    if org_digest is None and org_receipt is not None:
        org_digest = org_receipt.get("receipt_sha256")
    evidence = (repo_receipt or {}).get("evidence") or {}
    findings = []
    if repo_receipt is None:
        findings.append("REPOSITORY_RECEIPT_ON_THE_PUBLISHED_REF")
    if org_receipt is None:
        findings.append("ORGANIZATION_RECEIPT_ON_THE_PUBLISHED_REF")
    elif result.get("organization_receipt_sha256") not in (None, org_digest):
        findings.append("ORGANIZATION_RECEIPT_HOLDS_THE_REPOSITORY_RECEIPT")
    if repo_receipt is not None and result.get("phase") == "send":
        # An emission record names its packet; the packet's payload carries the
        # communication_id the closure correlates on.
        if evidence.get("packet_id") != result.get("packet_id"):
            findings.append("RECEIPT_NAMES_THE_EMITTED_PACKET")
    elif repo_receipt is not None and evidence.get("communication_id") != result.get("communication_id"):
        findings.append("RECEIPT_CARRIES_THE_HOP_COMMUNICATION_ID")
    if repo_receipt is not None and ((repo_receipt.get("hb_reference") or {}).get("epoch") != result.get("hb_epoch")):
        findings.append("RECEIPT_CARRIES_THE_SUPPLIED_EPOCH")
    return {
        "schema": SCHEMA + "#readback", "phase": result.get("phase"), "location": location,
        "repository_namespace": namespace, "communication_id": result.get("communication_id"),
        "transition_class": (repo_receipt or {}).get("transition_class"),
        "repository_receipt_sha256": repo_digest, "organization_receipt_sha256": org_digest,
        "disposition": ALLOW if not findings else FAIL_CLOSED, "failed_predicates": findings,
        **CONSTRAINTS,
    }


def _write(result: Mapping[str, Any], out: Path | None) -> None:
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered, encoding="utf-8")
    shown = {key: value for key, value in result.items() if key != "frame"}
    print(json.dumps(shown, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="phase", required=True)
    p = sub.add_parser("send")
    p.add_argument("--destination-slug", required=True)
    p.add_argument("--communication-id", required=True)
    p.add_argument("--hb-epoch", type=int, required=True)
    p.add_argument("--peer-root", type=Path, required=True)
    p.add_argument("--out", type=Path)
    p = sub.add_parser("receive")
    p.add_argument("--frame-file", type=Path, required=True)
    p.add_argument("--origin-slug", required=True)
    p.add_argument("--hb-epoch", type=int, required=True)
    p.add_argument("--out", type=Path)
    p = sub.add_parser("close")
    p.add_argument("--frame-file", type=Path, required=True)
    p.add_argument("--destination-slug", required=True)
    p.add_argument("--communication-id", required=True)
    p.add_argument("--hb-epoch", type=int, required=True)
    p.add_argument("--out", type=Path)
    p = sub.add_parser("readback")
    p.add_argument("--location", required=True)
    p.add_argument("--repository-namespace", required=True)
    p.add_argument("--result", type=Path, required=True)
    p.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.phase == "send":
            result = send(args.destination_slug, args.communication_id, args.hb_epoch, peer_root=args.peer_root)
        elif args.phase == "receive":
            result = receive(decode_frame(args.frame_file.read_text(encoding="ascii")), args.hb_epoch,
                             origin_slug=args.origin_slug)
        elif args.phase == "close":
            result = close(decode_frame(args.frame_file.read_text(encoding="ascii")), args.destination_slug,
                           args.communication_id, args.hb_epoch)
        else:
            result = readback(args.location, args.repository_namespace,
                              json.loads(args.result.read_text(encoding="utf-8")))
    except HopRefused as exc:
        result = _refusal(args.phase, exc)
    _write(result, args.out)
    if args.phase == "close":
        return 0 if result.get("closed") else 1
    return 0 if result.get("disposition") == ALLOW else 1


if __name__ == "__main__":
    sys.exit(main())
