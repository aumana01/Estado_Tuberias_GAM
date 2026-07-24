from __future__ import annotations

import math
import re
from typing import Optional

import geopandas as gpd
import pandas as pd

from .utils import SEVERITY_ORDER, normalize_text, safe_numeric


STATE_ORDER = {"Malo": 0, "Regular": 1, "Bueno": 2}
STATE_TO_SEVERITY = {"Malo": "Rojo", "Regular": "Amarillo", "Bueno": "Verde"}
SEVERITY_TO_STATE = {"Rojo": "Malo", "Amarillo": "Regular", "Verde": "Bueno", "Sin datos": "Bueno"}

# Supuestos base por material cuando un segmento NO tiene órdenes/intervenciones asociadas.
# El algoritmo depura estos porcentajes cuando sí hay órdenes, asignando el segmento por indicador /100 m.
DEFAULT_MATERIAL_STATE_PERCENT = {
    "AC - Asbesto cemento": {"Malo": 1.00, "Regular": 0.00, "Bueno": 0.00},
    "Desconocido": {"Malo": 0.00, "Regular": 0.85, "Bueno": 0.15},
    "HD - Hierro dúctil": {"Malo": 0.05, "Regular": 0.70, "Bueno": 0.25},
    "HF - Hierro fundido": {"Malo": 0.05, "Regular": 0.70, "Bueno": 0.25},
    "HG - Hierro galvanizado": {"Malo": 0.05, "Regular": 0.70, "Bueno": 0.25},
    "LATON": {"Malo": 1.00, "Regular": 0.00, "Bueno": 0.00},
    "Otro": {"Malo": 1.00, "Regular": 0.00, "Bueno": 0.00},
    "PEAD - Polietileno de alta densidad": {"Malo": 0.10, "Regular": 0.40, "Bueno": 0.50},
    "Polietileno reticulado (PEX)": {"Malo": 0.00, "Regular": 0.00, "Bueno": 1.00},
    "PVC- Policloruro de vinilo": {"Malo": 0.20, "Regular": 0.50, "Bueno": 0.30},
}

PREFERRED_MATERIAL_ORDER = list(DEFAULT_MATERIAL_STATE_PERCENT.keys())

FORCED_BAD_MATERIALS = {
    "ac",
    "asbesto cemento",
    "asbesto-cemento",
    "ac - asbesto cemento",
    "laton",
    "latón",
    "otro",
}

INCH_TO_MM_NOMINAL = {
    0.5: 12,
    0.75: 19,
    1.0: 25,
    1.5: 38,
    2.0: 50,
    2.5: 63,
    3.0: 75,
    4.0: 100,
    6.0: 150,
    8.0: 200,
    10.0: 250,
    12.0: 300,
    14.0: 350,
    16.0: 400,
    18.0: 450,
    20.0: 500,
    24.0: 600,
}


def _to_float(value: object) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    txt = str(value).strip()
    if not txt or normalize_text(txt) in {"nan", "none", "null", "<null>"}:
        return None
    txt = txt.replace(",", ".")
    m = re.search(r"-?\d+(?:\.\d+)?", txt)
    if not m:
        return None
    try:
        return float(m.group(0))
    except Exception:
        return None


def pipe_diameter_norm(value: object) -> int | None:
    """Normalize pipe diameters to nominal millimeters when possible."""
    n = _to_float(value)
    if n is None or n <= 0:
        return None
    return int(round(n))


def order_diameter_candidates(value: object) -> list[int]:
    """Return possible nominal-mm candidates for an order diameter.

    The first candidate is the value as written. For values up to 24, a common
    inch-to-mm nominal conversion is also tested because some order systems record
    pipe size in inches while the catastro is usually in nominal millimeters.
    """
    n = _to_float(value)
    if n is None or n <= 0:
        return []
    candidates: list[int] = []
    exact = int(round(n))
    candidates.append(exact)
    # Common nominal inch sizes. This is only a candidate; if no catastro segment
    # exists with that diameter inside the radius, the algorithm falls back to nearest.
    rounded = round(n * 4) / 4
    if rounded in INCH_TO_MM_NOMINAL:
        candidates.append(INCH_TO_MM_NOMINAL[rounded])
    # Very small integer sizes often represent inches in field records.
    if n <= 24 and float(int(n)) == float(n) and float(n) in INCH_TO_MM_NOMINAL:
        candidates.append(INCH_TO_MM_NOMINAL[float(n)])
    # Deduplicate while preserving order.
    out: list[int] = []
    for c in candidates:
        if c and c > 0 and c not in out:
            out.append(c)
    return out


def material_label(value: object) -> str:
    """Map catastro material codes/names to display labels used in the summary."""
    raw = str(value).strip() if value is not None else ""
    norm = normalize_text(raw)
    mapping = {
        "ac": "AC - Asbesto cemento",
        "asbesto cemento": "AC - Asbesto cemento",
        "asbesto-cemento": "AC - Asbesto cemento",
        "desconocido": "Desconocido",
        "hd": "HD - Hierro dúctil",
        "hierro ductil": "HD - Hierro dúctil",
        "hierro dúctil": "HD - Hierro dúctil",
        "hf": "HF - Hierro fundido",
        "hierro fundido": "HF - Hierro fundido",
        "hg": "HG - Hierro galvanizado",
        "hierro galvanizado": "HG - Hierro galvanizado",
        "laton": "LATON",
        "latón": "LATON",
        "otro": "Otro",
        "pead": "PEAD - Polietileno de alta densidad",
        "polietileno alta densidad": "PEAD - Polietileno de alta densidad",
        "polietileno de alta densidad": "PEAD - Polietileno de alta densidad",
        "pex": "Polietileno reticulado (PEX)",
        "polietileno reticulado": "Polietileno reticulado (PEX)",
        "pvc": "PVC- Policloruro de vinilo",
        "pvc- policloruro de vinilo": "PVC- Policloruro de vinilo",
        "policloruro de vinilo": "PVC- Policloruro de vinilo",
    }
    return mapping.get(norm, raw if raw else "Desconocido")


def is_forced_bad_material(value: object) -> bool:
    label = material_label(value)
    norm_label = normalize_text(label)
    raw_norm = normalize_text(value)
    return norm_label in FORCED_BAD_MATERIALS or raw_norm in FORCED_BAD_MATERIALS


def classify_indicator(value: float, green_threshold: float, yellow_threshold: float, red_threshold: float) -> str:
    """Classify indicator into traffic-light colors.

    Values between yellow_threshold and red_threshold are kept as yellow/regular
    to avoid creating a fourth class. Lower red_threshold if those cases should be red.
    """
    if pd.isna(value):
        return "Sin datos"
    if value <= green_threshold:
        return "Verde"
    if value < red_threshold:
        return "Amarillo"
    return "Rojo"


def _nearest_join(points: gpd.GeoDataFrame, segments: gpd.GeoDataFrame, radius_m: float) -> gpd.GeoDataFrame:
    if points.empty or segments.empty:
        return gpd.GeoDataFrame(columns=list(points.columns) + ["segmento_id", "tramo_id", "distancia_m"], geometry="geometry", crs=points.crs)
    right_cols = [c for c in ["segmento_id", "tramo_id", "diametro_catastro_norm", "sistema_norm", "geometry"] if c in segments.columns]
    right = segments[right_cols].copy().reset_index(drop=True)
    left = points.copy()
    try:
        joined = gpd.sjoin_nearest(left, right, how="left", max_distance=radius_m, distance_col="distancia_m")
    except Exception:
        buffered = left.copy()
        buffered["geometry_original"] = left.geometry
        buffered["geometry"] = buffered.geometry.buffer(radius_m)
        candidates = gpd.sjoin(buffered, right, how="left", predicate="intersects")
        if candidates.empty:
            joined = left.copy()
            joined["segmento_id"] = pd.NA
            joined["tramo_id"] = pd.NA
            joined["distancia_m"] = pd.NA
        else:
            seg_geom = right.geometry
            distances = []
            for _, cand in candidates.iterrows():
                if pd.isna(cand.get("index_right")):
                    distances.append(pd.NA)
                else:
                    distances.append(cand["geometry_original"].distance(seg_geom.loc[int(cand["index_right"])]))
            candidates["distancia_m"] = distances
            candidates["geometry"] = candidates["geometry_original"]
            joined = candidates.drop(columns=["geometry_original"], errors="ignore")
    if joined.empty:
        return joined
    if "punto_idx" in joined.columns:
        joined = joined.sort_values("distancia_m", na_position="last").drop_duplicates("punto_idx", keep="first")
    return joined.dropna(subset=["segmento_id"]).copy()


def _join_by_key(
    points: gpd.GeoDataFrame,
    segments: gpd.GeoDataFrame,
    radius_m: float,
    key_col: str,
    method_label: str,
) -> gpd.GeoDataFrame:
    joined_parts: list[gpd.GeoDataFrame] = []
    common_keys = sorted(set(points[key_col].dropna().astype(str)) & set(segments[key_col].dropna().astype(str)))
    for key in common_keys:
        pgrp = points[points[key_col].astype(str) == key]
        sgrp = segments[segments[key_col].astype(str) == key]
        part = _nearest_join(pgrp, sgrp, radius_m)
        if not part.empty:
            part["metodo_asociacion"] = method_label
            joined_parts.append(part)
    if not joined_parts:
        return gpd.GeoDataFrame(columns=list(points.columns) + ["segmento_id", "tramo_id", "distancia_m", "metodo_asociacion"], geometry="geometry", crs=points.crs)
    out = pd.concat(joined_parts, ignore_index=True)
    return gpd.GeoDataFrame(out, geometry="geometry", crs=points.crs)


def associate_points_to_segments(
    points_metric: gpd.GeoDataFrame,
    segments_metric: gpd.GeoDataFrame,
    radius_m: float = 10.0,
    point_type_col: Optional[str] = None,
    point_location_col: Optional[str] = None,
    point_diameter_col: Optional[str] = None,
    segment_diameter_col: Optional[str] = None,
    point_system_col: Optional[str] = None,
    segment_system_col: Optional[str] = None,
) -> tuple[pd.DataFrame, gpd.GeoDataFrame]:
    """Associate points to segments using diameter first and nearest as fallback.

    Assignment order:
    1) same system + same normalized diameter, when both fields are available;
    2) same normalized diameter;
    3) nearest segment inside the configured radius.
    """
    if radius_m <= 0:
        raise ValueError("El radio de asociación debe ser mayor que cero.")
    if points_metric.empty:
        raise ValueError("La capa de puntos no contiene registros para analizar.")
    if segments_metric.empty:
        raise ValueError("La capa de segmentos no contiene registros para analizar.")

    left = points_metric.copy().reset_index(drop=True)
    left["punto_idx"] = left.index
    right = segments_metric.copy().reset_index(drop=True)

    # Normalize matching fields.
    if segment_diameter_col and segment_diameter_col in right.columns:
        right["diametro_catastro_norm"] = right[segment_diameter_col].apply(pipe_diameter_norm)
    else:
        right["diametro_catastro_norm"] = pd.NA

    if point_diameter_col and point_diameter_col in left.columns:
        left["diametro_orden_original"] = left[point_diameter_col]
        left["diametro_orden_candidatos"] = left[point_diameter_col].apply(order_diameter_candidates)
    else:
        left["diametro_orden_original"] = pd.NA
        left["diametro_orden_candidatos"] = [[] for _ in range(len(left))]

    if segment_system_col and segment_system_col in right.columns:
        right["sistema_norm"] = right[segment_system_col].apply(normalize_text)
    else:
        right["sistema_norm"] = ""
    if point_system_col and point_system_col in left.columns:
        left["sistema_norm"] = left[point_system_col].apply(normalize_text)
    else:
        left["sistema_norm"] = ""

    assigned_parts: list[gpd.GeoDataFrame] = []
    assigned_ids: set[int] = set()

    # Explode by diameter candidates for diameter-priority passes.
    cand = left[left["diametro_orden_candidatos"].map(len) > 0].copy()
    if not cand.empty and right["diametro_catastro_norm"].notna().any():
        cand = cand.explode("diametro_orden_candidatos")
        cand["diametro_match"] = pd.to_numeric(cand["diametro_orden_candidatos"], errors="coerce").astype("Int64")
        right["diametro_match"] = pd.to_numeric(right["diametro_catastro_norm"], errors="coerce").astype("Int64")
        cand["match_diametro_sistema"] = cand["sistema_norm"].astype(str) + "|" + cand["diametro_match"].astype(str)
        right["match_diametro_sistema"] = right["sistema_norm"].astype(str) + "|" + right["diametro_match"].astype(str)
        cand["match_diametro"] = cand["diametro_match"].astype(str)
        right["match_diametro"] = right["diametro_match"].astype(str)

        # 1) same system + diameter, only when system fields exist.
        if point_system_col and segment_system_col:
            j1 = _join_by_key(cand, right, radius_m, "match_diametro_sistema", "Diámetro + sistema")
            if not j1.empty:
                j1 = j1.sort_values("distancia_m").drop_duplicates("punto_idx", keep="first")
                assigned_parts.append(j1)
                assigned_ids.update(j1["punto_idx"].astype(int).tolist())

        # 2) same diameter for remaining points.
        cand2 = cand[~cand["punto_idx"].isin(assigned_ids)].copy()
        j2 = _join_by_key(cand2, right, radius_m, "match_diametro", "Diámetro")
        if not j2.empty:
            j2 = j2.sort_values("distancia_m").drop_duplicates("punto_idx", keep="first")
            assigned_parts.append(j2)
            assigned_ids.update(j2["punto_idx"].astype(int).tolist())

    # 3) nearest segment for remaining points.
    remaining = left[~left["punto_idx"].isin(assigned_ids)].copy()
    j3 = _nearest_join(remaining, right, radius_m)
    if not j3.empty:
        j3["metodo_asociacion"] = "Cercanía"
        assigned_parts.append(j3)
        assigned_ids.update(j3["punto_idx"].astype(int).tolist())

    if assigned_parts:
        joined_valid = pd.concat(assigned_parts, ignore_index=True)
        joined_valid = joined_valid.sort_values("distancia_m").drop_duplicates("punto_idx", keep="first")
        joined_valid = gpd.GeoDataFrame(joined_valid, geometry="geometry", crs=points_metric.crs)
    else:
        joined_valid = gpd.GeoDataFrame(columns=list(left.columns) + ["segmento_id", "tramo_id", "distancia_m", "metodo_asociacion"], geometry="geometry", crs=points_metric.crs)

    counts = joined_valid.groupby("segmento_id").size().rename("cantidad_intervenciones").reset_index()

    if point_type_col and point_type_col in joined_valid.columns:
        type_counts = (
            joined_valid.groupby(["segmento_id", point_type_col])
            .size()
            .rename("conteo")
            .reset_index()
            .sort_values(["segmento_id", "conteo"], ascending=[True, False])
        )
        dominant = type_counts.drop_duplicates("segmento_id").rename(columns={point_type_col: "tipo_intervencion_dominante"})
        counts = counts.merge(dominant[["segmento_id", "tipo_intervencion_dominante"]], on="segmento_id", how="left")
    else:
        counts["tipo_intervencion_dominante"] = pd.NA

    if point_location_col and point_location_col in joined_valid.columns:
        loc_counts = (
            joined_valid.groupby(["segmento_id", point_location_col])
            .size()
            .rename("conteo")
            .reset_index()
            .sort_values(["segmento_id", "conteo"], ascending=[True, False])
        )
        dominant_loc = loc_counts.drop_duplicates("segmento_id").rename(columns={point_location_col: "sector_direccion_aprox"})
        counts = counts.merge(dominant_loc[["segmento_id", "sector_direccion_aprox"]], on="segmento_id", how="left")
    else:
        counts["sector_direccion_aprox"] = pd.NA

    if "metodo_asociacion" in joined_valid.columns and not joined_valid.empty:
        method_counts = (
            joined_valid.groupby(["segmento_id", "metodo_asociacion"])
            .size()
            .rename("conteo")
            .reset_index()
            .sort_values(["segmento_id", "conteo"], ascending=[True, False])
        )
        dominant_method = method_counts.drop_duplicates("segmento_id")
        counts = counts.merge(
            dominant_method[["segmento_id", "metodo_asociacion"]].rename(columns={"metodo_asociacion": "metodo_asociacion_dominante"}),
            on="segmento_id",
            how="left",
        )
        method_pivot = (
            joined_valid.pivot_table(index="segmento_id", columns="metodo_asociacion", values="punto_idx", aggfunc="count", fill_value=0)
            .reset_index()
        )
        method_pivot.columns = ["segmento_id" if c == "segmento_id" else f"ordenes_{normalize_text(c).replace(' ', '_')}" for c in method_pivot.columns]
        counts = counts.merge(method_pivot, on="segmento_id", how="left")
    else:
        counts["metodo_asociacion_dominante"] = pd.NA

    return counts, joined_valid


def calculate_results(
    segments_metric: gpd.GeoDataFrame,
    counts: pd.DataFrame,
    green_threshold: float = 3.0,
    yellow_threshold: float = 5.0,
    red_threshold: float = 7.0,
    material_col: Optional[str] = None,
) -> gpd.GeoDataFrame:
    """Merge counts and calculate indicators, semáforo and estimated state."""
    result = segments_metric.copy()
    result = result.merge(counts, on="segmento_id", how="left")
    result["cantidad_intervenciones"] = result["cantidad_intervenciones"].fillna(0).astype(int)
    result["tipo_intervencion_dominante"] = result.get("tipo_intervencion_dominante", pd.Series(index=result.index, dtype="object")).fillna("")
    result["sector_direccion_aprox"] = result.get("sector_direccion_aprox", pd.Series(index=result.index, dtype="object")).fillna("")
    result["metodo_asociacion_dominante"] = result.get("metodo_asociacion_dominante", pd.Series(index=result.index, dtype="object")).fillna("")
    result["indicador_100m"] = (result["cantidad_intervenciones"] / result["longitud_segmento_m"]) * 100

    if material_col and material_col in result.columns:
        result["material_resumen"] = result[material_col].apply(material_label)
        result["material_malo_forzado"] = result[material_col].apply(is_forced_bad_material)
    else:
        result["material_resumen"] = "Desconocido"
        result["material_malo_forzado"] = False

    result["clasificacion"] = result["indicador_100m"].apply(lambda x: classify_indicator(x, green_threshold, yellow_threshold, red_threshold))
    result.loc[result["material_malo_forzado"], "clasificacion"] = "Rojo"
    result["estado_estimado"] = result["clasificacion"].map(SEVERITY_TO_STATE).fillna("Bueno")
    result.loc[result["material_malo_forzado"], "estado_estimado"] = "Malo"
    result["orden_gravedad"] = result["clasificacion"].map(SEVERITY_ORDER).fillna(0).astype(int)
    result["longitud_malo_directa_m"] = result["longitud_segmento_m"].where(result["estado_estimado"] == "Malo", 0.0)
    return result


def _material_percentages(label: str) -> dict[str, float]:
    if label in DEFAULT_MATERIAL_STATE_PERCENT:
        return DEFAULT_MATERIAL_STATE_PERCENT[label]
    # Conservative fallback for unknown non-catalogued labels.
    return {"Malo": 0.0, "Regular": 0.85, "Bueno": 0.15}


def condition_long_form(
    results: gpd.GeoDataFrame,
    system_col: Optional[str] = None,
    material_col: Optional[str] = None,
) -> pd.DataFrame:
    """Build long-form condition allocation by system, material and state.

    Segments with orders are assigned directly by the calculated state. Segments without
    orders are distributed by base material percentages. Forced-bad materials are always bad.
    """
    if results.empty:
        return pd.DataFrame(columns=["sistema", "material", "estado", "longitud_m"])
    df = pd.DataFrame(results.drop(columns="geometry", errors="ignore")).copy()
    if system_col and system_col in df.columns:
        df["sistema"] = df[system_col].fillna("Sin sistema").astype(str).replace({"": "Sin sistema", "<Null>": "Sin sistema"})
    else:
        df["sistema"] = "Sin sistema"
    if "material_resumen" in df.columns:
        df["material"] = df["material_resumen"]
    elif material_col and material_col in df.columns:
        df["material"] = df[material_col].apply(material_label)
    else:
        df["material"] = "Desconocido"

    rows = []
    for _, row in df.iterrows():
        length = float(row.get("longitud_segmento_m", 0) or 0)
        if length <= 0:
            continue
        system = row["sistema"]
        material = row["material"]
        forced = bool(row.get("material_malo_forzado", False))
        count = int(row.get("cantidad_intervenciones", 0) or 0)
        if forced:
            rows.append({"sistema": system, "material": material, "estado": "Malo", "longitud_m": length, "origen_estimacion": "Material malo forzado"})
        elif count > 0:
            state = str(row.get("estado_estimado", "Bueno"))
            rows.append({"sistema": system, "material": material, "estado": state, "longitud_m": length, "origen_estimacion": "Depurado por órdenes"})
        else:
            for state, pct in _material_percentages(material).items():
                if pct > 0:
                    rows.append({"sistema": system, "material": material, "estado": state, "longitud_m": length * pct, "origen_estimacion": "Supuesto base por material"})
    if not rows:
        return pd.DataFrame(columns=["sistema", "material", "estado", "longitud_m", "origen_estimacion"])
    return pd.DataFrame(rows)


def build_condition_tables(
    results: gpd.GeoDataFrame,
    system_col: Optional[str] = None,
    material_col: Optional[str] = None,
    selected_system: Optional[str] = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return length matrix, percent-by-material matrix and long-form allocation."""
    long_df = condition_long_form(results, system_col=system_col, material_col=material_col)
    if selected_system and selected_system != "Todos los sistemas":
        long_df = long_df[long_df["sistema"].astype(str) == str(selected_system)]

    if long_df.empty:
        empty = pd.DataFrame({"Estado": ["Malo", "Regular", "Bueno", "Total"]})
        return empty, empty.copy(), long_df

    materials_present = list(dict.fromkeys(long_df["material"].astype(str).tolist()))
    ordered_materials = [m for m in PREFERRED_MATERIAL_ORDER if m in materials_present]
    ordered_materials.extend(sorted([m for m in materials_present if m not in ordered_materials]))

    pivot = long_df.pivot_table(index="estado", columns="material", values="longitud_m", aggfunc="sum", fill_value=0.0)
    for state in ["Malo", "Regular", "Bueno"]:
        if state not in pivot.index:
            pivot.loc[state] = 0.0
    for material in ordered_materials:
        if material not in pivot.columns:
            pivot[material] = 0.0
    pivot = pivot.loc[["Malo", "Regular", "Bueno"], ordered_materials]
    pivot["TOTAL"] = pivot.sum(axis=1)
    grand_total = float(pivot["TOTAL"].sum())
    pivot["Porcentaje"] = pivot["TOTAL"] / grand_total if grand_total else 0.0
    total_row = pivot[ordered_materials + ["TOTAL"]].sum(axis=0)
    total_row["Porcentaje"] = 1.0 if grand_total else 0.0
    length_table = pd.concat([pivot, pd.DataFrame([total_row], index=["Total"])])
    length_table = length_table.reset_index().rename(columns={"index": "Estado"})

    material_totals = pivot[ordered_materials].sum(axis=0).replace(0, pd.NA)
    pct_material = pivot.loc[["Malo", "Regular", "Bueno"], ordered_materials].div(material_totals, axis=1).fillna(0.0)
    pct_material.loc["Total"] = [1.0 if float(pivot[m].sum()) > 0 else 0.0 for m in ordered_materials]
    pct_material = pct_material.reset_index().rename(columns={"index": "Estado"})
    return length_table, pct_material, long_df


def summarize_assignment(joined_valid: gpd.GeoDataFrame) -> pd.DataFrame:
    if joined_valid is None or joined_valid.empty or "metodo_asociacion" not in joined_valid.columns:
        return pd.DataFrame(columns=["Método de asociación", "Órdenes asociadas", "Porcentaje"])
    df = joined_valid.groupby("metodo_asociacion").size().rename("Órdenes asociadas").reset_index()
    df = df.rename(columns={"metodo_asociacion": "Método de asociación"})
    total = df["Órdenes asociadas"].sum()
    df["Porcentaje"] = df["Órdenes asociadas"] / total if total else 0
    return df.sort_values("Órdenes asociadas", ascending=False)
