# Integración del histórico de fugas RANC — GAM

## Fuentes y alcance

- **DATAMAR (principal):** `data/Ordenes de Servicio GAM.csv`; el archivo original permanece intacto.
- **RANC (histórico complementario):** `data/Ordenes de Servicio RANC.csv`, generado a partir de los dos shapefiles ZIP de acometidas 12–19 mm y redes >=25 mm.
- La opción predeterminada del aplicativo continúa siendo **DATAMAR**. Cuando se encuentra el archivo RANC en `data/`, el aplicativo ofrece analizar DATAMAR, RANC o ambas fuentes. La opción combinada se marca expresamente **sin deduplicación cruzada**.

## Resumen de los datos de RANC (archivos analizados el 10/11/2026)

| Archivo de origen | Registros |
| --- | ---: |
| Acometidas 12–19 mm | 9.751 |
| Redes >=25 mm | 25.256 |
| **Total RANC** | **35.007** |
| Sin georreferencia válida | 134 |
| Georreferenciados | 34.873 |

Fecha de resolución: 2018 (452), 2019 (2.851), 2020 (2.637), 2021 (3.884), 2022 (4.353), 2023 (9.888), 2024 (10.808), 2025 (134). Pese al nombre de los ZIP, algunos registros fueron resueltos en enero de 2025.

## Instrucciones de instalación

1. Descargar el archivo normalizado **`Ordenes de Servicio RANC.csv`** que acompaña el análisis.
2. Agregarlo al repositorio en la ruta exacta `data/Ordenes de Servicio RANC.csv` en la rama de integración (o en la rama principal cuando se incorpore la funcionalidad).
3. Abrir el aplicativo; activar «Usar archivos cargados de ejemplo» y seleccionar «RANC (histórico de referencia)» o «DATAMAR + RANC (sin deduplicación cruzada)».
4. Mantener `DATAMAR (principal)` para la comparación o evaluación que no incorpore datos históricos externos.

El CSV normalizado usa los **33 campos originales de DATAMAR** y agrega `Fuente_datos`, `ID_fuente`, `Grupo_RANC`, localización administrativa original y coordenadas WGS84. En compatibilidad con el CSV DATAMAR, `Longitud` representa **Este CRTM05 / EPSG:5367** y `Latitud` **Norte CRTM05 / EPSG:5367**. Los campos `Longitud_WGS84` y `Latitud_WGS84` representan las coordenadas geográficas verdaderas.

Las fechas seriales de Excel RANC se convierten a `DD/MM/AAAA`. Las geometrías del shapefile son la fuente preferente para obtener posiciones porque sus atributos `Longitud` y `Latitud` aparecen invertidos, incompletos o en cero en distintas épocas. Las órdenes sin ubicación permanecen en el archivo, con `Coordenada_valida=NO`, y son omitidas al construir la capa de puntos.

## Advertencias de calidad y metodología

- RANC **no proporciona `Codigo_Sis`** fiable. No se asigna ficticiamente ningún sistema de abastecimiento; el aplicativo intentará asociar cada punto geográfico con el catastro existente dentro del radio configurado.
- Los datos de ambas fuentes **pueden representar una misma intervención**; no se recomienda interpretarlos como órdenes únicas ni contabilizarlos juntos sin una validación espacial, temporal y de ID.
- En el CSV DATAMAR existen órdenes con numeración repetida, por lo que **`Orden` por sí solo no es clave fiable** para eliminar duplicados.
- RANC informa materiales y diámetros asociados a la orden, pero estos datos pueden diferir del catastro de la tubería más cercana. El aplicativo utiliza criterios de cercanía y diámetro.
- Las fechas históricas y la calidad de georreferenciación pueden variar entre períodos; se recomienda informar la cobertura de cada fuente al comparar tasas anuales.
- La clasificación del estado de la tubería debe contrastarse con inspección, condición hidráulica y criticidad.

## Reversibilidad

Ningún archivo DATAMAR se reemplaza. Al retirar `data/Ordenes de Servicio RANC.csv`, la aplicación vuelve a trabajar exclusivamente con el archivo DATAMAR original, sin depender de los archivos históricos adicionales.
