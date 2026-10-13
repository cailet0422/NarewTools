@AGENTS.md

Claude Code specifics:
- The `narew-studio` MCP server comes from `.mcp.json` in this folder; its tools are `mcp__narew-studio__*`.
  If they are missing, check `/mcp` (approval) or run the same tools through
  `python -m studio.agent call <tool> ... --pretty`.
- Pictures from `world_render`, `gui_render`, `image_view`, `model_render`, `sound_info` come back inline;
  look at them before telling the user something is done.
