"""Pruebas de integración de órdenes sin dependencia de Streamlit."""
import unittest

import pandas as pd

from modules.order_sources import merge_sources, normalize_source, source_counts


class OrderSourcesTests(unittest.TestCase):
    def test_datamar_gets_traceability_without_changing_original(self):
        original = pd.DataFrame({
            "OBJECTID *": [123],
            "Orden": ["D-123"],
            "Longitud": [490000],
            "Latitud": [1095000],
        })
        result = normalize_source(original, "DATAMAR")
        self.assertEqual(result.loc[0, "Fuente_datos"], "DATAMAR")
        self.assertEqual(result.loc[0, "ID_fuente"], "DATAMAR:D-123")
        self.assertNotIn("Fuente_datos", original.columns)
        self.assertEqual(result.loc[0, "Longitud"], 490000)

    def test_ranc_empty_coordinates_and_system_remain_empty(self):
        original = pd.DataFrame({
            "Orden": ["R-9"],
            "Codigo_Sis": [""],
            "Latitud": [""],
            "Longitud": [""],
            "Fuente_datos": ["RANC"],
            "ID_fuente": ["RANC:R-9"],
            "Grupo_RANC": ["Redes >=25 mm"],
        })
        result = normalize_source(original, "RANC")
        self.assertEqual(result.loc[0, "Codigo_Sis"], "")
        self.assertEqual(result.loc[0, "Longitud"], "")
        self.assertEqual(result.loc[0, "ID_fuente"], "RANC:R-9")

    def test_exploratory_merge_keeps_both_sources(self):
        datamar = pd.DataFrame({"Orden": ["1"], "Nombre_Ord": ["Fuga"]})
        ranc = pd.DataFrame({"Orden": ["1"], "Nombre_Ord": ["Fuga"]})
        result = merge_sources(datamar, ranc)
        self.assertEqual(len(result), 2)
        self.assertEqual(source_counts(result), {"DATAMAR": 1, "RANC": 1})

    def test_already_combined_source_tags_are_preserved(self):
        combined = pd.DataFrame({
            "Orden": ["A", "B"],
            "Fuente_datos": ["DATAMAR", "RANC"],
            "ID_fuente": ["DATAMAR:A", "RANC:B"],
        })
        result = merge_sources(combined)
        self.assertEqual(source_counts(result), {"DATAMAR": 1, "RANC": 1})
        self.assertEqual(result.loc[1, "ID_fuente"], "RANC:B")

    def test_exported_index_column_is_discarded(self):
        ranc = pd.DataFrame({"index": [0], "Orden": ["1"], "Unnamed: 0": [0]})
        result = normalize_source(ranc, "RANC")
        self.assertNotIn("index", result)
        self.assertNotIn("Unnamed: 0", result)


if __name__ == "__main__":
    unittest.main()
