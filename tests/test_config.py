"""Tests for configuration integrity."""
from pdf_tutor import config


def test_all_providers_have_required_keys():
    for name, prov in config.PROVIDERS.items():
        assert "id" in prov, f"{name} missing 'id'"
        assert "needs_key" in prov, f"{name} missing 'needs_key'"
        assert "models" in prov and prov["models"], f"{name} has no models"
        assert "note" in prov, f"{name} missing 'note'"


def test_provider_ids_unique():
    ids = [p["id"] for p in config.PROVIDERS.values()]
    assert len(ids) == len(set(ids)), "Duplicate provider ids"


def test_local_provider_needs_no_key():
    ollama = next(p for p in config.PROVIDERS.values() if p["id"] == "ollama")
    assert ollama["needs_key"] is False
