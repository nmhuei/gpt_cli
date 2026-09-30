# Wire Lifecycle Diagnosis Specification

**Date:** 2026-09-30
**Status:** Revised after owner feedback

## Goal

Make the runtime explain and recover from an apparently early GPT stop without
changing the solver's strategy, adding CTF-specific instructions, restricting
challenge files, or treating a text response as task success.

## Scope

The runtime must dynamically distinguish these observable states:

- connector not attached to the turn;
- upstream conversation still active and awaiting a tool result;
- tool operation reached its own timeout;
- transport/SSE observation ended while the upstream DAG continued;
- assistant emitted a terminal message;
- upstream returned an explicit error.

The classification is evidence-based: SSE events, session checkpoints, the
current upstream DAG node, and connector health. A model's private reason for
EOS is not exposed by the upstream API and must not be invented.

## Required behavior

1. Route an enabled connector using its native dynamically configured mention.
   The user prompt remains solve plus its absolute path.
2. Persist conversation and message pointers as events arrive, not only after
   the turn returns.
3. When the stream goes quiet or ends ambiguously, query the same conversation
   DAG with normal integrity headers and classify the current node.
4. Report a lifecycle status separate from response text:
   connector_unavailable, server_active, tool_timeout, transport_lost,
   model_terminal, upstream_error, or completed_turn.
5. Treat finish_details stop as one message EOS. It is not a task-completion
   reason and is not evidence of filtering.
6. Do not create a blind continuation loop, add solve hints, inspect solution
   artifacts, or impose CTF-specific output rules.

## Non-goals

- No answer verifier, sandbox, staging, source-tree policy, or web-search
  policy is part of this work.
- No forced extension of a model generation after it emitted EOS.
- No static connector name, static tool name, or word-list heuristic.

## Acceptance tests

1. A configured connector produces native mention routing without a source
   constant.
2. CLI checkpoints survive an interruption after a conversation event.
3. A server current node that invokes a tool reports server_active even when
   SSE is quiet.
4. A final assistant text node reports model_terminal rather than task success.
5. A tool result containing exit 124 reports tool_timeout.
6. gpt status JSON does not raise UnboundLocalError.

