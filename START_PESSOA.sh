# Pessoa Startup Script
PYTHON_BIN="/opt/homebrew/bin/python3.12"

# 1. Setup Environment
if [ ! -d "venv" ]; then
    echo "Creating virtual environment with Python 3.12..."
    $PYTHON_BIN -m venv venv
    source venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt
else
    source venv/bin/activate
fi

# 2. Start MCP Bridge
echo "Starting Pessoa Framework Bridge..."
./venv/bin/python3 core/base_server.py
