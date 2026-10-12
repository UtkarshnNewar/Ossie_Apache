"""Offline tests for the migration planners."""
import unittest
from pathlib import Path

import yaml

from ossie_hub.profile import nonunique_keys
from ossie_hub.targets import plan_databricks, plan_snowflake
from ossie_hub.targets.planner import make_names_unique, pick_fact

ROOT = Path(__file__).resolve().parents[2]
LIVE = ROOT / "output_models" / "live_sales_ossie.yaml"
RAW = Path("C:/Desktop_New/Projects/Data/Tables/Fabric_Ready")


def doc():
    return yaml.safe_load(LIVE.read_text(encoding="utf-8"))


def tiny():
    """Self-contained model (no external files) with a colliding key name."""
    def f(n, dt="String"):
        return {"name": n, "datatype": dt,
                "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": n}]}}
    return {"version": "0.2.0.dev0", "semantic_model": [{
        "name": "t", "datasets": [
            {"name": "dim", "source": "a.b.dim", "primary_key": ["k"], "fields": [f("k"), f("label")]},
            {"name": "fact", "source": "a.b.fact", "fields": [f("k"), f("amt", "Decimal")]}],
        "relationships": [{"name": "fact_to_dim", "from": "fact", "to": "dim",
                           "from_columns": ["k"], "to_columns": ["k"]}],
        "metrics": [{"name": "total", "datatype": "Decimal",
                     "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "SUM(fact.amt)"}]}}]}]}


class NamesTests(unittest.TestCase):
    def test_fact_keeps_names_dimension_is_renamed_columns_untouched(self):
        d = tiny()
        self.assertEqual(pick_fact(d), "fact")
        out, notes = make_names_unique(d)
        by = {x["name"]: x for x in out["semantic_model"][0]["datasets"]}
        self.assertEqual([f["name"] for f in by["fact"]["fields"]], ["k", "amt"])
        self.assertEqual([f["name"] for f in by["dim"]["fields"]], ["dim_k", "label"])
        # physical column and relationship / key references are unchanged
        self.assertEqual(by["dim"]["fields"][0]["expression"]["dialects"][0]["expression"], "k")
        self.assertEqual(out["semantic_model"][0]["relationships"][0]["to_columns"], ["k"])
        self.assertEqual(by["dim"]["primary_key"], ["k"])
        self.assertEqual(len(notes), 1)
        # input untouched (deep copy)
        self.assertEqual(d["semantic_model"][0]["datasets"][0]["fields"][0]["name"], "k")


class DatabricksPlanTests(unittest.TestCase):
    def test_plan_shape_and_metric_view(self):
        p = plan_databricks(tiny(), "main", "s")
        kinds = [s.kind for s in p.steps]
        self.assertEqual(kinds[0], "create_schema")
        self.assertEqual(kinds.count("create_table"), 2)
        self.assertEqual(kinds.count("copy_data"), 2)
        self.assertEqual(kinds[-1], "create_semantic_layer")
        mv = p.steps[-1].sql
        self.assertIn("WITH METRICS LANGUAGE YAML", mv)
        self.assertIn("using:", mv)          # join on real column, not a renamed one
        self.assertIn("dim_k", mv)           # dimension display name disambiguated
        self.assertIn("rely:", mv)           # key declared + not contradicted -> allowed
        ddl = next(s.sql for s in p.steps if s.detail.get("table") == "fact")
        self.assertIn("`amt` DECIMAL(38,10)", ddl)

    def test_unsafe_key_withholds_uniqueness_assertion(self):
        p = plan_databricks(tiny(), "main", "s", unsafe_keys={"dim": "dim.k is not unique"})
        self.assertNotIn("rely:", p.steps[-1].sql)
        self.assertTrue(any("DATA QUALITY" in n for n in p.notes))

    @unittest.skipUnless(LIVE.exists() and RAW.exists(), "live model / raw data not present")
    def test_real_model_flags_duplicate_keys(self):
        unsafe = nonunique_keys(doc(), RAW)
        self.assertEqual(sorted(unsafe), ["location", "products"])


class SnowflakePlanTests(unittest.TestCase):
    def test_plan_has_tables_stage_and_artifact(self):
        p = plan_snowflake(tiny(), "DB", "SC")
        kinds = [s.kind for s in p.steps]
        self.assertEqual(kinds.count("create_table"), 2)
        self.assertIn("create_stage", kinds)
        self.assertEqual(kinds[-1], "create_semantic_layer")
        self.assertEqual(len(p.artifacts), 1)
        ddl = next(s.sql for s in p.steps if s.detail.get("table") == "fact")
        self.assertIn("amt NUMBER(38,10)", ddl)


if __name__ == "__main__":
    unittest.main()
