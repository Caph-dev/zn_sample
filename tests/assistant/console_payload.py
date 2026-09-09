"""操作台页面（Astryx React 壳）bootstrap JSON 的测试辅助。

页面正文由前端渲染，服务端只保证 bootstrap JSON 的数据契约，
所以页面级断言改成解析 JSON，而不是匹配已不再渲染的 HTML。
"""
from __future__ import annotations

import json
import re
from typing import Any

_BOOTSTRAP_PATTERN = re.compile(
    r'<script id="console-bootstrap" type="application/json">(.*?)</script>',
    re.S,
)


def console_payload(response: Any) -> dict:
    match = _BOOTSTRAP_PATTERN.search(response.text)
    assert match is not None, "console-bootstrap script missing"
    return json.loads(match.group(1))


def console_data(response: Any) -> dict:
    return console_payload(response)["data"]


def operator_items(response: Any) -> list[dict]:
    payload = console_payload(response)
    return [
        item
        for group in payload["data"]["operator_groups"]
        for item in group["items"]
    ]
