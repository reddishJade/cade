# Cache key stability

`src/cache.py` indexes rendered reports by the request parameters that produced
them. `python -m unittest discover -s tests -q` currently fails on this fixture.

Required behavior:

- two parameter mappings with the same entries produce the same cache key,
  whatever order the caller built them in;
- `get` returns the value stored by `put` for an equivalent parameter mapping;
- parameters that were never stored still return `None`.

Constraints:

- modify `src/cache.py` as needed;
- do not modify `docs/behavior.md`;
- do not replace the in-process store with an external cache.

Success is determined only by the unittest command in the task manifest.
