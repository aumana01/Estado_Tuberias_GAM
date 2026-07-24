"""Entrada principal del aplicativo.

La implementación activa se encuentra en app_cache.py. Esta entrada aplica
parches livianos de rendimiento sin modificar el algoritmo técnico base.
"""

from pathlib import Path

import streamlit.components.v1 as components

_SOURCE_PATH = Path(__file__).with_name("app_cache.py")
_SOURCE = _SOURCE_PATH.read_text(encoding="utf-8")

# Corrección v1.3.2:
# La caché persistente del análisis completo se mantiene en modules/cache_manager.py.
# Sólo se evita cachear la función visual que recibe GeoDataFrames, porque Streamlit
# no puede generar un hash estable para esos objetos.
_SOURCE = _SOURCE.replace(
    "\n@st.cache_data(show_spinner=False)\ndef prepare_map_segments",
    "\ndef prepare_map_segments",
)

# Modo rápido de mapa por defecto. No altera los cálculos ni la tabla/Excel; sólo
# reduce lo que se envía al navegador para evitar mapas transparentes o lentos.
_SOURCE = _SOURCE.replace(
    'show_points = st.checkbox("Mostrar puntos asociados", True)',
    'show_points = st.checkbox("Mostrar puntos asociados", False, help="Para mayor rendimiento, el mapa de calor queda activo y los puntos individuales se muestran sólo si se requiere revisión puntual.")',
)
_SOURCE = _SOURCE.replace(
    'max_features_map = st.number_input("Máximo de elementos en mapa", 500, 50000, 10000, 500)',
    'max_features_map = st.number_input("Máximo de segmentos visibles en mapa", 500, 20000, 2500, 500, help="Sólo limita la capa visual del mapa. La tabla y Excel conservan el cálculo completo.")',
)
_SOURCE = _SOURCE.replace(
    'map_simplify_m = st.slider("Simplificación visual del mapa (m)", 0.0, 5.0, 1.0, 0.5, help="Sólo afecta la visualización; no altera el cálculo técnico.")',
    'map_simplify_m = st.slider("Simplificación visual del mapa (m)", 0.0, 10.0, 3.0, 0.5, help="Sólo afecta la visualización del mapa. El cálculo técnico no se modifica.")',
)
_SOURCE = _SOURCE.replace(
    'st_folium(fmap, width=None, height=700)',
    'components.html(fmap.get_root().render(), height=700, scrolling=False)',
)
_SOURCE = _SOURCE.replace(
    'st.caption("Los segmentos conservan popup con ID, sistema, material, diámetro, longitud, órdenes asociadas, indicador y método dominante de asociación.")',
    'st.caption("Mapa en modo rápido: muestra una capa visual resumida y mantiene popups en los segmentos visibles. La tabla y el Excel usan el cálculo completo, sin recorte visual.")',
)

exec(compile(_SOURCE, str(_SOURCE_PATH), "exec"), globals(), globals())
