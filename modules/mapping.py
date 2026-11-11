from __future__ import annotations

from html import escape
from math import isfinite
from typing import Optional

import folium
import geopandas as gpd
import pandas as pd
from folium.plugins import FastMarkerCluster, HeatMap

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

# Solo estos atributos viajan al navegador. El GeoDataFrame analítico,
# las cifras por sistema y las exportaciones conservan todos los campos.
PIPE_MAP_FIELDS = (
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
)

# Los puntos son dibujados mediante una sola instrucción JavaScript, en
# lugar de miles de sentencias Folium (CircleMarker individual).
POINT_CALLBACK = """
function (row) {
    var marker = L.circleMarker([row[0], row[1]], {
        radius: 3, weight: 1, color: '#3388ff',
        fill: true, fillOpacity: 0.65
    });
    marker.bindPopup(row[2]);
    return marker;
}
"""


def _center_from_layers(*layers: gpd.GeoDataFrame) -> list[float]:
    """Centro a partir de límites; evita el costoso unary_union de la red."""
    for layer in layers:
        if layer is None or layer.empty:
            continue
        wgs = layer.to_crs("EPSG:4326") if layer.crs and not layer.crs.to_epsg() == 4326 else layer
        xmin, ymin, xmax, ymax = wgs.total_bounds
        if all(isfinite(float(n)) for n in (xmin, ymin, xmax, ymax)):
            return [(ymin + ymax) / 2, (xmin + xmax) / 2]
    return [9.935, -84.09]


def _style_function(feature, line_weight: int = 4, line_opacity: float = 0.85):
    severity = feature["properties"].get("clasificacion", "Sin datos")
    return {
        "color": SEVERITY_COLORS.get(severity, "#808080"),
        "weight": line_weight,
        "opacity": line_opacity,
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
    # No recorrer todas las geometrías/columnas de una capa pesada para
    # decidir qué atributos caben en el popup.
    sample = gdf.head(50)
    candidates = []
    for col in sample.columns:
        if col == sample.geometry.name:
            continue
        if sample[col].astype(str).str.len().mean() <= 120:
            candidates.append(col)
        if len(candidates) >= max_fields:
            break
    return candidates


def _select_map_segments(results: gpd.GeoDataFrame, limit: int) -> gpd.GeoDataFrame:
    """Muestra representativa sin cambiar la clasificación ni las exportaciones.

    Se reserva espacio a las clases Rojo/Amarillo/Verde cuando la vista supera
    el límite; el método anterior podía mostrar únicamente segmentos rojos.
    """
    limit = max(1, int(limit))
    if len(results) <= limit:
        return results
    if "clasificacion" not in results:
        return results.sample(n=limit, random_state=42)

    groups = {
        name: results[results["clasificacion"] == name]
        for name in ("Rojo", "Amarillo", "Verde", "Sin datos")
    }
    allocated = {}
    quotas = {"Rojo": 0.60, "Amarillo": 0.25, "Verde": 0.15, "Sin datos": 0.0}
    for name, group in groups.items():
        allocated[name] = min(len(group), int(limit * quotas[name]))
        if name != "Sin datos" and len(group) and not allocated[name] and limit >= 3:
            allocated[name] = 1

    remainder = max(0, limit - sum(allocated.values()))
    for name in ("Rojo", "Amarillo", "Verde", "Sin datos"):
        extra = min(remainder, len(groups[name]) - allocated[name])
        allocated[name] += extra
        remainder -= extra

    samples = [
        group.sample(n=allocated[name], random_state=42)
        for name, group in groups.items()
        if allocated[name]
    ]
    selected = pd.concat(samples) if samples else results.head(0)
    # Registros con clasificación atípica también son visibles si queda cupo.
    if len(selected) < limit:
        other = results.loc[~results.index.isin(selected.index)]
        if not other.empty:
            selected = pd.concat([selected, other.sample(n=min(limit - len(selected), len(other)), random_state=42)])
    return gpd.GeoDataFrame(selected, geometry=results.geometry.name, crs=results.crs)


def _add_overlay_layer(m: folium.Map, gdf: gpd.GeoDataFrame, name: str, max_features: int) -> None:
    if gdf is None or gdf.empty:
        return
    layer = gdf.to_crs("EPSG:4326") if gdf.crs else gdf.copy()
    if len(layer) > max_features:
        layer = layer.head(max_features).copy()

    popup_fields = _popup_fields(layer)
    # Evita adjuntar cientos de campos largos/fechas a cada geometría GeoJSON.
    display = layer[popup_fields + [layer.geometry.name]].copy()
    geom_types = set(display.geometry.geom_type.dropna().unique())

    if geom_types.issubset({"Point", "MultiPoint"}):
        data = []
        for geom, attrs in zip(display.geometry, display[popup_fields].itertuples(index=False, name=None)):
            if geom is None or geom.is_empty:
                continue
            label = "<br>".join(
                f"<b>{escape(str(col))}</b>: {escape(str(value))}"
                for col, value in zip(popup_fields, attrs)
            ) or escape(name)
            points = geom.geoms if geom.geom_type == "MultiPoint" else [geom]
            for pt in points:
                data.append([pt.y, pt.x, label])
        if data:
            FastMarkerCluster(data, name=name, callback=POINT_CALLBACK).add_to(m)
    else:
        folium.GeoJson(
            display.to_json(default=str, drop_id=True),
            name=name,
            style_function=_overlay_style,
            smooth_factor=1.5,
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
    """Mapa ligero: cálculo completo, GeoJSON reducido y trazado Canvas."""
    layer_candidates = [results_wgs84, points_wgs84]
    if overlay_layers:
        layer_candidates.extend([gdf for _, gdf in overlay_layers])
    center = _center_from_layers(*layer_candidates)
    tile = BASEMAPS.get(basemap, "OpenStreetMap")
    tile_params = {"tiles": tile["tiles"], "attr": tile["attr"]} if isinstance(tile, dict) else {"tiles": tile}
    m = folium.Map(location=center, zoom_start=12, control_scale=True, prefer_canvas=True, **tile_params)

    folium.TileLayer("OpenStreetMap", name="OpenStreetMap", show=False).add_to(m)
    folium.TileLayer("CartoDB positron", name="CartoDB Positron", show=False).add_to(m)

    if overlay_layers:
        for name, gdf in overlay_layers:
            _add_overlay_layer(m, gdf, name=name, max_features=max_features_map)

    res = _select_map_segments(results_wgs84, max_features_map)
    fields = [c for c in PIPE_MAP_FIELDS if c in res.columns]
    # Reducir el GeoJSON evita enviar docenas de columnas del catastro por cada
    # segmento. El contenido mostrado en los popups se mantiene.
    render = res[fields + [res.geometry.name]]
    tooltip_fields = [c for c in ("clasificacion", "estado_estimado", "cantidad_intervenciones", "indicador_100m") if c in fields]
    popup_fields = fields
    folium.GeoJson(
        render.to_json(default=str, drop_id=True),
        name="Segmentos clasificados",
        style_function=lambda feature: _style_function(feature, line_weight, line_opacity),
        smooth_factor=1.5,
        tooltip=folium.features.GeoJsonTooltip(fields=tooltip_fields, aliases=tooltip_fields, localize=True) if tooltip_fields else None,
        popup=folium.features.GeoJsonPopup(fields=popup_fields, aliases=popup_fields, localize=True, max_width=500) if popup_fields else None,
    ).add_to(m)

    if points_wgs84 is not None and not points_wgs84.empty and (show_points or show_heatmap):
        pts = points_wgs84
        if len(pts) > max_features_map:
            pts = pts.sample(max_features_map, random_state=42)
        valid = pts.geometry.notna() & ~pts.geometry.is_empty
        pts = pts.loc[valid]
        if show_heatmap:
            coords = [[geom.y, geom.x] for geom in pts.geometry]
            if coords:
                HeatMap(coords, radius=heat_radius, blur=max(10, heat_radius), name="Mapa de calor").add_to(m)
        if show_points:
            if "Nombre_Ord" in pts:
                names = pts["Nombre_Ord"].fillna("Intervención").astype(str)
            elif "Tipo_Orden" in pts:
                names = pts["Tipo_Orden"].fillna("Intervención").astype(str)
            else:
                names = pd.Series("Intervención", index=pts.index)
            data = [[geom.y, geom.x, escape(label[:120])] for geom, label in zip(pts.geometry, names)]
            if data:
                FastMarkerCluster(data, name="Puntos de intervención", callback=POINT_CALLBACK).add_to(m)

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
