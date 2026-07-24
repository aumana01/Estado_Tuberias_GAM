from __future__ import annotations

from typing import Optional

import folium
import geopandas as gpd
from folium.plugins import FastMarkerCluster, HeatMap

from .utils import SEVERITY_COLORS

BASEMAPS = {
    "CartoDB Positron": "CartoDB positron",
    "OpenStreetMap": "OpenStreetMap",
    "CartoDB DarkMatter": "CartoDB dark_matter",
    "Esri Satelital": {
        "tiles": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "attr": "Esri World Imagery",
    },
    "Esri Topográfico": {
        "tiles": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
        "attr": "Esri World Topographic Map",
    },
}

# Columnas necesarias para el popup. El cálculo conserva todos los campos en memoria;
# el mapa recibe sólo esta versión liviana para acelerar el navegador.
SEGMENT_POPUP_FIELDS = [
    "segmento_id",
    "tramo_id",
    "CODSISTEMA",
    "CODMATERIA",
    "Funcion",
    "Diametro",
    "longitud_segmento_m",
    "cantidad_intervenciones",
    "indicador_100m",
    "clasificacion",
    "estado_estimado",
    "metodo_asociacion_dominante",
]

TOOLTIP_FIELDS = ["clasificacion", "estado_estimado", "cantidad_intervenciones", "indicador_100m"]


def _center_from_layers(*layers: gpd.GeoDataFrame) -> list[float]:
    for layer in layers:
        if layer is not None and not layer.empty:
            wgs = layer.to_crs("EPSG:4326") if layer.crs else layer
            centroid = wgs.geometry.unary_union.centroid
            return [centroid.y, centroid.x]
    return [9.935, -84.09]


def _style_function(feature):
    severity = feature["properties"].get("clasificacion", "Sin datos")
    return {
        "color": SEVERITY_COLORS.get(severity, "#808080"),
        "weight": feature["properties"].get("map_weight", 4),
        "opacity": feature["properties"].get("map_opacity", 0.85),
    }


def _overlay_style(_feature):
    return {
        "color": "#002B5C",
        "weight": 2,
        "opacity": 0.75,
        "fillColor": "#C9A227",
        "fillOpacity": 0.12,
    }


def _popup_fields(gdf: gpd.GeoDataFrame, max_fields: int = 6) -> list[str]:
    candidates = []
    sample = gdf.head(min(len(gdf), 250))
    for col in gdf.columns:
        if col == "geometry":
            continue
        try:
            mean_len = sample[col].astype(str).str.len().mean()
        except Exception:
            mean_len = 999
        if mean_len <= 80:
            candidates.append(col)
        if len(candidates) >= max_fields:
            break
    return candidates


def _compact_segment_layer(gdf: gpd.GeoDataFrame, line_weight: int, line_opacity: float) -> gpd.GeoDataFrame:
    if gdf is None or gdf.empty:
        return gdf
    keep = [c for c in SEGMENT_POPUP_FIELDS if c in gdf.columns]
    keep = list(dict.fromkeys(keep + ["geometry"]))
    out = gdf[keep].copy()
    out["map_weight"] = line_weight
    out["map_opacity"] = line_opacity
    for col in ["longitud_segmento_m", "indicador_100m"]:
        if col in out.columns:
            out[col] = out[col].round(2)
    if "cantidad_intervenciones" in out.columns:
        out["cantidad_intervenciones"] = out["cantidad_intervenciones"].fillna(0).astype(int)
    return out


def _add_overlay_layer(m: folium.Map, gdf: gpd.GeoDataFrame, name: str, max_features: int) -> None:
    if gdf is None or gdf.empty:
        return
    layer = gdf.to_crs("EPSG:4326") if gdf.crs else gdf.copy()
    if len(layer) > max_features:
        layer = layer.head(max_features).copy()

    popup_fields = _popup_fields(layer)
    folium.GeoJson(
        layer.to_json(drop_id=True),
        name=name,
        style_function=_overlay_style,
        tooltip=folium.features.GeoJsonTooltip(fields=popup_fields, aliases=popup_fields, localize=True) if popup_fields else None,
        popup=folium.features.GeoJsonPopup(fields=popup_fields, aliases=popup_fields, localize=True, max_width=380) if popup_fields else None,
        smooth_factor=2.0,
    ).add_to(m)


def build_map(
    results_wgs84: gpd.GeoDataFrame,
    points_wgs84: Optional[gpd.GeoDataFrame] = None,
    basemap: str = "CartoDB Positron",
    show_points: bool = False,
    show_heatmap: bool = True,
    heat_radius: int = 14,
    line_weight: int = 4,
    line_opacity: float = 0.85,
    max_features_map: int = 2500,
    overlay_layers: Optional[list[tuple[str, gpd.GeoDataFrame]]] = None,
) -> folium.Map:
    """Build a lightweight interactive map.

    The technical analysis still uses the complete GeoDataFrames. This function
    intentionally sends a compact display layer to the browser so Streamlit Cloud
    does not spend most of the time rendering thousands of geometries/markers.
    """
    layer_candidates = [results_wgs84, points_wgs84]
    if overlay_layers:
        layer_candidates.extend([gdf for _, gdf in overlay_layers])
    center = _center_from_layers(*layer_candidates)
    tile = BASEMAPS.get(basemap, "CartoDB positron")
    if isinstance(tile, dict):
        m = folium.Map(location=center, zoom_start=14, tiles=tile["tiles"], attr=tile["attr"], control_scale=True, prefer_canvas=True)
    else:
        m = folium.Map(location=center, zoom_start=14, tiles=tile, control_scale=True, prefer_canvas=True)

    # Mantener opciones de mapa base sin cargar demasiadas capas por defecto.
    folium.TileLayer("OpenStreetMap", name="OpenStreetMap", show=False).add_to(m)
    folium.TileLayer("CartoDB positron", name="CartoDB Positron", show=False).add_to(m)

    if overlay_layers:
        for name, gdf in overlay_layers:
            _add_overlay_layer(m, gdf, name=name, max_features=min(max_features_map, 1500))

    res = results_wgs84.copy()
    if len(res) > max_features_map:
        sort_cols = [c for c in ["orden_gravedad", "indicador_100m", "cantidad_intervenciones"] if c in res.columns]
        res = res.sort_values(sort_cols, ascending=False).head(max_features_map) if sort_cols else res.head(max_features_map)

    res = _compact_segment_layer(res, line_weight=line_weight, line_opacity=line_opacity)
    popup_fields = [c for c in SEGMENT_POPUP_FIELDS if c in res.columns]
    tooltip_fields = [c for c in TOOLTIP_FIELDS if c in res.columns]

    folium.GeoJson(
        res.to_json(drop_id=True),
        name="Segmentos clasificados visibles",
        style_function=_style_function,
        tooltip=folium.features.GeoJsonTooltip(fields=tooltip_fields, aliases=tooltip_fields, localize=True) if tooltip_fields else None,
        popup=folium.features.GeoJsonPopup(fields=popup_fields, aliases=popup_fields, localize=True, max_width=420) if popup_fields else None,
        smooth_factor=2.0,
    ).add_to(m)

    if points_wgs84 is not None and not points_wgs84.empty:
        pts = points_wgs84.copy()
        if show_heatmap:
            heat_limit = min(len(pts), max(2500, max_features_map))
            heat_pts = pts.sample(heat_limit, random_state=42) if len(pts) > heat_limit else pts
            coords = [[geom.y, geom.x] for geom in heat_pts.geometry if geom is not None and not geom.is_empty]
            if coords:
                HeatMap(coords, radius=heat_radius, blur=max(10, heat_radius), name="Mapa de calor").add_to(m)
        if show_points:
            point_limit = min(len(pts), 1200)
            pts_vis = pts.sample(point_limit, random_state=42) if len(pts) > point_limit else pts
            coords = [[geom.y, geom.x] for geom in pts_vis.geometry if geom is not None and not geom.is_empty]
            if coords:
                FastMarkerCluster(coords, name=f"Puntos asociados visibles ({len(coords):,})".replace(",", ".")).add_to(m)

    legend_html = """
    <div style="position: fixed; bottom: 30px; left: 30px; z-index: 9999; background: white; padding: 10px; border: 1px solid #999; border-radius: 4px; font-size: 13px;">
      <b>Clasificación</b><br>
      <span style="color:#1a9641;">■</span> Verde<br>
      <span style="color:#fdae61;">■</span> Amarillo<br>
      <span style="color:#d7191c;">■</span> Rojo<br>
      <span style="color:#808080;">■</span> Sin datos<br>
      <hr style="margin:6px 0;">
      <span style="color:#002B5C;">■</span> Capas adicionales<br>
      <small>Mapa optimizado: muestra una capa visual resumida; la tabla y Excel usan el cálculo completo.</small>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))
    folium.LayerControl(collapsed=True).add_to(m)
    return m
