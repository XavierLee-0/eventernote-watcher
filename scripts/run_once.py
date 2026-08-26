"""GitHub Actions 模式入口: 同步配置 → 抓取一轮 → 推送 → 生成 site/calendar.ics。

与 WebUI 版共享 app/ 全部核心代码, 仅入口不同。

出演者: actors.json (随仓库维护)
设置基线: settings.json (无密钥部分, 随仓库维护)
通知密钥: 环境变量注入 (GitHub Secrets), 优先级高于 settings.json
数据库: data/eventernote.db (每次运行后由 workflow 提交回仓库, 保持状态)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.calendar import generate_ics  # noqa: E402
from app.db import Database  # noqa: E402
from app.watcher import Watcher  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("run_once")

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SITE_DIR = ROOT / "site"

# 环境变量 → settings.notifiers 字段 的映射 (GitHub Secrets 注入)
ENV_OVERRIDES = {
    "WXPUSHER_APP_TOKEN": ("wxpusher", "app_token"),
    "WXPUSHER_UID": ("wxpusher", "uid"),
    "PUSHPLUS_TOKEN": ("pushplus", "token"),
    "EMAIL_HOST": ("email", "host"),
    "EMAIL_USERNAME": ("email", "username"),
    "EMAIL_PASSWORD": ("email", "password"),
    "EMAIL_FROM": ("email", "from_addr"),
    "EMAIL_TO": ("email", "to_addr"),
}


def load_json(path: Path, fallback):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return fallback


def sync_actors(db: Database, actors: list[dict]) -> None:
    """以 actors.json 为准同步出演者列表 (不在 json 里的库内出演者将被停用)。"""
    wanted = {a["actor_id"]: a for a in actors if a.get("enabled", True)}
    for actor_id, a in wanted.items():
        db.add_actor(actor_id, a["name"])
    for row in db.list_actors():
        if row["actor_id"] not in wanted:
            log.info("disabling actor not in actors.json: %s", row["name"])
            db.update_actor(row["actor_id"], enabled=False)


def apply_env_overrides(settings: dict) -> dict:
    for env_key, (channel, field) in ENV_OVERRIDES.items():
        value = os.environ.get(env_key)
        if value:
            settings.setdefault("notifiers", {}).setdefault(channel, {})
            settings["notifiers"][channel][field] = value
            settings["notifiers"][channel]["enabled"] = True
    return settings


async def main() -> int:
    DATA_DIR.mkdir(exist_ok=True)
    db = Database(DATA_DIR / "eventernote.db")

    actors = load_json(ROOT / "actors.json", [])
    if not actors:
        log.warning("actors.json 为空或不存在, 仅用库内已有出演者")
    else:
        sync_actors(db, actors)

    settings = load_json(ROOT / "settings.json", db.get_settings())
    settings = apply_env_overrides(settings)
    db.save_settings(settings)

    watcher = Watcher(db)
    summary = await watcher.run_once()
    log.info("summary: %s", summary)

    # 生成本地 .ics 快照 (Pages 发布用); Actions 模式不做 token 校验, 仓库私有即可
    SITE_DIR.mkdir(exist_ok=True)
    (SITE_DIR / "calendar.ics").write_text(generate_ics(db), encoding="utf-8")
    # 时间戳文件: 保证仓库每轮运行都有变更, 防止 60 天不活跃停用 scheduled workflow
    (SITE_DIR / "last_run.txt").write_text(
        summary and json.dumps(summary, ensure_ascii=False) or "", encoding="utf-8"
    )
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
