-- Create schema main.sales if missing
CREATE SCHEMA IF NOT EXISTS `main`.`sales`;

-- Create table customers
CREATE TABLE IF NOT EXISTS `main`.`sales`.`customers` (
  `Customer_ID` STRING,
  `Customer_Name` STRING
) USING DELTA;

-- Create table products
CREATE TABLE IF NOT EXISTS `main`.`sales`.`products` (
  `Product_ID` STRING,
  `Category` STRING,
  `Sub_Category` STRING,
  `Product_Name` STRING
) USING DELTA;

-- Create table orders
CREATE TABLE IF NOT EXISTS `main`.`sales`.`orders` (
  `Row_ID` BIGINT,
  `Order_ID` STRING,
  `Order_Date` TIMESTAMP,
  `Ship_Date` TIMESTAMP,
  `Ship_Mode` STRING,
  `Customer_ID` STRING,
  `Segment` STRING,
  `Postal_Code` BIGINT,
  `Product_ID` STRING,
  `Sales` DOUBLE,
  `Quantity` BIGINT,
  `Discount` DOUBLE,
  `Profit` DOUBLE
) USING DELTA;

-- Create table location
CREATE TABLE IF NOT EXISTS `main`.`sales`.`location` (
  `Postal_Code` BIGINT,
  `City` STRING,
  `State` STRING,
  `Region` STRING,
  `Country_Region` STRING
) USING DELTA;

-- Create Metric View sales_metrics
CREATE OR REPLACE VIEW `main`.`sales`.`sales_metrics`
WITH METRICS LANGUAGE YAML AS $$
version: '1.1'
source: main.sales.orders
comment: Converted from Fabric semantic model TMDL (sales).
joins:
- name: customers
  source: main.sales.customers
  using:
  - Customer_ID
  rely:
    at_most_one_match: true
- name: products
  source: main.sales.products
  using:
  - Product_ID
- name: location
  source: main.sales.location
  using:
  - Postal_Code
dimensions:
- name: Row_ID
  expr: Row_ID
- name: Order_ID
  expr: Order_ID
- name: Order_Date
  expr: Order_Date
- name: Ship_Date
  expr: Ship_Date
- name: Ship_Mode
  expr: Ship_Mode
- name: Customer_ID
  expr: Customer_ID
- name: Segment
  expr: Segment
- name: Postal_Code
  expr: Postal_Code
- name: Product_ID
  expr: Product_ID
- name: Sales
  expr: Sales
- name: Quantity
  expr: Quantity
- name: Discount
  expr: Discount
- name: Profit
  expr: Profit
- name: customers_Customer_ID
  expr: customers.Customer_ID
- name: Customer_Name
  expr: customers.Customer_Name
- name: products_Product_ID
  expr: products.Product_ID
- name: Category
  expr: products.Category
- name: Sub_Category
  expr: products.Sub_Category
- name: Product_Name
  expr: products.Product_Name
- name: location_Postal_Code
  expr: location.Postal_Code
- name: City
  expr: location.City
- name: State
  expr: location.State
- name: Region
  expr: location.Region
- name: Country_Region
  expr: location.Country_Region
measures:
- name: total_sales
  expr: SUM(Sales)
  comment: Sum of sales amount across all orders.
- name: total_profit
  expr: SUM(Profit)
  comment: Sum of profit across all orders.
- name: total_quantity
  expr: SUM(Quantity)
  comment: Total units sold.
- name: order_count
  expr: COUNT(DISTINCT Order_ID)
  comment: Count of distinct orders.
- name: profit_margin
  expr: SUM(Profit) / NULLIF(SUM(Sales), 0)
  comment: Profit divided by sales.
- name: avg_discount
  expr: AVG(Discount)
  comment: Average discount rate applied across order lines.
$$;
