# =============================================================================
# test_headless_entry.py
#
# The command-line entry points must at least import: `python -m agent` was
# broken for weeks by an import of a module that had moved, and nothing noticed.
#     python3 -m pytest agent/tests/test_headless_entry.py  (run from backend/)
# =============================================================================
import importlib


def test_python_m_agent_imports():
    assert callable(importlib.import_module("agent.orchestrator").main)
    assert callable(importlib.import_module("agent.__main__").main)


def test_the_report_and_benchmark_import():
    assert callable(importlib.import_module("agent.report").main)
    assert callable(importlib.import_module("agent.benchmark").main)
