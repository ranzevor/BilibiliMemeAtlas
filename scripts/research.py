#!/usr/bin/env python3
"""Public Bilibili discovery and inspect receipts, never automated fact promotion."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

sys.dont_write_bytecode = True

from atlas import strict_json_loads
from update import BVID, NoRedirect, MAX_RESPONSE_BYTES

API = 'https://api.bilibili.com'
FORMAT = 'bilibili-meme-atlas-research/v1'
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 50000


def bounded_structure(data):
    stack = [(data, 0)]
    count = 0
    while stack:
        value, depth = stack.pop()
        count += 1
        if depth > MAX_JSON_DEPTH or count > MAX_JSON_NODES:
            raise ValueError('响应 JSON 嵌套或节点数量超过安全上限')
        if isinstance(value, dict):
            stack.extend((v, depth + 1) for v in value.values())
        elif isinstance(value, list):
            stack.extend((v, depth + 1) for v in value)
    # Validate Unicode before the metadata can become part of a receipt.
    json.dumps(data, ensure_ascii=False).encode('utf-8')


def request_spec(action, value):
    if action == 'search':
        if not isinstance(value, str) or not value.strip() or len(value) > 80:
            raise ValueError('检索词必须为 1–80 字符')
        return API + '/x/web-interface/search/type?' + urlencode({
            'search_type': 'video', 'keyword': value.strip(), 'page': 1, 'page_size': 20})
    if action == 'inspect' and isinstance(value, str) and BVID.fullmatch(value):
        return API + '/x/web-interface/view?' + urlencode({'bvid': value})
    raise ValueError('只允许检索或核对合法 BV 号')


def collect(action, value, *, opener=None, now=None):
    """Single request, bounded body, no cookies/auth, redirects, retry or challenge solving."""
    url = request_spec(action, value)
    receipt = {'format': FORMAT, 'action': action, 'input': value,
               'collected_at': now or datetime.now(timezone.utc).isoformat(),
               'request_url': url, 'auth': 'none', 'attempts': 1,
               'status': 'unavailable', 'access_level': 'metadata',
               'limitations': '响应仅包含标题、简介及元数据；没有观看视频或读取字幕，不能证明梗的含义、首创或流行。'}
    request = Request(url, headers={'Accept': 'application/json',
                                   'User-Agent': 'BilibiliMemeAtlas/0.1.0 (read-only)'})
    try:
        with (opener or build_opener(NoRedirect())).open(request, timeout=8) as response:
            receipt['http_status'] = response.status
            receipt['content_type'] = response.headers.get('Content-Type', '')
            body = response.read(MAX_RESPONSE_BYTES + 1)
            receipt['body_bytes'] = len(body)
            receipt['body_sha256'] = hashlib.sha256(body).hexdigest()
            if len(body) > MAX_RESPONSE_BYTES:
                raise ValueError('响应超过 2 MiB；未解析')
            if response.status != 200 or 'json' not in receipt['content_type'].lower():
                raise ValueError('响应不是成功的 JSON')
            data = strict_json_loads(
                body, max_depth=MAX_JSON_DEPTH, max_nodes=MAX_JSON_NODES)
            if not isinstance(data, dict) or type(data.get('code')) is not int:
                raise ValueError('响应缺少合法 API code')
            receipt['api_code'] = data['code']
            if data['code'] != 0:
                receipt['status'] = 'refused'
                receipt['error'] = str(data.get('message', 'API 拒绝请求'))[:300]
            elif not isinstance(data.get('data'), dict):
                raise ValueError('响应缺少 data 对象')
            else:
                receipt['status'] = 'collected'
                receipt['metadata'] = data['data']
    except HTTPError as exc:
        receipt.update(status='refused', http_status=exc.code, error=str(exc))
        if exc.fp is not None:
            try:
                body = exc.read(MAX_RESPONSE_BYTES + 1)
                if not isinstance(body, bytes):
                    raise TypeError('拒绝响应正文不是字节数据')
                receipt['body_bytes'] = len(body)
                receipt['body_sha256'] = hashlib.sha256(body).hexdigest()
            except (TimeoutError, OSError, TypeError) as body_error:
                receipt['error_body_read_error'] = str(body_error)[:500]
            finally:
                exc.close()
    except (URLError, TimeoutError, OSError, ValueError, RecursionError) as exc:
        receipt['error'] = str(exc)[:500]
    return receipt


def write_receipt(path, receipt, *, root=None):
    path = Path(path)
    # Exclusive creation preserves earlier evidence; raw network output stays outside the Skill.
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    if path.resolve().is_relative_to(root.resolve()):
        raise ValueError('原始联网凭据不能写入技能目录')
    try:
        payload = (json.dumps(
            receipt, ensure_ascii=False, indent=2, allow_nan=False)
                   + '\n').encode('utf-8')
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError('凭据不可完整序列化；尚未创建输出文件') from exc
    with path.open('xb') as stream:
        stream.write(payload)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['search', 'inspect'])
    parser.add_argument('value')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    # Check before networking, avoiding an unrecorded request when the destination is occupied.
    if not args.output.parent.is_dir():
        parser.error('输出父目录不存在；先选择可写的证据目录')
    if args.output.exists():
        parser.error('凭据已存在；请选新文件，不覆盖历史')
    try:
        if args.output.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
            raise ValueError('联网凭据不能保存到技能目录')
        receipt = collect(args.action, args.value)
        write_receipt(args.output, receipt)
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps({'status': receipt['status'], 'receipt': str(args.output),
                      'interpretation': receipt['limitations']}, ensure_ascii=False))
    return 0 if receipt['status'] == 'collected' else 3

if __name__ == '__main__':
    raise SystemExit(main())
