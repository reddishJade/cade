# f118d163 merge regression audit (2026-10-18)

Base: `c27562db` (divergence point of the two branches). Dev first parent: `cf4e9e55`. Feature-branch second parent: `66ab71bd`. Merge commit: `f118d163`. Current remote dev: `8006e85`. Audit scope: the 24 commits in `c27562db..cf4e9e55`.

## Method

This audit reads the files changed by each of the 24 commits. It compares the two parent trees, the merge tree, and current dev by Git tree blob SHA. The **dev side / branch side / manual** columns describe which file version the merge result selected, not whether behavior was preserved. A file can appear in several commits, so the counts are not additive. Later dev changes to a file still need equivalence checks against the current API contracts and behavior tests.

The net change (`c27562db → cf4e9e55`) touches 99 file paths: 6 match the dev parent tree exactly, 88 match the feature-branch tree exactly, and 5 are independent merge results. These numbers measure coverage risk; they are not 88 lost features.

## Per-commit file version check

| Dev commit | Topic | Dev / branch / manual | Code audit status |
| --- | --- | ---: | --- |
| [2659163](https://github.com/reddishJade/cade/commit/2659163ad167) | SDK retry / remove ProviderRuntime | 0 / 11 / 0 | **Revert confirmed**: `_runtime.py` and `ProviderRuntime` were reintroduced |
| [32431a5](https://github.com/reddishJade/cade/commit/32431a566765) | asyncio worker | 1 / 0 / 0 | File preserved |
| [fe638f1](https://github.com/reddishJade/cade/commit/fe638f1d6894) | Central GateConfig | 0 / 5 / 0 | GateConfig exists; assembly equivalence untested |
| [a025a9e](https://github.com/reddishJade/cade/commit/a025a9eaf1d5) | Typed rollover | 0 / 8 / 0 | `message_codec.py` is back; behavior untested |
| [afb15e1](https://github.com/reddishJade/cade/commit/afb15e128307) | Run result metadata | 0 / 6 / 0 | Old `CodingRunState` restored; scope untested |
| [242a37e](https://github.com/reddishJade/cade/commit/242a37e3c7cb) | Immutable context sections | 0 / 8 / 0 | Frozen collectors exist; equivalence untested |
| [86c4943](https://github.com/reddishJade/cade/commit/86c4943d5cb5) | Build providers for active roles only | 0 / 10 / 0 | **Behavior regression confirmed**: `_build_llm_profiles` builds instances for all of main, subagent, reviewer, judge, and refiner |
| [2212d53](https://github.com/reddishJade/cade/commit/2212d53b0f1c) | Remove redundant file display metadata | 0 / 1 / 0 | Cleanup not preserved; low priority |
| [23b45df](https://github.com/reddishJade/cade/commit/23b45dfc13c1) | Remove unreachable structured search tools | 0 / 12 / 1 | `glob_search.py` and similar files are back; reachability needs a check |
| [ff3569f](https://github.com/reddishJade/cade/commit/ff3569f54e12) | Remove inactive citations decoration | 0 / 6 / 0 | `prompting/citations.py` is back; check whether something uses it again |
| [3011a78](https://github.com/reddishJade/cade/commit/3011a7897925) | Single runtime tool adapter | 0 / 2 / 0 | Old tool gate file version restored; overlapping responsibilities to review |
| [8945851](https://github.com/reddishJade/cade/commit/89458517ff47) | Shared redaction for audit and MCP | 3 / 1 / 2 | Partially preserved with a manual merge; check the MCP output path |
| [14467fc](https://github.com/reddishJade/cade/commit/14467fcd518c) | Single subagent tool registry | 1 / 0 / 1 | Partially preserved with a manual merge; check instance uniqueness |
| [c7b54ca](https://github.com/reddishJade/cade/commit/c7b54ca2a082) | Read history from session entries directly | 1 / 0 / 1 | Partially preserved with a manual merge; history queries untested |
| [1a24f88](https://github.com/reddishJade/cade/commit/1a24f88343a0) | Single MCP server state | 0 / 0 / 1 | Manual merge; current MCP state contracts have tests, configuration changes still need checks |
| [094ecdd](https://github.com/reddishJade/cade/commit/094ecdd22cb1) | Unified loop-to-host event pipeline | 0 / 17 / 1 | **High risk, untested**: structured events, persistence, and host consumption paths |
| [22b534a](https://github.com/reddishJade/cade/commit/22b534ac4cd4) | Native async provider and host stream | 0 / 19 / 0 | **High risk, untested**: async interfaces remain; cancellation and stream-close semantics are not yet verified against this design |
| [563238a](https://github.com/reddishJade/cade/commit/563238a2466d) | Resolve provider config once | 0 / 11 / 0 | **High risk, untested**: the registry still constructs runtime wrappers |
| [f4b83ce](https://github.com/reddishJade/cade/commit/f4b83ce19903) | Remove duplicate telemetry | 0 / 6 / 0 | The simplification was overwritten; verify a single usage source |
| [c19c7c6](https://github.com/reddishJade/cade/commit/c19c7c6f6366) | Bind permission evidence to the tool call | 0 / 8 / 0 | **Structural regression confirmed**: evidence still travels across hooks through `_last_perm_results[tool_call_id]`; a real failure needs a concurrency or retry reproduction |
| [fc32a6d](https://github.com/reddishJade/cade/commit/fc32a6da771f) | Structured tool results and images | 0 / 18 / 2 | **Behavior regression confirmed**: `_codec._convert_tool_result` turns `ImageContent` into `[image result]`, dropping the multimodal payload |
| [4d24e34](https://github.com/reddishJade/cade/commit/4d24e34280f3) | Clean up runtime test contracts | 0 / 3 / 0 | Tests were reverted; decide what to restore against the current design |
| [948facf](https://github.com/reddishJade/cade/commit/948facf016f2) | Freeze the permission snapshot per run | 0 / 5 / 0 | **High risk, untested**: Gate snapshots exist; check mid-run permission changes |
| [cf4e9e5](https://github.com/reddishJade/cade/commit/cf4e9e55eb75) | Explicit execution modes | 0 / 8 / 1 | **Revert confirmed**: the Plan timeout is back; [PR #17](https://github.com/reddishJade/cade/pull/17) fixes it |

## Follow-up order

1. **Permission evidence**: cover `c19c7c6` and `948facf` with tests for parallel calls that share a tool ID, retries, run switches, and approval attribution. Fix confirmed mismatches in separate PRs. [PR #18](https://github.com/reddishJade/cade/pull/18) handles input provenance, which differs from call-local result attribution.
2. **Structured tool results**: restore provider transport of the multimodal payload for `fc32a6d` and add round-trip tests. Do not rewrite images as prompt text only.
3. **Provider configuration and lifecycle**: retest the number of role instances when only main is used, independent role configuration, error retries, and stream close. Target the current code; do not cherry-pick the large historical refactor as is.
4. **Events and session recovery**: run the same replayable trajectory against `094ecdd`, `22b534a`, `a025a9e`, and `948facf`, and prove the contracts with observed logs and wire requests.

The audit states only what has source or file-version evidence; everything else stays untested. Passing CI does not prove that no cross-branch semantic regression remains.
