"""Data export formats and changelog parsing stay usable."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

from pi_guard.core.changelog import recent
from pi_guard.storage.db import Database
from pi_guard.storage.persons import Persons
from pi_guard.web.api_export import FORMAT_VERSION, as_csv_zip, as_text, collect_all


def test_changelog_reads_repo() -> None:
    entries = recent(2)
    assert entries
    assert entries[0]["version"]
    assert entries[0]["body"]


def test_export_formats_include_persons(tmp_path: Path) -> None:
    db = Database(tmp_path / "e.db")
    Persons(db).save(None, {"display_name": "Sam", "roles": ["household"]})
    app = SimpleNamespace(
        db=db,
        config=SimpleNamespace(file=SimpleNamespace(cameras=[])),
        addons=SimpleNamespace(medical=None, blocks=None, agents=None),
        faces=None,
    )
    d = SimpleNamespace(app=app, profiles=SimpleNamespace(all=lambda: {}), wg_peers=None)
    data = collect_all(d)
    assert any(p["display_name"] == "Sam" for p in data["persons"])
    text = as_text(data)
    assert "persons" in text and "Sam" in text
    bundle = as_csv_zip(data)
    with zipfile.ZipFile(io.BytesIO(bundle)) as zf:
        names = zf.namelist()
        assert "persons.csv" in names
        assert "events.csv" in names
        rows = list(csv.DictReader(io.StringIO(zf.read("persons.csv").decode())))
        assert rows and rows[0]["display_name"] == "Sam"
    payload = json.loads(json.dumps(data, default=str))
    assert "persons" in payload
    assert FORMAT_VERSION == 1
    db.close()
