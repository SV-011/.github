#!/usr/bin/env python3
from __future__ import annotations
import json, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
data=json.loads((ROOT/"tools/tasks.json").read_text())
failed=[]
for task in data["tasks"]:
    if not task.get("enabled",True): continue
    p=subprocess.run(task["command"],cwd=ROOT)
    if p.returncode!=0: failed.append(task["id"])
if failed:
    print(json.dumps({"status":"FAIL","failed":failed}))
    raise SystemExit(1)
print(json.dumps({"status":"PASS","task_count":len([t for t in data["tasks"] if t.get("enabled",True)])}))
