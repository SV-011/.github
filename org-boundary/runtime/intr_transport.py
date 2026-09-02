#!/usr/bin/env python3
import hashlib, json, uuid
from datetime import datetime, timezone

def _canon(v):
    return json.dumps(v,sort_keys=True,separators=(",",":")).encode()

def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00","Z")

def build_ingress(origin,destination,payload,carrier_reference,transition_reference,authority_effect="NONE",packet_id=None):
    return {
      "schema_version":"stegverse.intr.org-boundary.v1",
      "packet_id":packet_id or str(uuid.uuid4()),
      "direction":"INGRESS",
      "origin":origin,
      "destination":destination,
      "carrier":{"kind":"HB_DERIVED","reference":carrier_reference,"observed_at":_now()},
      "intr_profile":"stegverse.intr.org-boundary.v1",
      "transition":{"reference":transition_reference,"authority_effect":authority_effect,"conditions":[]},
      "payload":payload,
      "evidence":{"ingress_receipt":None,"dispatch_receipt":None,"consumption_receipt":None,"egress_receipt":None,"reconstruction_reference":None}
    }

def build_egress(ingress, execution_result):
    payload={"request_packet_id":ingress["packet_id"],"execution_result":execution_result}
    return {
      "schema_version":"stegverse.intr.org-boundary.v1",
      "packet_id":ingress["packet_id"]+":egress",
      "direction":"EGRESS",
      "origin":ingress["destination"],
      "destination":ingress["origin"],
      "carrier":{"kind":ingress["carrier"]["kind"],"reference":ingress["carrier"]["reference"],"observed_at":_now()},
      "intr_profile":ingress["intr_profile"],
      "transition":{"reference":ingress["transition"]["reference"],"authority_effect":ingress["transition"]["authority_effect"],"conditions":ingress["transition"].get("conditions",[])},
      "payload":payload,
      "evidence":{
        "ingress_receipt":execution_result["receipts"][0]["receipt_id"],
        "dispatch_receipt":execution_result["receipts"][1]["receipt_id"],
        "consumption_receipt":execution_result["receipts"][2]["receipt_id"],
        "egress_receipt":execution_result["receipts"][-1]["receipt_id"],
        "reconstruction_reference":execution_result["reconstruction"]["terminal_receipt_id"]
      },
      "payload_hash":hashlib.sha256(_canon(payload)).hexdigest()
    }

def validate_org_crossing(envelope, expected_direction):
    if envelope.get("direction") != expected_direction:
        raise ValueError("wrong-direction")
    for key in ("packet_id","origin","destination","carrier","intr_profile","transition","payload","evidence"):
        if key not in envelope:
            raise ValueError("missing-"+key)
    return True
