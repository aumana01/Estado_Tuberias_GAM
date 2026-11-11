"""Asegurar que la optimización del resumen conserva los estados y longitudes."""

import unittest

import geopandas as gpd
from shapely.geometry import LineString

from modules.analysis import build_condition_tables, condition_long_form


class ConditionTablePerformanceTests(unittest.TestCase):
    @staticmethod
    def results():
        return gpd.GeoDataFrame(
            {
                "CODSISTEMA": ["S1", "S1", "S2", "S2"],
                "CODMATERIA": ["PVC", "PVC", "HD", "AC"],
                "material_resumen": [
                    "PVC- Policloruro de vinilo",
                    "PVC- Policloruro de vinilo",
                    "HD - Hierro dúctil",
                    "AC - Asbesto cemento",
                ],
                "material_malo_forzado": [False, False, False, True],
                "cantidad_intervenciones": [2, 0, 0, 3],
                "estado_estimado": ["Rojo", "Bueno", "Bueno", "Malo"],
                "longitud_segmento_m": [100.0, 50.0, 200.0, 25.0],
                "atributo_innecesario": ["X" * 1000] * 4,
            },
            geometry=[
                LineString([(0, float(i)), (1, float(i))])
                for i in range(4)
            ],
            crs="EPSG:5367",
        )

    def test_asignacion_por_ordenes_y_material_no_cambia(self):
        result = self.results()
        result.loc[0, "estado_estimado"] = "Malo"
        long_df = condition_long_form(result, system_col="CODSISTEMA", material_col="CODMATERIA")
        self.assertEqual(len(long_df), 8)
        self.assertAlmostEqual(long_df["longitud_m"].sum(), 375.0)
        forced = long_df[long_df["origen_estimacion"] == "Material malo forzado"]
        self.assertEqual(forced["estado"].tolist(), ["Malo"])
        self.assertEqual(forced["longitud_m"].tolist(), [25.0])
        s1_base = long_df[(long_df["sistema"] == "S1") & (long_df["origen_estimacion"] == "Supuesto base por material")]
        self.assertEqual(dict(zip(s1_base["estado"], s1_base["longitud_m"])), {
            "Malo": 10.0, "Regular": 25.0, "Bueno": 15.0
        })
        self.assertNotIn("atributo_innecesario", long_df.columns)

    def test_tablas_mantienen_longitud_total(self):
        result = self.results()
        lengths, percentages, long_df = build_condition_tables(
            result, system_col="CODSISTEMA", material_col="CODMATERIA"
        )
        total = lengths[lengths["Estado"] == "Total"]["TOTAL"].iloc[0]
        self.assertAlmostEqual(total, 375.0)
        self.assertAlmostEqual(long_df["longitud_m"].sum(), 375.0)


if __name__ == "__main__":
    unittest.main()
