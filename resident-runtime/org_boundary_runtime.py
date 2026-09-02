from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path
_p=Path(__file__).resolve().parents[1]/"org-boundary"/"runtime"/"intr_transport.py"
_s=spec_from_file_location("intr_transport",_p); _m=module_from_spec(_s); _s.loader.exec_module(_m)
build_egress=_m.build_egress
validate_org_crossing=_m.validate_org_crossing
