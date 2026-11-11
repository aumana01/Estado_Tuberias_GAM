"""Consolidación controlada de fuentes de órdenes DATAMAR y RANC.

No elimina posibles solapamientos entre fuentes. DATAMAR se mantiene como
fuente de referencia para el cálculo principal de condición de la red.
"""
from __future__ import annotations

import pandas as pd

SOURCE_FIELD = "Fuente_datos"
ID_FIELD = "ID_fuente"


def normalize_source(df: pd.DataFrame, default_source: str) -> pd.DataFrame:
    """Conserva el esquema de entrada y crea metadatos de trazabilidad.

    Acepta archivos DATAMAR, RANC normalizados o un CSV previamente combinado.
    No inventa códigos de sistema ni coordenadas faltantes.
    """
    if default_source not in {"DATAMAR", "RANC"}:
        raise ValueError("La fuente debe ser DATAMAR o RANC.")
    out = df.copy()
    out = out.loc[:, ~out.columns.astype(str).str.match(r"^Unnamed:\s*\d+$")]
    out = out.drop(columns=["index"], errors="ignore")
    if SOURCE_FIELD not in out:
        out[SOURCE_FIELD] = default_source
    else:
        source = out[SOURCE_FIELD].astype("string").str.strip().str.upper()
        out[SOURCE_FIELD] = source.where(source.isin(["DATAMAR", "RANC"]), default_source)
    if ID_FIELD not in out:
        out[ID_FIELD] = ""
    ids = out[ID_FIELD].astype("string").fillna("").str.strip()
    missing = ids.eq("")
    if missing.any():
        order_id = out["Orden"].astype("string").fillna("").str.strip() if "Orden" in out else pd.Series("", index=out.index)
        object_id = out["OBJECTID *"].astype("string").fillna("").str.strip() if "OBJECTID *" in out else pd.Series("", index=out.index)
        base_id = order_id.where(order_id.ne(""), object_id)
        fallback = pd.Series(["fila_" + str(i + 1) for i in range(len(out))], index=out.index)
        base_id = base_id.where(base_id.ne(""), fallback)
        generated = out[SOURCE_FIELD].astype(str) + ":" + base_id.astype(str)
        out.loc[missing, ID_FIELD] = generated.loc[missing]
    return out


def merge_sources(datamar: pd.DataFrame, ranc: pd.DataFrame | None = None) -> pd.DataFrame:
    """Agrupa para exploración, sin deduplicación ni alterar originales."""
    primary = normalize_source(datamar, "DATAMAR")
    if ranc is None or ranc.empty:
        return primary
    historical = normalize_source(ranc, "RANC")
    return pd.concat([primary, historical], ignore_index=True, sort=False)


def source_counts(df: pd.DataFrame) -> dict[str, int]:
    if SOURCE_FIELD not in df:
        return {"DATAMAR": len(df)}
    counts = df[SOURCE_FIELD].astype("string").str.upper().value_counts()
    return {str(source): int(count) for source, count in counts.items()}
