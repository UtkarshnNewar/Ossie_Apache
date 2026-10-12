# Measure translation report: sales

Source: `Fabric semantic model Sales`

- Translated to SQL: **6 of 6** (100%)
- Kept as the original DAX only: **0**

Rules: a measure is translated only when its meaning is expressible as a model-level SQL aggregate. Anything else is **rejected with a reason, never guessed**, and its original DAX stays in the model (`custom_extensions`).

## Translated

| Measure | SQL (ANSI) | Notes |
|---|---|---|
| total_sales | `SUM(orders.Sales)` |  |
| total_profit | `SUM(orders.Profit)` |  |
| total_quantity | `SUM(orders.Quantity)` |  |
| order_count | `COUNT(DISTINCT orders.Order_ID)` |  |
| profit_margin | `SUM(orders.Profit) / NULLIF(SUM(orders.Sales), 0)` |  |
| avg_discount | `AVG(orders.Discount)` |  |
