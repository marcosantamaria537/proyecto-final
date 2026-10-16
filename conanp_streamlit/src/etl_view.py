from __future__ import annotations

import pandas as pd
import streamlit as st

from src.etl.etl import (
    ETLError,
    ETLPreview,
    load_preview,
    preview_excel,
)


def _show_preview(preview: ETLPreview) -> None:
    if preview.errors:
        for error in preview.errors:
            st.error(error)
        return
    for warning in preview.warnings:
        st.warning(warning)
    metric_1, metric_2, metric_3 = st.columns(3)
    metric_1.metric("Filas válidas", f"{preview.valid_rows:,}")
    metric_2.metric("Filas rechazadas", f"{preview.rejected_rows:,}")
    metric_3.metric("Hojas detectadas", len(preview.frames))
    for sheet, frame in preview.frames.items():
        with st.expander(f"Vista previa: {sheet} ({len(frame):,} filas)"):
            visible = frame.drop(
                columns=[column for column in ("_fault_names",) if column in frame],
                errors="ignore",
            )
            st.dataframe(visible.head(20), use_container_width=True, hide_index=True)


def _upload_section(connection, title: str, description: str, kind: str, key: str) -> None:
    st.markdown(f"#### {title}")
    st.caption(description)
    uploaded = st.file_uploader(
        "Seleccionar archivo Excel",
        type=["xlsx", "xlsm"],
        key=f"{key}_file",
        label_visibility="collapsed",
    )
    if uploaded is None:
        return
    preview = preview_excel(uploaded.getvalue(), uploaded.name, kind)
    _show_preview(preview)
    if preview.errors:
        return
    confirmed = st.checkbox(
        "Confirmo que revisé la vista previa y deseo cargar estos datos.",
        key=f"{key}_confirm",
    )
    if st.button(
        f"Procesar {title.lower()}",
        type="primary",
        disabled=not confirmed,
        key=f"{key}_button",
    ):
        try:
            with st.spinner("Validando, transformando y cargando datos..."):
                result = load_preview(connection.engine, preview)
        except ETLError as exc:
            st.error(str(exc))
            return
        except Exception as exc:
            st.error(
                "La transacción fue cancelada y no se guardaron datos parciales. "
                f"Detalle técnico: {exc}"
            )
            return
        if result.already_loaded:
            st.warning("Este mismo archivo ya había sido procesado. No se duplicaron datos.")
            return
        st.session_state["etl_success"] = (
            f"Carga terminada: {result.inserted:,} filas nuevas, "
            f"{result.duplicates:,} duplicadas omitidas y "
            f"{result.rejected:,} rechazadas."
        )
        try:
            connection.reset()
        except Exception:
            pass
        st.cache_data.clear()
        st.rerun()


def render_etl_tab(connection) -> None:
    st.markdown("### Carga y actualización de datos")
    st.info(
        "Cada archivo se valida antes de escribir en PostgreSQL. La carga utiliza una "
        "transacción y omite archivos o filas ya procesados."
    )
    message = st.session_state.pop("etl_success", None)
    if message:
        st.success(message)
    supervision_tab, recorrido_tab, history_tab = st.tabs(
        ["Supervisiones", "Recorridos", "Historial de cargas"]
    )
    with supervision_tab:
        _upload_section(
            connection,
            "Supervisiones",
            "Acepta un libro con la hoja FALTAS, SIN FALTAS o ambas.",
            "supervisiones",
            "etl_supervisiones",
        )
    with recorrido_tab:
        _upload_section(
            connection,
            "Recorridos",
            "El libro debe contener la hoja RECORRIDOS.",
            "recorridos",
            "etl_recorridos",
        )
    with history_tab:
        try:
            history = connection.query(
                """
                SELECT nombre_archivo, tipo_carga, filas_insertadas,
                       filas_duplicadas, filas_rechazadas, cargado_en
                FROM etl.carga_archivo
                ORDER BY cargado_en DESC
                LIMIT 100
                """,
                ttl=0,
                show_spinner=False,
            )
        except Exception:
            history = pd.DataFrame()
        if history.empty:
            st.caption("Aún no hay cargas registradas.")
        else:
            st.dataframe(history, use_container_width=True, hide_index=True)
