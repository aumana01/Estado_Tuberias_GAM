"""Regresiones del mapa: estados, conteos y renderizado ligero."""

import unittest

import folium
import geopandas as gpd
from folium.plugins import FastMarkerCluster, HeatMap
from shapely.geometry import LineString, Point

from modules.mapping import _center_from_layers, _select_map_segments, build_map


class MapPerformanceTests(unittest.TestCase):
    @staticmethod
    def pipes(n_red=40, n_yellow=20, n_green=40):
        labels = ["Rojo"] * n_red + ["Amarillo"] * n_yellow + ["Verde"] * n_green
        return gpd.GeoDataFrame(
            {
                "segmento_id": [f"S_{i}" for i in range(len(labels))],
                "clasificacion": labels,
                "estado_estimado": [
                    {"Rojo": "Malo", "Amarillo": "Regular", "Verde": "Bueno"}[label]
                    for label in labels
                ],
                "cantidad_intervenciones": [i % 8 for i in range(len(labels))],
                "indicador_100m": [float(i % 8) for i in range(len(labels))],
                "dato_pesado": ["No debe viajar al mapa"] * len(labels),
            },
            geometry=[
                LineString([(-84.10 + i * 0.00002, 9.90), (-84.10 + i * 0.00002, 9.901)])
                for i in range(len(labels))
            ],
            crs="EPSG:4326",
        )

    def test_muestreo_que_mantiene_estados_y_no_altera_analisis(self):
        pipes = self.pipes()
        sample = _select_map_segments(pipes, 15)
        self.assertEqual(len(sample), 15)
        self.assertEqual(set(sample["clasificacion"]), {"Rojo", "Amarillo", "Verde"})
        self.assertEqual(len(pipes), 100)
        self.assertEqual(int(pipes["cantidad_intervenciones"].sum()), sum(i % 8 for i in range(100)))
        self.assertEqual(sample["segmento_id"].tolist(), _select_map_segments(pipes, 15)["segmento_id"].tolist())

    def test_mapa_no_envia_atributos_ajenos_al_popup(self):
        pipes = self.pipes(2, 1, 2)
        points = gpd.GeoDataFrame(
            {"Nombre_Ord": ["Fuga A", "Fuga B"]},
            geometry=[Point(-84.100, 9.900), Point(-84.099, 9.901)],
            crs="EPSG:4326",
        )
        fmap = build_map(pipes, points, max_features_map=10, show_points=True, show_heatmap=True)
        geojson_layers = [
            obj for obj in fmap._children.values()
            if isinstance(obj, folium.GeoJson) and obj.layer_name == "Segmentos clasificados"
        ]
        self.assertEqual(len(geojson_layers), 1)
        features = geojson_layers[0].data["features"]
        self.assertEqual(len(features), len(pipes))
        self.assertEqual(features[0]["properties"]["cantidad_intervenciones"], 0)
        self.assertIn("clasificacion", features[0]["properties"])
        self.assertNotIn("dato_pesado", features[0]["properties"])
        self.assertTrue(any(isinstance(o, FastMarkerCluster) for o in fmap._children.values()))
        self.assertTrue(any(isinstance(o, HeatMap) for o in fmap._children.values()))
        self.assertIn("Fuga A", fmap.get_root().render())

    def test_centro_usa_limites_sin_necesitar_union(self):
        center = _center_from_layers(self.pipes(1, 0, 0))
        self.assertAlmostEqual(center[0], 9.9005)
        self.assertAlmostEqual(center[1], -84.1)

    def test_capacidad_de_mapa_no_altera_mayor_numero_de_fugas(self):
        pipes = self.pipes(40, 20, 40)
        fmap = build_map(pipes, None, max_features_map=12)
        layer = next(x for x in fmap._children.values() if isinstance(x, folium.GeoJson))
        self.assertEqual(len(layer.data["features"]), 12)
        self.assertEqual(len(pipes), 100)
        self.assertTrue({"Rojo", "Amarillo", "Verde"}.issubset(
            {f["properties"]["clasificacion"] for f in layer.data["features"]}
        ))


if __name__ == "__main__":
    unittest.main()
