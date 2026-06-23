# Manual Installation

If you prefer to install manually instead of using `./start.sh`:

## Requirements

- **Python 3.11+** (3.12 recommended)
- **pip** (comes with Python)
- **macOS, Linux, or WSL** (native Windows not supported)

## Steps

### 1. Verify Python

```bash
python3 --version
# Must be 3.11 or higher
```

If not installed:
- macOS: `brew install python@3.12`
- Ubuntu/Debian: `sudo apt install python3.12 python3.12-venv`
- WSL: same as Ubuntu

### 2. Create virtual environment

```bash
python3.12 -m venv .venv
```

### 3. Activate it

```bash
source .venv/bin/activate
```

### 4. Install e2n

```bash
pip install -e ".[dev]"
```

### 5. Launch

```bash
# Wizard UI (recommended for first-time users):
e2n-ui --open

# Or CLI directly:
e2n --help
```

## Updating

```bash
source .venv/bin/activate
pip install -e ".[dev]"
```
