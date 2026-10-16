from __future__ import annotations

import json
from pathlib import Path

from app.schema import SCHEMA_VERSION, OverlayEvent, overlay_json_schema

DOC = Path(__file__).resolve().parents[1] / "docs" / "overlay-schema.json"


def test_every_overlay_round_trips_through_schema(full_run):
    _, timeline = full_run
    total = 0
    for overlays in timeline.values():
        for o in overlays:
            dumped = o.model_dump(mode="json")
            again = OverlayEvent.model_validate(dumped)
            assert again.model_dump(mode="json") == dumped
            assert dumped["schema_version"] == SCHEMA_VERSION
            json.dumps(dumped)
            total += 1
    assert total > 300


def test_overlays_are_timed_and_ids_unique(full_run):
    _, timeline = full_run
    for overlays in timeline.values():
        ids = [o.id for o in overlays]
        assert len(ids) == len(set(ids))
        times = [o.clock.t for o in overlays]
        assert times == sorted(times)
        for o in overlays:
            if o.display.slot == "pitch_anchor":
                assert o.display.anchor is not None


def test_committed_schema_doc_is_current():
    assert DOC.exists(), "run: python scripts/export_schema.py"
    assert json.loads(DOC.read_text()) == overlay_json_schema()


def test_export_script_regenerates_identical_schema(capsys):
    import runpy

    before = DOC.read_text()
    runpy.run_path(str(DOC.parents[1] / "scripts" / "export_schema.py"), run_name="__main__")
    assert DOC.read_text() == before and "wrote" in capsys.readouterr().out
