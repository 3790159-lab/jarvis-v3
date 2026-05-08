from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from jarvis_stage3_artifacts.generated_modules.dependency_bundle_probe_depplan_20260423_223408_3bdd4117 import run_patch_probe


def main() -> int:
    result = run_patch_probe()
    payload = {
        'plan_id': result.plan_id,
        'goal': result.goal,
        'task': result.task,
        'status': result.status,
        'created_at': result.created_at,
    }
    out = PROJECT_ROOT / 'jarvis_stage3_artifacts' / 'temp' / f'{result.plan_id}_verify_result.json'
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result.status == 'ok' else 1


if __name__ == '__main__':
    raise SystemExit(main())
