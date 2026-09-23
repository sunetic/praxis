"""Concurrent scene resolution must receive complete, independent skill lists."""

import threading
from concurrent.futures import ThreadPoolExecutor

from app.skills.store import SkillStore


def test_concurrent_loads_do_not_consume_each_others_skills(tmp_path, monkeypatch):
    store = SkillStore(str(tmp_path / "custom"), str(tmp_path / "builtin"))
    store.create(
        name="always-required",
        version="1.0.0",
        description="Always applied constraints",
        database="general",
        always_apply=True,
        prompt="Preserve the user's constraints.",
    )
    parse = store._parse_skill_file
    both_reading = threading.Barrier(3)
    release = threading.Event()

    def paused_parse(path):
        value = parse(path)
        both_reading.wait(timeout=3)
        assert release.wait(3)
        return value

    monkeypatch.setattr(store, "_parse_skill_file", paused_parse)
    with ThreadPoolExecutor(max_workers=2) as workers:
        first, second = workers.submit(store.load), workers.submit(store.load)
        try:
            both_reading.wait(timeout=3)
            # Readers keep the last completed snapshot during the reload.
            assert store.get("always-required") is not None
        finally:
            release.set()
        results = [first.result(), second.result()]
    assert [[skill.name for skill in result] for result in results] == [
        ["always-required"],
        ["always-required"],
    ]
    assert results[0][0] is not results[1][0]
    assert store.errors == []
