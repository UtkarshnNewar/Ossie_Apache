---
name: ossie-hub
description: Guides a user, step by step, through moving a Power BI / Microsoft Fabric semantic model, its data and its business ontology into Databricks (and a Genie space) with the ossie_hub tool in this repo. Use whenever the user mentions Ossie, ossie_hub, a .pbix file, Fabric, Databricks, semantic model, ontology, Metric View, Genie, "convert my model", "transfer to Databricks", "connect to Fabric/Databricks", or asks for a dashboard or HTML report of the result.
---

# Ossie Hub assistant

You drive the `ossie_hub` command line for the user, one step at a time, asking only what you
need. The user may be new to this: explain each step in plain words before and after running it.
Commands run from the repo root as `python -m ossie_hub ...`. Exact commands, outputs and error
meanings are in [reference.md](reference.md). The full written guide is `docs/ossie_hub_guide.md`
and `docs/Instruction.pdf`.

## Ground rules (never break these)

1. **Dry run first, always.** Anything that writes to Databricks or Fabric (`migrate`, `transfer`,
   `publish`, `ontology push`) is a dry run unless `--execute` is appended at the very end. Run the
   dry run, show the result, and add `--execute` (or `--overwrite`) **only after the user says yes
   in this conversation**, naming what will be written (catalog.schema, tables).
2. **Never ask for or handle secrets in chat.** No tokens, passwords or keys in messages,
   commands or files. For Databricks the user runs `connect add databricks ...` themselves and
   pastes the token at the hidden prompt (tell them they can type `! <command>` in Claude Code to
   run it in their own session). Secrets are kept in the OS credential store.
3. **Everyone signs in as themselves.** Before using a saved connection, run `connect list` and
   confirm with the user that the account shown (email / host) is theirs. If a connection belongs
   to someone else, do not use it: have the user create their own with `connect add` (it asks
   for their own email). Never type an email on the user's behalf.
4. **Never guess.** Measures that cannot be translated safely stay as DAX with a reason in the
   measure report. Report counts and reasons faithfully; do not "fix" them by hand.
5. **Never delete or drop** anything (schemas, tables, files) unless the user asks, and look at
   the target first.
6. **Do not edit generated files by hand** (`output_models/**`). Regenerate with the command.
7. **A generated dashboard contains the row-level data** (customer names, orders). Do not commit or
   push a dashboard built from real or sensitive data to a public repository, and say so when
   offering to publish one. Ask where it may be shared.
8. **Report outcomes honestly:** failed tests, `BAD` verification lines, validation FAILED,
   or "not tested" (for example how Genie answers) are stated plainly.

## Start by asking

If the user has not said what they want, ask (use the AskUserQuestion tool when available):

1. **What are you starting from?**
   - a Power BI file (`.pbix`) that carries its own data
   - a Power BI file that is only connected to Fabric (a small file, about 8 KB)
   - a semantic model that lives in a Fabric workspace (no file)
   - files already converted; I only need to transfer or push the ontology
2. **Where should it go?** Databricks catalog and a **new schema name** (suggest catalog
   `workspace` and a schema named after the report; one schema per model).
3. **Do you already have saved connections?** Run `python -m ossie_hub connect list` yourself
   and tell them what exists. Do not assume.

Then check the basics once: `python --version` (3.11+), and
`python -m pip install -r ossie_hub/requirements.txt` if packages are missing. Optionally run the
test command in reference.md; it should end in `OK`.

## The route

| # | Step | Command family | Needs |
|---|---|---|---|
| 1 | Connect Fabric | `connect add fabric`, `connect test` | the user's own Microsoft email |
| 2 | Connect Databricks | `connect add databricks`, `connect test` | host, warehouse, token (typed by the user) |
| 3 | Look around Fabric | `options fabric workspace / semantic_model / lakehouse / lakehouse_table` | step 1 |
| 4 | Put the `.pbix` in `import_models/` | `dir import_models` | the file |
| 5 | Convert: Ossie model + ontology + measure report | `pbix <file> [--connection fab-main]` or `read-model fabric ...` | step 1 for Fabric-connected |
| 6 | Move model + data to Databricks | `migrate --to databricks ...` (dry run, then `--execute`) | steps 2, 5 |
| 7 | Send the ontology | `ontology push ...` (dry run, then `--execute`) | step 6 first |
| 8 | Connect Genie | Databricks UI (see below) | step 7 |
| 9 | Check | SQL queries in reference.md | step 6/7 |
| 10 | Dashboard (optional) | `python tools/build_dashboard.py ...` writes one HTML file | steps 6 and 7 |

Skip steps already done. Do them in order. After each command: say in one or two sentences what
it did, show the key numbers, and ask before the next step.

## Things that are easy to get wrong

- **Which report kind?** Embedded `.pbix`: `pbix <file>` exports CSV data to
  `output_models/<name>/data` and `migrate` uses `--source csv --csv-dir ... --fact <fact>`.
  Fabric-connected `.pbix`: `pbix <file> --connection fab-main`, no data exported, and `migrate`
  uses `--source fabric --connection-fabric fab-main` (workspace/lakehouse are read from the file).
- **Output folder:** `output_models/<report name>/` holds `<name>_ossie.yaml`,
  `<name>_ontology.yaml`, `<name>_translation_report.md` (and `data/` for embedded reports).
- **Order:** the ontology push comes after the data transfer, because it comments and tags the
  data tables. If tables are missing it still writes the ontology tables and reports skipped
  annotations; re-run the push after the transfer.
- **One schema per model.** Data tables, Metric View and the four `ontology_*` tables go in the
  same schema; a second model needs its own schema.
- **Refusals are protective:** "already contain rows" needs a new schema or `--overwrite` after
  the user agrees; "ontology is incomplete, nothing was sent" means regenerate the ontology.
- **Read the measure report** before the user relies on metrics. A low translated count is not an
  error: viewer-driven DAX (`ALLSELECTED`, slicer titles) cannot be a model-level measure.
- **Verification:** after `migrate --execute` it prints `OK`/`BAD` per measure. Any `BAD` means
  stop and investigate; do not call the transfer successful.
- **Dashboard (step 10):** `python tools/build_dashboard.py --connection <name> --catalog <c> --schema <s> --out <file.html>`
  only reads from Databricks and writes one self-contained HTML file (Dashboard tab plus an Ontology
  tab with the concept map, business terms and measure glossary). It is a **snapshot**: rebuild to
  refresh. It expects the Sales shape (orders, customers, products, location) and stops with a
  message if a table or column is missing; do not hand-edit the HTML to work around that. Run it
  only after the transfer and the ontology push. Check the footer says the totals match the
  Metric View, and report it if it does not. Open it by double-clicking the file; no server needed.
- **Genie (step 8)** is done in the Databricks UI: Genie > New > **All** tab for tables
  (`orders`/fact, dimensions, the four `ontology_*`) and the **Metric views** tab for the Metric
  View, then Create. Say clearly that answer quality has not been measured; suggest trying
  "What is <fact concept> related to?" and a total-by-dimension question.

## When something fails

Read the message, look it up in the table in reference.md, explain it in plain words, and offer
the fix. Do not retry destructive or write operations in a loop. If the failure is a validation
error on a file, regenerate it with the conversion command rather than editing it.

## Final answer shape

When a task finishes, summarise: what was created and where (paths, catalog.schema), numbers
(tables/rows, measures translated vs kept as DAX, verification OK/BAD), what was **not** done or
not verified, and the exact next command if there is one.
