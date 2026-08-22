"""Write -- P0 · fs.write. Step 7 · Phase 06 + Tool Catalog §P0.

Default target decision: ASK, with a CLEARER warning than Edit when the target
already exists -- Edit changes part of a file, Write replaces all of it.
"""

# NOTE ->> Shares preconditions, atomicity and locking with edit.py -- read those notes.


# ==============================================================================
# 1 · Input / output
# ==============================================================================

# NOTE ->> extra="forbid". file_path: str (ABSOLUTE per catalog), content: str (COMPLETE).
# NOTE ->> "Complete" is the whole risk: a model that emits a shortened version with
# NOTE ->> "... rest unchanged ..." in the middle destroys the file and the call SUCCEEDS.
# NOTE ->> Say so in the description, and consider refusing content containing an
# NOTE ->> elision marker on a file that already exists.
# NOTE ->> Output: "create" | "update", path, written content, structured patch, nullable
# NOTE ->> original content, optional git diff. The create/update discriminator is what
# NOTE ->> lets the UI warn correctly.


# ==============================================================================
# 2 · Risk is not constant
# ==============================================================================

# NOTE ->> LOW for a NEW path -- trivially reverted, nothing was lost.
# NOTE ->> MEDIUM once the file exists -- this is now a destructive overwrite.
# NOTE ->> That is exactly why risk_for(args) takes ARGS. See base.py §2.
# NOTE ->> ELEVATED REVIEW regardless of mode (catalog) for files that execute or configure:
# NOTE ->> executables, settings, hooks, CI workflows, anything secret-bearing. A tool that
# NOTE ->> can write .github/workflows/ under ACCEPT_EDITS is a supply-chain hole.


# ==============================================================================
# 3 · Validation
# ==============================================================================

# NOTE ->> canonical contained path; allowed file type/policy; existing file requires a
# NOTE ->> COMPLETE FRESH READ and a matching expected hash; content-size limit; PARENT
# NOTE ->> containment (creating a directory tree is also a write); secret checks; never a
# NOTE ->> special device target.
# NOTE ->> Parent creation is a separate intent -- take a directory-level lock for it, or
# NOTE ->> two concurrent creates race on mkdir.


# ==============================================================================
# 4 · Write mechanics + idempotency
# ==============================================================================

# NOTE ->> Atomic replace, explicit mode policy, before/after hashes, artifact-backed diff,
# NOTE ->> no implicit line-ending rewrite.
# NOTE ->> IDEMPOTENCY on (expected-before hash, content hash). NEVER overwrite changed
# NOTE ->> content on a retry: if before-hash no longer matches, the retry is not a retry,
# NOTE ->> it is a second write against a file somebody else moved.


# ==============================================================================
# Gate  ->  tests/agent/test_preconditions.py
# ==============================================================================

# NOTE ->> new path -> create, risk LOW.
# NOTE ->> existing path without a fresh read -> refused.
# NOTE ->> existing path with a stale hash -> refused, nothing written.
# NOTE ->> parent directory created only inside the project root.
# NOTE ->> a settings / CI / hook path -> asks even in ACCEPT_EDITS.
# NOTE ->> replayed call does not write twice.
# NOTE ->> partial write interrupted by a crash leaves the ORIGINAL file intact (atomicity).
