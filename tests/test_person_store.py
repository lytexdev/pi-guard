"""Central person model and migration roundtrip."""

from __future__ import annotations

from pathlib import Path

from pi_guard.storage.db import Database
from pi_guard.storage.persons import Persons


def test_person_crud_and_links(tmp_path: Path) -> None:
    db = Database(tmp_path / "p.db")
    store = Persons(db)
    pid = store.save(None, {"display_name": "Alex", "roles": ["trusted", "emergency"]})
    store.link(pid, "face", "Alex")
    store.link(pid, "web_account", "alex")
    person = store.get(pid)
    assert person is not None
    assert person.display_name == "Alex"
    assert set(person.roles) == {"trusted", "emergency"}
    assert ("face", "Alex") in person.links
    assert store.find_by_link("face", "Alex").id == pid
    store.unlink("face", "Alex")
    assert store.find_by_link("face", "Alex") is None
    assert store.delete(pid)
    db.close()


def test_migrate_existing_by_name(tmp_path: Path) -> None:
    db = Database(tmp_path / "m.db")
    store = Persons(db)
    store.migrate_existing(
        face_names=["Sam"],
        profiles=[{"username": "sam", "person": "Sam", "telegram": "123"}],
        contacts=[{"id": 7, "name": "Sam", "account": "sam", "note": "sis"}],
    )
    people = store.all()
    assert len(people) == 1
    links = {f"{k}:{r}" for k, r in people[0].links}
    assert "face:Sam" in links
    assert "web_account:sam" in links
    assert "emergency_contact:7" in links
    assert "messenger:telegram:123" in links
    assert "emergency" in people[0].roles
    store.migrate_existing(face_names=["Sam"], contacts=[{"id": 7, "name": "Sam"}])
    assert len(store.all()) == 1
    db.close()
