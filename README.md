# Aplicativo de estado estimado de tuberías por órdenes de servicio

Versión: **v1.2.5**

Aplicación en Python + Streamlit para estimar el estado de tuberías a partir de:

- Catastro de redes de tubería en JSON, GeoJSON, SHP ZIP, KML o KMZ.
- Órdenes de servicio/intervenciones en CSV o Excel.

La versión v1.2.1 simplifica el aplicativo para concentrarse en dos salidas principales:

1. **Mapa interactivo** con mapa de calor, puntos de órdenes y segmentos de tubería asociados.
2. **Tabla resumen por sistema de abastecimiento**, con longitud estimada por estado y material.
3. **Exportación a Excel**, con una pestaña por sistema cuando se analiza más de un sistema.

## Ejecución rápida en Windows

1. Descomprima el ZIP.
2. Abra la carpeta `app_sustitucion_tuberias`.
3. Ejecute `ejecutar_app.bat`.

El archivo `.bat` crea un entorno virtual `.venv`, instala dependencias y ejecuta:

```bash
python -m streamlit run app.py
```

## Algoritmo de asociación de órdenes

Cada orden se asocia a un segmento de tubería siguiendo esta prioridad:

1. Misma combinación de **sistema + diámetro** dentro del radio configurado.
2. Mismo **diámetro** dentro del radio configurado.
3. **Tubería más cercana** dentro del radio configurado.

Configuración por defecto:

- Longitud máxima de segmento: **100 m**.
- Radio de asociación espacial: **10 m**.
- Bueno / Verde: hasta **3** órdenes / 100 m.
- Regular / Amarillo: entre el umbral verde y antes del umbral rojo.
- Malo / Rojo: desde **7** órdenes / 100 m.

Para evitar una cuarta categoría, los valores entre el umbral amarillo de referencia y el umbral rojo se mantienen como **Regular / Amarillo**. Si se desea que el rojo inicie en 5, ajuste el parámetro “Malo / rojo desde” a 5.

## Tratamiento de diámetros

El algoritmo compara el diámetro de la orden con el diámetro del catastro.

Adicionalmente, para órdenes con valores pequeños, prueba equivalencias nominales comunes en pulgadas:

- 2 ≈ 50 mm
- 3 ≈ 75 mm
- 4 ≈ 100 mm
- 6 ≈ 150 mm
- 8 ≈ 200 mm
- 10 ≈ 250 mm
- 12 ≈ 300 mm

Si no hay coincidencia de diámetro dentro del radio, se usa la tubería más cercana.

## Supuestos de estado por material

Los materiales **AC / asbesto-cemento**, **latón** y **otro** se consideran por defecto **100 % en estado Malo**.

Para los segmentos sin órdenes asociadas se aplica una distribución base por material. Cuando un segmento tiene órdenes asociadas, el estado se depura con el indicador de órdenes por cada 100 m.

Distribución base incluida:

| Material | Malo | Regular | Bueno |
|---|---:|---:|---:|
| AC - Asbesto cemento | 100% | 0% | 0% |
| Desconocido | 0% | 85% | 15% |
| HD - Hierro dúctil | 5% | 70% | 25% |
| HF - Hierro fundido | 5% | 70% | 25% |
| HG - Hierro galvanizado | 5% | 70% | 25% |
| LATON | 100% | 0% | 0% |
| Otro | 100% | 0% | 0% |
| PEAD - Polietileno de alta densidad | 10% | 40% | 50% |
| Polietileno reticulado (PEX) | 0% | 0% | 100% |
| PVC- Policloruro de vinilo | 20% | 50% | 30% |

## Exportación a Excel

En la pestaña **Tabla por sistema** se incluye el botón **Descargar reporte Excel**.

- Si se visualiza **Todos los sistemas**, el archivo Excel crea una hoja de resumen y una pestaña independiente por cada sistema de abastecimiento.
- Si se selecciona un sistema específico, el Excel exporta únicamente ese sistema.
- Cada pestaña incluye la tabla de longitud por estado/material, la tabla porcentual por material, los supuestos metodológicos y los porcentajes base utilizados.
- El mapa se mantiene como componente interactivo dentro del aplicativo; no se inserta imagen del mapa en Excel para evitar dependencia de capturas externas.


## Rendimiento del mapa (v1.2.5)

- **Desplazamiento y zoom sin recálculo:** los movimientos del mapa no devuelven las coordenadas ni los límites al servidor, por lo que no vuelven a ejecutar el análisis de Streamlit.
- **Canvas + marcadores rápidos:** las tuberías se dibujan con Canvas y los puntos con FastMarkerCluster, evitando miles de objetos Python/JavaScript independientes.
- **GeoJSON reducido:** el navegador recibe solamente los atributos necesarios para el popup/tooltip (ID, sistema, material, diámetro, longitud, cantidad de órdenes, indicador y clasificación). No se recorta el GeoDataFrame utilizado para el análisis.
- **Límite sólo visual:** inicialmente se dibujan hasta 5.000 elementos por capa (ajustable a 50.000). Cuando hay más segmentos, se seleccionan de manera reproducible, reservando representación de los estados Rojo, Amarillo y Verde. **No se pierden segmentos ni órdenes del cálculo, de las tablas ni de la exportación Excel/SHP.**
- **Capas adicionales:** los polígonos SHP, GeoJSON, KML y otros traslapes se siguen dibujando con sus atributos; si la capa excede el límite visual sólo se muestran las primeras geometrías.
- **Exportación Excel bajo demanda:** presione primero *Preparar Excel* y después *Descargar reporte Excel*. Se conserva en sesión hasta cambiar de análisis o de sistema, para evitar reconstruir el libro en cada recarga.

El mapa de calor y los marcadores de órdenes respetan el límite de puntos visible; los conteos y el estado por segmento se calculan sobre todas las órdenes filtradas. Si necesita verificar cada geometría, seleccione un sistema de abastecimiento para reducir la carga del navegador o aumente el límite visual.

## Integración de históricos RANC (v1.2.4)

Se incorporó una **integración por fuente** para complementar las órdenes DATAMAR con
fugas históricas del Programa RANC. **El CSV original DATAMAR permanece intacto**.
El histórico RANC es una referencia adicional, no una sustitución de la serie DATAMAR.

### Preparación de los históricos

Se utiliza el script `scripts/integrar_fugas_ranc.py` para normalizar los dos
archivos geográficos (ZIP con SHP y atributos), correspondientes a:

- `FugasGAM_Mant_Redes_12-19mm_nov2018-dic2024.zip`
- `FugasGAM_Mant_Redes_+25mm_nov2018-dic2024.zip`

Ejecutar en la raíz del repositorio, después de instalar `requirements.txt`:

```bash
python scripts/integrar_fugas_ranc.py \
  --acometidas "FugasGAM_Mant_Redes_12-19mm_nov2018-dic2024.zip" \
  --redes "FugasGAM_Mant_Redes_+25mm_nov2018-dic2024.zip" \
  --salida data
```

Genera `data/Ordenes de Servicio RANC.csv` e
`data/Informe_calidad_RANC.json`. Si se indica además
`--datamar "data/Ordenes de Servicio GAM.csv"`, también genera
`data/Ordenes de Servicio GAM_Ampliado.csv` como artefacto derivado, sin
modificar la fuente DATAMAR. El CSV combinado no se utiliza automáticamente
para evitar cargar los mismos registros dos veces.

### Uso en el aplicativo

En **Histórico RANC normalizado** se puede subir el CSV generado.
Alternativamente, si `data/Ordenes de Servicio RANC.csv` existe en el
repositorio, se detecta automáticamente. En **Procedencia de órdenes** se
selecciona uno de los modos disponibles:

1. **DATAMAR (base)**: análisis oficial de referencia; valor predeterminado.
2. **RANC (histórico)**: análisis de registros RANC de manera independiente.
3. **DATAMAR + RANC (exploratorio)**: visualización conjunta, con advertencia
   de posibles solapamientos.

Se conservan `Fuente_datos` e `ID_fuente` para la trazabilidad.
Las columnas de DATAMAR se preservan, con campos extra para el histórico RANC.
Las coordenadas RANC se toman de la geometría geográfica de origen y se
transforman de WGS84 a CRTM05 (EPSG:5367); faltantes permanecen vacíos.
**No se inventa `Codigo_Sis`** para RANC y no se eliminan coincidencias
entre DATAMAR y RANC sin una conciliación verificable.

**Advertencia metodológica:** una fuga puede aparecer en ambas fuentes.
El modo combinado no debe interpretarse como número de eventos únicos ni
usarse por sí solo para cambiar umbrales o priorizar inversiones. Conviene
conciliar eventos por orden, fecha, ubicación y descripción, y validar
espacialmente los resultados antes de conclusiones definitivas.

## Datos de ejemplo incluidos

La carpeta `data/` incluye:

- `JSON_catastro.json`
- `Ordenes de Servicio GAM.csv`

Estos archivos quedan activados por defecto mediante la opción “Usar archivos cargados de ejemplo”.

## Notas metodológicas

El resultado es una estimación para priorización y debe complementarse con criterio operativo, antigüedad, criticidad, inspección de campo, condición hidráulica, continuidad del servicio y disponibilidad presupuestaria.
