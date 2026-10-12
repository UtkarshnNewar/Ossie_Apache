# Ossie -> Tableau loss report: `sales`

Source: `output_models/fabric_sales_ossie.yaml`

- Fields emitted: **24**
- Calculated fields emitted: **6** of 6 Ossie metrics
- Mapped cleanly: **9**  |  approximated: **2**  |  dropped: **0**

## Approximated (review these)

- DATA QUALITY: `location.Postal_Code` is not unique (632 rows, 631 distinct; 1 keys repeated). A left join fans out 39 `orders` rows, so SUM/COUNT measures from `orders` will be overstated.
- DATA QUALITY: `products.Product_ID` is not unique (1894 rows, 1862 distinct; 32 keys repeated). A left join fans out 337 `orders` rows, so SUM/COUNT measures from `orders` will be overstated.

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
