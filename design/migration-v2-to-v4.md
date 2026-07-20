# v2 to v4 migration inventory

Generated baseline: 2026-07-11. Scans exclude build, publish, vendored, and binary output directories.

| Concern | Baseline finding | v4 disposition |
|---|---|---|
| Token authority | v2 comments and values remained in platform themes | `design/tokens.json` v4 is the only value authority; platform files carry a v4 marker and are drift-checked. |
| New Rocker | Removed from production in A2-1 | Validator rejects references and production asset filenames. |
| Fixed Web theme | Audit and replay pages were fixed dark | A4 removes fixed roots and resolves host/user choice before system preference. |
| WPF static theme resources | Runtime-sensitive brushes require audit | A3 converts color consumers to `DynamicResource`; stable non-color resources are out of scope. |
| Android inline palette | Java palette and system bars contained inline colors | A5 reads semantic colors from `res/values` and `values-night`. |
| Platform naked colors | Media/HUD and workflow category colors coexist with theme colors | A3-A5 classify each as semantic token, approved media exception, or domain data color. |

Canonical scans: `rg -i "new[ -]?rocker|data-theme=\"dark\"|StaticResource Fantasy|Color.rgb|#[0-9A-Fa-f]{6,8}" frontend desktop/SpiritKinDesktop ios mobile-link-bridge`.

Rollback boundary: A1/A2 contracts, WPF, Web, iOS, and Android are independent patches. A failed client can be reverted without reverting a client that passed its own checks. No business behavior or data contract is part of this migration.
