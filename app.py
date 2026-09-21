"""Streamlit-app voor D-Stability-geometriebewerkingen en -export."""
from __future__ import annotations

import csv
import hashlib
import io
import logging
import math
import tempfile
import warnings
from collections.abc import Iterable
from importlib.metadata import PackageNotFoundError, version
from numbers import Real
from pathlib import Path
from typing import Any, Callable

import geolib
import plotly.graph_objects as go
import streamlit as st
from pydantic import BaseModel


def prepare_geolib_version() -> None:
    """Herstel ontbrekende D-GEOLib-versie-informatie voor modelimports."""
    if hasattr(geolib, "__version__"):
        return
    try:
        geolib.__version__ = version("d-geolib")
    except PackageNotFoundError as exc:
        raise RuntimeError(
            "De dependency 'd-geolib' is niet geinstalleerd. Verwijder "
            "'geolib' uit requirements.txt en voeg 'd-geolib==2.9.1' toe."
        ) from exc


prepare_geolib_version()

# Deze import moet na prepare_geolib_version() staan.
from geolib.models.dstability import DStabilityModel  # noqa: E402

warnings.filterwarnings("ignore", category=UserWarning, module="requests")

LOGGER = logging.getLogger(__name__)
RESULT_KEY = "dstability_result"
RESULT_SCHEMA_VERSION = 7
OPERATION_SHIFT = "Verschuiven"
OPERATION_MIRROR = "Spiegelen"
MAX_UPLOAD_BYTES = 100 * 1024 * 1024

X_FIELD_NAMES = {
    "x",
    "xcenter",
    "xcentre",
    "xcoordinate",
    "xend",
    "xleft",
    "xright",
    "xstart",
}
MIRROR_FIELD_PAIRS = (
    ("x", "xend"),
    ("xstart", "xend"),
    ("xleft", "xright"),
)


def iter_items(value: Any) -> Iterable[Any]:
    """Geef een lege iterable terug wanneer een collectie ontbreekt."""
    return value or ()


def clear_result() -> None:
    """Verwijder een eerder resultaat wanneer de invoer wijzigt."""
    st.session_state.pop(RESULT_KEY, None)


def get_valid_result(
    input_digest: str | None = None,
    operation: str | None = None,
) -> dict[str, Any] | None:
    """Lees alleen een volledig resultaat voor invoer en schemaversie."""
    result = st.session_state.get(RESULT_KEY)
    required = {
        "schema_version",
        "operation",
        "input_digest",
        "data",
        "file_name",
        "message",
        "min_x",
        "max_x",
        "geometry_rows",
    }
    if not isinstance(result, dict) or not required.issubset(result):
        clear_result()
        return None
    if result.get("schema_version") != RESULT_SCHEMA_VERSION:
        clear_result()
        return None
    if input_digest is not None and result.get("input_digest") != input_digest:
        clear_result()
        return None
    if operation is not None and result.get("operation") != operation:
        clear_result()
        return None
    return result


def extract_geometry_rows(model: DStabilityModel) -> list[dict[str, Any]]:
    """Zet geometriepunten om naar rijen voor grafiek en CSV-export."""
    rows: list[dict[str, Any]] = []
    geometries = getattr(model.datastructure, "geometries", None)
    for geometry_index, geometry in enumerate(iter_items(geometries), 1):
        layers = getattr(geometry, "Layers", None)
        if layers is None:
            layers = getattr(geometry, "layers", None)
        for layer_index, layer in enumerate(iter_items(layers), 1):
            layer_id = getattr(layer, "Id", None) or getattr(layer, "id", None)
            layer_name = (
                getattr(layer, "Label", None)
                or getattr(layer, "Name", None)
                or getattr(layer, "name", None)
                or f"Laag {layer_index}"
            )
            points = getattr(layer, "Points", None)
            if points is None:
                points = getattr(layer, "points", None)
            for point_index, point in enumerate(iter_items(points), 1):
                x_value = getattr(point, "X", None)
                if x_value is None:
                    x_value = getattr(point, "x", None)
                z_value = getattr(point, "Z", None)
                if z_value is None:
                    z_value = getattr(point, "z", None)
                if z_value is None:
                    z_value = getattr(point, "Y", None)
                if z_value is None:
                    z_value = getattr(point, "y", None)
                if x_value is None or z_value is None:
                    continue
                rows.append(
                    {
                        "geometry_index": geometry_index,
                        "layer_index": layer_index,
                        "layer_id": "" if layer_id is None else str(layer_id),
                        "layer_name": str(layer_name),
                        "point_index": point_index,
                        "x": float(x_value),
                        "z": float(z_value),
                    }
                )
    return rows


def format_csv_coordinate(value: float) -> str:
    """Formatteer een coordinaat met een decimale komma voor de CSV-export."""
    if not math.isfinite(value):
        raise ValueError("Coordinaten voor CSV-export moeten eindige getallen zijn.")
    return format(value, ".15g").replace(".", ",")


def geometry_rows_to_csv(rows: list[dict[str, Any]]) -> bytes:
    """Converteer geometriepunten naar Excel-vriendelijke UTF-8-CSV."""
    if not rows:
        raise ValueError("Geen geometriepunten beschikbaar voor CSV-export.")
    fields = [
        "geometry_index",
        "layer_index",
        "layer_id",
        "layer_name",
        "point_index",
        "x",
        "z",
    ]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream,
        fieldnames=fields,
        delimiter=";",
        lineterminator="\n",
    )
    writer.writeheader()
    for row in rows:
        csv_row = dict(row)
        csv_row["x"] = format_csv_coordinate(float(row["x"]))
        csv_row["z"] = format_csv_coordinate(float(row["z"]))
        writer.writerow(csv_row)
    return stream.getvalue().encode("utf-8-sig")


def extract_cross_section_rows(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Bepaal de bovencontour van alle geometrische laagpolygonen."""
    if not rows:
        raise ValueError("Geen geometriepunten beschikbaar voor het dwarsprofiel.")

    grouped: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for row in rows:
        key = (row["geometry_index"], row["layer_index"])
        grouped.setdefault(key, []).append(row)

    segments: list[tuple[float, float, float, float]] = []
    x_coordinates: set[float] = set()
    for layer_rows in grouped.values():
        ordered = sorted(layer_rows, key=lambda item: item["point_index"])
        if not ordered:
            continue
        x_coordinates.update(float(row["x"]) for row in ordered)
        polygon = ordered + [ordered[0]] if len(ordered) > 2 else ordered
        for start, end in zip(polygon, polygon[1:]):
            segments.append(
                (
                    float(start["x"]),
                    float(start["z"]),
                    float(end["x"]),
                    float(end["z"]),
                )
            )

    profile: list[dict[str, Any]] = []
    tolerance = 1e-9
    for point_index, x_value in enumerate(sorted(x_coordinates), 1):
        intersections: list[float] = []
        for x_start, z_start, x_end, z_end in segments:
            minimum_x = min(x_start, x_end) - tolerance
            maximum_x = max(x_start, x_end) + tolerance
            if not minimum_x <= x_value <= maximum_x:
                continue
            if math.isclose(x_start, x_end, abs_tol=tolerance):
                if math.isclose(x_value, x_start, abs_tol=tolerance):
                    intersections.extend((z_start, z_end))
                continue
            fraction = (x_value - x_start) / (x_end - x_start)
            intersections.append(z_start + fraction * (z_end - z_start))

        if intersections:
            profile.append(
                {
                    "geometry_index": 1,
                    "layer_index": 0,
                    "layer_id": "",
                    "layer_name": "Dwarsprofiel",
                    "point_index": point_index,
                    "x": x_value,
                    "z": max(intersections),
                }
            )

    if len(profile) < 2:
        raise ValueError(
            "Het dwarsprofiel kon niet uit de geometrie worden bepaald."
        )
    return profile


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
        figure.add_trace(
            go.Scatter(
                x=[row["x"] for row in plotted],
                y=[row["z"] for row in plotted],
                mode="lines+markers",
                name=f"G{geometry_index} L{layer_index}: {layer_name}",
                hovertemplate=(
                    "X=%{x:.3f}<br>Z=%{y:.3f}"
                    "<extra>%{fullData.name}</extra>"
                ),
            )
        )

    figure.update_layout(
        title="Geometrie uit het STIX-bestand",
        xaxis_title="X",
        yaxis_title="Z",
        hovermode="closest",
        legend_title="Lagen",
        margin=dict(l=20, r=20, t=55, b=20),
    )
    figure.update_yaxes(scaleanchor="x", scaleratio=1)
    return figure


def collect_geometry_x(model: DStabilityModel) -> list[float]:
    """Verzamel alle X-coordinaten uit de geometrie."""
    return [row["x"] for row in extract_geometry_rows(model)]


def pydantic_field_names(model: BaseModel) -> tuple[str, ...]:
    """Geef gedeclareerde veldnamen voor Pydantic 1 en 2."""
    fields = getattr(type(model), "model_fields", None)
    if fields is not None:
        return tuple(fields.keys())
    return tuple(getattr(model, "__fields__", {}).keys())


def is_number(value: Any) -> bool:
    """Controleer op een reeel getal, maar sluit booleans uit."""
    return isinstance(value, Real) and not isinstance(value, bool)


def transform_mapping_fields(
    field_names: tuple[str, ...],
    get_value: Callable[[str], Any],
    set_value: Callable[[str, Any], None],
    operation: str,
    shift: float,
) -> set[str]:
    """Transformeer bekende X-velden eenmaal."""
    by_normalized = {name.casefold(): name for name in field_names}
    handled: set[str] = set()

    if operation == OPERATION_MIRROR:
        for left_key, right_key in MIRROR_FIELD_PAIRS:
            left_name = by_normalized.get(left_key)
            right_name = by_normalized.get(right_key)
            if left_name is None or right_name is None or left_name == right_name:
                continue
            left_value = get_value(left_name)
            right_value = get_value(right_name)
            if is_number(left_value) and is_number(right_value):
                set_value(left_name, -float(right_value))
                set_value(right_name, -float(left_value))
                handled.update((left_name, right_name))

    for field_name in field_names:
        if field_name in handled or field_name.casefold() not in X_FIELD_NAMES:
            continue
        value = get_value(field_name)
        if not is_number(value):
            continue
        transformed = (
            -float(value)
            if operation == OPERATION_MIRROR
            else float(value) + shift
        )
        set_value(field_name, transformed)
        handled.add(field_name)

    return handled


def transform_object_tree_x(
    obj: Any,
    operation: str,
    shift: float = 0.0,
    memo: set[int] | None = None,
) -> None:
    """Transformeer gedeclareerde X-velden eenmaal in een objectboom."""
    if obj is None or isinstance(obj, (str, bytes, int, float, bool)):
        return
    if memo is None:
        memo = set()
    if id(obj) in memo:
        return
    memo.add(id(obj))

    if isinstance(obj, BaseModel):
        field_names = pydantic_field_names(obj)
        handled = transform_mapping_fields(
            field_names,
            lambda name: getattr(obj, name, None),
            lambda name, value: setattr(obj, name, value),
            operation,
            shift,
        )
        for field_name in field_names:
            if field_name not in handled:
                transform_object_tree_x(
                    getattr(obj, field_name, None), operation, shift, memo
                )
        return

    if isinstance(obj, dict):
        field_names = tuple(key for key in obj if isinstance(key, str))
        handled = transform_mapping_fields(
            field_names,
            obj.get,
            obj.__setitem__,
            operation,
            shift,
        )
        for key, value in obj.items():
            if key not in handled:
                transform_object_tree_x(value, operation, shift, memo)
        return

    if isinstance(obj, (list, tuple, set)):
        for item in obj:
            transform_object_tree_x(item, operation, shift, memo)


def reverse_geometry_polygon_points(model: DStabilityModel) -> None:
    """Herstel alleen de winding order van geometrische polygonen."""
    geometries = getattr(model.datastructure, "geometries", None)
    for geometry in iter_items(geometries):
        layers = getattr(geometry, "Layers", None)
        if layers is None:
            layers = getattr(geometry, "layers", None)
        for layer in iter_items(layers):
            points = getattr(layer, "Points", None)
            if points is None:
                points = getattr(layer, "points", None)
            if isinstance(points, list) and len(points) > 2:
                points.reverse()


def shift_model_x(model: DStabilityModel, existing_x_to_zero: float) -> None:
    """Verschuif alle bekende X-coordinaten in het model."""
    if not collect_geometry_x(model):
        raise ValueError("Geen geometrie gevonden in het D-Stability-bestand.")
    transform_object_tree_x(
        model.datastructure,
        OPERATION_SHIFT,
        shift=-float(existing_x_to_zero),
    )


def mirror_model_x(model: DStabilityModel) -> None:
    """Spiegel bekende X-coordinaten rond X = 0."""
    if not collect_geometry_x(model):
        raise ValueError("Geen geometrie gevonden in het D-Stability-bestand.")
    transform_object_tree_x(model.datastructure, OPERATION_MIRROR)
    reverse_geometry_polygon_points(model)


def assert_close_sequences(
    actual: list[float], expected: list[float], label: str
) -> None:
    """Valideer numerieke reeksen onafhankelijk van puntvolgorde."""
    if len(actual) != len(expected):
        raise ValueError(f"Aantal {label} is na verwerking gewijzigd.")
    for actual_value, expected_value in zip(sorted(actual), sorted(expected)):
        if not math.isclose(
            actual_value, expected_value, rel_tol=1e-9, abs_tol=1e-8
        ):
            raise ValueError(f"Validatie van {label} na verwerking is mislukt.")


def validate_transformed_geometry(
    original_rows: list[dict[str, Any]],
    output_rows: list[dict[str, Any]],
    operation: str,
    existing_x_to_zero: float,
) -> None:
    """Controleer aantallen en geometriecoordinaten na roundtrip."""
    if len(output_rows) != len(original_rows):
        raise ValueError("Het aantal geometriepunten is na verwerking gewijzigd.")

    original_x = [row["x"] for row in original_rows]
    output_x = [row["x"] for row in output_rows]
    expected_x = (
        [value - existing_x_to_zero for value in original_x]
        if operation == OPERATION_SHIFT
        else [-value for value in original_x]
    )
    assert_close_sequences(output_x, expected_x, "X-coordinaten")
    assert_close_sequences(
        [row["z"] for row in output_rows],
        [row["z"] for row in original_rows],
        "Z-coordinaten",
    )


@st.cache_data(show_spinner=False)
def read_original_geometry(data: bytes) -> list[dict[str, Any]]:
    """Lees geometrie uit een STIX-bestand zonder het model te wijzigen."""
    if not data:
        raise ValueError("Het geuploade bestand is leeg.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("Het bestand is groter dan de toegestane 100 MB.")

    with tempfile.TemporaryDirectory() as temp_dir:
        input_path = Path(temp_dir) / "input.stix"
        input_path.write_bytes(data)

        model = DStabilityModel()
        model.parse(input_path)
        rows = extract_geometry_rows(model)
        if not rows:
            raise ValueError("Geen geometrie gevonden in het D-Stability-bestand.")
        return rows


def process_stix(
    data: bytes,
    operation: str,
    existing_x_to_zero: float = 0.0,
) -> tuple[bytes, float, float, list[dict[str, Any]]]:
    """Bewerk STIX en valideer het resultaat via een roundtrip."""
    if not data:
        raise ValueError("Het geuploade bestand is leeg.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("Het bestand is groter dan de toegestane 100 MB.")
    if operation not in {OPERATION_SHIFT, OPERATION_MIRROR}:
        raise ValueError("Onbekende bewerking geselecteerd.")
    if not math.isfinite(float(existing_x_to_zero)):
        raise ValueError("De ingevoerde X-waarde moet een eindig getal zijn.")

    with tempfile.TemporaryDirectory() as temp_dir:
        input_path = Path(temp_dir) / "input.stix"
        output_path = Path(temp_dir) / "output.stix"
        input_path.write_bytes(data)

        model = DStabilityModel()
        model.parse(input_path)
        original_rows = extract_geometry_rows(model)
        if not original_rows:
            raise ValueError("Geen geometrie gevonden in het D-Stability-bestand.")

        if operation == OPERATION_SHIFT:
            shift_model_x(model, float(existing_x_to_zero))
        else:
            mirror_model_x(model)

        model.serialize(output_path)
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise ValueError("D-Stability heeft geen geldig uitvoerbestand gemaakt.")

        validated_model = DStabilityModel()
        validated_model.parse(output_path)
        validated_rows = extract_geometry_rows(validated_model)
        if not validated_rows:
            raise ValueError(
                "Het uitvoerbestand is leesbaar, maar bevat geen geometriepunten."
            )
        validate_transformed_geometry(
            original_rows,
            validated_rows,
            operation,
            float(existing_x_to_zero),
        )
        x_values = [row["x"] for row in validated_rows]
        return output_path.read_bytes(), min(x_values), max(x_values), validated_rows


def make_output_name(input_name: str, operation: str) -> str:
    """Maak een veilige uitvoerbestandsnaam met extensie .stix."""
    stem = Path(Path(input_name).name).stem or "dstability"
    suffix = "GESPIEGELD" if operation == OPERATION_MIRROR else "VERSCHOVEN"
    return f"{stem}_{suffix}.stix"


def make_original_csv_name(input_name: str, export_type: str) -> str:
    """Maak een veilige CSV-bestandsnaam voor het gekozen exporttype."""
    stem = Path(Path(input_name).name).stem or "dstability"
    suffix = "dwarsprofiel" if export_type == "Enkel dwarsprofiel" else "geometrie"
    return f"{stem}_{suffix}_origineel.csv"


def render_original_geometry_tab(input_data: bytes, input_name: str) -> None:
    """Toon en exporteer de oorspronkelijke, ongewijzigde geometrie."""
    st.subheader("Oorspronkelijke geometrie")
    st.caption(
        "De geometrie wordt rechtstreeks uit het geuploade bestand gelezen. "
        "Er wordt geen verschuiving of spiegeling toegepast."
    )

    try:
        with st.spinner("Geometrie uitlezen..."):
            original_rows = read_original_geometry(input_data)
    except (ValueError, OSError, AttributeError, TypeError) as exc:
        st.error(f"De geometrie kon niet worden gelezen: {exc}")
        return
    except Exception:
        LOGGER.exception("Onverwachte fout bij uitlezen van geometrie")
        st.error(
            "De geometrie kon niet worden gelezen. Controleer het "
            "STIX-bestand of neem contact op met de beheerder."
        )
        return

    x_values = [row["x"] for row in original_rows]
    left, middle, right = st.columns(3)
    left.metric("Minimale X", f"{min(x_values):.3f}")
    middle.metric("Maximale X", f"{max(x_values):.3f}")
    right.metric("Aantal geometriepunten", len(original_rows))

    st.plotly_chart(
        build_geometry_figure(original_rows),
        use_container_width=True,
        key="original_geometry_chart",
    )
    st.divider()
    st.subheader("CSV-export")
    export_type = st.radio(
        "Welke gegevens wil je downloaden?",
        ("Volledige geometrie", "Enkel dwarsprofiel"),
        horizontal=True,
        key="original_geometry_export_type",
    )

    export_rows = (
        extract_cross_section_rows(original_rows)
        if export_type == "Enkel dwarsprofiel"
        else original_rows
    )
    if export_type == "Enkel dwarsprofiel":
        st.caption(
            "Het dwarsprofiel is de bovencontour van de geometrie: per "
            "X-positie wordt de hoogste doorsnijding met de laagpolygonen gebruikt."
        )
        st.plotly_chart(
            build_geometry_figure(export_rows),
            use_container_width=True,
            key="original_cross_section_chart",
        )

    st.download_button(
        f"Download {export_type.lower()} als CSV",
        data=geometry_rows_to_csv(export_rows),
        file_name=make_original_csv_name(input_name, export_type),
        mime="text/csv",
        key="download_original_geometry_csv",
        type="primary",
    )


def render_edit_tab(
    input_data: bytes,
    input_name: str,
    input_digest: str,
) -> None:
    """Toon bewerkingen en downloads voor het aangepaste model."""
    st.subheader("Geometrie bewerken")
    operation = st.radio(
        "Bewerking",
        (OPERATION_SHIFT, OPERATION_MIRROR),
        horizontal=True,
        key="operation",
        on_change=clear_result,
    )

    with st.form("dstability_form"):
        existing_x_to_zero = st.number_input(
            "Bestaande X-waarde die X = 0 moet worden",
            value=0.0,
            format="%.3f",
            disabled=operation != OPERATION_SHIFT,
        )
        submitted = st.form_submit_button(
            "Bestand verwerken",
            type="primary",
        )

    if submitted:
        try:
            with st.spinner("STIX-bestand verwerken..."):
                output, min_x, max_x, rows = process_stix(
                    input_data,
                    operation,
                    float(existing_x_to_zero),
                )
            message = (
                f"De oorspronkelijke X-waarde {existing_x_to_zero:.3f} "
                "is nu X = 0.000."
                if operation == OPERATION_SHIFT
                else "Het model is gespiegeld rond X = 0."
            )
            st.session_state[RESULT_KEY] = {
                "schema_version": RESULT_SCHEMA_VERSION,
                "operation": operation,
                "input_digest": input_digest,
                "data": output,
                "file_name": make_output_name(input_name, operation),
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
            LOGGER.exception("Onverwachte fout bij verwerking van STIX-bestand")
            st.error(
                "Het bestand kon niet worden verwerkt. Controleer het "
                "STIX-bestand of neem contact op met de beheerder."
            )

    result = get_valid_result(input_digest, operation)
    if result is None:
        st.info(
            "Kies een bewerking en klik op 'Bestand verwerken' om een "
            "aangepast STIX-bestand te maken."
        )
        return

    st.success(result["message"])
    left, middle, right = st.columns(3)
    left.metric("Nieuwe minimale X", f"{result['min_x']:.3f}")
    middle.metric("Nieuwe maximale X", f"{result['max_x']:.3f}")
    right.metric("Aantal geometriepunten", len(result["geometry_rows"]))

    st.subheader("Bewerkte geometrie")
    st.plotly_chart(
        build_geometry_figure(result["geometry_rows"]),
        use_container_width=True,
        key="processed_geometry_chart",
    )
    st.download_button(
        "Download verwerkt STIX-bestand",
        data=result["data"],
        file_name=result["file_name"],
        mime="application/octet-stream",
        key="download_processed_stix",
        type="primary",
    )
    csv_name = f"{Path(result['file_name']).stem}_geometrie.csv"
    st.download_button(
        "Download bewerkte geometrie als CSV",
        data=geometry_rows_to_csv(result["geometry_rows"]),
        file_name=csv_name,
        mime="text/csv",
        key="download_processed_geometry_csv",
    )


def main() -> None:
    """Render de Streamlit-app."""
    st.set_page_config(
        page_title="D-Stability geometriebewerker",
        page_icon="📐",
        layout="wide",
    )
    st.title("D-Stability geometriebewerker")
    st.write(
        "Upload een `.stix`-bestand. Je kunt de oorspronkelijke geometrie "
        "direct bekijken en downloaden, of het model verschuiven of spiegelen."
    )

    uploaded = st.file_uploader(
        "D-Stability-bestand",
        type=["stix"],
        key="stix_upload",
        on_change=clear_result,
    )
    if uploaded is None:
        st.info("Upload een STIX-bestand om de geometrie te bekijken.")
        return

    try:
        input_data = uploaded.getvalue()
    except (AttributeError, OSError) as exc:
        st.error(f"Het geuploade bestand kon niet worden gelezen: {exc}")
        return

    if not input_data:
        st.error("Het geuploade bestand is leeg.")
        return
    if len(input_data) > MAX_UPLOAD_BYTES:
        st.error("Het bestand is groter dan de toegestane 100 MB.")
        return

    input_digest = hashlib.sha256(input_data).hexdigest()
    geometry_tab, edit_tab = st.tabs(
        ["Geometrie bekijken en downloaden", "Bewerken en exporteren"]
    )

    with geometry_tab:
        render_original_geometry_tab(input_data, uploaded.name)

    with edit_tab:
        render_edit_tab(input_data, uploaded.name, input_digest)


if __name__ == "__main__":
    main()
