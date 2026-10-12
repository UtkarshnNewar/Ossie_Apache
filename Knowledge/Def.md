# Apache Ossie (incubating) — Architecture & Definition Guide

> **Note:** Apache Ossie was formerly known as **Open Semantic Interchange (OSI)**.  
> Official Community: [https://ossie.apache.org/community/](https://ossie.apache.org/community/)  
> Repository: [https://github.com/apache/ossie](https://github.com/apache/ossie)

---

## 1. Executive Summary & Core Purpose

In modern data analytics and business intelligence (BI), different tools define and calculate business logic independently:
- A metric like **Monthly Recurring Revenue (MRR)**, **Gross Margin**, or **Customer Churn Rate** is often duplicated across Snowflake, Databricks, dbt, Tableau/Salesforce, PowerBI, Sigma, and Omni.
- When formulas, business logic, or joins differ slightly across these tools, executives and analysts see conflicting numbers.
- AI agents and LLM text-to-SQL assistants generate hallucinations or unreliable outputs because they lack an agreed-upon semantic model.

**Apache Ossie provides an open, vendor-agnostic semantic model specification (YAML/JSON)** that serves as a single source of truth across BI tools, data warehouses, and AI platforms.

---

## 2. Key Distinction: Data vs. Metadata

To avoid misconceptions:

* **What Ossie is NOT:**
  - It is **not** an ETL or data-movement engine (like Apache Spark, Flink, Kafka, or Airbyte).
  - It does not process, store, or migrate actual raw rows/records of data.

* **What Ossie IS:**
  - It standardizes **Semantic Layer Metadata** (the definitions of business concepts and data relationships):
    - **Datasets (Logical Tables):** Tables, columns, and data types.
    - **Relationships:** Primary key and foreign key joins across tables.
    - **Dimensions & Hierarchies:** How data should be sliced and categorized (e.g., date grains, geographic drill-downs).
    - **Metrics & Measures:** Business formulas and mathematical aggregations (e.g., `SUM`, `AVG`, window functions).
    - **AI Context:** Synonyms, descriptions, and prompt hints so LLMs and AI agents can query the data accurately.

---

## 3. The Hub-and-Spoke Architecture

Without a central semantic standard, connecting $N$ tools requires point-to-point translations ($N \times (N-1)$ converters).  
Ossie acts as a **central hub**, reducing complexity to $2 \times N$ converters (one import and one export per platform):

```
                  ┌──────────────┐
                  │  Snowflake   │
                  └──────┬───────┘
                         │
┌──────────────┐   ┌─────┴──────┐   ┌──────────────┐
│     dbt      ├───┤Apache Ossie├───┤  Salesforce  │
└──────────────┘   └─────┬──────┘   │  / Tableau   │
                         │          └──────────────┘
                  ┌──────┴───────┐
                  │  Databricks  │
                  └──────────────┘
```

### Two Directions of Conversion:
1. **Export (Apache Ossie $\to$ Target Vendor):**
   - Author your model once using the standard Ossie YAML/JSON format.
   - The converter generates native configurations for platforms such as Snowflake, dbt semantic models, Databricks, or Tableau/Salesforce.
2. **Import (Vendor $\to$ Apache Ossie):**
   - Ingest an existing vendor semantic model and convert it into the vendor-neutral Ossie schema.
   - Any proprietary vendor-specific fields are preserved inside Ossie's `custom_extensions`.

---

## 4. Repository Structure & Key Components

| Directory | Purpose / Description |
| :--- | :--- |
| `core-spec/` | The core standard definition, containing `spec.md`, `spec.yaml`, `ossie-schema.json`, and the semantic expression language specification. |
| `converters/` | Reference implementations translating between Ossie and vendor platforms (dbt, Snowflake, Databricks, Salesforce, Sigma, Omni, GoodData, NVIDIA GSF, Wisdom, etc.). |
| `examples/` | Concrete reference models showing how full semantic models are authored in Ossie (e.g., `flights.yaml`, `tpcds_semantic_model.yaml`). |
| `python/` | The official Python package providing Pydantic v2 models to parse, validate, build, and serialize Ossie documents programmatically. |
| `cli/` | Go-based command line interface for validating models and executing conversions. |
| `validation/` | Tooling and test suites for validating semantic models against the Ossie schema. |
| `bi-sql-examples/`| SQL generation patterns and dialect queries translated from semantic models. |

---

## 5. Core Entities in an Ossie Model

An Ossie semantic model consists of four primary structural blocks:

1. **Model Metadata:** `name`, `description`, `version`, and `ai_context`.
2. **Datasets:** The underlying physical or logical tables, their physical column names, data types, and logical business aliases.
3. **Relationships:** The join conditions between datasets (specifying `join_type`, relationship cardinality like `many_to_one`, and join expressions).
4. **Metrics:** The business KPIs calculated over the datasets, including aggregation types, expression syntax, and qualifying dimensions.
