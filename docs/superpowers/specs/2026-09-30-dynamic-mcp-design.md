# Dynamic MCP Design Specification

**Date:** 2026-09-30  
**Status:** Spec only — no implementation

## 1. Purpose

Replace static MCP assumptions with a capability-driven design that supports dynamic discovery, resource binding, tool lifecycle tracking, and portable configuration across environments.

The design addresses these audit findings:

- fixed MCP bridge naming
- absolute tunnel/path assumptions
- static tool alias maps
- boolean SSE tool lifecycle tracking
- CTF-specific telemetry coupling

## 2. Design Boundaries

### In scope

- MCP server discovery and registration
- capability-based bridge selection
- dynamic tool metadata ingestion
- tool-call lifecycle correlation
- configuration migration from legacy defaults
- test strategy and compatibility validation

### Not in scope

- MCP protocol redesign
- replacing upstream transport implementations
- changing authentication flows
- changing model routing policy
- changing challenge solver logic
- automatic permission escalation
- removing existing compatibility paths in the first migration phase

## 3. Dynamic Discovery Model

### Goals

The client must discover available MCP resources instead of assuming:

- bridge name
- tunnel location
- tool names
- tool aliases
- permission model

### Discovery flow

```
Startup
  |
  v
Configuration sources
  |
  v
MCP endpoint discovery
  |
  v
Capability handshake
  |
  v
Resource registry
  |
  v
Tool resolver
```

### Discovery sources

Priority order:

1. Explicit runtime configuration
2. Environment configuration
3. User configuration directory
4. Previously registered MCP metadata

A missing discovery source must produce a clear diagnostic state, not silently fall back to a machine-specific path.

## 4. Resource Binding

### Resource identity

Resources must be identified by:

- provider id
- server id
- capability set
- endpoint metadata
- lifecycle state

Example conceptual identity:

```
provider=mcp
server=filesystem
capabilities=[read,write,list]
```

### Binding rules

- Binding selects resources by capability.
- Human-readable names are metadata only.
- Multiple MCP servers may coexist.
- Workspace selection must be explicit and portable.

## 5. Tool Lifecycle Model

### Problem

A single boolean state such as `pending_tool_call` cannot represent:

- parallel tools
- retries
- out-of-order completion
- partial failures

### Required lifecycle states

```
created
  |
started
  |
streaming
  |
finished
  |
failed
  |
expired
```

### Correlation requirements

Every tool execution event must carry:

- tool_call_id
- server_id
- tool name
- start timestamp
- completion timestamp
- status

### Stream completion rule

A stream may finalize only when:

- upstream completion is observed
- all active tool calls are terminal
- no unresolved lifecycle entries remain

## 6. Configuration Migration

### Legacy assumptions to remove

| Legacy | Replacement |
|---|---|
| absolute MCP tunnel path | config discovery chain |
| fixed bridge name | capability registry |
| fixed workspace paths | platform-aware storage provider |
| static permission default | negotiated capability policy |

### Migration compatibility

The migration layer must:

- read existing configuration
- warn on deprecated values
- preserve existing user state
- provide deterministic resolution order

## 7. Telemetry Boundary

Telemetry must consume generic tool metadata.

It must not contain assumptions about:

- CTF tools
- one MCP implementation
- one workspace provider
- one bridge name

Formatting should be driven by metadata supplied by the tool registry.

## 8. Testing Requirements

## Discovery tests

Required cases:

- MCP endpoint from explicit config
- MCP endpoint from environment
- MCP endpoint from user config
- missing endpoint diagnostics
- multiple MCP servers

## Tool lifecycle tests

Required cases:

- one tool call
- multiple concurrent tool calls
- out-of-order completion
- tool failure before stream completion
- duplicate completion event
- stream completion with active tools

## Configuration migration tests

Required cases:

- legacy path migration
- invalid legacy configuration
- portable HOME/XDG layouts
- container environment

## Compatibility tests

Required cases:

- existing MCP server behavior unchanged
- existing SSE streams remain parseable
- existing tool calls preserve output format

## 9. Self Review

### Coverage review

Covered:

- dynamic discovery
- resource binding
- tool lifecycle
- configuration migration
- test expectations
- boundaries
- out-of-scope constraints

### Remaining risks

- capability schemas may vary between MCP providers
- migration ordering must avoid breaking existing users
- lifecycle correlation depends on upstream event quality

### Design constraints validated

- no implementation details specified
- no provider-specific paths required
- no CTF workflow embedded into architecture
- no permission escalation mechanism introduced

## 10. Out-of-Scope Confirmation

This specification does not define:

- source code changes
- API implementation
- database schema
- deployment migration scripts
- runtime patch procedures
