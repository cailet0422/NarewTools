"""Model Context Protocol server over stdio (newline-delimited JSON-RPC 2.0), standard library only.

    python -m studio.agent mcp          (or NarewAgent.exe mcp)

Claude Code: the .mcp.json next to AGENTS.md registers it as "narew-studio".
"""
from __future__ import annotations

import base64
import io
import json
import sys
import time

from narew.version import VERSION

from . import guide
from .registry import TOOLS

SERVER = {'name': 'narew-studio', 'title': 'NarewStudio (Islet Online modding)', 'version': VERSION}
PROTOCOLS = ('2025-06-18', '2025-03-26', '2024-11-05')
MAX_INLINE = 1_500_000            # bytes of PNG sent inline; bigger pictures are scaled down for the message


def _default(o):
    try:
        import numpy as np
        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, np.floating):
            return float(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
    except ImportError:
        pass
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    return str(o)


INLINE = 110                       # containers whose one-line form is at most this long stay on one line


def _fmt(o, pad: str) -> str:
    one = json.dumps(o, ensure_ascii=False)
    scalars = isinstance(o, list) and all(not isinstance(v, (list, dict)) for v in o)
    if not isinstance(o, (list, dict)) or len(one) <= INLINE or scalars:
        return one
    inner = pad + ' '
    if isinstance(o, dict):
        rows = [f'{inner}{json.dumps(str(k), ensure_ascii=False)}: {_fmt(v, inner)}' for k, v in o.items()]
        return '{\n' + ',\n'.join(rows) + '\n' + pad + '}'
    return '[\n' + ',\n'.join(inner + _fmt(v, inner) for v in o) + '\n' + pad + ']'


def to_json(obj, indent=1) -> str:
    """JSON for agents: small containers on one line, big ones one item per line (indent=None: one line)."""
    if indent is None:
        return json.dumps(obj, ensure_ascii=False, default=_default)
    return _fmt(json.loads(json.dumps(obj, default=_default)), '')


def split_text(data) -> tuple[object, list[tuple[str, str]]]:
    """Move long multi-line strings (ASCII grids, XML, file text) out of the JSON into their own blocks,
    so they read as text instead of one line full of \\n escapes."""
    blocks: list[tuple[str, str]] = []

    def walk(o, path):
        if isinstance(o, str) and '\n' in o and len(o) > 160:
            blocks.append((path, o))
            return f'<text block {len(blocks)} below>'
        if isinstance(o, dict):
            return {k: walk(v, f'{path}.{k}' if path else str(k)) for k, v in o.items()}
        if isinstance(o, list):
            return [walk(v, f'{path}[{i}]') for i, v in enumerate(o)]
        return o
    return walk(data, ''), blocks


def render_result(data) -> list[str]:
    """Text blocks for a tool result: the JSON, then each long text."""
    body, blocks = split_text(data)
    out = [to_json(body)]
    out += [f'--- text block {i} ({path}) ---\n{text}' for i, (path, text) in enumerate(blocks, 1)]
    return out


def tool_list() -> list[dict]:
    out = []
    for t in TOOLS.values():
        out.append({'name': t.name, 'title': t.name.replace('_', ' '), 'description': t.describe(),
                    'inputSchema': t.schema,
                    'annotations': {'readOnlyHint': not (t.writes or t.game), 'destructiveHint': t.game,
                                    'idempotentHint': False, 'openWorldHint': False}})
    return out


def image_block(path: str) -> dict | None:
    try:
        with open(path, 'rb') as f:
            data = f.read()
        if len(data) > MAX_INLINE:
            from PIL import Image
            im = Image.open(io.BytesIO(data))
            while True:
                im = im.resize((max(1, im.width * 3 // 4), max(1, im.height * 3 // 4)), Image.LANCZOS)
                buf = io.BytesIO()
                im.save(buf, 'PNG', optimize=True)
                data = buf.getvalue()
                if len(data) <= MAX_INLINE or im.width < 200:
                    break
        return {'type': 'image', 'data': base64.b64encode(data).decode('ascii'), 'mimeType': 'image/png'}
    except OSError:
        return None


class Server:
    def __init__(self, make_ctx):
        self.make_ctx = make_ctx
        self.ctx = None

    def context(self):
        if self.ctx is None:
            self.ctx = self.make_ctx()
        return self.ctx

    def handle(self, msg: dict) -> dict | None:
        method = msg.get('method')
        mid = msg.get('id')
        params = msg.get('params') or {}
        if method is None:                      # a response to something we never send
            return None
        try:
            if method == 'initialize':
                want = params.get('protocolVersion')
                result = {'protocolVersion': want if want in PROTOCOLS else PROTOCOLS[0],
                          'capabilities': {'tools': {'listChanged': False}},
                          'serverInfo': SERVER, 'instructions': guide.INSTRUCTIONS}
            elif method == 'ping':
                result = {}
            elif method == 'tools/list':
                result = {'tools': tool_list()}
            elif method == 'tools/call':
                result = self.call(params.get('name', ''), params.get('arguments') or {})
            elif method in ('resources/list', 'resources/templates/list', 'prompts/list'):
                key = method.split('/')[0] if method != 'resources/templates/list' else 'resourceTemplates'
                result = {key: []}
            elif method.startswith('notifications/'):
                return None
            else:
                return self.error(mid, -32601, f'method not found: {method}')
        except Exception as ex:                  # never let one request kill the server
            return self.error(mid, -32603, f'{type(ex).__name__}: {ex}')
        return None if mid is None else {'jsonrpc': '2.0', 'id': mid, 'result': result}

    @staticmethod
    def error(mid, code, message):
        return None if mid is None else {'jsonrpc': '2.0', 'id': mid, 'error': {'code': code, 'message': message}}

    def call(self, name: str, args: dict) -> dict:
        from .registry import call
        t0 = time.time()
        try:
            ctx = self.context()
        except Exception as ex:
            return {'content': [{'type': 'text', 'text': f'NarewStudio could not start: {ex}'}], 'isError': True}
        ok, data, images = call(ctx, name, args)
        sys.stderr.write(f'[narew-studio] {name} {"ok" if ok else "ERROR"} {time.time() - t0:.2f}s\n')
        texts = render_result(data) if ok else [str(data)]
        content = [{'type': 'text', 'text': t} for t in texts]
        for p in images:
            blk = image_block(p)
            if blk:
                content.append(blk)
        return {'content': content, 'isError': not ok}


def serve(make_ctx, stdin=None, stdout=None):
    """Read requests line by line until stdin closes."""
    out = stdout or sys.stdout.buffer
    inp = stdin or sys.stdin.buffer
    sys.stdout = sys.stderr                      # stray prints must not corrupt the protocol stream
    srv = Server(make_ctx)

    def send(obj):
        out.write((json.dumps(obj, ensure_ascii=False) + '\n').encode('utf-8'))
        out.flush()
    for raw in inp:
        line = raw.decode('utf-8', 'replace').strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError as ex:
            send({'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': f'parse error: {ex}'}})
            continue
        if isinstance(msg, list):
            replies = [r for r in (srv.handle(m) for m in msg if isinstance(m, dict)) if r]
            if replies:
                send(replies)
            continue
        if isinstance(msg, dict):
            r = srv.handle(msg)
            if r:
                send(r)
