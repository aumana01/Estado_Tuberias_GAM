from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import streamlit as st

try:
    from streamlit_folium import st_folium
except Exception:  # pragma: no cover
    st_folium = None

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from modules.analysis import (
    DEFAULT_MATERIAL_STATE_PERCENT,
    associate_points_to_segments,
    build_condition_tables,
    calculate_results,
    summarize_assignment,
)
from modules.data_loader import (
    build_points_gdf,
    infer_crs_from_coordinates,
    read_table_file,
    read_vector_file,
    suggest_point_columns,
    validate_line_layer,
)
from modules.excel_export import build_excel_report
from modules.geoprocessing import available_filter_values, prepare_lines, to_metric, to_wgs84
from modules.mapping import BASEMAPS, build_map
from modules.segmentation import segment_lines
from modules.shp_export import build_results_shp_zip
from modules.utils import APP_VERSION, CRS_CRTM05, CRS_WGS84, format_number, pick_default

DEFAULT_PIPES = ROOT / "data" / "JSON_catastro.json"
DEFAULT_ORDERS = ROOT / "data" / "Ordenes de Servicio GAM.csv"

st.set_page_config(
    page_title="Estado estimado de tuberías",
    page_icon="🚰",
    layout="wide",
    initial_sidebar_state="expanded",
)


def local_css() -> None:
    st.markdown(
        """
        <style>
        .main .block-container {padding-top: 1.0rem;}
        .aya-title {color:#002B5C;font-weight:800;}
        .small-note {font-size:0.86rem;color:#4b5563;}
        </style>
        """,
        unsafe_allow_html=True,
    )


@st.cache_data(show_spinner=False)
def load_default_vector(path: str) -> gpd.GeoDataFrame:
    return read_vector_file(path, default_crs=CRS_WGS84)


@st.cache_data(show_spinner=False)
def load_default_table(path: str) -> pd.DataFrame:
    return read_table_file(path)


@st.cache_data(show_spinner=False)
def load_overlay_from_bytes(name: str, content: bytes, default_crs: str) -> gpd.GeoDataFrame:
    import tempfile

    suffix = Path(name).suffix.lower()
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    return read_vector_file(tmp_path, default_crs=default_crs)


def filter_dataframe(df: pd.DataFrame, column: str | None, selected: list) -> pd.Series:
    if not column or column not in df.columns or not selected:
        return pd.Series(True, index=df.index)
    return df[column].astype(str).isin([str(x) for x in selected])


def display_length_table(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        if col == "Estado":
            continue
        if col == "Porcentaje":
            out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0).map(lambda x: f"{x:.1%}")
        else:
            out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0).map(lambda x: format_number(x, 1))
    return out


def display_percent_table(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        if col == "Estado":
            continue
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0).map(lambda x: f"{x:.0%}" if x > 0 else "")
    return out


local_css()
st.markdown("<h1 class='aya-title'>Estado estimado de tuberías por órdenes de servicio</h1>", unsafe_allow_html=True)
st.caption(f"Versión {APP_VERSION} · Salidas principales: mapa de calor + tabla resumen por sistema")

with st.expander("Criterio del aplicativo", expanded=False):
    st.markdown(
        """
        El aplicativo estima el estado de la red a partir del catastro de tuberías y las órdenes/intervenciones.
        La asociación de puntos se realiza en tres etapas: primero busca tuberías del **mismo sistema y diámetro**,
        luego tuberías del **mismo diámetro** y finalmente, si no hay coincidencia, asigna la orden a la **tubería más cercana**
        dentro del radio configurado.

        Las salidas se reducen a dos elementos: un mapa interactivo con mapa de calor, puntos y segmentos asociados;
        y una tabla por sistema de abastecimiento con la longitud estimada en estado **Malo**, **Regular** y **Bueno** por material.
        Adicionalmente, se puede exportar el resultado geoespacial en **SHP ZIP** con las tuberías verdes, amarillas y rojas.
        """
    )

# ----------------------------- Sidebar -----------------------------
with st.sidebar:
    st.header("1. Datos")
    use_demo = st.toggle("Usar archivos cargados de ejemplo", value=True)

    pipe_upload = st.file_uploader(
        "Capa de tuberías: JSON, GeoJSON, SHP .zip, KML o KMZ",
        type=["json", "geojson", "zip", "shp", "kml", "kmz"],
        disabled=use_demo,
    )
    order_upload = st.file_uploader(
        "Órdenes/intervenciones: CSV o Excel",
        type=["csv", "xlsx", "xls"],
        disabled=use_demo,
    )

    st.header("2. Parámetros base")
    metric_crs_label = st.selectbox(
        "CRS métrico para análisis",
        ["CRTM05 / EPSG:5367", "Web Mercator / EPSG:3857"],
        index=0,
        help="Para Costa Rica se recomienda CRTM05 / EPSG:5367.",
    )
    metric_crs = CRS_CRTM05 if "5367" in metric_crs_label else "EPSG:3857"
    max_segment_len = st.number_input("Longitud máxima de segmento (m)", min_value=10.0, max_value=500.0, value=100.0, step=10.0)
    radius_m = st.number_input("Radio de asociación espacial (m)", min_value=0.1, max_value=100.0, value=10.0, step=1.0)

    st.header("3. Semáforo / estado")
    green_threshold = st.number_input("Bueno / verde hasta intervenciones / 100 m", min_value=0.0, max_value=1000.0, value=3.0, step=0.5)
    yellow_threshold = st.number_input("Referencia regular / amarillo", min_value=green_threshold, max_value=1000.0, value=5.0, step=0.5)
    red_threshold = st.number_input("Malo / rojo desde intervenciones / 100 m", min_value=yellow_threshold, max_value=1000.0, value=7.0, step=0.5)
    st.caption("Los valores entre amarillo y rojo se mantienen como Regular/Amarillo para evitar una cuarta categoría.")

    st.header("4. Mapa")
    basemap = st.selectbox("Mapa base", list(BASEMAPS.keys()), index=0)
    show_points = st.checkbox("Mostrar puntos asociados", value=True)
    show_heatmap = st.checkbox("Mostrar mapa de calor", value=True)
    heat_radius = st.slider("Radio mapa de calor", min_value=5, max_value=40, value=14)
    line_weight = st.slider("Grosor de segmentos", min_value=1, max_value=10, value=4)
    line_opacity = st.slider("Transparencia de segmentos", min_value=0.1, max_value=1.0, value=0.85)
    max_features_map = st.number_input("Máximo de elementos en mapa", min_value=500, max_value=50000, value=10000, step=500)

# ----------------------------- Load data -----------------------------
try:
    if use_demo:
        pipes_raw = load_default_vector(str(DEFAULT_PIPES))
        orders_df = load_default_table(str(DEFAULT_ORDERS))
    else:
        if pipe_upload is None or order_upload is None:
            st.info("Cargue la capa de tuberías y el archivo de órdenes, o active los archivos de ejemplo.")
            st.stop()
        pipes_raw = read_vector_file(pipe_upload, default_crs=CRS_WGS84)
        orders_df = read_table_file(order_upload)
    pipes_raw = validate_line_layer(pipes_raw)
except Exception as exc:
    st.error(f"No fue posible cargar los datos: {exc}")
    st.stop()

st.subheader("Configuración de campos")
col_a, col_b = st.columns(2)
with col_a:
    st.markdown("**Catastro de tuberías**")
    st.caption(f"Registros: {len(pipes_raw):,} · CRS: {pipes_raw.crs}".replace(",", "."))
    pipe_cols = list(pipes_raw.columns.drop("geometry", errors="ignore"))
    default_id = pick_default(pipe_cols, ["OBJECTID", "GlobalID_1", "id"])
    default_len = pick_default(pipe_cols, ["Shape_Length", "longitud", "length"])
    default_system = pick_default(pipe_cols, ["CODSISTEMA", "Codigo_Sis", "Sistema"])
    default_material = pick_default(pipe_cols, ["CODMATERIA", "Material", "Tipo_Tuber"])
    default_diameter = pick_default(pipe_cols, ["Diametro", "DIAMETRO"])
    default_function = pick_default(pipe_cols, ["Funcion", "Función"])

    id_col = st.selectbox("ID del tramo", [None] + pipe_cols, index=([None] + pipe_cols).index(default_id) if default_id in pipe_cols else 0)
    length_col = st.selectbox("Longitud existente, si aplica", [None] + pipe_cols, index=([None] + pipe_cols).index(default_len) if default_len in pipe_cols else 0)
    system_col = st.selectbox("Sistema de abastecimiento", [None] + pipe_cols, index=([None] + pipe_cols).index(default_system) if default_system in pipe_cols else 0)
    material_col = st.selectbox("Material de tubería", [None] + pipe_cols, index=([None] + pipe_cols).index(default_material) if default_material in pipe_cols else 0)
    diameter_col = st.selectbox("Diámetro de catastro", [None] + pipe_cols, index=([None] + pipe_cols).index(default_diameter) if default_diameter in pipe_cols else 0)
    function_col = st.selectbox("Función de tubería", [None] + pipe_cols, index=([None] + pipe_cols).index(default_function) if default_function in pipe_cols else 0)

with col_b:
    st.markdown("**Órdenes de servicio / intervenciones**")
    st.caption(f"Registros: {len(orders_df):,}".replace(",", "."))
    order_cols = list(orders_df.columns)
    suggestions = suggest_point_columns(orders_df)
    x_default = suggestions.get("x")
    y_default = suggestions.get("y")
    x_col = st.selectbox("Coordenada X / Este / Longitud", order_cols, index=order_cols.index(x_default) if x_default in order_cols else 0)
    y_col = st.selectbox("Coordenada Y / Norte / Latitud", order_cols, index=order_cols.index(y_default) if y_default in order_cols else min(1, len(order_cols) - 1))
    inferred_crs = infer_crs_from_coordinates(orders_df, x_col, y_col)
    point_crs_label = st.selectbox(
        "CRS de los puntos",
        ["CRTM05 / EPSG:5367", "WGS84 / EPSG:4326", "Inferir automáticamente"],
        index=0 if inferred_crs == CRS_CRTM05 else 1,
    )
    if "Inferir" in point_crs_label:
        point_crs = inferred_crs
    elif "4326" in point_crs_label:
        point_crs = CRS_WGS84
    else:
        point_crs = CRS_CRTM05

    date_col = st.selectbox("Fecha de intervención", [None] + order_cols, index=([None] + order_cols).index(suggestions.get("date")) if suggestions.get("date") in order_cols else 0)
    type_col = st.selectbox("Tipo/temática de intervención", [None] + order_cols, index=([None] + order_cols).index(suggestions.get("type")) if suggestions.get("type") in order_cols else 0)
    point_diameter_default = pick_default(order_cols, ["Diametro", "Diámetro", "diametro"])
    point_diameter_col = st.selectbox("Diámetro de la orden", [None] + order_cols, index=([None] + order_cols).index(point_diameter_default) if point_diameter_default in order_cols else 0)
    point_system_col = st.selectbox("Sistema en órdenes", [None] + order_cols, index=([None] + order_cols).index(suggestions.get("system")) if suggestions.get("system") in order_cols else 0)
    location_col = st.selectbox("Sector/dirección aproximada desde órdenes", [None] + order_cols, index=([None] + order_cols).index("Localizaci") if "Localizaci" in order_cols else 0)

# ----------------------------- Pre-analysis filters -----------------------------
st.subheader("Filtros previos al análisis")
filter_cols = st.columns(4)
orders_filtered = orders_df.copy()

with filter_cols[0]:
    if date_col and date_col in orders_filtered.columns:
        orders_filtered["fecha_intervencion_dt"] = pd.to_datetime(orders_filtered[date_col].replace({"<Null>": None}), errors="coerce", dayfirst=True)
        min_date = orders_filtered["fecha_intervencion_dt"].min()
        max_date = orders_filtered["fecha_intervencion_dt"].max()
        if pd.notna(min_date) and pd.notna(max_date):
            selected_dates = st.date_input("Rango de fechas", value=(min_date.date(), max_date.date()))
            if isinstance(selected_dates, tuple) and len(selected_dates) == 2:
                start, end = selected_dates
                orders_filtered = orders_filtered[
                    orders_filtered["fecha_intervencion_dt"].isna()
                    | orders_filtered["fecha_intervencion_dt"].between(pd.Timestamp(start), pd.Timestamp(end) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1))
                ]
        else:
            st.caption("La fecha seleccionada no pudo interpretarse.")
    else:
        st.caption("Sin filtro de fechas.")

with filter_cols[1]:
    if type_col and type_col in orders_filtered.columns:
        types = available_filter_values(orders_filtered, type_col)
        selected_types = st.multiselect("Tipo de intervención", options=types, default=[])
        if selected_types:
            orders_filtered = orders_filtered[filter_dataframe(orders_filtered, type_col, selected_types)]
    else:
        st.caption("Sin filtro por tipo.")

with filter_cols[2]:
    if point_system_col and point_system_col in orders_filtered.columns:
        systems_points = available_filter_values(orders_filtered, point_system_col)
        selected_systems_points = st.multiselect("Sistema en órdenes", options=systems_points, default=[])
        if selected_systems_points:
            orders_filtered = orders_filtered[filter_dataframe(orders_filtered, point_system_col, selected_systems_points)]
    else:
        st.caption("Sin filtro por sistema en órdenes.")

with filter_cols[3]:
    max_points = st.number_input(
        "Límite opcional de puntos para pruebas rápidas",
        min_value=0,
        max_value=max(0, len(orders_filtered)),
        value=0,
        step=1000,
        help="0 usa todos los puntos filtrados. Úselo sólo para pruebas de rendimiento.",
    )
    if max_points and len(orders_filtered) > max_points:
        orders_filtered = orders_filtered.head(int(max_points))

st.info(f"Órdenes/intervenciones a analizar: {len(orders_filtered):,}".replace(",", "."))

run = st.button("Ejecutar análisis", type="primary")

if run:
    try:
        progress = st.progress(0, text="Preparando catastro de tuberías...")
        pipes_metric = prepare_lines(pipes_raw, id_col=id_col, length_col=length_col, metric_crs=metric_crs)

        progress.progress(20, text="Segmentando tuberías mayores a 100 m...")
        segments = segment_lines(pipes_metric, max_segment_length_m=max_segment_len)

        progress.progress(40, text="Construyendo capa de puntos de órdenes...")
        points = build_points_gdf(orders_filtered, x_col=x_col, y_col=y_col, point_crs=point_crs, date_col=date_col)
        points_metric = to_metric(points, metric_crs)

        progress.progress(60, text="Asociando órdenes por diámetro y cercanía...")
        counts, joined = associate_points_to_segments(
            points_metric,
            segments,
            radius_m=radius_m,
            point_type_col=type_col,
            point_location_col=location_col,
            point_diameter_col=point_diameter_col,
            segment_diameter_col=diameter_col,
            point_system_col=point_system_col,
            segment_system_col=system_col,
        )

        progress.progress(85, text="Calculando estado estimado por material y órdenes...")
        results = calculate_results(
            segments,
            counts,
            green_threshold=green_threshold,
            yellow_threshold=yellow_threshold,
            red_threshold=red_threshold,
            material_col=material_col,
        )

        st.session_state["results"] = results
        st.session_state["points"] = points_metric
        st.session_state["joined"] = joined
        st.session_state["field_config"] = {
            "system_col": system_col,
            "material_col": material_col,
            "diameter_col": diameter_col,
            "function_col": function_col,
            "point_system_col": point_system_col,
        }
        st.session_state["params"] = {
            "version": APP_VERSION,
            "crs_metrico": metric_crs,
            "crs_puntos": point_crs,
            "longitud_max_segmento_m": max_segment_len,
            "radio_asociacion_m": radius_m,
            "umbral_verde_bueno": green_threshold,
            "umbral_amarillo_referencia": yellow_threshold,
            "umbral_rojo_malo_desde": red_threshold,
            "tuberias_originales": len(pipes_raw),
            "segmentos_generados": len(results),
            "puntos_analizados": len(points_metric),
            "puntos_asociados": len(joined),
        }
        progress.progress(100, text="Análisis completado.")
        st.success("Análisis completado correctamente.")
    except Exception as exc:
        st.error(f"Error durante el análisis: {exc}")
        st.stop()

if "results" not in st.session_state:
    st.warning("Ejecute el análisis para visualizar el mapa y la tabla por sistema.")
    st.stop()

results: gpd.GeoDataFrame = st.session_state["results"]
points_metric: gpd.GeoDataFrame = st.session_state["points"]
joined: gpd.GeoDataFrame = st.session_state["joined"]
field_config = st.session_state["field_config"]
params = st.session_state["params"]
system_col = field_config.get("system_col")
material_col = field_config.get("material_col")
diameter_col = field_config.get("diameter_col")
function_col = field_config.get("function_col")

st.divider()
st.subheader("Salidas principales")

if system_col and system_col in results.columns:
    system_options = ["Todos los sistemas"] + available_filter_values(results, system_col)
else:
    system_options = ["Todos los sistemas"]
selected_system = st.selectbox("Sistema de abastecimiento para visualizar", system_options, index=0)

filtered = results.copy()
if selected_system != "Todos los sistemas" and system_col and system_col in filtered.columns:
    filtered = filtered[filtered[system_col].astype(str) == str(selected_system)].copy()

# Keep associated points consistent with mapped segments.
if not joined.empty and "segmento_id" in joined.columns and "segmento_id" in filtered.columns:
    map_points_metric = joined[joined["segmento_id"].isin(filtered["segmento_id"])].copy()
    if map_points_metric.empty:
        map_points_metric = points_metric.iloc[0:0].copy()
else:
    map_points_metric = points_metric

metric_cols = st.columns(5)
metric_cols[0].metric("Segmentos", f"{len(filtered):,}".replace(",", "."))
metric_cols[1].metric("Órdenes asociadas", f"{len(map_points_metric):,}".replace(",", "."))
metric_cols[2].metric("Longitud total analizada (m)", format_number(filtered["longitud_segmento_m"].sum(), 1))
metric_cols[3].metric("Segmentos rojos", f"{int((filtered['clasificacion'] == 'Rojo').sum()):,}".replace(",", "."))
metric_cols[4].metric("Radio asociación", f"{params['radio_asociacion_m']} m")

map_tab, table_tab = st.tabs(["Mapa", "Tabla por sistema"])

with map_tab:
    st.markdown("**Mapa de calor, puntos de órdenes y segmentos asociados**")
    overlay_cols = st.columns([2, 1])
    overlay_files = overlay_cols[0].file_uploader(
        "Subir KML, KMZ, GeoJSON o Shapefile ZIP para traslape visual",
        type=["kml", "kmz", "geojson", "json", "zip", "shp"],
        accept_multiple_files=True,
        help="Estas capas son sólo de referencia visual; no alteran el cálculo.",
    )
    overlay_crs_label = overlay_cols[1].selectbox(
        "CRS si la capa no lo trae",
        ["WGS84 / EPSG:4326", "CRTM05 / EPSG:5367"],
        index=0,
        key="overlay_crs_label",
    )
    overlay_default_crs = CRS_WGS84 if "4326" in overlay_crs_label else CRS_CRTM05
    overlay_layers: list[tuple[str, gpd.GeoDataFrame]] = []
    if overlay_files:
        for upl in overlay_files:
            try:
                ov = load_overlay_from_bytes(upl.name, upl.getvalue(), overlay_default_crs)
                overlay_layers.append((upl.name, ov))
            except Exception as exc:
                st.warning(f"No fue posible leer la capa adicional {upl.name}: {exc}")
        if overlay_layers:
            st.caption(f"Capas adicionales cargadas: {len(overlay_layers)}")

    if st_folium is None:
        st.warning("streamlit-folium no está instalado. Instale requirements.txt para activar el mapa interactivo.")
    elif filtered.empty:
        st.warning("No hay segmentos para mostrar con el sistema seleccionado.")
    else:
        fmap = build_map(
            to_wgs84(filtered),
            to_wgs84(map_points_metric) if not map_points_metric.empty else None,
            basemap=basemap,
            show_points=show_points,
            show_heatmap=show_heatmap,
            heat_radius=heat_radius,
            line_weight=line_weight,
            line_opacity=line_opacity,
            max_features_map=int(max_features_map),
            overlay_layers=overlay_layers,
        )
        st_folium(fmap, width=None, height=700)
        st.caption("Los segmentos conservan popup con ID, sistema, material, diámetro, longitud, órdenes asociadas, indicador y método dominante de asociación.")

    with st.expander("Resumen del método de asociación", expanded=False):
        assign_summary = summarize_assignment(joined)
        if assign_summary.empty:
            st.caption("No hay órdenes asociadas.")
        else:
            display = assign_summary.copy()
            display["Porcentaje"] = display["Porcentaje"].map(lambda x: f"{x:.1%}")
            st.dataframe(display, use_container_width=True, hide_index=True)

with table_tab:
    st.markdown("**Longitud estimada por estado y material de tubería**")
    length_table, pct_table, long_df = build_condition_tables(
        results,
        system_col=system_col,
        material_col=material_col,
        selected_system=selected_system,
    )

    st.markdown("Tabla 1. Longitud de tubería por estado y material")
    st.dataframe(display_length_table(length_table), use_container_width=True, hide_index=True, height=220)

    st.markdown("Tabla 2. Porcentaje de estado dentro de cada material")
    st.dataframe(display_percent_table(pct_table), use_container_width=True, hide_index=True, height=220)

    st.markdown("**Exportar resultados**")
    st.caption("El Excel resume la tabla. El SHP ZIP exporta las geometrías de tuberías verdes, amarillas y rojas con los atributos calculados. Si selecciona un sistema, ambos archivos respetan esa selección.")
    export_col_excel, export_col_shp = st.columns(2)

    with export_col_excel:
        try:
            excel_bytes = build_excel_report(
                results,
                joined,
                params,
                system_col=system_col,
                material_col=material_col,
                selected_system=selected_system,
            )
            export_name = "reporte_estado_tuberias_todos_sistemas.xlsx" if selected_system == "Todos los sistemas" else f"reporte_estado_tuberias_{str(selected_system).replace(' ', '_')}.xlsx"
            st.download_button(
                "Descargar reporte Excel",
                data=excel_bytes,
                file_name=export_name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
            )
        except Exception as exc:
            st.warning(f"No fue posible generar el Excel: {exc}")

    with export_col_shp:
        try:
            shp_bytes = build_results_shp_zip(
                filtered,
                system_col=system_col,
                material_col=material_col,
                diameter_col=diameter_col,
                function_col=function_col,
            )
            shp_name = "resultados_tuberias_todos_sistemas_shp.zip" if selected_system == "Todos los sistemas" else f"resultados_tuberias_{str(selected_system).replace(' ', '_')}_shp.zip"
            st.download_button(
                "Descargar resultados SHP ZIP",
                data=shp_bytes,
                file_name=shp_name,
                mime="application/zip",
                help="Incluye un SHP combinado y capas separadas por clasificación: rojas, amarillas y verdes.",
            )
        except Exception as exc:
            st.warning(f"No fue posible generar el SHP: {exc}")

    with st.expander("Supuestos y consideraciones aplicadas", expanded=True):
        st.markdown(
            f"""
            - La tabla se calcula para: **{selected_system}**.
            - Las tuberías se segmentan con longitud máxima de **{params['longitud_max_segmento_m']} m**.
            - Las órdenes se asocian dentro de un radio de **{params['radio_asociacion_m']} m**.
            - La asociación prioriza **diámetro + sistema**, luego **diámetro**, y finalmente **cercanía**.
            - El diámetro de la orden se compara contra el diámetro del catastro. Para registros pequeños se prueban equivalencias nominales comunes en pulgadas, por ejemplo 2≈50 mm, 3≈75 mm, 4≈100 mm, 6≈150 mm, 8≈200 mm, 10≈250 mm y 12≈300 mm.
            - Los materiales **AC / asbesto-cemento**, **latón** y **otro** se consideran por defecto **100 % en estado Malo**.
            - Cuando un segmento tiene órdenes asociadas, su estado se depura por el indicador de órdenes por cada 100 m.
            - Cuando un segmento no tiene órdenes asociadas, se distribuye por los porcentajes base por material mostrados abajo.
            - Umbrales activos: **Bueno/Verde ≤ {params['umbral_verde_bueno']}**, **Regular/Amarillo desde ese valor hasta antes de {params['umbral_rojo_malo_desde']}**, **Malo/Rojo ≥ {params['umbral_rojo_malo_desde']}** órdenes / 100 m.
            - El resultado es una estimación para priorización y debe complementarse con criterio operativo, antigüedad, criticidad, inspección de campo, condición hidráulica y disponibilidad presupuestaria.
            """
        )
        assumptions = pd.DataFrame(
            [
                {
                    "Material": mat,
                    "Malo": pct.get("Malo", 0),
                    "Regular": pct.get("Regular", 0),
                    "Bueno": pct.get("Bueno", 0),
                }
                for mat, pct in DEFAULT_MATERIAL_STATE_PERCENT.items()
            ]
        )
        assumptions_display = assumptions.copy()
        for col in ["Malo", "Regular", "Bueno"]:
            assumptions_display[col] = assumptions_display[col].map(lambda x: f"{x:.0%}" if x > 0 else "")
        st.dataframe(assumptions_display, use_container_width=True, hide_index=True)
