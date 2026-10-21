from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from src.data import normalize_name
from src.postgres_data import DatabaseLoadError, load_postgres_data


ROOT = Path(__file__).resolve().parents[1]

st.set_page_config(
    page_title="Registros | CONANP",
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="expanded",
)


def apply_styles() -> None:
    styles_path = ROOT / "assets" / "styles.css"
    if styles_path.exists():
        st.markdown(
            f"<style>{styles_path.read_text(encoding='utf-8')}</style>",
            unsafe_allow_html=True,
        )


def require_optional_password() -> None:
    try:
        expected = st.secrets.get("APP_PASSWORD", "")
    except Exception:
        expected = ""

    if not expected or st.session_state.get("authenticated"):
        return

    st.title("Acceso al sistema")
    password = st.text_input("Contraseña", type="password")

    if st.button("Ingresar", type="primary", use_container_width=True):
        if password == expected:
            st.session_state.authenticated = True
            st.rerun()
        st.error("La contraseña no es correcta.")

    st.stop()


def clean_polygon_label(value: object) -> str:
    if pd.isna(value):
        return ""
    return " ".join(
        str(value).replace("\u00a0", " ").strip().split()
    ).upper()


def polygon_key(value: object) -> str:
    return normalize_name(clean_polygon_label(value))


def filter_period_and_polygon(
    data: pd.DataFrame,
    start_date: date,
    end_date: date,
    polygons: list[str],
) -> pd.DataFrame:
    if data.empty or "fecha" not in data.columns:
        return data.copy()

    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    dates = pd.to_datetime(data["fecha"], errors="coerce")
    mask = dates.between(start, end)

    if polygons and "poligono" in data.columns:
        selected_keys = {polygon_key(value) for value in polygons}
        mask &= data["poligono"].map(polygon_key).isin(selected_keys)

    return data.loc[mask].copy()


def text_search(data: pd.DataFrame, value: str) -> pd.DataFrame:
    if not value:
        return data

    mask = (
        data.astype("string")
        .apply(
            lambda column: column.str.contains(
                value,
                case=False,
                na=False,
                regex=False,
            )
        )
        .any(axis=1)
    )
    return data.loc[mask]


def options(data: pd.DataFrame, column: str) -> list[str]:
    if column not in data.columns:
        return []
    return sorted(data[column].dropna().astype(str).unique())


apply_styles()
require_optional_password()

try:
    connection = st.connection("postgresql", type="sql")
    supervisions, recorridos, _ = load_postgres_data(connection)
except DatabaseLoadError as exc:
    st.error(str(exc))
    st.info(
        "Comprueba la conexión PostgreSQL configurada en "
        ".streamlit/secrets.toml."
    )
    st.stop()


with st.sidebar:
    st.markdown("### Navegación")
    st.page_link("app.py", label="Tablero principal")
    st.page_link("pages/1_Registros.py", label="Registros")
    st.page_link(
        "pages/2_Administracion_de_datos.py",
        label="Calidad de datos y Carga de datos",
    )

    st.divider()
    st.markdown("### Fuente de información")
    st.success("PostgreSQL conectado")
    st.caption("Base: registro_recorridos · Esquema: dw")
    st.caption("### Filtros generales")

    all_dates = pd.concat(
        [supervisions["fecha"], recorridos["fecha"]],
        ignore_index=True,
    )
    all_dates = pd.to_datetime(all_dates, errors="coerce").dropna()

    if all_dates.empty:
        st.error("No existen fechas válidas para aplicar filtros.")
        st.stop()

    min_date = all_dates.min().date()
    max_date = all_dates.max().date()
    selected_dates = st.date_input(
        "Periodo",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
    )

    if isinstance(selected_dates, tuple) and len(selected_dates) == 2:
        start_date, end_date = selected_dates
    else:
        start_date = end_date = (
            selected_dates if isinstance(selected_dates, date) else min_date
        )

    polygon_values = pd.concat(
        [supervisions["poligono"], recorridos["poligono"]],
        ignore_index=True,
    ).dropna()
    polygon_labels: dict[str, str] = {}
    for value in polygon_values:
        label = clean_polygon_label(value)
        key = polygon_key(value)
        if label and key and key not in polygon_labels:
            polygon_labels[key] = label

    selected_polygons = st.multiselect(
        "Polígono",
        sorted(polygon_labels.values()),
        placeholder="Todos",
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

st.title("Registros")
st.caption(
    "Consulta, filtra y descarga los registros de supervisiones y recorridos."
)

supervision_tab, recorrido_tab = st.tabs(["Supervisiones", "Recorridos"])

with supervision_tab:
    supervision_columns = [
        "fecha", "hora", "tipos_falta", "poligono", "subzona",
        "subpoligono", "medida_tomada", "embarcacion",
        "embarcacion_anonima", "num_faltas", "fuente",
    ]
    visible_supervisions = filtered_supervisions[
        [column for column in supervision_columns if column in filtered_supervisions.columns]
    ].copy()

    with st.expander("Filtros de supervisiones", expanded=True):
        sup_left, sup_right = st.columns(2)
        with sup_left:
            supervision_search = st.text_input(
                "Buscar en supervisiones",
                placeholder="Embarcación, falta, subzona, medida...",
                key="page_supervision_search",
            )
            selected_faults = st.multiselect(
                "Tipo de falta",
                options(visible_supervisions, "tipos_falta"),
                placeholder="Todos",
                key="page_supervision_faults",
            )
        with sup_right:
            selected_measures = st.multiselect(
                "Medida tomada",
                options(visible_supervisions, "medida_tomada"),
                placeholder="Todas",
                key="page_supervision_measures",
            )
            selected_subzones = st.multiselect(
                "Subzona de supervisión",
                options(visible_supervisions, "subzona"),
                placeholder="Todas",
                key="page_supervision_subzones",
            )

    visible_supervisions = text_search(visible_supervisions, supervision_search)
    for column, selected in (
        ("tipos_falta", selected_faults),
        ("medida_tomada", selected_measures),
        ("subzona", selected_subzones),
    ):
        if selected and column in visible_supervisions.columns:
            visible_supervisions = visible_supervisions[
                visible_supervisions[column].astype(str).isin(selected)
            ]

    if "fecha" in visible_supervisions.columns:
        visible_supervisions = visible_supervisions.sort_values("fecha", ascending=False)

    st.caption(f"Registros encontrados: {len(visible_supervisions):,}")
    st.dataframe(
        visible_supervisions,
        use_container_width=True,
        hide_index=True,
        height=500,
    )
    st.download_button(
        "Descargar supervisiones filtradas en CSV",
        data=visible_supervisions.to_csv(index=False).encode("utf-8-sig"),
        file_name="supervisiones_filtradas.csv",
        mime="text/csv",
        key="page_download_supervisions",
    )

with recorrido_tab:
    recorrido_columns = [
        "fecha", "hora_inicio", "hora_fin", "poligono", "subzona",
        "subpoligono", "tipo_actividad", "vehiculo", "km_recorridos",
    ]
    visible_recorridos = filtered_recorridos[
        [column for column in recorrido_columns if column in filtered_recorridos.columns]
    ].copy()

    with st.expander("Filtros de recorridos", expanded=True):
        route_left, route_right = st.columns(2)
        with route_left:
            recorrido_search = st.text_input(
                "Buscar en recorridos",
                placeholder="Vehículo, actividad, subzona...",
                key="page_recorrido_search",
            )
            selected_activities = st.multiselect(
                "Tipo de actividad",
                options(visible_recorridos, "tipo_actividad"),
                placeholder="Todas",
                key="page_recorrido_activities",
            )
        with route_right:
            selected_vehicles = st.multiselect(
                "Vehículo",
                options(visible_recorridos, "vehiculo"),
                placeholder="Todos",
                key="page_recorrido_vehicles",
            )
            selected_route_subzones = st.multiselect(
                "Subzona del recorrido",
                options(visible_recorridos, "subzona"),
                placeholder="Todas",
                key="page_recorrido_subzones",
            )

    visible_recorridos = text_search(visible_recorridos, recorrido_search)
    for column, selected in (
        ("tipo_actividad", selected_activities),
        ("vehiculo", selected_vehicles),
        ("subzona", selected_route_subzones),
    ):
        if selected and column in visible_recorridos.columns:
            visible_recorridos = visible_recorridos[
                visible_recorridos[column].astype(str).isin(selected)
            ]

    if "fecha" in visible_recorridos.columns:
        visible_recorridos = visible_recorridos.sort_values("fecha", ascending=False)

    st.caption(f"Registros encontrados: {len(visible_recorridos):,}")
    st.dataframe(
        visible_recorridos,
        use_container_width=True,
        hide_index=True,
        height=500,
    )
    st.download_button(
        "Descargar recorridos filtrados en CSV",
        data=visible_recorridos.to_csv(index=False).encode("utf-8-sig"),
        file_name="recorridos_filtrados.csv",
        mime="text/csv",
        key="page_download_recorridos",
    )

st.caption("Módulo de consulta de registros de CONANP.")
