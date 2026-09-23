#!/usr/bin/env python3
"""
Validates a risk lookup table file against contracts/risk-lookup-table-format.md.

Run this the MOMENT Pair 1 hands off their real file — takes a few seconds
and tells you immediately whether it's usable, instead of finding out live
when the API silently falls back to mock data.

Usage:
    python validate_lookup_table.py data/processed/risk_lookup_koramangala.json
"""

import json
import sys
from datetime import datetime


def validate(path: str) -> bool:
    errors = []
    warnings = []

    try:
        raw = open(path).read()
    except FileNotFoundError:
        print(f"FAIL: file not found at {path}")
        return False

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"FAIL: not valid JSON — {e}")
        return False

    for key in ("ward_id", "generated_at", "timesteps"):
        if key not in data:
            errors.append(f"missing top-level key: '{key}'")

    if "timesteps" in data:
        if not isinstance(data["timesteps"], dict):
            errors.append("'timesteps' must be an object keyed by ISO8601 timestamp")
        elif len(data["timesteps"]) == 0:
            errors.append("'timesteps' is empty — no data to serve")
        else:
            all_segment_ids = None
            for ts, segments in data["timesteps"].items():
                try:
                    datetime.fromisoformat(ts)
                except ValueError:
                    errors.append(f"timestep key '{ts}' is not valid ISO8601")

                if not isinstance(segments, list) or len(segments) == 0:
                    errors.append(f"timestep '{ts}' has no segments")
                    continue

                seg_ids_here = set()
                for seg in segments:
                    for field in ("segment_id", "geometry", "risk_score"):
                        if field not in seg:
                            errors.append(f"timestep '{ts}': segment missing required field '{field}'")
                    if "risk_score" in seg:
                        rs = seg["risk_score"]
                        if not isinstance(rs, (int, float)) or not (0 <= rs <= 1):
                            errors.append(
                                f"timestep '{ts}', segment '{seg.get('segment_id','?')}': "
                                f"risk_score={rs} is not a number in [0, 1]"
                            )
                    if "predicted_depth_cm" not in seg:
                        warnings.append(f"segment '{seg.get('segment_id','?')}' has no predicted_depth_cm (optional, but frontend should handle null)")
                    if "confidence" not in seg:
                        warnings.append(f"segment '{seg.get('segment_id','?')}' has no confidence (optional)")
                    if "segment_id" in seg:
                        seg_ids_here.add(seg["segment_id"])

                # Check segment_id stability across timesteps — required by the contract
                # so the frontend's time slider can track the same segment over time.
                if all_segment_ids is None:
                    all_segment_ids = seg_ids_here
                elif seg_ids_here != all_segment_ids:
                    errors.append(
                        f"timestep '{ts}' has different segment_ids than earlier timesteps — "
                        f"segment_id must be stable across all timesteps (see contract)"
                    )

    print(f"Checked: {path}")
    print(f"  ward_id: {data.get('ward_id', '(missing)')}")
    print(f"  timesteps: {len(data.get('timesteps', {}))}")
    if data.get("timesteps"):
        first_ts = list(data["timesteps"].keys())[0]
        print(f"  segments per timestep: {len(data['timesteps'][first_ts])}")

    if warnings:
        print(f"\n{len(warnings)} warning(s) (non-blocking):")
        for w in warnings[:10]:
            print(f"  - {w}")
        if len(warnings) > 10:
            print(f"  ... and {len(warnings) - 10} more")

    if errors:
        print(f"\n{len(errors)} ERROR(s) — this file will NOT work with data_loader.py:")
        for e in errors[:15]:
            print(f"  - {e}")
        if len(errors) > 15:
            print(f"  ... and {len(errors) - 15} more")
        print("\nFAIL — send this back to Pair 1 with the errors above, "
              "or check contracts/risk-lookup-table-format.md together.")
        return False

    print("\nPASS — this file matches the contract. Drop it in data/processed/ and it will be served as-is.")
    return True


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python validate_lookup_table.py <path-to-json-file>")
        sys.exit(1)
    ok = validate(sys.argv[1])
    sys.exit(0 if ok else 1)
