"""Frame schemas -- Phase 10.

Every frame crossing stdio is a pydantic model. This is the one place where an
unknown field is a protocol error rather than something to tolerate: the parent
and the sidecar are versioned separately and will drift.
"""
