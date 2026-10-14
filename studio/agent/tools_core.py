"""Session, game folder, mod project, and the link to the NarewStudio window."""
from __future__ import annotations

import os

from narew.modfmt import ModFolder, ModInfo, slug

from .. import svc_resources as R
from ..core import StudioError
from . import guide, live
from .registry import BOOL, STR, TOOLS, tool


def project_summary(ctx) -> dict | None:
    st = ctx.st
    if not st.project:
        return None
    try:
        info = st.project.read_info().to_dict()
    except Exception as ex:
        info = {'error': str(ex)}
    return {'root': st.project.root, **info, 'changed_files': len(st.project_files()),
            'table_patches': len(st.project_merges()),
            'code_patches': [name for name, _ in st.project.iter_patches()]}


@tool('status', 'core',
      'Start here. Game folder, open mod project, open world sessions, whether the NarewStudio window '
      'is running and what it shows, and next-step hints.')
def status(ctx):
    st = ctx.st
    gui = live.gui_state()
    hints = []
    if not st.game:
        hints.append('No game folder: call set_game with the Islet Online install folder.')
    if not st.project:
        hints.append('No mod project: project_new (or project_open) before editing - all edits go into the project.')
    if gui and gui.get('project') and st.project and \
            os.path.normcase(gui['project']) != os.path.normcase(st.project.root):
        hints.append('The window has another project open than this agent.')
    hints.append('guide(topic) explains workflows: overview, world, content, gui, sound, models, mod, safety.')
    return {
        'game': st.game.root if st.game else None,
        'project': project_summary(ctx),
        'world_sessions': [{'session': sid, 'name': s.doc.name, 'dirty': s.dirty(),
                            'edits': len(s.undo_stack)} for sid, s in ctx.sessions.items()],
        'clipboard': {cid: {'size_xyz': c.size, 'products': len(c.products), 'from': c.source}
                      for cid, c in ctx.clips.items()},
        'studio_window': gui and {k: gui.get(k) for k in ('page', 'project', 'open', 'camera', 'selection')},
        'follows_window_settings': ctx.follow,
        'output_dir': ctx.out_dir,
        'hints': hints,
    }


@tool('guide', 'core', 'How-to for agents: coordinate system, rules, recommended workflows, examples.',
      topic=STR('Topic.', 'overview', enum=guide.TOPICS))
def guide_tool(ctx, topic):
    return {'topic': topic, 'text': guide.text(topic)}


@tool('list_tools', 'core', 'All tools by group with one-line descriptions (filter by group or text).',
      group=STR('Only this group (core, files, tables, content, world, gui, sound, models, manager).', None),
      query=STR('Only tools whose name or description contains this.', None))
def list_tools(ctx, group, query):
    out = {}
    for t in TOOLS.values():
        if group and t.group != group:
            continue
        if query and query.lower() not in (t.name + ' ' + t.description).lower():
            continue
        out.setdefault(t.group, []).append({'name': t.name, 'about': t.describe().split('. ')[0],
                                            'writes': t.writes, 'game': t.game})
    return out


@tool('set_game', 'core', 'Choose the Islet Online install folder (read-only for the studio; packs are indexed).',
      path=STR('Folder containing data\\*.pack and the client exe.'))
def set_game(ctx, path):
    ctx.st.open_game(path)
    ctx._cache.clear()
    live.post('game', root=ctx.st.game.root)
    return {'game': ctx.st.game.root, 'packs': len(ctx.st.game.readers), 'entries': len(ctx.st.game.index)}


@tool('project_list', 'core', 'Mod projects: recent ones and every folder with mod.json in the projects folder.')
def project_list(ctx):
    st = ctx.st
    seen, out = set(), []
    cands = list(st.settings.get('recent_projects', []))
    d = st.default_projects_dir()
    if os.path.isdir(d):
        cands += [os.path.join(d, n) for n in sorted(os.listdir(d))]
    for p in cands:
        k = os.path.normcase(os.path.abspath(p))
        if k in seen or not os.path.isfile(os.path.join(p, 'mod.json')):
            continue
        seen.add(k)
        try:
            info = ModFolder(p).read_info().to_dict()
        except Exception as ex:
            info = {'error': str(ex)}
        out.append({'root': p, **info, 'open': bool(st.project and os.path.normcase(st.project.root) == k)})
    return {'projects_dir': d, 'projects': out}


@tool('project_new', 'core', 'Create a mod project in the projects folder and open it (the window follows).',
      writes=True,
      name=STR('Display name, e.g. "파스텔 블록".'),
      id=STR('Folder/mod id: lowercase a-z0-9._- (default: made from the name).', None),
      version=STR('Version.', '1.0.0'),
      author=STR('Author.', ''),
      description=STR('Description.', ''))
def project_new(ctx, name, id, version, author, description):
    info = ModInfo(id=id or slug(name), name=name, version=version, author=author, description=description)
    info.validate()
    root = ctx.st.new_project(ctx.st.default_projects_dir(), info)
    live.post('project', root=root)
    return project_summary(ctx)


@tool('project_open', 'core', 'Open an existing mod project folder (or its mod.json); the window follows.',
      path=STR('Project folder.'))
def project_open(ctx, path):
    ctx.st.open_project(path)
    live.post('project', root=ctx.st.project.root)
    return project_summary(ctx)


@tool('project_info', 'core', 'The open project: mod.json fields, counts and every changed file / table patch.')
def project_info(ctx):
    ctx.st.need_project()
    return {**project_summary(ctx), 'changes': R.changes(ctx.st)}


@tool('project_set_info', 'core', 'Change mod.json fields of the open project (id cannot change).', writes=True,
      name=STR('Display name.', None), version=STR('Version.', None), author=STR('Author.', None),
      description=STR('Description.', None))
def project_set_info(ctx, name, version, author, description):
    p = ctx.st.need_project()
    info = p.read_info()
    for k, v in (('name', name), ('version', version), ('author', author), ('description', description)):
        if v is not None:
            setattr(info, k, v)
    p.write_info(info)
    return project_summary(ctx)


@tool('project_revert', 'core',
      'Drop the project\'s version of a file (back to the game original). merge=true drops the whole table '
      'patch of that table instead (all records this mod added/changed there).', writes=True,
      name=STR('Resource entry name, e.g. "terrain.png" or "XML\\item_table.xml".'),
      merge=BOOL('Remove the table patch (merge/) instead of the file override (files/).', False))
def project_revert(ctx, name, merge):
    st = ctx.st
    p = st.need_project()
    if merge:
        ok = p.remove_merge(st.canonical_name(name))
        st.invalidate()
    else:
        ok = st.revert(name)
    ctx.notify([name])
    return {'name': name, 'removed': ok, 'status': st.status_of(name)}


@tool('project_set_preview', 'core', 'Set the mod\'s preview.png (shown in the mod manager) from an image file.',
      writes=True, path=STR('Image file.'))
def project_set_preview(ctx, path):
    R.set_preview(ctx.st, path)
    return {'preview': os.path.join(ctx.st.project.root, 'preview.png')}


@tool('project_export_zip', 'core', 'Package the open project as a distributable mod .zip.', writes=True,
      path=STR('Output .zip path.'))
def project_export_zip(ctx, path):
    n = R.export_zip(ctx.st, path)
    return {'path': path if path.lower().endswith('.zip') else path + '.zip', 'bytes': n}


@tool('studio_view', 'core',
      'What the NarewStudio window shows now: page, opened world (and camera target = where the user is '
      'looking), GUI layout and selection, resource. Use it to edit "here" / "this". None if not running.')
def studio_view(ctx):
    s = live.gui_state()
    if not s:
        return {'running': False}
    return {'running': True, **{k: v for k, v in s.items() if k not in ('time',)}}


@tool('studio_show', 'core',
      'Ask the running NarewStudio window to open a page and item so the user sees the result, e.g. '
      'page="world" + path of a .bwr / name of a pack world, page="gui" + layout name, page="resources" + entry.',
      page=STR('Page.', enum=('resources', 'blocks', 'furniture', 'create', 'gui', 'sound', 'world', 'models',
                              'explorer', 'project')),
      name=STR('Entry name to open (pack world/prefab, GUI layout, resource, sound, model).', None),
      path=STR('A .bwr file outside the packs (world page only).', None))
def studio_show(ctx, page, name, path):
    if not live.gui_state():
        raise StudioError('NarewStudio 창이 실행 중이 아닙니다 (studio_view 참고)')
    seq = live.post('show', page=page, name=name, path=os.path.abspath(path) if path else None)
    return {'requested': True, 'seq': seq}
