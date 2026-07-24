from __future__ import annotations

import io
import zipfile
from datetime import datetime
from typing import Optional

import geopandas as gpd
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import LETTER, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .utils import APP_VERSION, dataframe_to_excel_bytes, format_number


def export_geojson_bytes(gdf: gpd.GeoDataFrame) -> bytes:
    return gdf.to_crs("EPSG:4326").to_json().encode("utf-8")


def export_shapefile_zip_bytes(gdf: gpd.GeoDataFrame, layer_name: str = "resultados_sustitucion") -> bytes:
    """Export a shapefile as ZIP. Uses pyogrio when available; Fiona is not required."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir) / layer_name
        out_dir.mkdir(parents=True, exist_ok=True)
        shp_path = out_dir / f"{layer_name}.shp"
        export = gdf.copy()
        rename = {
            "cantidad_intervenciones": "interv",
            "indicador_ajustado_100m": "ind100m",
            "clasificacion": "semaforo",
            "longitud_segmento_m": "long_m",
            "costo_unitario_m": "cost_m",
            "costo_estimado": "costo",
        }
        export = export.rename(columns={k: v for k, v in rename.items() if k in export.columns})
        try:
            export.to_file(shp_path, driver="ESRI Shapefile", encoding="utf-8", engine="pyogrio")
        except TypeError:
            export.to_file(shp_path, driver="ESRI Shapefile", encoding="utf-8")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for file in out_dir.iterdir():
                zf.write(file, arcname=file.name)
        return buffer.getvalue()


def export_results_excel(results: gpd.GeoDataFrame, summaries: dict[str, pd.DataFrame], params: dict) -> bytes:
    base = pd.DataFrame(results.drop(columns="geometry", errors="ignore"))
    params_df = pd.DataFrame([params])
    sheets = {"Parametros": params_df, "Resultados": base}
    sheets.update(summaries)
    return dataframe_to_excel_bytes(sheets)


def _fmt_cell(value: object, decimals: int = 2) -> str:
    if pd.isna(value):
        return ""
    if isinstance(value, float):
        return format_number(value, decimals)
    if isinstance(value, int):
        return format_number(value, 0)
    text = str(value)
    return text[:250]


def _paragraph_table(rows: list[list[object]], style: ParagraphStyle) -> list[list[Paragraph]]:
    out: list[list[Paragraph]] = []
    for row in rows:
        out.append([Paragraph(str(cell), style) for cell in row])
    return out


def _severity_styles(rows: list[list[str]], classification_col: Optional[int]) -> list[tuple]:
    if classification_col is None:
        return []
    styles = []
    color_map = {
        "Rojo": colors.HexColor("#d7191c"),
        "Amarillo": colors.HexColor("#fdae61"),
        "Verde": colors.HexColor("#1a9641"),
        "Sin datos": colors.HexColor("#808080"),
    }
    for i, row in enumerate(rows[1:], start=1):
        value = row[classification_col]
        if value in color_map:
            styles.append(("BACKGROUND", (classification_col, i), (classification_col, i), color_map[value]))
            styles.append(("TEXTCOLOR", (classification_col, i), (classification_col, i), colors.white if value != "Amarillo" else colors.black))
    return styles


def build_pdf_report(
    results: gpd.GeoDataFrame,
    summaries: dict[str, pd.DataFrame],
    params: dict,
    analyst: str = "",
    department: str = "",
    institution: str = "AyA",
    observations: str = "",
    result_columns: Optional[list[str]] = None,
    max_result_rows: int = 500,
) -> bytes:
    """Create an executive PDF report in memory, including a compact results table."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(LETTER),
        rightMargin=1.0 * cm,
        leftMargin=1.0 * cm,
        topMargin=1.0 * cm,
        bottomMargin=1.0 * cm,
    )
    styles = getSampleStyleSheet()
    body = styles["BodyText"]
    small = ParagraphStyle("small", parent=styles["BodyText"], fontSize=6.5, leading=8, wordWrap="CJK")
    small_center = ParagraphStyle("small_center", parent=small, alignment=TA_CENTER)
    header = ParagraphStyle("header", parent=small_center, fontName="Helvetica-Bold", textColor=colors.white)
    story = []

    title = "Informe ejecutivo de priorización de sustitución de tuberías"
    story.append(Paragraph(title, styles["Title"]))
    story.append(Paragraph(f"Versión del aplicativo: {APP_VERSION}", styles["Normal"]))
    story.append(Paragraph(f"Fecha de análisis: {datetime.now().strftime('%d/%m/%Y %H:%M')}", styles["Normal"]))
    if analyst:
        story.append(Paragraph(f"Analista: {analyst}", styles["Normal"]))
    if department:
        story.append(Paragraph(f"Departamento: {department}", styles["Normal"]))
    if institution:
        story.append(Paragraph(f"Institución: {institution}", styles["Normal"]))
    story.append(Spacer(1, 0.35 * cm))

    total_segments = len(results)
    total_interv = int(results["cantidad_intervenciones"].sum()) if "cantidad_intervenciones" in results else 0
    total_recom = float(results["longitud_recomendada_m"].sum()) if "longitud_recomendada_m" in results else 0
    total_cost = float(results["costo_estimado"].sum()) if "costo_estimado" in results else 0
    red = int((results["clasificacion"] == "Rojo").sum()) if "clasificacion" in results else 0
    yellow = int((results["clasificacion"] == "Amarillo").sum()) if "clasificacion" in results else 0

    story.append(Paragraph("Resumen ejecutivo", styles["Heading2"]))
    story.append(
        Paragraph(
            f"Se analizaron {format_number(total_segments,0)} segmentos filtrados y {format_number(total_interv,0)} intervenciones asociadas. "
            f"La longitud recomendada preliminar para sustitución es de {format_number(total_recom,2)} m, correspondiente a segmentos clasificados en rojo o amarillo. "
            f"Se identificaron {format_number(red,0)} segmentos rojos y {format_number(yellow,0)} segmentos amarillos. "
            f"El costo estimado total, según los costos unitarios configurados o editados, es de ₡{format_number(total_cost,2)}.",
            body,
        )
    )
    story.append(Spacer(1, 0.25 * cm))

    story.append(Paragraph("Parámetros utilizados", styles["Heading2"]))
    param_rows_text = [["Parámetro", "Valor"]] + [[str(k), str(v)] for k, v in params.items()]
    param_rows = _paragraph_table(param_rows_text, small)
    param_rows[0] = [Paragraph("Parámetro", header), Paragraph("Valor", header)]
    table = Table(param_rows, colWidths=[6 * cm, 18 * cm], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#002B5C")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(table)
    story.append(Spacer(1, 0.35 * cm))

    if "Resumen semáforo" in summaries and not summaries["Resumen semáforo"].empty:
        story.append(Paragraph("Resumen por semáforo", styles["Heading2"]))
        df = summaries["Resumen semáforo"].copy()
        keep = [c for c in ["clasificacion", "tramos", "intervenciones", "longitud_recomendada_m", "costo_estimado"] if c in df.columns]
        data_text = [keep] + df[keep].round(2).astype(str).values.tolist()
        data = _paragraph_table(data_text, small_center)
        data[0] = [Paragraph(c, header) for c in keep]
        table = Table(data, repeatRows=1)
        base_styles = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#002B5C")),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]
        class_idx = keep.index("clasificacion") if "clasificacion" in keep else None
        table.setStyle(TableStyle(base_styles + _severity_styles(data_text, class_idx)))
        story.append(table)
        story.append(Spacer(1, 0.35 * cm))

    story.append(Paragraph("Tabla de resultados", styles["Heading2"]))
    base = pd.DataFrame(results.drop(columns="geometry", errors="ignore"))
    if result_columns:
        cols = [c for c in result_columns if c in base.columns]
    else:
        candidates = [
            "segmento_id", "tramo_id", "sector_direccion_aprox", "CODSISTEMA", "CODMATERIA", "Diametro",
            "Funcion", "longitud_segmento_m", "cantidad_intervenciones", "indicador_ajustado_100m",
            "clasificacion", "longitud_recomendada_m", "costo_unitario_m", "costo_estimado",
        ]
        cols = [c for c in candidates if c in base.columns]
    if not cols:
        cols = list(base.columns[:10])

    table_df = base[cols].copy().head(max_result_rows)
    numeric_cols = table_df.select_dtypes(include="number").columns
    for col in numeric_cols:
        table_df[col] = table_df[col].map(lambda x: _fmt_cell(x, 2))
    for col in table_df.columns.difference(numeric_cols):
        table_df[col] = table_df[col].map(lambda x: _fmt_cell(x, 2))

    header_labels = {
        "segmento_id": "Segmento",
        "tramo_id": "Tramo",
        "sector_direccion_aprox": "Sector / dirección",
        "CODSISTEMA": "Sistema",
        "CODMATERIA": "Material",
        "Diametro": "Diám.",
        "Funcion": "Función",
        "longitud_segmento_m": "Long. m",
        "cantidad_intervenciones": "Interv.",
        "indicador_100m": "Ind. 100 m",
        "indicador_ajustado_100m": "Ind. ajust.",
        "clasificacion": "Semáforo",
        "tipo_intervencion_dominante": "Tipo dominante",
        "longitud_recomendada_m": "Long. rec. m",
        "costo_unitario_m": "Costo/m",
        "costo_estimado": "Costo total",
    }
    table_rows_text = [[header_labels.get(c, c) for c in cols]] + table_df.astype(str).values.tolist()
    table_rows = _paragraph_table(table_rows_text, small)
    table_rows[0] = [Paragraph(str(c), header) for c in table_rows_text[0]]

    available_width = 25.5 * cm
    width_by_col = {
        "segmento_id": 2.5 * cm,
        "tramo_id": 2.2 * cm,
        "sector_direccion_aprox": 3.8 * cm,
        "CODSISTEMA": 1.7 * cm,
        "CODMATERIA": 1.5 * cm,
        "Diametro": 1.2 * cm,
        "Funcion": 1.9 * cm,
        "longitud_segmento_m": 1.6 * cm,
        "cantidad_intervenciones": 1.3 * cm,
        "indicador_100m": 1.5 * cm,
        "indicador_ajustado_100m": 1.6 * cm,
        "clasificacion": 1.6 * cm,
        "tipo_intervencion_dominante": 2.5 * cm,
        "longitud_recomendada_m": 1.7 * cm,
        "costo_unitario_m": 1.7 * cm,
        "costo_estimado": 1.9 * cm,
    }
    col_widths = [width_by_col.get(c, 1.8 * cm) for c in cols]
    total_width = sum(col_widths)
    if total_width > available_width:
        scale = available_width / total_width
        col_widths = [max(1.0 * cm, w * scale) for w in col_widths]

    result_table = Table(table_rows, colWidths=col_widths, repeatRows=1, splitByRow=1)
    base_styles = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#002B5C")),
        ("GRID", (0, 0), (-1, -1), 0.20, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]
    class_idx = cols.index("clasificacion") if "clasificacion" in cols else None
    result_table.setStyle(TableStyle(base_styles + _severity_styles(table_rows_text, class_idx)))
    story.append(result_table)
    if len(base) > max_result_rows:
        story.append(Spacer(1, 0.15 * cm))
        story.append(Paragraph(f"Nota: por tamaño del PDF se muestran {max_result_rows} de {len(base)} filas filtradas. El Excel contiene la tabla completa.", small))
    story.append(Spacer(1, 0.35 * cm))

    story.append(Paragraph("Observaciones metodológicas", styles["Heading2"]))
    methodology = (
        "El indicador se calcula como cantidad de intervenciones asociadas al segmento dividida entre la longitud del segmento en metros, multiplicada por 100. "
        "El resultado es un criterio técnico preliminar y debe complementarse con validación hidráulica, condición estructural, criticidad operativa, antigüedad, continuidad del servicio, revisión de campo y disponibilidad presupuestaria."
    )
    story.append(Paragraph(methodology, body))
    if observations:
        story.append(Spacer(1, 0.2 * cm))
        story.append(Paragraph(f"Observaciones del analista: {observations}", body))

    doc.build(story)
    return buffer.getvalue()
