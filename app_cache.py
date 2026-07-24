from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import streamlit as st

try:
    from streamlit_folium import st_folium
except Exception:
    st_folium = None

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from modules.analysis import DEFAULT_MATERIAL_STATE_PERCENT, associate_points_to_segments, build_condition_tables, calculate_results, summarize_assignment
from modules.cache_manager import build_analysis_hash, cache_info, clear_cache, load_cached_analysis, save_cached_analysis
from modules.data_loader import build_points_gdf, infer_crs_from_coordinates, read_table_file, read_vector_file, suggest_point_columns, validate_line_layer
from modules.excel_export import build_excel_report
from modules.geoprocessing import available_filter_values, prepare_lines, to_metric, to_wgs84
from modules.mapping import BASEMAPS, build_map
from modules.segmentation import segment_lines
from modules.utils import APP_VERSION, CRS_CRTM05, CRS_WGS84, format_number, pick_default

DEFAULT_PIPES = ROOT / "data" / "JSON_catastro.json"
DEFAULT_ORDERS = ROOT / "data" / "Ordenes de Servicio GAM.csv"
DEMO_FILES_AVAILABLE = DEFAULT_PIPES.exists() and DEFAULT_ORDERS.exists()

st.set_page_config(page_title="Estado estimado de tuberías", page_icon="🚰", layout="wide", initial_sidebar_state="expanded")
st.markdown("""
<style>.main .block-container{padding-top:1rem}.aya-title{color:#002B5C;font-weight:800}.small-note{font-size:.86rem;color:#4b5563}</style>
""", unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def load_default_vector(path: str) -> gpd.GeoDataFrame:
    return read_vector_file(path, default_crs=CRS_WGS84)


@st.cache_data(show_spinner=False)
def load_default_table(path: str) -> pd.DataFrame:
    return read_table_file(path)


@st.cache_data(show_spinner=False)
def load_overlay_from_bytes(name: str, content: bytes, default_crs: str) -> gpd.GeoDataFrame:
    suffix = Path(name).suffix.lower()
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        return read_vector_file(tmp.name, default_crs=default_crs)


@st.cache_data(show_spinner=False)
def prepare_map_segments(filtered_metric: gpd.GeoDataFrame, max_features_map: int, simplify_tolerance_m: float) -> gpd.GeoDataFrame:
    if filtered_metric.empty:
        return to_wgs84(filtered_metric)
    out = filtered_metric.copy()
    sort_cols = [c for c in ["orden_gravedad", "indicador_100m", "cantidad_intervenciones"] if c in out.columns]
    if len(out) > max_features_map:
        out = out.sort_values(sort_cols, ascending=False).head(max_features_map) if sort_cols else out.head(max_features_map)
    if simplify_tolerance_m > 0:
        try:
            out["geometry"] = out.geometry.simplify(float(simplify_tolerance_m), preserve_topology=True)
        except Exception:
            pass
    return to_wgs84(out)


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
        if col != "Estado":
            out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0).map(lambda x: f"{x:.0%}" if x > 0 else "")
    return out


st.markdown("<h1 class='aya-title'>Estado estimado de tuberías por órdenes de servicio</h1>", unsafe_allow_html=True)
st.caption(f"Versión {APP_VERSION} · mapa de calor + tabla por sistema · caché persistente")

with st.expander("Criterio del aplicativo", expanded=False):
    st.markdown("""
    La asociación de órdenes prioriza **diámetro + sistema**, luego **diámetro**, y finalmente **cercanía** dentro del radio configurado.
    La versión v1.3.0 guarda el análisis en `.cache_estado_tuberias/` para evitar recalcular si los datos, filtros y parámetros no cambian.
    """)

with st.sidebar:
    st.header("1. Datos")
    use_demo = st.toggle("Usar archivos cargados de ejemplo", value=DEMO_FILES_AVAILABLE, disabled=not DEMO_FILES_AVAILABLE)
    if not DEMO_FILES_AVAILABLE:
        st.caption("No se encontraron archivos locales en data/. Cargue los archivos manualmente.")
    pipe_upload = st.file_uploader("Capa de tuberías", type=["json", "geojson", "zip", "shp", "kml", "kmz"], disabled=use_demo)
    order_upload = st.file_uploader("Órdenes/intervenciones", type=["csv", "xlsx", "xls"], disabled=use_demo)

    st.header("2. Parámetros base")
    metric_crs_label = st.selectbox("CRS métrico para análisis", ["CRTM05 / EPSG:5367", "Web Mercator / EPSG:3857"], index=0)
    metric_crs = CRS_CRTM05 if "5367" in metric_crs_label else "EPSG:3857"
    max_segment_len = st.number_input("Longitud máxima de segmento (m)", 10.0, 500.0, 100.0, 10.0)
    radius_m = st.number_input("Radio de asociación espacial (m)", 0.1, 100.0, 10.0, 1.0)

    st.header("3. Semáforo / estado")
    green_threshold = st.number_input("Bueno / verde hasta intervenciones / 100 m", 0.0, 1000.0, 3.0, 0.5)
    yellow_threshold = st.number_input("Referencia regular / amarillo", green_threshold, 1000.0, 5.0, 0.5)
    red_threshold = st.number_input("Malo / rojo desde intervenciones / 100 m", yellow_threshold, 1000.0, 7.0, 0.5)

    st.header("4. Mapa")
    basemap = st.selectbox("Mapa base", list(BASEMAPS.keys()), index=0)
    show_points = st.checkbox("Mostrar puntos asociados", True)
    show_heatmap = st.checkbox("Mostrar mapa de calor", True)
    heat_radius = st.slider("Radio mapa de calor", 5, 40, 14)
    line_weight = st.slider("Grosor de segmentos", 1, 10, 4)
    line_opacity = st.slider("Transparencia de segmentos", 0.1, 1.0, 0.85)
    max_features_map = st.number_input("Máximo de elementos en mapa", 500, 50000, 10000, 500)
    map_simplify_m = st.slider("Simplificación visual del mapa (m)", 0.0, 5.0, 1.0, 0.5, help="Sólo afecta la visualización; no altera el cálculo técnico.")

    st.header("5. Rendimiento / caché")
    use_disk_cache = st.checkbox("Usar caché persistente", True, help="Guarda resultados calculados para reabrirlos sin repetir todo el análisis.")
    if st.button("Borrar caché guardada"):
        clear_cache(ROOT)
        for key in ["results", "points", "joined", "field_config", "params", "analysis_hash"]:
            st.session_state.pop(key, None)
        st.success("Caché local eliminada.")

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
    x_col = st.selectbox("Coordenada X / Este / Longitud", order_cols, index=order_cols.index(suggestions.get("x")) if suggestions.get("x") in order_cols else 0)
    y_col = st.selectbox("Coordenada Y / Norte / Latitud", order_cols, index=order_cols.index(suggestions.get("y")) if suggestions.get("y") in order_cols else min(1, len(order_cols)-1))
    inferred_crs = infer_crs_from_coordinates(orders_df, x_col, y_col)
    point_crs_label = st.selectbox("CRS de los puntos", ["CRTM05 / EPSG:5367", "WGS84 / EPSG:4326", "Inferir automáticamente"], index=0 if inferred_crs == CRS_CRTM05 else 1)
    point_crs = inferred_crs if "Inferir" in point_crs_label else (CRS_WGS84 if "4326" in point_crs_label else CRS_CRTM05)
    date_col = st.selectbox("Fecha de intervención", [None] + order_cols, index=([None] + order_cols).index(suggestions.get("date")) if suggestions.get("date") in order_cols else 0)
    type_col = st.selectbox("Tipo/temática de intervención", [None] + order_cols, index=([None] + order_cols).index(suggestions.get("type")) if suggestions.get("type") in order_cols else 0)
    point_diameter_default = pick_default(order_cols, ["Diametro", "Diámetro", "diametro"])
    point_diameter_col = st.selectbox("Diámetro de la orden", [None] + order_cols, index=([None] + order_cols).index(point_diameter_default) if point_diameter_default in order_cols else 0)
    point_system_col = st.selectbox("Sistema en órdenes", [None] + order_cols, index=([None] + order_cols).index(suggestions.get("system")) if suggestions.get("system") in order_cols else 0)
    location_col = st.selectbox("Sector/dirección aproximada desde órdenes", [None] + order_cols, index=([None] + order_cols).index("Localizaci") if "Localizaci" in order_cols else 0)

st.subheader("Filtros previos al análisis")
filter_cols = st.columns(4)
orders_filtered = orders_df.copy()
with filter_cols[0]:
    if date_col and date_col in orders_filtered.columns:
        orders_filtered["fecha_intervencion_dt"] = pd.to_datetime(orders_filtered[date_col].replace({"<Null>": None}), errors="coerce", dayfirst=True)
        min_date, max_date = orders_filtered["fecha_intervencion_dt"].min(), orders_filtered["fecha_intervencion_dt"].max()
        if pd.notna(min_date) and pd.notna(max_date):
            selected_dates = st.date_input("Rango de fechas", value=(min_date.date(), max_date.date()))
            if isinstance(selected_dates, tuple) and len(selected_dates) == 2:
                start, end = selected_dates
                orders_filtered = orders_filtered[orders_filtered["fecha_intervencion_dt"].isna() | orders_filtered["fecha_intervencion_dt"].between(pd.Timestamp(start), pd.Timestamp(end) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1))]
    else:
        st.caption("Sin filtro de fechas.")
with filter_cols[1]:
    if type_col and type_col in orders_filtered.columns:
        selected_types = st.multiselect("Tipo de intervención", options=available_filter_values(orders_filtered, type_col), default=[])
        if selected_types:
            orders_filtered = orders_filtered[filter_dataframe(orders_filtered, type_col, selected_types)]
    else:
        st.caption("Sin filtro por tipo.")
with filter_cols[2]:
    if point_system_col and point_system_col in orders_filtered.columns:
        selected_systems_points = st.multiselect("Sistema en órdenes", options=available_filter_values(orders_filtered, point_system_col), default=[])
        if selected_systems_points:
            orders_filtered = orders_filtered[filter_dataframe(orders_filtered, point_system_col, selected_systems_points)]
    else:
        st.caption("Sin filtro por sistema en órdenes.")
with filter_cols[3]:
    max_points = st.number_input("Límite opcional de puntos para pruebas rápidas", min_value=0, max_value=max(0, len(orders_filtered)), value=0, step=1000)
    if max_points and len(orders_filtered) > max_points:
        orders_filtered = orders_filtered.head(int(max_points))
st.info(f"Órdenes/intervenciones a analizar: {len(orders_filtered):,}".replace(",", "."))

cache_params = {
    "metric_crs": metric_crs, "point_crs": point_crs, "max_segment_len": float(max_segment_len), "radius_m": float(radius_m),
    "green_threshold": float(green_threshold), "yellow_threshold": float(yellow_threshold), "red_threshold": float(red_threshold),
    "id_col": id_col, "length_col": length_col, "system_col": system_col, "material_col": material_col,
    "diameter_col": diameter_col, "function_col": function_col, "x_col": x_col, "y_col": y_col, "date_col": date_col,
    "type_col": type_col, "point_diameter_col": point_diameter_col, "point_system_col": point_system_col,
    "location_col": location_col, "orders_filtered_count": int(len(orders_filtered)), "app_version": APP_VERSION,
}
pipe_hash_cols = [c for c in [id_col, length_col, system_col, material_col, diameter_col, function_col] if c]
order_hash_cols = [c for c in [x_col, y_col, date_col, type_col, point_diameter_col, point_system_col, location_col] if c]
analysis_hash = build_analysis_hash(pipes_raw, orders_filtered, pipe_columns=pipe_hash_cols, order_columns=order_hash_cols, params=cache_params)
cache_meta = cache_info(ROOT, analysis_hash) if use_disk_cache else None
if use_disk_cache and cache_meta:
    st.success(f"Existe análisis guardado para esta configuración · {cache_meta['modified_at']} · {cache_meta['size_mb']:.1f} MB")
elif use_disk_cache:
    st.caption("No existe análisis guardado para esta configuración. Al ejecutar, se guardará automáticamente.")

run_cols = st.columns(2)
run_cached = run_cols[0].button("Usar análisis guardado / ejecutar", type="primary")
run_force = run_cols[1].button("Recalcular análisis")

if run_cached or run_force:
    try:
        cached = load_cached_analysis(ROOT, analysis_hash) if (use_disk_cache and run_cached and not run_force) else None
        if cached:
            for key in ["results", "points", "joined", "field_config", "params"]:
                st.session_state[key] = cached[key]
            st.session_state["analysis_hash"] = analysis_hash
            st.success("Análisis cargado desde caché. No fue necesario recalcular.")
        else:
            progress = st.progress(0, text="Preparando catastro de tuberías...")
            pipes_metric = prepare_lines(pipes_raw, id_col=id_col, length_col=length_col, metric_crs=metric_crs)
            progress.progress(20, text="Segmentando tuberías mayores a 100 m...")
            segments = segment_lines(pipes_metric, max_segment_length_m=max_segment_len)
            progress.progress(40, text="Construyendo capa de puntos de órdenes...")
            points = build_points_gdf(orders_filtered, x_col=x_col, y_col=y_col, point_crs=point_crs, date_col=date_col)
            points_metric = to_metric(points, metric_crs)
            progress.progress(60, text="Asociando órdenes por diámetro y cercanía...")
            counts, joined = associate_points_to_segments(points_metric, segments, radius_m=radius_m, point_type_col=type_col, point_location_col=location_col, point_diameter_col=point_diameter_col, segment_diameter_col=diameter_col, point_system_col=point_system_col, segment_system_col=system_col)
            progress.progress(85, text="Calculando estado estimado por material y órdenes...")
            results = calculate_results(segments, counts, green_threshold=green_threshold, yellow_threshold=yellow_threshold, red_threshold=red_threshold, material_col=material_col)
            field_config_payload = {"system_col": system_col, "material_col": material_col, "diameter_col": diameter_col, "function_col": function_col, "point_system_col": point_system_col}
            params_payload = {"version": APP_VERSION, "crs_metrico": metric_crs, "crs_puntos": point_crs, "longitud_max_segmento_m": max_segment_len, "radio_asociacion_m": radius_m, "umbral_verde_bueno": green_threshold, "umbral_amarillo_referencia": yellow_threshold, "umbral_rojo_malo_desde": red_threshold, "tuberias_originales": len(pipes_raw), "segmentos_generados": len(results), "puntos_analizados": len(points_metric), "puntos_asociados": len(joined), "analysis_hash": analysis_hash}
            st.session_state.update({"results": results, "points": points_metric, "joined": joined, "field_config": field_config_payload, "params": params_payload, "analysis_hash": analysis_hash})
            if use_disk_cache:
                save_cached_analysis(ROOT, analysis_hash, {"results": results, "points": points_metric, "joined": joined, "field_config": field_config_payload, "params": params_payload})
                progress.progress(100, text="Análisis completado y guardado en caché.")
                st.success("Análisis completado y guardado en caché.")
            else:
                progress.progress(100, text="Análisis completado.")
                st.success("Análisis completado correctamente.")
    except Exception as exc:
        st.error(f"Error durante el análisis: {exc}")
        st.stop()

if "results" not in st.session_state:
    st.warning("Ejecute el análisis para visualizar el mapa y la tabla por sistema.")
    st.stop()
if st.session_state.get("analysis_hash") != analysis_hash:
    st.warning("Los datos, filtros o parámetros cambiaron desde el último análisis mostrado. Use el análisis guardado compatible o recalcule para actualizar el mapa y la tabla.")
    st.stop()

results: gpd.GeoDataFrame = st.session_state["results"]
points_metric: gpd.GeoDataFrame = st.session_state["points"]
joined: gpd.GeoDataFrame = st.session_state["joined"]
field_config = st.session_state["field_config"]
params = st.session_state["params"]
system_col = field_config.get("system_col")
material_col = field_config.get("material_col")

st.divider()
st.subheader("Salidas principales")
system_options = ["Todos los sistemas"] + (available_filter_values(results, system_col) if system_col and system_col in results.columns else [])
selected_system = st.selectbox("Sistema de abastecimiento para visualizar", system_options, index=0)
filtered = results.copy()
if selected_system != "Todos los sistemas" and system_col and system_col in filtered.columns:
    filtered = filtered[filtered[system_col].astype(str) == str(selected_system)].copy()
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
    overlay_files = overlay_cols[0].file_uploader("Subir KML, KMZ, GeoJSON o Shapefile ZIP para traslape visual", type=["kml", "kmz", "geojson", "json", "zip", "shp"], accept_multiple_files=True, help="Estas capas son sólo de referencia visual; no alteran el cálculo.")
    overlay_crs_label = overlay_cols[1].selectbox("CRS si la capa no lo trae", ["WGS84 / EPSG:4326", "CRTM05 / EPSG:5367"], index=0, key="overlay_crs_label")
    overlay_default_crs = CRS_WGS84 if "4326" in overlay_crs_label else CRS_CRTM05
    overlay_layers: list[tuple[str, gpd.GeoDataFrame]] = []
    if overlay_files:
        for upl in overlay_files:
            try:
                overlay_layers.append((upl.name, load_overlay_from_bytes(upl.name, upl.getvalue(), overlay_default_crs)))
            except Exception as exc:
                st.warning(f"No fue posible leer la capa adicional {upl.name}: {exc}")
    if st_folium is None:
        st.warning("streamlit-folium no está instalado. Instale requirements.txt para activar el mapa interactivo.")
    elif filtered.empty:
        st.warning("No hay segmentos para mostrar con el sistema seleccionado.")
    else:
        fmap = build_map(prepare_map_segments(filtered, int(max_features_map), float(map_simplify_m)), to_wgs84(map_points_metric) if not map_points_metric.empty else None, basemap=basemap, show_points=show_points, show_heatmap=show_heatmap, heat_radius=heat_radius, line_weight=line_weight, line_opacity=line_opacity, max_features_map=int(max_features_map), overlay_layers=overlay_layers)
        st_folium(fmap, width=None, height=700)
        st.caption("Los segmentos conservan popup con ID, sistema, material, diámetro, longitud, órdenes asociadas, indicador y método dominante de asociación.")
    with st.expander("Resumen del método de asociación", expanded=False):
        assign_summary = summarize_assignment(joined)
        if assign_summary.empty:
            st.caption("No hay órdenes asociadas.")
        else:
            d = assign_summary.copy(); d["Porcentaje"] = d["Porcentaje"].map(lambda x: f"{x:.1%}")
            st.dataframe(d, use_container_width=True, hide_index=True)

with table_tab:
    st.markdown("**Longitud estimada por estado y material de tubería**")
    length_table, pct_table, _ = build_condition_tables(results, system_col=system_col, material_col=material_col, selected_system=selected_system)
    st.markdown("Tabla 1. Longitud de tubería por estado y material")
    st.dataframe(display_length_table(length_table), use_container_width=True, hide_index=True, height=220)
    st.markdown("Tabla 2. Porcentaje de estado dentro de cada material")
    st.dataframe(display_percent_table(pct_table), use_container_width=True, hide_index=True, height=220)
    try:
        excel_bytes = build_excel_report(results, joined, params, system_col=system_col, material_col=material_col, selected_system=selected_system)
        export_name = "reporte_estado_tuberias_todos_sistemas.xlsx" if selected_system == "Todos los sistemas" else f"reporte_estado_tuberias_{str(selected_system).replace(' ', '_')}.xlsx"
        st.download_button("Descargar reporte Excel", data=excel_bytes, file_name=export_name, mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary")
    except Exception as exc:
        st.warning(f"No fue posible generar el Excel: {exc}")
    with st.expander("Supuestos y consideraciones aplicadas", expanded=True):
        st.markdown(f"""
        - La tabla se calcula para: **{selected_system}**.
        - Las tuberías se segmentan con longitud máxima de **{params['longitud_max_segmento_m']} m**.
        - Las órdenes se asocian dentro de un radio de **{params['radio_asociacion_m']} m**.
        - La asociación prioriza **diámetro + sistema**, luego **diámetro**, y finalmente **cercanía**.
        - Los materiales **AC / asbesto-cemento**, **latón** y **otro** se consideran por defecto **100 % en estado Malo**.
        - Cuando un segmento tiene órdenes asociadas, su estado se depura por el indicador de órdenes por cada 100 m.
        - Cuando un segmento no tiene órdenes asociadas, se distribuye por los porcentajes base por material mostrados abajo.
        - Umbrales activos: **Bueno/Verde ≤ {params['umbral_verde_bueno']}**, **Regular/Amarillo desde ese valor hasta antes de {params['umbral_rojo_malo_desde']}**, **Malo/Rojo ≥ {params['umbral_rojo_malo_desde']}** órdenes / 100 m.
        - El resultado es una estimación para priorización y debe complementarse con criterio operativo, antigüedad, criticidad, inspección de campo, condición hidráulica y disponibilidad presupuestaria.
        """)
        assumptions = pd.DataFrame([{"Material": mat, "Malo": pct.get("Malo", 0), "Regular": pct.get("Regular", 0), "Bueno": pct.get("Bueno", 0)} for mat, pct in DEFAULT_MATERIAL_STATE_PERCENT.items()])
        for col in ["Malo", "Regular", "Bueno"]:
            assumptions[col] = assumptions[col].map(lambda x: f"{x:.0%}" if x > 0 else "")
        st.dataframe(assumptions, use_container_width=True, hide_index=True)
