#!/usr/bin/env python3
"""Normaliza los dos ZIP RANC y opcionalmente crea un CSV combinado con DATAMAR.

Uso:
  python integrar_fugas_ranc.py --acometidas FugasGAM_Mant_Redes_12-19mm_nov2018-dic2024.zip \
      --redes FugasGAM_Mant_Redes_+25mm_nov2018-dic2024.zip \
      --datamar 'Ordenes de Servicio GAM.csv' --salida ./data

Dependencias: pip install pyshp pyproj

No descarta registros RANC sin coordenadas (los marca); no intenta deduplicar
entre fuentes sin evidencia suficiente. Nunca modifica el CSV DATAMAR original.
"""
from __future__ import annotations

import argparse
import csv
import json
import tempfile
import zipfile
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

import shapefile
from pyproj import Transformer

DATAMAR_HEADERS = [
    'OBJECTID *', 'Oficina', 'Tipo_Orden', 'Nombre_Ord', 'Estado', 'Orden',
    'Responsabl', 'Fecha_Reso', 'Expediente', 'Observacio', 'Hora_Asign',
    'Diametro', 'Profundida', 'Causa', 'Tipo_Tuber', 'Equipo_Esp',
    'Equipo_E_1', 'Equipo_E_2', 'Equipo_E_3', 'se_present', 'valvula_nu',
    'reemplazo_', 'cierre_val', 'danos', 'tipo_contr', 'tipo_valvu',
    'material_c', 'Fecha_Gene', 'Localizaci', 'Latitud', 'Longitud',
    'Direccion', 'Codigo_Sis',
]
ADDITIONAL_HEADERS = [
    'Fuente_datos', 'ID_fuente', 'Grupo_RANC', 'RANC_Material',
    'RANC_Provincia', 'RANC_Canton', 'RANC_Distrito', 'RANC_Anio',
    'RANC_Elev_med', 'Latitud_WGS84', 'Longitud_WGS84',
    'Coordenada_valida', 'Fecha_Gene_original', 'Fecha_Reso_original',
]
HEADERS = DATAMAR_HEADERS + ADDITIONAL_HEADERS
TRANSFORMER = Transformer.from_crs('EPSG:4326', 'EPSG:5367', always_xy=True)


def parse_date(value: object) -> str:
    """Devuelve DD/MM/AAAA, aceptando seriales de Excel e ISO."""
    s = str(value or '').strip()
    if not s:
        return ''
    if s.isdigit() and 10000 <= int(s) <= 90000:
        return (datetime(1899, 12, 30) + timedelta(days=int(s))).strftime('%d/%m/%Y')
    for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y'):
        try:
            return datetime.strptime(s[:10], fmt).strftime('%d/%m/%Y')
        except ValueError:
            continue
    return ''


def locate_geometry(record: dict, shape) -> tuple[float | None, float | None]:
    """Usa primero la geometría del SHP. Los campos Latitud/Longitud no son fiables."""
    if getattr(shape, 'points', None):
        lon, lat = shape.points[0]
        if -86 < lon < -82 and 8 < lat < 12:
            return lon, lat
    # Algunos archivos RANC recientes contienen campos intercambiados.
    lat, lon = record.get('Longitud'), record.get('Latitud')
    if lat is not None and lon is not None:
        try:
            lon, lat = float(lon), float(lat)
            if -86 < lon < -82 and 8 < lat < 12:
                return lon, lat
        except (ValueError, TypeError):
            pass
    return None, None


def ranc_rows(zip_path: Path, grupo: str) -> Iterable[dict[str, object]]:
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp)
        shp_files = list(Path(tmp).rglob('*.shp'))
        if len(shp_files) != 1:
            raise ValueError(f'Se esperaba un SHP en {zip_path}, encontrados {len(shp_files)}')
        reader = shapefile.Reader(str(shp_files[0]), encoding='utf-8')
        for shprec in reader.iterShapeRecords():
            src = shprec.record.as_dict()
            lon, lat = locate_geometry(src, shprec.shape)
            if lon is not None and lat is not None:
                este, norte = TRANSFORMER.transform(lon, lat)
            else:
                este, norte = None, None
            fg = parse_date(src.get('Fecha_Gene'))
            fr = parse_date(src.get('Fecha_Reso'))
            orden = str(src.get('Orden') or '').strip()
            municipio = ' / '.join(filter(None, [str(src.get('Provincia') or '').strip(),
                                               str(src.get('Cantón') or '').strip(),
                                               str(src.get('Distrito') or '').strip()]))
            row: dict[str, object] = {key: '' for key in HEADERS}
            row.update({
                'Oficina': str(src.get('Oficina') or '').strip(),
                'Tipo_Orden': 'Mantenimiento de Redes',
                'Nombre_Ord': str(src.get('Nombre_Ord') or '').strip(),
                'Orden': orden,
                'Fecha_Reso': fr,
                'Diametro': src.get('Diametro') or '',
                'Tipo_Tuber': str(src.get('Material') or '').strip(),
                'Fecha_Gene': fg,
                'Localizaci': municipio,
                # DATAMAR usa Longitud=X/Este y Latitud=Y/Norte en CRTM05.
                'Longitud': f'{este:.4f}' if este is not None else '',
                'Latitud': f'{norte:.4f}' if norte is not None else '',
                'Direccion': str(src.get('direccion') or '').strip(),
                # No inventar Codigo_Sis ni Estado/Responsabl que RANC no informa.
                'Fuente_datos': 'RANC',
                'ID_fuente': f'RANC:{orden}',
                'Grupo_RANC': grupo,
                'RANC_Material': str(src.get('Material') or '').strip(),
                'RANC_Provincia': str(src.get('Provincia') or '').strip(),
                'RANC_Canton': str(src.get('Cantón') or '').strip(),
                'RANC_Distrito': str(src.get('Distrito') or '').strip(),
                'RANC_Anio': src.get('Ano') or '',
                'RANC_Elev_med': str(src.get('Elev_med') or '').strip(),
                'Longitud_WGS84': f'{lon:.8f}' if lon is not None else '',
                'Latitud_WGS84': f'{lat:.8f}' if lat is not None else '',
                'Coordenada_valida': 'SI' if lon is not None else 'NO',
                'Fecha_Gene_original': str(src.get('Fecha_Gene') or '').strip(),
                'Fecha_Reso_original': str(src.get('Fecha_Reso') or '').strip(),
            })
            yield row


def copy_datamar(writer: csv.DictWriter, input_path: Path, stats: dict) -> None:
    with input_path.open('r', encoding='utf-8-sig', newline='') as source:
        reader = csv.DictReader(source)
        for row in reader:
            clean = {k: v for k, v in row.items() if k in DATAMAR_HEADERS}
            clean['Fuente_datos'] = 'DATAMAR'
            clean['ID_fuente'] = 'DATAMAR:' + str(row.get('OBJECTID *', '')).strip()
            writer.writerow(clean)
            stats['DATAMAR'] += 1


def process(acometidas: Path, redes: Path, datamar: Path | None, salida: Path) -> dict:
    salida.mkdir(parents=True, exist_ok=True)
    ranc_path = salida / 'Ordenes de Servicio RANC.csv'
    combinado_path = salida / 'Ordenes de Servicio GAM_Ampliado.csv'
    stats = Counter()
    anios: Counter = Counter()
    tipos: Counter = Counter()
    ids: set[str] = set()
    with ranc_path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=HEADERS, extrasaction='ignore')
        writer.writeheader()
        for file, group in [(acometidas, 'Acometidas 12-19 mm'), (redes, 'Redes >=25 mm')]:
            for row in ranc_rows(file, group):
                writer.writerow(row)
                stats['RANC_total'] += 1
                stats[group] += 1
                if row['Coordenada_valida'] != 'SI':
                    stats['RANC_sin_georreferencia'] += 1
                if row['ID_fuente'] in ids:
                    stats['RANC_orden_repetida'] += 1
                ids.add(str(row['ID_fuente']))
                anio = str(row['Fecha_Reso'])[-4:] if row['Fecha_Reso'] else 'Sin fecha'
                anios[anio] += 1
                tipos[str(row['Nombre_Ord'])] += 1
    if datamar is not None:
        with combinado_path.open('w', encoding='utf-8-sig', newline='') as out:
            writer = csv.DictWriter(out, fieldnames=HEADERS, extrasaction='ignore')
            writer.writeheader()
            copy_datamar(writer, datamar, stats)
            with ranc_path.open('r', encoding='utf-8-sig', newline='') as source:
                for row in csv.DictReader(source):
                    writer.writerow(row)
        stats['Consolidado_sin_deduplicar'] = stats['DATAMAR'] + stats['RANC_total']
    report = {
        'descripcion': 'RANC normalizado como referencia histórica; DATAMAR mantiene prioridad para evaluación actual.',
        'conteos': dict(stats),
        'RANC_anios_fecha_resolucion': dict(sorted(anios.items())),
        'RANC_tipo_orden': dict(tipos),
        'sistema_abastecimiento': 'Codigo_Sis en blanco para RANC por falta de asignación fiable.',
        'coordenadas': 'Campos DATAMAR Longitud/Latitud en CRTM05 EPSG:5367; *_WGS84 en EPSG:4326.',
        'duplicados': 'Solo se identifican órdenes duplicadas dentro de RANC; no se eliminan posibles coincidencias entre fuentes.',
        'advertencia': 'No usar la suma DATAMAR+RANC como frecuencia de fugas sin resolver solapamientos.',
        'archivos': [ranc_path.name] + ([combinado_path.name] if datamar else []),
    }
    (salida / 'Informe_calidad_RANC.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    return report


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--acometidas', type=Path, required=True)
    p.add_argument('--redes', type=Path, required=True)
    p.add_argument('--datamar', type=Path, help='CSV DATAMAR original (opcional).')
    p.add_argument('--salida', type=Path, default=Path('.'))
    args = p.parse_args()
    result = process(args.acometidas, args.redes, args.datamar, args.salida)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()