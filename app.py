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
from modules.satellite_grid import (
    add_square_to_map,
    build_satellite_excel,
    build_satellite_shp_zip,
    clip_pipes_to_square,
    create_square_20km,
    default_center_from_layer,
    metric_point_to_wgs84,
    summarize_clipped_pipes,
    wgs84_point_to_metric,
)
from modules.segmentation import segment_lines
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


def move_satellite_square(dx: float, dy: float) -> None:
    st.session_state["sat_x"] = float(st.session_state.get("sat_x", 0.0)) + float(dx)
    st.session_state["sat_y"] = float(st.session_state.get("sat_y", 0.0)) + float(dy)
    st.session_state["sat_enabled"] = True


def clear_satellite_square() -> None:
    st.session_state["sat_enabled"] = False


def use_last_click_as_square_center(metric_crs: str) -> None:
    click = st.session_state.get("sat_last_click")
    if click:
        x, y = wgs84_point_to_metric(click.get("lng"), click.get("lat"), metric_crs)
        st.session_state["sat_x"] = x
        st.session_state["sat_y"] = y
        st.session_state["sat_enabled"] = True


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

        Las salidas principales son el mapa interactivo con mapa de calor, puntos y segmentos asociados; y una tabla
        por sistema de abastecimiento con la longitud estimada en estado **Malo**, **Regular** y **Bueno** por material.
        La versión v1.2.2 agrega una herramienta de cuadro satelital de **20 km x 20 km** para estimar la longitud de
        tubería dentro de una escena de detección de fugas satelital.
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
            "id_col": id_col,
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
id_col = field_config.get("id_col")

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

    # ----------------------------- Satellite 20x20 km square -----------------------------
    square_metric = None
    clipped_sat = None
    sat_summary = None

    with st.expander("Herramienta de cuadro satelital 20 km x 20 km", expanded=False):
        st.markdown(
            """
            Esta herramienta utiliza **únicamente el catastro de tuberías cargado en JSON/capa de tuberías**.
            No utiliza las órdenes de servicio. El cuadro permite estimar cuántos kilómetros de tubería quedarían
            dentro de una escena de detección de fugas satelital de **20 km x 20 km**.
            """
        )

        if "sat_x" not in st.session_state or "sat_y" not in st.session_state:
            try:
                default_x, default_y = default_center_from_layer(pipes_raw, metric_crs)
            except Exception:
                default_x, default_y = 0.0, 0.0
            st.session_state["sat_x"] = default_x
            st.session_state["sat_y"] = default_y
        if "sat_enabled" not in st.session_state:
            st.session_state["sat_enabled"] = False
        if "sat_saved_locations" not in st.session_state:
            st.session_state["sat_saved_locations"] = []

        st.checkbox("Mostrar cuadro satelital 20 km x 20 km", key="sat_enabled")

        coord_cols = st.columns(3)
        with coord_cols[0]:
            st.number_input("Centro X / Este", key="sat_x", format="%.3f")
        with coord_cols[1]:
            st.number_input("Centro Y / Norte", key="sat_y", format="%.3f")
        with coord_cols[2]:
            move_step_km = st.number_input("Paso para mover (km)", min_value=0.5, max_value=20.0, value=1.0, step=0.5)

        move_step_m = float(move_step_km) * 1000.0
        move_cols = st.columns(6)
        move_cols[0].button("← Oeste", on_click=move_satellite_square, args=(-move_step_m, 0.0))
        move_cols[1].button("→ Este", on_click=move_satellite_square, args=(move_step_m, 0.0))
        move_cols[2].button("↑ Norte", on_click=move_satellite_square, args=(0.0, move_step_m))
        move_cols[3].button("↓ Sur", on_click=move_satellite_square, args=(0.0, -move_step_m))
        move_cols[4].button("Borrar cuadro", on_click=clear_satellite_square)

        if st.session_state.get("sat_enabled"):
            square_metric = create_square_20km(st.session_state["sat_x"], st.session_state["sat_y"], metric_crs=metric_crs)
            clipped_sat, sat_summary = clip_pipes_to_square(
                pipes_raw,
                square_metric,
                metric_crs=metric_crs,
                id_col=id_col,
                system_col=system_col,
                material_col=material_col,
                diameter_col=diameter_col,
                function_col=function_col,
            )

            lon_c, lat_c = metric_point_to_wgs84(st.session_state["sat_x"], st.session_state["sat_y"], metric_crs)
            sat_metrics = st.columns(4)
            sat_metrics[0].metric("Escena", "20 km x 20 km")
            sat_metrics[1].metric("Área", "400 km²")
            sat_metrics[2].metric("Km de tubería", format_number(sat_summary.get("total_km", 0.0), 2))
            sat_metrics[3].metric("Tramos/intersecciones", f"{sat_summary.get('tramos', 0):,}".replace(",", "."))
            st.caption(f"Centro WGS84 aproximado: lat {lat_c:.6f}, lon {lon_c:.6f}")

            by_system_sat, by_material_sat, detail_sat = summarize_clipped_pipes(clipped_sat)
            if not detail_sat.empty:
                st.markdown("**Resumen de tubería dentro del cuadro**")
                detail_show = detail_sat.copy()
                for col in ["Longitud_km", "Longitud_m"]:
                    if col in detail_show.columns:
                        detail_show[col] = detail_show[col].map(lambda x: format_number(x, 2))
                st.dataframe(detail_show, use_container_width=True, hide_index=True, height=220)
            else:
                st.info("No se identificaron tuberías dentro del cuadro actual.")

            save_cols = st.columns(3)
            if save_cols[0].button("Guardar ubicación definida"):
                st.session_state["sat_saved_locations"].append(
                    {
                        "id": f"CUADRO_{len(st.session_state['sat_saved_locations']) + 1:03d}",
                        "centro_x": st.session_state["sat_x"],
                        "centro_y": st.session_state["sat_y"],
                        "latitud_wgs84": lat_c,
                        "longitud_wgs84": lon_c,
                        "km_tuberia": sat_summary.get("total_km", 0.0),
                        "area_km2": 400.0,
                    }
                )
                st.success("Ubicación guardada en la sesión actual.")

            excel_sat = build_satellite_excel(square_metric, clipped_sat, sat_summary, st.session_state.get("sat_saved_locations", []))
            save_cols[1].download_button(
                "Exportar Excel contratista",
                data=excel_sat,
                file_name="cuadro_satelital_20x20_tuberias.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            try:
                shp_sat = build_satellite_shp_zip(square_metric, clipped_sat)
                save_cols[2].download_button(
                    "Exportar SHP ZIP",
                    data=shp_sat,
                    file_name="cuadro_satelital_20x20_shp.zip",
                    mime="application/zip",
                )
            except Exception as exc:
                save_cols[2].warning(f"No fue posible generar SHP: {exc}")

            if st.session_state.get("sat_saved_locations"):
                st.markdown("**Ubicaciones guardadas en la sesión**")
                st.dataframe(pd.DataFrame(st.session_state["sat_saved_locations"]), use_container_width=True, hide_index=True)
        else:
            st.caption("Active el cuadro para calcular los kilómetros de tubería dentro de la escena. También puede ubicarlo con las coordenadas o moviéndolo por pasos.")

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
        if square_metric is not None and sat_summary is not None:
            add_square_to_map(fmap, square_metric, sat_summary.get("total_km", 0.0))

        map_result = st_folium(fmap, width=None, height=700)
        if map_result and map_result.get("last_clicked"):
            st.session_state["sat_last_click"] = map_result.get("last_clicked")
        if st.session_state.get("sat_last_click"):
            click = st.session_state["sat_last_click"]
            st.caption(f"Último clic del mapa: lat {click.get('lat'):.6f}, lon {click.get('lng'):.6f}. Puede usarlo como centro del cuadro satelital.")
            st.button("Usar último clic como centro del cuadro 20x20 km", on_click=use_last_click_as_square_center, args=(metric_crs,))
        st.caption("Los segmentos conservan popup con ID, sistema, material, diámetro, longitud, órdenes asociadas, indicador y método dominante de asociación. El cuadro satelital muestra la longitud de tubería del catastro dentro de 20 km x 20 km.")

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

    st.markdown("**Exportar reporte Excel**")
    st.caption("Si se exportan varios sistemas, el archivo genera una pestaña por cada sistema de abastecimiento. El mapa interactivo se mantiene en la aplicación; no se inserta imagen en Excel para evitar capturas externas.")
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
