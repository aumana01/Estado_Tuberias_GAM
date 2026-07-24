# Aplicativo de estado estimado de tuberías por órdenes de servicio

Versión: **v1.2.1**

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

## Datos de ejemplo incluidos

La carpeta `data/` incluye:

- `JSON_catastro.json`
- `Ordenes de Servicio GAM.csv`

Estos archivos quedan activados por defecto mediante la opción “Usar archivos cargados de ejemplo”.

## Notas metodológicas

El resultado es una estimación para priorización y debe complementarse con criterio operativo, antigüedad, criticidad, inspección de campo, condición hidráulica, continuidad del servicio y disponibilidad presupuestaria.
