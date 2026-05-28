"""Tests for teaching modes."""
from pdf_tutor.learning import modes


def test_modes_have_required_fields():
    for name, cfg in modes.MODES.items():
        assert "icon" in cfg, f"{name} missing icon"
        assert "sys" in cfg and cfg["sys"], f"{name} missing system prompt"
        assert "user" in cfg, f"{name} missing user prompt"
        assert "followups" in cfg, f"{name} missing followups"


def test_vark_modes_present():
    names = list(modes.MODES.keys())
    joined = " ".join(names).lower()
    assert "visual" in joined
    assert "auditory" in joined
    assert "read/write" in joined or "read" in joined
    assert "kinesthetic" in joined
    assert "omni" in joined
