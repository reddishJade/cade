# Discovery behavior contract

- `discover(root)` returns repository-relative paths with `/` separators;
- a directory input is traversed recursively and only `.py` files are reported;
- an explicit file input is reported as that single path;
- an input path that does not exist raises `DiscoveryError`;
- `discover` and `iter_sources` keep their names and signatures.

This contract is part of the frozen fixture and must not be edited.
