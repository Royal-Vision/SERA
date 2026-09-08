# SERA — code reviewer architecture and build guide

[Project documentation](../README.md)

Status: implementation blueprint from the September 7, 2026 design discussion.
This describes the intended small reviewer, not verified existing functionality.
The developer implements it; this guide supplies boundaries and acceptance criteria.

This is the single guide for the small reviewer: scope, architecture, review
behavior, build order, and the first practical exercise.

## Contents

1. [Scope and documentation authority](#1-scope-and-documentation-authority)
2. [User workflow](#2-user-workflow)
3. [Components and execution](#3-components-and-execution)
4. [Shared contracts and ownership](#4-shared-contracts-and-ownership)
5. [Failure policy](#5-failure-policy)
6. [Rules that make drift visible](#6-rules-that-make-drift-visible)
7. [Build sequence: developer deliverables](#7-build-sequence)
8. [Start here: first Git milestone](#8-start-here-first-git-milestone)
9. [Later expansion](#9-later-expansion)

Read sections 1–3 to understand the design. Section 7 turns it into developer
deliverables; section 8 walks through the first one. Use the contracts and failure
rules as references while you build.

## 1. Scope and documentation authority

Build a local Python CLI that reviews committed changes on the current branch
against an explicitly selected target and prints an evidence-backed report.
Initially support Python source files. Always list excluded files and incomplete checks.

This guide defines the current reviewer scope and build order. Consult the
[agent runtime reference](../agent/README.md) for provider/tool boundaries and
[future platform decisions](../platform/README.md) when an additional capability
becomes necessary. These references do not add requirements to v1. Older competing
plans have been consolidated or removed; do not treat arbitrary Markdown notes as
current architecture rules.

Existing provider code under `app/agent/providers/` is a potential integration
point, not an assumed complete interface. Inspect and adapt it when reaching the
model milestone. Do not rebuild authentication to implement the first Git milestone.

## 2. User workflow

The intended CLI interface is:

```bash
sera review --target origin/canary
```

This command is a target interface, not a currently available command.
The target is required; never assume `main` or infer intent from branch activity.
Use local Git references. The developer fetches updates explicitly before reviewing.
Report that reference freshness is not guaranteed by SERA.

The reviewer reads committed objects only. Staged, unstaged, and untracked content
are excluded and that exclusion is displayed. Do not stash, reset, check out, or
modify the developer's files to perform a review.

## 3. Components and execution

One application, with a LangGraph workflow coordinating four stages:

```text
CLI -> prepare -> collect evidence -> analyze -> finalize -> terminal report
```

These are responsibilities, not separate services. Start with ordinary functions
and contracts; wire them into LangGraph once their behavior is understood.

| Component | Owns | Does not own |
| --- | --- | --- |
| CLI | Arguments, local configuration, terminal rendering, exit status | Git comparison rules or finding policy |
| Repository access | Commit resolution, merge base, diffs, committed file reads | Model calls or publication |
| Evidence collection | Hunk locations, Python symbol context, analyzer results, coverage | Unlimited repository exploration |
| Model analysis | Review instructions, bounded evidence packet, candidate parsing | Choosing a new target or reading arbitrary files |
| Provider adapter | Model request/response translation and transport | Repository access or review policy |
| Finding validation | Schema, location and evidence checks, duplicate consolidation | Proving semantic correctness automatically |
| LangGraph coordinator | Stage order, shared state, failure routing and budgets | Implementing Git commands or provider protocols |

Keep review contracts independent of CLI, provider SDKs, and LangGraph. The
coordinator calls the components through their interfaces. Provider adapters and
repository helpers must not import the graph or terminal renderer.

### Prepare

1. Validate that the requested directory is a Git working repository.
2. Resolve `HEAD` and the explicit target to full commit IDs exactly once.
3. Calculate their merge base. Fail clearly for missing refs or unrelated history.
   If multiple merge bases exist, report unsupported history in v1 rather than
   silently selecting one. Shallow history may require the developer to fetch more.
4. Record the target label and all three commit IDs in `ReviewSnapshot`.
5. Obtain changed paths, statuses, hunks, and baseline/new line coordinates.

Review the aggregate difference from the merge base to the change commit. There
is no fixed number of commits to load and no need to send every commit to the model.
This assesses the branch's proposed change, not a simulated merge with the latest
target. Merge-result analysis is deferred and this limitation belongs in the report.

Read every source excerpt from one of the recorded commits and label its revision.
A moving branch must not change the evidence halfway through a review.

### Collect evidence

Start with changed Python functions/classes and bounded surrounding source.
For changes outside a symbol, use a bounded module excerpt. Python AST can identify
symbol ranges without importing or executing the reviewed code. Full call-graph
resolution and automatic retrieval of arbitrary callers are later improvements.

Handle added and deleted files as having only one available side. Preserve both
old and new locations for renames. Mark binary files, unsupported languages,
submodules, symlinks, parse failures, and oversized inputs as excluded or partial;
do not follow links into the live filesystem. Hunk parsing must preserve line
coordinates, including deletion-only hunks.

Add one explicit static analyzer before calling v1 complete. A possible first
adapter runs a narrow set of Ruff correctness checks on captured Python source,
with pinned configuration, without executing repository code. Record analyzer
version and configuration. Do not automatically load arbitrary project plugins.
Running the same checks on baseline source helps identify pre-existing diagnostics.
Keep deterministic findings distinguishable from model findings in the report.

Set configurable limits for source bytes, file count, model input/output, and
execution time. Record effective limits in the result. If context does not fit,
use a documented deterministic selection order and list exclusions; do not claim
complete coverage. Never let truncation silently produce a misleading clean result.

### Analyze

Begin with one bounded model request for a small review. No autonomous tool loop,
subagents, filesystem mutation, or arbitrary shell access. An empty finding list
is a valid response. Large-change chunking can follow once the small path works.

The request includes the fixed diff, selected evidence, analyzer results, and an
optional developer change description. A rules file is authoritative only when
explicitly selected by the developer; record its identity and content digest.
Other documents are contextual evidence. Distinguish declared rules from inferred
architecture; a new framework is not automatically a violation.

All model-bound content passes through a configured data policy before dispatch.
Remote source transmission must be explicitly enabled in local configuration;
there is no silent switch to a remote provider. Repository text is untrusted task
data and cannot grant tool permissions or override the review instructions.

Ask for concrete defects introduced by the change, with a trigger, consequence,
location, and evidence references. Avoid speculative architecture claims and
pre-existing issues. Initial categories: correctness, security, performance, and
explicit architecture-rule violations. Start with high/medium/low severity;
severity reflects impact, not confidence.

### Finalize

Validate candidate structure and check that referenced evidence IDs, paths,
revisions, and line ranges exist in the supplied evidence. Require a stated link
to the proposed change; a valid location alone does not establish causation.
Allow a baseline location for removed code and label that side in the report.
Consolidate exact duplicates using normalized location and claim; do not merge
unrelated issues just because they share a line. Reject invalid candidates with
recorded reasons rather than silently treating invalid output as no findings.

These checks reduce malformed and unsupported output; they do not prove findings
correct. Assess semantic quality with human-reviewed examples.

Report findings, snapshot IDs, checks performed, exclusions, effective limits,
model/analyzer configuration, and errors. Distinguish:

- `complete`: all planned supported checks completed within the declared scope.
- `partial`: usable analysis exists but a stage failed or relevant scope was skipped.
- `failed`: no trustworthy review result could be constructed.

Completion is independent of finding count. An empty diff completes without a
model request. Unsupported-only changes produce an explicit no-supported-coverage
result, not a claim that the repository is defect-free.

## 4. Shared contracts and ownership

Choose Python representations yourself; define the meaning before the classes.

| Contract | Minimum information |
| --- | --- |
| `ReviewRequest` | Repository root, target ref, optional description/rules, configuration |
| `ReviewSnapshot` | Target label, target commit, change commit, merge-base commit |
| `ChangeSet` | Old/new paths, statuses, hunks, baseline/new line coordinates |
| `ReviewEvidence` | Stable evidence IDs, source excerpts with revisions/locations, analyzer results, exclusions |
| `CandidateFinding` | Origin, category, title, explanation, severity, location side/range, evidence IDs |
| `ReviewResult` | Snapshot, findings, stage outcomes, coverage, errors, effective configuration |

The recorded Git objects are the authority for source. Graph state owns temporary
execution progress; `ReviewResult` is the finalized output of the run. Terminal
text is a rendering of that result. Keep v1 state in memory and allow rerunning
after a crash. No database, cache, or persistent checkpoint is required initially.

## 5. Failure policy

| Failure | Behavior |
| --- | --- |
| Missing Git executable, repository, target, or usable merge base | Stop preparation; clear error; no model call |
| Missing or unreadable committed source | Record affected coverage; never substitute a working-tree file |
| Analyzer timeout/unavailable | Record failed check; continue where useful; result is partial |
| Provider timeout, rejection, or malformed response | Preserve deterministic results; partial or failed outcome; never report clean success |
| Budget exceeded | Stop affected work and report incomplete coverage |
| User cancellation | Stop pending work, clean temporary artifacts, report cancellation distinctly |

Keep v1 retries bounded; start with none at the application layer and account for
any provider SDK retries in the overall timeout. Credentials and raw source should
not appear in diagnostic logs by default.

## 6. Rules that make drift visible

| Rule | Property protected | Verification |
| --- | --- | --- |
| Explicit target and frozen commit IDs | Stable review scope | Move a branch during a fixture review; evidence remains unchanged |
| Committed reads through repository access | Consistent source and read-only review | Edit the working tree; captured evidence still matches the commit |
| Provider adapters cannot fetch repository context | Controlled data access | Dependency/interface review |
| Every publication path consumes finalized results | Common validation policy | Exercise CLI against valid and malformed candidate responses |
| Exclusions and failures are visible | Honest coverage | Analyzer, parser, and budget failure cases |
| Documentation authority is explicit | Avoid stale plans becoming enforced rules | Conflicting notes do not override selected architecture rules |

When changing a rule, record the reason and affected checks here. Do not call an
intentional design change drift merely because older documentation differs.

## 7. Build sequence

### What you build as the developer

Build one usable step at a time. The file names below are a suggested target layout,
not files already implemented. Create a module when its milestone needs it; do not
start by filling the repository with empty placeholders. You write the implementation.

| Suggested location | What you implement | First needed |
| --- | --- | --- |
| `app/review/__init__.py` | A lightweight package initializer | Milestone 1 |
| `app/review/contracts.py` | Request, snapshot, changes, evidence, findings, result, and configuration types; add them progressively | Milestone 1 |
| `app/review/repository.py` | Checked Git subprocess calls, ref resolution, merge-base selection, file statuses, committed reads | Milestone 1 |
| `app/review/__main__.py` | Parse CLI arguments, assemble dependencies, call the workflow, render output, set exit status | Milestone 1 |
| `app/review/diff.py` | Convert a patch into hunks with old/new coordinates | Milestone 2 |
| `app/review/context.py` | Select Python symbol context, assign evidence IDs, enforce input limits, record exclusions | Milestone 2 |
| `app/review/analysis.py` | Build review requests for a provider, apply data policy, parse model candidates | Milestone 3 |
| `app/review/validation.py` | Validate candidates against evidence and consolidate exact duplicates | Milestone 3 |
| `app/review/report.py` | Render a finalized result, including coverage and failures | Milestone 3 |
| `app/review/workflow.py` | LangGraph state, stage wiring, budgets, and failure routing | Milestone 4 |
| `app/agent/providers/` | Adapt one existing provider to the review interface after inspecting its behavior | Milestone 4 |
| `app/review/analyzers.py` | Run one configured analyzer and normalize its diagnostics | Milestone 5 |
| `tests/review/` | Temporary Git fixtures, fake provider responses, and behavior checks | Throughout |

Keep low-level Git access in `repository.py`, pure patch interpretation in `diff.py`,
and context selection in `context.py`. The CLI is the assembly point; it may know
which provider and workflow to construct. The report renderer only knows results.
The model stage requests inference through an interface; provider implementations
must not import the review workflow. Tests and fixtures do not become dependencies
of application code.

### Milestone 1 — repository preparation

- [ ] Define the minimal request, snapshot, and changed-file records from section 4.
- [ ] Implement Git calls with argument lists, checked exit codes, explicit working
  directory, and timeouts. Distinguish expected Git failures from programming errors.
- [ ] Resolve the explicit target and `HEAD` once; require one usable merge base.
- [ ] Parse NUL-delimited file statuses, including rename old/new paths.
- [ ] Add a minimal entry point and print the snapshot and changed-file summary.
- [ ] Verify behavior against temporary Git repositories using section 8's examples.

Input: repository path and target reference. Output: fixed snapshot and file changes.
No model or graph is necessary yet. The provisional development interface is
`python -m app.review --repo <path> --target <ref>` once you implement the package.
The packaged `sera review` command comes later; it is not installed by these docs.

**Completion check:** the summary is correct even when the target has independent
commits and the working tree contains uncommitted edits. Missing targets fail clearly.
Use [section 8](#8-start-here-first-git-milestone) as the hands-on exercise.

### Milestone 2 — diff and evidence

- [ ] Add hunks with baseline/new start lines and line counts to `ChangeSet`.
- [ ] Handle additions, deletions, renames, and deletion-only hunks without inventing
  new-side locations for removed code.
- [ ] Read source by commit ID and path; never substitute working-tree content.
- [ ] Select enclosing Python symbols using AST, with a bounded module fallback.
- [ ] Assign evidence IDs and record revision, path, side, line range, and content.
- [ ] Implement context limits and exclusion reasons for unsupported/oversized input.

Input: snapshot and change set. Output: `ReviewEvidence`. Print or inspect this
packet locally before sending anything to a provider. Do not add a vector database
or whole-repository call graph to complete this milestone.

**Completion check:** every excerpt matches the recorded Git object and its line
range. Parse failures and skipped files are visible. Changing a live file cannot
change already captured evidence.

### Milestone 3 — candidates to report, using a fake provider

- [ ] Define the provider-facing request/response contract and candidate schema.
- [ ] Write review instructions that request introduced defects with evidence and
  accept an empty findings list. Keep repository content separate from instructions.
- [ ] Create a fake provider in the test fixtures with valid, empty, malformed,
  duplicate, and failed responses. This is for testing the pipeline, not a real review.
- [ ] Implement schema, evidence-ID, revision, path, and line-range validation.
- [ ] Preserve candidate rejection reasons and consolidate exact duplicates.
- [ ] Build `ReviewResult` and render findings, coverage, snapshot, and errors.

Input: evidence and optional declared rules/change description. Output: a finalized
result. Start with direct function calls; add graph wiring in the next milestone.
Analyzer results can be empty with the analyzer stage explicitly marked not yet
implemented. Do not call this intermediate build a complete v1 review.

**Completion check:** an empty valid response differs visibly from malformed output,
and an invented evidence reference does not become an accepted finding. No credentials
are needed for these checks. Structural validation does not establish semantic truth.

### Milestone 4 — LangGraph and one real provider

- [ ] Wire `prepare -> collect evidence -> analyze -> finalize`, delegating behavior
  to the functions you already tested. The CLI renders the result after execution.
- [ ] Route an empty diff around model analysis. Route failed preparation to a
  failed result; preserve useful evidence when later stages fail.
- [ ] Construct one provider through the existing provider integration boundary.
  Implement any missing interface behavior without duplicating Git or finding logic.
- [ ] Load provider settings, explicit remote-source policy, and resource limits
  through local configuration. Record effective non-secret configuration in results.
- [ ] Enforce the request deadline, context/output limits, and cancellation. Account
  for SDK retries so an outer timeout does not hide unbounded inner work.
- [ ] Capture model identity, usage when available, duration, and stage outcomes.

Input: the same review request. Output: the same result contract, now using a real
model. Keep fake-provider integration tests to distinguish orchestration regressions
from model variability. Persistent checkpoints and an autonomous tool loop are deferred.

**Completion check:** a small review reaches a report through LangGraph; empty diffs
make zero provider calls, and provider errors cannot become successful clean reports.
An explicitly disallowed remote request is blocked before source transmission.

### Milestone 5 — deterministic analysis

- [ ] Choose one analyzer and a narrow initial correctness rule set; record its
  version, configuration, timeout, and accepted process exit codes.
- [ ] Run it on captured source before model analysis, independently of live edits.
  Use a fixed explicit configuration rather than accidentally discovering local config.
- [ ] Normalize diagnostics into evidence and candidate records with analyzer origin.
- [ ] Compare baseline diagnostics where necessary to avoid presenting pre-existing
  findings as newly introduced. Do not equate a shifted line number with a new defect.
- [ ] Include relevant diagnostics in the model packet and final validation path.
- [ ] Preserve analyzer findings if the provider fails; label the overall review partial.

**Completion check:** a known analyzer-detectable defect is reported from the change
snapshot. An unchanged baseline defect is not reported as new. Analyzer failure is
shown as an incomplete check, even if the model returns no candidates.

This analyzer is required for v1. It is built here to keep the learning sequence
small, but its runtime position is before model analysis.

### Milestone 6 — verify and finish v1

- [ ] Assemble a small human-labeled set of buggy and clean changes. State the
  expected defect, trigger, evidence, and why clean examples should remain clean.
- [ ] Run the whole reviewer against those cases; record misses, false positives,
  coverage, elapsed time, and model usage. Treat this as an initial evaluation,
  not proof that SERA finds every defect.
- [ ] Exercise missing refs, empty diffs, unsupported files, context limits,
  analyzer failures, provider failures, and cancellation through the CLI.
- [ ] Define and test CLI exit behavior. A simple starting contract is 0 for a
  complete review regardless of finding count, 2 for partial/failed review or invalid
  invocation, and 130 for user cancellation. Findings remain part of the result;
  a future CI failure threshold would be a separate policy.
- [ ] Document the actual installation/run command and supported limits here once
  implemented. Add `sera review` packaging only when the module entry point works.

**Completion check:** the CLI reviews a small committed Python change, runs the
configured analyzer before model reasoning, validates and reports findings, and
discloses incomplete coverage. Real-model evaluation has been performed with
explicitly permitted inputs, and deterministic checks run without model credentials.

### Decisions to record while implementing

Before each affected milestone, choose and record concrete values for:

| Decision | Needed by |
| --- | --- |
| Contract representation, Git timeout, and expected-error format | Milestone 1 |
| Context byte/file limits, deterministic selection order, encoding/parse fallback | Milestone 2 |
| Provider request/response shape, finding schema, duplicate key | Milestone 3 |
| Model/provider, local configuration format, data policy, token/deadline limits | Milestone 4 |
| Analyzer version, rule set, baseline matching, and timeout | Milestone 5 |
| Evaluation examples, acceptable known limitations, and reproducible run instructions | Milestone 6 |

These are implementation decisions to make deliberately, not reasons to build
extra infrastructure. Begin today with `contracts.py`, `repository.py`, and a
minimal `__main__.py`, plus the package initializer and Git fixture tests.

## 8. Start here: first Git milestone

Your first task is repository preparation and changed-file listing. You write the
code; this exercise describes behavior and experiments, not an implementation.

### What you should produce

Given a repository directory and an explicit target reference, print:

```text
Repository:    /path/to/project
Target:        origin/canary
Target commit: <full commit ID>
Change commit: <full commit ID for HEAD>
Merge base:    <full commit ID>
Scope:         committed changes only; local reference freshness not checked

Changed files:
M  app/users.py
A  app/validation.py
```

This is an example output, not a result from this repository. No model,
authentication flow, LangGraph integration, static analyzer, or database is needed
to reach this milestone. Choose a minimal Python entry point before packaging the
eventual `sera review` command.

### Understand the comparison first

```text
target    A -- B -- C
               \
feature         D -- E
```

With `feature` checked out, the target is C, the change commit is E, and the merge
base is B. Compare B to E to identify the feature's aggregate change. C to E answers
a different question and may include differences caused by target-only work.

Try these commands manually in a small disposable repository you control:

```bash
git rev-parse --show-toplevel
git rev-parse --verify 'HEAD^{commit}'
git rev-parse --verify 'origin/canary^{commit}'
```

`origin/canary` is an example; replace it with the intended target. You do not need
a remote for learning: a local `canary` branch works too. `git fetch origin` is a
separate explicit operation if you want to refresh remote-tracking references.

Then capture the revisions and examine the comparison:

```bash
review_head=$(git rev-parse --verify 'HEAD^{commit}')
review_target=$(git rev-parse --verify 'origin/canary^{commit}')
git merge-base --all "$review_target" "$review_head"
```

Confirm exactly one merge base exists before assigning it. These interactive
commands assume success; your Python implementation must check every exit code.

```bash
review_base=$(git merge-base "$review_target" "$review_head")
git diff --no-ext-diff --no-textconv --name-status --find-renames \
  "$review_base" "$review_head" --
git diff --no-ext-diff --no-textconv --stat \
  "$review_base" "$review_head" --
```

For later milestones, inspect the patch and committed contents:

```bash
git diff --no-ext-diff --no-textconv --unified=20 \
  "$review_base" "$review_head" --
git show "${review_head}:app/users.py"
```

The example path must exist at that commit. A deleted file can be read from the
baseline instead. `git show` reads committed content, whereas opening a filesystem
path reads the working tree. That distinction is the foundation of consistent review.

### Implement in small steps

1. Write down the fields you want in `ReviewRequest` and `ReviewSnapshot`.
2. Create a Git helper that receives a repository directory and returns explicit
   success or failure. Run Git using argument lists, without a shell; set its working
   directory explicitly and impose a timeout. Validate refs and prevent user input
   from being interpreted as command options.
3. Resolve the refs once and calculate the merge base. Handle zero or multiple
   merge bases explicitly. Do not fetch or modify the repository automatically.
4. List file changes using Git's machine-readable NUL-delimited output (`-z`).
   Do not split filenames on spaces or assume every status has one path: a rename
   includes an old and a new path. Treat rename detection as a Git heuristic.
5. Convert that output into your own change records and render the summary.

A possible home is a new `app/review/` package with contracts, repository access,
and an entry point. These are suggested locations; no modules have been created by
this documentation change. Keep Git logic out of `app/agent/providers/openai_compat.py`:
that file concerns provider access, not repository comparison.

### When the milestone is finished

Use a small temporary Git repository to demonstrate:

- A feature with several commits lists the aggregate changes since its merge base.
- Target-only commits are not presented as feature changes.
- Added, modified, deleted, and renamed files have correct paths and statuses.
- Filenames containing spaces are preserved.
- Missing targets and repositories without an initial commit fail clearly.
- Unrelated history, multiple merge bases, or insufficient shallow history do not
  silently produce a guessed baseline.
- An empty diff produces an explicit no-changes result.
- Uncommitted edits are excluded, disclosed, and left untouched.
- Advancing a branch after preparation does not alter the captured comparison.

Turn the important examples into integration tests around real temporary Git
repositories. Assert the comparison behavior, not just mocked command strings.

Once this works, continue with hunk parsing and source retrieval. Bring your code
or a failing example for review; finish understanding this boundary before adding
the model. The next milestone should be explainable entirely without an LLM.

## 9. Later expansion

Potential later additions: staged/uncommitted snapshots, PR integration, proposed
merge analysis, bounded agent retrieval, richer dependency graphs, chunked review,
and durable recovery. Each needs a demonstrated requirement and a new decision.
Deep Agents, Temporal, vector storage, multiple agents, automatic fixes, and the
broader hosted platform are not prerequisites for this v1.
