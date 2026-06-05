# Edge-naming convention

This project distinguishes two layers of relationships:

- **Asserted edges** — primary facts ingested from sources (CMR, GCMD, the publication citation DB, OpenAlex). These are the base graph; **do not rename them** (they are in the published HF dataset and downstream queries depend on them).
- **Derived edges** — computed from the graph's own structure (no external source). These follow the convention below and are always tagged so they can never be confused with asserted facts.

## Asserted layer (existing — fixed)

| Edge | Direction |
|---|---|
| `AUTHORED_BY` | Publication → Author |
| `AFFILIATED_WITH` | Author → Institution |
| `CITES` | Publication → Publication |
| `USES_DATASET` | Publication → Dataset |
| `HAS_DATASET` | DataCenter → Dataset |
| `HAS_PLATFORM` | Dataset → Platform |
| `HAS_INSTRUMENT` | Platform → Instrument |
| `HAS_SCIENCEKEYWORD` | Dataset → ScienceKeyword |
| `HAS_APPLIEDRESEARCHAREA` | Publication → ScienceKeyword |
| `HAS_SUBCATEGORY` | ScienceKeyword → ScienceKeyword |
| `OF_PROJECT` | Dataset → Project |

(The asserted layer is internally inconsistent — mixes `HAS_*`, passive, active-verb, prepositional. We preserve it as-is and do not extend its inconsistencies.)

## Rules for derived edges

1. **`UPPER_SNAKE_CASE`**, reads as subject → verb → object in the arrow direction.
2. **Symmetric co-occurrence → `CO_<participle>_WITH`** (e.g. `CO_USED_WITH`). Store **one direction only** (canonicalize by `globalId` ordering), query undirected, carry a **`weight`** (co-occurrence count).
3. **Directed actor → resource/topic → present-tense active verb** (e.g. `WORKS_WITH_DATASET`, `RESEARCHES`). Present tense matches the asserted active verbs (`CITES`, `USES_DATASET`) and KG convention (schema.org `worksFor`); it asserts a standing fact, not a timestamped event.
4. **Every derived edge carries `derived: true`** (plus `weight`/year properties as needed), so it is always filterable and never mistaken for an asserted fact.
5. **Reuse one type across different actor labels when the meaning is identical** (e.g. `WORKS_WITH_DATASET` for both Author→Dataset and Institution→Dataset). The endpoint labels disambiguate. Only avoid reuse across *different* semantics (asserted vs derived).
6. **No near-homonyms / tense-twins** (e.g. never add `USED_DATASET` next to `USES_DATASET`).
7. **Idempotent + recomputable**: derived edges are (re)built by the `compute_derived_edges` pipeline stage; re-running recomputes weights from the current graph.

## Derived layer (planned / building)

| Edge | Direction | Meaning | Status |
|---|---|---|---|
| `CO_USED_WITH` | Dataset – Dataset (symmetric) | datasets co-used in the same publications (`weight`) | building |
| `CO_AUTHORED_WITH` | Author – Author (symmetric) | collaboration (`weight`) | planned |
| `CO_CITED_WITH` | Publication – Publication (symmetric) | co-citation similarity (`weight`) | planned |
| `WORKS_WITH_DATASET` | Author → Dataset, Institution → Dataset | derived data usage | planned |
| `RESEARCHES` | Author → ScienceKeyword, Institution → ScienceKeyword | derived expertise/focus | planned |
