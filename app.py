"""Streamlit-app voor D-Stability geometriebewerkingen en geometrie-export."""
from __future__ import annotations

import csv
import io
import tempfile
import warnings
from pathlib import Path
from typing import Any, Iterable

import plotly.graph_objects as go
import streamlit as st
from pydantic import BaseModel

try:
    from geolib.models.dstability import DStabilityModel
except ImportError:
    from geolib.models.dstability.dstability_model import DStabilityModel

warnings.filterwarnings("ignore", category=UserWarning, module="requests")

RESULT_KEY = "dstability_result"
RESULT_SCHEMA_VERSION = 4
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
    required = {
        "schema_version", "operation", "data", "file_name", "message",
        "min_x", "max_x", "geometry_rows",
    }
    if not isinstance(result, dict) or not required.issubset(result):
        clear_result()
        return None
    if result.get("schema_version") != RESULT_SCHEMA_VERSION:
        clear_result()
        return None
    return result


def extract_geometry_rows(model: DStabilityModel) -> list[dict[str, Any]]:
    """Zet geometriepunten om naar rijen voor grafiek en CSV-export."""
    rows: list[dict[str, Any]] = []
    geometries = getattr(model.datastructure, "geometries", None)
    for geometry_index, geometry in enumerate(iter_items(geometries), 1):
        for layer_index, layer in enumerate(
            iter_items(getattr(geometry, "Layers", None)), 1
        ):
            layer_id = getattr(layer, "Id", None) or getattr(layer, "id", None)
            layer_name = (
                getattr(layer, "Label", None)
                or getattr(layer, "Name", None)
                or getattr(layer, "name", None)
                or f"Laag {layer_index}"
            )
            for point_index, point in enumerate(
                iter_items(getattr(layer, "Points", None)), 1
            ):
                x_value = getattr(point, "X", None)
                z_value = getattr(point, "Z", None)
                if z_value is None:
                    z_value = getattr(point, "Y", None)
                if x_value is None or z_value is None:
                    continue
                rows.append({
                    "geometry_index": geometry_index,
                    "layer_index": layer_index,
                    "layer_id": "" if layer_id is None else str(layer_id),
                    "layer_name": str(layer_name),
                    "point_index": point_index,
                    "x": float(x_value),
                    "z": float(z_value),
                })
    return rows


def geometry_rows_to_csv(rows: list[dict[str, Any]]) -> bytes:
    """Converteer geometriepunten naar Excel-vriendelijke UTF-8 CSV-bytes."""
    if not rows:
        raise ValueError("Geen geometriepunten beschikbaar voor CSV-export.")
    fields = [
        "geometry_index", "layer_index", "layer_id", "layer_name",
        "point_index", "x", "z",
    ]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8-sig")


def build_geometry_figure(rows: list[dict[str, Any]]) -> go.Figure:
    """Bouw een interactieve geometriegrafiek met een spoor per laag."""
    figure = go.Figure()
    grouped: dict[tuple[int, int, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (row["geometry_index"], row["layer_index"], row["layer_name"])
        grouped.setdefault(key, []).append(row)
    for (geometry_index, layer_index, layer_name), layer_rows in grouped.items():
        ordered = sorted(layer_rows, key=lambda item: item["point_index"])
        plotted = ordered + [ordered[0]] if len(ordered) > 2 else ordered
        figure.add_trace(go.Scatter(
            x=[row["x"] for row in plotted],
            y=[row["z"] for row in plotted],
            mode="lines+markers",
            name=f"G{geometry_index} L{layer_index}: {layer_name}",
            hovertemplate=(
                "X=%{x:.3f}<br>Z=%{y:.3f}<extra>%{fullData.name}</extra>"
            ),
        ))
    figure.update_layout(
        title="Geometrie uit het STIX-bestand",
        xaxis_title="X", yaxis_title="Z", hovermode="closest",
        legend_title="Lagen", margin=dict(l=20, r=20, t=55, b=20),
    )
    figure.update_yaxes(scaleanchor="x", scaleratio=1)
    return figure


def collect_geometry_x(model: DStabilityModel) -> list[float]:
    """Verzamel alle X-coordinaten uit de geometrie."""
    return [row["x"] for row in extract_geometry_rows(model)]


def shift_points(points: Any, x_shift: float) -> None:
    """Verschuif een optionele verzameling punten over de X-as."""
    for point in iter_items(points):
        point.X += x_shift


def shift_search_grid(settings: Any, attribute: str, x_shift: float) -> None:
    """Verschuif de oorsprong van een optioneel zoekraster."""
    method = getattr(settings, attribute, None)
    origin = getattr(getattr(method, "SearchGrid", None), "Origin", None)
    if origin is not None and getattr(origin, "X", None) is not None:
        origin.X += x_shift


def shift_model_x(model: DStabilityModel, new_origin: float) -> tuple[float, float]:
    """Verschuif alle ondersteunde X-coordinaten in het model."""
    x_values = collect_geometry_x(model)
    if not x_values:
        raise ValueError("Geen geometrie gevonden in het D-Stability-bestand.")
    shift = -float(new_origin)
    data = model.datastructure
    for geometry in iter_items(getattr(data, "geometries", None)):
        for layer in iter_items(getattr(geometry, "Layers", None)):
            shift_points(getattr(layer, "Points", None), shift)
    for waternet in iter_items(getattr(data, "waternets", None)):
        for head_line in iter_items(getattr(waternet, "HeadLines", None)):
            shift_points(getattr(head_line, "Points", None), shift)
    for load in iter_items(getattr(data, "loads", None)):
        shift_points(getattr(load, "Points", None), shift)
        for attribute in ("X", "XEnd"):
            value = getattr(load, attribute, None)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                setattr(load, attribute, value + shift)
    stages = getattr(data, "Stages", None)
    if stages is None:
        stages = getattr(data, "stages", None)
    attributes = (
        "BishopBruteForceSettings", "BishopSettings", "SpencerSettings",
        "SpencerGeneticSettings", "UpliftVanSettings",
        "UpliftVanMethodParticleSwarmSettings",
    )
    for stage in iter_items(stages):
        settings = getattr(stage, "CalculationSettings", None)
        if settings is not None:
            for attribute in attributes:
                shift_search_grid(settings, attribute, shift)
    return min(x_values) + shift, max(x_values) + shift


def pydantic_field_names(model: BaseModel) -> Iterable[str]:
    """Geef veldnamen terug voor Pydantic 1 en Pydantic 2."""
    fields = getattr(type(model), "model_fields", None)
    return fields.keys() if fields is not None else getattr(model, "__fields__", {}).keys()


def mirror_object_tree(obj: Any, memo: set[int]) -> None:
    """Spiegel X-waarden en herstel winding order in een objectboom."""
    if obj is None or isinstance(obj, (str, bytes, int, float, bool)):
        return
    if id(obj) in memo:
        return
    memo.add(id(obj))
    for attribute in ("x", "X", "x_left", "x_right", "XLeft", "XRight", "XCenter"):
        value = getattr(obj, attribute, None)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            setattr(obj, attribute, -value)
    for attribute in ("points", "Points", "point_ids", "PointIds"):
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
    """Spiegel het volledige model rond X = 0."""
    if not collect_geometry_x(model):
        raise ValueError("Geen geometrie gevonden in het D-Stability-bestand.")
    mirror_object_tree(model.datastructure, set())
    mirrored = collect_geometry_x(model)
    return min(mirrored), max(mirrored)


def process_stix(
    data: bytes, operation: str, new_origin: float = 0.0
) -> tuple[bytes, float, float, list[dict[str, Any]]]:
    """Voer de bewerking uit en retourneer STIX, bereik en geometrie."""
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
        rows = extract_geometry_rows(model)
        if not rows:
            raise ValueError("Geen geometriepunten gevonden voor weergave of export.")
        model.serialize(output_path)
        if not output_path.exists() or output_path.stat().st_size == 0:
            raise ValueError("D-Stability heeft geen geldig uitvoerbestand gemaakt.")
        return output_path.read_bytes(), min_x, max_x, rows


def make_output_name(input_name: str, operation: str) -> str:
    """Maak de uitvoerbestandsnaam."""
    path = Path(Path(input_name).name)
    if operation == OPERATION_MIRROR:
        return f"{path.stem}_GESPIEGELD{path.suffix}"
    return f"shifted_{path.name}"


def main() -> None:
    """Render de Streamlit-app."""
    st.set_page_config(page_title="D-Stability geometriebewerker", page_icon="📐")
    st.title("D-Stability geometriebewerker")
    st.write(
        "Upload een `.stix`-bestand, kies een bewerking en bekijk of download "
        "de resulterende geometrie."
    )
    uploaded = st.file_uploader(
        "D-Stability-bestand", type=["stix"], key="stix_upload",
        on_change=clear_result,
    )
    operation = st.radio(
        "Bewerking", (OPERATION_SHIFT, OPERATION_MIRROR), horizontal=True,
        key="operation", on_change=clear_result,
    )
    with st.form("dstability_form"):
        new_origin = st.number_input(
            "Bestaande X-waarde die X = 0 moet worden", value=0.0,
            format="%.3f", disabled=operation != OPERATION_SHIFT,
        )
        submitted = st.form_submit_button(
            "Bestand verwerken", type="primary", disabled=uploaded is None,
        )
    if submitted and uploaded is not None:
        try:
            output, min_x, max_x, rows = process_stix(
                uploaded.getvalue(), operation, float(new_origin)
            )
            message = (
                f"De oorspronkelijke X-waarde {new_origin:.3f} is nu X = 0.000."
                if operation == OPERATION_SHIFT
                else "De geometrie is gespiegeld rond X = 0."
            )
            st.session_state[RESULT_KEY] = {
                "schema_version": RESULT_SCHEMA_VERSION,
                "operation": operation,
                "data": output,
                "file_name": make_output_name(uploaded.name, operation),
                "message": message,
                "min_x": min_x,
                "max_x": max_x,
                "geometry_rows": rows,
            }
        except (ValueError, OSError, AttributeError, TypeError) as exc:
            clear_result()
            st.error(f"Het bestand kon niet worden verwerkt: {exc}")
        except Exception:
            clear_result()
            st.error("Het bestand kon niet worden verwerkt. Controleer het STIX-bestand.")
    result = get_valid_result()
    if result is not None:
        st.success(result["message"])
        left, right = st.columns(2)
        left.metric("Nieuwe minimale X", f"{result['min_x']:.3f}")
        right.metric("Nieuwe maximale X", f"{result['max_x']:.3f}")
        st.subheader("Geometrie")
        st.plotly_chart(
            build_geometry_figure(result["geometry_rows"]),
            width="stretch", key="geometry_chart",
        )
        st.caption(f"{len(result['geometry_rows'])} geometriepunten gevonden.")
        st.download_button(
            "Download verwerkt STIX-bestand", result["data"],
            file_name=result["file_name"], mime="application/octet-stream",
            type="primary",
        )
        csv_name = f"{Path(result['file_name']).stem}_geometrie.csv"
        st.download_button(
            "Download geometrie als CSV",
            geometry_rows_to_csv(result["geometry_rows"]),
            file_name=csv_name, mime="text/csv",
        )


if __name__ == "__main__":
    main()
