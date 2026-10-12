import unittest

import yaml

from ossie_hub import ontology, ontology_push as op
from ossie_hub.connectors.base import ConnectorError

OSSIE = {
    "version": "0.2.0.dev0",
    "semantic_model": [{
        "name": "shop",
        "datasets": [
            {"name": "orders", "source": "lh.s.orders", "fields": [
                {"name": "Row_ID", "datatype": "Integer"},
                {"name": "Customer_ID", "datatype": "String"},
                {"name": "Sales", "datatype": "Decimal"}]},
            {"name": "customers", "source": "lh.s.customers", "primary_key": ["Customer_ID"],
             "fields": [{"name": "Customer_ID", "datatype": "String"},
                        {"name": "Customer_Name", "datatype": "String"}]},
        ],
        "relationships": [{"name": "o_c", "from": "orders", "to": "customers",
                           "from_columns": ["Customer_ID"], "to_columns": ["Customer_ID"]}],
    }],
}


class FakeConn:
    def __init__(self, existing=None):
        self.existing = existing or {}   # table -> comment
        self.sql: list[str] = []
        self.counts: dict[str, int] = {}

    def execute(self, sql, timeout_s=300):
        self.sql.append(sql)
        if "information_schema.tables" in sql and "comment" in sql:
            return {"rows": [[t, c] for t, c in self.existing.items()]}
        if "information_schema.tables" in sql:
            return {"rows": [[t] for t in self.existing] + [["orders"], ["customers"]]}
        if sql.startswith("INSERT INTO"):
            table = sql.split("`.`")[2].split("`")[0]
            self.counts[table] = self.counts.get(table, 0) + sql.count("),(") + 1
        if sql.startswith("TRUNCATE"):
            self.counts[sql.split("`.`")[2].strip("`")] = 0
        if sql.startswith("SELECT COUNT"):
            table = sql.split("`.`")[2].strip("`")
            return {"rows": [[str(self.counts.get(table, 0))]]}
        return {"rows": []}


def doc():
    return yaml.safe_load(ontology.render(OSSIE, "shop.yaml"))


class FlattenTests(unittest.TestCase):
    def test_tables_and_links(self):
        rows = op.rows_from_ontology(doc())
        self.assertEqual(set(rows), set(op.TABLES))
        ents = {r["concept"]: r for r in rows["ontology_concepts"] if r["concept_type"] == "EntityType"}
        self.assertEqual(ents["Customer"]["dataset"], "customers")
        self.assertTrue(ents["Customer"]["identified_by"])
        links = [r for r in rows["ontology_relationships"] if r["relationship_kind"] == "link"]
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["multiplicity"], "ManyToOne")
        mapped = [m for m in rows["ontology_mappings"] if m["kind"] == "link"]
        self.assertEqual((mapped[0]["dataset"], mapped[0]["column_name"]), ("orders", "Customer_ID"))

    def test_every_row_has_every_column(self):
        for t, rows in op.rows_from_ontology(doc()).items():
            for r in rows:
                self.assertEqual(set(r), set(op.TABLES[t]), t)


def other_model():
    """A model with none of the sales vocabulary: two facts, no declared keys, odd names."""
    return {"version": "0.2.0.dev0", "semantic_model": [{
        "name": "hr",
        "datasets": [
            {"name": "Shifts", "source": "x", "fields": [
                {"name": "Shift_Code"}, {"name": "Emp_Key"}, {"name": "Hours", "datatype": "Decimal"}]},
            {"name": "Leaves", "source": "x", "fields": [
                {"name": "Leave_No"}, {"name": "Emp_Key"}, {"name": "Days", "datatype": "Integer"}]},
            {"name": "Employee", "source": "x", "primary_key": ["Emp_Key"], "fields": [
                {"name": "Emp_Key"}, {"name": "Full_Name"}]},
            {"name": "Misc", "source": "x", "fields": [{"name": "Remark"}, {"name": "Weight"}]},
        ],
        "relationships": [
            {"name": "s_e", "from": "Shifts", "to": "Employee",
             "from_columns": ["Emp_Key"], "to_columns": ["Emp_Key"]},
            {"name": "l_e", "from": "Leaves", "to": "Employee",
             "from_columns": ["Emp_Key"], "to_columns": ["Emp_Key"]}]}]}


class OtherModelTests(unittest.TestCase):
    def setUp(self):
        self.onto = yaml.safe_load(ontology.render(other_model(), "hr.yaml"))

    def test_nothing_is_dropped(self):
        self.assertEqual(ontology.problems(self.onto), [])

    def test_every_entity_has_identifier_and_mapping(self):
        ents = [c for c in self.onto["ontology"] if c["type"] == "EntityType"]
        self.assertEqual({e["concept"] for e in ents}, {"Shift", "Leave", "Employee", "Misc"})
        self.assertTrue(all(e.get("identify_by") for e in ents))

    def test_both_facts_link_to_the_dimension(self):
        rows = op.rows_from_ontology(self.onto)
        links = {(r["concept"], r["related_concept"]) for r in rows["ontology_relationships"]
                 if r["relationship_kind"] == "link"}
        self.assertEqual(links, {("Shift", "Employee"), ("Leave", "Employee")})
        mapped = {(m["concept"], m["column_name"]) for m in rows["ontology_mappings"]
                  if m["kind"] == "link"}
        self.assertEqual(mapped, {("Shift", "Emp_Key"), ("Leave", "Emp_Key")})

    def test_incomplete_ontology_is_refused(self):
        broken = yaml.safe_load(ontology.render(other_model(), "hr.yaml"))
        broken["ontology_mappings"][0]["concept_mappings"].pop(0)
        c = FakeConn()
        with self.assertRaises(ConnectorError):
            op.run(broken, c, "cat", "sch", execute=True, log=lambda s: None)
        self.assertEqual(c.sql, [])


class PushTests(unittest.TestCase):
    def test_dry_run_writes_nothing(self):
        c = FakeConn()
        res = op.run(doc(), c, "cat", "sch", execute=False, log=lambda s: None)
        self.assertFalse(res["executed"])
        self.assertTrue(all(s.upper().startswith("SELECT") for s in c.sql), c.sql)

    def test_execute_creates_schema_tables_rows_and_annotations(self):
        c = FakeConn()
        res = op.run(doc(), c, "cat", "sch", execute=True, log=lambda s: None)
        joined = "\n".join(c.sql)
        self.assertIn("CREATE SCHEMA IF NOT EXISTS `cat`.`sch`", joined)
        for t in op.TABLES:
            self.assertIn(f"`cat`.`sch`.`{t}`", joined)
        self.assertGreater(res["annotated"], 0)
        self.assertIn("COMMENT ON TABLE `cat`.`sch`.`customers`", joined)

    def test_foreign_table_is_not_replaced(self):
        c = FakeConn(existing={"ontology_concepts": "someone else's"})
        with self.assertRaises(ConnectorError):
            op.run(doc(), c, "cat", "sch", execute=True, log=lambda s: None)
        self.assertFalse(any(s.startswith(("TRUNCATE", "INSERT", "CREATE")) for s in c.sql))

    def test_own_table_is_refreshed(self):
        c = FakeConn(existing={"ontology_concepts": op.MARK + " ontology - old"})
        op.run(doc(), c, "cat", "sch", execute=True, log=lambda s: None)
        self.assertTrue(any(s.startswith("TRUNCATE") for s in c.sql))

    def test_no_annotate(self):
        c = FakeConn()
        op.run(doc(), c, "cat", "sch", execute=True, annotate=False, log=lambda s: None)
        self.assertFalse(any(s.startswith(("COMMENT ON", "ALTER TABLE")) for s in c.sql))

    def test_quotes_are_escaped(self):
        d = doc()
        d["ontology"][-1]["description"] = "it's 'quoted'"
        c = FakeConn()
        op.run(d, c, "cat", "sch", execute=True, log=lambda s: None)
        self.assertIn("it\\'s \\'quoted\\'", "\n".join(c.sql))


if __name__ == "__main__":
    unittest.main()
