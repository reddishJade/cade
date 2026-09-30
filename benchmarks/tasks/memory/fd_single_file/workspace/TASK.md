# Explicit single-file discovery

`src/discover.py` reports the Python sources below a path.
`python -m unittest discover -s tests -q` currently fails on this fixture.

Required behavior:

- `discover("src")` returns the matching files below `src` recursively, using
  `/` separators;
- an explicit file path such as `src/discover.py` is returned as that single
  result instead of being rejected;
- a path that does not exist still raises `DiscoveryError`.

Constraints:

- modify `src/discover.py` as needed;
- do not modify `docs/behavior.md`;
- do not create a second discovery implementation such as `src/discover_v2.py`.

Success is determined only by the unittest command in the task manifest.
