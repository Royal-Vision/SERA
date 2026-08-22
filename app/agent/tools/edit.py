"""Edit -- P0 · fs.write. Step 7 · Phase 06 + Tool Catalog §P0.

Default target decision: ASK. ACCEPT_EDITS may allow ordinary workspace files;
protected paths stay ask/deny and are BYPASS-immune.

The failure this tool exists to prevent is not an error. It is a SUCCESSFUL edit
applied to a file the agent last saw three turns ago, silently discarding whatever
changed in between. An error is recoverable; silent data loss is not.
"""

# NOTE ->> Preconditions come FIRST (engine/preconditions.py). The state machine is what
# NOTE ->> makes this tool safe to write at all -- do not start with the patch logic.


# ==============================================================================
# 1 · Input / output
# ==============================================================================

# NOTE ->> extra="forbid". file_path: str, old_string: str, new_string: str,
# NOTE ->> replace_all: bool = False.
# NOTE ->> EXACT STRINGS, never line numbers. Line numbers go stale the moment anything
# NOTE ->> above them changes; Read hands the model numbers for DISCUSSION and this tool
# NOTE ->> takes strings for MUTATION. That split is deliberate -- see read.py §6.
# NOTE ->> Output: file path, old/new strings, ORIGINAL content, structured diff hunks,
# NOTE ->> user-modified flag, replace-all flag, optional git diff.
# NOTE ->> Return before AND after hashes (catalog) -- they are what makes the retry below
# NOTE ->> decidable.


# ==============================================================================
# 2 · The two invariants
# ==============================================================================

# NOTE ->> READ-BEFORE-EDIT: refuse if this turn has no recorded read of the file.
# NOTE ->> UNCHANGED-SINCE-READ: size and mtime first, sha256 only when those are ambiguous
# NOTE ->> (same second, same size). Cheap check first, expensive check only when needed.
# NOTE ->> The refusal message must say EXACTLY what to do -- "the file changed since you
# NOTE ->> read it; read it again and re-apply". An error message is a prompt.


# ==============================================================================
# 3 · The window nobody closes
# ==============================================================================

# NOTE ->> RECHECK THE FINGERPRINT AFTER APPROVAL, immediately before the atomic replace.
# NOTE ->> An ASK suspends the turn -- seconds or minutes of a human reading a diff. The
# NOTE ->> file can change in that window, and the approval the user gave was for the diff
# NOTE ->> they SAW. Checking only before the prompt means you apply a stale patch with a
# NOTE ->> signature on it.
# NOTE ->> Approval must display the exact proposed diff, not a summary of it.


# ==============================================================================
# 4 · Validation
# ==============================================================================

# NOTE ->> canonical contained path; old_string != new_string; target exists (unless
# NOTE ->> creating from empty); a COMPLETE prior read; unchanged fingerprint;
# NOTE ->> occurrence count -- UNIQUE match required unless replace_all, because an
# NOTE ->> ambiguous match silently edits the wrong one; file-size cap; secret and settings
# NOTE ->> guards; reject .ipynb (NotebookEdit owns those).


# ==============================================================================
# 5 · Write mechanics
# ==============================================================================

# NOTE ->> Atomic: write a temp file in the SAME directory, fsync, os.replace(). Same
# NOTE ->> directory because os.replace is only atomic within a filesystem.
# NOTE ->> Preserve permissions and encoding DELIBERATELY -- a mode-0755 script that comes
# NOTE ->> back 0644 is a broken deploy, and it will not be traced to the edit tool.
# NOTE ->> No implicit line-ending rewrite. A CRLF file stays CRLF; converting the whole
# NOTE ->> file produces a diff of every line and buries the actual change.


# ==============================================================================
# 6 · Concurrency + idempotency
# ==============================================================================

# NOTE ->> EXCLUSIVE write lock on canonical file identity (dev+ino, not the path string).
# NOTE ->> Conflicts with reads whose consistency matters -- Step 6's batcher must never
# NOTE ->> put a read and a write to the same path in one batch. That is a correctness bug,
# NOTE ->> not a performance question.
# NOTE ->> IDEMPOTENCY: deduplicate on (expected-before hash, after hash). A repeated
# NOTE ->> completed call returns the EXISTING RECEIPT rather than editing twice -- after a
# NOTE ->> reconnect the model will re-send, and old_string is usually gone by then, so the
# NOTE ->> naive path fails a retry that actually succeeded.


# ==============================================================================
# Gate  ->  tests/agent/test_preconditions.py
# ==============================================================================

# NOTE ->> read a file -> modify it externally -> edit refused, message says what to do.
# NOTE ->> edit_file on a never-read file -> refused.
# NOTE ->> old_string matching twice without replace_all -> refused, count reported.
# NOTE ->> file changed DURING the approval window -> refused after approval, not applied.
# NOTE ->> file mode and line endings survive an edit.
# NOTE ->> the same call replayed returns the first receipt and edits once.
# NOTE ->> a .ipynb path -> refused, points at NotebookEdit.
