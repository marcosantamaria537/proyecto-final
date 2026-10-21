from __future__ import annotations
import re
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go


GREEN = "#006B4F"
TEAL = "#0A8F7A"
GOLD = "#D7A62A"
INK = "#16352D"
GRID = "#E1EBE7"


def _style(fig: go.Figure, title: str) -> go.Figure:
    fig.update_layout(
        title={"text": title, "x": 0, "xanchor": "left", "font": {"size": 18, "color": INK}},
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font={"family": "Inter, system-ui, sans-serif", "color": INK},
        margin={"l": 12, "r": 12, "t": 55, "b": 12},
        hoverlabel={"bgcolor": "white", "font_color": INK},
    )
    fig.update_xaxes(gridcolor=GRID, zeroline=False)
    fig.update_yaxes(gridcolor=GRID, zeroline=False)
    return fig


def calculate_kpis(supervisions: pd.DataFrame, recorridos: pd.DataFrame | None = None) -> dict[str, float]:
    total_supervisions = len(supervisions)
    if "num_faltas" in supervisions:
        faults = int(supervisions["num_faltas"].sum())
    else:
        faults = int(supervisions.get("es_falta", pd.Series(dtype=bool)).sum())
    if recorridos is None:
        recorrido_count = int(supervisions.get("recorrido_id", pd.Series(dtype=object)).nunique())
        kilometres = float(supervisions.get("km_recorridos", pd.Series(dtype=float)).sum())
    else:
        recorrido_count = int(recorridos["recorrido_id"].nunique())
        kilometres = float(recorridos["km_recorridos"].sum())
    con_falta = int(supervisions.get("es_falta", pd.Series(dtype=bool)).sum())
    return {
        "faltas": faults,
        "recorridos": recorrido_count,
        "porcentaje_con_falta": (con_falta / total_supervisions * 100) if total_supervisions else 0.0,
        "kilometros": kilometres,
    }


def build_monthly_trend(
    supervisions: pd.DataFrame,
    recorridos: pd.DataFrame,
    start_date,
    end_date,
):
    """Genera una evolución diaria, mensual o anual según el periodo."""

    working = supervisions.copy()
    routes_working = recorridos.copy()

    # Convierte las fechas y excluye años generados por horas aisladas de Excel.
    working["fecha"] = pd.to_datetime(
        working["fecha"],
        errors="coerce",
    )

    current_year = pd.Timestamp.today().year
    working = working[
        working["fecha"].notna()
        & working["fecha"].dt.year.between(
            2010,
            current_year + 1,
        )
    ].copy()

    # Prepara las fechas de los recorridos con las mismas reglas aplicadas
    # a las supervisiones, evitando también fechas erróneas del año 1900.
    if "fecha" not in routes_working.columns:
        routes_working["fecha"] = pd.NaT

    routes_working["fecha"] = pd.to_datetime(
        routes_working["fecha"],
        errors="coerce",
    )
    routes_working = routes_working[
        routes_working["fecha"].notna()
        & routes_working["fecha"].dt.year.between(
            2010,
            current_year + 1,
        )
    ].copy()

    working["num_faltas"] = pd.to_numeric(
        working["num_faltas"],
        errors="coerce",
    ).fillna(0)

    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()

    # Evita que un valor inválido del selector extienda el eje hasta 1900.
    minimum_allowed = pd.Timestamp("2010-01-01")
    maximum_allowed = pd.Timestamp(f"{current_year + 1}-12-31")

    if start < minimum_allowed or start > maximum_allowed:
        start = (
            working["fecha"].min().normalize()
            if not working.empty
            else pd.Timestamp.today().normalize()
        )

    if end < minimum_allowed or end > maximum_allowed:
        end = (
            working["fecha"].max().normalize()
            if not working.empty
            else start
        )

    if end < start:
        start, end = end, start

    # Conserva únicamente los datos del periodo mostrado.
    working = working[
        working["fecha"].between(
            start,
            end + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1),
        )
    ].copy()
    routes_working = routes_working[
        routes_working["fecha"].between(
            start,
            end + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1),
        )
    ].copy()

    number_of_months = (
        (end.year - start.year) * 12
        + end.month
        - start.month
        + 1
    )

    same_month = (
        start.year == end.year
        and start.month == end.month
    )

    if same_month:
        chart_title = "Evolución diaria"
        x_axis_title = "Día"
        period_index = pd.date_range(start=start, end=end, freq="D", name="periodo")
        working["periodo"] = working["fecha"].dt.normalize()
        routes_working["periodo"] = routes_working["fecha"].dt.normalize()
        labels = period_index.strftime("%d/%m").tolist()
    elif number_of_months <= 24:
        chart_title = "Evolución mensual"
        x_axis_title = "Mes"
        period_index = pd.period_range(start=start, end=end, freq="M", name="periodo")
        working["periodo"] = working["fecha"].dt.to_period("M")
        routes_working["periodo"] = routes_working["fecha"].dt.to_period("M")
        month_names = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
        labels = [f"{month_names[period.month - 1]} {period.year}" for period in period_index]
    else:
        chart_title = "Evolución anual"
        x_axis_title = "Año"
        period_index = pd.period_range(start=start, end=end, freq="Y", name="periodo")
        working["periodo"] = working["fecha"].dt.to_period("Y")
        routes_working["periodo"] = routes_working["fecha"].dt.to_period("Y")
        labels = [str(period.year) for period in period_index]

    if working.empty:
        summary = pd.DataFrame(
            {"supervisiones": 0, "incidencias": 0},
            index=period_index,
        )
    else:
        summary = (
            working.groupby("periodo")
            .agg(
                supervisiones=("supervision_id", "nunique"),
                incidencias=("num_faltas", "sum"),
            )
            .reindex(period_index, fill_value=0)
        )

    # Cuenta cada recorrido una sola vez. Si no existe recorrido_id, cada fila
    # se considera un recorrido para mantener compatibilidad con otros Excel.
    if routes_working.empty:
        route_counts = pd.Series(0, index=period_index, name="recorridos")
    elif "recorrido_id" in routes_working.columns:
        route_counts = (
            routes_working.groupby("periodo")["recorrido_id"]
            .nunique()
            .reindex(period_index, fill_value=0)
            .rename("recorridos")
        )
    else:
        route_counts = (
            routes_working.groupby("periodo")
            .size()
            .reindex(period_index, fill_value=0)
            .rename("recorridos")
        )

    summary["recorridos"] = route_counts.astype(int)

    summary = summary.reset_index()
    summary["etiqueta"] = labels

    figure = go.Figure()
    figure.add_trace(go.Scatter(


        x=summary["etiqueta"], y=summary["recorridos"],
        mode="lines+markers", name="Recorridos",
        line={"color": "#2878B5", "width": 3}, marker={"size": 8},
        hovertemplate=f"{x_axis_title}: %{{x}}<br>Recorridos: %{{y}}<extra></extra>",
        
        
        
    ))
    figure.add_trace(go.Scatter(

        x=summary["etiqueta"], y=summary["supervisiones"],
        mode="lines+markers", name="Supervisiones",
        line={"color": "#00866A", "width": 3}, marker={"size": 8},
        hovertemplate=f"{x_axis_title}: %{{x}}<br>Supervisiones: %{{y}}<extra></extra>",

        
    ))
    figure.add_trace(go.Scatter(
        x=summary["etiqueta"], y=summary["incidencias"],
        mode="lines+markers", name="Incidencias",
        line={"color": "#D9A321", "width": 3}, marker={"size": 8},
        hovertemplate=f"{x_axis_title}: %{{x}}<br>Incidencias: %{{y}}<extra></extra>",
    ))
    figure.update_layout(
        title=chart_title,
        hovermode="x unified",
        xaxis={
            "title": x_axis_title,
            "type": "category",
            "categoryorder": "array",
            "categoryarray": labels,
            "tickangle": -45,
        },
        yaxis={"title": "Registros", "rangemode": "tozero", "tickformat": ",d"},
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "right", "x": 1},
        margin={"l": 40, "r": 20, "t": 70, "b": 70},
    )
    return figure


def build_fault_type_chart(data: pd.DataFrame) -> go.Figure:
    if data.empty:
        counts = pd.DataFrame({"tipo_falta": [], "faltas": []})
    else:
        counts = data.groupby("tipo_falta", as_index=False)["cantidad"].sum().nlargest(8, "cantidad").sort_values("cantidad").rename(columns={"cantidad": "faltas"})
    fig = px.bar(counts, x="faltas", y="tipo_falta", orientation="h", color_discrete_sequence=[GREEN])
    fig.update_traces(hovertemplate="%{y}<br>%{x} faltas<extra></extra>")
    fig.update_xaxes(title="Faltas")
    fig.update_yaxes(title=None)
    return _style(fig, "Faltas por categoría")


def build_day_hour_heatmap(data: pd.DataFrame) -> go.Figure:
    order = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
    faults = data.loc[data["es_falta"]]
    matrix = faults.pivot_table(index="dia_semana", columns="hora_num", values="num_faltas", aggfunc="sum", fill_value=0)
    matrix = matrix.reindex(index=order, columns=range(24), fill_value=0)
    fig = go.Figure(go.Heatmap(
        z=matrix.values, x=[f"{hour:02d}:00" for hour in matrix.columns], y=matrix.index,
        colorscale=[[0, "#EDF5F1"], [0.55, TEAL], [1, GREEN]],
        colorbar={"title": "Faltas"}, hovertemplate="%{y}, %{x}<br>%{z} faltas<extra></extra>",
    ))
    fig.update_xaxes(title="Hora", dtick=2)
    fig.update_yaxes(title=None)
    return _style(fig, "Concentración por día y hora")


def build_measure_chart(data: pd.DataFrame) -> go.Figure:
    """Separa y contabiliza individualmente las medidas tomadas."""

    raw_measures = data.loc[
        data["es_falta"].fillna(False),
        "medida_tomada",
    ].fillna("Sin registro")

    separated_measures: list[str] = []

    for raw_value in raw_measures:
        clean_value = str(raw_value).replace("\u00a0", " ").strip()

        parts = re.split(
            r"\s*(?:,|;|\||/|\r?\n|\s+y\s+)\s*",
            clean_value,
            flags=re.IGNORECASE,
        )

        for part in parts:
            normalized_part = re.sub(
                r"\s+",
                " ",
                part,
            ).strip()

            if normalized_part:
                separated_measures.append(normalized_part)

    measure_values = pd.Series(
        separated_measures,
        dtype="object",
    )

    measure_values = measure_values.replace(
        {
            "NAN": "Sin registro",
            "NONE": "Sin registro",
            "SIN DATO": "Sin registro",
            "SIN DATOS": "Sin registro",
        }
    )

    measures = (
        measure_values
        .value_counts()
        .head(8)
        .rename_axis("medida")
        .reset_index(name="registros")
    )

    if measures.empty:
        figure = go.Figure()
        figure.add_annotation(
            text="No hay medidas registradas",
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            showarrow=False,
        )
        return _style(
            figure,
            "Medidas tomadas — versión 2",
        )

    figure = px.treemap(
        measures,
        path=["medida"],
        values="registros",
        color="registros",
        color_continuous_scale=[
            "#DCEDE7",
            TEAL,
            GREEN,
        ],
    )

    figure.update_traces(
        texttemplate=(
            "<b>%{label}</b><br>"
            "%{value} registros<br>"
            "%{percentRoot:.1%}"
        ),
        hovertemplate=(
            "<b>%{label}</b><br>"
            "Registros: %{value}<br>"
            "Porcentaje: %{percentRoot:.1%}"
            "<extra></extra>"
        ),
        marker={
            "line": {
                "color": "white",
                "width": 2,
            }
        },
    )

    figure.update_layout(
        coloraxis_showscale=False,
        margin={
            "l": 8,
            "r": 8,
            "t": 55,
            "b": 8,
        },
    )

    return _style(
        figure,
        "Medidas tomadas — versión 2",
    )

def build_recurrence_chart(data: pd.DataFrame) -> go.Figure:
    """Muestra embarcaciones con faltas en fechas distintas."""
    figure = go.Figure()
    required_columns = {"fecha", "es_falta", "supervision_id", "num_faltas"}

    # Prioriza el nombre real y conserva la columna anonimizada como respaldo.
    vessel_columns = [
        column
        for column in (
            "embarcacion",
            "nombre_embarcacion",
            "embarcacion_nombre",
            "embarcacion_anonima",
        )
        if column in data.columns
    ]

    if (
        data.empty
        or not vessel_columns
        or not required_columns.issubset(data.columns)
    ):
        figure.add_annotation(text="No hay información de reincidencia disponible", x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False)
        figure.update_layout(title="Embarcaciones con mayor reincidencia", height=520, xaxis={"visible": False}, yaxis={"visible": False})
        return figure

    faults = data[data["es_falta"].fillna(False)].copy()
    faults["fecha"] = pd.to_datetime(faults["fecha"], errors="coerce")
    faults["num_faltas"] = pd.to_numeric(faults["num_faltas"], errors="coerce").fillna(0)

    vessel_values = pd.Series("", index=faults.index, dtype="object")
    for column in vessel_columns:
        candidate = faults[column].fillna("").astype(str).str.strip()
        vessel_values = vessel_values.mask(vessel_values.eq(""), candidate)

    vessel_values = (
        vessel_values
        .str.replace("\u00a0", " ", regex=False).str.strip().str.upper()
        .str.replace(r"\s+", " ", regex=True)
    )

    # En estos registros, NO identifica embarcaciones no autorizadas.
    unauthorized_mask = vessel_values.str.fullmatch(
        r"(?:NO[.\-]?|NO AUTORIZAD[AO]S?|EMBARCACI[ÓO]N(?:ES)? NO AUTORIZAD[AO]S?)",
        case=False,
        na=False,
    )
    vessel_values.loc[unauthorized_mask] = "EMBARCACIONES NO AUTORIZADAS"
    faults["embarcacion_id"] = vessel_values

    invalid_identifiers = {"", "NAN", "NONE", "N/A", "SIN DATO", "SIN DATOS", "SIN REGISTRO", "NO IDENTIFICADA", "NO IDENTIFICADO", "DESCONOCIDA", "DESCONOCIDO"}
    faults = faults[~faults["embarcacion_id"].isin(invalid_identifiers)].dropna(subset=["fecha"])
    faults["fecha_dia"] = faults["fecha"].dt.normalize()
    recurrence = (
        faults.groupby("embarcacion_id", as_index=False)
        .agg(
            fechas_con_falta=("fecha_dia", "nunique"),
            registros_con_falta=("supervision_id", "nunique"),
            incidencias=("num_faltas", "sum"),
            ultima_fecha=("fecha", "max"),
        )
    )
    recurrence = recurrence[recurrence["fechas_con_falta"] >= 2]

    if recurrence.empty:
        figure.add_annotation(text="No se encontraron embarcaciones reincidentes en el periodo seleccionado", x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False)
        figure.update_layout(title="Embarcaciones con mayor reincidencia", height=520, xaxis={"visible": False}, yaxis={"visible": False})
        return figure

    recurrence = recurrence.sort_values(["fechas_con_falta", "incidencias"], ascending=False).head(10).sort_values("fechas_con_falta", ascending=True)
    recurrence["ultima_fecha_texto"] = recurrence["ultima_fecha"].dt.strftime("%d/%m/%Y")
    recurrence["nivel"] = recurrence["fechas_con_falta"].apply(lambda value: "Alta reincidencia" if value >= 4 else "Reincidencia")
    colors = recurrence["nivel"].map({"Reincidencia": "#D9A321", "Alta reincidencia": "#C44E52"})
    custom_data = recurrence[["registros_con_falta", "incidencias", "ultima_fecha_texto", "nivel"]].to_numpy()
    figure.add_trace(go.Bar(
        x=recurrence["fechas_con_falta"], y=recurrence["embarcacion_id"],
        orientation="h", marker_color=colors, text=recurrence["fechas_con_falta"],
        textposition="outside", customdata=custom_data,
        hovertemplate=("<b>%{y}</b><br>Fechas distintas con falta: %{x}<br>Supervisiones con falta: %{customdata[0]}<br>Incidencias registradas: %{customdata[1]}<br>Última fecha: %{customdata[2]}<br>Clasificación: %{customdata[3]}<extra></extra>"),
    ))
    figure.update_layout(
        title={"text": "Embarcaciones con mayor reincidencia", "x": 0.02, "xanchor": "left"},
        height=520,
        xaxis={"title": "Fechas distintas con falta", "rangemode": "tozero", "tickmode": "auto", "nticks": 8, "tickformat": "d", "automargin": True},
        yaxis={"title": "", "automargin": True},
        showlegend=False,
        margin={"l": 125, "r": 35, "t": 70, "b": 70},
    )
    return figure
