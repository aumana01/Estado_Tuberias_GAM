from __future__ import annotations

import io
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import folium
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point, box

from .geoprocessing import to_metric, to_wgs84
from .utils import CRS_CRTM05

SQUARE_SIZE_M = 20_000.0
HALF_SIZE_M = SQUARE_SIZE_M / 2.0


def _patch_streamlit_satellite_state() -> None:
    """Allow map-click callbacks to update satellite-square widget keys safely.

    Streamlit does not allow direct assignment to a session_state key after a
    widget with the same key has already been instantiated in the same run. The
    map click is captured after the controls are drawn, so updating sat_x/sat_y
    from that click can raise StreamlitAPIException. For these three internal
    satellite keys only, this patch writes directly to Streamlit's new-session
    state when the public setter refuses the update. It leaves all other keys
    untouched.
    """
    try:
        import streamlit as st  # noqa: F401
        from streamlit.errors import StreamlitAPIException
        from streamlit.runtime.state.session_state_proxy import SessionStateProxy, get_session_state
    except Exception:
        return

    if getattr(SessionStateProxy, "_aya_satellite_patch", False):
        return

    original_setitem = SessionStateProxy.__setitem__
    protected_keys = {"sat_x", "sat_y", "sat_enabled"}

    def patched_setitem(self, key, value):  # type: ignore[no-untyped-def]
        try:
            return original_setitem(self, key, value)
        except StreamlitAPIException:
            if key not in protected_keys:
                raise
            state = get_session_state()
            try:
                raw_state = state._state  # SafeSessionState -> SessionState
                raw_state._new_session_state[key] = value
                return None
            except Exception:
                raise

    SessionStateProxy.__setitem__ = patched_setitem
    SessionStateProxy._aya_satellite_patch = True


_patch_streamlit_satellite_state()


def wgs84_point_to_metric(lon: float, lat: float, metric_crs: str = CRS_CRTM05) -> tuple[float, float]:
    """Convert a lon/lat point to the selected metric CRS."""
    pt = gpd.GeoDataFrame([{"id": 1}], geometry=[Point(float(lon), float(lat))], crs="EPSG:4326").to_crs(metric_crs)
    geom = pt.geometry.iloc[0]
    return float(geom.x), float(geom.y)


def metric_point_to_wgs84(x: float, y: float, metric_crs: str = CRS_CRTM05) -> tuple[float, float]:
    """Convert metric center coordinates to lon/lat."""
    pt = gpd.GeoDataFrame([{"id": 1}], geometry=[Point(float(x), float(y))], crs=metric_crs).to_crs("EPSG:4326")
    geom = pt.geometry.iloc[0]
    return float(geom.x), float(geom.y)


def default_center_from_layer(gdf: gpd.GeoDataFrame, metric_crs: str = CRS_CRTM05) -> tuple[float, float]:
    """Return a sensible default center for the square from the pipe layer centroid."""
    metric = to_metric(gdf, metric_crs)
    centroid = metric.geometry.unary_union.centroid
    return float(centroid.x), float(centroid.y)


def create_square_20km(center_x: float, center_y: float, metric_crs: str = CRS_CRTM05, square_name: str = "CUADRO_20KM") -> gpd.GeoDataFrame:
    """Create a 20 km x 20 km square in a metric CRS."""
    cx = float(center_x)
    cy = float(center_y)
    geom = box(cx - HALF_SIZE_M, cy - HALF_SIZE_M, cx + HALF_SIZE_M, cy + HALF_SIZE_M)
    return gpd.GeoDataFrame(
        [
            {
                "id_cuadro": square_name,
                "ancho_km": 20.0,
                "alto_km": 20.0,
                "area_km2": 400.0,
                "centro_x": cx,
                "centro_y": cy,
                "fecha": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
        ],
        geometry=[geom],
        crs=metric_crs,
    )


def _value_series(df: gpd.GeoDataFrame, col: Optional[str], default: str) -> pd.Series:
    if col and col in df.columns:
        return df[col].fillna(default).replace({"<Null>": default, "nan": default, "None": default, "": default}).astype(str)
    return pd.Series([default] * len(df), index=df.index)


def _empty_clipped(metric_crs: str) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        columns=["tramo_id", "sistema", "material", "diametro", "funcion", "long_m", "long_km", "geometry"],
        geometry="geometry",
        crs=metric_crs,
    )


def clip_pipes_to_square(
    pipes_raw: gpd.GeoDataFrame,
    square_metric: gpd.GeoDataFrame,
    metric_crs: str = CRS_CRTM05,
    id_col: Optional[str] = None,
    system_col: Optional[str] = None,
    material_col: Optional[str] = None,
    diameter_col: Optional[str] = None,
    function_col: Optional[str] = None,
) -> tuple[gpd.GeoDataFrame, dict[str, float]]:
    """Clip the original pipe catastro to the square and calculate length inside.

    This uses only the pipe layer/catastro, not the order-service analysis. It is intended
    for satellite-leak detection planning windows.
    """
    square_geom = square_metric.geometry.iloc[0]
    pipes_metric = to_metric(pipes_raw, metric_crs).explode(index_parts=False).reset_index(drop=True)

    if pipes_metric.empty:
        empty = _empty_clipped(metric_crs)
        return empty, {"total_km": 0.0, "total_m": 0.0, "tramos": 0, "area_km2": 400.0}

    try:
        idx = list(pipes_metric.sindex.intersection(square_geom.bounds))
        candidates = pipes_metric.iloc[idx].copy() if idx else pipes_metric.iloc[0:0].copy()
    except Exception:
        candidates = pipes_metric.copy()

    if candidates.empty:
        empty = _empty_clipped(metric_crs)
        return empty, {"total_km": 0.0, "total_m": 0.0, "tramos": 0, "area_km2": 400.0}

    candidates = candidates[candidates.geometry.intersects(square_geom)].copy()
    if candidates.empty:
        empty = _empty_clipped(metric_crs)
        return empty, {"total_km": 0.0, "total_m": 0.0, "tramos": 0, "area_km2": 400.0}

    clipped_geom = candidates.geometry.intersection(square_geom)
    keep = clipped_geom.notna() & ~clipped_geom.is_empty
    candidates = candidates[keep].copy()
    clipped_geom = clipped_geom[keep]

    if candidates.empty:
        empty = _empty_clipped(metric_crs)
        return empty, {"total_km": 0.0, "total_m": 0.0, "tramos": 0, "area_km2": 400.0}

    tramo_id = _value_series(candidates, id_col, "Sin ID") if id_col else pd.Series([f"TRAMO_{i+1}" for i in range(len(candidates))], index=candidates.index)
    out = gpd.GeoDataFrame(
        {
            "tramo_id": tramo_id.astype(str).values,
            "sistema": _value_series(candidates, system_col, "Sin sistema").values,
            "material": _value_series(candidates, material_col, "Desconocido").values,
            "diametro": _value_series(candidates, diameter_col, "Sin dato").values,
            "funcion": _value_series(candidates, function_col, "Sin dato").values,
        },
        geometry=clipped_geom.values,
        crs=metric_crs,
    )
    out["long_m"] = out.geometry.length
    out = out[out["long_m"] > 0].copy()
    out["long_km"] = out["long_m"] / 1000.0

    total_m = float(out["long_m"].sum()) if not out.empty else 0.0
    summary = {
        "total_km": total_m / 1000.0,
        "total_m": total_m,
        "tramos": int(len(out)),
        "area_km2": 400.0,
    }
    return out.reset_index(drop=True), summary


def summarize_clipped_pipes(clipped: gpd.GeoDataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return system, material and system-material-diameter summary tables."""
    if clipped is None or clipped.empty:
        empty = pd.DataFrame(columns=["Sistema", "Material", "Diametro", "Longitud_km", "Tramos"])
        return empty, empty, empty

    by_system = (
        clipped.groupby("sistema", dropna=False)
        .agg(Longitud_km=("long_km", "sum"), Longitud_m=("long_m", "sum"), Tramos=("tramo_id", "count"))
        .reset_index()
        .rename(columns={"sistema": "Sistema"})
        .sort_values("Longitud_km", ascending=False)
    )
    by_material = (
        clipped.groupby("material", dropna=False)
        .agg(Longitud_km=("long_km", "sum"), Longitud_m=("long_m", "sum"), Tramos=("tramo_id", "count"))
        .reset_index()
        .rename(columns={"material": "Material"})
        .sort_values("Longitud_km", ascending=False)
    )
    detail = (
        clipped.groupby(["sistema", "material", "diametro"], dropna=False)
        .agg(Longitud_km=("long_km", "sum"), Longitud_m=("long_m", "sum"), Tramos=("tramo_id", "count"))
        .reset_index()
        .rename(columns={"sistema": "Sistema", "material": "Material", "diametro": "Diametro"})
        .sort_values("Longitud_km", ascending=False)
    )
    return by_system, by_material, detail


def square_vertices_table(square_metric: gpd.GeoDataFrame) -> pd.DataFrame:
    """Return WGS84 vertex coordinates for the square."""
    wgs = to_wgs84(square_metric)
    coords = list(wgs.geometry.iloc[0].exterior.coords)
    return pd.DataFrame(
        [
            {"Vertice": i + 1, "Longitud_WGS84": lon, "Latitud_WGS84": lat}
            for i, (lon, lat) in enumerate(coords)
        ]
    )


def build_satellite_excel(square_metric: gpd.GeoDataFrame, clipped: gpd.GeoDataFrame, summary: dict[str, float], saved_locations: Optional[list[dict]] = None) -> bytes:
    """Build an XLSX file for the contractor."""
    by_system, by_material, detail = summarize_clipped_pipes(clipped)
    lon, lat = metric_point_to_wgs84(square_metric.iloc[0]["centro_x"], square_metric.iloc[0]["centro_y"], str(square_metric.crs))
    resumen = pd.DataFrame(
        [
            {"Indicador": "Escena", "Valor": square_metric.iloc[0]["id_cuadro"]},
            {"Indicador": "Tamaño", "Valor": "20 km x 20 km"},
            {"Indicador": "Área", "Valor": "400 km²"},
            {"Indicador": "Centro X métrico", "Valor": square_metric.iloc[0]["centro_x"]},
            {"Indicador": "Centro Y métrico", "Valor": square_metric.iloc[0]["centro_y"]},
            {"Indicador": "Centro longitud WGS84", "Valor": lon},
            {"Indicador": "Centro latitud WGS84", "Valor": lat},
            {"Indicador": "Longitud total de tubería dentro del cuadro (km)", "Valor": summary.get("total_km", 0.0)},
            {"Indicador": "Cantidad de tramos/intersecciones", "Valor": summary.get("tramos", 0)},
            {"Indicador": "Fecha de exportación", "Valor": datetime.now().strftime("%Y-%m-%d %H:%M")},
        ]
    )

    pipe_table = clipped.drop(columns="geometry", errors="ignore").copy() if clipped is not None and not clipped.empty else pd.DataFrame(columns=["tramo_id", "sistema", "material", "diametro", "funcion", "long_m", "long_km"])
    saved_df = pd.DataFrame(saved_locations or [])

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        resumen.to_excel(writer, sheet_name="Resumen", index=False)
        by_system.to_excel(writer, sheet_name="Por sistema", index=False)
        by_material.to_excel(writer, sheet_name="Por material", index=False)
        detail.to_excel(writer, sheet_name="Detalle agrupado", index=False)
        pipe_table.to_excel(writer, sheet_name="Tuberias dentro", index=False)
        square_vertices_table(square_metric).to_excel(writer, sheet_name="Vertices WGS84", index=False)
        if not saved_df.empty:
            saved_df.to_excel(writer, sheet_name="Ubicaciones guardadas", index=False)
    return output.getvalue()


def build_satellite_shp_zip(square_metric: gpd.GeoDataFrame, clipped: gpd.GeoDataFrame) -> bytes:
    """Build a ZIP with shapefiles for the 20x20 km square and clipped pipes."""
    buffer = io.BytesIO()
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        square_out = square_metric.copy()
        square_out.to_file(tmp / "cuadro_20x20_crtm05.shp", driver="ESRI Shapefile", engine="pyogrio")

        if clipped is not None and not clipped.empty:
            pipe_out = clipped.copy()
            pipe_out.to_file(tmp / "tuberias_dentro_20x20_crtm05.shp", driver="ESRI Shapefile", engine="pyogrio")
        else:
            (tmp / "sin_tuberias_dentro_del_cuadro.txt").write_text("No se identificaron tuberías dentro del cuadro 20x20 km.", encoding="utf-8")

        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in tmp.iterdir():
                zf.write(file, arcname=file.name)
    return buffer.getvalue()


def add_square_to_map(m: folium.Map, square_metric: gpd.GeoDataFrame, total_km: float) -> None:
    """Add a transparent 20x20 km square and label to an existing Folium map."""
    square_wgs = to_wgs84(square_metric)
    label = f"20 km x 20 km<br><b>{total_km:,.2f} km</b> de tubería".replace(",", "X").replace(".", ",").replace("X", ".")

    folium.GeoJson(
        square_wgs.to_json(),
        name="Cuadro satelital 20x20 km",
        style_function=lambda feature: {
            "color": "#004C97",
            "weight": 3,
            "opacity": 0.95,
            "fillColor": "#00A6ED",
            "fillOpacity": 0.14,
        },
        tooltip="Cuadro satelital 20 km x 20 km",
        popup=folium.Popup(label, max_width=320),
    ).add_to(m)

    centroid = square_wgs.geometry.iloc[0].centroid
    folium.Marker(
        location=[centroid.y, centroid.x],
        icon=folium.DivIcon(
            html=f"""
            <div style="background:white;border:2px solid #004C97;border-radius:6px;padding:6px 8px;font-size:12px;min-width:150px;text-align:center;box-shadow:0 1px 4px rgba(0,0,0,.25);">
                {label}
            </div>
            """
        ),
    ).add_to(m)
