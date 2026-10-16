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
from src.data import load_geojson_bytes, normalize_name, quality_summary
from src.etl_view import render_etl_tab
from src.map_view import render_surveillance_map
from src.postgres_data import (
    DatabaseLoadError,
    load_postgres_data,
)


ROOT = Path(__file__).resolve().parent

DEFAULT_GEOJSON_PATH = (
    ROOT
    / "data"
    / "poligonos_marinos_artificiales_cancun.geojson"
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

# Valores predeterminados para permitir que la aplicación
# funcione cuando la base de datos está vacía.
selected_polygons: list[str] = []
selected_faults: list[str] = []

with st.sidebar:
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
    )

    all_dates = pd.to_datetime(
        all_dates,
        errors="coerce",
    ).dropna()

    # Fechas predeterminadas cuando la base está vacía.
    if all_dates.empty:
        min_date = date.today()
        max_date = date.today()
    else:
        min_date = all_dates.min().date()
        max_date = all_dates.max().date()

    start_date = min_date
    end_date = max_date

    selected_dates = st.date_input(
        "Periodo",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
    )

    # Interpretar correctamente el resultado.
    if isinstance(selected_dates, (tuple, list)):
        if len(selected_dates) == 2:
            start_date = selected_dates[0]
            end_date = selected_dates[1]
        elif len(selected_dates) == 1:
            start_date = selected_dates[0]
            end_date = selected_dates[0]

    elif isinstance(selected_dates, date):
        start_date = selected_dates
        end_date = selected_dates

    # Obtener todos los nombres de polígonos.
    polygon_values = pd.concat(
        [
            supervisions["poligono"],
            recorridos["poligono"],
        ],
        ignore_index=True,
    ).dropna()

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
        options=polygon_options,
        placeholder="Todos",
    )

    if "tipo_falta" in fault_details.columns:
        fault_options = sorted(
            fault_details["tipo_falta"]
            .dropna()
            .astype(str)
            .unique()
            .tolist()
        )
    else:
        fault_options = []

    selected_faults = st.multiselect(
        "Tipo de falta",
        options=fault_options,
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
    logo_path = ROOT / "assets" / "conanp_logo.png"

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
        "La base de datos está vacía. "
        "Utiliza la pestaña Carga ETL para importar "
        "los nuevos archivos."
    )


(
    overview_tab,
    spatial_tab,
    records_tab,
    quality_tab,
    etl_tab,
) = st.tabs(
    [
        "Resumen",
        "Análisis espacial",
        "Registros",
        "Calidad de datos",
        "Carga ETL",
    ]
)


with overview_tab:
    # Primera fila
    left, right = st.columns(
        [1.6, 1],
        gap="large",
    )

    with left:
        st.plotly_chart(
            build_monthly_trend(
                filtered_supervisions,
                filtered_recorridos,
                start_date,
                end_date,
            ),
            use_container_width=True,
        )

    with right:
        st.plotly_chart(
            build_fault_type_chart(
                filtered_fault_details
            ),
            use_container_width=True,
        )

    # Segunda fila
    # IMPORTANTE: debe estar fuera de "with right:"
    left, right = st.columns(
        [1.6, 1],
        gap="large",
    )

    with left:
        st.plotly_chart(
            build_recurrence_chart(
                filtered_supervisions
            ),
            use_container_width=True,
        )

    with right:
        st.plotly_chart(
            build_measure_chart(
                filtered_supervisions
            ),
            use_container_width=True,
        )


with spatial_tab:
   
    render_surveillance_map(
        filtered_supervisions,
        polygons_geojson,
    )


with records_tab:
    # ==========================================================
    # SUPERVISIONES
    # ==========================================================
    st.markdown("#### Supervisiones")

    supervision_columns = [
        "fecha",
        "hora",
        "tipos_falta",
        "poligono",
        "subzona",
        "subpoligono",
        "medida_tomada",
        "embarcacion_anonima",
        "num_faltas",
        "fuente",
    ]

    visible_supervisions = filtered_supervisions[
        [
            column
            for column in supervision_columns
            if column in filtered_supervisions.columns
        ]
    ].copy()

    with st.expander(
        "Filtros de supervisiones",
        expanded=True,
    ):
        sup_filter_1, sup_filter_2 = st.columns(2)

        with sup_filter_1:
            supervision_search = st.text_input(
                "Buscar en supervisiones",
                placeholder=(
                    "Embarcación, falta, subzona, medida..."
                ),
                key="supervision_search",
            )

            selected_supervision_faults = st.multiselect(
                "Tipo de falta",
                options=sorted(
                    visible_supervisions["tipos_falta"]
                    .dropna()
                    .astype(str)
                    .unique()
                )
                if "tipos_falta" in visible_supervisions.columns
                else [],
                key="record_supervision_faults",
                placeholder="Todos",
            )

        with sup_filter_2:
            selected_supervision_measures = st.multiselect(
                "Medida tomada",
                options=sorted(
                    visible_supervisions["medida_tomada"]
                    .dropna()
                    .astype(str)
                    .unique()
                )
                if "medida_tomada" in visible_supervisions.columns
                else [],
                key="record_supervision_measures",
                placeholder="Todas",
            )

            selected_supervision_subzones = st.multiselect(
                "Subzona de supervisión",
                options=sorted(
                    visible_supervisions["subzona"]
                    .dropna()
                    .astype(str)
                    .unique()
                )
                if "subzona" in visible_supervisions.columns
                else [],
                key="record_supervision_subzones",
                placeholder="Todas",
            )

    # Aplicar búsqueda general.
    if supervision_search:
        supervision_search_mask = (
            visible_supervisions.astype("string")
            .apply(
                lambda column: column.str.contains(
                    supervision_search,
                    case=False,
                    na=False,
                    regex=False,
                )
            )
            .any(axis=1)
        )

        visible_supervisions = visible_supervisions.loc[
            supervision_search_mask
        ]

    # Aplicar filtros específicos.
    if (
        selected_supervision_faults
        and "tipos_falta" in visible_supervisions.columns
    ):
        visible_supervisions = visible_supervisions[
            visible_supervisions["tipos_falta"]
            .astype(str)
            .isin(selected_supervision_faults)
        ]

    if (
        selected_supervision_measures
        and "medida_tomada" in visible_supervisions.columns
    ):
        visible_supervisions = visible_supervisions[
            visible_supervisions["medida_tomada"]
            .astype(str)
            .isin(selected_supervision_measures)
        ]

    if (
        selected_supervision_subzones
        and "subzona" in visible_supervisions.columns
    ):
        visible_supervisions = visible_supervisions[
            visible_supervisions["subzona"]
            .astype(str)
            .isin(selected_supervision_subzones)
        ]

    if "fecha" in visible_supervisions.columns:
        visible_supervisions = (
            visible_supervisions.sort_values(
                "fecha",
                ascending=False,
            )
        )

    st.caption(
        f"Registros encontrados: "
        f"{len(visible_supervisions):,}"
    )

    st.dataframe(
        visible_supervisions,
        use_container_width=True,
        hide_index=True,
        height=360,
    )

    st.download_button(
        "Descargar supervisiones filtradas en CSV",
        data=visible_supervisions.to_csv(
            index=False
        ).encode("utf-8-sig"),
        file_name="supervisiones_filtradas.csv",
        mime="text/csv",
        key="download_filtered_supervisions",
    )

    st.divider()

    # ==========================================================
    # RECORRIDOS
    # ==========================================================
    st.markdown("#### Recorridos")

    recorrido_columns = [
        "fecha",
        "hora_inicio",
        "hora_fin",
        "poligono",
        "subzona",
        "subpoligono",
        "tipo_actividad",
        "vehiculo",
        "km_recorridos",
    ]

    visible_recorridos = filtered_recorridos[
        [
            column
            for column in recorrido_columns
            if column in filtered_recorridos.columns
        ]
    ].copy()

    with st.expander(
        "Filtros de recorridos",
        expanded=True,
    ):
        recorrido_filter_1, recorrido_filter_2 = st.columns(2)

        with recorrido_filter_1:
            recorrido_search = st.text_input(
                "Buscar en recorridos",
                placeholder=(
                    "Vehículo, actividad, subzona..."
                ),
                key="recorrido_search",
            )

            selected_recorrido_activities = st.multiselect(
                "Tipo de actividad",
                options=sorted(
                    visible_recorridos["tipo_actividad"]
                    .dropna()
                    .astype(str)
                    .unique()
                )
                if "tipo_actividad" in visible_recorridos.columns
                else [],
                key="record_recorrido_activities",
                placeholder="Todas",
            )

        with recorrido_filter_2:
            selected_recorrido_vehicles = st.multiselect(
                "Vehículo",
                options=sorted(
                    visible_recorridos["vehiculo"]
                    .dropna()
                    .astype(str)
                    .unique()
                )
                if "vehiculo" in visible_recorridos.columns
                else [],
                key="record_recorrido_vehicles",
                placeholder="Todos",
            )

            selected_recorrido_subzones = st.multiselect(
                "Subzona del recorrido",
                options=sorted(
                    visible_recorridos["subzona"]
                    .dropna()
                    .astype(str)
                    .unique()
                )
                if "subzona" in visible_recorridos.columns
                else [],
                key="record_recorrido_subzones",
                placeholder="Todas",
            )

    # Aplicar búsqueda general.
    if recorrido_search:
        recorrido_search_mask = (
            visible_recorridos.astype("string")
            .apply(
                lambda column: column.str.contains(
                    recorrido_search,
                    case=False,
                    na=False,
                    regex=False,
                )
            )
            .any(axis=1)
        )

        visible_recorridos = visible_recorridos.loc[
            recorrido_search_mask
        ]

    # Aplicar filtros específicos.
    if (
        selected_recorrido_activities
        and "tipo_actividad" in visible_recorridos.columns
    ):
        visible_recorridos = visible_recorridos[
            visible_recorridos["tipo_actividad"]
            .astype(str)
            .isin(selected_recorrido_activities)
        ]

    if (
        selected_recorrido_vehicles
        and "vehiculo" in visible_recorridos.columns
    ):
        visible_recorridos = visible_recorridos[
            visible_recorridos["vehiculo"]
            .astype(str)
            .isin(selected_recorrido_vehicles)
        ]

    if (
        selected_recorrido_subzones
        and "subzona" in visible_recorridos.columns
    ):
        visible_recorridos = visible_recorridos[
            visible_recorridos["subzona"]
            .astype(str)
            .isin(selected_recorrido_subzones)
        ]

    if "fecha" in visible_recorridos.columns:
        visible_recorridos = visible_recorridos.sort_values(
            "fecha",
            ascending=False,
        )

    st.caption(
        f"Registros encontrados: "
        f"{len(visible_recorridos):,}"
    )

    st.dataframe(
        visible_recorridos,
        use_container_width=True,
        hide_index=True,
        height=360,
    )

    st.download_button(
        "Descargar recorridos filtrados en CSV",
        data=visible_recorridos.to_csv(
            index=False
        ).encode("utf-8-sig"),
        file_name="recorridos_filtrados.csv",
        mime="text/csv",
        key="download_filtered_recorridos",
    )


with quality_tab:
    quality = quality_summary(supervisions)

    q1, q2, q3 = st.columns(3)

    q1.metric(
        "Supervisiones cargadas",
        f"{quality['registros']:,}",
    )

    q2.metric(
        "Fechas válidas",
        f"{quality['fechas_validas_pct']:.1f}%",
    )

    q3.metric(
        "Polígonos identificados",
        f"{quality['poligonos_identificados_pct']:.1f}%",
    )

    st.markdown("#### Campos con valores faltantes")

    st.dataframe(
        quality["faltantes"],
        use_container_width=True,
        hide_index=True,
    )

    st.markdown("#### Controles del modelo")

    try:
        controls = connection.query(
            """
            SELECT *
            FROM dw.vw_control_calidad
            ORDER BY control
            """,
            ttl=0,
        )

        st.dataframe(
            controls,
            use_container_width=True,
            hide_index=True,
        )

    except Exception as exc:
        st.info(
            "Los controles de calidad estarán disponibles "
            "después de cargar los datos."
        )


with etl_tab:
    render_etl_tab(connection)


st.caption(
    "Sistema interactivo de visualización "
    "y análisis para la supervisión ambiental."
)
