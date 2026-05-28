---
name: implement-cycle
description: Full autonomous implementation cycle for a GitHub issue on the NASA KG augmentation project. Fetches the issue, implements following edgraph patterns, tests, opens PR to the fork, and loops on failures until green. Use when the user says "implement this issue", "run the cycle", "ship this ticket", or passes a GitHub issue number/URL.
argument-hint: "[GitHub issue number, URL, or description of work]"
---

# Implement Cycle

Full autonomous implementation lifecycle for nasa-kg-augmentation: implement, test, PR to fork. Loops on failures.

## Input

<issue_reference> #$ARGUMENTS </issue_reference>

## Available Skills and Agents

Before starting, understand what tools are at your disposal:

### Implementation Skills
| Skill | When to use |
|-------|-------------|
| `/ce-work` | Primary implementation skill. Takes a plan/issue and executes systematically |
| `/tdd` | When the issue involves behavior changes that benefit from test-first development |
| `/ce-debug` | When you hit a bug during implementation or tests fail unexpectedly |
| `/diagnose` | When you encounter a hard bug that needs systematic root cause analysis |

### Quality Skills
| Skill | When to use |
|-------|-------------|
| `/ce-code-review` | After implementation, before opening PR. Runs structured review with persona agents |
| `/ce-simplify-code` | After implementation, to simplify and clean up changed code |

### Shipping Skills
| Skill | When to use |
|-------|-------------|
| `/ce-commit` | To create well-structured commits |
| `/ce-commit-push-pr` | To commit, push, and open a PR with good description |

### Agents (via Agent tool)
| Agent | When to use |
|-------|-------------|
| `tester` | Run tests and verify quality |
| `coder` | Implementation when you need a focused coding agent |
| `pr-code-reviewer` | Review the PR diff before shipping |
| `compound-engineering:ce-correctness-reviewer` | Review for logic errors and edge cases |
| `compound-engineering:ce-testing-reviewer` | Review for test coverage gaps |
| `compound-engineering:ce-performance-reviewer` | When changes touch Neo4j queries or batch loops |
| `compound-engineering:ce-security-reviewer` | When changes touch config, API keys, or external APIs |
| `compound-engineering:ce-data-integrity-guardian` | When changes affect graph data consistency or node/edge creation |

## Execution Workflow

### Phase 1: Issue Triage

1. **Fetch the issue** from the fork:
   ```
   gh issue view <number> --repo aaarrmiinnn/edgraph --json title,body,labels,assignees
   ```
2. **Read the issue body** completely. Understand acceptance criteria and blocked-by dependencies.
3. **Check blocked-by**: If the issue is blocked by another issue, check if that blocker is closed. If not, STOP and inform the user.
4. **Select skills and agents** for this issue based on the work type:
   - Data pipeline / ingest script -> `coder` agent + `tester` agent
   - API client / external integration -> `/tdd` + `tester` agent
   - Export / format generation -> `coder` agent + manual verification
   - Bug fix -> `/diagnose` first, then fix

Announce your skill/agent selection to the user before proceeding.

### Phase 2: Implementation

1. **Create a feature branch** from `main`:
   ```
   cd /Users/ventryaa-ai/Projects/nasa-kg-aumentation/edgraph
   git checkout main && git pull origin main
   git checkout -b feat/<issue-slug>
   ```

2. **Implement using selected skills.** Pass the issue body as the work description.
   - Follow edgraph's ingestor class pattern: `__init__` (config/logger/driver), `set_uniqueness_constraint()`, `process_files(batch_size)`, `add_data(tx, batch)`, `main()`
   - Use `uuid5(NAMESPACE_DNS, identifier)` for all globalId generation (consistent with edgraph)
   - Use `tqdm` for progress bars on batch operations
   - Place augmentation code in the appropriate directory within the project

3. **Run tests continuously** during implementation:
   ```
   cd /Users/ventryaa-ai/Projects/nasa-kg-aumentation/edgraph/src
   python -m pytest graph_ingest/tests/unit/ -x -q
   python -m pytest graph_ingest/tests/integration/ -x -q
   ```
   For new augmentation code tests:
   ```
   python -m pytest <test_path> -x -q
   ```

4. **If tests fail**, use `/diagnose` to find root cause. Do NOT move to Phase 3 with failing tests.

### Phase 3: Quality Gate

1. **Run code review** using the `pr-code-reviewer` agent on the current branch changes
2. **Run `/simplify`** to clean up
3. **Fix any issues** found by reviewers
4. **Verify all checks pass:**
   ```
   cd /Users/ventryaa-ai/Projects/nasa-kg-aumentation/edgraph/src
   python -m pytest graph_ingest/tests/ -x -q
   ```

### Phase 4: PR to Fork

1. **Commit and push:**
   - Create a well-structured commit referencing the issue number
   - Push to the fork: `git push -u origin feat/<issue-slug>`

2. **Open PR** targeting `main` on `aaarrmiinnn/edgraph`:
   ```
   gh pr create --repo aaarrmiinnn/edgraph --base main --head feat/<issue-slug> \
     --title "<title>" --body "<body with issue reference and acceptance criteria>"
   ```
   Apply relevant labels: `feat`/`fix`/`refactor` + `backend` + priority

3. **If CI exists and fails:**
   - Read the failing check logs: `gh run view <run-id> --log-failed`
   - Fix the issue, push the fix
   - Go back to step 2

4. **Merge the PR:**
   ```
   gh pr merge <pr-number> --repo aaarrmiinnn/edgraph --squash --delete-branch
   ```

## Project-Specific Rules

### Git Remotes
- `origin` -> `aaarrmiinnn/edgraph` (fork — all pushes go here)
- `upstream` -> `nasa/edgraph` (DO NOT push, read-only reference)
- All issues and PRs are on `aaarrmiinnn/edgraph`

### Code Patterns
- All ingest scripts follow the class pattern in `src/graph_ingest/ingest_scripts/`
- Shared utilities in `src/graph_ingest/common/` (core.py, config_reader.py, neo4j_driver.py, logger_setup.py)
- Config at `src/graph_ingest/config/config.json` with env var overrides
- Tests mirror the source structure under `src/graph_ingest/tests/unit/` and `integration/`
- Batch size default: 100 items per transaction

### Neo4j Conventions
- All nodes use `globalId` as primary key (UUID5)
- Use `MERGE` (not `CREATE`) to ensure idempotency
- Set uniqueness constraints before ingesting data
- Use `session.execute_write()` for write transactions

### Output Compatibility
- Augmented graph must be exportable to: `graph.json` (JSONL), `graph.graphml`, `graph.cypher`
- Must match the format of https://huggingface.co/datasets/nasa-gesdisc/nasa-eo-knowledge-graph

## Failure Recovery Rules

- **Test failure during implementation**: Use `/diagnose`. Do not proceed until green.
- **CI failure on PR**: Read logs, fix, push. Do not merge red PRs.
- **Maximum loop iterations**: If you've looped 3 times on the same failure, STOP and ask the user for guidance.

## Completion Checklist

Before reporting success, verify:
- [ ] All acceptance criteria from the issue are met
- [ ] Tests pass locally
- [ ] PR is merged to fork
- [ ] GitHub issue can be closed (inform user, don't auto-close)
