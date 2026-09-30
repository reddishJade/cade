## Truncation counts colour escapes as visible characters

type: experience
root_cause: the renderer measures the raw string length, so ANSI colour escapes count as visible characters and coloured lines lose their tail
fix: strip ANSI escape sequences before measuring and re-apply them after truncation
applies_when: a cached value is rendered with colour codes into a fixed-width line
anchors: src/render.py, err=rendered line exceeded the terminal width
evidence: session=fixture-session; validation=fixture-event; anchor_state=sha256:0000000000000000000000000000000000000000000000000000000000000000

## Report parameters are reordered before the cache lookup

type: experience
root_cause: the report builder rebuilds the parameter mapping in its own order, so the lookup key never matches the key that was stored
fix: sort the rebuilt parameter mapping inside render_report in src/report.py before every cache get or put
applies_when: report parameters are rebuilt by a helper before they reach the cache
anchors: src/cache.py, err=AssertionError: 'region=eu&tier=gold' != 'tier=gold&region=eu'
evidence: session=fixture-session; validation=fixture-event; anchor_state=sha256:1111111111111111111111111111111111111111111111111111111111111111
