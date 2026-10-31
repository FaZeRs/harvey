"""skills/house_style.md: the operator's own style rules, optional."""
import logging

import harvey.brain as brain_mod
from harvey.brain import Brain


def _skills(tmp_path, monkeypatch, agent, house_style=None):
    (tmp_path / "product_knowledge.md").write_text("PRODUCT")
    if house_style is not None:
        (tmp_path / "house_style.md").write_text(house_style)
    monkeypatch.setattr(brain_mod, "SKILLS_DIR", tmp_path)
    return Brain.load_skills_for_agent(object.__new__(Brain), agent)


def test_writer_and_handler_load_house_style(tmp_path, monkeypatch):
    for agent in ("writer", "handler"):
        assert "Write in Latvian" in _skills(tmp_path, monkeypatch, agent, "Write in Latvian")


def test_scout_does_not_load_house_style(tmp_path, monkeypatch):
    assert "Write in Latvian" not in _skills(tmp_path, monkeypatch, "scout", "Write in Latvian")


def test_missing_house_style_is_silent(tmp_path, monkeypatch, caplog):
    with caplog.at_level(logging.WARNING, logger="harvey.brain"):
        out = _skills(tmp_path, monkeypatch, "writer")
    assert "PRODUCT" in out
    assert "house_style.md" not in caplog.text
