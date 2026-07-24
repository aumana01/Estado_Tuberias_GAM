from __future__ import annotations

from typing import Optional

import folium
import geopandas as gpd
from folium.plugins import HeatMap, MarkerCluster

from .utils import SEVERITY_COLORS

BASEMAPS = {
    "OpenStreetMap": "OpenStreetMap",
    "CartoDB Positron": "CartoDB positron",
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


def _overlay_style(feature):
    return {
        "color": "#002B5C",
        "weight": 3,
        "opacity": 0.85,
        "fillColor": "#C9A227",
        "fillOpacity": 0.18,
    }


def _popup_fields(gdf: gpd.GeoDataFrame, max_fields: int = 8) -> list[str]:
    candidates = []
    for col in gdf.columns:
        if col == "geometry":
            continue
        if gdf[col].astype(str).str.len().mean() <= 120:
            candidates.append(col)
        if len(candidates) >= max_fields:
            break
    return candidates


def _add_overlay_layer(m: folium.Map, gdf: gpd.GeoDataFrame, name: str, max_features: int) -> None:
    if gdf is None or gdf.empty:
        return
    layer = gdf.to_crs("EPSG:4326") if gdf.crs else gdf.copy()
    if len(layer) > max_features:
        layer = layer.head(max_features).copy()

    geom_types = set(layer.geometry.geom_type.dropna().unique())
    popup_fields = _popup_fields(layer)

    if geom_types.issubset({"Point", "MultiPoint"}):
        cluster = MarkerCluster(name=name).add_to(m)
        for _, row in layer.iterrows():
            geom = row.geometry
            if geom is None or geom.is_empty:
                continue
            points = list(geom.geoms) if geom.geom_type == "MultiPoint" else [geom]
            popup_txt = "<br>".join([f"<b>{c}</b>: {row.get(c, '')}" for c in popup_fields]) or name
            for pt in points:
                folium.CircleMarker(
                    location=[pt.y, pt.x],
                    radius=5,
                    weight=1,
                    color="#002B5C",
                    fill=True,
                    fill_color="#C9A227",
                    fill_opacity=0.8,
                    popup=folium.Popup(popup_txt, max_width=450),
                ).add_to(cluster)
    else:
        folium.GeoJson(
            layer.to_json(),
            name=name,
            style_function=_overlay_style,
            tooltip=folium.features.GeoJsonTooltip(fields=popup_fields, aliases=popup_fields, localize=True) if popup_fields else None,
            popup=folium.features.GeoJsonPopup(fields=popup_fields, aliases=popup_fields, localize=True, max_width=450) if popup_fields else None,
        ).add_to(m)


def build_map(
    results_wgs84: gpd.GeoDataFrame,
    points_wgs84: Optional[gpd.GeoDataFrame] = None,
    basemap: str = "OpenStreetMap",
    show_points: bool = True,
    show_heatmap: bool = True,
    heat_radius: int = 14,
    line_weight: int = 4,
    line_opacity: float = 0.85,
    max_features_map: int = 5000,
    overlay_layers: Optional[list[tuple[str, gpd.GeoDataFrame]]] = None,
) -> folium.Map:
    """Build interactive map with classified pipe segments, interventions and optional overlays."""
    layer_candidates = [results_wgs84, points_wgs84]
    if overlay_layers:
        layer_candidates.extend([gdf for _, gdf in overlay_layers])
    center = _center_from_layers(*layer_candidates)
    tile = BASEMAPS.get(basemap, "OpenStreetMap")
    if isinstance(tile, dict):
        m = folium.Map(location=center, zoom_start=14, tiles=tile["tiles"], attr=tile["attr"], control_scale=True)
    else:
        m = folium.Map(location=center, zoom_start=14, tiles=tile, control_scale=True)

    folium.TileLayer("OpenStreetMap", name="OpenStreetMap", show=False).add_to(m)
    folium.TileLayer("CartoDB positron", name="CartoDB Positron", show=False).add_to(m)

    if overlay_layers:
        for name, gdf in overlay_layers:
            _add_overlay_layer(m, gdf, name=name, max_features=max_features_map)

    res = results_wgs84.copy()
    res["map_weight"] = line_weight
    res["map_opacity"] = line_opacity
    if len(res) > max_features_map:
        sort_cols = [c for c in ["orden_gravedad", "indicador_100m", "cantidad_intervenciones"] if c in res.columns]
        res = res.sort_values(sort_cols, ascending=False).head(max_features_map) if sort_cols else res.head(max_features_map)

    popup_fields = [
        c
        for c in [
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
        if c in res.columns
    ]
    tooltip_fields = [c for c in ["clasificacion", "estado_estimado", "cantidad_intervenciones", "indicador_100m"] if c in res.columns]
    folium.GeoJson(
        res.to_json(),
        name="Segmentos clasificados",
        style_function=_style_function,
        tooltip=folium.features.GeoJsonTooltip(fields=tooltip_fields, aliases=tooltip_fields, localize=True) if tooltip_fields else None,
        popup=folium.features.GeoJsonPopup(fields=popup_fields, aliases=popup_fields, localize=True, max_width=500) if popup_fields else None,
    ).add_to(m)

    if points_wgs84 is not None and not points_wgs84.empty:
        pts = points_wgs84.copy()
        if len(pts) > max_features_map:
            pts = pts.sample(max_features_map, random_state=42)
        if show_heatmap:
            coords = [[geom.y, geom.x] for geom in pts.geometry if geom is not None and not geom.is_empty]
            if coords:
                HeatMap(coords, radius=heat_radius, blur=max(10, heat_radius), name="Mapa de calor").add_to(m)
        if show_points:
            cluster = MarkerCluster(name="Puntos de intervención").add_to(m)
            for _, row in pts.iterrows():
                geom = row.geometry
                if geom is None or geom.is_empty:
                    continue
                label = str(row.get("Nombre_Ord", row.get("Tipo_Orden", "Intervención")))[:120]
                folium.CircleMarker(
                    location=[geom.y, geom.x],
                    radius=3,
                    weight=1,
                    fill=True,
                    fill_opacity=0.65,
                    popup=label,
                ).add_to(cluster)

    legend_html = """
    <div style="position: fixed; bottom: 30px; left: 30px; z-index: 9999; background: white; padding: 10px; border: 1px solid #999; border-radius: 4px; font-size: 13px;">
      <b>Clasificación</b><br>
      <span style="color:#1a9641;">■</span> Verde<br>
      <span style="color:#fdae61;">■</span> Amarillo<br>
      <span style="color:#d7191c;">■</span> Rojo<br>
      <span style="color:#808080;">■</span> Sin datos<br>
      <hr style="margin:6px 0;">
      <span style="color:#002B5C;">■</span> Capas adicionales
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))
    folium.LayerControl(collapsed=False).add_to(m)
    return m
