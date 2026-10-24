#!/usr/bin/env python3
import argparse, hashlib, json, os, sys, tempfile
from pathlib import Path
KINDS=["INGRESS_ACCEPTED","DISPATCHED","CONSUMED","RESULT_BOUND","EGRESS_EMITTED"]
def canon(v): return json.dumps(v,sort_keys=True,separators=(",",":")).encode()
def hid(prefix,v): return prefix+"-"+hashlib.sha256(canon(v)).hexdigest()[:24]
def load(path): return json.loads(Path(path).read_text())
def fault(code,predicate,repair,terminal,status):
 rec={"failure_code":code,"failed_predicate":predicate,"required_evidence_or_repair":repair,"retry_entrypoint":"python3 org-boundary/runtime/process_boundary.py "+" ".join(sys.argv[1:]),"owning_existing_goal":"org-boundary execution (.github/workflows/org-boundary-execution.yml)","next_attempt":"re-run retry_entrypoint after the repair; no partial --out file was left" if not terminal else "none until the input is repaired; re-running unchanged input fails identically","terminal":terminal}
 sys.stderr.write(json.dumps(rec,sort_keys=True)+"\n"); raise SystemExit(status)
def load_input(path,name):
 try: v=load(path)
 except (OSError,ValueError,RecursionError,MemoryError) as e: fault("PROCESS_BOUNDARY_INPUT_FAULT","INPUT_READABLE_JSON_OBJECT","provide a readable UTF-8 JSON object for --"+name+" ("+type(e).__name__+")",True,65)
 if not isinstance(v,dict): fault("PROCESS_BOUNDARY_INPUT_FAULT","INPUT_READABLE_JSON_OBJECT","--"+name+" must be a JSON object, got "+type(v).__name__,True,65)
 return v
def write_atomic(out,text):
 out=Path(out); out.parent.mkdir(parents=True,exist_ok=True); fd,tmp=tempfile.mkstemp(dir=out.parent,prefix="."+out.name+".",suffix=".tmp")
 try:
  with os.fdopen(fd,"w") as f: f.write(text)
  m=os.umask(0); os.umask(m); os.chmod(tmp,0o666&~m)
  os.replace(tmp,out)
 except BaseException:
  try: os.unlink(tmp)
  except OSError: pass
  raise
def main():
 ap=argparse.ArgumentParser(); ap.add_argument("--envelope",required=True); ap.add_argument("--registry",default="org-boundary/registry/services.json"); ap.add_argument("--out",required=True); a=ap.parse_args()
 env=load_input(a.envelope,"envelope"); reg=load_input(a.registry,"registry")
 req=["schema_version","packet_id","direction","origin","destination","carrier","intr_profile","transition","payload","evidence"]
 def bad(predicate,repair): fault("PROCESS_BOUNDARY_INPUT_FAULT",predicate,repair,True,65)
 miss=[k for k in req if k not in env]
 if miss: bad("ENVELOPE_HAS_REQUIRED_FIELDS","add the missing --envelope fields: "+",".join(miss))
 dst,tr=env["destination"],env["transition"]
 if not isinstance(dst,dict) or not isinstance(dst.get("org"),str) or not isinstance(dst.get("service"),str): bad("ENVELOPE_HAS_REQUIRED_FIELDS","--envelope destination must be an object with string org and service")
 if not isinstance(tr,dict) or "authority_effect" not in tr: bad("ENVELOPE_HAS_REQUIRED_FIELDS","--envelope transition must be an object with authority_effect")
 svcs=reg.get("services")
 if not isinstance(reg.get("organization"),str) or not isinstance(svcs,list) or not all(isinstance(s,dict) and isinstance(s.get("service_id"),str) for s in svcs): bad("REGISTRY_HAS_REQUIRED_FIELDS","--registry must be an object with string organization and services: a list of objects with string service_id")
 if dst["org"]!=reg["organization"]: bad("DESTINATION_ORG_MATCHES_REGISTRY","destination.org "+json.dumps(dst["org"])+" must equal registry organization "+json.dumps(reg["organization"]))
 svc=next((s for s in svcs if s["service_id"]==dst["service"]),None)
 if not svc: bad("SERVICE_REGISTERED","destination.service "+json.dumps(dst["service"])+" is not a service_id in --registry")
 if svc.get("boundary_role")!="BOUNDARY_LOCAL_DIAGNOSTIC": bad("ENDPOINT_ADAPTER_INSTALLED","service "+json.dumps(svc["service_id"])+" has boundary_role "+json.dumps(svc.get("boundary_role"))+"; only BOUNDARY_LOCAL_DIAGNOSTIC has an installed endpoint adapter")
 try: text,prev=build(env,reg,svc)
 except (RecursionError,MemoryError) as e: bad("INPUT_READABLE_JSON_OBJECT","reduce the nesting depth of --envelope ("+type(e).__name__+")")
 try: write_atomic(a.out,text)
 except OSError as e: fault("PROCESS_BOUNDARY_WRITE_FAULT","OUT_PATH_WRITABLE","make --out a writable file path whose parent is a writable directory ("+type(e).__name__+")",False,75)
 print(json.dumps({"status":"PASS","terminal_receipt_id":prev}))
def build(env,reg,svc):
 base={"packet_id":env["packet_id"],"service_id":svc["service_id"],"payload_hash":hashlib.sha256(canon(env["payload"])).hexdigest()}
 receipts=[]; prev=None
 for kind in KINDS:
  subject={**base,"kind":kind,"previous_receipt_id":prev}; rid=hid(kind.lower(),subject)
  receipts.append({"kind":kind,"receipt_id":rid,"subject":svc["service_id"],"evidence_hash":hashlib.sha256(canon(subject)).hexdigest(),"previous_receipt_id":prev}); prev=rid
 result={"schema_version":reg["organization"].lower().replace(" ","-")+".boundary-execution.v1","packet_id":env["packet_id"],"organization":reg["organization"],"service_id":svc["service_id"],"consumed":True,"application_result":{"echo":env["payload"]},"authority_effect":env["transition"]["authority_effect"],"receipts":receipts,"reconstruction":{"same_execution_required":True,"status":"RECONSTRUCTED","terminal_receipt_id":prev}}
 return json.dumps(result,indent=2,sort_keys=True)+"\n",prev
if __name__=="__main__": main()
