from __future__ import annotations

from typing import Iterable, Optional

import geopandas as gpd
import pandas as pd

from .utils import CRS_CRTM05, pick_default


def to_metric(gdf: gpd.GeoDataFrame, metric_crs: str = CRS_CRTM05) -> gpd.GeoDataFrame:
    """Project layer to a metric CRS for distance/length calculations."""
    if gdf.crs is None:
        raise ValueError("La capa no tiene CRS definido.")
    if str(gdf.crs).upper() == metric_crs.upper():
        return gdf.copy()
    return gdf.to_crs(metric_crs)


def to_wgs84(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Project a GeoDataFrame to WGS84 for web mapping."""
    if gdf.crs is None:
        raise ValueError("La capa no tiene CRS definido.")
    return gdf.to_crs("EPSG:4326")


def prepare_lines(
    lines: gpd.GeoDataFrame,
    id_col: Optional[str] = None,
    length_col: Optional[str] = None,
    metric_crs: str = CRS_CRTM05,
) -> gpd.GeoDataFrame:
    """Prepare tubería layer: explode multilines, create ID and metric length."""
    gdf = lines.copy()
    gdf = gdf.explode(index_parts=False).reset_index(drop=True)
    if id_col and id_col in gdf.columns:
        gdf["tramo_id"] = gdf[id_col].astype(str)
    else:
        inferred = pick_default(gdf.columns, ["OBJECTID", "GlobalID_1", "id", "ID"])
        if inferred:
            gdf["tramo_id"] = gdf[inferred].astype(str)
        else:
            gdf["tramo_id"] = [f"TRAMO_{i+1:06d}" for i in range(len(gdf))]
    metric = to_metric(gdf, metric_crs)
    metric["longitud_m"] = metric.geometry.length
    if length_col and length_col in metric.columns:
        supplied = pd.to_numeric(metric[length_col], errors="coerce")
        metric["longitud_original_atributo"] = supplied
    else:
        metric["longitud_original_atributo"] = pd.NA
    metric = metric[metric["longitud_m"] > 0].reset_index(drop=True)
    if metric.empty:
        raise ValueError("No hay tramos con longitud positiva para analizar.")
    return metric


def available_filter_values(df: pd.DataFrame, column: Optional[str]) -> list:
    if not column or column not in df.columns:
        return []
    values = sorted([v for v in df[column].dropna().unique().tolist() if str(v).strip() not in {"", "<Null>"}], key=lambda x: str(x))
    return values
