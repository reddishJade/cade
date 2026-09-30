# Cache behavior contract

- `cache_key(parameters)` depends only on the parameter entries, never on the
  order in which the caller inserted them;
- `get(parameters)` returns the value stored by `put` for equivalent parameters;
- parameters that were never stored return `None`;
- `cache_key`, `get`, and `put` keep their names and signatures.

This contract is part of the frozen fixture and must not be edited.
