# Calling a connected MCP plugin

This document records the observed wire contract for a ChatGPT Web MCP
connector.  It applies to the local `gpt` CLI; the connector named `br` is an
example only.  Connector and link identifiers are intentionally not static:
the CLI resolves them from the account before every explicitly bound turn.

## User commands

Use one of these forms to attach a connector to a one-shot turn:

```sh
gpt -b br -p "inspect the current workspace"
gpt -p "@br inspect the current workspace"
gpt run --bridge br -p "inspect the current workspace"
```

`-b br` is the clearest form.  It does two distinct things:

1. It renders the text in the same rich app-reference form as ChatGPT Web,
   `[$br](app://<connector-id>) …`.
2. It attaches the account's current connector source to the request.

`@br …` remains accepted as compact CLI input, but is normalized to that rich
app reference when an active connector is resolved.  This is material: the
Web message that successfully invoked BR contained the app reference, not
plain `@br`.  A normal text answer with no `api_tool` event is therefore a
failed BR invocation for a manual health-check prompt, even if it says that
BR is alive.

To deliberately avoid all connector selection, use `--no-bridge`.  In an
interactive chat, select the bridge before submitting the turn; the CLI then
resolves and attaches it for every turn in that chat.

For a BR challenge, use the normal compact form:

```sh
gpt -b br -p "solve /absolute/path/to/challenge"
```

It is rendered as `[$br](app://<connector-id>) solve <absolute-path>`.
The CLI never adds a `bind`, `continue`, or any other instruction that the user
did not provide.

## Verified request path

```text
CLI --bridge br
  -> PluginManager list_accessible links
  -> find active link named br
  -> GET its live connector source by connector_id
  -> POST /backend-api/f/conversation
       top-level system_hints = [connector_id]
       message.metadata.selected_mcp_sources = [full connector source]
       message text = "[$br](app://<connector-id>) <user prompt>"
  -> ChatGPT backend exposes /br/link_<current-link-id>/<tool>
  -> model may call api_tool.list_resources then api_tool.call_tool
  -> ChatGPT backend calls the MCP connector endpoint
```

Both `system_hints` and `selected_mcp_sources` are required parts of the
selection contract.  `system_hints` is also sent to the preparation request.
The full connector source is intentionally copied into message metadata; a
connector ID by itself is not the native per-message attachment.

An earlier successful BR session showed this exact backend behavior: resource
listing under `br`, followed by a tool call whose path was
`/br/link_<link-id>/<tool-name>`.  Therefore tool execution happens in the
ChatGPT backend after attachment, rather than the CLI directly calling the MCP
server.

## What to inspect when a plugin seems absent

| Observable result | Meaning | Next check |
| --- | --- | --- |
| `Active MCP connector not found: br` before a turn starts | The CLI could not resolve an active linked connector. | Check the link name and `auth_status` in ChatGPT connector settings. |
| Turn contains `api_tool.list_resources` / `api_tool.call_tool` | Attachment and backend tool routing worked. | Diagnose the tool's result or its upstream MCP server. |
| BR health-check turn completes as text without an API-tool event | The model did not route to BR, so the invocation did not meet the expected behavior. | Compare the sent message text and connector metadata with this document. |
| Tool event reports a connector/network error | Backend selected the app, but the MCP endpoint failed. | Check connector URL, tunnel, server health and the connector's action list. |
| CLI reports an SSE error | Streaming broke independently of backend completion. | Inspect the saved session/DAG; recovery polls it before classifying the turn failed. |

When verifying interactively, preserve the session identifier and inspect the
CLI event output.  The significant proof is an `api_tool` event, not whether
the prose happens to say a plugin name.

## Burp observation procedure

The PortSwigger MCP extension at `127.0.0.1:9876` is a separate local HTTP MCP
server.  It is suitable for observing the browser's traffic or controlling the
Burp browser only while the Burp process and its extension are running.

For one *manual* `gpt -b br -p …` turn, filter Burp HTTP history for:

```text
/backend-api/aip/connectors/links/list_accessible
/backend-api/aip/connectors/<connector-id>
/backend-api/f/conversation
```

In the final conversation request verify, after redacting credentials:

- `system_hints` contains the resolved connector ID;
- the outgoing user message has `metadata.selected_mcp_sources` with the full
  source object;
- the message begins with `[$br](app://<resolved-connector-id>)`, not plain
  `@br`.

Do not reuse a captured link ID as a constant.  Links may be recreated and the
backend paths include the current link ID.

## Create, connect, and authorize an MCP plugin

The CLI now exposes the same no-auth lifecycle observed for a manually added
MCP app in ChatGPT Web:

```sh
# Inspect current links and their discovered actions (read-only)
gpt plugin list --json

# Create connector → create link → set the requested app permission
gpt plugin add hello https://host.example/mcp \
  --description "Host MCP" --permission full_access

# Change permission later without recreating the app
gpt plugin permission hello read_only

# Remove is explicit and deletes both the app link and its connector
gpt plugin remove hello
```

`add` uses this backend sequence:

```text
POST  /backend-api/aip/connectors/mcp
POST  /backend-api/aip/connectors/links/noauth
PATCH /backend-api/aip/connectors/links/<link-id>
POST  /backend-api/aip/connectors/links/list_accessible
```

The final read verifies the linked connector's `ACTIVE` status, permission,
and actions discovered from the server.  A duplicate connector name is an
error; `add` never deletes or replaces an existing app.  Use the explicit
`remove` command if replacement is really intended.
