"""DAX -> SQL translation and its independent verifier."""
import unittest

from ossie_hub import executor
from ossie_hub.pbix import reason_for
from ossie_hub.tmdl import DaxUnsupported, dax_to_sql, dax_to_sql_notes, slug

COL = lambda t, c: f"{t.lower()}.{c.replace(' ', '_')}"


class TranslatorTests(unittest.TestCase):
    def setUp(self):
        self.m = {}
        self.m["Selected Year"] = dax_to_sql("2023", self.m, COL)
        self.m["Previous Year"] = dax_to_sql("[Selected Year] - 1", self.m, COL)

    def test_year_filter_with_all_becomes_a_filter_clause(self):
        sql, notes = dax_to_sql_notes(
            "CALCULATE(SUM('Orders'[Sales]), FILTER(ALL('Orders'[Order Date]), "
            "YEAR('Orders'[Order Date]) = [Selected Year]))", self.m, COL)
        self.assertEqual(
            sql, "SUM(orders.Sales) FILTER (WHERE EXTRACT(YEAR FROM orders.Order_Date) = 2023)")
        self.assertTrue(any("outer filter" in n for n in notes))   # the caveat is reported

    def test_previous_year_parameter_is_inlined_with_parentheses(self):
        sql = dax_to_sql("CALCULATE(DISTINCTCOUNT('Orders'[Customer ID]), FILTER(ALL('Orders'[Order Date]), "
                         "YEAR('Orders'[Order Date]) = [Previous Year]))", self.m, COL)
        self.assertIn("= (2023 - 1)", sql)
        self.assertTrue(sql.startswith("COUNT(DISTINCT orders.Customer_ID) FILTER"))

    def test_ratio_of_filtered_measures(self):
        self.m["CY"] = dax_to_sql("CALCULATE(SUM('Orders'[Sales]), FILTER(ALL('Orders'[Order Date]), "
                                  "YEAR('Orders'[Order Date]) = [Selected Year]))", self.m, COL)
        sql = dax_to_sql("DIVIDE([CY], [CY])", self.m, COL)
        self.assertIn("/ NULLIF(", sql)

    def test_all_outside_filter_is_rejected(self):
        with self.assertRaises(DaxUnsupported):
            dax_to_sql("CALCULATE(SUM('Orders'[Sales]), ALL('Orders'))", self.m, COL)

    def test_allselected_is_rejected_with_a_specific_reason(self):
        dax = ("VAR cv = [Selected Year] VAR avgv = AVERAGEX(ALLSELECTED('Products'[Category]), "
               "CALCULATE([Selected Year])) RETURN IF(cv > avgv, \"Above\", \"Below\")")
        with self.assertRaises(DaxUnsupported) as cm:
            dax_to_sql(dax, self.m, COL)
        self.assertIn("ALLSELECTED", reason_for(dax, str(cm.exception)))

    def test_percent_in_a_name_reads_as_pct(self):
        self.assertEqual(slug("% Diff Sales"), "pct_diff_sales")
        self.assertEqual(slug("Min/Max Sales"), "min_max_sales")


class VerifierTests(unittest.TestCase):
    ROWS = {"orders": [
        {"Order_ID": "A", "Order_Date": "2023-03-01 00:00:00", "Sales": "100", "Customer_ID": "c1"},
        {"Order_ID": "B", "Order_Date": "2023-07-09 00:00:00", "Sales": "50", "Customer_ID": "c2"},
        {"Order_ID": "C", "Order_Date": "2022-05-02 00:00:00", "Sales": "30", "Customer_ID": "c1"},
    ]}

    @staticmethod
    def metric(name, sql):
        return {"name": name, "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": sql}]}}

    def test_filtered_ratio_and_diff_are_checked_independently(self):
        cy = "SUM(orders.Sales) FILTER (WHERE EXTRACT(YEAR FROM orders.Order_Date) = 2023)"
        py = "SUM(orders.Sales) FILTER (WHERE EXTRACT(YEAR FROM orders.Order_Date) = (2023 - 1))"
        doc = {"semantic_model": [{"metrics": [
            self.metric("cy", cy), self.metric("py", py),
            self.metric("diff", f"(({cy}) - ({py})) / NULLIF(({py}), 0)"),
            self.metric("cust", "COUNT(DISTINCT orders.Customer_ID) FILTER "
                                "(WHERE EXTRACT(YEAR FROM orders.Order_Date) = 2023)"),
            self.metric("yr", "2023 - 1"),
        ]}]}
        got = executor.expected_from_rows(doc, self.ROWS)
        self.assertEqual(got["cy"], 150.0)
        self.assertEqual(got["py"], 30.0)
        self.assertAlmostEqual(got["diff"], (150 - 30) / 30)
        self.assertEqual(got["cust"], 2.0)
        self.assertEqual(got["yr"], 2022.0)



# ---------------------------------------------------------------------------- importer rules
import json  # noqa: E402

import pandas as pd  # noqa: E402

from ossie_hub import pbix  # noqa: E402


class FakeModel:
    """The slice of a pbixray model that to_ossie() reads."""

    def __init__(self, tables, columns, relationships, measures):
        self.tables = tables
        self.schema = pd.DataFrame(
            [(t, c, "string") for t, cols in columns.items() for c in cols],
            columns=["TableName", "ColumnName", "PandasDataType"])
        self.relationships = pd.DataFrame(
            [(a, b, c, d, 1, "M:1", "Single") for a, b, c, d in relationships],
            columns=["FromTableName", "FromColumnName", "ToTableName", "ToColumnName",
                     "IsActive", "Cardinality", "CrossFilteringBehavior"])
        self.dax_measures = pd.DataFrame(
            [(t, n, e) for t, n, e in measures], columns=["TableName", "Name", "Expression"])


def fact_of(doc):
    ext = doc["semantic_model"][0]["custom_extensions"][0]["data"]
    return json.loads(ext).get("fact_table")


class ImporterFactTests(unittest.TestCase):
    def test_bridge_model_with_heavy_date_use_still_picks_the_request_table(self):
        # Date is mentioned most often (prior-month measures) but is only ever a dimension.
        model = FakeModel(
            ["Requests", "Date", "Committees", "Request Committees"],
            {"Requests": ["Request ID", "Status", "Created Date"], "Date": ["Date", "Year"],
             "Committees": ["Committee Name"], "Request Committees": ["Request ID", "Committee Name"]},
            [("Requests", "Created Date", "Date", "Date"),
             ("Request Committees", "Committee Name", "Committees", "Committee Name"),
             ("Request Committees", "Request ID", "Requests", "Request ID")],
            [("Requests", "Total", "DISTINCTCOUNT('Requests'[Request ID])"),
             ("Requests", "Open", "CALCULATE([Total], 'Requests'[Status] = \"Open\")"),
             ("Requests", "A", "CALCULATE([Total], 'Date'[Year] = 1, 'Date'[Year] = 2, 'Date'[Year] = 3)"),
             ("Requests", "B", "CALCULATE([Total], 'Date'[Year] = 4, 'Date'[Year] = 5)")])
        doc, _ = pbix.to_ossie(model, "m")
        self.assertEqual(fact_of(doc), "requests")

    def test_slicer_only_relationship_does_not_hide_the_real_fact_table(self):
        model = FakeModel(
            ["Orders", "Slicer A", "Slicer B"],
            {"Orders": ["Id", "Flag"], "Slicer A": ["Metric"], "Slicer B": ["Metric"]},
            [("Slicer A", "Metric", "Slicer B", "Metric")],
            [("Orders", "Late", "CALCULATE(COUNTROWS(Orders), Orders[Flag] = \"Late\")"),
             ("Orders", "All", "COUNTROWS(Orders)"),
             ("Orders", "Rate", "DIVIDE([Late], [All])")])
        doc, notes = pbix.to_ossie(model, "m")
        self.assertEqual(fact_of(doc), "orders")
        sql = {m["name"]: m["expression"]["dialects"][0]["expression"]
               for m in doc["semantic_model"][0]["metrics"]}
        self.assertEqual(sql["all"], "COUNT(*)")
        self.assertIn("COUNT(*) FILTER (WHERE orders.Flag = 'Late')", sql["late"])
        self.assertIn("NULLIF", sql["rate"])

    def test_countrows_of_a_dimension_is_rejected_not_guessed(self):
        model = FakeModel(
            ["Sales", "Product"],
            {"Sales": ["Id", "Amount", "ProductId"], "Product": ["ProductId", "Name"]},
            [("Sales", "ProductId", "Product", "ProductId")],
            [("Sales", "Total", "SUM(Sales[Amount])"),
             ("Product", "Products", "COUNTROWS(Product)")])
        doc, notes = pbix.to_ossie(model, "m")
        names = [m["name"] for m in doc["semantic_model"][0]["metrics"]]
        self.assertIn("total", names)
        self.assertNotIn("products", names)          # a dimension row count would be wrong


class NewConstructTests(unittest.TestCase):
    def test_exact_equivalents(self):
        self.assertEqual(dax_to_sql("COUNTA(s[Returned])", {}, COL), "COUNT(s.Returned)")
        self.assertEqual(dax_to_sql("DISTINCTCOUNTNOBLANK(s[Id])", {}, COL), "COUNT(DISTINCT s.Id)")
        self.assertEqual(dax_to_sql("-SUM(s[Sales])", {}, COL), "-SUM(s.Sales)")
        self.assertEqual(dax_to_sql("SUM(s[Sales]) -- note" + chr(10) + " + 1", {}, COL), "SUM(s.Sales) + 1")

    def test_comment_markers_inside_text_are_kept(self):
        self.assertEqual(dax_to_sql('IF(1 = 1, "a--b", "c")', {}, COL),
                         "CASE WHEN 1 = 1 THEN 'a--b' ELSE 'c' END")

    def test_text_concatenation(self):
        sql = dax_to_sql('"Was " & 5 & " last"', {}, COL)
        self.assertTrue(sql.startswith("CONCAT('Was ', COALESCE(CAST(5 AS STRING), '')"))

    def test_unsafe_constructs_are_rejected_not_guessed(self):
        for dax in ("VAR y = MAX(d[Year]) RETURN CALCULATE(SUM(s[Sales]), d[Year] = y)",   # aggregate in filter
                    "CALCULATE(SUM(s[Sales]), DATESQTD(s[D]))",                           # time intelligence
                    "CALCULATE(SUM(s[Sales]), YEAR(s[D]) = YEAR(TODAY()))",               # volatile
                    "SUMMARIZE(s, s[Id])",                                                # table function
                    "RANKX(ALL(s[Id]), [Total])"):
            with self.assertRaises(DaxUnsupported, msg=dax):
                dax_to_sql(dax, {"Total": "SUM(s.Sales)"}, COL, fact_table="s")


if __name__ == "__main__":
    unittest.main()
