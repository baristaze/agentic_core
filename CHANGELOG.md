# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.19.0 (2026-10-14)

The engine's nouns name the agent or the mechanism they serve, and its
base moves to the guideline's v0.61.0, whose own nouns leave the bare
word to the product too. The engine wires, configures, and extends its
loop in files of its own. Its hosted namespace owns its OpenAPI
document, and each role's migration chain is one revision at its head.
Minor, with reversals: the renames, with routes renamed inside `/v1`; a
stamp no longer orders a chain; kinds are registered strings; and
CTX-12's `tenantless` option is refused.

### Added

- `acme.om.agent_root` builds the engine's managers from one
  `AgentRootOptions`, called once by `build_managers`, and
  `acme.infra.agent_root` holds its capabilities as one `AgentInfra`.
  The loop asks a `LoopHooksInterface` for a session's attached toolsets
  and for the steps before its first call, so a layer extends the loop
  through its hooks (ADR 1041, #113).
- Signals files for `agent_sessions`, its four kinds of work, and
  `spend_budgets`, the gate's outcomes, which the ops skills that read
  signals read with no edit (#114).

### Changed

- The engine's own nouns are renamed on every surface, a reversal: the
  bare session is `agent_session` (its tables, `manage_agent_sessions`,
  `WAKE_AGENT_SESSION(S)`, the command line's `agent-session`, the
  usage route, `agent_session_runner`, `ops-agent-session-spend`);
  `Step` is `AgentStep`; `om/models` is `om/language_models`
  (`ACME_LLM_*`); `Artifact` is `ToolOutput` (`tool-outputs`,
  `read_tool_output`); a workspace is an `AgentWorkspace`
  (`ACME_AGENT_WORKSPACE_*`); `infra/machines` is `infra/vms`
  (`ACME_VM_*`); a budget is a `SpendBudget` (scopes `agent_session`
  and `agent_project`, park `spend_budget`); `Plan` is `AgentPlan`
  (`write_agent_plan`); `PolicyRule`, `PolicyLayer`, and `PolicyCall`
  are `ToolPolicyRule`, `ToolPolicyLayer`, and `ToolPolicyCall`.
  Migrations move tables, columns, indexes, and stored values in place
  (ADR 1040, #112).
- The base's renames reach the engine's code, a reversal: `OmBase` and
  `OmException`, the change events manager and its storage, the
  leasable resource, its kinds, and its wait line, and the auth
  session's credential kind. A loop parked in line holds a
  `WaitLinePark` under `wait_line`, naming its `leasable_resource_id`,
  and its park reason is `leasable_resource`. The agent sessions' list
  is the operation `list_agent_sessions` (#114).
- `agent_sessions` has its own OpenAPI document, its generated types,
  and its module of the Python client's calls and types; the enums its
  routes carry from `agent_steps` and `attribution` are defined in
  those namespaces' documents. The usage route is the agent sessions'
  operator route, at the same path, a reversal for code that imports
  the `admin` router or its types (#114).
- The engine's kinds of work and its waiter are named in
  `om/agent_sessions/kinds.py` (`AgentSessionWorkKind`,
  `AGENT_SESSION_WAITER`) and registered below the root's markers, a
  reversal for code that imports them from `WorkKind` or `WaiterKind`.
  The session runner claims from the lane its loop kind is registered
  on (#114).
- A chain is ordered by its parents, a reversal: the engine's first
  migration of each chain follows the base's head, and its own
  revisions fold into one at its head, core and activity at
  `202610150049` and queue at `202610141614`, after a migration names
  each not-null constraint by its table and its column. A local
  database at an older head is made anew (`make reset`, #114).
- The engine's storage and manager methods that take no tenant or no
  stage are declared beside them, and CTX-12 reads the declarations;
  each of its contract suites names its interface. Its entries in
  every list it shares with its base sit below the list's marker. The
  role check reads the renamed tables from `DROPPED_TABLE_ROLES` (#114).
- `build_managers`, `LoopManagerImpl`, `AgentsManagerImpl`, and
  `snapshot_agent_workspace` take new keywords (#113).
- The spec pins the guideline at v0.61.0, and cites Naming Entities
  (OM-19) for a noun a product could own (#114).

### Removed

- The `admin` router and its types: each namespace serves its own
  operator routes (#114).
- The list of tenantless methods under CTX-12 in `pyproject.toml` (#114).
- `WORK_PAYLOADS`, `WORK_ENQUEUE_PERMISSIONS`, `WORK_LANES`, and
  `relayed_lane`: a kind's payload, permission, and lane are its
  registered spec's (#114).
