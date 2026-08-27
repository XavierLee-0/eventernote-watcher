"""Notifier 抽象接口与注册表。新增渠道只需实现 Notifier 并注册。"""
from __future__ import annotations

from typing import Callable


class Notifier:
    """一个通知渠道。config 为 settings['notifiers'][key] 的内容。"""

    name: str = "base"

    def __init__(self, config: dict):
        self.config = config

    def validate(self) -> str | None:
        """配置不完整时返回错误说明，否则 None。"""
        return None

    async def send(self, title: str, content: str, html: str | None = None) -> None:
        """发送通知。content 为 Markdown 文本; html 为可选的 HTML 版本(邮件用)。"""
        raise NotImplementedError


_registry: dict[str, Callable[[dict], Notifier]] = {}


def register(cls):
    _registry[cls.name] = cls
    return cls


def build_all(notifier_settings: dict) -> list[Notifier]:
    instances = []
    for key, cfg in notifier_settings.items():
        if not isinstance(cfg, dict) or not cfg.get("enabled"):
            continue
        factory = _registry.get(key)
        if factory is None:
            continue
        n = factory(cfg)
        if n.validate() is None:
            instances.append(n)
    return instances


def available_channels() -> list[dict]:
    return [
        {"key": k, "name": getattr(cls, "label", k)}
        for k, cls in _registry.items()
    ]
