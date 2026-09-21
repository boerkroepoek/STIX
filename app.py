"""Streamlit-app voor X-transformaties van D-Stability-bestanden."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Iterable

import streamlit as st


X_ATTRIBUTES = (
    "x",
    "X",
    "x_left",
    "x_right",
    "XLeft",
    "XRight",
    "XCenter",
)
POINT_LIST_ATTRIBUTES = ("points", "Points", "point_ids", "PointIds")


def iter_items(value: Any) -> Iterable[Any]:
    """Geef een lege iterable terug wanneer een collectie ontbreekt."""
    return value or ()


def collect_geometry_x(model: Any) -> list[float]:
    """Verzamel alle X-coordinaten uit de geometrie van het model."""
    x_values: list[float] = []
    for geometry in iter_items(getattr(model.datastructure, "geometries", None)):
        for layer in iter_items(getattr(geometry, "Layers", None)):
            for point in iter_items(getattr(layer, "Points", None)):
                x_values.append(float(point.X))
    return x_values


def shift_point_collection(points: Any, x_shift: float) -> None:
    """Verschuif alle punten in een optionele puntencollectie."""
    for point in iter_items(points):
        point.X += x_shift


def shift_search_grid(settings: Any, attribute: str, x_shift: float) -> None:
    """Verschuif de oorsprong van een optioneel zoekraster."""
    method_settings = getattr(settings, attribute, None)
    search_grid = getattr(method_settings, "SearchGrid", None)
    origin = getattr(search_grid, "Origin", None)
    if origin is not None:
        origin.X += x_shift


def shift_model_x(model: Any, new_origin: float) -> tuple[float, float]:
    """Verschuif ondersteunde X-coordinaten en geef het nieuwe bereik terug."""
    x_values = collect_geometry_x(model)
    if not x_values:
        raise ValueError("Geen geometrie gevonden in het D-Stability-bestand.")

    x_shift = -float(new_origin)

    for geometry in iter_items(getattr(model.datastructure, "geometries", None)):
        for layer in iter_items(getattr(geometry, "Layers", None)):
            shift_point_collection(getattr(layer, "Points", None), x_shift)

    for waternet in iter_items(getattr(model.datastructure, "waternets", None)):
        for head_line in iter_items(getattr(waternet, "HeadLines", None)):
            shift_point_collection(getattr(head_line, "Points", None), x_shift)

    for load in iter_items(getattr(model.datastructure, "loads", None)):
        shift_point_collection(getattr(load, "Points", None), x_shift)
        for attribute in ("X", "XEnd"):
            value = getattr(load, attribute, None)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                setattr(load, attribute, value + x_shift)

    stages = getattr(model.datastructure, "Stages", None)
    if stages is None:
        stages = getattr(model.datastructure, "stages", None)

    search_grid_attributes = (
        "BishopBruteForceSettings",
        "BishopSettings",
        "SpencerSettings",
        "SpencerGeneticSettings",
        "UpliftVanSettings",
        "UpliftVanMethodParticleSwarmSettings",
    )
    for stage in iter_items(stages):
        settings = getattr(stage, "CalculationSettings", None)
        if settings is None:
            continue
        for attribute in search_grid_attributes:
            shift_search_grid(settings, attribute, x_shift)

    return min(x_values) + x_shift, max(x_values) + x_shift


def model_field_names(obj: Any) -> Iterable[str]:
    """Geef veldnamen van Pydantic V1- en V2-modellen terug."""
    model_fields = getattr(obj, "model_fields", None)
    if not isinstance(model_fields, dict):
        model_fields = getattr(type(obj), "model_fields", None)
    if isinstance(model_fields, dict):
        return model_fields.keys()

    legacy_fields = getattr(type(obj), "__fields__", None)
    if isinstance(legacy_fields, dict):
        return legacy_fields.keys()

    return ()


def mirror_object_tree(obj: Any, visited: set[int] | None = None) -> None:
    """Spiegel X-waarden recursief en herstel winding order van puntenlijsten."""
    if visited is None:
        visited = set()

    if obj is None or isinstance(obj, (str, bytes, int, float, bool)):
        return
    if id(obj) in visited:
        return
    visited.add(id(obj))

    for attribute in X_ATTRIBUTES:
        if not hasattr(obj, attribute):
            continue
        value = getattr(obj, attribute)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            setattr(obj, attribute, -value)

    reversed_lists: set[int] = set()
    for attribute in POINT_LIST_ATTRIBUTES:
        value = getattr(obj, attribute, None)
        if isinstance(value, list) and id(value) not in reversed_lists:
            value.reverse()
            reversed_lists.add(id(value))

    if isinstance(obj, dict):
        children = obj.values()
    elif isinstance(obj, (list, tuple, set)):
        children = obj
    else:
        children = (
            getattr(obj, field_name)
            for field_name in model_field_names(obj)
            if hasattr(obj, field_name)
        )

    for child in children:
        mirror_object_tree(child, visited)


def mirror_model_x(model: Any) -> tuple[float, float]:
    """Spiegel het hele datamodel in X = 0 en geef het geometriebereik terug."""
    original_x = collect_geometry_x(model)
    if not original_x:
        raise ValueError("Geen geometrie gevonden in het D-Stability-bestand.")

    mirror_object_tree(model.datastructure)
    mirrored_x = collect_geometry_x(model)
    return min(mirrored_x), max(mirrored_x)


def load_dstability_model(input_path: Path) -> Any:
    """Laad een D-Stability-model met de beschikbare GEOLib-import."""
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
    """Verwerk STIX-bytes en retourneer uitvoerbytes en nieuw X-bereik."""
    with tempfile.TemporaryDirectory() as temp_directory:
        input_path = Path(temp_directory) / "input.stix"
        output_path = Path(temp_directory) / "output.stix"
        input_path.write_bytes(uploaded_bytes)

        model = load_dstability_model(input_path)
        if operation == "Verschuiven":
            new_min_x, new_max_x = shift_model_x(model, new_origin)
        elif operation == "Spiegelen":
            new_min_x, new_max_x = mirror_model_x(model)
        else:
            raise ValueError(f"Onbekende bewerking: {operation}")

        model.serialize(output_path)
        return output_path.read_bytes(), new_min_x, new_max_x


def reset_result() -> None:
    """Verwijder een eerder resultaat nadat invoer is gewijzigd."""
    st.session_state.pop("result", None)


def output_file_name(source_name: str, operation: str) -> str:
    """Maak een veilige, herkenbare bestandsnaam voor het resultaat."""
    source = Path(source_name).name
    if operation == "Spiegelen":
        path = Path(source)
        return f"{path.stem}_GESPIEGELD{path.suffix}"
    return f"shifted_{source}"


def main() -> None:
    """Render de Streamlit-interface."""
    st.set_page_config(page_title="D-Stability X-transformaties", page_icon="📐")
    st.title("D-Stability X-transformaties")
    st.write(
        "Upload een `.stix`-bestand en kies of je de X-as wilt verschuiven "
        "of de volledige datastructuur wilt spiegelen in X = 0."
    )

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
        on_change=reset_result,
    )

    with st.form("transform_form"):
        new_origin = st.number_input(
            "Bestaande X-waarde die X = 0 moet worden",
            value=0.0,
            format="%.3f",
            disabled=operation != "Verschuiven",
        )
        if operation == "Spiegelen":
            st.caption(
                "Alle herkende X-coordinaten worden vermenigvuldigd met -1. "
                "Puntenlijsten worden omgekeerd om de winding order te herstellen."
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
            output_bytes, new_min_x, new_max_x = process_stix(
                source_bytes,
                operation,
                float(new_origin),
            )
            st.session_state["result"] = {
                "bytes": output_bytes,
                "name": output_file_name(uploaded_file.name, operation),
                "operation": operation,
                "origin": float(new_origin),
                "min_x": new_min_x,
                "max_x": new_max_x,
            }
        except (ValueError, OSError, AttributeError, TypeError) as exc:
            st.session_state.pop("result", None)
            st.error(f"Het bestand kon niet worden verwerkt: {exc}")
        except Exception:
            st.session_state.pop("result", None)
            st.error(
                "Het bestand kon niet worden verwerkt. Controleer of het een "
                "geldig en ondersteund D-Stability-bestand is."
            )

    result = st.session_state.get("result")
    if result:
        if result["operation"] == "Verschuiven":
            st.success(
                f"De oorspronkelijke X-waarde {result['origin']:.3f} "
                "is nu X = 0.000."
            )
        else:
            st.success("De D-Stability-datastructuur is gespiegeld in X = 0.")

        col_min, col_max = st.columns(2)
        col_min.metric("Nieuwe minimale X", f"{result['min_x']:.3f}")
        col_max.metric("Nieuwe maximale X", f"{result['max_x']:.3f}")
        st.download_button(
            "Download verwerkt bestand",
            data=result["bytes"],
            file_name=result["name"],
            mime="application/octet-stream",
            type="primary",
        )


if __name__ == "__main__":
    main()
