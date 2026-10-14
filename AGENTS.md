# AGENTS.md — NarewStudio for AI agents

NarewTools is a modding toolkit for **Islet Online** (in-house engine NarewEngine2). The user writes in
**Korean**; answer in Korean. Tool errors are Korean messages meant to be shown as-is.

Everything NarewStudio (the Qt app) can do is available to you as **113 typed tools**: maps and buildings,
furniture placement, new blocks / furniture / items / sounds, any game file, XML table records, GUI screen
layouts, sounds, models, packs, the mod manager — plus pictures and ASCII views so you can check your work.

## Connect

| Agent | How |
|---|---|
| Claude Code (this folder) | `.mcp.json` registers the MCP server **`narew-studio`** (approve it once). Tools appear as `mcp__narew-studio__<tool>`. |
| Any MCP client | stdio server: `python -m studio.agent mcp` (cwd = this folder), or `NarewAgent.exe mcp` from the built `dist\NarewTools\`. |
| Shell only | `python -m studio.agent call <tool> key=value ... --pretty` · several steps with state (world sessions): `python -m studio.agent run steps.json` · JSON lines: `python -m studio.agent serve` |

`python -m studio.agent tools` lists every tool; `python -m studio.agent guide <topic>` (or the `guide` tool)
explains workflows: `overview`, `world`, `content`, `gui`, `sound`, `models`, `mod`, `safety`.

## Ground rules

1. **The game install is read-only.** All edits go into the open *mod project*
   (`Documents\NarewStudio\Projects\<id>`: `files/` replaced or new files, `merge/` table records).
   Never write into the game folder with your own file tools.
2. Tools marked **CHANGES THE REAL GAME INSTALL OR ITS SAVES** (`world_apply_to_game`,
   `world_restore_backup`, `world_remove_from_game`, `manager_apply`, `manager_restore`) need `confirm=true`. Call once without it,
   show the user the dry run, **ask**, then confirm. The game must be closed; backups are made first.
3. The user's own world saves (`<game>\Local\*.bwr`) are never overwritten by `world_save` — it writes a new
   file in `Documents\NarewStudio\Worlds\`; only `world_apply_to_game` (with consent) puts it back.
4. Do not ship whole original game files in a mod zip; do not try to obtain other players'/servers' worlds.
5. In-game verified: new blocks, new furniture, hand recipes, strings, UI sound, GUI images/layout, world edits
   + apply to my world, picture pixel-art walls, worlds up to 4096×4096 (`world_new` + `new_world`). **Not verified yet** (say so when you deliver them): edited models/animations in game,
   generic new items' effects, weather/time BGM, multiplayer.

## Start of a session

```
status                       -> game folder, project, NarewStudio window state, hints
project_new / project_open   -> if there is no project (the window follows your choice)
studio_view                  -> what the user is looking at in the window (world + camera, GUI selection)
```

The NarewStudio window and you share settings (game folder, project). While it runs it **reloads whatever you
change** (unless the user has unsaved edits there) and `studio_show` opens your result for the user.

## Maps (the main use)

Coordinates: `x`, `z` horizontal, `y` up (0..255). Regions are 16×16 columns. Brick `0` = air,
`1..254` = brick types (`bricks_list`), `255` = invisible collision under furniture (never paint with it).
ASCII views and top pictures: **x to the right, z downward**.

```
world_list(kind="save")                       the user's own worlds (Local\ = the one they play)
world_open(path=... | name="map\\64_0.bwr")   -> session "w1"   (no args = what the window shows)
world_info / world_heightmap / world_ground   measure before building (floors, heights, conventions)
world_render(view="top"|"iso", ...)           look; check again after every few edits
world_fill / world_build / world_shape / world_set_blocks / world_replace / world_clear
world_copy -> world_paste(rotate)             copy buildings with their furniture
products_list(placeable_only=true) -> world_place / world_place_many
world_switch(ids | box, on=true|false)         turn lamps on/off (world_objects shows "on")
world_rotate(ids | box, rot | turn)            turn placed furniture about its anchor (collision moves too)
world_image(image, at, width, facing)          a picture file as brick pixel art (dry_run=true: preview first)
world_undo / world_redo                        every call is one undo step
world_save                                     pack world -> mod project; game save -> new file
world_apply_to_game(session)                   dry run -> ask -> confirm=true
world_new(width, depth)                        new flat world 16..4096 wide (the game itself only makes 1024)
world_apply_to_game(path, new_world="name")    adds it as a NEW Local\<name>.bwr (nothing overwritten); resize=true
                                               replaces a world with one of another size
world_remove_from_game(world)                  takes a world out of Local\ again (backed up first)
```

Furniture height: shipped maps put furniture right on the supporting brick (`y = brick + 1`, the default);
in the user's flat world (ground #60 at y=0) the game's own objects stand at y=2. `world_place` notes when
existing objects differ — match them unless the user says otherwise.

Lights: products of kind `"on"` (lamps, candles, street lights) only shine in game when the object state has
bit `0x10` (IsUse) set — shipped maps store most lamps with state 16 (191 of 297). `world_place` / `world_place_many`
place them on (`on=false` for off), `world_paste` keeps the copied state, `world_switch` turns existing ones
on/off. `world_rotate` turns placed furniture in place (a chest keeps its items, a lamp its on/off; the turned
footprint must be free). Wall-mounted products: rot 0 = wall at +z of the anchor cell, rot 1 = +x, rot 2 = -z,
rot 3 = -x (verified on shipped maps).

Pictures: `world_image` matches every pixel to the nearest brick colour and stands it as a wall (or a floor,
`facing="up"`). Game space is left-handed: someone looking toward +z has +x on the right, so `facing="-z"` (the
default) reads right for a viewer at smaller z. Walls are double-sided by default (a mirrored layer behind, 2
bricks thick). `palette="plain"` keeps patterned tiles out; transparent pixels leave cells unchanged. Verified in
game: a 128×128 double-sided wall in the user's world.

Bigger worlds: the server reads the size from the .bwr header, so `world_new` + `world_apply_to_game(new_world=)`
gives worlds the game cannot make itself. `world_new` returns `limits.notes` (Korean) - pass them on: over 2048x2048
the engine's 32-bit block position ids overflow above some height and 16-bit land ids overflow, and the game server
keeps every 16x16 region in RAM (~69 KB each: 2048 ~1 GB, 4096 ~4 GB). Verified in game: 2048 and 4096 load, play and
are saved back at their size (blocks above y=128 kept); new worlds stop at 4096 (bigger files are refused).

Saves played with mods: furniture the open game folder's tables do not know (a mod's chair) makes that region's
objects unreadable — they are kept as they are but not listed, so `world_clear` cannot delete them. The tools say
so: `world_open` / `world_info` return `objects_unreadable_regions` (count, first [rx, rz], objects in them, the
unknown product ids and where they stand, `played_in` = the install the save belongs to, Korean `hint`), and
`world_clear` / `world_delete_objects(box)` / `world_objects` / `world_stats` / `world_copy` add a `note` when their
box touches such a region. **Tell the user** before claiming furniture there is gone. The 3D editor warns once
and marks the title "⚠". Open such saves with the game folder they were played in (`set_game`, or
`--game "<Steam install>" --no-follow` on the command line).

## Code map (when you change the tools themselves)

| Path | What |
|---|---|
| `studio/agent/registry.py` | `@tool(name, group, description, writes=, game=, **params)` + argument checking |
| `studio/agent/tools_*.py` | the tools by group (core, files/tables, content, world, gui, media = sound+models, manager) |
| `studio/agent/context.py` | per-process state: Studio, world sessions, clipboards, follows the window's settings |
| `studio/agent/render.py` | PNG views: world top/3D (offscreen OpenGL), GUI layouts (PIL), models, waveforms |
| `studio/agent/mcp.py`, `cli.py` | MCP stdio server (stdlib only) and command line |
| `studio/agent/live.py`, `studio/ui/agentlink.py` | link to the running window (`%APPDATA%\NarewStudio\agent\`) |
| `studio/worldedit.py` | headless world editing + furniture rules shared with the 3D editor (`ui/worldview.py`) |
| `studio/*.py` (svc_*, creator, guilayout, audio, savedworlds) | the studio services the tools call |

Tests (never touch the real install; they build a fake game folder):

```
python tests/test_agent.py "<game folder>" <scratch dir>
python tests/test_agent_live.py "<game folder>" <scratch dir>
```

Run them after changing tools; `tests/test_place_products.py` covers the shared furniture rules in the 3D editor.
Docs: `docs/09-ai-agents.md` (Korean, for the user), `docs/05-brick-formats.md` (world format), `docs/07-new-content.md`.
