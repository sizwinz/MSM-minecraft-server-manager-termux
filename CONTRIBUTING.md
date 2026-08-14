# Contributing to MSM

Thank you for your interest in contributing to Minecraft Server Manager (MSM). We welcome bug fixes, documentation enhancements, feature additions, and performance improvements.

This document outlines our engineering standards, development workflow, and pull request expectations.

---

## Development Setup

MSM requires **Python 3.10+**. Follow these steps to initialize your local development environment:

```bash
# 1. Clone the repository
git clone https://github.com/sizwinz/MSM-minecraft-server-manager-termux.git
cd MSM-minecraft-server-manager-termux

# 2. Create and activate a dedicated virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows PowerShell: .venv\Scripts\Activate.ps1

# 3. Upgrade pip and install runtime + dev dependencies
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r requirements-dev.txt

# 4. Launch the application
python msm.py
```

---

## Repository Architecture

```
MSM-minecraft-server-manager-termux/
├── msm.py          # Application CLI entrypoint
├── core/           # Runtime orchestration, ServerInstance, ConfigManager, constants
├── db/             # SQLite persistence layer with WAL mode
├── ui/             # Terminal UI flows, ANSI colors, prompts, and tables
├── utils/          # Subprocess helpers, network API fetchers, RCON, tunnels, ZIP safety
└── tests/          # Pytest test suite and test fixtures
```

---

## Engineering Invariants & Guidelines

When contributing code, please ensure adherence to the following core architectural rules:

1. **Strict Instance Sandboxing**:
   Runtime state belongs inside `ServerInstance`. Do not introduce global module-level variables for session states, process IDs, or active tunnels.

2. **Security & Subprocess Safety**:
   - All external commands must use explicit argument lists with `shell=False`.
   - Never pass unsanitized user strings to shell execution environments.
   - All archive extraction logic must use `safe_extract_zip()` to mitigate Zip-Slip and symlink traversal vulnerabilities.

3. **Atomic Schema Migration**:
   - Add new configuration keys directly to `DEFAULT_CONFIG` or `DEFAULT_SERVER_CONFIG` in `core/constants.py`.
   - The `ConfigManager` automatically deep-merges defaults on startup without mutating existing user keys.
   - Configuration writes must remain atomic via temporary files (`.tmp` to `.json` rename).

4. **POSIX & Termux Compatibility**:
   - Maintain compatibility with Termux Android path structures and standard Linux filesystem hierarchies.
   - Avoid platform-specific assumptions that break headless or mobile execution.

---

## Code Quality Standards

We enforce strict formatting, linting, and testing standards matching our CI pipeline:

- **Formatting**: Formatted with [Black](https://github.com/psf/black).
- **Style & Linting**: Checked via [Flake8](https://flake8.pycqa.org) with a **100-character line length** limit.
- **Testing**: Regression and unit tests written with [Pytest](https://docs.pytest.org).

### Pre-Commit Verification Commands

Before opening a pull request, run the CI-equivalent verification suite:

```bash
# Style and lint checks
python -m flake8 --jobs=1 .

# Formatting validation
python -m black --check .

# Execute test suite
python -m pytest

# Bytecode syntax validation
python -m compileall msm.py core db ui utils tests
```

---

## Pull Request Workflow

1. **Branching**: Create a focused topic branch from `main` (e.g., `git checkout -b feat/flavor-metadata-cache` or `git checkout -b fix/zip-path-traversal`).
2. **Commit Messages**: Follow the [Conventional Commits](https://www.conventionalcommits.org/) format:
   - `feat(tunnel): add support for custom proxy endpoints`
   - `fix(archive): validate relative archive paths before extraction`
   - `refactor(runtime): simplify process heartbeat checks`
   - `docs: update setup and troubleshooting guides`
3. **Tests**: Add or update corresponding unit tests under `tests/` for any new logic or bug fixes.
4. **Submitting**: Open a PR with a clear description of the problem solved, architectural decisions, and confirmation of passing verification commands.
