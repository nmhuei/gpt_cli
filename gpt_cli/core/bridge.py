import re


def resolve_connector_context(connector_name: str | None) -> tuple[list[str], list[dict]]:
    """Resolve a configured MCP connector to native turn-selection fields."""
    if not connector_name:
        return [], []

    connector_name = str(connector_name).lstrip("@").strip()
    if not connector_name:
        return [], []

    from ..plugins.manager import PluginManager

    manager = PluginManager()
    connector = manager.find_plugin(connector_name)
    if connector is None or connector.auth_status != "ACTIVE" or not connector.connector_id:
        raise RuntimeError(f"Active MCP connector not found: {connector_name}")
    source = manager.get_connector_source(connector.connector_id)
    return [connector.connector_id], [source]


def resolve_connector_system_hints(connector_name: str | None) -> list[str]:
    """Compatibility helper for callers that only need prepare-time hints."""
    return resolve_connector_context(connector_name)[0]

def resolve_prompt_with_bridge(
    prompt: str,
    bridge_name: str | None = None,
    connector_id: str | None = None,
) -> str:
    """
    Resolve an explicit connector mention into ChatGPT Web's native syntax.

    The Web composer encodes an attached app as ``[$name](app://connector_id)``.
    A plain ``@name`` is retained only when no live connector has been resolved.
    """
    p = prompt.strip()
    if not p:
        return p

    # Preserve a rich app reference copied from ChatGPT Web without nesting it.
    if re.match(r"^\[\$[a-zA-Z0-9_\-]+\]\(app://[^)]+\)(?:\s|$)", p):
        return p

    legacy_match = re.match(
        r"^\[Use MCP connector @\[([a-zA-Z0-9_\-]+)\] for this request\]:\s*(.*)$",
        p,
        re.DOTALL,
    )
    if legacy_match:
        target_bridge = legacy_match.group(1).strip()
        clean_prompt = legacy_match.group(2).strip()
        return f"@{target_bridge}" + (f" {clean_prompt}" if clean_prompt else "")

    # Check for explicit leading @[name] or @name
    # Matches: @[name], @[name]:, @name, @name:
    explicit_match = re.match(r"^@\[([a-zA-Z0-9_\-]+)\](?:\s*:\s*|\s+)?(.*)$", p, re.DOTALL)
    if not explicit_match:
        explicit_match = re.match(r"^@([a-zA-Z0-9_\-]+)(?:\s*:\s*|\s+|$)(.*)$", p, re.DOTALL)

    if explicit_match:
        target_bridge = explicit_match.group(1).strip()
        clean_prompt = explicit_match.group(2).strip()
    else:
        # No explicit @mention in prompt: check default / active bridge
        if not bridge_name or str(bridge_name).lower() in ("off", "none", "false"):
            return p
        target_bridge = str(bridge_name).lstrip("@").strip()
        if not target_bridge or target_bridge.lower() in ("off", "none", "false"):
            return p
        clean_prompt = p

    prefix = f"[${target_bridge}](app://{connector_id})" if connector_id else f"@{target_bridge}"
    return prefix + (f" {clean_prompt}" if clean_prompt else "")
