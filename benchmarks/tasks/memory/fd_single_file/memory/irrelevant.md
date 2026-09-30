## Cached discovery results survive a deleted source file

type: experience
root_cause: the discovery cache is keyed only by the scanned root and suffix, so an entry keeps reporting a file that was deleted during the same session
fix: drop a cached entry when one of its reported paths no longer exists on disk
applies_when: a long-lived process reuses cached discovery results while the working tree changes underneath it
anchors: src/cache.py, err=stale cache entry reported a missing path
evidence: session=fixture-session; validation=fixture-event; anchor_state=sha256:0000000000000000000000000000000000000000000000000000000000000000

## Explicit file input is lost during path normalization

type: experience
root_cause: the shared path normalizer drops the trailing file component, so discovery receives a directory-shaped path instead of the requested file
fix: resolve and return explicit file inputs inside to_repo_path in src/paths.py before discovery traverses anything
applies_when: explicit file inputs are passed through the shared path normalizer of a discovery command
anchors: src/discover.py, err=discovery target is not a directory: src/discover.py
evidence: session=fixture-session; validation=fixture-event; anchor_state=sha256:1111111111111111111111111111111111111111111111111111111111111111
