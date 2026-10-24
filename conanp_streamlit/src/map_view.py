from __future__ import annotations

import copy
import re
import unicodedata
from typing import Iterator

import folium
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from branca.colormap import linear
from streamlit_folium import st_folium

POLYGON_NAMES = {
    "poligono_1": "POLÍGONO 1 COSTA OCCIDENTAL DE ISLA MUJERES",
    "poligono_2": "POLÍGONO 2 PUNTA CANCÚN",
    "poligono_3": "POLÍGONO 3 PUNTA NIZUC",
}

# Transparencia del cuadro de información del mapa.
# 0.0 = invisible; 1.0 = completamente sólido.
TOOLTIP_OPACITY = 0.5


def _polygon_key(value: object) -> str:
    """Asigna cualquier variante de nombre a uno de los tres polígonos."""

    if value is None or pd.isna(value):
        return "sin_clasificar"

    # Normalización independiente: elimina acentos y convierte cualquier
    # signo, guion o guion bajo en un espacio. Así PostgreSQL y el GeoJSON
    # producen exactamente las mismas claves.
    normalized = unicodedata.normalize("NFKD", str(value))
    normalized = "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )
    normalized = normalized.casefold()
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized).strip()

    # Dar prioridad al número explícito del polígono y evitar que
    # "POLÍGONO 10" sea interpretado como "POLÍGONO 1".
    match = re.search(
        r"\bpoligono\s*0*(1|2|3)\b",
        normalized,
    )
    if match:
        return f"poligono_{int(match.group(1))}"

    # Variantes históricas asociadas al Polígono 1.
    if (
        "isla mujeres" in normalized
        or "zona de influencia" in normalized
    ):
        return "poligono_1"

    # Variantes históricas asociadas al Polígono 2.
    if (
        "punta cancun" in normalized
        or "puerto juarez" in normalized
        or "benito juarez" in normalized
    ):
        return "poligono_2"

    # Variantes históricas asociadas al Polígono 3.
    if "punta nizuc" in normalized:
        return "poligono_3"

    return "sin_clasificar"


def _subpolygon_key(value: object) -> str:
    """Normaliza una clave como ``(UPA 1-2)`` a ``UPA1-2``."""

    if value is None or pd.isna(value):
        return ""

    normalized = unicodedata.normalize("NFKD", str(value))
    normalized = "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    ).upper()
    return re.sub(r"[^A-Z0-9]+", "", normalized)


def _extract_subpolygon_keys(value: object) -> list[str]:
    """Extrae una o varias claves de la columna ``subpoligono``."""

    if value is None or pd.isna(value):
        return []

    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(
        character
        for character in text
        if not unicodedata.combining(character)
    ).upper()

    # Reconoce ASRN-1, RC-1, P 1-1, UPA 2-3 y UPPMA 3-1,
    # aunque tengan espacios o estén escritos entre paréntesis.
    matches = re.findall(
        r"\b(?:UPPMA|UPA|P)\s*[- ]?\s*\d+\s*-\s*\d+\b"
        r"|\b(?:ASRN|RC)\s*-\s*\d+\b",
        text,
    )
    keys = [_subpolygon_key(match) for match in matches]

    # Evita duplicar una clave dentro del mismo registro conservando el orden.
    return list(dict.fromkeys(keys))


def _prepare_polygon_counts(data: pd.DataFrame) -> pd.DataFrame:
    """Agrupa las faltas por subpolígono usando la clave oficial ABRE."""

    required_columns = {"subpoligono", "num_faltas"}
    if data.empty or not required_columns.issubset(data.columns):
        return pd.DataFrame(columns=["subpoligono_clave", "faltas"])

    working = data.copy()
    working["num_faltas"] = pd.to_numeric(
        working["num_faltas"],
        errors="coerce",
    ).fillna(0)
    working["subpoligono_clave"] = working["subpoligono"].map(
        _extract_subpolygon_keys
    )
    working = working.explode("subpoligono_clave")
    working = working[
        working["subpoligono_clave"].notna()
        & working["subpoligono_clave"].ne("")
    ].copy()

    polygon_counts = (
        working.groupby("subpoligono_clave", as_index=False)
        .agg(faltas=("num_faltas", "sum"))
    )
    polygon_counts["faltas"] = (
        polygon_counts["faltas"]
        .round()
        .astype(int)
    )
    return polygon_counts


def _prepare_geojson(geojson: dict, polygon_counts: pd.DataFrame) -> dict:
    """Normaliza GeoJSON generales o de subzonas y añade sus faltas.

    También admite el archivo oficial de subzonificación cuyos atributos
    son ISLA, NOM2, ABRE y SUBZONIF.
    """

    prepared = copy.deepcopy(geojson)
    count_map = dict(
        zip(
            polygon_counts.get(
                "subpoligono_clave",
                pd.Series(dtype=str),
            ),
            polygon_counts.get(
                "faltas",
                pd.Series(dtype=float),
            ),
        )
    )

    for feature in prepared.get("features", []):
        properties = feature.setdefault("properties", {})
        source_name = (
            properties.get("ISLA")
            or properties.get("isla")
            or properties.get("poligono")
            or properties.get("nombre")
            or properties.get("name")
            or properties.get("sector")
            or ""
        )
        key = _polygon_key(source_name)
        subpolygon_key = _subpolygon_key(
            properties.get("ABRE")
            or properties.get("clave_subzona")
            or ""
        )

        properties["__poligono_clave"] = key
        properties["__subpoligono_clave"] = subpolygon_key
        properties["faltas_registradas"] = int(
            round(count_map.get(subpolygon_key, 0))
        )
        # Campos comunes para que el mismo tooltip funcione con ambos
        # archivos GeoJSON. En el nuevo archivo, NOM2 es el nombre de la
        # subárea y SUBZONIF contiene su tipo de zonificación.
        properties["poligono"] = (
            POLYGON_NAMES.get(key)
            or properties.get("poligono")
            or str(source_name).strip()
            or "SIN ESPECIFICAR"
        )
        properties["subpoligono"] = (
            properties.get("NOM2")
            or properties.get("subpoligono")
            or properties.get("sector")
            or "SIN ESPECIFICAR"
        )
        properties["clave_subzona"] = (
            properties.get("ABRE")
            or properties.get("clave_subzona")
            or "SIN ESPECIFICAR"
        )
        properties["subzona"] = (
            properties.get("SUBZONIF")
            or properties.get("subzona")
            or "SIN ESPECIFICAR"
        )
        properties.setdefault("sector", properties["subpoligono"])
        properties.setdefault("superficie_declarada_ha", 0)

    return prepared


def _iterate_coordinates(coordinates: list) -> Iterator[tuple[float, float]]:
    """Recorre las coordenadas de geometrías Polygon y MultiPolygon."""

    if (
        isinstance(coordinates, list)
        and len(coordinates) >= 2
        and isinstance(coordinates[0], (int, float))
        and isinstance(coordinates[1], (int, float))
    ):
        yield float(coordinates[0]), float(coordinates[1])
        return

    if isinstance(coordinates, list):
        for item in coordinates:
            yield from _iterate_coordinates(item)


def _fit_geojson_bounds(map_object: folium.Map, geojson: dict) -> None:
    """Ajusta la vista del mapa a la extensión del GeoJSON."""

    points: list[tuple[float, float]] = []
    for feature in geojson.get("features", []):
        geometry = feature.get("geometry") or {}
        points.extend(
            _iterate_coordinates(geometry.get("coordinates") or [])
        )

    if not points:
        return

    longitudes = [point[0] for point in points]
    latitudes = [point[1] for point in points]
    map_object.fit_bounds(
        [
            [min(latitudes), min(longitudes)],
            [max(latitudes), max(longitudes)],
        ]
    )

    # Streamlit crea el mapa cuando la pestaña todavia puede estar oculta.
    # En ese momento Leaflet calcula un tamano incorrecto y muestra el mundo
    # completo. Recalcular el tamano varias veces permite que el mapa se
    # encuadre correctamente en cuanto la pestana se vuelve visible.
    map_name = map_object.get_name()
    bounds = [
        [min(latitudes), min(longitudes)],
        [max(latitudes), max(longitudes)],
    ]
    map_object.get_root().script.add_child(
        folium.Element(
            f"""
            (function () {{
                const map = {map_name};
                const bounds = {bounds!r};
                let attempts = 0;

                function refreshMap() {{
                    map.invalidateSize({{pan: false}});
                    map.fitBounds(bounds, {{padding: [20, 20]}});
                    attempts += 1;

                    if (attempts < 20) {{
                        window.setTimeout(refreshMap, 250);
                    }}
                }}

                window.setTimeout(refreshMap, 100);
                window.addEventListener("resize", refreshMap);
            }})();
            """
        )
    )


def render_surveillance_map(
    data: pd.DataFrame,
    geojson: dict | None,
    recorridos: pd.DataFrame | None = None,
) -> None:
    """Muestra los polígonos coloreados según sus faltas registradas."""

    coordinates = pd.DataFrame()
    if {"latitud", "longitud"}.issubset(data.columns):
        coordinates = data.dropna(subset=["latitud", "longitud"])

    if not coordinates.empty:
        center = [
            float(coordinates["latitud"].median()),
            float(coordinates["longitud"].median()),
        ]
    else:
        center = [21.127, -86.763]

    map_object = folium.Map(
        # Vista inicial fija para mostrar Isla Mujeres, Punta Cancun
        # y Punta Nizuc desde la primera apertura de la pestana.
        location=[21.127, -86.763],
        zoom_start=10.2,
        zoom_snap=0.1,
        tiles=None,
        control_scale=True,
    )

    # Fondo único que muestra simultáneamente tierra y mar.
    folium.TileLayer(
        tiles="https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        attr="© OpenStreetMap contributors",
        name="Mapa base: tierra y mar",
        overlay=False,
        control=False,
        show=True,
        max_zoom=19,
    ).add_to(map_object)

    if geojson:
        polygon_counts = _prepare_polygon_counts(data)
        prepared = _prepare_geojson(geojson, polygon_counts)

        # Capa base visible incluso cuando un polígono no tiene faltas.
        folium.GeoJson(
            prepared,
            name="Zonas marinas",
            style_function=lambda _: {
                "fillColor": "#70C5E8",
                "fillOpacity": 0.5,
                "color": "#174A5B",
                "weight": 0,
            },
        ).add_to(map_object)

        positive_values = [
            feature.get("properties", {}).get("faltas_registradas", 0)
            for feature in prepared.get("features", [])
            if feature.get("properties", {}).get(
                "faltas_registradas",
                0,
            ) > 0
        ]

        if positive_values:
            minimum = min(positive_values)
            maximum = max(positive_values)
            if minimum == maximum:
                minimum = 0

            color_scale = linear.YlOrRd_09.scale(minimum, maximum)
            color_scale.caption = "Faltas registradas"

            def fault_style(feature: dict) -> dict:
                value = feature.get("properties", {}).get(
                    "faltas_registradas",
                    0,
                )
                return {
                    "fillColor": (
                        color_scale(value) if value > 0 else "#DCE5E8"
                    ),
                    "fillOpacity": 0.80 if value > 0 else 0.20,
                    "color": "#174A5B",
                    "weight": 0.5,
                }

            folium.GeoJson(
                prepared,
                name="Faltas por polígono",
                style_function=fault_style,
                highlight_function=lambda _: {
                    "color": "#FF8C00",
                    "weight": 2,
                    "fillOpacity": 0.1,
                },
            ).add_to(map_object)
            color_scale.add_to(map_object)

        # Capa independiente para mostrar información al pasar el cursor.
        folium.GeoJson(
            prepared,
            name="Información de las zonas",
            style_function=lambda _: {
                "fillOpacity": 0,
                "color": "#174A5B",
                "weight": 0,
            },
            highlight_function=lambda _: {
                "fillOpacity": 0.0,
                "color": "#FF8C00",
                "weight": 0,
            },
            tooltip=folium.GeoJsonTooltip(
                fields=[
                    "poligono",
                    "subpoligono",
                    "clave_subzona",
                    "subzona",
                    "faltas_registradas",
                ],
                aliases=[
                    "Polígono principal:",
                    "Subpolígono:",
                    "Clave:",
                    "Subzona:",
                    "Faltas registradas:",
                ],
                localize=True,
                sticky=True,
            ),
        ).add_to(map_object)

        # No ejecutar _fit_geojson_bounds aqui. Cuando el mapa se crea dentro
        # de una pestana oculta, Leaflet calcula un zoom mundial. La vista fija
        # definida arriba mantiene visibles los tres poligonos desde el inicio.

        # Personaliza el cuadro que aparece al pasar el cursor sobre un
        # polígono. El uso de rgba permite mostrar el mapa detrás del texto.
        map_object.get_root().html.add_child(
            folium.Element(
                f"""
                <style>
                .foliumtooltip {{
                    background-color: rgba(
                        255, 255, 255, {TOOLTIP_OPACITY}
                    ) !important;
                    border: 1px solid rgba(
                        23, 74, 91, 0.35
                    ) !important;
                    box-shadow: 0 2px 8px rgba(
                        0, 0, 0, 0.20
                    ) !important;
                }}

                .foliumtooltip table,
                .foliumtooltip tbody,
                .foliumtooltip tr,
                .foliumtooltip th,
                .foliumtooltip td {{
                    background-color: transparent !important;
                }}
                </style>
                """
            )
        )
    else:
        st.warning(
            "No se encontró el archivo GeoJSON de los polígonos marinos."
        )

    folium.LayerControl(collapsed=False).add_to(map_object)
    st_folium(
        map_object,
        use_container_width=True,
        height=560,
        returned_objects=[],
    )

    if data.empty or "poligono" not in data.columns:
        st.caption("No hay supervisiones para los filtros seleccionados.")
        return

    required_table_columns = {"poligono", "num_faltas"}
    if not required_table_columns.issubset(data.columns):
        st.caption("No existen columnas suficientes para generar el resumen.")
        return

    # La tabla utiliza la misma clasificación aplicada al mapa.
    table_data = data.copy()
    table_data["poligono_clave"] = (
        table_data["poligono"]
        .fillna("")
        .map(_polygon_key)
    )
    table_data = table_data[
        table_data["poligono_clave"].isin(POLYGON_NAMES)
    ].copy()
    table_data["num_faltas"] = pd.to_numeric(
        table_data["num_faltas"],
        errors="coerce",
    ).fillna(0)

    if table_data.empty:
        st.caption("No hay datos asociados a los tres polígonos.")
        return

    if "supervision_id" in table_data.columns:
        counts = (
            table_data.groupby("poligono_clave", as_index=False)
            .agg(
                supervisiones=("supervision_id", "nunique"),
                faltas=("num_faltas", "sum"),
            )
        )
    else:
        counts = (
            table_data.groupby("poligono_clave", as_index=False)
            .agg(
                supervisiones=("poligono", "size"),
                faltas=("num_faltas", "sum"),
            )
        )

    counts["poligono"] = counts["poligono_clave"].map(POLYGON_NAMES)
    counts["faltas"] = counts["faltas"].round().astype(int)
    # Conservar poligono_clave porque las gráficas de indicadores la usan
    # para relacionar las supervisiones con los recorridos.
    counts = counts[
        [
            "poligono_clave",
            "poligono",
            "supervisiones",
            "faltas",
        ]
    ]

    counts = counts.sort_values(
        "faltas",
        ascending=True,
    )

    # Gráfica comparativa que sustituye a la tabla inferior del mapa.
    comparison_figure = go.Figure()
    comparison_figure.add_trace(
        go.Bar(
            name="Supervisiones",
            y=counts["poligono"],
            x=counts["supervisiones"],
            orientation="h",
            marker_color="#00866A",
            text=counts["supervisiones"],
            textposition="outside",
            cliponaxis=False,
            hovertemplate=(
                "<b>%{y}</b><br>"
                "Supervisiones: %{x:,}"
                "<extra></extra>"
            ),
        )
    )
    comparison_figure.add_trace(
        go.Bar(
            name="Faltas",
            y=counts["poligono"],
            x=counts["faltas"],
            orientation="h",
            marker_color="#D9A321",
            text=counts["faltas"],
            textposition="outside",
            cliponaxis=False,
            hovertemplate=(
                "<b>%{y}</b><br>"
                "Faltas registradas: %{x:,}"
                "<extra></extra>"
            ),
        )
    )
    comparison_figure.update_layout(
        title={
            "text": "Supervisiones y faltas por polígono",
            "x": 0,
            "xanchor": "left",
        },
        barmode="group",
        height=390,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={
            "family": "Inter, system-ui, sans-serif",
            "color": "#16352D",
        },
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "xanchor": "right",
            "x": 1,
        },
        margin={
            "l": 20,
            "r": 55,
            "t": 75,
            "b": 45,
        },
        xaxis={
            "title": "Registros",
            "rangemode": "tozero",
            "tickformat": ",d",
            "gridcolor": "#E1EBE7",
            "zeroline": False,
        },
        yaxis={
            "title": "",
            "automargin": True,
        },
    )

    st.plotly_chart(
        comparison_figure,
        use_container_width=True,
    )

    # ==============================================================
    # INDICADORES RELATIVOS
    # Se colocan debajo del resumen existente sin modificar el mapa.
    # ==============================================================
    indicator_data = counts.copy()
    indicator_data["incidencias_por_supervision"] = (
        indicator_data["faltas"]
        .div(indicator_data["supervisiones"].replace(0, pd.NA))
        .fillna(0)
    )

    # Preparar el número de recorridos únicos de cada polígono. Se utiliza
    # el tercer argumento cuando app.py lo proporciona. Como respaldo, se
    # admite recorrido_id dentro del DataFrame de supervisiones.
    route_source: pd.DataFrame | None = recorridos
    if route_source is None and "recorrido_id" in table_data.columns:
        route_source = table_data

    if (
        route_source is not None
        and not route_source.empty
        and "poligono" in route_source.columns
    ):
        route_data = route_source.copy()
        route_data["poligono_clave"] = (
            route_data["poligono"]
            .fillna("")
            .map(_polygon_key)
        )
        route_data = route_data[
            route_data["poligono_clave"].isin(POLYGON_NAMES)
        ].copy()

        if "recorrido_id" in route_data.columns:
            route_counts = (
                route_data.groupby("poligono_clave", as_index=False)
                .agg(recorridos=("recorrido_id", "nunique"))
            )
        else:
            route_counts = (
                route_data.groupby("poligono_clave", as_index=False)
                .size()
                .rename(columns={"size": "recorridos"})
            )

        indicator_data = indicator_data.merge(
            route_counts,
            on="poligono_clave",
            how="left",
        )
        indicator_data["recorridos"] = (
            indicator_data["recorridos"].fillna(0).astype(int)
        )
        indicator_data["supervisiones_por_recorrido"] = (
            indicator_data["supervisiones"]
            .div(indicator_data["recorridos"].replace(0, pd.NA))
            .fillna(0)
        )
    else:
        indicator_data["recorridos"] = 0
        indicator_data["supervisiones_por_recorrido"] = 0.0

    # Mantener el mismo orden visual en las dos gráficas.
    indicator_data = indicator_data.sort_values(
        "incidencias_por_supervision",
        ascending=True,
    )

    incidence_figure = go.Figure(
        go.Bar(
            y=indicator_data["poligono"],
            x=indicator_data["incidencias_por_supervision"],
            orientation="h",
            marker_color="#D9A321",
            text=indicator_data["incidencias_por_supervision"].map(
                lambda value: f"{value:.2f}"
            ),
            textposition="outside",
            cliponaxis=False,
            customdata=indicator_data[["faltas", "supervisiones"]],
            hovertemplate=(
                "<b>%{y}</b><br>"
                "Incidencias por supervisión: %{x:.2f}<br>"
                "Faltas: %{customdata[0]:,}<br>"
                "Supervisiones: %{customdata[1]:,}"
                "<extra></extra>"
            ),
        )
    )
    incidence_figure.update_layout(
        title={
            "text": "Promedio de incidencias por supervisión",
            "x": 0,
            "xanchor": "left",
        },
        height=390,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={
            "family": "Inter, system-ui, sans-serif",
            "color": "#16352D",
        },
        showlegend=False,
        margin={"l": 20, "r": 55, "t": 75, "b": 45},
        xaxis={
            "title": "Incidencias por supervisión",
            "rangemode": "tozero",
            "tickformat": ".2f",
            "gridcolor": "#E1EBE7",
            "zeroline": False,
        },
        yaxis={"title": "", "automargin": True},
    )

    route_figure = go.Figure(
        go.Bar(
            y=indicator_data["poligono"],
            x=indicator_data["supervisiones_por_recorrido"],
            orientation="h",
            marker_color="#00866A",
            text=indicator_data["supervisiones_por_recorrido"].map(
                lambda value: f"{value:.2f}"
            ),
            textposition="outside",
            cliponaxis=False,
            customdata=indicator_data[["supervisiones", "recorridos"]],
            hovertemplate=(
                "<b>%{y}</b><br>"
                "Supervisiones por recorrido: %{x:.2f}<br>"
                "Supervisiones: %{customdata[0]:,}<br>"
                "Recorridos: %{customdata[1]:,}"
                "<extra></extra>"
            ),
        )
    )
    route_figure.update_layout(
        title={
            "text": "Promedio de supervisiones por recorrido",
            "x": 0,
            "xanchor": "left",
        },
        height=390,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={
            "family": "Inter, system-ui, sans-serif",
            "color": "#16352D",
        },
        showlegend=False,
        margin={"l": 20, "r": 55, "t": 75, "b": 45},
        xaxis={
            "title": "Supervisiones por recorrido",
            "rangemode": "tozero",
            "tickformat": ".2f",
            "gridcolor": "#E1EBE7",
            "zeroline": False,
        },
        yaxis={"title": "", "automargin": True},
    )

    incidence_column, route_column = st.columns(
        [1, 1],
        gap="large",
    )
    with incidence_column:
        st.plotly_chart(
            incidence_figure,
            use_container_width=True,
        )

    with route_column:
        if route_source is None:
            st.info(
                "Proporciona filtered_recorridos para calcular "
                "las supervisiones por recorrido."
            )
        else:
            st.plotly_chart(
                route_figure,
                use_container_width=True,
            )

    # ==============================================================
    # INCIDENCIAS POR CONDICIÓN DE AUTORIZACIÓN DE LA EMBARCACIÓN
    # Estas gráficas se agregan al final sin modificar las anteriores.
    # ==============================================================
    vessel_columns = [
        column
        for column in (
            "embarcacion_autorizada",
            "embarcacion_no_autorizada",
            "embarcacion",
            "nombre_embarcacion",
            "embarcacion_nombre",
            "embarcacion_anonima",
        )
        if column in data.columns
    ]

    if vessel_columns:
        vessel_data = data.copy()
        vessel_data["num_faltas"] = pd.to_numeric(
            vessel_data["num_faltas"],
            errors="coerce",
        ).fillna(0)

        # Conservar únicamente registros que realmente tengan incidencias.
        if "es_falta" in vessel_data.columns:
            fault_mask = (
                vessel_data["es_falta"]
                .fillna(False)
                .astype(bool)
            )
        else:
            fault_mask = vessel_data["num_faltas"] > 0

        vessel_data = vessel_data.loc[fault_mask].copy()

        invalid_names = {
            "",
            "NAN",
            "NONE",
            "N/A",
            "NO APLICA",
            "NO APLICA.",
            "NO",
            "NO.",
            "SIN DATO",
            "SIN DATOS",
            "SIN REGISTRO",
            "SIN EMBARCACION",
            "SIN EMBARCACIÓN",
            "NO IDENTIFICADA",
            "NO IDENTIFICADO",
            "DESCONOCIDA",
            "DESCONOCIDO",
        }

        def clean_vessel_names(column: str) -> pd.Series:
            """Limpia una columna sin anonimizar los nombres."""

            if column not in vessel_data.columns:
                return pd.Series(
                    "",
                    index=vessel_data.index,
                    dtype="object",
                )

            return (
                vessel_data[column]
                .fillna("")
                .astype(str)
                .str.replace("\u00a0", " ", regex=False)
                .str.replace(r"\s+", " ", regex=True)
                .str.strip()
                .str.upper()
            )

        # Usar directamente las dos columnas que el ETL obtiene del Excel.
        authorized_names = clean_vessel_names(
            "embarcacion_autorizada"
        )
        unauthorized_names = clean_vessel_names(
            "embarcacion_no_autorizada"
        )

        # Respaldo para datos antiguos que solo contienen `embarcacion`.
        if "embarcacion_autorizada" not in vessel_data.columns:
            authorized_names = clean_vessel_names("embarcacion")

        vessel_data["embarcacion_autorizada_nombre"] = authorized_names
        vessel_data["embarcacion_no_autorizada_nombre"] = (
            unauthorized_names
        )

        # Embarcaciones autorizadas: se muestran individualmente por nombre.
        authorized_data = vessel_data.loc[
            ~authorized_names.isin(invalid_names)
        ].copy()

        if authorized_data.empty:
            authorized_summary = pd.DataFrame(
                columns=[
                    "embarcacion_autorizada_nombre",
                    "incidencias",
                ]
            )
        else:
            authorized_summary = (
                authorized_data.groupby(
                    "embarcacion_autorizada_nombre",
                    as_index=False,
                )
                .agg(incidencias=("num_faltas", "sum"))
                .sort_values("incidencias", ascending=False)
                .head(10)
                .sort_values("incidencias", ascending=True)
            )

        authorized_figure = go.Figure()
        if authorized_summary.empty:
            authorized_figure.add_annotation(
                text=(
                    "No hay embarcaciones autorizadas con incidencias "
                    "en el periodo seleccionado"
                ),
                x=0.5,
                y=0.5,
                xref="paper",
                yref="paper",
                showarrow=False,
            )
            authorized_figure.update_xaxes(visible=False)
            authorized_figure.update_yaxes(visible=False)
        else:
            authorized_figure.add_trace(
                go.Bar(
                    y=authorized_summary[
                        "embarcacion_autorizada_nombre"
                    ],
                    x=authorized_summary["incidencias"],
                    orientation="h",
                    marker_color="#00866A",
                    text=authorized_summary["incidencias"].map(
                        lambda value: f"{value:,.0f}"
                    ),
                    textposition="outside",
                    cliponaxis=False,
                    hovertemplate=(
                        "<b>%{y}</b><br>"
                        "Incidencias: %{x:,.0f}"
                        "<extra></extra>"
                    ),
                )
            )

        authorized_figure.update_layout(
            title={
                "text": "Embarcaciones autorizadas con incidencias",
                "x": 0,
                "xanchor": "left",
            },
            height=480,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font={
                "family": "Inter, system-ui, sans-serif",
                "color": "#16352D",
            },
            showlegend=False,
            margin={"l": 20, "r": 55, "t": 75, "b": 45},
            xaxis={
                "title": "Incidencias registradas",
                "rangemode": "tozero",
                "tickformat": ",d",
                "gridcolor": "#E1EBE7",
                "zeroline": False,
            },
            yaxis={"title": "", "automargin": True},
        )

        # Las embarcaciones no autorizadas se obtienen directamente de la
        # columna NOMBRE DE LA EMBARCACIÓN INFRACTOR (NO AUTORIZADA).
        unauthorized_data = vessel_data.loc[
            ~unauthorized_names.isin(invalid_names)
        ].copy()

        if unauthorized_data.empty:
            unauthorized_summary = pd.DataFrame(
                columns=[
                    "embarcacion_no_autorizada_nombre",
                    "incidencias",
                ]
            )
        else:
            unauthorized_summary = (
                unauthorized_data.groupby(
                    "embarcacion_no_autorizada_nombre",
                    as_index=False,
                )
                .agg(incidencias=("num_faltas", "sum"))
                .sort_values("incidencias", ascending=False)
                .head(10)
                .sort_values("incidencias", ascending=True)
            )

        unauthorized_figure = go.Figure()
        if unauthorized_summary.empty:
            unauthorized_figure.add_annotation(
                text=(
                    "No hay embarcaciones sin autorización con incidencias "
                    "en el periodo seleccionado"
                ),
                x=0.5,
                y=0.5,
                xref="paper",
                yref="paper",
                showarrow=False,
            )
            unauthorized_figure.update_xaxes(visible=False)
            unauthorized_figure.update_yaxes(visible=False)
        else:
            unauthorized_figure.add_trace(
                go.Bar(
                    y=unauthorized_summary[
                        "embarcacion_no_autorizada_nombre"
                    ],
                    x=unauthorized_summary["incidencias"],
                    orientation="h",
                    marker_color="#C44E52",
                    text=unauthorized_summary["incidencias"].map(
                        lambda value: f"{value:,.0f}"
                    ),
                    textposition="outside",
                    cliponaxis=False,
                    hovertemplate=(
                        "<b>%{y}</b><br>"
                        "Incidencias de embarcaciones sin autorización: "
                        "%{x:,.0f}"
                        "<extra></extra>"
                    ),
                )
            )

        unauthorized_figure.update_layout(
            title={
                "text": "Embarcaciones sin autorización con incidencias",
                "x": 0,
                "xanchor": "left",
            },
            height=480,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font={
                "family": "Inter, system-ui, sans-serif",
                "color": "#16352D",
            },
            showlegend=False,
            margin={"l": 20, "r": 55, "t": 75, "b": 45},
            xaxis={
                "title": "Incidencias registradas",
                "rangemode": "tozero",
                "tickformat": ",d",
                "gridcolor": "#E1EBE7",
                "zeroline": False,
            },
            yaxis={"title": "", "automargin": True},
        )

        authorized_column, unauthorized_column = st.columns(
            [1, 1],
            gap="large",
        )
        with authorized_column:
            st.plotly_chart(
                authorized_figure,
                use_container_width=True,
            )

        with unauthorized_column:
            st.plotly_chart(
                unauthorized_figure,
                use_container_width=True,
            )
    else:
        st.info(
            "No existe una columna de embarcación disponible para generar "
            "las gráficas de autorización."
        )
