"""Regresiones del cargador ZIP-SHP y la visualización de capas adicionales."""

import tempfile
import unittest
import zipfile
from pathlib import Path

import folium
import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon

from modules.data_loader import read_vector_file, validate_line_layer
from modules.mapping import _add_overlay_layer


class ShpZipOverlayTests(unittest.TestCase):
    @staticmethod
    def polygon_layer() -> gpd.GeoDataFrame:
        # Equivalente mínimo del SHP Propuesta Sust RANC:
        # una geometría de polígono Z y campos de fecha de una geodatabase.
        return gpd.GeoDataFrame(
            {
                "Proyecto": ["Paso Ancho"],
                "CreationDa": [pd.Timestamp("2025-05-28")],
                "EditDate": [pd.Timestamp("2025-05-30")],
            },
            geometry=[
                Polygon(
                    [
                        (-84.10, 9.90, 0),
                        (-84.09, 9.90, 0),
                        (-84.09, 9.91, 0),
                        (-84.10, 9.90, 0),
                    ]
                )
            ],
            crs="EPSG:4326",
        )

    def test_zip_with_nested_shapefile_can_be_read(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            nested = root / "Propuesta Sust RANC"
            nested.mkdir()
            shp_path = nested / "Sust_Tub_RANC.shp"
            self.polygon_layer().to_file(shp_path, driver="ESRI Shapefile", index=False)

            zip_path = root / "Propuesta Sust RANC.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                for component in nested.iterdir():
                    zf.write(component, arcname=f"{nested.name}/{component.name}")

            loaded = read_vector_file(zip_path)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded.geometry.geom_type.iloc[0], "Polygon")
            self.assertIsNotNone(loaded.crs)

    def test_polygon_with_timestamp_fields_is_shown_on_map(self):
        fmap = folium.Map(location=[9.9, -84.1], zoom_start=12)
        _add_overlay_layer(fmap, self.polygon_layer(), "Propuesta RANC", 100)
        folium.LayerControl().add_to(fmap)
        html = fmap.get_root().render()

        self.assertIn("Paso Ancho", html)
        self.assertIn("2025-05-28", html)
        self.assertIn("Propuesta RANC", html)

    def test_polygon_cannot_replace_primary_pipe_layer(self):
        with self.assertRaisesRegex(ValueError, "traslape visual"):
            validate_line_layer(self.polygon_layer())


if __name__ == "__main__":
    unittest.main()
