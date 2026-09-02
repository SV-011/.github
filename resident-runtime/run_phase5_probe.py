#!/usr/bin/env python3
from __future__ import annotations
import argparse, importlib.util, json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def load_module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

intr=load_module("intr_transport",ROOT/"org-boundary/runtime/intr_transport.py")
adapter=load_module("denial_adapter",ROOT/"org-boundary/runtime/denial_adapter.py")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--request",required=True)
    ap.add_argument("--evidence-dir",required=True)
    ap.add_argument("--runtime-observer",required=True)
    a=ap.parse_args()
    req=json.loads(Path(a.request).read_text(encoding="utf-8"))
    outdir=Path(a.evidence_dir); outdir.mkdir(parents=True,exist_ok=True)
    envelope=intr.build_ingress(
      {"org":"SV-011","service":"resident-observer"},
      {"org":"SV-011","service":req["destination_service"]},
      req["payload"],req["carrier_reference"],req["transition_reference"],req.get("authority_effect","NONE"),req["request_id"]
    )
    intr.validate_org_crossing(envelope,"INGRESS")
    result=adapter.process(envelope)
    evidence={
      "schema":"stegverse.sv011-resident-boundary-observation/v0.1",
      "entity_id":"SV-011",
      "request_id":req["request_id"],
      "runtime_observer":a.runtime_observer,
      "github_actions_is_runtime_authority":False,
      "result":result
    }
    if result["decision"]=="ALLOW":
      execution=result["execution"]
      egress=intr.build_egress(envelope,execution)
      intr.validate_org_crossing(egress,"EGRESS")
      evidence["egress"]=egress
    Path(outdir/(req["request_id"]+".json")).write_text(json.dumps(evidence,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({"request_id":req["request_id"],"decision":result["decision"],"evidence":str(outdir/(req["request_id"]+".json"))}))
    return 0
if __name__=="__main__": raise SystemExit(main())
