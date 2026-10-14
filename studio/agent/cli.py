"""Command line for agents without MCP (and for scripts).

    python -m studio.agent tools [group]                 list tools (--json: full schemas)
    python -m studio.agent call <tool> [args] [--pretty]  one tool; args = JSON object, @file.json, or key=value ...
                                                          (--pretty: ASCII grids / XML as readable text)
    python -m studio.agent run <script.json | ->          [{"tool": .., "args": {..}}, ..] in one process
    python -m studio.agent serve                          JSON lines on stdin -> one result line each
    python -m studio.agent mcp                            MCP server on stdio
    python -m studio.agent guide [topic]

Options (before the command): --game DIR, --project DIR (use these folders without changing the NarewStudio
window's settings), --no-follow (do not follow the window's game/project changes).
World sessions live as long as the process: use run/serve/mcp for open -> edit -> save sequences.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import guide
from .mcp import render_result, to_json


def _load_tools():
    from . import tools_content, tools_core, tools_files, tools_gui, tools_manager, tools_media, tools_world  # noqa: F401


def parse_args(items: list[str]) -> dict:
    if not items:
        return {}
    first = items[0]
    if first.startswith('@'):
        with open(first[1:], encoding='utf-8') as f:
            return json.load(f)
    if first == '-':
        return json.load(sys.stdin)
    if first.lstrip().startswith('{'):
        return json.loads(' '.join(items))
    out = {}
    for it in items:
        if '=' not in it:
            raise SystemExit(f'argument {it!r}: use key=value, a JSON object, or @file.json')
        k, v = it.split('=', 1)
        try:
            out[k] = json.loads(v)
        except ValueError:
            out[k] = v
    return out


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ['--apply-update'] and len(argv) == 2:     # the updater, run from the new version's folder
        from ..selfupdate import apply_job
        return apply_job(argv[1])
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding='utf-8')
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog='narew-agent', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--game')
    ap.add_argument('--project')
    ap.add_argument('--no-follow', action='store_true')
    ap.add_argument('command', choices=['tools', 'call', 'run', 'serve', 'mcp', 'guide'])
    ap.add_argument('rest', nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    _load_tools()
    from .registry import TOOLS, call

    def make_ctx():
        from .context import AgentContext
        return AgentContext(a.game, a.project, follow=not a.no_follow)

    if a.command == 'mcp':
        from .mcp import serve
        serve(make_ctx)
        return 0
    if a.command == 'guide':
        print(guide.text(a.rest[0] if a.rest else 'overview'))
        return 0
    if a.command == 'tools':
        as_json = '--json' in a.rest
        group = next((r for r in a.rest if not r.startswith('--')), None)
        order = ['core', 'files', 'tables', 'content', 'world', 'gui', 'sound', 'models', 'manager']
        rows = sorted((t for t in TOOLS.values() if not group or t.group == group),
                      key=lambda t: order.index(t.group) if t.group in order else 99)
        if as_json:
            print(to_json([{'name': t.name, 'group': t.group, 'description': t.describe(), 'inputSchema': t.schema}
                           for t in rows]))
        else:
            cur = None
            for t in rows:
                if t.group != cur:
                    cur = t.group
                    print(f'\n[{cur}]')
                flag = ' (game!)' if t.game else ' (writes)' if t.writes else ''
                print(f'  {t.name:28s}{flag} {t.description.split(". ")[0][:110]}')
        return 0
    ctx = make_ctx()
    if a.command == 'call':
        if not a.rest:
            raise SystemExit('call <tool> [args]')
        pretty = '--pretty' in a.rest
        rest = [r for r in a.rest if r != '--pretty']
        ok, data, images = call(ctx, rest[0], parse_args(rest[1:]))
        if ok and pretty:
            print('\n\n'.join(render_result(data)))
        else:
            print(to_json(data) if ok else data, file=sys.stdout if ok else sys.stderr)
        for p in images:
            print(f'[image] {p}', file=sys.stderr)
        return 0 if ok else 1
    if a.command == 'run':
        src = a.rest[0] if a.rest else '-'
        keep_going = '--keep-going' in a.rest
        steps = json.load(sys.stdin) if src == '-' else json.load(open(src, encoding='utf-8'))
        results, failed = [], False
        for i, st in enumerate(steps):
            ok, data, images = call(ctx, st['tool'], st.get('args') or {})
            results.append({'step': i, 'tool': st['tool'], 'ok': ok, ('result' if ok else 'error'): data,
                            'images': images})
            if not ok:
                failed = True
                if not keep_going:
                    break
        print(to_json(results))
        return 1 if failed else 0
    if a.command == 'serve':
        for raw in sys.stdin:
            line = raw.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
                ok, data, images = call(ctx, req['tool'], req.get('args') or {})
                out = {'id': req.get('id'), 'ok': ok, ('result' if ok else 'error'): data, 'images': images}
            except (ValueError, KeyError, TypeError) as ex:
                out = {'ok': False, 'error': f'bad request: {ex}'}
            sys.stdout.write(to_json(out, indent=None) + '\n')
            sys.stdout.flush()
        return 0
    return 2


if __name__ == '__main__':
    sys.exit(main())
