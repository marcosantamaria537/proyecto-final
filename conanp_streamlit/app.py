from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from src.analytics import (
    build_fault_type_chart,
    build_measure_chart,
    build_monthly_trend,
    build_recurrence_chart,
    calculate_kpis,
)
from src.data import load_geojson_bytes, normalize_name
from src.map_view import render_surveillance_map
from src.postgres_data import (
    DatabaseLoadError,
    load_postgres_data,
)


ROOT = Path(__file__).resolve().parent

DEFAULT_GEOJSON_PATH = (
    ROOT
    / "data"
    / "sub_PNCOIMPCyPN.geojson"
)

st.set_page_config(
    page_title="Vigilancia CONANP",
    layout="wide",
    initial_sidebar_state="expanded",
)


def apply_styles() -> None:
    styles_path = ROOT / "assets" / "styles.css"

    if styles_path.exists():
        styles = styles_path.read_text(
            encoding="utf-8"
        )
        st.markdown(
            f"<style>{styles}</style>",
            unsafe_allow_html=True,
        )


def require_optional_password() -> None:
    try:
        expected = st.secrets.get(
            "APP_PASSWORD",
            "",
        )
    except Exception:
        expected = ""

    if (
        not expected
        or st.session_state.get("authenticated")
    ):
        return

    st.title("Acceso al tablero")

    password = st.text_input(
        "Contraseña",
        type="password",
    )

    if st.button(
        "Ingresar",
        type="primary",
        use_container_width=True,
    ):
        if password == expected:
            st.session_state.authenticated = True
            st.rerun()

        st.error(
            "La contraseña no es correcta."
        )

    st.stop()


@st.cache_data(show_spinner=False)
def cached_geojson_load(
    file_bytes: bytes,
    filename: str,
) -> dict:
    return load_geojson_bytes(
        file_bytes,
        filename,
    )


@st.cache_data(show_spinner=False)
def cached_default_geojson_load() -> dict | None:
    """Carga el GeoJSON incluido en la carpeta data."""

    if not DEFAULT_GEOJSON_PATH.exists():
        return None

    try:
        content = DEFAULT_GEOJSON_PATH.read_text(
            encoding="utf-8"
        )
        geojson = json.loads(content)

        if geojson.get("type") != "FeatureCollection":
            return None

        return geojson

    except (OSError, json.JSONDecodeError):
        return None
def clean_polygon_label(value: object) -> str:
    """Limpia el nombre visible del polígono."""
    if pd.isna(value):
        return ""

    return " ".join(
        str(value)
        .replace("\u00a0", " ")
        .strip()
        .split()
    ).upper()


def polygon_key(value: object) -> str:
    """Genera una clave común para comparar nombres."""
    return normalize_name(clean_polygon_label(value))

def filter_period_and_polygon(
    data: pd.DataFrame,
    start_date: date,
    end_date: date,
    polygons: list[str],
) -> pd.DataFrame:
    start = pd.Timestamp(start_date)
    end = (
        pd.Timestamp(end_date)
        + pd.Timedelta(days=1)
        - pd.Timedelta(microseconds=1)
    )

    mask = data["fecha"].between(start, end)

    if polygons:
        selected_keys = {
            polygon_key(polygon)
            for polygon in polygons
        }

        data_polygon_keys = data["poligono"].map(
            polygon_key
        )

        mask &= data_polygon_keys.isin(selected_keys)

    return data.loc[mask].copy()


apply_styles()
require_optional_password()

try:
    connection = st.connection(
        "postgresql",
        type="sql",
    )

    (
        supervisions,
        recorridos,
        fault_details,
    ) = load_postgres_data(connection)

except DatabaseLoadError as exc:
    st.error(str(exc))

    st.info(
        "Comprueba el archivo "
        ".streamlit/secrets.toml y confirma "
        "que primero hayas ejecutado "
        "cargar_postgres.py."
    )

    st.stop()


with st.sidebar:
    st.markdown("### Navegación")

    st.page_link(
        "app.py",
        label="Tablero principal",
        
    )

    st.page_link(
        "pages/2_Administracion_de_datos.py",
        label="Calidad de datos y carga de datos",
       
    )
    st.page_link(
            "pages/1_Registros.py",
            label="Registros",
           
        )

    st.divider()

    st.markdown(
        "### Fuente de información"
    )

    st.success(
        "PostgreSQL conectado"
    )

    st.caption(
        "Base: registro_recorridos · "
        "Esquema: dw"
    )

    # Carga automática del GeoJSON del proyecto.
    polygons_geojson = (
        cached_default_geojson_load()
    )

    if polygons_geojson:
        number_of_polygons = len(
            polygons_geojson.get(
                "features",
                [],
            )
        )

        st.success(
            "Polígonos marinos cargados: "
            f"{number_of_polygons}"
        )

    else:
        st.error(
            "No se encontró el GeoJSON en: "
            f"{DEFAULT_GEOJSON_PATH}"
        )

    polygon_file = st.file_uploader(
        "Cargar otro archivo de polígonos",
        type=[
            "geojson",
            "json",
            "zip",
        ],
        help=(
            "Opcionalmente puedes sustituir "
            "los polígonos predeterminados."
        ),
    )

    if polygon_file:
        try:
            polygons_geojson = cached_geojson_load(
                polygon_file.getvalue(),
                polygon_file.name,
            )

            st.success(
                "Se está utilizando el archivo "
                "cargado manualmente."
            )

        except Exception as exc:
            st.warning(
                "No fue posible leer los "
                f"polígonos: {exc}"
            )

    st.divider()
    st.markdown("### Filtros")

    all_dates = pd.concat(
        [
            supervisions["fecha"],
            recorridos["fecha"],
        ],
        ignore_index=True,
    ).dropna()

    min_date = all_dates.min().date()
    max_date = all_dates.max().date()

    selected_dates = st.date_input(
        "Periodo",
        value=(
            min_date,
            max_date,
        ),
        min_value=min_date,
        max_value=max_date,
    )

    if (
        isinstance(selected_dates, tuple)
        and len(selected_dates) == 2
    ):
        start_date, end_date = selected_dates
    else:
        start_date = end_date = (
            selected_dates
            if isinstance(selected_dates, date)
            else min_date
        )

    # Obtener todos los nombres de polígonos.
    polygon_values = pd.concat(
        [
            supervisions["poligono"],
            recorridos["poligono"],
        ],
        ignore_index=True,
    ).dropna()

    # Eliminar nombres repetidos aunque tengan
    # espacios, acentos o mayúsculas diferentes.
    polygon_labels: dict[str, str] = {}

    for value in polygon_values:
        label = clean_polygon_label(value)
        key = polygon_key(value)

        if (
            label
            and key
            and key not in polygon_labels
        ):
            polygon_labels[key] = label

    polygon_options = sorted(
        polygon_labels.values()
    )

    selected_polygons = st.multiselect(
        "Polígono",
        polygon_options,
        placeholder="Todos",
    )

    fault_options = sorted(
        fault_details["tipo_falta"]
        .dropna()
        .astype(str)
        .unique()
    )

    selected_faults = st.multiselect(
        "Tipo de falta",
        fault_options,
        placeholder="Todos",
    )

    st.caption(
        "El periodo y el polígono se aplican "
        "a supervisiones y recorridos."
    )


filtered_supervisions = filter_period_and_polygon(
    supervisions,
    start_date,
    end_date,
    selected_polygons,
)

filtered_recorridos = filter_period_and_polygon(
    recorridos,
    start_date,
    end_date,
    selected_polygons,
)

filtered_fault_details = filter_period_and_polygon(
    fault_details,
    start_date,
    end_date,
    selected_polygons,
)


if selected_faults:
    filtered_fault_details = (
        filtered_fault_details[
            filtered_fault_details[
                "tipo_falta"
            ].isin(selected_faults)
        ]
    )

    selected_ids = (
        filtered_fault_details[
            "supervision_id"
        ].unique()
    )

    filtered_supervisions = (
        filtered_supervisions[
            filtered_supervisions[
                "supervision_id"
            ].isin(selected_ids)
        ]
    )


header_left, header_right = st.columns([4, 1.5])

with header_left:
    st.markdown(
        '<p class="eyebrow">'
        "MONITOREO Y VIGILANCIA"
        "</p>",
        unsafe_allow_html=True,
    )

    st.title(
        "Tablero de recorridos y faltas"
    )

    st.caption(
        "Parque Nacional Costa Occidental "
        "de Isla Mujeres, Punta Cancún "
        "y Punta Nizuc"
    )

with header_right:
    logo_path = ROOT / "assets" / "Conanp_Logo_Vector.png"

    if logo_path.exists():
        st.image(
            str(logo_path),
            width=220,
        )

kpis = calculate_kpis(
    filtered_supervisions,
    filtered_recorridos,
)

m1, m2, m3, m4 = st.columns(4)

m1.metric(
    "Faltas registradas",
    f"{kpis['faltas']:,}",
)

m2.metric(
    "Recorridos",
    f"{kpis['recorridos']:,}",
)

m3.metric(
    "Supervisiones con falta",
    f"{kpis['porcentaje_con_falta']:.1f}%",
)

m4.metric(
    "Kilómetros recorridos",
    f"{kpis['kilometros']:,.1f}",
)


if (
    filtered_supervisions.empty
    and filtered_recorridos.empty
):
    st.warning(
        "No hay registros que coincidan "
        "con los filtros seleccionados."
    )
    st.stop()


(
    overview_tab,
    spatial_tab,
) = st.tabs(
    [
        "Resumen",
        "Análisis espacial",
    ]
)


with overview_tab:
    # Primera fila: evolución temporal a todo el ancho.
    st.plotly_chart(
        build_monthly_trend(
            filtered_supervisions,
            filtered_recorridos,
            start_date,
            end_date,
        ),
        use_container_width=True,
    )

    # Segunda fila: análisis categórico en dos columnas iguales.
    category_column, measure_column = st.columns(
        [1, 1],
        gap="large",
    )

    with category_column:
        st.plotly_chart(
            build_fault_type_chart(
                filtered_fault_details
            ),
            use_container_width=True,
        )

    with measure_column:
        st.plotly_chart(
            build_measure_chart(
                filtered_supervisions
            ),
            use_container_width=True,
        )

    # Tercera fila: reincidencia a todo el ancho para evitar etiquetas
    # amontonadas y facilitar la comparación entre embarcaciones.
    st.plotly_chart(
        build_recurrence_chart(
            filtered_supervisions
        ),
        use_container_width=True,
    )


with spatial_tab:
    render_surveillance_map(
    filtered_supervisions,
    polygons_geojson,
    filtered_recorridos,
)




st.caption(
    "Sistema interactivo de visualización "
    "y análisis para la supervisión ambiental."
)
