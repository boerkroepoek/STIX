"""Streamlit-app voor het verschuiven van X-coordinaten in D-Stability-bestanden."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Iterable

import streamlit as st
from geolib.models.dstability import DStabilityModel


def iter_items(value: Any) -> Iterable[Any]:
    """Geef een lege iterable terug wanneer een collectie ontbreekt."""
    return value or ()


def collect_geometry_x(model: DStabilityModel) -> list[float]:
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


def shift_model_x(model: DStabilityModel, new_origin: float) -> tuple[float, float]:
    """Verschuif alle ondersteunde X-coordinaten en geef het nieuwe bereik terug."""
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
            if value is not None:
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


def process_stix(uploaded_bytes: bytes, new_origin: float) -> tuple[bytes, float, float]:
    """Lees STIX-bytes, verschuif het model en retourneer uitvoerbytes en bereik."""
    with tempfile.TemporaryDirectory() as temp_directory:
        input_path = Path(temp_directory) / "input.stix"
        output_path = Path(temp_directory) / "shifted_input.stix"
        input_path.write_bytes(uploaded_bytes)

        model = DStabilityModel()
        model.parse(input_path)
        new_min_x, new_max_x = shift_model_x(model, new_origin)
        model.serialize(output_path)

        return output_path.read_bytes(), new_min_x, new_max_x


def reset_result() -> None:
    """Verwijder een eerder resultaat nadat de invoer is gewijzigd."""
    st.session_state.pop("result", None)


def main() -> None:
    """Render de Streamlit-interface."""
    st.set_page_config(page_title="D-Stability X-as verschuiven", page_icon="📐")
    st.title("D-Stability X-as verschuiven")
    st.write(
        "Upload een `.stix`-bestand, kies welke bestaande X-waarde het nieuwe "
        "nulpunt wordt en download het aangepaste bestand."
    )

    uploaded_file = st.file_uploader(
        "D-Stability-bestand",
        type=["stix"],
        key="stix_file",
        on_change=reset_result,
        help="Selecteer een D-Stability-bestand met de extensie .stix.",
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

    if submitted:
        if uploaded_file is None:
            st.error("Upload eerst een geldig `.stix`-bestand.")
        else:
            try:
                source_bytes = uploaded_file.getvalue()
                if not source_bytes:
                    raise ValueError("Het geuploade bestand is leeg.")
                output_bytes, new_min_x, new_max_x = process_stix(
                    source_bytes,
                    float(new_origin),
                )
                output_name = f"shifted_{Path(uploaded_file.name).name}"
                st.session_state["result"] = {
                    "bytes": output_bytes,
                    "name": output_name,
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
        st.success(
            f"De oorspronkelijke X-waarde {result['origin']:.3f} is nu X = 0.000."
        )
        col_min, col_max = st.columns(2)
        col_min.metric("Nieuwe minimale X", f"{result['min_x']:.3f}")
        col_max.metric("Nieuwe maximale X", f"{result['max_x']:.3f}")
        st.download_button(
            "Download verschoven bestand",
            data=result["bytes"],
            file_name=result["name"],
            mime="application/octet-stream",
            type="primary",
        )


if __name__ == "__main__":
    main()
