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


def _prepare_polygon_counts(data: pd.DataFrame) -> pd.DataFrame:
    """Agrupa y suma las faltas de todas las variantes de cada polígono."""

    required_columns = {"poligono", "num_faltas"}
    if data.empty or not required_columns.issubset(data.columns):
        return pd.DataFrame(columns=["poligono_clave", "faltas"])

    working = data.copy()
    working["num_faltas"] = pd.to_numeric(
        working["num_faltas"],
        errors="coerce",
    ).fillna(0)
    working["poligono_clave"] = (
        working["poligono"]
        .fillna("")
        .map(_polygon_key)
    )

    # Evita que nombres no reconocidos alteren la escala del mapa.
    working = working[
        working["poligono_clave"].isin(POLYGON_NAMES)
    ].copy()

    polygon_counts = (
        working.groupby("poligono_clave", as_index=False)
        .agg(faltas=("num_faltas", "sum"))
    )
    polygon_counts["faltas"] = (
        polygon_counts["faltas"]
        .round()
        .astype(int)
    )
    return polygon_counts


def _prepare_geojson(geojson: dict, polygon_counts: pd.DataFrame) -> dict:
    """Añade al GeoJSON la clave común y el total de faltas."""

    prepared = copy.deepcopy(geojson)
    count_map = dict(
        zip(
            polygon_counts.get(
                "poligono_clave",
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
            properties.get("poligono")
            or properties.get("nombre")
            or properties.get("name")
            or properties.get("sector")
            or ""
        )
        key = _polygon_key(source_name)

        properties["__poligono_clave"] = key
        properties["faltas_registradas"] = int(
            round(count_map.get(key, 0))
        )
        properties.setdefault("poligono", "SIN ESPECIFICAR")
        properties.setdefault("sector", "SIN ESPECIFICAR")
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
                    "fillOpacity": 0.55 if value > 0 else 0.20,
                    "color": "#174A5B",
                    "weight": 2.5,
                }

            folium.GeoJson(
                prepared,
                name="Faltas por polígono",
                style_function=fault_style,
                highlight_function=lambda _: {
                    "color": "#FF8C00",
                    "weight": 4,
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
                "weight": 2,
            },
            highlight_function=lambda _: {
                "fillOpacity": 0,
                "color": "#FF8C00",
                "weight": 4,
            },
            tooltip=folium.GeoJsonTooltip(
                fields=[
                    "poligono",
                    "sector",
                    "superficie_declarada_ha",
                    "faltas_registradas",
                ],
                aliases=[
                    "Polígono:",
                    "Sector:",
                    "Superficie oficial (ha):",
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
    counts = counts[["poligono", "supervisiones", "faltas"]]

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
