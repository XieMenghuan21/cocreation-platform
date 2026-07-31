"""共享递归 JSON 结构保护。"""
from __future__ import annotations


def validate_json_shape(
    value: object,
    *,
    max_depth: int = 8,
    max_nodes: int = 2000,
    max_string_length: int = 20000,
) -> object:
    nodes = 0

    def visit(item: object, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > max_nodes:
            raise ValueError("JSON 节点数量超过限制")
        if depth > max_depth:
            raise ValueError("JSON 嵌套深度超过限制")
        if isinstance(item, str):
            if len(item) > max_string_length:
                raise ValueError("JSON 字符串长度超过限制")
            return
        if isinstance(item, list):
            for child in item:
                visit(child, depth + 1)
            return
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str) or len(key) > 160:
                    raise ValueError("JSON 对象键无效或过长")
                visit(child, depth + 1)
            return
        if item is not None and not isinstance(item, (bool, int, float)):
            raise ValueError("JSON 包含不支持的值类型")

    visit(value, 0)
    return value
