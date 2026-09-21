"""Streamlit-app voor het verschuiven van X-coördinaten in D-Stability."""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import streamlit as st
from geolib.models.dstability import DStabilityModel


@dataclass(frozen=True)
class XRange:
    """Beschrijft het minimale en maximale X-coördinaat."""

    minimum: float
    maximum: float

    def shifted(self, x_shift: float) -> "XRange":
        """Geef het bereik na toepassing van een X-verschuiving."""
        return XRange(
            minimum=self.minimum + x_shift,
            maximum=self.maximum + x_shift,
        )


@dataclass
class LoadedDStabilityFile:
    """Bevat het ingelezen D-Stability-model en bijbehorende metadata."""

    model: DStabilityModel
    file_name: str
    file_hash: str
    x_range: XRange


def iter_items(value: Any) -> Iterable"""Geef een lege iterable terug wanneer een collectie ontbreekt."""
    return value if value is not None else ()


def shift_point_x(point: Any, x_shift: float) -> None:
    """Verschuif de X-coördinaat van een punt indien aanwezig."""
    if point is not None and hasattr(point, "X") and point.X is not None:
        point.X += x_shift


def get_stages(data_structure: Any) -> Iterable"""Haal stages op met ondersteuning voor verschillende naamvarianten."""
    stages = getattr(data_structure, "Stages", None)

    if stages is None:
        stages = getattr(data_structure, "stages", None)

    return iter_items(stages)


def get_geometry_x_values(model: DStabilityModel) -> list"""Verzamel alle X-coördinaten uit de geometrische lagen."""
    x_values: list[float] = []
    geometries = getattr(model.datastructure, "geometries", None)

    for geometry in iter_items(geometries):
        layers = getattr(geometry, "Layers", None)

        for layer in iter_items(layers):
            points = getattr(layer, "Points", None)

            for point in iter_items(points):
                x_value = getattr(point, "X", None)

                if x_value is not None:
                    x_values.append(float(x_value))

    return x_values


def determine_x_range(model: DStabilityModel) -> XRange:
    """Bepaal het minimale en maximale geometrische X-coördinaat."""
    x_values = get_geometry_x_values(model)

    if not x_values:
        raise ValueError(
            "Het bestand bevat geen geometrie met bruikbare "
            "X-coördinaten."
        )

    return XRange(minimum=min(x_values), maximum=max(x_values))


def shift_geometry(model: DStabilityModel, x_shift: float) -> None:
    """Verschuif alle punten in de geometrie."""
    geometries = getattr(model.datastructure, "geometries", None)

    for geometry in iter_items(geometries):
        layers = getattr(geometry, "Layers", None)

        for layer in iter_items(layers):
            points = getattr(layer, "Points", None)

            for point in iter_items(points):
                shift_point_x(point, x_shift)


def shift_waternets(model: DStabilityModel, x_shift: float) -> None:
    """Verschuif punten in de stijghoogtelijnen van waternetten."""
    waternets = getattr(model.datastructure, "waternets", None)

    for waternet in iter_items(waternets):
        head_lines = getattr(waternet, "HeadLines", None)

        for head_line in iter_items(head_lines):
            points = getattr(head_line, "Points", None)

            for point in iter_items(points):
                shift_point_x(point, x_shift)


def shift_loads(model: DStabilityModel, x_shift: float) -> None:
    """Verschuif punt- en lijncoördinaten van belastingen."""
    loads = getattr(model.datastructure, "loads", None)

    for load in iter_items(loads):
        points = getattr(load, "Points", None)

        for point in iter_items(points):
            shift_point_x(point, x_shift)

        for attribute_name in ("X", "XEnd"):
            coordinate = getattr(load, attribute_name, None)

            if coordinate is not None:
                setattr(load, attribute_name, coordinate + x_shift)


def shift_search_grid(search_grid: Any, x_shift: float) -> None:
    """Verschuif de oorsprong van een zoekraster."""
    if search_grid is None:
        return

    origin = getattr(search_grid, "Origin", None)
    shift_point_x(origin, x_shift)


def shift_calculation_settings(
    calculation_settings: Any,
    x_shift: float,
) -> None:
    """Verschuif zoekrasters binnen ondersteunde rekeninstellingen."""
    if calculation_settings is None:
        return

    settings_names = (
        "BishopBruteForceSettings",
        "BishopSettings",
        "SpencerSettings",
        "SpencerGeneticSettings",
        "UpliftVanSettings",
        "UpliftVanMethodParticleSwarmSettings",
    )

    for settings_name in settings_names:
        method_settings = getattr(
            calculation_settings,
            settings_name,
            None,
        )

        if method_settings is None:
            continue

        search_grid = getattr(method_settings, "SearchGrid", None)
        shift_search_grid(search_grid, x_shift)


def shift_stages(model: DStabilityModel, x_shift: float) -> None:
    """Verschuif zoekrasters van alle stages."""
    for stage in get_stages(model.datastructure):
        calculation_settings = getattr(
            stage,
            "CalculationSettings",
            None,
        )
        shift_calculation_settings(calculation_settings, x_shift)


def shift_dstability_model(
    model: DStabilityModel,
    new_origin: float,
) -> float:
    """Verschuif alle ondersteunde X-coördinaten in een model.

    Args:
        model: Het te wijzigen D-Stability-model.
        new_origin: De bestaande X-waarde die na verschuiving nul moet zijn.

    Returns:
        De toegepaste verschuiving.
    """
    x_shift = -float(new_origin)

    shift_geometry(model, x_shift)
    shift_waternets(model, x_shift)
    shift_loads(model, x_shift)
    shift_stages(model, x_shift)

    return x_shift


def calculate_file_hash(file_bytes: bytes) -> str:
    """Bereken een stabiele hash waarmee uploads worden herkend."""
    return hashlib.sha256(file_bytes).hexdigest()


def load_dstability_file(
    file_bytes: bytes,
    file_name: str,
) -> LoadedDStabilityFile:
    """Lees een geüpload `.stix`-bestand in als D-Stability-model."""
    if not file_bytes:
        raise ValueError("Het geüploade bestand is leeg.")

    if Path(file_name).suffix.lower() != ".stix":
        raise ValueError("Selecteer een bestand met de extensie .stix.")

    model = DStabilityModel()

    with tempfile.TemporaryDirectory() as temporary_directory:
        input_path = Path(temporary_directory) / "input.stix"
        input_path.write_bytes(file_bytes)
        model.parse(input_path)

    return LoadedDStabilityFile(
        model=model,
        file_name=Path(file_name).name,
        file_hash=calculate_file_hash(file_bytes),
        x_range=determine_x_range(model),
    )


def serialize_dstability_model(model: DStabilityModel) -> bytes:
    """Serialiseer een model naar downloadbare `.stix`-bytes."""
    with tempfile.TemporaryDirectory() as temporary_directory:
        output_path = Path(temporary_directory) / "shifted_output.stix"
        model.serialize(output_path)

        if not output_path.exists():
            raise RuntimeError(
                "D-Stability heeft geen uitvoerbestand aangemaakt."
            )

        output_bytes = output_path.read_bytes()

    if not output_bytes:
        raise RuntimeError("Het aangemaakte uitvoerbestand is leeg.")

    return output_bytes


def make_output_file_name(input_file_name: str) -> str:
    """Maak een veilige naam voor het verschoven uitvoerbestand."""
    original_name = Path(input_file_name).name
    return f"shifted_{original_name}"


def initialize_session_state() -> None:
    """Initialiseer de benodigde Streamlit-sessiestatus."""
    defaults = {
        "loaded_file": None,
        "output_bytes": None,
        "output_file_name": None,
        "processed_new_origin": None,
    }

    for key, default_value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = default_value


def clear_output() -> None:
    """Verwijder een eerder gegenereerd uitvoerbestand."""
    st.session_state.output_bytes = None
    st.session_state.output_file_name = None
    st.session_state.processed_new_origin = None


def process_upload(uploaded_file: Any) -> None:
    """Lees een nieuwe upload in wanneer deze nog niet geladen is."""
    file_bytes = uploaded_file.getvalue()
    file_hash = calculate_file_hash(file_bytes)
    loaded_file = st.session_state.loaded_file

    if loaded_file is not None and loaded_file.file_hash == file_hash:
        return

    clear_output()

    with st.spinner("D-Stability-bestand wordt ingelezen..."):
        st.session_state.loaded_file = load_dstability_file(
            file_bytes=file_bytes,
            file_name=uploaded_file.name,
        )


def format_coordinate(value: float) -> str:
    """Formatteer een coördinaat compact en leesbaar."""
    return f"{value:,.3f}".replace(",", "_").replace(".", ",").replace("_", ".")


def render_range_metrics(
    original_range: XRange,
    shifted_range: XRange,
) -> None:
    """Toon het oorspronkelijke en verwachte nieuwe bereik."""
    first_column, second_column = st.columns(2)

    with first_column:
        st.metric(
            "Oorspronkelijk X-bereik",
            (
                f"{format_coordinate(original_range.minimum)} tot "
                f"{format_coordinate(original_range.maximum)}"
            ),
        )

    with second_column:
        st.metric(
            "Nieuw X-bereik",
            (
                f"{format_coordinate(shifted_range.minimum)} tot "
                f"{format_coordinate(shifted_range.maximum)}"
            ),
        )


def render_loaded_file(loaded_file: LoadedDStabilityFile) -> None:
    """Toon bediening en resultaten voor een ingelezen bestand."""
    st.success(f"Bestand ingelezen: {loaded_file.file_name}")

    default_origin = float(loaded_file.x_range.minimum)

    with st.form("shift_form"):
        new_origin = st.number_input(
            "Welke bestaande X-waarde moet het nieuwe nulpunt worden?",
            value=default_origin,
            format="%.6f",
            help=(
                "Deze waarde wordt van alle ondersteunde "
                "X-coördinaten afgetrokken."
            ),
        )

        x_shift = -float(new_origin)
        shifted_range = loaded_file.x_range.shifted(x_shift)

        render_range_metrics(
            original_range=loaded_file.x_range,
            shifted_range=shifted_range,
        )

        st.caption(
            f"Toe te passen verschuiving: "
            f"{format_coordinate(x_shift)}"
        )

        submitted = st.form_submit_button(
            "Verschuif X-coördinaten",
            type="primary",
            use_container_width=True,
        )

    if submitted:
        try:
            with st.spinner("X-coördinaten worden verschoven..."):
                # Het model wordt opnieuw geladen vanuit de uploadstatus
                # voorkomen is hier belangrijk: na een tweede submit zou
                # hetzelfde model anders nogmaals worden verschoven.
                uploaded_file = st.session_state.current_upload
                fresh_loaded_file = load_dstability_file(
                    file_bytes=uploaded_file["bytes"],
                    file_name=uploaded_file["name"],
                )

                applied_shift = shift_dstability_model(
                    model=fresh_loaded_file.model,
                    new_origin=float(new_origin),
                )

                output_bytes = serialize_dstability_model(
                    fresh_loaded_file.model
                )

                st.session_state.output_bytes = output_bytes
                st.session_state.output_file_name = make_output_file_name(
                    fresh_loaded_file.file_name
                )
                st.session_state.processed_new_origin = float(new_origin)

            st.success(
                "De X-coördinaten zijn succesvol verschoven. "
                f"De toegepaste verschuiving is "
                f"{format_coordinate(applied_shift)}."
            )
        except (ValueError, OSError, AttributeError, RuntimeError) as error:
            clear_output()
            st.error(
                "Het bestand kon niet worden verwerkt. "
                f"Controleer of het een geldig D-Stability-bestand is. "
                f"Details: {error}"
            )
        except Exception:
            clear_output()
            st.error(
                "Er is een onverwachte fout opgetreden tijdens het "
                "verwerken van het bestand. Controleer het bestand en "
                "probeer het opnieuw."
            )

    if st.session_state.output_bytes is not None:
        processed_origin = st.session_state.processed_new_origin
        processed_shift = -processed_origin
        processed_range = loaded_file.x_range.shifted(processed_shift)

        st.info(
            f"De oorspronkelijke X-waarde "
            f"{format_coordinate(processed_origin)} is nu 0,000. "
            f"Het nieuwe bereik is "
            f"{format_coordinate(processed_range.minimum)} tot "
            f"{format_coordinate(processed_range.maximum)}."
        )

        st.download_button(
            label="Download verschoven D-Stability-bestand",
            data=st.session_state.output_bytes,
            file_name=st.session_state.output_file_name,
            mime="application/octet-stream",
            type="primary",
            use_container_width=True,
        )


def main() -> None:
    """Start de Streamlit-app."""
    st.set_page_config(
        page_title="D-Stability X-coördinaten verschuiven",
        page_icon="↔️",
        layout="centered",
    )

    initialize_session_state()

    st.title("D-Stability X-coördinaten verschuiven")
    st.write(
        "Upload een `.stix`-bestand en kies welke bestaande "
        "X-coördinaat het nieuwe nulpunt moet worden."
    )

    st.warning(
        "De app verschuift geometrie, waternetten, belastingen en "
        "ondersteunde zoekrasters. Bewaar altijd het oorspronkelijke "
        "bestand als back-up."
    )

    uploaded_file = st.file_uploader(
        "Selecteer een D-Stability-bestand",
        type=["stix"],
        accept_multiple_files=False,
        help="Alleen bestanden met de extensie .stix worden geaccepteerd.",
    )

    if uploaded_file is None:
        st.session_state.loaded_file = None
        st.session_state.current_upload = None
        clear_output()
        st.info("Upload een `.stix`-bestand om te beginnen.")
        return

    st.session_state.current_upload = {
        "name": uploaded_file.name,
        "bytes": uploaded_file.getvalue(),
    }

    try:
        process_upload(uploaded_file)
    except (ValueError, OSError, AttributeError) as error:
        st.session_state.loaded_file = None
        clear_output()
        st.error(
            "Het bestand kon niet worden ingelezen. "
            f"Controleer of het een geldig D-Stability-bestand is. "
            f"Details: {error}"
        )
        return
    except Exception:
        st.session_state.loaded_file = None
        clear_output()
        st.error(
            "Er is een onverwachte fout opgetreden bij het inlezen. "
            "Controleer of het bestand geldig en niet beschadigd is."
        )
        return

    loaded_file = st.session_state.loaded_file

    if loaded_file is not None:
        render_loaded_file(loaded_file)


if __name__ == "__main__":
    main()
