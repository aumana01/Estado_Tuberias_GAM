from __future__ import annotations

import io
import re
import unicodedata
from datetime import datetime
from typing import Iterable, Optional

import pandas as pd

APP_VERSION = "v1.2.3"
CRS_WGS84 = "EPSG:4326"
CRS_CRTM05 = "EPSG:5367"

SEVERITY_ORDER = {"Rojo": 3, "Amarillo": 2, "Verde": 1, "Sin datos": 0}
SEVERITY_COLORS = {
    "Rojo": "#d7191c",
    "Amarillo": "#fdae61",
    "Verde": "#1a9641",
    "Sin datos": "#808080",
}


def normalize_text(value: object) -> str:
    """Normalize a value for robust column and category comparisons."""
    if value is None:
        return ""
    text = str(value).strip()
    text = "" if text.lower() in {"nan", "none", "<null>", "null"} else text
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def safe_numeric(series: pd.Series) -> pd.Series:
    """Convert a column to numeric, accepting commas and common null markers."""
    return pd.to_numeric(
        series.astype(str)
        .str.replace(" ", "", regex=False)
        .str.replace(",", ".", regex=False)
        .replace({"<Null>": None, "nan": None, "None": None, "": None}),
        errors="coerce",
    )


def parse_dates(series: pd.Series, dayfirst: bool = True) -> pd.Series:
    """Parse date strings robustly."""
    cleaned = series.replace({"<Null>": None, "nan": None, "None": None, "": None})
    return pd.to_datetime(cleaned, errors="coerce", dayfirst=dayfirst)


def pick_default(columns: Iterable[str], candidates: Iterable[str], fallback: Optional[str] = None) -> Optional[str]:
    """Return the best matching column name from candidates."""
    cols = list(columns)
    norm_to_col = {normalize_text(c): c for c in cols}
    for candidate in candidates:
        norm = normalize_text(candidate)
        if norm in norm_to_col:
            return norm_to_col[norm]
    for candidate in candidates:
        norm = normalize_text(candidate)
        for col_norm, col in norm_to_col.items():
            if norm and norm in col_norm:
                return col
    return fallback if fallback in cols else (cols[0] if cols else None)


def dataframe_to_excel_bytes(sheets: dict[str, pd.DataFrame]) -> bytes:
    """Build an Excel workbook in memory from several dataframes."""
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for sheet, df in sheets.items():
            clean_sheet = re.sub(r"[^A-Za-z0-9 _-]", "", sheet)[:31] or "Hoja"
            df.to_excel(writer, sheet_name=clean_sheet, index=False)
    return buffer.getvalue()


def now_label() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def format_number(value: float, decimals: int = 2) -> str:
    try:
        return f"{float(value):,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return ""
