"""Streamlit-app voor X-transformaties van D-Stability-bestanden."""

from __future__ import annotations

import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import streamlit as st

X_ATTRIBUTES = (
    "x", "X", "x_left", "x_right", "XLeft", "XRight", "XCenter",
)
POINT_LIST_ATTRIBUTES = ("points", "Points", "point_ids", "PointIds")
SEARCH_GRID_ATTRIBUTES = (
    "BishopBruteForceSettings",
    "BishopSettings",
    "SpencerSettings",
    "SpencerGeneticSettings",
    "UpliftVanSettings",
    "UpliftVanMethodParticleSwarmSettings",
)


def iter_items(value: Any) -> Iterable[Any]:
    """Geef een lege iterable terug wanneer een collectie ontbreekt."""
    return value or ()


def collect_geometry_x(model: Any) -> list[float]:
    """Verzamel X-coordinaten uit alle geometrische lagen."""
    values: list[float] = []
    for geometry in iter_items(getattr(model.datastructure, "geometries", None)):
        for layer in iter_items(getattr(geometry, "Layers", None)):
            for point in iter_items(getattr(layer, "Points", None)):
                values.append(float(point.X))
    return values


def shift_points(points: Any, delta_x: float) -> None:
    """Verschuif een optionele verzameling punten."""
    for point in iter_items(points):
        point.X += delta_x


def shift_search_grid(settings: Any, name: str, delta_x: float) -> None:
    """Verschuif de oorsprong van een optioneel zoekraster."""
    method = getattr(settings, name, None)
    grid = getattr(method, "SearchGrid", None)
    origin = getattr(grid, "Origin", None)
    if origin is not None:
        origin.X += delta_x


def shift_model_x(model: Any, new_origin: float) -> tuple[float, float]:
    """Verschuif ondersteunde X-coordinaten en retourneer het nieuwe bereik."""
    original_x = collect_geometry_x(model)
    if not original_x:
        raise ValueError("Geen geometrie gevonden in het D-Stability-bestand.")

    delta_x = -float(new_origin)
    data = model.datastructure

    for geometry in iter_items(getattr(data, "geometries", None)):
        for layer in iter_items(getattr(geometry, "Layers", None)):
            shift_points(getattr(layer, "Points", None), delta_x)

    for waternet in iter_items(getattr(data, "waternets", None)):
        for headline in iter_items(getattr(waternet, "HeadLines", None)):
            shift_points(getattr(headline, "Points", None), delta_x)

    for load in iter_items(getattr(data, "loads", None)):
        shift_points(getattr(load, "Points", None), delta_x)
        for name in ("X", "XEnd"):
            value = getattr(load, name, None)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                setattr(load, name, value + delta_x)

    stages = getattr(data, "Stages", None)
    if stages is None:
        stages = getattr(data, "stages", None)
    for stage in iter_items(stages):
        settings = getattr(stage, "CalculationSettings", None)
        if settings is not None:
            for name in SEARCH_GRID_ATTRIBUTES:
                shift_search_grid(settings, name, delta_x)

    return min(original_x) + delta_x, max(original_x) + delta_x


def model_field_names(obj: Any) -> Iterable[str]:
    """Geef veldnamen van Pydantic 1- en 2-modellen terug."""
    fields = getattr(obj, "model_fields", None)
    if not isinstance(fields, dict):
        fields = getattr(type(obj), "model_fields", None)
    if isinstance(fields, dict):
        return fields.keys()

    fields = getattr(obj, "__fields__", None)
    if not isinstance(fields, dict):
        fields = getattr(type(obj), "__fields__", None)
    return fields.keys() if isinstance(fields, dict) else ()


def mirror_object_tree(obj: Any, visited: set[int] | None = None) -> None:
    """Spiegel X-waarden recursief en herstel de winding order."""
    if visited is None:
        visited = set()
    if obj is None or isinstance(obj, (str, bytes, int, float, bool)):
        return
    if id(obj) in visited:
        return
    visited.add(id(obj))

    for name in X_ATTRIBUTES:
        value = getattr(obj, name, None)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            setattr(obj, name, -value)

    reversed_ids: set[int] = set()
    for name in POINT_LIST_ATTRIBUTES:
        value = getattr(obj, name, None)
        if isinstance(value, list) and id(value) not in reversed_ids:
            value.reverse()
            reversed_ids.add(id(value))

    if isinstance(obj, dict):
        children = obj.values()
    elif isinstance(obj, (list, tuple, set)):
        children = obj
    else:
        children = (
            getattr(obj, name)
            for name in model_field_names(obj)
            if hasattr(obj, name)
        )
    for child in children:
        mirror_object_tree(child, visited)


def mirror_model_x(model: Any) -> tuple[float, float]:
    """Spiegel het model in X = 0 en retourneer het nieuwe bereik."""
    if not collect_geometry_x(model):
        raise ValueError("Geen geometrie gevonden in het D-Stability-bestand.")
    mirror_object_tree(model.datastructure)
    mirrored_x = collect_geometry_x(model)
    return min(mirrored_x), max(mirrored_x)


def load_dstability_model(input_path: Path) -> Any:
    """Laad een model via de actuele of oudere GEOLib-modulelocatie."""
    try:
        from geolib.models.dstability import DStabilityModel
    except ImportError:
        from geolib.models.dstability.dstability_model import DStabilityModel

    model = DStabilityModel()
    model.parse(input_path)
    return model


def process_stix(
    uploaded_bytes: bytes,
    operation: str,
    new_origin: float = 0.0,
) -> tuple[bytes, float, float]:
    """Verwerk STIX-bytes en retourneer uitvoer en nieuw X-bereik."""
    with tempfile.TemporaryDirectory() as temporary_directory:
        input_path = Path(temporary_directory) / "input.stix"
        output_path = Path(temporary_directory) / "output.stix"
        input_path.write_bytes(uploaded_bytes)
        model = load_dstability_model(input_path)

        if operation == "Verschuiven":
            minimum_x, maximum_x = shift_model_x(model, new_origin)
        elif operation == "Spiegelen":
            minimum_x, maximum_x = mirror_model_x(model)
        else:
            raise ValueError(f"Onbekende bewerking: {operation}")

        model.serialize(output_path)
        return output_path.read_bytes(), minimum_x, maximum_x


def output_file_name(source_name: str, operation: str) -> str:
    """Maak een veilige en herkenbare uitvoernaam."""
    source = Path(source_name).name
    if operation == "Spiegelen":
        path = Path(source)
        return f"{path.stem}_GESPIEGELD{path.suffix}"
    return f"shifted_{source}"


def reset_result() -> None:
    """Verwijder een resultaat wanneer de invoer wijzigt."""
    st.session_state.pop("result", None)


def main() -> None:
    """Render de Streamlit-interface."""
    st.set_page_config(page_title="D-Stability X-transformaties", page_icon="📐")
    st.title("D-Stability X-transformaties")
    st.caption(f"Python {sys.version_info.major}.{sys.version_info.minor}")

    uploaded_file = st.file_uploader(
        "D-Stability-bestand",
        type=["stix"],
        key="stix_file",
        on_change=reset_result,
    )
    operation = st.radio(
        "Bewerking",
        ("Verschuiven", "Spiegelen"),
        horizontal=True,
        key="operation",
        on_change=reset_result,
    )

    with st.form("transform_form"):
        new_origin = st.number_input(
            "Bestaande X-waarde die X = 0 moet worden",
            value=0.0,
            format="%.3f",
            disabled=operation != "Verschuiven",
        )
        submitted = st.form_submit_button(
            "Bestand verwerken",
            type="primary",
            disabled=uploaded_file is None,
        )

    if submitted and uploaded_file is not None:
        try:
            source_bytes = uploaded_file.getvalue()
            if not source_bytes:
                raise ValueError("Het geuploade bestand is leeg.")
            output_bytes, minimum_x, maximum_x = process_stix(
                source_bytes,
                operation,
                float(new_origin),
            )
            st.session_state["result"] = {
                "bytes": output_bytes,
                "name": output_file_name(uploaded_file.name, operation),
                "operation": operation,
                "origin": float(new_origin),
                "minimum_x": minimum_x,
                "maximum_x": maximum_x,
            }
        except (ValueError, OSError, AttributeError, TypeError, ImportError) as error:
            st.session_state.pop("result", None)
            st.error(f"Het bestand kon niet worden verwerkt: {error}")

    result = st.session_state.get("result")
    if result:
        st.success(f"Bewerking {result['operation'].lower()} is uitgevoerd.")
        left, right = st.columns(2)
        left.metric("Nieuwe minimale X", f"{result['minimum_x']:.3f}")
        right.metric("Nieuwe maximale X", f"{result['maximum_x']:.3f}")
        st.download_button(
            "Download verwerkt bestand",
            data=result["bytes"],
            file_name=result["name"],
            mime="application/octet-stream",
            type="primary",
        )


if __name__ == "__main__":
    main()
