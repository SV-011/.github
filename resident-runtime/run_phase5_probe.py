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

def input_refusal(exc):
    return {"disposition":"FAIL_CLOSED",
            "failure_code":"PHASE5_PROBE_INPUT_"+("UNREADABLE" if isinstance(exc,OSError) else "INVALID"),
            "failed_predicate":"SUPPLIED_REQUEST_IS_A_READABLE_VALID_FILE",
            "detail":"--request: "+type(exc).__name__+": "+str(exc),
            "required_evidence_or_repair":"supply --request as a readable phase-5 request JSON",
            "retry_entrypoint":"resident-runtime/run_phase5_probe.py --request <file>",
            "owning_existing_goal":"INTERLOCK-INTR-RUNTIME-INDEPENDENT-PROTOCOL-001",
            "next_attempt":"none with this input; nothing was written",
            "terminal":True,"derived_from_clock":False,"consequence_committed":False}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--request",required=True)
    ap.add_argument("--evidence-dir",required=True)
    ap.add_argument("--runtime-observer",required=True)
    a=ap.parse_args()
    outdir=Path(a.evidence_dir)
    # One input stage: an unreadable or invalid --request is terminal six fields; nothing is written.
    try:
      req=json.loads(Path(a.request).read_text(encoding="utf-8"))
      envelope=intr.build_ingress(
        {"org":"SV-011","service":"resident-observer"},
        {"org":"SV-011","service":req["destination_service"]},
        req["payload"],req["carrier_reference"],req["transition_reference"],req.get("authority_effect","NONE"),req["request_id"]
      )
      intr.validate_org_crossing(envelope,"INGRESS")
    except (OSError,ValueError,KeyError,TypeError,AttributeError,RecursionError,MemoryError) as exc:
      print(json.dumps(input_refusal(exc),sort_keys=True))
      return 1
    result=adapter.process(envelope)
    if result["decision"]=="FAIL_CLOSED":
      # An own-state write fault (process_boundary exit 75): non-terminal, so no evidence is written and the
      # request stays pending; the next run produces its evidence once.
      print(json.dumps(result["fault"],sort_keys=True))
      return adapter.WRITE_FAULT_EXIT
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
    try:
      outdir.mkdir(parents=True,exist_ok=True)
      Path(outdir/(req["request_id"]+".json")).write_text(json.dumps(evidence,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    except OSError as exc:
      # Six fields, non-terminal: the evidence file could not be written; nothing else was (F-WRITER-TRACE).
      print(json.dumps({"disposition":"FAIL_CLOSED","failure_code":"RESULT_WRITE_FAILED",
                        "failed_predicate":"OUT_FILE_ACCEPTS_THE_RESULT:"+type(exc).__name__,
                        "detail":type(exc).__name__+": "+str(exc),
                        "required_evidence_or_repair":"an --evidence-dir that accepts a file write",
                        "retry_entrypoint":"resident-runtime/run_phase5_probe.py --evidence-dir <writable dir>",
                        "owning_existing_goal":"INTERLOCK-INTR-RUNTIME-INDEPENDENT-PROTOCOL-001",
                        "next_attempt":"re-run the same invocation; it writes only this file",
                        "terminal":False,"derived_from_clock":False,"consequence_committed":False},sort_keys=True))
      return 1
    print(json.dumps({"request_id":req["request_id"],"decision":result["decision"],"evidence":str(outdir/(req["request_id"]+".json"))}))
    return 0
if __name__=="__main__": raise SystemExit(main())
