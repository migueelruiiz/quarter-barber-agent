"""
Permanent regression suite for config.py.

config.py is the single source of truth for business data that, if wrong,
causes real booking errors or wrong prices quoted to real customers (see
config.py's module docstring and CLAUDE.md). This suite must be run before
every commit that touches config.py.
"""

import ast
import pathlib

import pytest

import config


CONFIG_PATH = pathlib.Path(config.__file__)


# ---------------------------------------------------------------------------
# render_price_menu()
# ---------------------------------------------------------------------------

def test_render_price_menu_does_not_raise():
    config.render_price_menu()


def test_render_price_menu_returns_non_empty_string():
    result = config.render_price_menu()
    assert isinstance(result, str)
    assert result.strip() != ""


# These two tests assert against hardcoded literal strings rather than
# recomputing render_price_menu()'s own formatting expressions. This is
# deliberate: a legitimate price change in PRICE_MENU will make these tests
# fail and require a human to update the literals here too, rather than a
# formatting bug silently passing because the test recomputed the same
# (possibly-broken) expression the implementation uses.

def test_render_price_menu_formats_multiword_names_with_title_case():
    result = config.render_price_menu()
    assert "Corte Fade: 15.00€" in result
    assert "Corte Infantil: 12.00€" in result
    assert "Corte Jubilado: 12.00€" in result
    assert "Arreglo Barba: 10.00€" in result
    assert "Colores Fantasia: 50.00€" in result


def test_render_price_menu_formats_prices_with_two_decimals_and_euro_sign():
    result = config.render_price_menu()
    assert "Corte: 15.00€" in result
    assert "Afeitado: 15.00€" in result
    assert "Mechas: 30.00€" in result
    assert "Color: 20.00€" in result


# ---------------------------------------------------------------------------
# Referential integrity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("barber_name", list(config.BARBERS))
def test_barber_eligible_services_exist_in_services(barber_name):
    eligible_services = config.BARBERS[barber_name]["eligible_services"]
    for service_name in eligible_services:
        assert service_name in config.SERVICES


@pytest.mark.parametrize("menu_item_name", list(config.PRICE_MENU))
def test_price_menu_duration_category_exists_in_services(menu_item_name):
    duration_category = config.PRICE_MENU[menu_item_name]["duration_category"]
    assert duration_category in config.SERVICES


# ---------------------------------------------------------------------------
# colorId uniqueness
# ---------------------------------------------------------------------------

def test_barber_color_ids_are_unique_when_not_none():
    color_ids = [
        data["color_id"]
        for data in config.BARBERS.values()
        if data["color_id"] is not None
    ]
    assert len(color_ids) == len(set(color_ids))


# ---------------------------------------------------------------------------
# SENIORITY_ORDER
# ---------------------------------------------------------------------------

def test_seniority_order_has_no_duplicates():
    assert len(config.SENIORITY_ORDER) == len(set(config.SENIORITY_ORDER))


def test_seniority_order_matches_barbers_keys_exactly():
    assert set(config.SENIORITY_ORDER) == set(config.BARBERS)


# ---------------------------------------------------------------------------
# WORKING_HOURS
# ---------------------------------------------------------------------------

def test_working_hours_has_exactly_monday_through_sunday_keys():
    assert set(config.WORKING_HOURS) == set(range(7))


# ---------------------------------------------------------------------------
# Zero internal imports (architectural constraint from config.py's docstring)
# ---------------------------------------------------------------------------

def _import_targets_from_source(source: str):
    tree = ast.parse(source)
    targets = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                targets.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            # node.level > 0 means a relative import (e.g. `from . import x`)
            if node.level > 0:
                targets.append("." * node.level + (node.module or ""))
            else:
                targets.append(node.module or "")
    return targets


def test_config_has_no_internal_project_imports():
    source = CONFIG_PATH.read_text()
    targets = _import_targets_from_source(source)
    offending = [
        target for target in targets
        if target.startswith("src") or target.startswith(".")
    ]
    assert offending == [], (
        f"config.py must have zero internal project imports, found: {offending}"
    )
