"""Fixtures communes."""

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Active le chargement de `custom_components/` dans chaque test."""
    yield
