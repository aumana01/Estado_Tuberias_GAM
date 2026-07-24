"""Entrada principal del aplicativo.

La implementación activa se encuentra en app_cache.py. En Streamlit Cloud/Python 3.14
se evita aplicar @st.cache_data sobre GeoDataFrames del mapa, porque esos objetos
no son hashables de forma confiable y pueden producir UnhashableParamError.
"""

from pathlib import Path

_SOURCE_PATH = Path(__file__).with_name("app_cache.py")
_SOURCE = _SOURCE_PATH.read_text(encoding="utf-8")

# Corrección v1.3.1:
# La caché persistente del análisis completo se mantiene en modules/cache_manager.py.
# Sólo se elimina el decorador de la función visual prepare_map_segments, porque recibe
# un GeoDataFrame filtrado y Streamlit no puede generar un hash estable para ese objeto.
_SOURCE = _SOURCE.replace(
    "\n@st.cache_data(show_spinner=False)\ndef prepare_map_segments",
    "\ndef prepare_map_segments",
)

exec(compile(_SOURCE, str(_SOURCE_PATH), "exec"), globals(), globals())
