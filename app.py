"""Streamlit-app voor het verschuiven van X-coordinaten in D-Stability-bestanden."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Iterable

import streamlit as st
from geolib.models.dstability import DStabilityModel

RESULT_KEY = "shift_result"
RESULT_SCHEMA_VERSION = 2


def iter_items(value: Any) -> Iterable[Any]:
    """Geef een lege iterable terug wanneer een collectie ontbreekt."""
    return value or ()


def clear_result() -> None:
    """Verwijder een oud resultaat wanneer de invoer wijzigt."""
    st.session_state.pop(RESULT_KEY, None)


def get_valid_result() -> dict[str, Any] | None:
    """Lees alleen een resultaat met het huidige en volledige schema."""
    result = st.session_state.get(RESULT_KEY)
    required_keys = {
        "schema_version",
        "data",
        "file_name",
        "origin",
        "min_x",
        "max_x",
    }
    if not isinstance(result, dict) or not required_keys.issubset(result):
        st.session_state.pop(RESULT_KEY, None)
        return None
    if result["schema_version"] != RESULT_SCHEMA_VERSION:
        st.session_state.pop(RESULT_KEY, None)
        return None
    return result


def collect_geometry_x(model: DStabilityModel) -> list[float]:
    """Verzamel alle X-coordinaten uit de geometrie."""
    x_values: list[float] = []
    geometries = getattr(model.datastructure, "geometries", None)
    for geometry in iter_items(geometries):
        for layer in iter_items(getattr(geometry, "Layers", None)):
            for point in iter_items(getattr(layer, "Points", None)):
                x_values.append(float(point.X))
    return x_values


def shift_points(points: Any, x_shift: float) -> None:
    """Verschuif de X-coordinaat van een optionele puntenverzameling."""
    for point in iter_items(points):
        point.X += x_shift


def shift_search_grid(settings: Any, attribute: str, x_shift: float) -> None:
    """Verschuif de oorsprong van een optioneel zoekraster."""
    method_settings = getattr(settings, attribute, None)
    search_grid = getattr(method_settings, "SearchGrid", None)
    origin = getattr(search_grid, "Origin", None)
    if origin is not None and getattr(origin, "X", None) is not None:
        origin.X += x_shift


def shift_model_x(model: DStabilityModel, new_origin: float) -> tuple[float, float]:
    """Verschuif alle ondersteunde X-coordinaten in het model."""
    x_values = collect_geometry_x(model)
    if not x_values:
        raise ValueError("Geen geometrie gevonden in het D-Stability-bestand.")

    x_shift = -float(new_origin)
    datastructure = model.datastructure

    for geometry in iter_items(getattr(datastructure, "geometries", None)):
        for layer in iter_items(getattr(geometry, "Layers", None)):
            shift_points(getattr(layer, "Points", None), x_shift)

    for waternet in iter_items(getattr(datastructure, "waternets", None)):
        for head_line in iter_items(getattr(waternet, "HeadLines", None)):
            shift_points(getattr(head_line, "Points", None), x_shift)

    for load in iter_items(getattr(datastructure, "loads", None)):
        shift_points(getattr(load, "Points", None), x_shift)
        for attribute in ("X", "XEnd"):
            value = getattr(load, attribute, None)
            if value is not None:
                setattr(load, attribute, value + x_shift)

    stages = getattr(datastructure, "Stages", None)
    if stages is None:
        stages = getattr(datastructure, "stages", None)

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


def process_stix(data: bytes, new_origin: float) -> tuple[bytes, float, float]:
    """Verwerk STIX-bytes en retourneer bestand en nieuw X-bereik."""
    if not data:
        raise ValueError("Het geuploade bestand is leeg.")

    with tempfile.TemporaryDirectory() as temp_dir:
        input_path = Path(temp_dir) / "input.stix"
        output_path = Path(temp_dir) / "output.stix"
        input_path.write_bytes(data)

        model = DStabilityModel()
        model.parse(input_path)
        min_x, max_x = shift_model_x(model, new_origin)
        model.serialize(output_path)

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise ValueError("D-Stability heeft geen geldig uitvoerbestand gemaakt.")
        return output_path.read_bytes(), min_x, max_x


def main() -> None:
    """Render de Streamlit-app."""
    st.set_page_config(
        page_title="D-Stability X-as verschuiven",
        page_icon="📐",
    )
    st.title("D-Stability X-as verschuiven")
    st.write(
        "Upload een `.stix`-bestand en geef aan welke bestaande X-waarde "
        "het nieuwe nulpunt moet worden."
    )

    uploaded_file = st.file_uploader(
        "D-Stability-bestand",
        type=["stix"],
        key="stix_upload",
        on_change=clear_result,
    )

    with st.form("shift_form"):
        new_origin = st.number_input(
            "Bestaande X-waarde die X = 0 moet worden",
            value=0.0,
            format="%.3f",
        )
        submitted = st.form_submit_button(
            "Verschuif X-as",
            type="primary",
            disabled=uploaded_file is None,
        )

    if submitted and uploaded_file is not None:
        try:
            output_data, min_x, max_x = process_stix(
                uploaded_file.getvalue(),
                float(new_origin),
            )
            st.session_state[RESULT_KEY] = {
                "schema_version": RESULT_SCHEMA_VERSION,
                "data": output_data,
                "file_name": f"shifted_{Path(uploaded_file.name).name}",
                "origin": float(new_origin),
                "min_x": min_x,
                "max_x": max_x,
            }
        except (ValueError, OSError, AttributeError, TypeError) as exc:
            clear_result()
            st.error(f"Het bestand kon niet worden verwerkt: {exc}")
        except Exception:
            clear_result()
            st.error(
                "Het bestand kon niet worden verwerkt. Controleer of het een "
                "geldig en ondersteund D-Stability-bestand is."
            )

    result = get_valid_result()
    if result is not None:
        st.success(
            f"De oorspronkelijke X-waarde {result['origin']:.3f} "
            "is nu X = 0.000."
        )
        min_column, max_column = st.columns(2)
        min_column.metric("Nieuwe minimale X", f"{result['min_x']:.3f}")
        max_column.metric("Nieuwe maximale X", f"{result['max_x']:.3f}")
        st.download_button(
            "Download verschoven bestand",
            data=result["data"],
            file_name=result["file_name"],
            mime="application/octet-stream",
            type="primary",
        )


if __name__ == "__main__":
    main()
