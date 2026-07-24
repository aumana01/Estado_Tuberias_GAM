# Estado_Tuberias_GAM

Aplicativo Streamlit para estimar el estado de tuberías de la GAM a partir del catastro de redes y órdenes de servicio/intervenciones.

## Versión

`v1.3.2`

## Salidas principales

1. **Mapa interactivo optimizado** con segmentos clasificados visibles, mapa de calor, puntos asociados opcionales y capas adicionales de traslape.
2. **Tabla resumen por sistema de abastecimiento**, con longitud estimada en estado Malo, Regular y Bueno por material, más exportación a Excel.

## Mejoras de rendimiento v1.3.2

Esta versión separa el **cálculo técnico completo** de la **visualización del mapa**:

- La tabla por sistema y el Excel usan todos los segmentos calculados.
- El mapa usa una capa visual liviana para evitar que el navegador renderice decenas de miles de geometrías o marcadores.
- Por defecto, el mapa muestra un máximo de `2.500` segmentos visibles priorizados por gravedad e indicador.
- Los puntos individuales se cargan apagados por defecto; el mapa de calor permanece activo.
- Los puntos visibles, si se activan, se muestran mediante `FastMarkerCluster` con límite visual.
- El mapa se renderiza como HTML estático para reducir la comunicación pesada entre Folium y Streamlit.
- Las geometrías se simplifican sólo para el mapa; el cálculo no se simplifica ni se recorta.

## Caché persistente

El aplicativo genera una firma técnica del análisis a partir de:

- catastro de tuberías cargado;
- órdenes/intervenciones filtradas;
- campos seleccionados;
- CRS de análisis;
- longitud máxima de segmento;
- radio de asociación;
- umbrales del semáforo;
- versión del aplicativo.

Si esos elementos no cambian, el botón **Usar análisis guardado / ejecutar** carga el resultado desde la carpeta local `.cache_estado_tuberias/` sin repetir segmentación, reproyección, asociación por diámetro/cercanía ni estimación de estados.

## Botones de rendimiento

- **Usar análisis guardado / ejecutar**: carga la caché compatible si existe; si no existe, ejecuta el análisis y lo guarda.
- **Recalcular análisis**: ignora la caché y actualiza el resultado guardado.
- **Borrar caché guardada**: elimina resultados calculados localmente.

## Algoritmo de asociación

La orden/intervención se asocia a la tubería con esta prioridad:

1. mismo sistema + mismo diámetro normalizado;
2. mismo diámetro normalizado;
3. tubería más cercana dentro del radio configurado.

Para órdenes con diámetros pequeños se prueban equivalencias nominales comunes en pulgadas, por ejemplo: 2≈50 mm, 3≈75 mm, 4≈100 mm, 6≈150 mm, 8≈200 mm, 10≈250 mm y 12≈300 mm.

## Parámetros por defecto

- Longitud máxima de segmento: `100 m`
- Radio de asociación espacial: `10 m`
- Bueno / verde: `≤ 3 órdenes / 100 m`
- Regular / amarillo: valores intermedios
- Malo / rojo: `≥ 7 órdenes / 100 m`
- Máximo de segmentos visibles en mapa: `2.500`
- Simplificación visual del mapa: `3 m`
- Puntos individuales: apagados por defecto
- Mapa de calor: activo por defecto

## Datos locales

Los archivos pesados no se suben al repositorio. Para usar archivos de ejemplo locales, coloque en `data/`:

```text
JSON_catastro.json
Ordenes de Servicio GAM.csv
```

Si esos archivos no existen, el aplicativo desactiva la opción de ejemplo y solicita cargar manualmente la capa de tuberías y el archivo de órdenes.

## Instalación local

```bash
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m streamlit run app.py
```

En Windows también puede usar `ejecutar_app.bat`.

## Archivos excluidos

El `.gitignore` excluye datos, salidas y caché local:

```gitignore
data/*.csv
data/*.json
outputs/
.cache_estado_tuberias/
```
