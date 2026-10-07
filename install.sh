#!/usr/bin/env bash
# ==============================================================================
# GPT-CLI Automated Installation Script
# Standard: ASD-STE100 Compliance
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
VENV_DIR="${SCRIPT_DIR}/.venv"

echo "=== Installing GPT-CLI ==="

# 1. Verify Python version
if ! command -v python3 &>/dev/null; then
    echo "Error: Python 3 is not installed. Please install Python 3.11 or newer." >&2
    exit 1
fi

PY_VER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PY_MAJOR="$(echo "$PY_VER" | cut -d. -f1)"
PY_MINOR="$(echo "$PY_VER" | cut -d. -f2)"

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
    echo "Error: Python 3.11+ required. Found Python ${PY_VER}." >&2
    exit 1
fi
echo "✓ Found Python ${PY_VER}"

# 2. Create or verify virtual environment
if command -v uv &>/dev/null; then
    echo "✓ Using uv package manager"
    if [ ! -d "${VENV_DIR}" ]; then
        uv venv "${VENV_DIR}"
    fi
    VENV_PYTHON="${VENV_DIR}/bin/python"
    uv pip install --python "${VENV_PYTHON}" -r "${SCRIPT_DIR}/requirements.txt"
    uv pip install --python "${VENV_PYTHON}" -e "${SCRIPT_DIR}"
else
    echo "Notice: uv not found, falling back to standard venv/pip."
    if [ ! -d "${VENV_DIR}" ]; then
        python3 -m venv "${VENV_DIR}"
    fi
    VENV_PYTHON="${VENV_DIR}/bin/python"
    "${VENV_PYTHON}" -m pip install --upgrade pip
    "${VENV_PYTHON}" -m pip install -r "${SCRIPT_DIR}/requirements.txt"
    "${VENV_PYTHON}" -m pip install -e "${SCRIPT_DIR}"
fi
echo "✓ Installed Python dependencies in ${VENV_DIR}"

# 3. Create global CLI launcher in ~/.local/bin
mkdir -p "${BIN_DIR}"
LAUNCHER="${BIN_DIR}/gpt"

cat <<EOF > "${LAUNCHER}"
#!/usr/bin/env bash
exec "${VENV_DIR}/bin/gpt" "\$@"
EOF
chmod +x "${LAUNCHER}"
echo "✓ Installed executable at ${LAUNCHER}"

# 4. Check PATH environment
if [[ ":$PATH:" != *":${BIN_DIR}:"* ]]; then
    echo "Warning: ${BIN_DIR} is not in your PATH."
    echo "Add this line to your ~/.bashrc or ~/.zshrc:"
    echo "  export PATH=\"\${HOME}/.local/bin:\$PATH\""
fi

echo ""
echo "=== Installation Completed ==="
echo "Run 'gpt login' to authenticate with CloakBrowser."
echo "Run 'gpt \"hello\"' to verify setup."
