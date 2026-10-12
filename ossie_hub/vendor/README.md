# Vendored code

`ossie_databricks/` is the Python Databricks Metric View converter of Apache Ossie
(`converters/databricks/src/ossie_databricks`) as it was at upstream commit `6b682d9^`.
Upstream replaced it with a Java converter in #333; ossie_hub keeps using this Python version
so that the Metric View output stays identical. Licensed under the Apache License 2.0
(see the header of each file).

It reads and writes the **flat** Ossie document (name / datasets / relationships / metrics at
the root). `ossie_hub` keeps its internal `semantic_model: [...]` shape and converts at the
edges with `ossie_hub/document.py`.
