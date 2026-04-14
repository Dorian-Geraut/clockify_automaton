#!/usr/bin/env bash
set -e

# Check that Python 3.9+ is available
if ! command -v python3 &>/dev/null; then
    echo "Error: python3 is not installed or not in PATH."
    exit 1
fi

python3 - <<'EOF'
import sys
if sys.version_info < (3, 9):
    print(f"Error: Python 3.9+ is required (found {sys.version})")
    sys.exit(1)
EOF

# Create the virtual environment if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment in .venv/ ..."
    python3 -m venv .venv
else
    echo "Virtual environment already exists, skipping creation."
fi

# Install the package and its dependencies
echo "Installing clockify-automaton and dependencies..."
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install -e .

echo ""
echo "Installation complete!"
echo ""
echo "Next steps:"
echo "  1. Copy the example config and fill in your details:"
echo "       cp config.example.json my_config.json"
echo ""
echo "  2. Activate the virtual environment:"
echo "       source .venv/bin/activate       # bash/zsh"
echo "       source .venv/bin/activate.fish  # fish"
echo ""
echo "  3. Run the app:"
echo "       python -m clockify_automaton my_config.json"
