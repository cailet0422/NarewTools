# NarewTools — Islet Online Modding Toolkit

[한국어](README.md) · **English** · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-TW.md) · [日本語](README.ja.md)

A modding toolkit for Windows 10/11, built by reverse engineering **NarewEngine2**, the in-house engine of Islet Online.
Without touching the game files directly, you create **mods** that add blocks, furniture, items and sounds, or change textures, GUI screens, sounds, models
and worlds (maps). A mod manager puts them into the game and can always restore the originals. An MCP server lets AI agents (Claude Code and others) use the same features.

**[⬇ Download the latest version (Releases)](https://github.com/cailet0422/NarewTools/releases/latest)**

![NarewStudio world editor with the user's own world open](docs/images/studio-world.png)
<sub>A single-player world open in the world editor. The castle was built by an AI agent using only the NarewStudio tools.</sub>

> [!IMPORTANT]
> This is an unofficial fan tool. It is not affiliated with the game's developer or publisher, and neither this repository nor the releases **contain any game files**.
> You need a legitimately installed copy of the game. The game has online features, so test mods in single player (your own world) and check the game's terms of service yourself.
> Originals are backed up automatically before a mod is applied, but you use the tools at your own risk.

> [!NOTE]
> The programs' user interface is currently **Korean only**. In this README, button and menu names are given in Korean followed by an English translation,
> e.g. **새 모드** (New mod), so you can find them on screen. The technical documents in `docs/` are in Korean.

## Contents

- [What's included](#whats-included)
- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [First-time setup](#first-time-setup)
- [Quick start: your first mod](#quick-start-your-first-mod)
- [Using NarewStudio](#using-narewstudio)
- [Using NarewModManager](#using-narewmodmanager)
- [Mod format](#mod-format)
- [Connecting AI agents](#connecting-ai-agents)
- [Command line tools (narew)](#command-line-tools-narew)
- [Running from source · building · tests](#running-from-source--building--tests)
- [File locations](#file-locations)
- [Troubleshooting](#troubleshooting)
- [In-game verification and known limits](#in-game-verification-and-known-limits)
- [Folder layout and technical docs](#folder-layout-and-technical-docs)
- [License](#license)

## What's included

| Program | What it does |
|---|---|
| **NarewStudio.exe** | Makes mods. It only **reads** the game files; changed files and added entries are saved into a mod project |
| **NarewModManager.exe** | Enables/disables and orders mods, applies them to the game, restores the originals |
| **NarewAgent.exe** | Bridge for AI agents (console). Exposes NarewStudio as 108 tools over MCP and a command line |
| `narew` (Python) | Command line tools: unpack/build packs, models ↔ glTF, brick (voxel) file conversion (run from source) |

## Features

**NarewStudio**

| Page | Features |
|---|---|
| 리소스 편집 (Resource editor) | Browse/search every file in the 20 game packs + `Language\`. Images (original/modified side by side, automatic conversion to the original format and size, colour adjustment), sounds, text/XML (encoding preserved, syntax highlighting), fonts, hex view. Drop a file to replace |
| 블록 텍스처 (Block textures) | Replace/recolour tiles of the `terrain.png` block atlas, free slots shown |
| 가구 텍스처 (Furniture textures) | Replace per-material textures of 936 furniture models + 3D preview |
| 새 리소스 추가 (Add new content) | Wizards for **new block / furniture / item / sound** (free IDs computed, icons generated, crafting recipes) |
| GUI 편집 (GUI editor) | 169 game screen layouts drawn with the real images: move, resize, change text and colour, add new images/buttons/text |
| 사운드 편집 (Sound editor) | Waveform view, cut/paste/fade/volume/normalize/reverse/speed, saved in the original format |
| 월드 에디터 (World editor) | 3D editing of world maps (`.bwr`) and building prefabs (`.iaf`), furniture placement, **open/capture your own world (game save) and put it back into the game** |
| 모델 뷰어 (Model viewer) | 3,633 models and their animations, combine character parts, export/import glTF (Blender) |
| 팩 탐색기 (Pack explorer) | Extract, verify and build `.pack` files |
| 내 모드 (My mod) | List/revert changes, install into the mod manager, make a .zip for sharing |

**NarewModManager**: install mods (drag & drop a zip), enable/disable, priority, conflict display, **per-record merging** of XML tables (several mods can add entries to the same table), apply to the game / restore originals, detects Steam updates.

**AI agents**: an agent can directly handle requests like "build a small house next to the spawn and put a bed in it" or "move the inventory close button to the bottom left". It checks its work with ASCII maps and pictures, and an open NarewStudio window reloads the changes immediately.

### Screenshots

| Model viewer — combine character parts, play animations | GUI editor — screen layouts drawn with the real game images |
|---|---|
| ![Model viewer](docs/images/studio-models.png) | ![GUI editor](docs/images/studio-gui.png) |
| **Sound editor — waveform and range editing** | **Add new content — new furniture** |
| ![Sound editor](docs/images/studio-sound.png) | ![New furniture](docs/images/studio-create.png) |
| **Furniture textures — per-material textures, 3D preview** | **Block textures — the block atlas** |
| ![Furniture textures](docs/images/studio-furniture.png) | ![Block textures](docs/images/studio-blocks.png) |

## Requirements

- Windows 10 / 11 (64-bit)
- An installation of Islet Online (game folder = the folder that contains `IsletClient_release(x64).exe`)
- For the 3D views (world editor, model viewer, furniture preview): a graphics driver with **OpenGL 3.3**. Everything else works without it.
- Only when running from source: Python 3.10 or newer (tested with 3.12)

## Installation

1. Download `NarewTools-<version>.zip` from [Releases](https://github.com/cailet0422/NarewTools/releases/latest).
2. Extract it anywhere (e.g. `C:\Tools\`). There is no installer.
3. Run `NarewTools\NarewStudio.exe`.

```
NarewTools\
  NarewStudio.exe        make mods
  NarewModManager.exe    apply / restore mods
  NarewAgent.exe         for AI agents (console)
  사용법.txt              short guide (Korean)
  _internal\             Qt/Python runtime shared by all three programs — do not delete or move it separately
```

- The three exes share the `_internal` folder, so always move **the whole `NarewTools` folder**.
- The programs are not code-signed, so Windows SmartScreen may show "Windows protected your PC". Click **More info → Run anyway**.
- To update, replace the folder. Settings, mod projects and installed mods are stored in your user folders and stay ([File locations](#file-locations)).

## First-time setup

### 1. Choose the game folder

On first start, NarewStudio and NarewModManager ask for the game folder. Choose the folder that contains `IsletClient_release(x64).exe`.

- Steam default: `C:\Program Files (x86)\Steam\steamapps\common\Islet Online`
- Other Steam library: `<library>\steamapps\common\Islet Online`
- Not sure? In your Steam library right-click the game → **Manage → Browse local files**.

Both programs and the AI agent share these settings. To change it later, pick the game folder again in either program.

### 2. Create a mod project

Click **새 모드** (New mod) at the top of NarewStudio and fill in 모드 이름 (mod name), 만든 사람 (author), 버전 (version), 설명 (description) and 저장 위치 (location).
Projects go under `Documents\NarewStudio\Projects\` by default. From then on every edit goes into this project only; the game folder is not changed.

### 3. Check

- If the 3D views are black or show a notice, update your graphics driver (OpenGL 3.3 required).
- Errors are logged to `%APPDATA%\NarewStudio\log.txt` and `%LOCALAPPDATA%\NarewModManager\log.txt`.

## Quick start: your first mod

Example: add a new block and see it in the game.

1. **NarewStudio** → **새 모드** (New mod) to create a project.
2. Left menu **새 리소스 추가** (Add new content) → tab **새 블록** (New block).
   - Enter a name and description, pick a free block ID (suggested automatically) and a similar block whose properties are copied.
   - Add a texture image (same on all faces, or top/side/bottom). It is fitted to 128×128 automatically.
   - Optionally add a crafting recipe (type part of an item name or ID in the ingredient field to get a list).
   - Create: the atlas texture, block table entry, block item, name, icon and recipe are added to the mod.
3. Left menu **내 모드** (My mod) → **모드 매니저에 설치** (Install into mod manager).
4. **With the game closed**, open **NarewModManager**, tick the mod and click **게임에 적용** (Apply to game).
5. Start the game and check.
6. To undo, close the game and click **원본 복구** (Restore originals) in NarewModManager.

## Using NarewStudio

### 리소스 편집 (Resource editor)

- Browse packs/folders in the tree on the left, or type part of a file name in the search box.
- **Images**: original and modified side by side. Drop a new picture and it is converted to the original format and size. Hue, saturation, brightness, contrast and tint.
- **Text / XML / shaders**: edited with the original encoding (EUC-KR etc.), BOM and line endings preserved. Syntax highlighting, checking, search.
- **Sounds**: play, replace in the same format. For detailed editing use "사운드 편집기로 열기" (Open in sound editor).
- **외부 편집기로 열기** (Open in external editor): edit in Photoshop, Notepad etc., then apply.
- Changed files go into the mod's `files\`. They can be reverted one by one on the **내 모드** (My mod) page.

### 블록 텍스처 · 가구 텍스처 (Block and furniture textures)

- **Block textures**: the 16×16 tile atlases `terrain.png` (colour) and `terrain_specular.png` (specular). Selecting a tile shows which blocks use it; replace the image (fitted to 128×128), adjust colours, or revert.
- **Furniture textures**: choose a piece of furniture to see its per-material textures and a 3D preview. Only textures the game actually loads are linked.

### 새 리소스 추가 (Add new content)

| Tab | Input | What goes into the mod |
|---|---|---|
| 새 블록 (New block) | Name/description (Korean/English), free block ID, similar block (copies hardness, gathering tool, category), texture (all faces / top·side·bottom), specular (optional), recipe (optional) | Atlas texture, block table entry, block item, name, generated icon, recipe |
| 새 가구 (New furniture) | Name/description, free furniture ID, item ID, behaviour template (furniture whose sit/lie/light behaviour is copied), model (**copy existing furniture + new textures** or **import glTF**), icon (image or snapshot of the 3D preview), recipe | `.nwmt` model (+textures), furniture table entry, item, name, icon, recipe |
| 새 아이템 (New item) | Name/description, item ID, template item (food, potion, material, tool… — effects and values follow the template), icon, recipe | Item table entry, name, icon, recipe |
| 새 사운드 (New sound) | Sound file (.wav/.ogg/.mp3), name, weather/time and volume when used as background music, or where to use it as a UI sound | Sound file, `sky.xml` / `Gui\sound.xml` entries |
| 만든 것 (Created) | — | Entries this mod added/changed; remove them one by one |

ID ranges the engine accepts (game as of 2026-10):

| Kind | Usable IDs | Note |
|---|---|---|
| Block | 149, 160–170, 192–239 | 171–191 are used by old prefabs, avoided |
| Furniture | 58, 59, 597–599, 942–1023 | Engine limit 1–1023 |
| Item | 19300–19999 recommended | Engine limit 1–19999. A block's item has the block's ID |
| Crafting recipe | from 2599 downwards | Engine limit ≤ 2599 |

NarewStudio recomputes free IDs every time it opens a mod. After a game update, reopen your mod and check that no ID collides with official content.

### GUI 편집 (GUI editor)

- Draws the game's screen layouts (`Gui\*.xml` in `gui.pack`) with the real game images.
- Click and drag an element, or move it with the **arrow keys** (Shift = 10 px). **Ctrl+click** or drag on empty space to select several.
- Enter position/size numbers, change text, font size and colour, add elements with **＋ 이미지 / 버튼 / 글자** (+ Image / Button / Text) using game images or your own PNG, delete with **Delete**.
- Preview screen sizes (1920×1080 / 1600×900 / 1280×720), show hidden elements, edit the XML directly, undo.
- Button images open in the resource editor via "그림 편집" (Edit image). Only the changed numbers are rewritten, so the original file format is kept.

### 사운드 편집 (Sound editor)

- Shows and plays the waveform of game sounds (WAV, OGG, MP3). Select a range by dragging (or Shift+click), zoom with the wheel.
- Cut, copy, paste, keep selection only, silence, fade in/out, volume (dB), normalize, reverse, speed; undo/redo.
- Import from another file (sample rate and channels matched automatically), export as WAV/OGG/MP3.
- **모드에 저장** (Save to mod) keeps the original format, sample rate and channels.

### 월드 에디터 (World editor)

Edits game maps (`.bwr`) and building prefabs (`.iaf`) in 3D.

| Action | Keys |
|---|---|
| Move | W A S D, up/down Q E |
| Rotate / pan / zoom | left drag / middle drag / wheel |
| Undo / redo / save | Ctrl+Z / Ctrl+Y / Ctrl+S |
| Rotate furniture 90° | R |

- Tools: view, place, erase, paint, eyedropper, fill/hollow box, paste prefab, **place furniture**, start position, delete object.
- **Place furniture**: choose a piece in the "설치물" (Furniture) tab on the right and click. Green = can be placed, red = blocked. The hidden collision cells under furniture are filled in automatically.
- Large maps only load around the camera (adjustable view distance). Click the minimap to jump.

**Editing your own world** (single-player world)

1. Under **💾 내 월드** (My worlds) open the game's save (`<game folder>\Local\<world>.bwr`). You get the state the game last saved.
2. Edit, then **저장** (Save) → saved as a new file in `Documents\NarewStudio\Worlds\`; the original stays untouched.
3. With that file open and **the game closed**, click **🎮 내 월드에 적용** (Apply to my world) → the game's save is replaced by this file.
   - The current save (`.bwr .lnd .npc .db`) is backed up first to `Documents\NarewStudio\WorldBackups\<world>_<time>\`.
   - **↩ 백업으로** (Back to backup) restores it at any time.
   - Refused while the game is running. Warns if the game saved after your edited file was made.

**World capture** — keep the world you are playing as a `.bwr`

- **📸 현재 월드 저장** (Save current world) or **캡처 창** (Capture window, a small always-on-top window). **Ctrl+Alt+S** works even while in the game.
- With "게임이 저장할 때마다 자동으로 남기기" (Keep a copy on every game save) a copy is kept every time the game saves.
- To show only the capture window while playing: `NarewStudio.exe --capture`.
- What is saved is **the state the game last saved** (no code is injected into the game). Only worlds saved on your own PC can be captured.

### 모델 뷰어 · Blender (Model viewer)

- Browse models by kind and play animations. For characters, double-click clothes, hair, face and shoe parts to combine them in one scene.
- **Export to glTF** → edit in Blender → **import from glTF** to change models and animations. Adding new animations and changing playback speed is supported too.
- Blender tips: set the scene frame rate to **30 fps** before importing, and enable **Include → Custom Properties** when exporting (keeps the original data).

### 팩 탐색기 · 내 모드 (Pack explorer · My mod)

- **Pack explorer**: pack list and file details (compression, MD5), extract a pack/folder/everything (MD5 verified), build a pack from a folder.
- **My mod**: mod info and preview image, list of changed files and table merges (revert per entry), **모드 매니저에 설치** (Install into mod manager), **배포용 .zip 만들기** (Make .zip for sharing).

## Using NarewModManager

1. Choose the game folder (shared with NarewStudio).
2. Install mods: drag a mod `.zip` onto the window / **＋ .zip 설치…** (Install .zip) / **폴더에서 설치…** (Install from folder) / **모드 매니저에 설치** in NarewStudio.
3. Tick to enable; drag or use ▲▼ to order them (**top wins**). The right side shows the mod's files and table merges and files hidden by other mods.
4. **With the game closed**, click **게임에 적용** (Apply to game). To undo, **원본 복구** (Restore originals).

![NarewModManager — installed mods and their priority](docs/images/modmanager.png)

Apply modes:

| Mode | What happens |
|---|---|
| Pack patch (default, recommended) | The original pack is kept once as `data\<pack>.pack.orig`; each apply rebuilds the pack from the original + enabled mods. Unchanged files are copied without recompression |
| Loose files (experimental) | Mod files are placed loose in the game folder; packs are not touched. Only verified for GUI images |

- **Table merging**: the `merge\` patches of enabled mods are applied from lowest to highest priority. If two mods change the same entry, the upper one wins. Original encoding and line endings are kept.
- Loose data files in the game folder (`Language\string.xml` etc.) are backed up once as `<file>.orig` before being changed. Executables and DLLs are never touched.
- When a **Steam update** or "Verify integrity of game files" changes files, the next start detects it and tells you. Click **게임에 적용** (Apply to game) again.

## Mod format

A mod is a folder with this layout (zipped for sharing).

```
my-mod/
  mod.json        {"id": "pastel-blocks", "name": "Pastel blocks", "version": "1.0.0", "author": "", "description": "", "format": 2}
  preview.png     (optional) picture shown in the list
  files/          files replaced whole or added — same path as inside the game packs (case-insensitive)
    terrain.png
    Item/Icon/brick/icon_brick_149.png
  merge/          per-record patches for game tables (XML)
    XML/item_table.xml
    Language/string.xml
```

A `merge\` patch (UTF-8). An entry with the same key is replaced whole; otherwise it is appended to the table.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<merge>
  <brick id="149"> … whole entry … </brick>
  <item id="149"> … </item>
</merge>
```

- `files\` replaces whole files. If two mods replace the same file, only the higher-priority one is used.
- `merge\` works per entry, so several mods can each add entries to the same table.
- **Do not redistribute whole original game files.** Include only what you changed or created (NarewStudio's "배포용 .zip 만들기" does exactly that).
- Details (Korean): [docs/07-new-content.md](docs/07-new-content.md)

## Connecting AI agents

Every NarewStudio feature is available as **108 tools** (maps, buildings and furniture placement; new blocks, furniture, items and sounds; any game file; XML tables; GUI; sound; models; packs; the mod manager).
Agents check their work with ASCII maps and pictures (top-down map, 3D, GUI, model, waveform).

| 3D picture (`world_render` view=iso) | Floor plan from above (view=top) |
|---|---|
| ![3D picture the agent sees](docs/images/agent-castle-3d.png) | ![Floor plan the agent sees](docs/images/agent-castle-top.png) |

<sub>What the agent looked at while building the castle shown at the top. Orange boxes = furniture.</sub>

### Claude Code

**From source (this repository)** — the simplest way.

```bash
git clone https://github.com/cailet0422/NarewTools.git
```
```bash
pip install -r NarewTools/requirements.txt
```

Start Claude Code in the `NarewTools` folder; the **narew-studio** server from `.mcp.json` is picked up (approve it once). Instructions for the agent are in `AGENTS.md` (loaded by `CLAUDE.md`).

**With the release exe** — no Python needed:

```bash
claude mcp add narew-studio -- "C:\Tools\NarewTools\NarewAgent.exe" mcp
```

### Other MCP clients (Claude Desktop etc.)

Register `NarewAgent.exe mcp` as a stdio server. Example `claude_desktop_config.json` for Claude Desktop:

```json
{
  "mcpServers": {
    "narew-studio": {
      "command": "C:\\Tools\\NarewTools\\NarewAgent.exe",
      "args": ["mcp"]
    }
  }
}
```

From source: `python -m studio.agent mcp` (working directory = `NarewTools`).

### Command-line-only agents

```bash
NarewAgent.exe tools
```
```bash
NarewAgent.exe call status --pretty
```
```bash
NarewAgent.exe guide world
```

- `call <tool> key=value …` for one call, `run steps.json` for several steps (keeps world sessions), `serve` for JSON lines.
- From source use `python -m studio.agent` instead of `NarewAgent.exe`.
- Guide topics: `overview`, `world`, `content`, `gui`, `sound`, `models`, `mod`, `safety`.

### Working with the NarewStudio window · safety

- The agent and the window share settings (game folder, mod project). When the agent saves, the window reloads within a second (unsaved edits in the window are never overwritten).
- The agent can see which world and camera position the window shows, so "build it here" works.
- The game folder is read-only. Tools that really change the game (`world_apply_to_game`, `world_restore_backup`, `manager_apply`, `manager_restore`) only report what would change unless called with `confirm=true`, and agents run them **only after asking you**. They refuse while the game runs and back up first.
- Details (Korean): [docs/09-ai-agents.md](docs/09-ai-agents.md)

## Command line tools (narew)

Needs only Python 3.10+ (Pillow optional, for putting textures into `.glb`). Run in the repository folder.

```bash
python -m narew --help
```

**Pack files**

```bash
python -m narew info "<game folder>\data"
```
```bash
python -m narew list "<game folder>\data\gui_skin.pack" -f "*login*"
```
```bash
python -m narew extract "<game folder>\data" -o D:\extracted --verify
```
```bash
python -m narew verify "<game folder>\data\item.pack"
```
```bash
python -m narew build D:\extracted\xml -o D:\out\xml.pack
```
```bash
python -m narew patch D:\work\gui_skin.pack D:\mymod
```

- `extract` writes `<out>\<pack>\` and `<pack>.narew.json` (original order and attributes). `build` uses it, so an unmodified folder rebuilds a byte-identical pack.
- `patch` replaces/adds files inside a pack from an overlay folder (an in-place patch makes a `.orig` backup the first time). To put mods into the game, the mod manager is recommended.

**Models ↔ glTF**

```bash
python -m narew model2gltf D:\extracted\effect\Effect\brick_effect.nwmt -o brick.glb
```
```bash
python -m narew gltf2model D:\work\f1_edit.glb -o D:\work\f1_mod
```
```bash
python -m narew gltf2anim D:\work\f1_edit.glb -s f1.nwbt -t f1_combat_attack_onewp01.nwat -o out.nwat
```

**Brick (voxel) files** — `.iaf` prefabs, `.bwr` worlds

```bash
python -m narew brick info D:\extracted\map\map\64_0.bwr
```
```bash
python -m narew brick export D:\extracted\architecture\Architecture\building\Choga_big_A01.iaf -o choga.vox
```
```bash
python -m narew brick import mybuilding.vox -o mybuilding.iaf
```
```bash
python -m narew brick paste D:\work\64_0.bwr mybuilding.iaf --at 24,110,24 -o D:\work\64_0_mod.bwr
```

- `export` picks the format from the extension: `.vox` (MagicaVoxel), `.png` (`--view top|front|side`), `.json`.
- Details (Korean): [docs/04-model-formats.md](docs/04-model-formats.md), [docs/05-brick-formats.md](docs/05-brick-formats.md)

## Running from source · building · tests

```bash
git clone https://github.com/cailet0422/NarewTools.git
```
```bash
cd NarewTools
```
```bash
pip install -r requirements.txt
```
```bash
python -m studio
```
```bash
python -m modmanager
```

**Building the exes** (needs PyInstaller)

```bash
pip install pyinstaller
```
```bash
python tools\build_exe.py
```

The three exes are built into `dist\NarewTools\`; after each passes its self test (`--selftest`, the agent an MCP handshake) `dist\NarewTools-<version>.zip` is made.
If `dist\NarewTools` is in use, build elsewhere with `--dist <folder>`.

**Tests** — the real game folder is only read; apply tests run on a fake game folder made from copies of some packs.

```bash
python tests\test_agent.py "<game folder>" D:\scratch\t_agent
```
```bash
python tests\test_agent_live.py "<game folder>" D:\scratch\t_live
```

For the other tests (`tests\test_*.py`, `smoke_*.py`) see the arguments at the top of each file.

## File locations

| What | Where |
|---|---|
| NarewStudio settings and log | `%APPDATA%\NarewStudio\` (`log.txt`) |
| Mod projects | `Documents\NarewStudio\Projects\` (can be changed when creating a mod) |
| Saved/captured worlds | `Documents\NarewStudio\Worlds\` |
| Backups made before "apply to my world" | `Documents\NarewStudio\WorldBackups\` |
| Mod manager (installed mods, settings, log) | `%LOCALAPPDATA%\NarewModManager\` (`mods\`, `settings.json`, `deployed.json`, `log.txt`) |
| Original backups made when applying mods | `<game folder>\data\<pack>.pack.orig`, `<game folder>\<file>.orig` |
| AI agent ↔ window link | `%APPDATA%\NarewStudio\agent\` (no network or ports) |

## Troubleshooting

| Problem | Fix |
|---|---|
| "Windows protected your PC" | The programs are not code-signed. **More info → Run anyway** |
| 3D view is black or only shows a notice | Update the graphics driver (OpenGL 3.3 needed). On laptops, run on the high-performance GPU |
| Applying to the game fails | Close the game completely and retry. Details in `%LOCALAPPDATA%\NarewModManager\log.txt` |
| Mods gone after a Steam update | The update overwrote the packs. Click **게임에 적용** (Apply to game) again in the mod manager |
| Game behaves oddly / back to vanilla | **원본 복구** (Restore originals) in the mod manager. If that is not enough: Steam → Properties → Installed Files → **Verify integrity of game files** |
| Undo changes to my world | **↩ 백업으로** (Back to backup) in the world editor, or the files in `Documents\NarewStudio\WorldBackups\` |
| AI agent tools missing | In Claude Code check `/mcp` that narew-studio is approved. From source, make sure `pip install -r requirements.txt` ran |

## In-game verification and known limits

| Feature | Status |
|---|---|
| New blocks, new furniture, hand-crafting recipes, name strings | ✅ verified in game |
| UI sound replacement, GUI images and layout changes | ✅ verified in game |
| World editing (blocks, furniture) + apply to my world | ✅ verified in game |
| Edited models/animations loading in game | ⚠ not verified yet |
| Effects of generic new items, weather/time background music | ⚠ not verified yet |
| Multiplayer | ⚠ not verified yet |

- Furniture placement in the world editor does not yet support the 80 kinds that need their own data (signs, photos, garden plots, shops, mailboxes …).
- The user interface is Korean only for now.
- If an unverified feature causes problems, use **원본 복구** (Restore originals).

## Folder layout and technical docs

```
narew/       format library + command line tools (pack, brick, model ↔ glTF, mod format, table merge)
studio/      NarewStudio — core, svc_*, creator (new content), worldedit (headless world editing), gl/ (OpenGL), ui/ (Qt), agent/ (AI agents)
modmanager/  NarewModManager — core (apply engine), ui
tests/       tests (use a fake game folder)
tools/       build_exe.py (exe build), PE/RTTI analysis and model validation scripts
docs/        technical documents (Korean)
AGENTS.md    instructions for AI agents; .mcp.json registers the MCP server for Claude Code
```

| Document (Korean) | Contents |
|---|---|
| [01 Pack format](docs/01-pack-format.md) | `.pack` layout (all 20 packs rebuild byte-identical) |
| [02 Engine architecture](docs/02-engine-architecture.md) | NarewEngine2 layers and classes |
| [03 Roadmap](docs/03-roadmap.md) | Next steps |
| [04 Model formats](docs/04-model-formats.md) | `.nwmt/.nwbt/.nwat` ↔ glTF |
| [05 Brick formats](docs/05-brick-formats.md) | `.iaf/.bwr` voxel worlds |
| [06 Program manual](docs/06-studio-modmanager.md) | NarewStudio and NarewModManager in detail |
| [07 New content](docs/07-new-content.md) | Engine limits, table merge rules |
| [08 GUI layout](docs/08-gui-layout.md) | Editing `Gui\*.xml` |
| [09 AI agents](docs/09-ai-agents.md) | 108 tools, connection, safety |

The `re/` analysis output mentioned in the docs (game binary dumps, decompiler output) is not included in this repository.

## License

[MIT](LICENSE). Islet Online and its trademarks and assets belong to their respective owners.
