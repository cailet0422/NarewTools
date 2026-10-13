"""Tool registry shared by the MCP server and the command line.

A tool is a function ``fn(ctx, **args)`` registered with @tool, a JSON schema built from the
parameter helpers below, and flags: ``writes`` (changes the mod project or files on disk) and
``game`` (changes the real game install or its saves - only when the user asked for it).
A tool returns JSON-able data, or Result(data, images=[png paths]) to also show pictures.
"""
from __future__ import annotations

import os
import traceback
from dataclasses import dataclass, field

REQUIRED = object()


@dataclass
class Param:
    schema: dict
    default: object = REQUIRED


def _p(kind: str, desc: str, default=REQUIRED, **extra) -> Param:
    s = {'type': kind, 'description': desc, **extra}
    if default is not REQUIRED and default is not None:
        s['default'] = default
    return Param(s, default)


def INT(desc, default=REQUIRED, **kw):
    return _p('integer', desc, default, **kw)


def NUM(desc, default=REQUIRED, **kw):
    return _p('number', desc, default, **kw)


def STR(desc, default=REQUIRED, enum=None, **kw):
    if enum:
        kw['enum'] = list(enum)
    return _p('string', desc, default, **kw)


def BOOL(desc, default=REQUIRED):
    return _p('boolean', desc, default)


def ARR(desc, items: dict | None = None, default=REQUIRED, **kw):
    return _p('array', desc, default, items=items or {}, **kw)


def OBJ(desc, default=REQUIRED, **kw):
    return _p('object', desc, default, **kw)


def BOX(desc='Inclusive box [x0, y0, z0, x1, y1, z1] in game coordinates (corners in any order).',
        default=REQUIRED):
    return ARR(desc, {'type': 'integer'}, default, minItems=6, maxItems=6)


def POINT(desc, default=REQUIRED):
    return ARR(desc, {'type': 'number'}, default, minItems=3, maxItems=3)


SESSION = STR('World session id from world_open (e.g. "w1").')


@dataclass
class Result:
    data: object
    images: list[str] = field(default_factory=list)


@dataclass
class Tool:
    name: str
    group: str
    description: str
    params: dict[str, Param]
    fn: object
    writes: bool = False
    game: bool = False

    @property
    def schema(self) -> dict:
        return {'type': 'object',
                'properties': {k: p.schema for k, p in self.params.items()},
                'required': [k for k, p in self.params.items() if p.default is REQUIRED],
                'additionalProperties': False}

    def describe(self) -> str:
        tags = []
        if self.game:
            tags.append('CHANGES THE REAL GAME INSTALL OR ITS SAVES: only when the user asked for it')
        elif self.writes:
            tags.append('writes to the open mod project / files')
        return self.description + (f' [{"; ".join(tags)}]' if tags else '')


TOOLS: dict[str, Tool] = {}


def tool(name: str, group: str, description: str, /, writes: bool = False, game: bool = False, **params: Param):
    """Register fn(ctx, **params) as a tool (positional-only names leave 'name', 'group' ... free for params)."""
    def deco(fn):
        if name in TOOLS:
            raise ValueError(f'duplicate tool {name}')
        TOOLS[name] = Tool(name, group, description, params, fn, writes, game)
        return fn
    return deco


class ToolError(Exception):
    """Bad arguments: shown to the agent as-is."""


_TYPES = {'integer': int, 'number': (int, float), 'string': str, 'boolean': bool, 'array': list, 'object': dict}


def _check(name: str, value, schema: dict):
    kind = schema.get('type')
    want = _TYPES.get(kind)
    if want is None:
        return value
    if kind == 'integer' and isinstance(value, float) and value.is_integer():
        value = int(value)
    if kind == 'integer' and isinstance(value, str) and value.lstrip('-').isdigit():
        value = int(value)
    if kind == 'number' and isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            pass
    if isinstance(value, bool) and kind in ('integer', 'number'):
        raise ToolError(f'{name}: expected {kind}, got boolean')
    if not isinstance(value, want):
        raise ToolError(f'{name}: expected {kind}, got {type(value).__name__} ({value!r:.60})')
    if 'enum' in schema and value not in schema['enum']:
        raise ToolError(f'{name}: must be one of {schema["enum"]}, got {value!r}')
    if kind == 'array':
        n = len(value)
        if 'minItems' in schema and n < schema['minItems'] or 'maxItems' in schema and n > schema['maxItems']:
            raise ToolError(f'{name}: expected {schema.get("minItems")}..{schema.get("maxItems")} items, got {n}')
        item = schema.get('items') or {}
        if item.get('type') in ('integer', 'number'):
            value = [_check(f'{name}[{i}]', v, item) for i, v in enumerate(value)]
    return value


def bind(t: Tool, args: dict | None) -> dict:
    args = dict(args or {})
    unknown = [k for k in args if k not in t.params]
    if unknown:
        raise ToolError(f'unknown argument(s) {unknown} for {t.name}; allowed: {list(t.params)}')
    out = {}
    for k, p in t.params.items():
        if k in args and args[k] is not None:
            out[k] = _check(k, args[k], p.schema)
        elif p.default is REQUIRED:
            raise ToolError(f'missing required argument "{k}" for {t.name}: {p.schema.get("description", "")}')
        else:
            out[k] = p.default
    return out


def call(ctx, name: str, args: dict | None) -> tuple[bool, object, list[str]]:
    """Run a tool. Returns (ok, data or error text, image paths)."""
    from ..core import StudioError
    t = TOOLS.get(name)
    if t is None:
        close = [n for n in TOOLS if name.split('_')[0] in n][:12]
        return False, f'unknown tool {name!r}. ' + (f'Similar: {close}' if close else 'Use list_tools.'), []
    try:
        kw = bind(t, args)
        ctx.before_call(t)
        res = t.fn(ctx, **kw)
        if isinstance(res, Result):
            return True, res.data, [p for p in res.images if p and os.path.isfile(p)]
        return True, res, []
    except (ToolError, StudioError) as ex:
        return False, str(ex), []
    except (ValueError, KeyError, FileNotFoundError, FileExistsError, PermissionError) as ex:
        return False, f'{type(ex).__name__}: {ex}', []
    except Exception as ex:                       # report, keep serving
        tb = traceback.format_exc().strip().splitlines()
        return False, f'{type(ex).__name__}: {ex}\n' + '\n'.join(tb[-6:]), []
