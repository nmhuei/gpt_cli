import re

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

    # The host MCP needs an explicit workspace bind before it can access an
    # absolute challenge directory.  Preserve the compact CLI form
    # ``gpt -b br -p 'solve /absolute/path'`` while emitting that native bind.
    solve_path = re.fullmatch(r"solve\s+(/\S+)", clean_prompt)
    if target_bridge == "br" and solve_path:
        return f"@br host_workspace_bind path={solve_path.group(1)}; solve"

    return f"@{target_bridge}" + (f" {clean_prompt}" if clean_prompt else "")
