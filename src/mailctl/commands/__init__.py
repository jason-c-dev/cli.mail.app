"""Command modules for mailctl.

Each command group lives in its own module with a clear separation between:
- Command handlers (Typer-decorated functions)
- AppleScript generation (script-building functions)
- Output formatting (delegated to ``mailctl.output``)
"""
