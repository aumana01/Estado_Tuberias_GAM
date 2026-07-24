from __future__ import annotations

import gzip
import hashlib
import json
import pickle
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd

CACHE_SCHEMA_VERSION = "estado_tuberias_cache_v1"
CACHE_DIRNAME = ".cache_estado_tuberias"


def cache_dir(root: str | Path) -> Path:
    path = Path(root) / CACHE_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_json(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _safe_json(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple, set)):
        return [_safe_json(v) for v in value]
    return str(value)


def _hash_tabular(df: pd.DataFrame, columns: list[str] | None = None) -> str:
    """Return a deterministic hash for the dataframe content used by the analysis.

    The function intentionally hashes only the selected analysis columns plus geometry
    WKB when present. This avoids invalidating the cache because of unrelated columns,
    while still detecting changes in the inputs that affect the result.
    """
    h = hashlib.sha256()
    h.update(str(df.shape).encode("utf-8"))
    selected = [c for c in (columns or list(df.columns)) if c in df.columns]
    # Keep order deterministic and exclude geometry from the attribute hash.
    selected = [c for c in selected if c != "geometry"]
    h.update(json.dumps(selected, ensure_ascii=False).encode("utf-8"))

    if selected:
        attrs = df[selected].copy()
        for col in attrs.columns:
            attrs[col] = attrs[col].astype(str).fillna("")
        h.update(pd.util.hash_pandas_object(attrs, index=True).values.tobytes())

    if isinstance(df, gpd.GeoDataFrame) and "geometry" in df.columns:
        try:
            geom = df.geometry.to_wkb(hex=True).fillna("")
            h.update(pd.util.hash_pandas_object(geom, index=True).values.tobytes())
            h.update(str(df.crs).encode("utf-8"))
            h.update(str(tuple(round(float(x), 6) for x in df.total_bounds)).encode("utf-8"))
        except Exception:
            h.update(str(df.geometry.astype(str).head(1000).tolist()).encode("utf-8"))
    return h.hexdigest()


def build_analysis_hash(
    pipes_raw: gpd.GeoDataFrame,
    orders_df: pd.DataFrame,
    *,
    pipe_columns: list[str],
    order_columns: list[str],
    params: dict[str, Any],
) -> str:
    """Build a stable cache key for one complete analysis configuration."""
    payload = {
        "schema": CACHE_SCHEMA_VERSION,
        "params": _safe_json(params),
        "pipe_columns": pipe_columns,
        "order_columns": order_columns,
        "pipes_hash": _hash_tabular(pipes_raw, pipe_columns + ["geometry"]),
        "orders_hash": _hash_tabular(orders_df, order_columns),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def cache_path(root: str | Path, analysis_hash: str) -> Path:
    return cache_dir(root) / f"analysis_{analysis_hash}.pkl.gz"


def load_cached_analysis(root: str | Path, analysis_hash: str) -> dict[str, Any] | None:
    path = cache_path(root, analysis_hash)
    if not path.exists():
        return None
    with gzip.open(path, "rb") as f:
        data = pickle.load(f)
    if data.get("schema") != CACHE_SCHEMA_VERSION:
        return None
    return data


def save_cached_analysis(root: str | Path, analysis_hash: str, payload: dict[str, Any]) -> Path:
    path = cache_path(root, analysis_hash)
    payload = dict(payload)
    payload.update(
        {
            "schema": CACHE_SCHEMA_VERSION,
            "analysis_hash": analysis_hash,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    with gzip.open(path, "wb") as f:
        pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
    return path


def cache_info(root: str | Path, analysis_hash: str) -> dict[str, Any] | None:
    path = cache_path(root, analysis_hash)
    if not path.exists():
        return None
    stat = path.stat()
    return {
        "path": str(path),
        "size_mb": stat.st_size / (1024 * 1024),
        "modified_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
    }


def clear_cache(root: str | Path) -> None:
    path = Path(root) / CACHE_DIRNAME
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def list_cache_files(root: str | Path) -> list[Path]:
    path = Path(root) / CACHE_DIRNAME
    if not path.exists():
        return []
    return sorted(path.glob("analysis_*.pkl.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
