# Ossie -> Tableau loss report: `sales_dashboard_model`

Source: `Knowledge/sales_dashboard_ossie.yaml`

- Fields emitted: **19**
- Calculated fields emitted: **30** of 31 Ossie metrics
- Mapped cleanly: **25**  |  approximated: **8**  |  dropped: **0**

## Approximated (review these)

- metric `kpi_sales_avg` -> `KPI Sales Avg` (OVER () mapped to Tableau WINDOW_* table calculation (depends on the view's partitioning, not model-level))
- metric `kpi_profit_avg` -> `KPI Profit Avg` (OVER () mapped to Tableau WINDOW_* table calculation (depends on the view's partitioning, not model-level))
- metric `min_max_sales` -> `Min Max Sales` (OVER () mapped to Tableau WINDOW_* table calculation (depends on the view's partitioning, not model-level); ANSI_SQL expression has no year filter although the source DAX is based on a current-year measure -> results differ; fix the Ossie model)
- metric `min_max_profit` -> `Min Max Profit` (OVER () mapped to Tableau WINDOW_* table calculation (depends on the view's partitioning, not model-level); ANSI_SQL expression has no year filter although the source DAX is based on a current-year measure -> results differ; fix the Ossie model)
- metric `min_max_quantity` -> `Min Max Quantity` (OVER () mapped to Tableau WINDOW_* table calculation (depends on the view's partitioning, not model-level); ANSI_SQL expression has no year filter although the source DAX is based on a current-year measure -> results differ; fix the Ossie model)
- metric `min_max_orders` -> `Min Max Orders` (OVER () mapped to Tableau WINDOW_* table calculation (depends on the view's partitioning, not model-level); ANSI_SQL expression has no year filter although the source DAX is based on a current-year measure -> results differ; fix the Ossie model)
- metric `min_max_customers` -> `Min Max Customers` (OVER () mapped to Tableau WINDOW_* table calculation (depends on the view's partitioning, not model-level); ANSI_SQL expression has no year filter although the source DAX is based on a current-year measure -> results differ; fix the Ossie model)
- metric `min_max_sales_per_customers` -> `Min Max Sales Per Customers` (OVER () mapped to Tableau WINDOW_* table calculation (depends on the view's partitioning, not model-level); NULLIF(x, 0) dropped: Tableau already returns NULL on divide-by-zero; ANSI_SQL expression has no year filter although the source DAX is based on a current-year measure -> results differ; fix the Ossie model)

## Dropped / not translated

- none

## Mapped cleanly

- parameter `Selected Year` = 2023 (from metric `selected_year`)
- relationship `orders_to_products` -> left join orders.['product_id'] = products.['product_id']
- relationship `orders_to_customers` -> left join orders.['customer_id'] = customers.['customer_id']
- metric `current_year` -> `Current Year` = [Parameters].[Parameter 1]
- metric `previous_year` -> `Previous Year` = [Parameters].[Parameter 1] - 1
- metric `cy_sales` -> `CY Sales`
- metric `py_sales` -> `PY Sales`
- metric `pct_diff_sales` -> `Pct Diff Sales` (NULLIF(x, 0) dropped: Tableau already returns NULL on divide-by-zero)
- metric `cy_profit` -> `CY Profit`
- metric `py_profit` -> `PY Profit`
- metric `pct_diff_profit` -> `Pct Diff Profit` (NULLIF(x, 0) dropped: Tableau already returns NULL on divide-by-zero)
- metric `cy_quantity` -> `CY Quantity`
- metric `py_quantity` -> `PY Quantity`
- metric `pct_diff_quantity` -> `Pct Diff Quantity` (NULLIF(x, 0) dropped: Tableau already returns NULL on divide-by-zero)
- metric `cy_orders` -> `CY Orders`
- metric `py_orders` -> `PY Orders`
- metric `pct_diff_orders` -> `Pct Diff Orders` (NULLIF(x, 0) dropped: Tableau already returns NULL on divide-by-zero)
- metric `cy_customers` -> `CY Customers`
- metric `py_customers` -> `PY Customers`
- metric `pct_diff_customers` -> `Pct Diff Customers` (NULLIF(x, 0) dropped: Tableau already returns NULL on divide-by-zero)
- metric `cy_sales_per_customer` -> `CY Sales Per Customer` (NULLIF(x, 0) dropped: Tableau already returns NULL on divide-by-zero)
- metric `py_sales_per_customer` -> `PY Sales Per Customer` (NULLIF(x, 0) dropped: Tableau already returns NULL on divide-by-zero)
- metric `pct_diff_sales_per_customer` -> `Pct Diff Sales Per Customer` (NULLIF(x, 0) dropped: Tableau already returns NULL on divide-by-zero)
- metric `orders_per_customer` -> `Orders Per Customer` (NULLIF(x, 0) dropped: Tableau already returns NULL on divide-by-zero)
- metric `kpi_cy_less_py` -> `KPI CY Less PY`
