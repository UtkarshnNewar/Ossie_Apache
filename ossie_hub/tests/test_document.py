import tempfile
import unittest
from pathlib import Path

import yaml

from ossie_hub import document, ontology, outputs
from ossie_hub.targets import plan_databricks

def _f(name, datatype):
    return {"name": name, "datatype": datatype, "expression": {"dialects": [
        {"dialect": "ANSI_SQL", "expression": name}]}}


MODEL = {
    "name": "shop",
    "datasets": [
        {"name": "orders", "source": "lh.s.orders", "fields": [
            _f("Row_ID", "Integer"), _f("Customer_ID", "String"), _f("Sales", "Decimal")]},
        {"name": "customers", "source": "lh.s.customers", "primary_key": ["Customer_ID"],
         "fields": [_f("Customer_ID", "String"), _f("Customer_Name", "String")]},
    ],
    "relationships": [{"name": "o_c", "from": "orders", "to": "customers",
                       "from_columns": ["Customer_ID"], "to_columns": ["Customer_ID"]}],
    "metrics": [{"name": "total_sales", "expression": {"dialects": [
        {"dialect": "ANSI_SQL", "expression": "SUM(orders.Sales)"}]}}],
}
WRAPPED = {"version": "0.2.0.dev0", "semantic_model": [MODEL]}
FLAT = {"version": "0.2.0.dev0", **MODEL}


class ShapeTests(unittest.TestCase):
    def test_wrap_and_flatten_are_inverse(self):
        self.assertEqual(document.wrap(FLAT), WRAPPED)
        self.assertEqual(document.flatten(WRAPPED), FLAT)
        self.assertEqual(document.wrap(WRAPPED), WRAPPED)
        self.assertEqual(document.flatten(FLAT), FLAT)

    def test_two_models_are_refused_not_silently_dropped(self):
        two = {"version": "x", "semantic_model": [MODEL, dict(MODEL, name="other")]}
        with self.assertRaises(ValueError):
            document.flatten(two)

    def test_load_reads_both_shapes(self):
        with tempfile.TemporaryDirectory() as d:
            for name, doc in (("w.yaml", WRAPPED), ("f.yaml", FLAT)):
                Path(d, name).write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
                self.assertEqual(document.load(Path(d, name)), WRAPPED)

    def test_dump_is_flat_with_header(self):
        text = document.dump(WRAPPED, "# header\n")
        self.assertTrue(text.startswith("# header\nversion:"))
        self.assertEqual(yaml.safe_load(text), FLAT)


class EdgeTests(unittest.TestCase):
    def test_written_files_are_flat_and_pass_the_repo_validator(self):
        with tempfile.TemporaryDirectory() as d:
            res = outputs.write_all(WRAPPED, Path(d) / "shop_ossie.yaml", "test", make_report=False)
            on_disk = yaml.safe_load(Path(res["ossie"]).read_text(encoding="utf-8"))
            self.assertIn("datasets", on_disk)
            self.assertNotIn("semantic_model", on_disk)
            self.assertTrue(res["ossie_valid"][0], res["ossie_valid"])
            self.assertTrue(res["ontology_valid"][0], res["ontology_valid"])

    def test_ontology_embeds_a_versioned_flat_model(self):
        onto = ontology.build(WRAPPED)
        self.assertEqual(ontology.build(FLAT), onto)   # same result from either shape
        embedded = onto["ontology_mappings"][0]["semantic_model"]
        self.assertIn("version", embedded)
        self.assertIn("datasets", embedded)

    def test_databricks_plan_is_the_same_from_either_shape(self):
        a = plan_databricks(WRAPPED, "c", "s", fact="orders")
        b = plan_databricks(document.wrap(FLAT), "c", "s", fact="orders")
        self.assertEqual([s.sql for s in a.steps], [s.sql for s in b.steps])
        self.assertEqual(a.artifacts, b.artifacts)
        self.assertTrue(a.artifacts, "a Metric View should have been produced")


if __name__ == "__main__":
    unittest.main()
