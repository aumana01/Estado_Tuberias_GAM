from __future__ import annotations

import io
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

import geopandas as gpd
import pandas as pd


CLASS_ORDER = ["Rojo", "Amarillo", "Verde"]
CLASS_FILENAMES = {
    "Rojo": "tuberias_rojas",
    "Amarillo": "tuberias_amarillas",
    "Verde": "tuberias_verdes",
}


def _safe_text(value: object, default: str = "") -> str:
    if value is None or pd.isna(value):
        return default
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "null", "<null>"}:
        return default
    return text[:254]


def _safe_series(gdf: gpd.GeoDataFrame, col: Optional[str], default: str) -> pd.Series:
    if col and col in gdf.columns:
        return gdf[col].map(lambda x: _safe_text(x, default))
    return pd.Series([default] * len(gdf), index=gdf.index)


def _write_shapefile(gdf: gpd.GeoDataFrame, path: Path) -> None:
    try:
        gdf.to_file(path, driver="ESRI Shapefile", engine="pyogrio", encoding="utf-8")
    except TypeError:
        gdf.to_file(path, driver="ESRI Shapefile", encoding="utf-8")


def _prepare_export_layer(
    results: gpd.GeoDataFrame,
    system_col: Optional[str] = None,
    material_col: Optional[str] = None,
    diameter_col: Optional[str] = None,
    function_col: Optional[str] = None,
) -> gpd.GeoDataFrame:
    """Create a SHP-friendly layer with source and calculated attributes.

    Shapefile field names are limited, so the export uses stable short names while
    preserving the attributes most relevant for review and GIS use.
    """
    if results is None or results.empty:
        return gpd.GeoDataFrame(columns=["geometry"], geometry="geometry", crs=getattr(results, "crs", None))

    gdf = results.copy()
    if "clasificacion" in gdf.columns:
        gdf = gdf[gdf["clasificacion"].astype(str).isin(CLASS_ORDER)].copy()

    if gdf.empty:
        return gpd.GeoDataFrame(columns=["geometry"], geometry="geometry", crs=results.crs)

    export = gpd.GeoDataFrame(
        {
            "seg_id": _safe_series(gdf, "segmento_id", "Sin ID"),
            "tramo_id": _safe_series(gdf, "tramo_id", "Sin tramo"),
            "sistema": _safe_series(gdf, system_col, "Sin sistema"),
            "material": _safe_series(gdf, material_col, "Desconocido"),
            "diametro": _safe_series(gdf, diameter_col, "Sin dato"),
            "funcion": _safe_series(gdf, function_col, "Sin dato"),
            "clasif": _safe_series(gdf, "clasificacion", "Sin datos"),
            "estado": _safe_series(gdf, "estado_estimado", "Sin datos"),
            "metodo": _safe_series(gdf, "metodo_asociacion_dominante", "Sin datos"),
            "long_m": pd.to_numeric(gdf.get("longitud_segmento_m", 0), errors="coerce").fillna(0).round(2),
            "cant_int": pd.to_numeric(gdf.get("cantidad_intervenciones", 0), errors="coerce").fillna(0).astype(int),
            "ind_100m": pd.to_numeric(gdf.get("indicador_100m", 0), errors="coerce").fillna(0).round(3),
            "gravedad": pd.to_numeric(gdf.get("orden_gravedad", 0), errors="coerce").fillna(0).astype(int),
        },
        geometry=gdf.geometry,
        crs=gdf.crs,
    )
    export["long_km"] = (export["long_m"] / 1000.0).round(5)
    export = export[export.geometry.notna()].copy()
    export = export[~export.geometry.is_empty].copy()
    return export.reset_index(drop=True)


def build_results_shp_zip(
    results: gpd.GeoDataFrame,
    system_col: Optional[str] = None,
    material_col: Optional[str] = None,
    diameter_col: Optional[str] = None,
    function_col: Optional[str] = None,
) -> bytes:
    """Return a ZIP with classified pipe results as ESRI Shapefiles.

    The ZIP includes one combined shapefile with all classified segments and one
    shapefile per classification when records exist: Rojo, Amarillo and Verde.
    """
    export = _prepare_export_layer(
        results,
        system_col=system_col,
        material_col=material_col,
        diameter_col=diameter_col,
        function_col=function_col,
    )

    buffer = io.BytesIO()
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        notes = []

        if export.empty:
            notes.append("No hay tuberías verdes, amarillas o rojas para exportar con la selección actual.")
        else:
            _write_shapefile(export, tmp / "tuberias_estado_todas.shp")
            notes.append(f"Registros exportados en capa combinada: {len(export)}")

            for class_name in CLASS_ORDER:
                subset = export[export["clasif"].astype(str) == class_name].copy()
                if subset.empty:
                    notes.append(f"Sin registros para clasificación {class_name}.")
                    continue
                _write_shapefile(subset, tmp / f"{CLASS_FILENAMES[class_name]}.shp")
                notes.append(f"{class_name}: {len(subset)} registros, {subset['long_km'].sum():.3f} km")

        (tmp / "LEAME_exportacion_SHP.txt").write_text("\n".join(notes), encoding="utf-8")

        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in tmp.iterdir():
                zf.write(file, arcname=file.name)

    return buffer.getvalue()
