"""Regenerate docs/overlay-schema.json from the pydantic models."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.schema import overlay_json_schema  # noqa: E402

out = ROOT / "docs" / "overlay-schema.json"
out.write_text(json.dumps(overlay_json_schema(), indent=2) + "\n")
print(f"wrote {out}")
