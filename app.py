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

warnings.filterwarnings("ignore", category=UserWarning, module="requests")

RESULT_KEY = "dstability_result"
RESULT_SCHEMA_VERSION = 3
OPERATION_SHIFT = "Verschuiven"
OPERATION_MIRROR = "Spiegelen"


def iter_items(value: Any) -> Iterable[Any]:
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
    }
    if not isinstance(result, dict) or not required_keys.issubset(result):
        clear_result()
        return None
    if result.get("schema_version") != RESULT_SCHEMA_VERSION:
        clear_result()
        return None
    return result


def collect_geometry_x(model: DStabilityModel) -> list[float]:
    """Verzamel alle X-coordinaten uit de geometrie."""
    x_values: list[float] = []
    for geometry in iter_items(getattr(model.datastructure, "geometries", None)):
        for layer in iter_items(getattr(geometry, "Layers", None)):
            for point in iter_items(getattr(layer, "Points", None)):
                x_values.append(float(point.X))
    return x_values


def shift_points(points: Any, x_shift: float) -> None:
    """Verschuif een optionele verzameling punten over de X-as."""
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
            if isinstance(value, (int, float)):
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


def pydantic_field_names(model: BaseModel) -> Iterable[str]:
    """Geef veldnamen terug voor zowel Pydantic 1 als Pydantic 2."""
    model_fields = getattr(type(model), "model_fields", None)
    if model_fields is not None:
        return model_fields.keys()
    legacy_fields = getattr(model, "__fields__", {})
    return legacy_fields.keys()


def mirror_object_tree(obj: Any, memo: set[int]) -> None:
    """Spiegel X-waarden en herstel de winding order in een objectboom."""
    if obj is None or isinstance(obj, (str, bytes, int, float, bool)):
        return
    if id(obj) in memo:
        return
    memo.add(id(obj))

    x_attributes = (
        "x",
        "X",
        "x_left",
        "x_right",
        "XLeft",
        "XRight",
        "XCenter",
    )
    for attribute in x_attributes:
        value = getattr(obj, attribute, None)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            setattr(obj, attribute, -value)

    point_list_names = ("points", "Points", "point_ids", "PointIds")
    for attribute in point_list_names:
        value = getattr(obj, attribute, None)
        if isinstance(value, list):
            value.reverse()

    if isinstance(obj, BaseModel):
        for field_name in pydantic_field_names(obj):
            mirror_object_tree(getattr(obj, field_name, None), memo)
    elif isinstance(obj, dict):
        for item in obj.values():
            mirror_object_tree(item, memo)
    elif isinstance(obj, (list, tuple, set)):
        for item in obj:
            mirror_object_tree(item, memo)


def mirror_model_x(model: DStabilityModel) -> tuple[float, float]:
    """Spiegel het volledige model rond X = 0 en geef het bereik terug."""
    original_x = collect_geometry_x(model)
    if not original_x:
        raise ValueError("Geen geometrie gevonden in het D-Stability-bestand.")
    mirror_object_tree(model.datastructure, set())
    mirrored_x = collect_geometry_x(model)
    return min(mirrored_x), max(mirrored_x)


def process_stix(
    data: bytes,
    operation: str,
    new_origin: float = 0.0,
) -> tuple[bytes, float, float]:
    """Voer de gekozen bewerking uit en retourneer uitvoer en X-bereik."""
    if not data:
        raise ValueError("Het geuploade bestand is leeg.")
    if operation not in {OPERATION_SHIFT, OPERATION_MIRROR}:
        raise ValueError("Onbekende bewerking geselecteerd.")

    with tempfile.TemporaryDirectory() as temp_dir:
        input_path = Path(temp_dir) / "input.stix"
        output_path = Path(temp_dir) / "output.stix"
        input_path.write_bytes(data)

        model = DStabilityModel()
        model.parse(input_path)
        if operation == OPERATION_SHIFT:
            min_x, max_x = shift_model_x(model, new_origin)
        else:
            min_x, max_x = mirror_model_x(model)
        model.serialize(output_path)

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise ValueError("D-Stability heeft geen geldig uitvoerbestand gemaakt.")
        return output_path.read_bytes(), min_x, max_x


def make_output_name(input_name: str, operation: str) -> str:
    """Maak een veilige uitvoerbestandsnaam voor de gekozen bewerking."""
    input_path = Path(input_name).name
    path = Path(input_path)
    if operation == OPERATION_MIRROR:
        return f"{path.stem}_GESPIEGELD{path.suffix}"
    return f"shifted_{input_path}"


def main() -> None:
    """Render de Streamlit-app."""
    st.set_page_config(
        page_title="D-Stability geometriebewerker",
        page_icon="📐",
    )
    st.title("D-Stability geometriebewerker")
    st.write(
        "Upload een `.stix`-bestand en kies of je de X-as wilt verschuiven "
        "of de volledige geometrie rond X = 0 wilt spiegelen."
    )

    uploaded_file = st.file_uploader(
        "D-Stability-bestand",
        type=["stix"],
        key="stix_upload",
        on_change=clear_result,
    )
    operation = st.radio(
        "Bewerking",
        options=(OPERATION_SHIFT, OPERATION_MIRROR),
        horizontal=True,
        key="operation",
        on_change=clear_result,
    )

    with st.form("dstability_form"):
        new_origin = st.number_input(
            "Bestaande X-waarde die X = 0 moet worden",
            value=0.0,
            format="%.3f",
            disabled=operation != OPERATION_SHIFT,
            help="Dit veld wordt alleen gebruikt bij verschuiven.",
        )
        submitted = st.form_submit_button(
            "Bestand verwerken",
            type="primary",
            disabled=uploaded_file is None,
        )

    if submitted and uploaded_file is not None:
        try:
            output_data, min_x, max_x = process_stix(
                uploaded_file.getvalue(),
                operation,
                float(new_origin),
            )
            file_name = make_output_name(uploaded_file.name, operation)
            if operation == OPERATION_SHIFT:
                message = (
                    f"De oorspronkelijke X-waarde {new_origin:.3f} is nu "
                    "X = 0.000."
                )
            else:
                message = "De geometrie is gespiegeld rond X = 0."
            st.session_state[RESULT_KEY] = {
                "schema_version": RESULT_SCHEMA_VERSION,
                "operation": operation,
                "data": output_data,
                "file_name": file_name,
                "message": message,
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
        st.success(result["message"])
        min_column, max_column = st.columns(2)
        min_column.metric("Nieuwe minimale X", f"{result['min_x']:.3f}")
        max_column.metric("Nieuwe maximale X", f"{result['max_x']:.3f}")
        st.download_button(
            "Download verwerkt bestand",
            data=result["data"],
            file_name=result["file_name"],
            mime="application/octet-stream",
            type="primary",
        )


if __name__ == "__main__":
    main()
