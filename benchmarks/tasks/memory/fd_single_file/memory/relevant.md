## Explicit single-file discovery is rejected

type: experience
root_cause: directory-oriented discovery assumes traversal semantics
fix: classify explicit file input before traversal
applies_when: a discovery helper that only walks directories receives one explicit file path
anchors: src/discover.py, err=discovery target is not a directory: src/discover.py
evidence: commit={commit}; test=python -m unittest discover -s tests -q
