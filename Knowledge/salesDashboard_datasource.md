createOrReplace

	table customers
		lineageTag: cae3a0e7-749a-4b7d-8400-8776d9be7dc8
		sourceLineageTag: [Schema2].[customers]

		column Customer_ID
			dataType: string
			lineageTag: 9afc18e9-06b9-4625-adb7-0fd6807cea04
			sourceLineageTag: Customer_ID
			summarizeBy: none
			sourceColumn: Customer_ID

			annotation SummarizationSetBy = Automatic

		column Customer_Name
			dataType: string
			lineageTag: 5cee6971-600f-4033-99cc-2bea87da8642
			sourceLineageTag: Customer_Name
			summarizeBy: none
			sourceColumn: Customer_Name

			annotation SummarizationSetBy = Automatic

		partition customers = entity
			mode: directLake
			source
				entityName: customers
				schemaName: Schema2
				expressionSource: 'DirectLake - Lakehouse_1'

	table location
		lineageTag: ce6da080-bb8c-4dc1-acf3-8b7fcdbd462a
		sourceLineageTag: [Schema2].[location]

		column Postal_Code
			dataType: int64
			formatString: 0
			lineageTag: 3d2d77d0-44e1-4b7d-ac64-a70d0e448a1f
			sourceLineageTag: Postal_Code
			summarizeBy: none
			sourceColumn: Postal_Code

			annotation SummarizationSetBy = Automatic

		column City
			dataType: string
			lineageTag: c7acbd7b-d134-4488-b85c-e34d6af23a12
			sourceLineageTag: City
			summarizeBy: none
			sourceColumn: City

			annotation SummarizationSetBy = Automatic

		column State
			dataType: string
			lineageTag: 8838df11-edc9-41fc-83f6-eab3837d4ca3
			sourceLineageTag: State
			summarizeBy: none
			sourceColumn: State

			annotation SummarizationSetBy = Automatic

		column Region
			dataType: string
			lineageTag: 48512792-edad-455e-ae22-a34b1c86288f
			sourceLineageTag: Region
			summarizeBy: none
			sourceColumn: Region

			annotation SummarizationSetBy = Automatic

		column Country_Region
			dataType: string
			lineageTag: 691bc0b9-14da-45ab-bd96-b907bb90999d
			sourceLineageTag: Country_Region
			summarizeBy: none
			sourceColumn: Country_Region

			annotation SummarizationSetBy = Automatic

		partition location = entity
			mode: directLake
			source
				entityName: location
				schemaName: Schema2
				expressionSource: 'DirectLake - Lakehouse_1'

	table products
		lineageTag: 06b37805-9944-41f0-9cbb-81ec0e9ceada
		sourceLineageTag: [Schema2].[products]

		column Product_ID
			dataType: string
			lineageTag: 2e1873e9-d8af-40a0-8729-186c6e299c3a
			sourceLineageTag: Product_ID
			summarizeBy: none
			sourceColumn: Product_ID

			annotation SummarizationSetBy = Automatic

		column Category
			dataType: string
			lineageTag: d8af0e90-b308-49c6-a1c2-bc6e41b18b84
			sourceLineageTag: Category
			summarizeBy: none
			sourceColumn: Category

			annotation SummarizationSetBy = Automatic

		column Sub_Category
			dataType: string
			lineageTag: 6985452d-1ef3-471b-b7cf-b89a96b0df81
			sourceLineageTag: Sub_Category
			summarizeBy: none
			sourceColumn: Sub_Category

			annotation SummarizationSetBy = Automatic

		column Product_Name
			dataType: string
			lineageTag: aec78af0-ae19-49fd-8bc0-bacc8dbc0c92
			sourceLineageTag: Product_Name
			summarizeBy: none
			sourceColumn: Product_Name

			annotation SummarizationSetBy = Automatic

		partition products = entity
			mode: directLake
			source
				entityName: products
				schemaName: Schema2
				expressionSource: 'DirectLake - Lakehouse_1'

	table orders
		lineageTag: d6e4454d-623f-4f77-a25b-9beeb526705e
		sourceLineageTag: [Schema2].[orders]

		/// Sum of sales amount across all orders.
		measure 'Total Sales' = SUM(orders[Sales])
			formatString: $#,0.00
			lineageTag: 674db78d-ff5a-4281-91e1-1ace4fc4f7da

		/// Sum of profit across all orders.
		measure 'Total Profit' = SUM(orders[Profit])
			formatString: $#,0.00
			lineageTag: 01a3b31d-f761-4a8a-bc56-3317c0da19c0

		/// Total units sold.
		measure 'Total Quantity' = SUM(orders[Quantity])
			formatString: #,0
			lineageTag: 0305d48f-6d29-43fd-b229-2605a489328a

		/// Count of distinct orders.
		measure 'Order Count' = DISTINCTCOUNT(orders[Order_ID])
			formatString: #,0
			lineageTag: 1c990274-8111-4470-bdc6-be903667cf69

		/// Profit divided by sales.
		measure 'Profit Margin' = DIVIDE([Total Profit], [Total Sales])
			formatString: 0.0%
			lineageTag: ede60755-a6d2-420f-a553-7a22b40cbf30

		/// Average discount rate applied across order lines.
		measure 'Avg Discount' = AVERAGE(orders[Discount])
			formatString: 0.0%
			lineageTag: 2ff14509-6911-431a-b3e2-273f0fd59e4b

		column Row_ID
			dataType: int64
			formatString: 0
			lineageTag: d01d9f58-5bc5-428c-bc6c-1fdedc4a7fb3
			sourceLineageTag: Row_ID
			summarizeBy: count
			sourceColumn: Row_ID

			annotation SummarizationSetBy = Automatic

		column Order_ID
			dataType: string
			lineageTag: e39a5b0a-1839-40ac-adac-12e01af50039
			sourceLineageTag: Order_ID
			summarizeBy: none
			sourceColumn: Order_ID

			annotation SummarizationSetBy = Automatic

		column Order_Date
			dataType: dateTime
			formatString: General Date
			lineageTag: 7ed89ce5-ba21-46f9-939f-71334093340c
			sourceLineageTag: Order_Date
			summarizeBy: none
			sourceColumn: Order_Date

			annotation SummarizationSetBy = Automatic

		column Ship_Date
			dataType: dateTime
			formatString: General Date
			lineageTag: 80edebaa-4b57-4d04-982f-623ec837f3a6
			sourceLineageTag: Ship_Date
			summarizeBy: none
			sourceColumn: Ship_Date

			annotation SummarizationSetBy = Automatic

		column Ship_Mode
			dataType: string
			lineageTag: 5b63b89d-ce01-4eda-a6a4-dfc61c846057
			sourceLineageTag: Ship_Mode
			summarizeBy: none
			sourceColumn: Ship_Mode

			annotation SummarizationSetBy = Automatic

		column Customer_ID
			dataType: string
			lineageTag: e1807662-dd34-45c6-9f0f-f13021e524a5
			sourceLineageTag: Customer_ID
			summarizeBy: none
			sourceColumn: Customer_ID

			annotation SummarizationSetBy = Automatic

		column Segment
			dataType: string
			lineageTag: 589b866e-4568-4b35-95a8-ec2a1683d04b
			sourceLineageTag: Segment
			summarizeBy: none
			sourceColumn: Segment

			annotation SummarizationSetBy = Automatic

		column Postal_Code
			dataType: int64
			formatString: 0
			lineageTag: d4c17144-2d9a-45b6-ad39-6411d120f034
			sourceLineageTag: Postal_Code
			summarizeBy: none
			sourceColumn: Postal_Code

			annotation SummarizationSetBy = Automatic

		column Product_ID
			dataType: string
			lineageTag: bac43c00-59a2-450d-917c-79d1498d0cbf
			sourceLineageTag: Product_ID
			summarizeBy: none
			sourceColumn: Product_ID

			annotation SummarizationSetBy = Automatic

		column Sales
			dataType: double
			lineageTag: 12ad3a28-3a52-498c-86d9-8d62e54c4dd4
			sourceLineageTag: Sales
			summarizeBy: sum
			sourceColumn: Sales

			annotation SummarizationSetBy = Automatic

			annotation PBI_FormatHint = {"isGeneralNumber":true}

		column Quantity
			dataType: int64
			formatString: 0
			lineageTag: fabdeab8-60f3-4162-b41e-b66579c089ac
			sourceLineageTag: Quantity
			summarizeBy: sum
			sourceColumn: Quantity

			annotation SummarizationSetBy = Automatic

		column Discount
			dataType: double
			lineageTag: c114cc1d-cbec-4d2c-ba60-ee13ad9bbb10
			sourceLineageTag: Discount
			summarizeBy: sum
			sourceColumn: Discount

			annotation SummarizationSetBy = Automatic

			annotation PBI_FormatHint = {"isGeneralNumber":true}

		column Profit
			dataType: double
			lineageTag: a88db552-1d43-4f37-b737-3a2b62ea3a86
			sourceLineageTag: Profit
			summarizeBy: sum
			sourceColumn: Profit

			annotation SummarizationSetBy = Automatic

			annotation PBI_FormatHint = {"isGeneralNumber":true}

		partition orders = entity
			mode: directLake
			source
				entityName: orders
				schemaName: Schema2
				expressionSource: 'DirectLake - Lakehouse_1'

