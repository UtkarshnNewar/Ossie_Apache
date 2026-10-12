# Ossie -> Tableau loss report: `sales`

Source: `output_models/fabric_sales_ossie.yaml`

- Fields emitted: **24**
- Calculated fields emitted: **6** of 6 Ossie metrics
- Mapped cleanly: **9**  |  approximated: **0**  |  dropped: **0**

## Approximated (review these)

- none

## Dropped / not translated

- none

## Mapped cleanly

- relationship `orders_to_customers` -> left join orders.['Customer_ID'] = customers.['Customer_ID']
- relationship `orders_to_location` -> left join orders.['Postal_Code'] = location.['Postal_Code']
- relationship `orders_to_products` -> left join orders.['Product_ID'] = products.['Product_ID']
- metric `total_sales` -> `Total Sales`
- metric `total_profit` -> `Total Profit`
- metric `total_quantity` -> `Total Quantity`
- metric `order_count` -> `Order Count`
- metric `profit_margin` -> `Profit Margin` (NULLIF(x, 0) dropped: Tableau already returns NULL on divide-by-zero)
- metric `avg_discount` -> `Avg Discount`
