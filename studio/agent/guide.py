"""How-to text for agents (the `guide` tool and the MCP server instructions)."""
from __future__ import annotations

TOPICS = ('overview', 'world', 'content', 'gui', 'sound', 'models', 'mod', 'safety')

OVERVIEW = """\
NarewStudio = modding studio for Islet Online (NarewEngine2). You drive it through these tools.

Model of the world
- The game install is READ-ONLY for the studio. Every change goes into the open MOD PROJECT
  (Documents\\NarewStudio\\Projects\\<id>: files/ = replaced or new files, merge/ = table records).
- A mod reaches the game only through the mod manager (manager_install_project -> manager_apply), or, for the
  user's own single-player world, world_save + world_apply_to_game. Both ask for confirm=true: ask the user.
- The NarewStudio window shares settings with you: same game folder and project. When it runs, it reloads what
  you change; studio_view tells you what the user is looking at, studio_show opens your result there.

First calls
1. status            -> game folder, project, window state, hints
2. project_new / project_open (if no project)
3. the feature's tools (list_tools(group=...) lists them; guide(topic) explains)

Groups: core, files (any resource, images, text), tables (XML records, strings), content (new blocks, furniture,
items, sounds; catalogs), world (maps and prefabs), gui (screen layouts), sound, models, manager (mod manager).

Resource names are pack entry paths with backslashes, case-insensitive: "terrain.png", "XML\\item_table.xml",
"Gui\\inventory.xml", "Sound\\...", "Object\\obj_chair001.nwmt", "map\\64_0.bwr", "Language\\string.xml".
Errors come back in Korean (the user's language); answer the user in Korean too.
Pictures (world_render, gui_render, image_view, model_render, sound_info) are PNG files in the output folder and
are also attached to the result - look at them to check your work.
"""

WORLD = """\
World editing (maps .bwr, prefabs .iaf)

Coordinates: x and z horizontal, y up. Worlds are W x 256 x D bricks (shipped maps 64..1024 wide), regions are
16x16 columns. Brick 0 = air, 1..254 = brick types (bricks_list), 255 = invisible collision filler that the
game puts under furniture (never paint with it; edits leave it alone by default).
In ASCII views and top pictures: x grows to the right, z grows downward.

Open
- world_list (kind="save" for the user's own worlds: <game>\\Local\\<world>.bwr is the world they play in
  single player; Worlds\\*.bwr are copies)
- world_open(name="map\\64_0.bwr") or world_open(path=...) -> session "w1". No args = what the window shows.
- objects_unreadable_regions in world_open/world_info: some regions hold furniture the open game folder's
  tables do not know (a save played with a mod) - their objects are not listed and world_clear cannot delete
  them (they are saved back unchanged). Tell the user; open the save with the game folder it was played in
  (the hint says which: set_game, or --game "<install>" --no-follow) before editing furniture there.

Look before you build
- world_info: size, start position (spawn) and the ground height there
- world_heightmap(area=[x0,z0,x1,z1]): surface heights; build on top_y + 1
- world_layer(y, area): ASCII of one layer;  world_section: vertical cut
- world_render(view="top", area, y_max=...): picture from above (y_max = floor plan)
- world_render(view="iso", box): 3D picture; check it after building
- world_objects / world_ground / world_stats / world_find

Edit (every call = one undo step; world_undo/world_redo)
- world_fill(box, brick, mode=solid|hollow|outline|walls|frame, replace=[ids], only_air)
- world_clear(box)  (also deletes furniture anchored inside)
- world_replace(box, old=[ids], new)
- world_set_blocks([[x,y,z,id], ...])
- world_shape(sphere|dome|cylinder|cone|pyramid|line, at, radius, height, hollow)
- world_build(origin, layers, legend): ASCII blueprint, layers bottom-up, row = +z, column = +x, space = keep
- world_copy(box) -> clip; world_paste(at, clip|prefab, rotate); world_save_prefab(name, clip|session+box)
- pictures: world_image(image=<png/jpg>, at=low corner, width, facing="-z"|"+z"|"-x"|"+x"|"up") -> pixel art;
  facing = the side people look from (game space is left-handed: looking toward +z, +x is on the right);
  double_sided walls (default) read right from both sides; dry_run=true first to look at the preview
- furniture: products_list(placeable_only=true) -> world_place(product, x, z[, y, rot]) / world_place_many;
  y defaults to standing on the ground; the whole footprint must be air; world_delete_objects(ids|box)
- lights: world_switch(ids|box, on=true|false); world_objects shows on=true/false for every lamp
- turn placed furniture: world_rotate(ids|box, rot=0-3 | turn=steps) about its anchor; collision moves with it,
  the object stays (container items, lamp on/off); blocked ones are listed in "failed"
- world_set_start(x, z): spawn point

Save
- pack maps/prefabs -> into the mod project (world_save)
- the user's own world: open the save (world_list kind=save), edit, world_save -> writes a NEW file
  Documents\\NarewStudio\\Worlds\\<world>_edit.bwr (the game's save is never overwritten), then
  world_apply_to_game(session=...) shows a dry run; with the user's OK call again with confirm=true
  (the game must be closed; the current save is backed up; world_restore_backup undoes it).
- bigger worlds: the game only makes 1024x1024. world_new(width=2048) writes a flat world 16..4096 wide to
  Documents\\NarewStudio\\Worlds; world_apply_to_game(path, new_world="t2048") adds it as a new Local world
  (resize=true replaces one of another size; world_remove_from_game takes it out again). Tell the user
  limits.notes: the game server needs ~69 KB of RAM per 16x16 region (2048 ~1 GB, 4096 ~4 GB). 2048 and 4096
  were verified in game; bigger worlds are refused.

Heights of furniture (measured, not an engine rule):
- shipped maps: ~90% of objects stand right on the supporting brick (anchor y = brick y + 1); the default.
- the user's flat single-play world (ground = brick #60 at y=0, player start y=1): every object the GAME placed
  there (treasure chests, a chair the user placed) is at y=2, one layer above the ground. A floor you build
  yourself (e.g. the gold pavilion floor at y=1) takes furniture right on it (y=2) - confirmed visible in game.
- world_place without y uses "right on the brick" and adds a note when nearby game-placed objects do otherwise.
Always measure with world_ground / world_heightmap / world_objects instead of assuming.

Lights (lamps, candles, street lights: products of kind "on" in products_list):
- a lamp only shines in game when its object state has bit 0x10 (IsUse) set; the light itself comes from the
  model's <light_list>. Shipped maps store most lamps on (state 16: 191 of 297; lobby maps nearly all).
- world_place / world_place_many place them ON (on=false for off); world_paste keeps the copied on/off;
  world_switch turns existing ones on/off (only bit 0x10 changes).
- wall-mounted products (wall lamps, sconces): rot 0 hangs on a wall at +z of the anchor cell, rot 1 at +x,
  rot 2 at -z, rot 3 at -x (verified on shipped maps). Put the anchor in the air cell in front of the wall.

Recipe: small house on flat ground at (x0, z0)
1. g = world_ground(x0, z0) -> base y = g.place_at_y
2. world_fill(box=[x0, y, z0, x0+8, y, z0+6], brick=<floor>)
3. world_fill(box=[x0, y+1, z0, x0+8, y+4, z0+6], brick=<wall>, mode="walls")
4. world_fill(box=[x0+4, y+1, z0, x0+4, y+2, z0], brick=0)            (door)
5. world_fill(box=[x0-1, y+5, z0-1, x0+9, y+5, z0+7], brick=<roof>)
6. world_place(product=<bed>, x=x0+2, z=z0+2)  (y defaults to the floor + 1)
7. world_render(view="iso", box=[x0-2, y-1, z0-2, x0+10, y+7, z0+8]) and fix what looks wrong
8. world_save
"""

CONTENT = """\
New content (all written into the mod project as files + table records)

Catalogs: bricks_list, products_list, items_search, content_slots, content_free_ids.
Textures: texture_make makes a quick PNG (colour + pattern) if you have no image.

- content_new_block(name_kor, textures={"all": png} or {"top","side","bottom"}, template_brick=<similar brick>,
  recipe={...}) -> new brick id usable in the world tools right away (brick ids 1-254; 171-191 reserved).
- content_new_furniture(name_kor, template_product=<behaviour: sit/sleep/light...>, model_name="my_x.nwmt",
  copy_model="Object\\obj_....nwmt" [+ retexture={texture entry: png}] or gltf=..., icon=png) -> product id for
  world_place. furniture_textures(product) lists the model's textures.
- content_new_item(name_kor, template_item=<similar item>, icon=png, recipe)
- content_new_sound(src, entry="Sound\\mod\\x.ogg", sky=[[time, weather]], ui=["click"])
- recipe = {"station": "hand"|"furniture"|"tool"|..., "ingredients": [[item_id, count], ...], "count": 1}
- content_added lists what the mod added; table_remove takes one out.
Block atlas: block_tile (look), block_tile_set / block_tile_adjust / block_tile_reset, block_free_tiles.
Tables in general: table_records (read with the mod applied), table_upsert (add/replace one record),
string_get / string_set (Language\\string.xml). Names of items live in string table "item_name" by item id.
Verified in game: new blocks, new furniture, hand recipes, string names. Generic new items: not yet verified.
"""

GUI = """\
GUI screens (gui.pack Gui\\*.xml)
- gui_list -> gui_tree(name) -> node indices, absolute boxes, text, textures
- gui_render(name, highlight=[i]) -> picture with index numbers on every element
- gui_edit(name, ops=[...]) saves to the mod. Ops: move (local pos), move_by, resize, text, font_size, color,
  texture (path under gui_skin; see gui_textures), show (visible), attrs, remove, add (image|button|extended_text
  under a parent index). Indices refer to the layout before each op.
- Button/image pictures are gui_skin images: change them with file_import / image_adjust on "gui_skin\\...".
Verified in game: moved elements and changed button images show up.
"""

SOUND = """\
Sounds (246 in the game: .wav/.ogg/.mp3)
- sound_list, sound_info (length, rate, waveform picture)
- sound_edit(name, ops=[{"op":"keep","start":0,"end":1.2}, {"op":"fade_out","start":1.0}, {"op":"gain","db":-3}])
  -> saved in the original format/rate/channels.
- sound_import(name, path): replace with any audio file (converted); sound_export to listen elsewhere.
- New sounds / BGM / UI sounds: content_new_sound. UI click sound replacement is verified in game.
"""

MODELS = """\
Models and animations
- models_list, model_info(names) (skeleton + animations), model_render(names, anims, time) to look.
- model_export_gltf -> edit in Blender (30 fps; export with Include > Custom Properties) -> model_import_gltf.
- model_import_new (new model entry from glTF), anim_import_gltf, anim_speed(name, factor).
In-game loading of edited models is not verified yet - say so when you deliver such changes.

Character swap (another 3D model as the player character, both genders, the game's own animations)
- character_inspect(src): format, meshes, textures, whether a humanoid skeleton was found (bone mapping).
  FBX, glTF/GLB, VRM, OBJ, PMX are read directly; DAE/3DS/BLEND/USD/ABC/PLY/STL/X3D need Blender.
- character_swap(src, dry_run=true) -> preview picture; then character_swap(src) writes skeletons, the body,
  all animations (bone lengths + root motion fitted), textures, and hides worn parts (face, hair, clothes ...).
  Options: genders ["f1","m1"], height, arm_angle (12), leg_angle (auto), npcs, hide_parts, turn (if it faces away).
- character_swap_status / character_swap_remove. To play it: manager_install_project -> manager_apply (ask first).
- No humanoid skeleton (OBJ, statues): the model is glued to the pelvis. Verified in game with an FBX character.
"""

MOD = """\
Mod project and mod manager
- project_info lists every change; project_revert / file_revert / table_remove undo pieces.
- project_export_zip makes a shareable .zip (only changed files, never whole game files).
- manager_install_project copies the project into NarewModManager (enabled, top priority).
- manager_status / manager_set_mod (enable, priority).
- manager_apply(confirm) and manager_restore(confirm) write the game install: dry run first, ask the user,
  game closed. Undo = manager_restore.
"""

SAFETY = """\
Safety rules
- Never write into the game folder yourself (no file tools on <game>\\data or <game>\\Local). Use
  manager_apply / world_apply_to_game, which back up first - and only after the user said yes.
- Tools marked [CHANGES THE REAL GAME INSTALL OR ITS SAVES] take confirm=true; call them without it first,
  show the user the dry run, ask, then confirm.
- The game must be closed for applying (the tools refuse otherwise).
- Do not ship whole original game files in a mod zip.
- Online play: test mods in single player. Do not try to extract other players' or servers' worlds.
"""

_TEXT = {'overview': OVERVIEW, 'world': WORLD, 'content': CONTENT, 'gui': GUI, 'sound': SOUND, 'models': MODELS,
         'mod': MOD, 'safety': SAFETY}


def text(topic: str) -> str:
    return _TEXT.get(topic, OVERVIEW)


INSTRUCTIONS = (OVERVIEW + '\nWorld editing in short: world_open -> look (world_info, world_heightmap, '
                'world_render) -> edit (world_fill/build/shape/place...) -> world_render to check -> world_save. '
                'guide("world") has the details and a recipe.\n' + SAFETY)
