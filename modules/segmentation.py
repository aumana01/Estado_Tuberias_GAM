from __future__ import annotations

import math
from typing import Iterable

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, MultiLineString
from shapely.ops import substring


def _segment_line(line: LineString, max_length: float) -> list[LineString]:
    """Split a LineString into segments with max_length in geometry units."""
    length = line.length
    if length <= 0:
        return []
    if length <= max_length:
        return [line]
    segments = []
    n = int(math.ceil(length / max_length))
    for i in range(n):
        start = i * max_length
        end = min((i + 1) * max_length, length)
        if end - start <= 0:
            continue
        seg = substring(line, start, end)
        if not seg.is_empty and seg.length > 0:
            if seg.geom_type == "LineString":
                segments.append(seg)
            elif seg.geom_type == "Point":
                continue
            else:
                try:
                    segments.extend([g for g in seg.geoms if g.geom_type == "LineString" and g.length > 0])
                except Exception:
                    pass
    return segments


def segment_lines(lines_metric: gpd.GeoDataFrame, max_segment_length_m: float = 100.0) -> gpd.GeoDataFrame:
    """Segment lines longer than max_segment_length_m. CRS must be metric."""
    if max_segment_length_m <= 0:
        raise ValueError("La longitud máxima de segmentación debe ser mayor que cero.")
    records = []
    geometries = []
    for _, row in lines_metric.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        parts = list(geom.geoms) if geom.geom_type == "MultiLineString" else [geom]
        part_counter = 0
        for part in parts:
            if part.geom_type != "LineString" or part.length <= 0:
                continue
            segs = _segment_line(part, max_segment_length_m)
            for seg_idx, seg in enumerate(segs, start=1):
                attrs = row.drop(labels=[lines_metric.geometry.name]).to_dict()
                part_counter += 1
                attrs["segmento_id"] = f"{attrs.get('tramo_id', 'TRAMO')}_S{part_counter:03d}"
                attrs["longitud_segmento_m"] = float(seg.length)
                attrs["fue_segmentado"] = bool(row.get("longitud_m", part.length) > max_segment_length_m)
                records.append(attrs)
                geometries.append(seg)
    if not records:
        raise ValueError("No fue posible segmentar la capa de tuberías.")
    return gpd.GeoDataFrame(records, geometry=geometries, crs=lines_metric.crs).reset_index(drop=True)
