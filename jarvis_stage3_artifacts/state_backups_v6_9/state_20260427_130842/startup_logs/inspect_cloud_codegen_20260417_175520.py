import json, importlib, inspect, pathlib

m = importlib.import_module("app.api.cloud_control")
module_file = getattr(m, "__file__", None)
text = pathlib.Path(module_file).read_text(encoding="utf-8", errors="ignore")
source = inspect.getsource(m.cloud_execute)

data = {
    "module_file": module_file,
    "file_has_SAFE_CLOUD_CODEGEN_V1": "SAFE_CLOUD_CODEGEN_V1" in text,
    "file_has_codegen_word": "codegen" in text,
    "source_has_codegen_branch": ('target == "codegen"' in source) or ("target == 'codegen'" in source),
    "target_map_keys": sorted(list(getattr(m, "TARGET_MAP", {}).keys())),
}
print(json.dumps(data, ensure_ascii=False, indent=2))