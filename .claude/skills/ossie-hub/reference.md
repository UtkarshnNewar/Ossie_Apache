# Ossie Hub: command reference

Run everything from the repo root. `<...>` are placeholders. Names with spaces need double quotes.
**Every write is a dry run until `--execute` is appended at the very end of the same command.**

## Install and self-test

```
python -m pip install -r ossie_hub/requirements.txt
python -m unittest ossie_hub.tests.test_dax ossie_hub.tests.test_pbix_files ossie_hub.tests.test_fabric ossie_hub.tests.test_targets ossie_hub.tests.test_connections ossie_hub.tests.test_ontology_push ossie_hub.tests.test_document
```
Expect `Ran 68 tests ... OK` (offline, seconds).

## Connections (personal; each person adds their own)

```
python -m ossie_hub connect list
python -m ossie_hub connect add fabric --name fab-main --set auth_mode=interactive_browser
python -m ossie_hub connect test fab-main
python -m ossie_hub connect add databricks --name dbx-main --set host=<host-without-https> --set "warehouse=<warehouse name or id>"
python -m ossie_hub connect test dbx-main
python -m ossie_hub connect remove <name>
```
- `connect add fabric` asks for the user's own Microsoft email (optional, not a password) and
  opens a browser sign-in on `connect test`. Tenant ID is advanced: add `--advanced` only if the
  sign-in picks the wrong organisation; it is a GUID or domain, never an email.
- `connect add databricks` asks for the access token at a hidden prompt. Never take it in chat.
- Saved connections live in `ossie_hub/connectors/<fabric|databricks>/connections/*.json`
  (no secrets). Another person's file there must not be reused.

## Look around Fabric

```
python -m ossie_hub options fabric workspace --connection fab-main
python -m ossie_hub options fabric semantic_model --connection fab-main --pick workspace=<ws>
python -m ossie_hub options fabric lakehouse --connection fab-main --pick workspace=<ws>
python -m ossie_hub options fabric lakehouse_table --connection fab-main --pick workspace=<ws> --pick lakehouse=<lh>
```

## Convert (writes Ossie model + ontology + measure report, validated)

```
python -m ossie_hub pbix import_models/<file>.pbix                          # carries its own data; exports CSV
python -m ossie_hub pbix import_models/<file>.pbix --connection fab-main    # connected to Fabric; no data exported
python -m ossie_hub read-model fabric --connection fab-main --pick workspace=<ws> --pick semantic_model=<model> -o output_models/<name>_ossie.yaml
```
Switches: `--no-ontology`, `--out-dir <folder>`, `--name <model name>`.
Output: `output_models/<file name>/` with `<name>_ossie.yaml`, `<name>_ontology.yaml`,
`<name>_translation_report.md`, and `data/` (embedded reports only).
Validate by hand: `python validation/validate.py <file>`; ontologies add
`--schema ontology/ontology.json`.

## Transfer model + data to Databricks (one schema per model)

Fabric-connected report, rows read from the Fabric lakehouse:
```
python -m ossie_hub migrate --to databricks --source fabric --connection-fabric fab-main --from-yaml output_models/<name>/<name>_ossie.yaml --catalog <catalog> --schema <schema> --connection dbx-main
```
Report with its own data, rows from CSV:
```
python -m ossie_hub migrate --to databricks --source csv --csv-dir output_models/<name>/data --from-yaml output_models/<name>/<name>_ossie.yaml --fact <fact table> --catalog <catalog> --schema <schema> --connection dbx-main
```
Straight from a Fabric model, one command: `python -m ossie_hub transfer --from fab-main --to dbx-main --workspace <ws> --model <model> --catalog <c> --schema <s>`.

Add `--execute` to write. `--overwrite` only if replacing existing rows is intended;
`--semantic-only` re-creates views and Metric Views without touching data.
Expected after execute: row counts, then a verification table with `OK`/`BAD` per measure.

## Push the ontology (after the transfer)

```
python -m ossie_hub ontology push --from-yaml output_models/<name>/<name>_ontology.yaml --catalog <catalog> --schema <schema> --connection dbx-main
```
Add `--execute` to write. Creates `ontology_info`, `ontology_concepts`, `ontology_relationships`,
`ontology_mappings` and applies comments/tags to the data tables. `--no-annotate` skips the
comments. A table with one of these names that ossie_hub did not create is never replaced
without `--overwrite`. An incomplete ontology is refused before anything is sent.

## Dashboard (one HTML file, read-only on Databricks)

```
python tools/build_dashboard.py --connection dbx-main --catalog <catalog> --schema <schema> --out output_models/<name>/<name>_dashboard.html
```
Optional: `--title "<page title>"`, `--metric-view <name>` (default: the first Metric View in the schema).
- Needs the data tables, the Metric View and, for the Ontology tab, the four `ontology_*` tables
  (run the transfer and the ontology push first). Without them it still works but says so.
- Expects the Sales shape: `orders`, `customers`, `products`, `location` with the standard columns.
  Otherwise it stops: `error: <table> is missing [...]`.
- It refuses if a join would drop or duplicate rows (row count must equal `orders`).
- The file embeds every order line (customer names included): keep real data out of public repos.
- The page template is `tools/dashboard_template.html`; the footer compares totals with the Metric View.

## Checks in Databricks (SQL editor)

```sql
SHOW TABLES IN <catalog>.<schema>;
SELECT COUNT(*) FROM <catalog>.<schema>.<fact table>;
SELECT concept, relationship, related_concept, verbalization
FROM <catalog>.<schema>.ontology_relationships WHERE relationship_kind = 'link';
SELECT table_name, comment FROM <catalog>.information_schema.tables WHERE table_schema = '<schema>';
```
Metric View measures are read with `MEASURE(<name>)`.

## Messages and meaning

| You see | Meaning and fix |
|---|---|
| `refusing to continue: [...] already contain rows` | Target schema has data. New schema name, or `--overwrite` if the user agrees |
| `the ontology is incomplete, nothing was sent` | An entity lacks an identifier or mapping (named in the message). Regenerate with `pbix` / `read-model`; nothing was written |
| `no saved connection 'x'` | `connect list`; add one with `connect add <type> --name x` |
| `secret '...' is missing from the credential store` | The connection was made on another computer or lost its secret: `connect add` again |
| Databricks authentication/authorization failed | Token revoked/expired or no access: new token via `connect add databricks` |
| Fabric sign-in picks the wrong organisation | `connect add fabric ... --advanced` and give the Tenant ID |
| `[Schema] ... 'semantic_model' was unexpected` | Old wrapped file; re-run the conversion (our commands read both shapes) |
| `.pbix` yields no data | It is a live connection: use `pbix <file> --connection fab-main` |
| Ontology tables exist but a data table has no comment | Data was not in the schema at push time: do the transfer, then push again |
| `error: <table> is missing [...]` from build_dashboard | The schema is not the Sales shape (or the transfer is incomplete). Check the tables with `SHOW TABLES`; do not edit the page |
| Dashboard footer says totals differ from the Metric View | Stop and investigate (data changed after the build, or a join problem); rebuild first |
| Genie "For you" tab shows only a few tables | Use the **All** tab and the **Metric views** tab |

## Facts worth knowing

- Internal model shape is `semantic_model: [...]`; files on disk are written in the current
  Apache Ossie form (one model per document, no wrapper) and both shapes are read.
- The Databricks Metric View generator is a vendored copy of the upstream Python converter in
  `ossie_hub/vendor/ossie_databricks` (upstream replaced it with a Java one).
- The fact table is the table most measures are written against, excluding pure dimensions.
- A repeated key in a dimension is made unique in transit (first row kept; variants recorded in
  `Alternate_Records`) so totals are not inflated.
- Sales_Dashboard sample: 23 of 31 measures translate; the other 8 use `ALLSELECTED`.
- Genie answer quality for business synonyms has not been measured; Metric View synonyms are not
  written yet.
- The dashboard is a snapshot of the data at build time; the Ontology tab reads the pushed
  `ontology_*` tables, so it reflects what is in Databricks, not the YAML file.
- Handover: before giving a folder to someone, delete the personal connection files and
  `.ossie_hub/` (guide section 1.5). Sharing through Git never includes them.
