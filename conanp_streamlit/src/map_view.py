from __future__ import annotations

import copy
import re
from typing import Iterator

import folium
import pandas as pd
import streamlit as st
from branca.colormap import linear
from streamlit_folium import st_folium

from src.data import normalize_name


def _polygon_key(value: object) -> str:
    """Relaciona nombres largos y cortos mediante el número del polígono."""

    normalized = normalize_name("" if value is None else str(value))
    match = re.search(r"\bpoligono\s*0*(\d+)\b", normalized, flags=re.IGNORECASE)
    if match:
        return f"poligono_{int(match.group(1))}"
    return normalized


def _prepare_polygon_counts(data: pd.DataFrame) -> pd.DataFrame:
    """
    Prepara los totales para el mapa.

    Admite:
    1. Una tabla consolidada con las columnas: poligono y faltas.
    2. Supervisiones individuales con: poligono y num_faltas.
    """

    if data.empty or "poligono" not in data.columns:
        return pd.DataFrame(
            columns=["poligono_clave", "faltas"]
        )

    work = data.copy()

    work["poligono_clave"] = (
        work["poligono"]
        .map(_polygon_key)
    )

    # Si ya recibimos la tabla consolidada de la captura
    if "faltas" in work.columns:
        work["faltas_mapa"] = pd.to_numeric(
            work["faltas"],
            errors="coerce",
        ).fillna(0)

    # Si recibimos las supervisiones sin agrupar
    elif "num_faltas" in work.columns:
        work["faltas_mapa"] = pd.to_numeric(
            work["num_faltas"],
            errors="coerce",
        ).fillna(0)

    # Alternativa cuando solamente existe es_falta
    elif "es_falta" in work.columns:
        work["faltas_mapa"] = (
            work["es_falta"]
            .astype(str)
            .str.strip()
            .str.upper()
            .isin(["TRUE", "1", "SI", "SÍ"])
            .astype(int)
        )

    else:
        work["faltas_mapa"] = 0

    polygon_counts = (
        work[
            work["poligono_clave"].isin(
                ["poligono_1", "poligono_2", "poligono_3"]
            )
        ]
        .groupby(
            "poligono_clave",
            as_index=False,
        )["faltas_mapa"]
        .sum()
        .rename(
            columns={"faltas_mapa": "faltas"}
        )
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
            polygon_counts.get("poligono_clave", pd.Series(dtype=str)),
            polygon_counts.get("faltas", pd.Series(dtype=float)),
        )
    )

    for feature in prepared.get("features", []):
        properties = feature.setdefault("properties", {})
        key = _polygon_key(
            properties.get("poligono")
            or properties.get("nombre")
            or properties.get("name")
            or properties.get("sector")
            or ""
        )
        properties["__poligono_clave"] = key
        properties["faltas_registradas"] = int(round(count_map.get(key, 0)))
        properties.setdefault("poligono", "SIN ESPECIFICAR")
        properties.setdefault("sector", "SIN ESPECIFICAR")
        properties.setdefault("superficie_declarada_ha", 0)

    return prepared


def _iterate_coordinates(coordinates: list) -> Iterator[tuple[float, float]]:
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
    points: list[tuple[float, float]] = []
    for feature in geojson.get("features", []):
        geometry = feature.get("geometry") or {}
        points.extend(_iterate_coordinates(geometry.get("coordinates") or []))

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


def render_surveillance_map(data: pd.DataFrame, geojson: dict | None) -> None:
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
        center = [21.135, -86.76]

    map_object = folium.Map(
        location=center,
        zoom_start=10,
        tiles="CartoDB positron",
        control_scale=True,
    )

    if geojson:
        polygon_counts = _prepare_polygon_counts(data)
        prepared = _prepare_geojson(geojson, polygon_counts)

        # Capa de referencia, visible también cuando un polígono no tiene faltas.
        folium.GeoJson(
            prepared,
            name="Zonas marinas",
            style_function=lambda _: {
                "fillColor": "#70C5E8",
                "fillOpacity": 0.45,
                "color": "#174A5B",
                "weight": 2,
            },
        ).add_to(map_object)

        positive_values = [
            feature.get("properties", {}).get("faltas_registradas", 0)
            for feature in prepared.get("features", [])
            if feature.get("properties", {}).get("faltas_registradas", 0) > 0
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
                    "faltas_registradas", 0
                )
                return {
                    "fillColor": color_scale(value) if value > 0 else "#DCE5E8",
                    "fillOpacity": 0.82 if value > 0 else 0.35,
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
                    "fillOpacity": 0.9,
                },
            ).add_to(map_object)
            color_scale.add_to(map_object)

        # Capa independiente para conservar el tooltip y el control existente.
        folium.GeoJson(
            prepared,
            name="Información de las zonas",
            style_function=lambda _: {
                "fillOpacity": 0,
                "color": "#174A5B",
                "weight": 2,
            },
            highlight_function=lambda _: {
                "fillOpacity": 0.18,
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

        _fit_geojson_bounds(map_object, prepared)
    else:
        st.warning("No se encontró el archivo GeoJSON de los polígonos marinos.")

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

    aggregation = {
        "supervisiones": (
            "supervision_id",
            "nunique",
        ) if "supervision_id" in data.columns else ("poligono", "size"),
        "faltas": ("num_faltas", "sum")
        if "num_faltas" in data.columns else ("poligono", "size"),
    }
    counts = data.groupby("poligono", as_index=False).agg(**aggregation)
    counts["faltas"] = pd.to_numeric(counts["faltas"], errors="coerce").fillna(0)
    st.dataframe(
        counts.sort_values("faltas", ascending=False),
        use_container_width=True,
        hide_index=True,
    )
