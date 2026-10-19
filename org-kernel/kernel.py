#!/usr/bin/env python3
"""SV-011 mesh locator and capability-ingress dispatch.

The mesh location is supplied by the caller. A missing location fails closed.
This kernel does not derive a mesh from the environment, a home directory, or
the checkout.

``BOUNDARY_LOCAL_CAPABILITY_INGRESS`` resolves through
``org-boundary/runtime/capability_ingress.py``. The receiving operation records
both ledger levels. This dispatch does not write a second receipt for the same
frame.

Authority and time are constraint variables. A disposition is transition
evidence. The carrier epoch is an oscillator count, not an admission.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HB_ANCHOR_EPOCH = 32
HB_ANCHOR_UNIX_NS = 1_787_511_600_000_000_000
HB_PERIOD_NS = 10_000_000
HB_HZ = 100
CHANNEL_COUNT = 16
SCHEMA = "stegverse.org-resident-kernel/v1"
CARRIER_SCHEMA = "stegverse.org-resident-kernel.carrier/v1"
PACKET_SCHEMA = "stegverse.intr.org-boundary.v1"
SUPPLIED = "SUPPLIED"
MESH_FRAME_PREFIX = "frames.d/"

CAPABILITY_INGRESS_ROLE = "BOUNDARY_LOCAL_CAPABILITY_INGRESS"
BOUNDARY_LOCAL_SELECTION = "BOUNDARY_LOCAL_NO_PROCESSOR_SELECTED"

RESPONDED_REQUEST_CLASSES = {
    "ecosystem.monitor.request": "ecosystem.monitor.response",
    "ecosystem.work.request": "ecosystem.work.ack",
    "ecosystem.communication": "ecosystem.communication.ack",
}


def canon(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode()


def sha(value: Any) -> str:
    raw = value if isinstance(value, (bytes, bytearray)) else canon(value)
    return "sha256:" + hashlib.sha256(bytes(raw)).hexdigest()


def validate_hb_reference(ref: dict[str, Any]) -> dict[str, Any]:
    """Check a carrier heartbeat reference. The epoch is a time constraint."""
    if not isinstance(ref, dict):
        raise ValueError("heartbeat_reference_invalid")
    epoch = ref.get("epoch")
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < HB_ANCHOR_EPOCH:
        raise ValueError("heartbeat_epoch_invalid")
    if ref.get("generation") != epoch:
        raise ValueError("heartbeat_generation_mismatch")
    if ref.get("heartbeat_id") != f"HB:{epoch}":
        raise ValueError("heartbeat_id_mismatch")
    if ref.get("frequency_hz") != HB_HZ:
        raise ValueError("heartbeat_frequency_mismatch")
    if ref.get("progression_dependency") != "OSCILLATOR_ONLY":
        raise ValueError("heartbeat_progression_not_oscillator")
    return ref


def hb_reference(now_ns: int | None = None, *, epoch: int | None = None) -> dict[str, Any]:
    """Build the heartbeat reference. Progression is oscillator-only.

    A supplied epoch is the time constraint for this frame. Deriving one from
    a host clock marks the reference as clock-derived. Neither value selects
    admission, routing, or execution.
    """
    if epoch is not None:
        if now_ns is not None:
            raise ValueError("supply_epoch_or_sample_not_both")
        if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < HB_ANCHOR_EPOCH:
            raise ValueError("epoch_precedes_hb32_anchor")
        return validate_hb_reference({
            "epoch": epoch, "generation": epoch, "heartbeat_id": f"HB:{epoch}",
            "phase_offset_ns": 0, "frequency_hz": HB_HZ,
            "progression_dependency": "OSCILLATOR_ONLY", "derived_from_clock": False,
            "authority_constraint": "NONE", "time_constraint": "HEARTBEAT_EPOCH",
        })
    if now_ns is None:
        now_ns = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    if now_ns < HB_ANCHOR_UNIX_NS:
        raise ValueError("sample_precedes_hb32_anchor")
    quotient, phase = divmod(now_ns - HB_ANCHOR_UNIX_NS, HB_PERIOD_NS)
    derived = HB_ANCHOR_EPOCH + quotient
    return validate_hb_reference({
        "epoch": derived, "generation": derived, "heartbeat_id": f"HB:{derived}",
        "sampled_unix_ns": now_ns, "phase_offset_ns": phase, "frequency_hz": HB_HZ,
        "progression_dependency": "OSCILLATOR_ONLY", "derived_from_clock": True,
        "authority_constraint": "NONE", "time_constraint": "HEARTBEAT_EPOCH",
    })


# The shared supplied-epoch rule and receipt-writer helper, byte-identical to
# StegVerse-Labs/.github@bc608a3:org-kernel/kernel.py (WU-B13a).
HB_EPOCH_NOT_SUPPLIED="HB_EPOCH_NOT_SUPPLIED"
HB_EPOCH_INVALID="HB_EPOCH_INVALID"
HB_EPOCH_PREDICATE="SUBMISSION_CARRIES_THE_SUPPLIED_HB_EPOCH"
HB_EPOCH_OWNING_EXISTING_GOAL="INTERLOCK-INTR-RUNTIME-INDEPENDENT-PROTOCOL-001"

def supplied_epoch_failure(hb_epoch: Any, reference: Any = None) -> tuple[str, str] | None:
    """The shared supplied-epoch rule (SUBMISSION_CARRIES_THE_SUPPLIED_HB_EPOCH).

    Byte-identical in StegVerse-org/.github#185 and StegVerse-Labs/.github#3212
    and #3213. `hb_epoch` absent (None) is HB_EPOCH_NOT_SUPPLIED. Otherwise it
    must be ASCII [0-9]+ only -- an int is read as its decimal text; a bool, a
    float or anything coerced ('+33', ' 33', '3_3', '33.0') is refused -- with
    integer value >= 32 (the HB32 anchor). `reference`, the carrier frame's
    heartbeat reference when there is one, must be an object, and a
    `derived_from_clock` it carries must be exactly false. Any failure is
    HB_EPOCH_INVALID. Returns (failure_code, detail), or None when the epoch is
    supplied. No clock is read.
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
    if reference is not None and "sampled_unix_ns" in reference:
        return "HB_EPOCH_INVALID", "the carrier's heartbeat reference carries sampled_unix_ns: a clock marker"
    if reference is not None and "derived_from_clock" not in reference:
        return "HB_EPOCH_PROVENANCE_UNMARKED", "the carrier's heartbeat reference has no derived_from_clock"
    return None

class SuppliedEpochRefused(ValueError):
    """A receipt writer's refusal of an absent or invalid supplied epoch.

    `refusal` is the six-field FAIL_CLOSED from `writer_epoch_refusal`. Raised
    before anything is written; no clock is read to stand in for the epoch.
    """
    def __init__(self, refusal:dict[str,Any])->None:
        super().__init__(refusal["failure_code"]+": "+refusal["detail"])
        self.disposition=refusal["disposition"]
        self.failed_predicate=refusal["failed_predicate"]
        self.refusal=dict(refusal)

def writer_epoch_refusal(hb_epoch:Any, *, retry_entrypoint:str)->dict[str,Any]|None:
    """The six-field FAIL_CLOSED of a receipt writer's epoch, or None when it is supplied.

    The shape StegVerse-org/.github#189 gave the organization ledger
    (`epoch_refusal`): a receipt references the heartbeat epoch its caller
    supplies, checked by `supplied_epoch_failure`; an absent one is
    HB_EPOCH_NOT_SUPPLIED and is never derived from a clock.
    """
    failure=supplied_epoch_failure(hb_epoch)
    if failure is None:
        return None
    code,detail=failure
    return {"disposition":"FAIL_CLOSED","failure_code":code,"failed_predicate":HB_EPOCH_PREDICATE,
            "detail":detail,
            "required_evidence_or_repair":("the caller supplies hb_epoch= (or --hb-epoch): ASCII digits with "
                                           "value >= "+str(HB_ANCHOR_EPOCH)+"; it is never derived from a clock"),
            "retry_entrypoint":retry_entrypoint,
            "owning_existing_goal":HB_EPOCH_OWNING_EXISTING_GOAL,
            "next_attempt":"call again with the supplied epoch; nothing was written",
            "derived_from_clock":False,"consequence_committed":False}

def supplied_hb_reference(hb_epoch:Any, *, retry_entrypoint:str)->dict[str,Any]:
    """The heartbeat reference of a supplied epoch, or SuppliedEpochRefused. No clock is read."""
    refusal=writer_epoch_refusal(hb_epoch,retry_entrypoint=retry_entrypoint)
    if refusal is not None:
        raise SuppliedEpochRefused(refusal)
    return hb_reference(epoch=int(str(hb_epoch)))


def derive_channel(payload_hash: str) -> dict[str, Any]:
    if not isinstance(payload_hash, str) or not payload_hash.startswith("sha256:") or len(payload_hash) != 71:
        raise ValueError("payload_hash_invalid")
    slot = int(payload_hash[7:23], 16) % CHANNEL_COUNT
    return {"channel_id": f"HB:H1:P{slot}", "phase_slot": slot, "phase_slot_count": CHANNEL_COUNT,
            "derivation": "PAYLOAD_SHA256_FIRST64_MOD_16", "authority_constraint": "NONE_CARRIER_ONLY"}


#: Provenance a writer may record on hb_reference for an epoch it was not
#: supplied but carried: the source, and the receipt that carried it. Neither
#: changes the epoch or derived_from_clock.
EPOCH_PROVENANCE_KEYS = ("source", "head_receipt_sha")


def with_epoch_provenance(reference: dict[str, Any], provenance: Any) -> dict[str, Any]:
    """``reference`` with a carried epoch's provenance recorded on it, or unchanged when there is none."""
    if provenance is None:
        return reference
    if not isinstance(provenance, dict) or set(provenance) != set(EPOCH_PROVENANCE_KEYS) \
            or not all(isinstance(provenance[key], str) and provenance[key] for key in EPOCH_PROVENANCE_KEYS):
        raise ValueError("epoch_provenance_invalid")
    return {**reference, **provenance}


CARRIED_PROVENANCE_UNVERIFIED = "CARRIED_PROVENANCE_UNVERIFIED"


def carried_provenance_refusal(provenance: Any, reference: dict[str, Any], head_store: Any,
                               receipt_key: Any, head_key: str, *, retry_entrypoint: str) -> dict[str, Any] | None:
    """The six-field FAIL_CLOSED when a CARRIED provenance does not verify, or None.

    A writer given a carried epoch checks it against ``head_store``, the
    organization ledger: ``head_receipt_sha`` must be its current head, that
    receipt's seal must verify, its epoch must pass the shared rule with
    derived_from_clock exactly false, and it must equal the epoch being written.
    """
    if provenance is None:
        return None
    claimed = provenance.get("head_receipt_sha") if isinstance(provenance, dict) else None
    head = head_store.get(head_key) if head_store is not None else None
    current = head.get("receipt_sha256") if isinstance(head, dict) else None
    detail = None
    if head_store is None:
        detail = "no organization ledger to verify the carried head against"
    elif not claimed or claimed != current:
        detail = "head_receipt_sha " + repr(claimed) + " is not the organization ledger head " + repr(current)
    else:
        receipt = head_store.get(receipt_key(current))
        body = {k: v for k, v in receipt.items() if k != "receipt_sha256"} if isinstance(receipt, dict) else None
        carried = receipt.get("hb_reference") if isinstance(receipt, dict) else None
        epoch = carried.get("epoch") if isinstance(carried, dict) else None
        if body is None or receipt.get("receipt_sha256") != current or sha(body) != current:
            detail = "the organization ledger head receipt " + current + " is absent or its seal does not verify"
        elif not isinstance(carried, dict) or carried.get("derived_from_clock") is not False \
                or type(epoch) is not int or supplied_epoch_failure(epoch, carried) is not None:
            detail = "the head receipt's hb_reference " + repr(carried) + " is not a supplied-rule, non-clock epoch"
        elif epoch != reference.get("epoch"):
            detail = "the carried epoch " + repr(reference.get("epoch")) + " is not the head epoch " + repr(epoch)
    if detail is None:
        return None
    return {"disposition": "FAIL_CLOSED", "failure_code": CARRIED_PROVENANCE_UNVERIFIED,
            "failed_predicate": "CARRIED_EPOCH_IS_THE_VERIFIED_ORGANIZATION_LEDGER_HEAD_EPOCH",
            "detail": detail,
            "required_evidence_or_repair": ("head_receipt_sha names the current organization ledger head, whose "
                                            "sealed receipt carries a supplied-rule epoch with derived_from_clock "
                                            "false equal to the carried epoch"),
            "retry_entrypoint": retry_entrypoint,
            "owning_existing_goal": HB_EPOCH_OWNING_EXISTING_GOAL,
            "next_attempt": "read the current head and carry its epoch; nothing was written",
            "derived_from_clock": False, "consequence_committed": False}


def carrier_frame(packet: dict[str, Any], *, now_ns: int | None = None,
                  epoch: int | None = None) -> dict[str, Any]:
    raw = canon(packet)
    payload_hash = sha(packet.get("payload", {}))
    ref = hb_reference(now_ns, epoch=epoch)
    channel = derive_channel(payload_hash)
    body = {"schema": CARRIER_SCHEMA, "packet_id": packet["packet_id"], "packet_sha256": sha(raw),
            "packet_base64": base64.b64encode(raw).decode("ascii"), "heartbeat_reference": ref,
            "channel": channel, "origin_org": packet["origin"]["org"],
            "destination_org": packet["destination"]["org"],
            "intr_profile": packet["intr_profile"], "authority_constraint": "NONE_CARRIER_ONLY",
            "time_constraint": "HEARTBEAT_EPOCH"}
    return {**body, "frame_sha256": sha(body)}


def _supplied_epoch_as_int(reference: Any) -> Any:
    """Read a carrier epoch the shared rule accepts (ASCII digits >= 32) as its integer.

    A digit-string ``epoch`` / ``generation`` is a supplied epoch, not an invalid
    one; anything else is left for ``validate_hb_reference`` to refuse.
    """
    if not isinstance(reference, dict) or supplied_epoch_failure(reference.get("epoch")) is not None:
        return reference
    normalized = dict(reference, epoch=int(str(reference["epoch"])))
    generation = reference.get("generation")
    if isinstance(generation, str) and generation.isascii() and generation.isdigit():
        normalized["generation"] = int(generation)
    return normalized


def recover_packet(frame: dict[str, Any]) -> dict[str, Any]:
    body = dict(frame)
    claimed = body.pop("frame_sha256", None)
    if claimed != sha(body):
        raise ValueError("carrier_frame_hash_mismatch")
    raw = base64.b64decode(frame["packet_base64"].encode("ascii"), validate=True)
    if sha(raw) != frame["packet_sha256"]:
        raise ValueError("packet_hash_mismatch")
    validate_hb_reference(_supplied_epoch_as_int(frame.get("heartbeat_reference")))
    packet = json.loads(raw)
    if packet["packet_id"] != frame["packet_id"]:
        raise ValueError("packet_id_mismatch")
    if packet["destination"]["org"] != frame["destination_org"]:
        raise ValueError("destination_org_mismatch")
    return packet


def receipt(kind: str, packet_id: str, subject: str, previous: str | None,
            detail: dict[str, Any]) -> dict[str, Any]:
    body = {"kind": kind, "packet_id": packet_id, "subject": subject,
            "previous_receipt_id": previous, "detail": detail}
    receipt_id = kind.lower() + "-" + hashlib.sha256(canon(body)).hexdigest()[:24]
    return {**body, "receipt_id": receipt_id, "evidence_hash": sha(body)}


def organization_slug(organization: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in organization).strip("-")


def capability_ingress(root: Path):
    path = root / "org-boundary/runtime/capability_ingress.py"
    if not path.is_file():
        raise ValueError("org_boundary_capability_ingress_missing")
    spec = importlib.util.spec_from_file_location("capability_ingress", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_registry(source: Path | dict[str, Any]) -> dict[str, Any]:
    if isinstance(source, dict):
        value = source
    else:
        value = json.loads((Path(source) / "org-boundary/registry/services.json").read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("organization"), str):
        raise ValueError("capability_registry_invalid")
    if not isinstance(value.get("services"), list):
        raise ValueError("capability_registry_services_invalid")
    return value


def load_federation_directory(root: Path) -> dict[str, Any]:
    path = root / "org-boundary/registry/federation.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("denominator") != len(value.get("organizations") or []):
        raise ValueError("federation_directory_denominator_mismatch")
    return value


def resolve_federation_root(root: Path | str | None) -> tuple[Path, str]:
    """The mesh location the caller supplied.

    StegOS does not derive mesh identity from an environment variable, a home
    directory, or a checkout. No supplied root fails closed.
    """
    if root is None:
        raise ValueError("mesh_location_required_from_materializer")
    return Path(root).expanduser().resolve(), SUPPLIED


def node_state_provenance(root: Path | str | None = None) -> dict[str, Any]:
    resolved, provenance = resolve_federation_root(root)
    return {"schema_version": "stegverse.stegos-node-state-provenance/v1",
            "mesh_locator": str(resolved), "mesh_provenance": provenance,
            "mesh_portable": provenance == SUPPLIED, "store_kind": "POSIX_FILESYSTEM",
            "authority_constraint": "NONE_REPORT_ONLY", "time_constraint": "HEARTBEAT_EPOCH"}


def _frame_path(root: Path, frame: dict[str, Any]) -> Path:
    digest = hashlib.sha256((frame["packet_id"] + "|" + frame["frame_sha256"]).encode()).hexdigest()
    return root / MESH_FRAME_PREFIX / (digest + ".json")


def publish_frame(frame: dict[str, Any], *, root: Path | None = None) -> Path:
    resolved, _provenance = resolve_federation_root(root)
    path = _frame_path(resolved, frame)
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(frame, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    if path.is_file():
        if path.read_text(encoding="utf-8") != rendered:
            raise ValueError("federation_frame_write_once_collision")
        return path
    path.write_text(rendered, encoding="utf-8")
    return path


def scan_addressed_frames(organization: str, *, root: Path | None = None,
                          seen: set[str] | None = None) -> list[dict[str, Any]]:
    resolved, _provenance = resolve_federation_root(root)
    directory = resolved / "frames.d"
    if not directory.is_dir():
        return []
    consumed = seen if seen is not None else set()
    quarantined = resolved / QUARANTINE_DIRECTORY
    found = []
    for path in sorted(directory.glob("*.json")):
        if path.name in consumed or (quarantined / path.name).is_file():
            continue
        frame = json.loads(path.read_text(encoding="utf-8"))
        if frame.get("destination_org") == organization:
            found.append({"path": str(path), "name": path.name, "frame": frame})
    return found


def carried_standing(request_packet: dict[str, Any]) -> dict[str, Any]:
    declared = request_packet.get("standing")
    if not isinstance(declared, dict):
        raise ValueError("response_requires_request_standing")
    return {**declared, "standing_carried_forward_from_request": True}


def build_packet(*, origin_org: str, origin_service: str, destination_org: str,
                 destination_service: str, payload: dict[str, Any], standing: dict[str, Any],
                 transition_reference: str = "federation.v1", authority_effect: str = "NONE",
                 packet_id: str | None = None) -> dict[str, Any]:
    """Build a packet. ``standing`` is required.

    ``authority_effect`` is the authority constraint variable carried on the
    transition, matching the existing organization-boundary envelope. It does
    not select admission. The name is the envelope field the resident transport
    already reads.
    """
    if not isinstance(standing, dict):
        raise ValueError("standing_constraint_required")
    pid = packet_id or "pkt-" + hashlib.sha256(canon({
        "origin_org": origin_org, "origin_service": origin_service,
        "destination_org": destination_org, "destination_service": destination_service,
        "payload": payload, "transition_reference": transition_reference,
    })).hexdigest()[:24]
    return {
        "schema_version": PACKET_SCHEMA,
        "packet_id": pid,
        "direction": "INGRESS",
        "origin": {"org": origin_org, "service": origin_service},
        "destination": {"org": destination_org, "service": destination_service},
        "carrier": {"kind": "HB_DERIVED", "reference": "org-federation"},
        "intr_profile": "stegverse.intr.org-boundary.v1",
        "transition": {"reference": transition_reference, "authority_effect": authority_effect,
                       "authority_constraint": authority_effect, "time_constraint": "HEARTBEAT_EPOCH",
                       "conditions": []},
        "standing": standing,
        "payload": payload,
        "evidence": {"ingress_receipt": None, "dispatch_receipt": None, "consumption_receipt": None,
                     "egress_receipt": None, "reconstruction_reference": None},
    }


PUBLISH_EPOCH_RETRY_ENTRYPOINT = "org-kernel/kernel.py::publish_packet (epoch=<digits >= 32>)"


def publish_packet(packet: dict[str, Any], *, root: Path | None = None,
                   now_ns: int | None = None, epoch: int | None = None) -> dict[str, Any]:
    """Publish a packet in a carrier frame.

    A supplied ``epoch`` is checked by the shared rule: ASCII digits >= 32 are
    taken as their integer; anything else is ``SuppliedEpochRefused`` (six
    fields) before a frame is built or published. ``epoch=None`` still derives
    one from the clock (WU-B13); no epoch-taking writer calls it that way.
    """
    if epoch is not None:
        refusal = writer_epoch_refusal(epoch, retry_entrypoint=PUBLISH_EPOCH_RETRY_ENTRYPOINT)
        if refusal is not None:
            raise SuppliedEpochRefused(refusal)
        epoch = int(str(epoch))
    frame = carrier_frame(packet, now_ns=now_ns, epoch=epoch)
    path = publish_frame(frame, root=root)
    return {"packet": packet, "frame": frame, "path": str(path)}


def response_message_class(request_class: str | None) -> str:
    return RESPONDED_REQUEST_CLASSES.get(request_class or "", "ecosystem.communication.ack")


def build_control_response(request_packet: dict[str, Any], execution_result: dict[str, Any]) -> dict[str, Any]:
    req_payload = request_packet.get("payload") or {}
    origin_org = request_packet["origin"]["org"]
    local_org = request_packet["destination"]["org"]
    message_class = response_message_class(req_payload.get("message_class"))
    payload = {
        "communication_id": req_payload.get("communication_id"),
        "message_class": message_class,
        "subject": "response:" + str(req_payload.get("subject") or ""),
        "body": {
            "request_packet_id": request_packet["packet_id"],
            "responding_organization": local_org,
            "application_result": execution_result.get("application_result"),
            "receipt_terminal": (execution_result.get("reconstruction") or {}).get("terminal_receipt_id"),
        },
        "requested_action": None,
        "audience": "ORIGIN",
        "target_organization": origin_org,
        "target_count": 1,
    }
    reference = str((request_packet.get("transition") or {}).get("reference") or "ecosystem.communication.v1")
    return build_packet(
        origin_org=local_org,
        origin_service=organization_slug(local_org) + ".org-control",
        destination_org=origin_org,
        destination_service=organization_slug(origin_org) + ".org-control",
        payload=payload,
        standing=carried_standing(request_packet),
        transition_reference=reference + ".response",
        authority_effect="NONE",
        packet_id=str(req_payload.get("communication_id")) + ":response:" + organization_slug(local_org),
    )


def collect_ecosystem_responses(origin_org: str, communication_id: str, *,
                                mesh_root: Path | None = None) -> dict[str, Any]:
    responses = []
    for item in scan_addressed_frames(origin_org, root=mesh_root):
        packet = recover_packet(item["frame"])
        payload = packet.get("payload") or {}
        if payload.get("communication_id") != communication_id:
            continue
        if payload.get("message_class") not in {
                "ecosystem.monitor.response", "ecosystem.work.ack", "ecosystem.communication.ack"}:
            continue
        body = payload.get("body") or {}
        responses.append({
            "organization": body.get("responding_organization") or packet["origin"]["org"],
            "message_class": payload.get("message_class"),
            "request_packet_id": body.get("request_packet_id"),
            "application_result": body.get("application_result"),
            "receipt_terminal": body.get("receipt_terminal"),
            "response_packet_id": packet["packet_id"],
            "frame_sha256": item["frame"].get("frame_sha256"),
        })
    dedup = {row["organization"]: row for row in responses}
    rows = [dedup[key] for key in sorted(dedup)]
    return {"communication_id": communication_id, "response_count": len(rows), "organizations": rows}


def _boundary_local_selection(payload: Any) -> dict[str, Any]:
    processing = payload.get("processing") if isinstance(payload, dict) else None
    capability = processing.get("capability") if isinstance(processing, dict) else None
    route_id = processing.get("route_id") if isinstance(processing, dict) else None
    return {"processing_selection": BOUNDARY_LOCAL_SELECTION,
            "declared_capability": capability if isinstance(capability, str) else None,
            "declared_route_id": route_id if isinstance(route_id, str) else None,
            "declared_capability_processed": False,
            "route_admissibility": "NOT_RESOLVED_AT_BOUNDARY_ROUTE_OWNER_IS_SDK"}


def dispatch(root: Path, packet: dict[str, Any], *, mesh_root: Path | None = None,
             node_state: Any | None = None, epoch: int | None = None) -> dict[str, Any]:
    """Dispatch one recovered packet.

    Only ``BOUNDARY_LOCAL_CAPABILITY_INGRESS`` is dispatched here. Other roles
    stay on their existing surfaces. A capability address whose resolver refuses
    the submission is raised, not recorded as a consumed crossing: the receiving
    operation records its own refusal when it is the one that saw the payload.
    """
    del node_state
    registry = load_registry(root)
    if packet["destination"]["org"] != registry["organization"]:
        raise ValueError("wrong_destination_org")
    service = next((item for item in registry["services"]
                    if item["service_id"] == packet["destination"]["service"]), None)
    if service is None:
        raise ValueError("unknown_service")
    standing = packet.get("standing")
    if not isinstance(standing, dict):
        raise ValueError("node_standing_refused:STANDING_IS_A_CONSTRAINT_VARIABLE")
    role = service.get("boundary_role")
    if role != CAPABILITY_INGRESS_ROLE:
        raise ValueError("endpoint_adapter_not_installed")
    selected = _boundary_local_selection(packet.get("payload"))
    previous = None
    receipts = []
    for kind in ("INGRESS_ACCEPTED", "DISPATCHED", "CONSUMED", "RESULT_BOUND", "EGRESS_EMITTED"):
        item = receipt(kind, packet["packet_id"], service["service_id"], previous,
                       {"payload_hash": sha(packet["payload"])})
        receipts.append(item)
        previous = item["receipt_id"]
    try:
        application_result = capability_ingress(root).receive(
            root, service, packet, registry=registry, mesh_root=mesh_root, hb_epoch=epoch)
    except SystemExit as refused:
        raise ValueError("capability_ingress_refused:" + str(refused)) from None
    constraint = (packet.get("transition") or {}).get("authority_effect")
    return {"schema_version": SCHEMA, "organization": registry["organization"],
            "packet_id": packet["packet_id"], "service_id": service["service_id"],
            "consumed": True, "application_result": application_result,
            "authority_constraint": constraint, "time_constraint": "HEARTBEAT_EPOCH",
            "disposition_is_transition_evidence": True, "standing_rechecked_here": False,
            **selected, "receipts": receipts,
            "reconstruction": {"same_execution_required": True, "status": "RECONSTRUCTED",
                               "terminal_receipt_id": previous}}


CARRIER_EPOCH_RETRY_ENTRYPOINT = ("org-kernel/kernel.py::ingest_frame: the sender re-publishes the packet "
                                  "with publish_packet(..., epoch=<digits >= 32>)")


def ingest_frame(root: Path, frame: dict[str, Any], *, mesh_root: Path | None = None) -> dict[str, Any]:
    # The carrier epoch is checked before the frame is recovered, so an absent,
    # invalid or clock-derived one is the six-field refusal, not a bare ValueError.
    reference = frame.get("heartbeat_reference")
    epoch = reference.get("epoch") if isinstance(reference, dict) else None
    failure = supplied_epoch_failure(epoch, reference)
    if failure is None and isinstance(reference, dict) and any(key in reference for key in EPOCH_PROVENANCE_KEYS):
        # A carrier cannot assert CARRIED: only a writer verifying the ledger head records it.
        failure = (CARRIED_PROVENANCE_UNVERIFIED, "a carrier frame asserts CARRIED provenance "
                   + repr({key: reference.get(key) for key in EPOCH_PROVENANCE_KEYS}))
    if failure is not None:
        # The writer's six-field shape, with the carrier's own failure code and detail.
        refusal = writer_epoch_refusal(None, retry_entrypoint=CARRIER_EPOCH_RETRY_ENTRYPOINT)
        refusal["failure_code"], refusal["detail"] = failure
        raise SuppliedEpochRefused(refusal)
    packet = recover_packet(frame)
    registry = load_registry(root)
    if frame["destination_org"] != registry["organization"]:
        return {"status": "IGNORED_NOT_ADDRESSED", "packet_id": frame["packet_id"]}
    result = dispatch(root, packet, mesh_root=mesh_root, epoch=int(str(epoch)))
    return {"status": "CONSUMED", "packet": packet, "execution_result": result}


QUARANTINE_DIRECTORY = "quarantine.d"
FRAME_REFUSED_QUARANTINED = "FRAME_REFUSED_QUARANTINED"


def quarantine_frame(item: dict[str, Any], refusal: dict[str, Any], *, mesh_root: Path | None = None) -> dict[str, Any]:
    """Write the terminal seen marker of a refused frame and return it. It is a marker, not a receipt.

    It carries the refusal's six fields. It is write-once and keyed by the frame
    file, so a later scan skips the frame instead of refusing it on every cycle.
    No clock is read.
    """
    resolved, _provenance = resolve_federation_root(mesh_root)
    frame = item["frame"]
    marker = {"schema": "stegverse.frame-quarantine/v1", "status": FRAME_REFUSED_QUARANTINED,
              "terminal": True, "frame": item["name"], "frame_sha256": frame.get("frame_sha256"),
              "packet_id": frame.get("packet_id"), "is_a_receipt": False,
              **{key: refusal[key] for key in ("disposition", "failure_code", "failed_predicate", "detail",
                                               "required_evidence_or_repair", "retry_entrypoint",
                                               "owning_existing_goal", "next_attempt")},
              "derived_from_clock": False, "consequence_committed": False}
    path = resolved / QUARANTINE_DIRECTORY / item["name"]
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(marker, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    if not path.is_file():
        path.write_text(rendered, encoding="utf-8")
    return marker


def consume_addressed_frames(repo_root: Path, *, mesh_root: Path | None = None,
                             seen: set[str] | None = None, node_state_root: Path | None = None,
                             repo_ledger_root: Path | None = None,
                             org_ledger_root: Path | None = None) -> list[dict[str, Any]]:
    """Consume frames addressed to this organization.

    A capability-address crossing is not receipted again here. The receiving
    operation records both ledger levels. ``repo_ledger_root`` and
    ``org_ledger_root`` are accepted so a caller can name the redirected stores;
    this function does not write them.
    """
    del node_state_root, repo_ledger_root, org_ledger_root
    registry = load_registry(repo_root)
    seen = seen if seen is not None else set()
    results = []
    for item in scan_addressed_frames(registry["organization"], root=mesh_root, seen=seen):
        try:
            result = ingest_frame(repo_root, item["frame"], mesh_root=mesh_root)
        except SuppliedEpochRefused as refused:
            # A refused frame is terminal: quarantined once, marked seen, and the batch goes on.
            result = quarantine_frame(item, refused.refusal, mesh_root=mesh_root)
        seen.add(item["name"])
        results.append({"path": item["path"], "result": result, "organization_record": None})
    return results
