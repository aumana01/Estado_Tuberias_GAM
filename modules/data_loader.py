from __future__ import annotations

import io
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

import geopandas as gpd
import pandas as pd

try:
    gpd.options.io_engine = "pyogrio"
except Exception:
    pass
from shapely.geometry import LineString, Point, Polygon

from .utils import CRS_CRTM05, CRS_WGS84, parse_dates, pick_default, safe_numeric

SUPPORTED_VECTOR_EXTENSIONS = {".json", ".geojson", ".zip", ".shp", ".kml", ".kmz"}
SUPPORTED_TABLE_EXTENSIONS = {".csv", ".xlsx", ".xls"}


def _safe_read_file(path: Path) -> gpd.GeoDataFrame:
    """Read vector files preferring pyogrio, avoiding Fiona/GDAL build issues on Windows."""
    try:
        return gpd.read_file(path, engine="pyogrio")
    except TypeError:
        return gpd.read_file(path)
    except Exception as exc:
        try:
            return gpd.read_file(path)
        except Exception:
            raise exc


def _save_upload_to_temp(uploaded_file, suffix: str) -> Path:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(uploaded_file.getvalue())
    tmp.flush()
    tmp.close()
    return Path(tmp.name)


def _parse_kml_coordinates(text: str) -> list[tuple[float, float]]:
    coords: list[tuple[float, float]] = []
    for item in text.replace("\n", " ").replace("\t", " ").split():
        parts = item.split(",")
        if len(parts) >= 2:
            try:
                lon = float(parts[0])
                lat = float(parts[1])
                coords.append((lon, lat))
            except ValueError:
                continue
    return coords


def _read_kml_simple(path: Path) -> gpd.GeoDataFrame:
    """Minimal KML reader for Point, LineString and Polygon placemarks.

    This fallback avoids depending on Fiona/KML drivers in Windows environments.
    It is intentionally conservative but covers common KML/KMZ overlays used for reference.
    """
    tree = ET.parse(path)
    root = tree.getroot()
    ns = {"kml": "http://www.opengis.net/kml/2.2"}
    placemarks = root.findall(".//kml:Placemark", ns)
    if not placemarks:
        placemarks = root.findall(".//Placemark")

    rows = []
    geoms = []
    for idx, pm in enumerate(placemarks, start=1):
        def find_text(xpath: str) -> str:
            el = pm.find(xpath, ns)
            if el is None:
                el = pm.find(xpath.replace("kml:", ""))
            return (el.text or "").strip() if el is not None else ""

        name = find_text("kml:name") or f"Elemento {idx}"
        desc = find_text("kml:description")

        geom = None
        point_coords = pm.find(".//kml:Point/kml:coordinates", ns)
        if point_coords is None:
            point_coords = pm.find(".//Point/coordinates")
        line_coords = pm.find(".//kml:LineString/kml:coordinates", ns)
        if line_coords is None:
            line_coords = pm.find(".//LineString/coordinates")
        poly_coords = pm.find(".//kml:Polygon//kml:outerBoundaryIs//kml:LinearRing/kml:coordinates", ns)
        if poly_coords is None:
            poly_coords = pm.find(".//Polygon//outerBoundaryIs//LinearRing/coordinates")

        if point_coords is not None and point_coords.text:
            coords = _parse_kml_coordinates(point_coords.text)
            if coords:
                geom = Point(coords[0])
        elif line_coords is not None and line_coords.text:
            coords = _parse_kml_coordinates(line_coords.text)
            if len(coords) >= 2:
                geom = LineString(coords)
        elif poly_coords is not None and poly_coords.text:
            coords = _parse_kml_coordinates(poly_coords.text)
            if len(coords) >= 3:
                if coords[0] != coords[-1]:
                    coords.append(coords[0])
                geom = Polygon(coords)

        if geom is not None and not geom.is_empty:
            rows.append({"name": name, "description": desc})
            geoms.append(geom)

    if not rows:
        raise ValueError("No fue posible leer geometrías Point, LineString o Polygon desde el KML.")
    return gpd.GeoDataFrame(rows, geometry=geoms, crs=CRS_WGS84)


def _read_shp_zip(path: Path, default_crs: str) -> gpd.GeoDataFrame:
    with tempfile.TemporaryDirectory() as tmpdir:
        with zipfile.ZipFile(path) as z:
            z.extractall(tmpdir)
        shp_files = list(Path(tmpdir).rglob("*.shp"))
        if not shp_files:
            # If it is not a shapefile zip, try reading the zip directly as a last resort.
            return _safe_read_file(path)
        gdf = _safe_read_file(shp_files[0])
        if gdf.crs is None:
            gdf = gdf.set_crs(default_crs, allow_override=True)
        return gdf


def read_vector_file(file_or_path, default_crs: str = CRS_WGS84) -> gpd.GeoDataFrame:
    """Read JSON/GeoJSON, zipped SHP, SHP, KML or KMZ as a GeoDataFrame."""
    if hasattr(file_or_path, "name") and hasattr(file_or_path, "getvalue"):
        suffix = Path(file_or_path.name).suffix.lower()
        temp_path = _save_upload_to_temp(file_or_path, suffix)
        path = temp_path
    else:
        path = Path(file_or_path)
        suffix = path.suffix.lower()

    if suffix not in SUPPORTED_VECTOR_EXTENSIONS:
        raise ValueError(f"Formato geoespacial no soportado: {suffix}")

    if suffix == ".kmz":
        with tempfile.TemporaryDirectory() as tmpdir:
            with zipfile.ZipFile(path) as z:
                z.extractall(tmpdir)
            kml_files = list(Path(tmpdir).rglob("*.kml"))
            if not kml_files:
                raise ValueError("El archivo KMZ no contiene un KML válido.")
            try:
                gdf = _safe_read_file(kml_files[0])
            except Exception:
                gdf = _read_kml_simple(kml_files[0])
    elif suffix == ".kml":
        try:
            gdf = _safe_read_file(path)
        except Exception:
            gdf = _read_kml_simple(path)
    elif suffix == ".zip":
        gdf = _read_shp_zip(path, default_crs)
    else:
        gdf = _safe_read_file(path)

    if gdf.empty:
        raise ValueError("La capa geoespacial no contiene registros.")
    if gdf.geometry.isna().all():
        raise ValueError("La capa geoespacial no contiene geometrías válidas.")
    gdf = gdf[gdf.geometry.notna()].copy()
    if gdf.crs is None:
        gdf = gdf.set_crs(default_crs, allow_override=True)
    return gdf


def validate_line_layer(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Validate and keep only line geometries."""
    gdf = gdf.copy()
    gdf = gdf[gdf.geometry.notna()]
    if gdf.empty:
        raise ValueError("La capa de tuberías no contiene geometrías.")
    valid_types = {"LineString", "MultiLineString"}
    gdf = gdf[gdf.geometry.geom_type.isin(valid_types)]
    if gdf.empty:
        raise ValueError("La capa de tuberías debe ser tipo línea o multilínea.")
    gdf = gdf[gdf.geometry.is_valid]
    if gdf.empty:
        raise ValueError("Todas las geometrías de tubería son inválidas.")
    return gdf.reset_index(drop=True)


def read_table_file(file_or_path) -> pd.DataFrame:
    """Read CSV/XLSX/XLS table with robust CSV encoding fallback."""
    if hasattr(file_or_path, "name") and hasattr(file_or_path, "getvalue"):
        name = file_or_path.name
        suffix = Path(name).suffix.lower()
        content = file_or_path.getvalue()
        if suffix == ".csv":
            for enc in ("utf-8-sig", "utf-8", "latin1", "cp1252"):
                try:
                    return pd.read_csv(io.BytesIO(content), encoding=enc)
                except UnicodeDecodeError:
                    continue
            return pd.read_csv(io.BytesIO(content), encoding="latin1", errors="replace")
        if suffix in {".xlsx", ".xls"}:
            return pd.read_excel(io.BytesIO(content))
        raise ValueError(f"Formato tabular no soportado: {suffix}")

    path = Path(file_or_path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        for enc in ("utf-8-sig", "utf-8", "latin1", "cp1252"):
            try:
                return pd.read_csv(path, encoding=enc)
            except UnicodeDecodeError:
                continue
        return pd.read_csv(path, encoding="latin1")
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    raise ValueError(f"Formato tabular no soportado: {suffix}")


def suggest_point_columns(df: pd.DataFrame) -> dict[str, Optional[str]]:
    """Suggest common CSV field mappings."""
    cols = list(df.columns)
    return {
        "x": pick_default(cols, ["Longitud", "X", "Este", "Easting", "lon", "longitude"]),
        "y": pick_default(cols, ["Latitud", "Y", "Norte", "Northing", "lat", "latitude"]),
        "date": pick_default(cols, ["Fecha_Reso", "Fecha_Gene", "Fecha", "fecha_intervencion"]),
        "type": pick_default(cols, ["Nombre_Ord", "Tipo_Orden", "Tipo", "Intervencion", "Tematica"]),
        "order": pick_default(cols, ["Orden", "Orden_Servicio", "ID", "OBJECTID *", "OBJECTID"]),
        "system": pick_default(cols, ["Codigo_Sis", "CODSISTEMA", "Sistema"]),
        "description": pick_default(cols, ["Observacio", "Observaciones", "Descripcion", "Direccion"]),
    }


def build_points_gdf(
    df: pd.DataFrame,
    x_col: str,
    y_col: str,
    point_crs: str = CRS_CRTM05,
    date_col: Optional[str] = None,
) -> gpd.GeoDataFrame:
    """Build a point layer from X/Y columns."""
    if x_col not in df.columns or y_col not in df.columns:
        raise ValueError("Las columnas de coordenadas seleccionadas no existen en el archivo.")
    work = df.copy()
    work[x_col] = safe_numeric(work[x_col])
    work[y_col] = safe_numeric(work[y_col])
    work = work.dropna(subset=[x_col, y_col]).copy()
    if work.empty:
        raise ValueError("No hay coordenadas válidas para construir puntos.")
    work["geometry"] = [Point(xy) for xy in zip(work[x_col], work[y_col])]
    gdf = gpd.GeoDataFrame(work, geometry="geometry", crs=point_crs)
    if date_col and date_col in gdf.columns:
        gdf["fecha_intervencion_dt"] = parse_dates(gdf[date_col])
    return gdf


def infer_crs_from_coordinates(df: pd.DataFrame, x_col: str, y_col: str) -> str:
    """Infer WGS84 if columns look like lon/lat, otherwise CRTM05."""
    x = safe_numeric(df[x_col]).dropna()
    y = safe_numeric(df[y_col]).dropna()
    if len(x) and len(y):
        if x.between(-180, 180).mean() > 0.95 and y.between(-90, 90).mean() > 0.95:
            return CRS_WGS84
    return CRS_CRTM05
