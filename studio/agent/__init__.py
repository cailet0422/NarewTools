"""NarewStudio for AI agents: every studio feature as a typed tool.

One registry (registry.py) of tools grouped by feature (tools_*.py), served three ways:

* MCP server over stdio    python -m studio.agent mcp      (NarewAgent.exe mcp in the build)
* command line             python -m studio.agent call <tool> '<json args>'
                           python -m studio.agent run <script.json>   (several calls, one process)
* JSON lines               python -m studio.agent serve    ({"tool": ..., "args": {...}} per line)

The agent edits the same mod project the NarewStudio window has open (settings are shared) and a
running window reloads what the agent changed (live.py).  See AGENTS.md for the workflow.
"""
