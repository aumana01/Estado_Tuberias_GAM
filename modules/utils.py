from __future__ import annotations

import io
import re
import unicodedata
from datetime import datetime
from typing import Iterable, Optional

import pandas as pd

APP_VERSION = "v1.3.3"
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
        .replace({"nan": None, "None": None, "<Null>": None, "": None}),
        errors="coerce",
    )


def parse_dates(series: pd.Series) -> pd.Series:
    """Parse dates robustly for CSV/Excel fields used by service orders.

    The AyA exports commonly mix blank values, <Null>, day-first dates and
    datetime strings. Returning pandas.NaT for invalid values keeps filtering
    and point creation stable instead of failing during app startup.
    """
    if series is None:
        return pd.Series(dtype="datetime64[ns]")
    cleaned = (
        series.astype(str)
        .str.strip()
        .replace({"": None, "nan": None, "None": None, "<Null>": None, "null": None})
    )
    parsed = pd.to_datetime(cleaned, errors="coerce", dayfirst=True)
    if parsed.notna().sum() == 0:
        parsed = pd.to_datetime(cleaned, errors="coerce", dayfirst=False)
    return parsed


def pick_default(columns: Iterable[str], candidates: Iterable[str]) -> Optional[str]:
    cols = list(columns)
    norm_map = {normalize_text(c): c for c in cols}
    for cand in candidates:
        if normalize_text(cand) in norm_map:
            return norm_map[normalize_text(cand)]
    for cand in candidates:
        nc = normalize_text(cand)
        for col in cols:
            if nc and nc in normalize_text(col):
                return col
    return None


def format_number(value: float, decimals: int = 0) -> str:
    try:
        return f"{float(value):,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "0"


def dataframe_to_excel_bytes(sheets: dict[str, pd.DataFrame]) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for name, df in sheets.items():
            safe_name = str(name)[:31].replace("/", "-").replace("\\", "-").replace("*", "-").replace("?", "-").replace("[", "(").replace("]", ")")
            df.to_excel(writer, sheet_name=safe_name, index=False)
    return output.getvalue()


def today_label() -> str:
    return datetime.now().strftime("%d/%m/%Y")
