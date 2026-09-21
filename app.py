"""Streamlit-app voor het verschuiven en spiegelen van D-Stability-bestanden."""

from __future__ import annotations

import tempfile
import warnings
from pathlib import Path
from typing import Any, Iterable

import streamlit as st
from pydantic import BaseModel

try:
    from geolib.models.dstability import DStabilityModel
except ImportError:
    from geolib.models.dstability.dstability_model import DStabilityModel

warnings.filterwarnings(
    "ignore",
    category=UserWarning,
    module="requests",
)

RESULT_KEY = "dstability_result"
RESULT_SCHEMA_VERSION = 3
OPERATION_SHIFT = "Verschuiven"
OPERATION_MIRROR = "Spiegelen"


def iter_items(value: Any) -> Iterable:
    """Geef een lege iterable terug wanneer een collectie ontbreekt."""
    return value or ()


def clear_result() -> None:
    """Verwijder een eerder resultaat wanneer de invoer wijzigt."""
    st.session_state.pop(RESULT_KEY, None)


def get_valid_result() -> dict[str, Any] | None:
    """Lees alleen een volledig resultaat van de huidige schemaversie."""
    result = st.session_state.get(RESULT_KEY)
    required_keys = {
        "schema_version",
        "operation",
        "data",
        "file_name",
        "message",
        "min_x",
        "max_x",
    }

    if not isinstance(result, dict):
        clear_result()
        return None

    if not required_keys.issubset(result):
        clear_result()
        return None

    if result.get("schema_version") != RESULT_SCHEMA_VERSION:
        clear_result()
        return None

    return result


def collect_geometry_x(model: DStabilityModel) -> list:
    """Verzamel alle X-coordinaten uit de geometrie."""
    x_values: list[float] = []

    geometries = getattr(
        model.datastructure,
        "geometries",
        None,
    )

    for geometry in iter_items(geometries):
        layers = getattr(geometry, "Layers", None)

        for layer in iter_items(layers):
            points = getattr(layer, "Points", None)

            for point in iter_items(points):
                x_values.append(float(point.X))

    return x_values


def shift_points(points: Any, x_shift: float) -> None:
    """Verschuif een optionele verzameling punten over de X-as."""
    for point in iter_items(points):
        point.X += x_shift


def shift_search_grid(
    settings: Any,
    attribute: str,
    x_shift: float,
) -> None:
    """Verschuif de oorsprong van een optioneel zoekraster."""
    method_settings = getattr(settings, attribute, None)
    search_grid = getattr(
        method_settings,
        "SearchGrid",
        None,
    )
    origin = getattr(search_grid, "Origin", None)

    if (
        origin is not None
        and getattr(origin, "X", None) is not None
    ):
        origin.X += x_shift


def shift_model_x(
    model: DStabilityModel,
    new_origin: float,
) -> tuple[float, float]:
    """Verschuif alle ondersteunde X-coordinaten in het model."""
    x_values = collect_geometry_x(model)

    if not x_values:
        raise ValueError(
            "Geen geometrie gevonden in het D-Stability-bestand."
        )

    x_shift = -float(new_origin)
    datastructure = model.datastructure

    geometries = getattr(
        datastructure,
        "geometries",
        None,
    )
    for geometry in iter_items(geometries):
        layers = getattr(geometry, "Layers", None)

        for layer in iter_items(layers):
            shift_points(
                getattr(layer, "Points", None),
                x_shift,
            )

    waternets = getattr(
        datastructure,
        "waternets",
        None,
    )
    for waternet in iter_items(waternets):
        head_lines = getattr(
            waternet,
            "HeadLines",
            None,
        )

        for head_line in iter_items(head_lines):
            shift_points(
                getattr(head_line, "Points", None),
                x_shift,
            )

    loads = getattr(
        datastructure,
        "loads",
        None,
    )
    for load in iter_items(loads):
        shift_points(
            getattr(load, "Points", None),
            x_shift,
        )

        for attribute in ("X", "XEnd"):
            value = getattr(load, attribute, None)

            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
            ):
                setattr(
                    load,
                    attribute,
                    value + x_shift,
                )

    stages = getattr(datastructure, "Stages", None)

    if stages is None:
        stages = getattr(
            datastructure,
            "stages",
            None,
        )

    search_grid_attributes = (
        "BishopBruteForceSettings",
        "BishopSettings",
        "SpencerSettings",
        "SpencerGeneticSettings",
        "UpliftVanSettings",
        "UpliftVanMethodParticleSwarmSettings",
    )

    for stage in iter_items(stages):
        settings = getattr(
            stage,
            "CalculationSettings",
            None,
        )

        if settings is None:
            continue

        for attribute in search_grid_attributes:
            shift_search_grid(
                settings,
  
