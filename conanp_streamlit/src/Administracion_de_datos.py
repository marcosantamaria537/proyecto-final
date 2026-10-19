from __future__ import annotations

from pathlib import Path

import streamlit as st

from src.data import quality_summary
from src.etl_view import render_etl_tab
from src.postgres_data import DatabaseLoadError, load_postgres_data


ROOT = Path(__file__).resolve().parents[1]

st.set_page_config(
    page_title="Administración de datos | CONANP",
    page_icon="🗃️",
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

    if st.button("Ingresar", type="primary"):
        if password == expected:
            st.session_state.authenticated = True
            st.rerun()
        st.error("La contraseña no es correcta.")

    st.stop()


apply_styles()
require_optional_password()

# La navegación se crea solamente después de validar la contraseña.
with st.sidebar:
    st.page_link(
        "app.py",
        label="Tablero principal",
        icon="🏠",
    )
    st.page_link(
        "pages/2_Administracion_de_datos.py",
        label="Administración de datos",
        icon="🗃️",
    )
    st.divider()

st.title("Administración de datos")
st.caption(
    "Consulta la calidad de la información y carga nuevos archivos mediante el ETL."
)

try:
    connection = st.connection("postgresql", type="sql")
    supervisions, _, _ = load_postgres_data(connection)
except DatabaseLoadError as exc:
    st.error(str(exc))
    st.info(
        "Comprueba la conexión PostgreSQL configurada en "
        ".streamlit/secrets.toml."
    )
    st.stop()

quality_tab, etl_tab = st.tabs(
    [
        "Calidad de datos",
        "Carga ETL",
    ]
)

with quality_tab:
    quality = quality_summary(supervisions)

    q1, q2, q3 = st.columns(3)
    q1.metric("Supervisiones cargadas", f"{quality['registros']:,}")
    q2.metric("Fechas válidas", f"{quality['fechas_validas_pct']:.1f}%")
    q3.metric(
        "Polígonos identificados",
        f"{quality['poligonos_identificados_pct']:.1f}%",
    )

    st.markdown("#### Campos con valores faltantes")

    missing_fields = quality["faltantes"].loc[
        :,
        ["campo", "porcentaje"],
    ].copy()

    st.dataframe(
        missing_fields,
        use_container_width=True,
        hide_index=True,
        column_config={
            "campo": st.column_config.TextColumn("Campo"),
            "porcentaje": st.column_config.NumberColumn(
                "Porcentaje",
                format="%.1f%%",
            ),
        },
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
        st.warning(
            "No fue posible consultar dw.vw_control_calidad. "
            f"Detalle: {exc}"
        )

with etl_tab:
    render_etl_tab(connection)

st.caption("Módulo de administración y control de datos de CONANP.")
