## Cache keys follow the caller's parameter order

type: experience
root_cause: the cache key is assembled from the caller's mapping iteration order
fix: canonicalize parameters with sorted(parameters.items()) before assembling the key
applies_when: a cache key is built from a mapping that callers may construct in any order
anchors: src/cache.py, err=AssertionError: 'region=eu&tier=gold' != 'tier=gold&region=eu'
evidence: session=fixture-session; validation=fixture-event; anchor_state={anchor_state}; verify=python -m unittest discover -s tests -q
