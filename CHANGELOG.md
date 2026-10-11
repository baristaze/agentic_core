# Changelog

The latest release is listed here; every release's notes, older ones
included, stay on its GitHub release. Releases are tagged
`vMAJOR.MINOR.PATCH`; see `CONTRIBUTING.md` for what bumps which
number.

## 0.16.0 (2026-10-11)

A kind is built from toolsets, and loads a deferred one on demand
without breaking the cache. A kind asks each model role it calls for a
model size and an effort, never a model. A session carries standing
text, a principal's instructions, whole in the cached system layer.
An MCP client makes a server's tools a toolset, its pin checked at
every call. Minor, with one reversal named: `McpCall` takes the call's
runtime.

### Added

- Model sizes: a kind asks, of each model role it calls, a size
  (`ModelSize`, XS to XL) and an effort (`ModelAsk`, the kind's
  `asks`). The table resolver serves an asked role from a size table
  (`DEFAULT_SIZES`) and any other role by its name. A fill lists the
  efforts its model takes (`Fill.efforts`) and runs at an asked effort
  only when it lists it, fallbacks included. The fill set keeps each
  ask beside its fill (ADR 1032, lens MOD-01) (#95).
- Toolsets: a named, versioned group of tools a kind names beside
  single tools; `kind.tools` stays their union, so a kind built from
  toolsets renders the bytes of its flat list. A deferred toolset waits
  outside the registry as one line of `load_toolset`'s description; a
  load is a `switched` step, and its tools render after the rest from
  the next request on, so sessions of a kind version share one prefix
  until a load (ADR 1031, lenses TOL-03, WIN-01, AGT-04, ECO-01) (#96).
- Standing text: a principal's instructions pinned with a session
  (`Start`, `Spawn`, `Handoff`, `switch_standing`) and held by a sealed
  `switched` step. It renders whole after the kind's prompts as an
  instruction, on every request, and is refused past 24,000 characters
  when pinned. A cache breakpoint at the end of the kind's prompts
  (`ModelCall.system_break`) keeps one cached entry for every session of
  a kind version, whatever each pins (ADR 1033) (#97).
- An MCP client (`integrations/mcp`, over the official SDK at
  `mcp==2.3.0`): stdio and streamable HTTP, one connection per exchange,
  ended by the tool's deadline. `McpServers.tools` builds each binding's
  tool from the server's listing and refuses a moved definition; each
  tool declares its server's secret, so its kind holds credentials.
  `McpServers.call` audits the secret by its name, resolves the
  credential, refuses a moved tool, and quotes the answer as data
  (ADR 1034, the spec's Tools over MCP, lens TOL-04) (#98).

### Changed

- `ModelResolverInterface.resolve` and
  `ModelsManagerInterface.resolve_fill_set` take `asks`, empty by
  default; an adopter's resolver adds the parameter (#95).
- A `switched` step's header's `fills` is optional: a load or a
  standing switch carries none (#96, #97).
- The inbox appends a standing switch; `trust_of` and `instructs` class
  it as an instruction, so the inbox checks its sender (#97).
- The Anthropic adapter marks `ModelCall.system_break`; the OpenAI
  adapter names it among the markers it drops (#97).
- `McpCall` takes the call's runtime: `(ctx, binding, input, runtime)`.
  A reversal (#98).
- `ToolRuntime.use_secret` audits a declared secret a tool uses outside
  a command, and refuses one it does not declare; `configure_logging`
  silences the SDK's loggers (`mcp`, `client`) (#98).
