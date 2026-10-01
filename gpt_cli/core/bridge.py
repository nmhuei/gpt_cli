import re


def resolve_connector_context(connector_name: str | None) -> tuple[list[str], list[dict]]:
    """Resolve a configured MCP connector to native turn-selection fields."""
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

def resolve_prompt_with_bridge(prompt: str, bridge_name: str | None = None) -> str:
    """
    Resolve an explicit connector mention into ChatGPT's native mention syntax.
    """
    p = prompt.strip()
    if not p:
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

    # Keep the compact CLI form, but ask the connector to bind by its workspace
    # label.  ``host_workspace_bind`` does not accept an arbitrary local path.
    solve_path = re.fullmatch(r"solve\s+(/\S+)", clean_prompt)
    if target_bridge == "br" and solve_path:
        workspace_name = solve_path.group(1).rstrip("/").rsplit("/", 1)[-1]
        return f"@br bind {workspace_name}; solve"

    return f"@{target_bridge}" + (f" {clean_prompt}" if clean_prompt else "")
