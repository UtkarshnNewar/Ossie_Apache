# Ossie Hub — Instruction Guide

Move a semantic model **and its data** between Power BI, Microsoft Fabric and Databricks,
using an Apache Ossie model as the neutral middle format. Every conversion produces **both the
Ossie model and the business ontology** (plus a measure report), validated, in one command.

Every task below has two ways to do it:

| | What it is | Use it when |
|---|---|---|
| **Code way** | Commands you type in a terminal | You want exact, repeatable steps (or to script them) |
| **Prompt way** | A prompt you paste to Claude Code, which runs the commands for you | You want Claude to drive and report back |

> All commands were run against the real files and workspaces while writing this guide.
> Anything that **writes** to a platform is a dry run unless you add `--execute`.

---

## 0. How it fits together

```
 Power BI .pbix ─┐      ONE command writes all three     ┌─▶ Databricks  (tables + data + Metric Views)
                 ├─▶  Ossie model (YAML)  ──────────────┤
 Fabric model  ──┘    + Ontology (YAML)                  └─▶ (Fabric via `publish`, Tableau via tools/)
                      + Measure report (Markdown)
```

| Piece | Where |
|---|---|
| Source Power BI files | `import_models/` |
| Generated Ossie models, ontologies, exported data | `output_models/` |
| The app / CLI | `ossie_hub/` (run as `python -m ossie_hub …`) |
| Helper scripts | `tools/` (`ossie_to_ontology.py`, `ossie_to_tableau.py`, …); the ontology code lives in `ossie_hub/ontology.py` |
| Validator | `validation/validate.py` (upstream's own; every file we write passes it) |
| File-shape helper | `ossie_hub/document.py` (reads old and new Ossie files, writes the current one) |
| Vendored converter | `ossie_hub/vendor/ossie_databricks/` (see 0.2) |
| Saved connections (no secrets) | `ossie_hub/connectors/<fabric\|databricks>/connections/*.json` |

Safety defaults you can rely on:

- **Dry run is the default.** Nothing is created or loaded without `--execute`.
- **Nothing is overwritten.** If a target table already holds rows the run stops, unless you add `--overwrite`.
- **Secrets never go in a file.** They live in the OS credential store (Windows Credential Manager) or an environment variable.
- **Every executed transfer verifies itself**: measures are recomputed from the source rows and compared with the target.

### 0.2 Updated to the current Apache Ossie (upstream main, Oct 2026)

The repo was brought up to date with `apache/ossie` (125 commits). **No command changed**: every
step in this guide and in `Instruction.pdf` is typed exactly as before. What changed inside:

| Change upstream | What we did |
|---|---|
| An Ossie document is now **one model at the root** (`name`, `datasets`, `relationships`, `metrics` next to `version`); the `semantic_model:` list wrapper was removed (#383, #396) | Every file we write is in the new form and passes upstream's validator. Files in the old form are still **read** without any change. The existing files in `output_models/` and `Knowledge/` were converted in place (content identical, comments kept) |
| The ontology's embedded model must carry a `version`; embedded mappings are marked deprecated in favour of a standalone mapping document (#458, #441) | The ontology embeds a versioned model and validates. It stays **one ontology file per report**, so folders and the push are unchanged. A standalone mapping file is not generated yet |
| The Python Databricks converter was replaced by a Java one (#333) | Metric View generation uses the last Python version, kept in `ossie_hub/vendor/ossie_databricks/` (Apache 2.0). Output is identical |
| New converter `converters/microsoft` (`ossie-microsoft`: lossless `model.bim`/TMDL <-> Ossie round trip, keeps DAX) | Not used by our pipeline: it does a different job (we translate DAX to SQL and move data). Our extension vendor name stays `POWERBI` so the two never read each other's data |
| New `DAX` / `OSSIE_SQL_2026` dialects | Not needed by our output; metrics we cannot translate stay in our extension as before |

### 0.3 The Claude skill (the "prompt way" without writing prompts)

The repo contains a Claude Code skill: `.claude/skills/ossie-hub/` (`SKILL.md` and `reference.md`).
When anyone opens this folder in Claude Code, Claude loads it automatically whenever the request is
about Ossie, a `.pbix`, Fabric, Databricks, ontology or Genie. It then **asks** what you start
from and where it should go, runs the dry runs, explains each result, and only executes after an
explicit yes. It never asks for a token in chat and never reuses someone else's saved connection.

You can simply say: *"I have a Power BI report and want it in Databricks"*. To use it outside
this folder, copy `.claude/skills/ossie-hub` into `~/.claude/skills/` (it then also needs the
repo path, so keeping it inside the repo is the intended use).

### 0.1 Finished outputs already in the repo

You can open these without running anything. Rebuild any of them with the commands in
sections 2 and 3.

| File | What it is | Made by |
|---|---|---|
| `import_models/Sales_Dashboard.pbix` | The source Power BI report | You |
| `Knowledge/sales_dashboard_ossie.yaml` | Ossie model of that report, **all 31 measures written as SQL** with the year fixed at 2023 | Hand-mapped earlier |
| `Knowledge/sales_dashboard_ontology.yaml` | Ontology for the report: `SalesTransaction`, `Product`, `Customer` and their relationships | `tools/ossie_to_ontology.py` on the file above |
| `output_models/Sales_Dashboard/Sales_Dashboard_ossie.yaml` | Ossie model of the same report, **imported automatically** (23 measures as SQL, 8 as DAX). Appears after you run 2.2. | `python -m ossie_hub pbix` |
| `output_models/Sales_Dashboard/Sales_Dashboard_ontology.yaml` | Ontology of the imported model (adds the `Location` dimension) | the same `pbix` command |
| `output_models/Sales_Dashboard/Sales_Dashboard_translation_report.md` | Every measure: its SQL, or why it was kept as DAX | the same `pbix` command |
| `output_models/Sales_Dashboard/data/` | The report's 4 tables exported to CSV (793 / 631 / 9,994 / 1,862 rows) | `python -m ossie_hub pbix` |
| `output_models/Sales_Dashboard_fabric/` | For the Fabric-connected report: Ossie model, ontology and measure report. **No data folder**: the data stays in Fabric. | `python -m ossie_hub pbix … --connection fab-main` |
| `output_models/live_sales_ossie.yaml`, `live_sales_ontology.yaml`, `live_sales_translation_report.md` | The Fabric `Sales` model, its ontology and measure report | `python -m ossie_hub read-model` |
| `Knowledge/salesDashboard_datasource.md` | The Fabric `Sales` model as TMDL text (copied from the TMDL view) | By hand |
| `Knowledge/Practical_PowerBI_to_Ossie_Guide.md`, `Def.md`, `What_is_Oddise_Apache.md` | Background notes on Ossie and the Power BI mapping | Earlier work |

The two `sales_dashboard_ontology.yaml` files differ only because their sources differ: the
hand-mapped model has no `Location` table, the imported one does. Either can be used as the
reference ontology for this report.

---

## 1. Connect Fabric and Databricks to the repo

### 1.1 What you need first

| Need | Details |
|---|---|
| Python | 3.11 or newer (developed on 3.14) |
| The repo | This folder, opened in a terminal at its root |
| Fabric | A workspace you can read, with a lakehouse (a trial capacity works). A Microsoft account that can sign in. |
| Databricks | A workspace, a **SQL warehouse** (serverless is fine), and a **personal access token** |
| Windows | Credential Manager is used to remember the token (other OSes need a `keyring` backend) |

Install once (from the repo root):

```
python -m pip install -r ossie_hub/requirements.txt
```

Check the install (should end with `OK`):

```
python -m unittest ossie_hub.tests.test_dax ossie_hub.tests.test_pbix_files ossie_hub.tests.test_fabric ossie_hub.tests.test_targets ossie_hub.tests.test_connections ossie_hub.tests.test_ontology_push ossie_hub.tests.test_document
```

This is the repo's automated test suite. It is **one command** (a terminal may wrap it over
several lines). It runs offline, with no Fabric or Databricks login, in about a second.
`OK` means the install is healthy and nothing has regressed. It is not needed to convert
anything; run it after installing and after changing code.

| Test file | What it checks |
|---|---|
| `test_dax` | The rules that translate Power BI measures (DAX) into SQL, including what must be rejected |
| `test_pbix_files` | Recognising a `.pbix` that carries its own data vs. one connected to Fabric, and finding its Fabric model |
| `test_fabric` | The Fabric connector against a fake Fabric API |
| `test_targets` | The Databricks / Snowflake planner (renaming, bridges, duplicate keys) |
| `test_connections` | Saved connections: secrets are never written to a file |
| `test_document` | The Ossie file shape: files are written in the current one-model-per-document form, both old and new forms are read, and the Databricks plan is identical from either |
| `test_ontology_push` | The ontology flattened into tables; refuses an incomplete ontology; never replaces a table it did not create; works for a model with none of the sales vocabulary |

### 1.2 Connect Microsoft Fabric

**Code way**

```
python -m ossie_hub connect add fabric --name fab-main --set auth_mode=interactive_browser
python -m ossie_hub connect test fab-main
```

- `connect add` asks one question: **"Your Microsoft account email"**. Type the work or school
  email you sign in to Fabric with (for example `name@company.com`). It is optional (press
  Enter to skip) and it is **not a password**; it only pre-selects that account in the sign-in
  window. Nothing else is asked.
- `connect test` opens a Microsoft sign-in window the first time, with your email already
  filled in. Enter your password / approve as you normally do. Afterwards the session is
  remembered for that account.
- Success prints `"ok": true` and the number of workspaces you can see.

> **Anyone can use their own email.** Each person (for example your manager) runs the same
> command on their own computer, types **their own** email, and signs in as themselves. They
> need at least *Viewer* access to the workspace. Saved connections and sign-ins are per person
> and per computer, so nothing is shared and nobody's password is stored.
>
> **Tenant ID is an advanced option, not part of the normal flow.** It is only needed for a
> service principal (an unattended app login), or if one account belongs to several
> organisations and the sign-in picks the wrong one. To be asked for it, add `--advanced`:
> `python -m ossie_hub connect add fabric --name fab-main --set auth_mode=interactive_browser --advanced`.
> Find it in the Entra admin center (`entra.microsoft.com` → *Overview* → *Tenant ID*). It is a
> code or a domain, never an email, and the app refuses an email there. A **workspace ID** shown
> in the Fabric admin portal is a different thing and is not used here.

### 1.2b Look around Fabric: workspace → model → lakehouse → tables

Once connected, you pick **what to convert** step by step. Each command narrows down the one
before it; use the names it prints in the next command. All of them use your saved connection
(`--connection fab-main`), so they sign in as you.

**Code way**

```
python -m ossie_hub options fabric workspace --connection fab-main
python -m ossie_hub options fabric semantic_model --connection fab-main --pick workspace=OssieApache_Workspace
python -m ossie_hub options fabric lakehouse --connection fab-main --pick workspace=OssieApache_Workspace
python -m ossie_hub options fabric lakehouse_table --connection fab-main --pick workspace=OssieApache_Workspace --pick lakehouse=Lakehouse_1
```

| Step | What it lists | What you take from it |
|---|---|---|
| 1. `workspace` | Every workspace you can open (12 in the sample) | The **workspace name**, e.g. `OssieApache_Workspace` |
| 2. `semantic_model` | The Power BI / Fabric models in that workspace | The **model name**, e.g. `Sales` |
| 3. `lakehouse` | The lakehouses in that workspace, with their SQL endpoint | The **lakehouse name**, e.g. `Lakehouse_1` |
| 4. `lakehouse_table` | The tables (the actual data) inside that lakehouse, as `schema.table` | Which data exists: `Schema2.orders`, `Schema2.products`, … |

Real output for the sample workspace:

```
Sales                                    954e73b5-d16f-4e00-9662-1548ea8c2d93          <- step 2

Lakehouse_1                              403f13a6-068b-4b8a-977f-5636f07b1ca1  ...datawarehouse.fabric.microsoft.com   <- step 3

Schema2.customers   Schema2.location   Schema2.orders   Schema2.products      <- step 4
dbo.customers       dbo.location       dbo.orders       dbo.products
```

Names with spaces need quotes: `--pick "workspace=My Workspace"`.

**Then convert the model you picked (step 5)**

```
python -m ossie_hub read-model fabric --connection fab-main --pick workspace=OssieApache_Workspace --pick semantic_model=Sales -o output_models/live_sales_ossie.yaml
```

This writes the Ossie model, the ontology and the measure report (section 3.1). The lakehouse
and its schema are recorded inside the model, so the later data transfer (section 3.2) finds the
tables on its own.

**Prompt way**

> Using my saved Fabric connection `fab-main`, show me the workspaces I can see. Then, for
> `OssieApache_Workspace`, list its semantic models, its lakehouses, and the tables inside
> `Lakehouse_1`. Tell me which model and which tables I would convert.

### 1.3 Connect Databricks

Get three things from Databricks first:

| Item | Where to find it |
|---|---|
| **Workspace host** | The URL of your workspace, e.g. `dbc-1cb023e2-67f6.cloud.databricks.com` (no `https://`) |
| **SQL warehouse** | *SQL Warehouses* page: its name (e.g. `Serverless Starter Warehouse`) or id |
| **Access token** | Profile → *Settings* → *Developer* → *Access tokens* → *Generate new token* (starts with `dapi…`) |

**Code way**

```
python -m ossie_hub connect add databricks --name dbx-main --set host=<your-host> --set "warehouse=<warehouse name or id>"
```

It then asks for the token. **Paste it at the hidden prompt** (nothing appears as you type). It is stored in the Windows credential store, never in a file.

```
python -m ossie_hub connect test dbx-main
```

Success prints your user and the default catalog (for example `"default_catalog": "workspace"`).

**Prompt way**

> Create a saved Databricks connection named `dbx-main`. Host: `<your-host>`, SQL warehouse:
> `<name>`. I will type the access token myself at the hidden prompt — do not ask me for it in
> chat. Then test the connection and list the catalogs and schemas.

> **Never paste a token into a chat or a file.** If one was exposed, revoke it in Databricks and
> re-run `connect add` with a new one (the old entry is replaced).

### 1.4 Manage connections

```
python -m ossie_hub connect list          # grouped by platform, with created / last used / last test
python -m ossie_hub connect test <name>
python -m ossie_hub connect remove <name> # also deletes the stored secret
```

### 1.5 Giving the repo to someone else (each person signs in as themselves)

A connection is **personal**. Two kinds of files hold your sign-in details and must not travel
with the repo:

| Where | What it holds |
|---|---|
| `ossie_hub/connectors/fabric/connections/*.json` and `.../databricks/connections/*.json` | Your saved connection: **your email**, your Databricks host and warehouse, the result of your last test. Tokens are not in these files; they live in your Windows credential store. |
| `.ossie_hub/` (in the folder you ran commands from) | Your remembered Fabric sign-in record (account name and tenant, no password) |

**Why it matters.** If these files are copied, the other person running a command with
`--connection fab-main` is *not* asked for their email: the browser opens with **your** account
pre-selected. Databricks would fail with "secret is missing", because your token is in your own
credential store.

**What to do before handing the folder over (zip, USB, shared drive):** delete those files, or
delete the folder `ossie_hub/connectors/*/connections/*.json` and `.ossie_hub/`. Sharing through Git
is already safe: both are git-ignored and never pushed.

**What the other person then sees (verified)**

```
python -m ossie_hub connect list
   Microsoft Fabric connections (0) ... none yet.
python -m ossie_hub connect test fab-main
   error: no saved connection 'fab-main'. Add one: python -m ossie_hub connect add <type> --name fab-main
```

They run steps 1.2 and 1.3 themselves: `connect add fabric` asks **their** email, and
`connect add databricks` asks **their** token. Nothing of yours is reused.

---

## 2. Power BI file → Ossie model → ontology → Databricks

### 2.1 Put the file in place

Copy the report into `import_models/`, e.g. `import_models/Sales_Dashboard.pbix`.

**Code way: confirm the file is there**

```
dir import_models
```

You should see `Sales_Dashboard.pbix` in the list (with its size and date). If it is not
listed, the file is in the wrong folder or has a different name: fix that before continuing,
and use the exact name in the next command. (On Linux or macOS the same check is
`ls import_models`.)

**Prompt way**

> Check that `import_models/Sales_Dashboard.pbix` exists and tell me its size.

> A report either carries its own data (Import mode) or is only a live connection to a Fabric
> model (a tiny file). Both are handled by the same command; section 2.2 covers the first kind
> and **2.2b the Fabric-connected kind**.

### 2.2 Convert the .pbix to an Ossie model and an ontology

The same command handles both kinds of Power BI report. It recognises the kind itself:

| Kind of report | How to tell | Where the data is | What the command does |
|---|---|---|---|
| **Has its data inside** (Import mode) | A normal-sized file, e.g. `Sales_Dashboard.pbix` (~700 KB) | In the file | Reads the model **and exports the data** to CSV |
| **Connected to Fabric** (live connection) | A tiny file, e.g. `Sales_Dashboard_fabric.pbix` (~8 KB) | In your Fabric lakehouse | Finds the Fabric model it points to and reads it from Fabric. **No data is exported.** (see 2.2b) |

**Output folder (the same for both):** a folder named after the report is created inside
`output_models/`, and everything for that report goes in it:

```
output_models/
└── Sales_Dashboard/                          <- named after the .pbix file
    ├── Sales_Dashboard_ossie.yaml            <- the Ossie model
    ├── Sales_Dashboard_ontology.yaml         <- the ontology
    ├── Sales_Dashboard_translation_report.md <- which measures became SQL, which stayed DAX
    └── data/                                 <- CSV data (only for reports that carry their own data)
```

**Code way, a report that carries its own data**

```
python -m ossie_hub pbix import_models/Sales_Dashboard.pbix
```

What you get (real output for `Sales_Dashboard.pbix`):

**One command produces three things and validates the first two:**

| Output | Location | Content |
|---|---|---|
| **Ossie model** | `output_models/Sales_Dashboard/Sales_Dashboard_ossie.yaml` | 4 datasets, 3 relationships, **23 of 31 measures translated to SQL** (all 31 kept, the other 8 as DAX) |
| **Ontology** | `output_models/Sales_Dashboard/Sales_Dashboard_ontology.yaml` | The business concepts and the fact → dimension relationships (see 2.3) |
| **Measure report** | `output_models/Sales_Dashboard/Sales_Dashboard_translation_report.md` | Every measure: translated (with its SQL) or kept as DAX (with the reason) |
| One CSV per table | `output_models/Sales_Dashboard/data/` | customers 793, location 631, orders 9,994, products 1,862 rows |

The command prints a summary like this:

```
Ossie model : output_models\Sales_Dashboard\Sales_Dashboard_ossie.yaml     validation: PASSED
Ontology    : output_models\Sales_Dashboard\Sales_Dashboard_ontology.yaml  validation: PASSED
              4 entity concepts (SalesTransaction, Customer, Location, Product), 21 value types
Measures    : output_models\Sales_Dashboard\Sales_Dashboard_translation_report.md
              23 of 31 translated to SQL, 8 kept as DAX only
```

It also prints a `WARNING` if fewer than half of the measures could be translated. **Open the
measure report before relying on the metrics** (section 2.6 explains how the translator works).

Switches: `--no-ontology` (skip the ontology), `--out-dir <folder>` (use another output folder
instead of `output_models/<report name>/`), `--name <name>` (the model's name inside the file).
To re-check a file yourself: `python validation/validate.py <file>`; add
`--schema ontology/ontology.json` for an ontology.

**Prompt way**

> Convert `import_models/Sales_Dashboard.pbix` with `python -m ossie_hub pbix`: export its tables
> to CSV in `output_models/Sales_Dashboard/data` and write the Ossie model
> `output_models/Sales_Dashboard/Sales_Dashboard_ossie.yaml`. It should also produce the ontology and the measure
> report and validate both. Then summarise the tables, relationships, the ontology concepts, and
> which measures were translated vs. kept as DAX and why.

### 2.2b A report connected to Fabric (the data stays in Fabric)

Use this for a report such as `import_models/Sales_Dashboard_fabric.pbix`. It is only a thin
window onto a model that lives in Fabric, so the file holds neither data nor model. The app reads
the model **from Fabric**, so you need your saved Fabric connection (section 1.2).

**Code way**

```
dir import_models
python -m ossie_hub pbix import_models/Sales_Dashboard_fabric.pbix --connection fab-main
```

Real output:

```
Report      : Sales_Dashboard_fabric.pbix  (live)
Data source : Fabric. This report is a live connection to the model 'Sales' in workspace 'OssieApache_Workspace'.
              No data is inside the file, so none is exported; the data stays in Fabric.

Ossie model : output_models\Sales_Dashboard_fabric\Sales_Dashboard_fabric_ossie.yaml     validation: PASSED
Ontology    : output_models\Sales_Dashboard_fabric\Sales_Dashboard_fabric_ontology.yaml  validation: PASSED
              4 entity concepts (SalesTransaction, Customer, Product, Location), 21 value types
Measures    : output_models\Sales_Dashboard_fabric\Sales_Dashboard_fabric_translation_report.md
              6 of 6 translated to SQL, 0 kept as DAX only
Model       : 4 datasets, 3 relationships
   customers        <- Lakehouse_1.Schema2.customers
   products         <- Lakehouse_1.Schema2.products
   orders           <- Lakehouse_1.Schema2.orders
   location         <- Lakehouse_1.Schema2.location
```

What it did, step by step:

1. Opened the `.pbix` and saw it is a **live connection** (it only stores the id of a Fabric model).
2. Searched the workspaces your account can open for that model id, and found `Sales`.
3. Read the model from Fabric and wrote the Ossie model, the ontology and the measure report into
   `output_models/Sales_Dashboard_fabric/`.
4. Recorded where the data lives. The last lines show each table's source in the lakehouse, so
   **no CSV folder is created** and the transfer in 2.4 reads the data straight from Fabric.

**Prompt way**

> `import_models/Sales_Dashboard_fabric.pbix` is connected to Fabric. Using my saved Fabric
> connection `fab-main`, convert it with `python -m ossie_hub pbix`. It should create the folder
> `output_models/Sales_Dashboard_fabric/` containing the Ossie model and the ontology, export no
> data, and tell me which Fabric model and lakehouse tables it points to.

### 2.3 The ontology

The ontology adds the **business names** and the **relationships between the dimensions and the
fact table** on top of the Ossie model. It is created by the same command as above, so there is
nothing extra to run. To (re)create it separately from any Ossie file:

**Code way**

```
python tools/ossie_to_ontology.py output_models/Sales_Dashboard/Sales_Dashboard_ossie.yaml output_models/Sales_Dashboard/Sales_Dashboard_ontology.yaml
python validation/validate.py output_models/Sales_Dashboard/Sales_Dashboard_ontology.yaml --schema ontology/ontology.json
```

Result for this file: 4 entity concepts (`SalesTransaction`, `Customer`, `Product`, `Location`),
21 business value types, and these fact → dimension relationships:

| Relationship | Reads as |
|---|---|
| `SalesTransaction.for_product` | "SalesTransaction is for Product" / "Product is sold in SalesTransaction" |
| `SalesTransaction.placed_by` | "SalesTransaction is placed by Customer" |
| `SalesTransaction.delivered_to` | "SalesTransaction is delivered to Location" |

Business names come from two small tables at the top of `ossie_hub/ontology.py`
(`ENTITY_NAMES`, `FIELD_NAMES`). Edit them and re-run to rename a concept. Unknown tables or
fields fall back to a humanised name, so any report gets an ontology; review the names before
publishing it.

**Prompt way**

> Create the ontology for `output_models/Sales_Dashboard/Sales_Dashboard_ossie.yaml`, write it to
> `output_models/Sales_Dashboard/Sales_Dashboard_ontology.yaml`, and validate it against
> `ontology/ontology.json`. Show me the business concepts and the relationships between each
> dimension and the fact table.

### 2.4 Transfer the model, data and metrics to Databricks

Always **dry-run first**. Name the fact table with `--fact` (here `orders`).

**Code way — step 1, dry run (changes nothing)**

```
python -m ossie_hub migrate --to databricks --source csv --csv-dir output_models/Sales_Dashboard/data --from-yaml output_models/Sales_Dashboard/Sales_Dashboard_ossie.yaml --fact orders --catalog workspace --schema sales_dashboard --connection dbx-main
```

It prints the plan (schema, tables, Metric View), reads the CSV rows, and checks the target for
existing tables.

**Step 2, execute**

```
python -m ossie_hub migrate --to databricks --source csv --csv-dir output_models/Sales_Dashboard/data --from-yaml output_models/Sales_Dashboard/Sales_Dashboard_ossie.yaml --fact orders --catalog workspace --schema sales_dashboard --connection dbx-main --execute
```

What gets created in `workspace.sales_dashboard`:

| Object | Detail |
|---|---|
| Tables | one per dataset, loaded from the CSVs (row counts are checked) |
| Metric View | `sales_dashboard_metrics` — joins, dimensions and the translated measures |
| Views | only when needed (see *Many-to-many bridges* below) |

At the end it prints **VERIFICATION**: each measure is recomputed from the CSVs and compared with
the Metric View. Any `BAD` line means the transfer is not trustworthy.

Useful switches: `--overwrite` (reload tables that already hold rows) and `--semantic-only`
(re-create views and Metric Views without touching data).

**Prompt way**

> Do a **dry run** of transferring `output_models/Sales_Dashboard/Sales_Dashboard_ossie.yaml` to Databricks with
> `python -m ossie_hub migrate`: CSV source `output_models/Sales_Dashboard/data`, fact table
> `orders`, catalog `workspace`, new schema `sales_dashboard`, connection `dbx-main`. Show me the
> plan and the notes. **Do not execute** until I say so.

Then:

> Execute it with `--execute`, and show me the verification table.

#### 2.4b The same transfer for the Fabric-connected report

Here the rows come **from the Fabric lakehouse**, not from CSV files. The Ossie file already
records the workspace, lakehouse and schema, so you do not repeat them:

```
python -m ossie_hub migrate --to databricks --source fabric --connection-fabric fab-main --from-yaml output_models/Sales_Dashboard_fabric/Sales_Dashboard_fabric_ossie.yaml --catalog workspace --schema sales_dashboard_fabric --connection dbx-main
```

It first prints which Fabric data it will read, for example:

```
Fabric data : workspace 'OssieApache_Workspace', lakehouse 'Lakehouse_1', schema 'Schema2'
```

Dry run first; add `--execute` to create and load. Repeated product IDs and postal codes in the
lakehouse are made unique in transit and reported, as in section 3.2.

**Prompt way**

> Do a **dry run** of moving the model in
> `output_models/Sales_Dashboard_fabric/Sales_Dashboard_fabric_ossie.yaml` to Databricks, reading
> the rows from the Fabric lakehouse it points to (use my saved connections `fab-main` and
> `dbx-main`), into the new schema `sales_dashboard_fabric` in catalog `workspace`. Show me which
> Fabric data it will read, the row counts, and the plan. Do not execute until I say so.

#### 2.4c Send the ontology to Databricks too (same schema as the data)

**Which schema?** Use **one schema per model** and put everything in it: the data tables, the
Metric View, and the four ontology tables. For the Fabric-connected report that is
`sales_dashboard_fabric` in catalog `workspace`, the same schema as in 2.4b. Do not put it into a
schema that belongs to another model: the ontology tables have fixed names, so two models would
overwrite each other. A *new* schema is therefore right whenever the model is new; if you already
ran 2.4b, the schema exists and the ontology simply joins it. `ontology push` creates the schema
if it is missing.

**Order**

1. Run 2.4b first (the data and Metric View land in the schema).
2. Then the ontology, dry run first:

```
python -m ossie_hub ontology push --from-yaml output_models/Sales_Dashboard_fabric/Sales_Dashboard_fabric_ontology.yaml --catalog workspace --schema sales_dashboard_fabric --connection dbx-main
```

It lists what it would write and changes nothing:

```
   ontology_info              1 rows
   ontology_concepts         25 rows
   ontology_relationships    24 rows
   ontology_mappings         24 rows
   annotations               32 comment/tag statements on the data tables
DRY RUN: nothing was written.
```

3. Add `--execute` to write it.

**What lands in Databricks**

| Object | What it holds |
|---|---|
| `ontology_info` | which ontology this is, which model it came from, when it was pushed |
| `ontology_concepts` | every business concept: entity or value type, business name, key, source table |
| `ontology_relationships` | every relationship with its kind (`link` = fact to dimension, `attribute`), multiplicity and sentence ("Sales Transaction is placed by Customer") |
| `ontology_mappings` | which table and column each concept and relationship comes from |
| comments and tags on the data tables | each table gets its business name and an `ontology_concept` tag; each mapped column gets a business comment (visible in Catalog Explorer and to Genie) |

Example query, every dimension related to the fact:

```sql
SELECT concept, relationship, related_concept, verbalization
FROM workspace.sales_dashboard_fabric.ontology_relationships
WHERE relationship_kind = 'link';
```

**Safety**

- Dry run is the default; nothing is written without `--execute`.
- The four ontology tables carry the marker `ossie_hub:` in their comment. Re-running refreshes
  them. A table with one of those names that does **not** carry the marker is never touched
  (the command stops) unless you add `--overwrite`.
- Before anything is sent, the ontology is checked for completeness: every entity needs an
  identifier and a table mapping. If one does not, the command stops and says which, instead of
  pushing a partial ontology.
- The data tables are never modified except for comments and tags; use `--no-annotate` to skip
  even that. A failed comment never undoes the ontology tables (it is counted and reported).

**Prompt way**

> Do a **dry run** of pushing
> `output_models/Sales_Dashboard_fabric/Sales_Dashboard_fabric_ontology.yaml` to Databricks
> (connection `dbx-main`) into catalog `workspace`, schema `sales_dashboard_fabric`. Tell me the
> tables and row counts it would write. Do not execute until I say so.

Then:

> Execute it with `--execute`, then run the SQL above and show me the result.

#### 2.4d Connect it to a Genie space (ask questions in plain words)

Genie is Databricks' "ask your data a question" feature. It answers from the tables, Metric
Views and comments you attach to a **Genie space**. Once 2.4b and 2.4c are executed, everything
it needs is in the schema. Creating the space is a step in the Databricks screen (the repo does
not automate it yet).

**Steps (Databricks UI)**

1. Open **Genie** and choose **New**. The *Connect your data* window opens.
2. The **For you** tab only lists recently used tables. Use the **All** tab (or Search
   `sales_dashboard_fabric`) to find the rest, and tick all of these:

| Tab | Item | Why |
|---|---|---|
| All | `orders`, `customers`, `products`, `location` | the data |
| All | `ontology_concepts`, `ontology_relationships`, `ontology_mappings`, `ontology_info` | the ontology: what the business terms mean and how they relate |
| **Metric views** | `sales_dashboard_fabric_metrics` | the translated measures (a Metric View is not listed under tables) |

3. Check that **Selected** shows 9 items, then click **Create**.
4. Optional: in the space's *Instructions*, paste a short orientation, adapted to your model:

> `orders` is the fact table: one row per sales line. It links to `customers` by Customer_ID,
> to `products` by Product_ID and to `location` by Postal_Code. The tables `ontology_concepts`,
> `ontology_relationships` and `ontology_mappings` describe the business terms and how they
> relate: use them to answer "what is X" and "how is X related to Y". Use the metric view for
> totals and averages.

**Questions to try**

1. "What is a Sales Transaction related to?" (answered from `ontology_relationships`)
2. "Total sales by customer segment" (answered from the Metric View / `orders`)
3. "Which locations receive the most sales transactions?" (needs the link `orders` to `location`)

**What was and was not verified.** The schema content was checked in Databricks (tables, rows,
comments, tags, links). How well Genie answers a given business word was **not** measured: try
the questions above and keep the answers that matter as examples in the space. Writing business
synonyms into the Metric View itself is not done yet (section 6.5).

#### 2.4e A dashboard of the result (one HTML file)

Once the data, the Metric View and the ontology are in Databricks, one command turns them into a
designed, self-contained dashboard (no internet or libraries needed; open it in any browser):

```
python tools/build_dashboard.py --connection dbx-main --catalog workspace --schema sales_dashboard_fabric --out output_models/Sales_Dashboard_fabric/Sales_Dashboard_fabric_dashboard.html
```

It only **reads** from Databricks (nothing is written). The file has two tabs:

| Tab | What it shows |
|---|---|
| Dashboard | KPI cards (the six Metric View measures plus customers) with year-on-year change, trend, and bars by category, region, segment, state, sub-category margin and top customers. Filters and click-to-filter on every chart. Each chart carries its ontology term, e.g. *Product > Product Category, from products.Category*. |
| Ontology: what it means | The three layers (ontology, semantic model, data), a concept map with the relationship sentences, every business term with its source column, and a glossary of the measures with their formulas. |

Notes: the page is a **snapshot** taken when it is built (rebuild to refresh); the footer shows
whether its totals match the Metric View. It expects the Sales shape (orders, customers, products,
location) and stops with a clear message if a table or column is missing. Numbers carry no
currency symbol because the model does not define a currency.

**Prompt way**

> Build the HTML dashboard for schema `workspace.sales_dashboard_fabric` with
> `tools/build_dashboard.py` using connection `dbx-main`, save it next to the Ossie model, and
> tell me whether its totals match the Metric View.

### 2.5 Check it in Databricks

After an `--execute` run, in a Databricks SQL editor:

```sql
SHOW TABLES IN workspace.sales_dashboard;
SELECT MEASURE(cy_sales), MEASURE(py_sales), MEASURE(pct_diff_sales)
FROM workspace.sales_dashboard.sales_dashboard_metrics;
```

Measures of a Metric View are always read with `MEASURE(<name>)` and can be grouped by any
dimension of the view, e.g. `SELECT <dimension>, MEASURE(<measure>) FROM <view> GROUP BY <dimension>`.

#### What the Sales dashboard's 31 measures became

| Group | Count | Result |
|---|---|---|
| Year parameters (`Selected Year`, `Current Year`, `Previous Year`) | 3 | Translated (constants) |
| CY / PY of Sales, Profit, Quantity, Orders, Customers (`CALCULATE(…, FILTER(ALL(col), YEAR(col) = [Selected Year]))`) | 10 | Translated to `AGG(…) FILTER (WHERE EXTRACT(YEAR FROM Order_Date) = 2023)` |
| `% Diff …` (6), `… per Customer` (3), `KPI CY Less PY` (1) | 10 | Translated (they are built from the ten above) |
| `KPI Sales Avg`, `KPI Profit Avg`, six `Min/Max …` | 8 | **Not translated.** They use `ALLSELECTED` with `MINX / MAXX / AVERAGEX`: the value of one group is compared with the other groups in the visual, which a model-level measure cannot see. Their DAX is kept in the Ossie file and runs unchanged in Fabric / Power BI. |

Three things to know when reading the numbers:

1. **The year is fixed at 2023.** In the report `[Selected Year]` is a constant measure (`2023`),
   so the SQL uses the literal. Changing the year means changing that one measure and re-running.
2. **`FILTER(ALL(col), …)` is approximated.** DAX also ignores any outer filter on that column;
   a SQL `FILTER (WHERE …)` cannot. The measure's description in the Ossie file records this.
3. **`% Diff Profit` divides by CY Profit; the other five `% Diff` measures divide by PY.** That is
   what the original report does (`DIVIDE([CY Profit] - [PY Profit], [CY Profit])`), so the
   converted value (0.1246) differs from a "like the others" value (0.1424). It is faithful to
   the source and probably a bug in the report: worth confirming with its owner.

How this was verified (Sales dashboard, 2023 vs 2022): the 19 numeric measures were recomputed
independently with pandas from the exported CSVs and with the translated SQL on Databricks'
engine; all match (e.g. CY Sales 733,215.26, PY Sales 609,205.60, CY Customers 693).

### 2.6 How measures are translated, and how mistakes are prevented

A Power BI measure is written in DAX; Databricks needs SQL. The importer translates each measure
by rules (`ossie_hub/dax.py`). The design goal is that **a translation is never silently wrong**:

| Rule | What it means |
|---|---|
| **Reject, never guess** | Anything the rules do not fully understand is left as the original DAX, with the reason written in the measure report. It is never approximated. |
| **Filters must be row-level** | A filter that contains an aggregate (for example `Year = MAX(Date[Year])`) depends on what the viewer selected, so the measure is rejected. |
| **Viewer-dependent logic is rejected** | `SELECTEDVALUE`, `VALUES`, `ALLSELECTED`, `EARLIER`, `RANKX`, `TOPN` and the time-intelligence functions (`DATESQTD`, `TODAY`…) have no model-level equivalent. |
| **Row counts only on the fact table** | `COUNTROWS(Table)` becomes `COUNT(*)` only when `Table` is the fact table. On a dimension it would count joined rows, so it is rejected. |
| **Differences are recorded** | Where SQL differs subtly from DAX (text compared case-insensitively, `DISTINCTCOUNT` counting a blank, `ALL()` also ignoring outer filters) the measure's description says so. |

How the **fact table** is chosen (it matters for `COUNTROWS` and for the Metric View source):
the table the measures are written against, i.e. referenced most often as `Table[column]`, after
excluding pure dimensions (tables that are only ever on the "one" side of a relationship). It is
recorded in the Ossie file and used by the planner, so the two always agree.

**What is translated:** `SUM`, `AVERAGE`, `MIN`, `MAX`, `COUNT`, `COUNTA`, `DISTINCTCOUNT`,
`DISTINCTCOUNTNOBLANK`, `SUMX`/`AVERAGEX`/`MINX`/`MAXX`/`COUNTX` over one plain table,
`COUNTROWS` of the fact table, `DIVIDE`, `IF`, `SWITCH`, `AND`/`OR`/`NOT`, `&&`/`||`, `&` text
joins, `COALESCE`, `ISBLANK`, `YEAR`/`MONTH`/`DAY`, `DATE`, `CALCULATE` with column filters,
`FILTER(table, cond)` and `FILTER(ALL(col), cond)`, `IN {…}`, `VAR`/`RETURN` (inlined), `--`
comments, and the "previous month" template (as a Databricks window measure).

#### Checked against every report we have

The same importer was run on all six Power BI files in the project folders:

| Report | Measures | Translated | Fact table chosen |
|---|---|---|---|
| `Sales_Dashboard.pbix` | 31 | 23 | `orders` |
| `Sales_powerbi_updated.pbix` | 24 | 20 | `superstoresales` |
| `mayoncpReport.pbix` (older copy) | 25 | 22 | `requests` |
| `Infomation.pbix` | 24 | 5 | `orders` |
| `Order_Without_CC.pbix` | 5 | 4 | `order` |
| `Order_With_CC.pbix` | 3 | 3 | `order` |
| **Total** | **112** | **77 (68%)** | |

The 35 left as DAX are all for a stated reason, mostly "depends on the viewer's selection"
(slicer-driven titles and what-if parameters, 20) and `ALLSELECTED` comparisons (8).

Two mistakes were found and fixed during this check, and are now covered by tests
(`ossie_hub/tests/test_dax.py`): choosing a slicer table as the fact because it was the only
related table, and choosing the `Date` dimension as the fact because time measures mention it
most.

**For a new report**, run the `pbix` command and read the summary and the measure report. A
low translated count is not an error; it means the report is mostly viewer-driven logic. The
data and relationships are unaffected, and the original DAX stays in the model for Power BI /
Fabric.

### 2.7 Clean up (optional)

Removing a schema is irreversible. List first, then drop only what you created:

> Show me everything inside `workspace.sales_dashboard` and, once I confirm, drop that schema.

---

## 3. Fabric → Ossie model → Databricks (model, data and metrics)

### 3.1 Convert the Fabric semantic model to an Ossie model and an ontology

**Code way**

```
python -m ossie_hub read-model fabric --connection fab-main --pick workspace=OssieApache_Workspace --pick semantic_model=Sales -o output_models/live_sales_ossie.yaml
```

As with the `.pbix`, this **one command produces and validates both** the Ossie model and the
ontology, and writes the measure report:

| Output | Location | Content |
|---|---|---|
| **Ossie model** | `output_models/live_sales_ossie.yaml` | 4 datasets, 3 relationships, 6 measures (all translated) |
| **Ontology** | `output_models/live_sales_ontology.yaml` | `SalesTransaction`, `Customer`, `Product`, `Location` and their relationships |
| **Measure report** | `output_models/live_sales_translation_report.md` | Each measure with its SQL, or why it was kept as DAX |

Source tables are recorded as `Lakehouse_1.Schema2.<table>`, which is where the data is read
from in the next step. The same switches apply: `--no-ontology`, `--ontology-out <file>`.

**Prompt way**

> Read the semantic model `Sales` from my Fabric workspace `OssieApache_Workspace` with the
> ossie_hub Fabric connector and write `output_models/live_sales_ossie.yaml`. It should also
> produce the ontology and the measure report and validate both. Summarise the datasets,
> relationships, ontology concepts, and measure coverage.

### 3.2 Send the model and the Fabric lakehouse data to Databricks

There are two equivalent ways. The second keeps the Ossie file as an explicit, reviewable step.

**Code way A — one command, from saved connections**

```
python -m ossie_hub transfer --from fab-main --to dbx-main --workspace OssieApache_Workspace --model Sales --catalog workspace --schema sales_from_fabric
```

Add `--execute` to run it. The lakehouse and schema are inferred from the model.

**Code way B — two steps through the Ossie file**

```
python -m ossie_hub migrate --to databricks --source fabric --workspace OssieApache_Workspace --lakehouse Lakehouse_1 --lake-schema Schema2 --from-yaml output_models/live_sales_ossie.yaml --fact orders --catalog workspace --schema sales_from_fabric --connection dbx-main
```

Dry run first; add `--execute` to run it.

What happens in both:

1. Rows are read **from the Fabric lakehouse** (Delta tables in OneLake), not from files.
2. Relationship keys that repeat in the data are made unique **in transit**; the first row is
   kept and the differing variants are recorded in an `Alternate_Records` column. In the sample
   data this collapses 32 repeated product IDs and 1 repeated postal code, so totals are not
   inflated by join fan-out.
3. Tables, the Metric View and the measures are created in Databricks and verified
   (e.g. total sales = 2,297,200.86).

**Prompt way**

> Transfer the Fabric model `Sales` (workspace `OssieApache_Workspace`) to Databricks using my
> saved connections `fab-main` and `dbx-main`: read the rows from the lakehouse, target catalog
> `workspace`, new schema `sales_from_fabric`. **Dry run first** and show me the row counts and
> which repeated keys were collapsed. Do not execute until I confirm.

Then:

> Execute it and show me the verification table.

---

## 4. Troubleshooting

| You see | Meaning and fix |
|---|---|
| `refusing to continue: [...] already contain rows` | The target schema already has data. Use a new schema name, or add `--overwrite` if replacing is intended. |
| `authentication/authorization failed` (Databricks) | Token revoked, expired or lacks access. Create a new one and re-run `connect add databricks …`. |
| Fabric sign-in loops or wrong tenant | Add the tenant (advanced): `connect add fabric --name fab-main --set auth_mode=interactive_browser --advanced`, then enter the Tenant ID (a code or domain, not an email). |
| `[Schema] ... 'semantic_model' was unexpected` (upstream validator) | The file is in the old wrapped form. Re-run the conversion, or just use it: our commands read both forms. |
| `the ontology is incomplete, nothing was sent` | An entity has no identifier or no table mapping. The message names it. Regenerate the ontology (section 2.2) and check the model has the columns. Nothing was written. |
| Ontology tables exist but a data table has no comment | The data tables were not in that schema when the ontology was pushed. Run 2.4b first, then push the ontology again (it refreshes). |
| Genie "For you" tab shows only 5 tables | It lists recent items only. Use the **All** tab (tables) and the **Metric views** tab. |
| `secret '...' is missing from the credential store` | The saved connection lost its secret. Re-run `connect add` for it. |
| `no saved connection 'x'` | Run `python -m ossie_hub connect list` and use an existing name. |
| `environment variable ... is not set` | A connection points at `env:NAME`. Set the variable, or re-add the connection and paste the secret. |
| Metric View rejected a join below a one-to-many join | Handled automatically: the bridge is exposed as a flattened `*_enriched` view and one Metric View is created per bridge. |
| `.pbix` yields no rows | The report is a live connection with no embedded data. Use section 3 from the Fabric model. |
| Quotes | Use double quotes around names with spaces, e.g. `--set "warehouse=Serverless Starter Warehouse"`. |

---

## 5. Known limitations (read before demoing)

1. **Measure translation is partial, by design** (section 2.6). A measure is translated only when
   it is expressible as a model-level SQL aggregate; everything else is kept as DAX with the
   reason in the measure report, never approximated. Across the six reports in this project that
   is 77 of 112 (68%); `Sales_Dashboard.pbix` is 23 of 31. Reports driven by slicer selections
   (what-if parameters, dynamic titles) translate least.
2. **The Ossie spec has no structured filter / window concept yet.** Filtered measures travel as
   SQL (`FILTER (WHERE …)`); prior-month windows travel as a Databricks-specific extension that
   other converters (e.g. Snowflake) drop.
3. **Databricks Metric Views** cannot slice by a dimension below a one-to-many join, so
   many-to-many bridges are modelled as separate views.
4. **Data movement is row-by-row batches** through SQL: fine for tens of thousands of rows, not for
   millions.
5. **Writing back to Fabric** exists (`python -m ossie_hub publish`) but is outside this guide.
6. **Not packaged or committed yet**: `ossie_hub/` is new and untracked in git.

---

## 6. Discussion: the ontology layer above the semantic model

*Proposal for discussion. The first prototype (option C) is built: see 6.5 and section 2.4c.*

### 6.1 The question

The target architecture has three layers:

```
Ontology            business concepts, relationships, verbalisations     (what things MEAN)
   │  mappings
Semantic model      datasets, joins, measures                            (how to COMPUTE)
   │
Data                tables                                               (the facts)
```

The semantic model and data already move from Power BI / Fabric to Databricks. The question from
the meeting is whether and how the **ontology layer** should move with them.

### 6.2 What exists today

- The ontology is an Ossie YAML file (concepts, relationships, mappings to semantic-model fields),
  generated by `tools/ossie_to_ontology.py` and validated by `ontology/ontology.json`.
- In **Fabric** there is a native Ontology item type (visible in the workspace as `Sales_Model`,
  with a graph model beside it), so Fabric has a home for this layer.
- In **Databricks**, to our knowledge there is no first-class "ontology" object. What exists:
  Unity Catalog comments and tags, informational primary/foreign keys, Metric Views (with
  comments, display names and synonyms), and Genie spaces that read those annotations.
  *This should be confirmed against current Databricks documentation before committing to a design.*

### 6.3 Options

| | Option | How | Pros | Cons |
|---|---|---|---|---|
| **A** | **Annotate** | Push business names, descriptions, synonyms and relationship text into Unity Catalog comments/tags and Metric View comments/synonyms/display names | Native; visible to Genie and BI users immediately; no new objects | Lossy: no mapping layer, multiplicity or verbalisations; scattered across objects |
| **B** | **Ontology as data** | Store concepts, relationships and mappings as governed Delta tables (e.g. `ontology.concepts`, `.relationships`, `.mappings`) | Lossless; queryable by SQL and AI agents; governed by Unity Catalog | Non-native: nothing enforces it; needs a sync tool |
| **C** | **A + B (hybrid)** | A for consumption, B for fidelity, YAML in git as the source of truth | Best of both; round-trippable | Two representations to keep in sync |
| **D** | External ontology / graph store | Push to a dedicated platform | Real reasoning and inference | Heavy; new platform; out of scope for now |
| **E** | **Keep in git only** | YAML stays a build artifact; reference its version via a tag | Zero effort | Not discoverable from Databricks |

**Recommendation:** start with **C**. Make the YAML the source of truth, push A for what business
users and Genie see, and B so the full structure is queryable and nothing is lost. Decide on D only
if a reasoning requirement appears.

### 6.4 Questions to settle with the team

1. **Purpose.** Documentation and discovery, AI grounding (Genie / agents), or reasoning and rules?
2. **Source of truth and direction.** Is the ontology authored in Fabric (native), in Ossie YAML, or
   in Databricks? Does it ever flow back?
3. **Enforcement.** Is it descriptive, or must constraints (`requires`, multiplicity) be checked?
4. **Scope.** One ontology per semantic model, or an enterprise ontology spanning several?
5. **Governance.** Who owns concept names and approves changes; how are versions tracked?
6. **Consumers.** Who actually reads it in Databricks: analysts, Genie, custom agents?

### 6.5 What could be prototyped quickly

**Done:** option C's first version exists as `python -m ossie_hub ontology push` (section 2.4c). It
writes four ontology tables and applies comments and tags in the same schema as the data.
The schema was connected to a Genie space (section 2.4d). **Still open:** the acceptance test:
ask the space a question using a business term (a synonym such as "client") and check it picks
the right table. Writing business names into the Metric View's own synonyms is not done either.

---

## 7. Quick reference

| Task | Command |
|---|---|
| Install | `python -m pip install -r ossie_hub/requirements.txt` |
| Connect Fabric | `python -m ossie_hub connect add fabric --name fab-main --set auth_mode=interactive_browser` |
| Connect Databricks | `python -m ossie_hub connect add databricks --name dbx-main --set host=<host> --set "warehouse=<wh>"` |
| List / test connections | `python -m ossie_hub connect list` / `connect test <name>` |
| .pbix with its own data → Ossie + ontology + report + CSV | `python -m ossie_hub pbix <file.pbix>` (creates `output_models/<file name>/`) |
| .pbix connected to Fabric → Ossie + ontology + report | `python -m ossie_hub pbix <file.pbix> --connection fab-main` (no data exported) |
| List workspaces / models / lakehouses / tables | `python -m ossie_hub options fabric <workspace\|semantic_model\|lakehouse\|lakehouse_table> --connection fab-main --pick workspace=<ws> [--pick lakehouse=<lh>]` |
| Fabric model → Ossie + ontology + report | `python -m ossie_hub read-model fabric --connection fab-main --pick workspace=<ws> --pick semantic_model=<model> -o <file_ossie.yaml>` |
| Ontology only (from any Ossie file) | `python tools/ossie_to_ontology.py <ossie.yaml> <ontology.yaml>` |
| Skip the ontology | add `--no-ontology` to `pbix` or `read-model` |
| Validate model | `python validation/validate.py <file.yaml>` |
| Validate ontology | `python validation/validate.py <file.yaml> --schema ontology/ontology.json` |
| Dry-run transfer (CSV) | `python -m ossie_hub migrate --to databricks --source csv --csv-dir <dir> --from-yaml <file> --fact <fact> --catalog <c> --schema <s> --connection dbx-main` |
| Dry-run transfer (Fabric) | `python -m ossie_hub transfer --from fab-main --to dbx-main --workspace <ws> --model <m> --catalog <c> --schema <s>` |
| Dry-run ontology push | `python -m ossie_hub ontology push --from-yaml <ontology.yaml> --catalog <c> --schema <s> --connection dbx-main` |
| Execute | add `--execute` (put it at the very end of the same command; `--overwrite` only if replacing is intended) |
| Hand the folder over | delete `ossie_hub/connectors/*/connections/*.json` and `.ossie_hub/` first (section 1.5) |
| Dashboard (HTML) | `python tools/build_dashboard.py --connection dbx-main --catalog <c> --schema <s> --out <file.html>` |
| Genie | Databricks UI: *Genie → New → All / Metric views tabs* (section 2.4d) |
