"""FastAPI 路由：出演者、设置、活动、通知历史。"""
from __future__ import annotations

import os

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .db import Database
from .fetcher import search_actors
from .notifiers import build_all
from .watcher import Watcher
from .calendar import generate_ics
from fastapi.responses import Response


def _calendar_url() -> str:
    """构造带 token 的完整订阅链接（用于展示在 WebUI；请求本身已通过 Basic Auth）。"""
    token = os.environ.get("ICS_TOKEN", "")
    suffix = f"?token={token}" if token else ""
    # 请求头里的 Host 即外部访问地址
    return f"/api/calendar.ics{suffix}"


# 注意: 需在模块级定义, 函数内的局部类无法被 PEP 563 字符串注解解析
class ActorIn(BaseModel):
    actor_id: int
    name: str


class ActorPatch(BaseModel):
    enabled: bool | None = None
    name: str | None = None


def create_router(db: Database, watcher: Watcher) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/actors")
    def list_actors():
        return db.list_actors()

    @router.post("/actors")
    def add_actor(body: ActorIn):
        if not db.add_actor(body.actor_id, body.name):
            raise HTTPException(409, "该出演者已在列表中")
        return {"ok": True}

    @router.patch("/actors/{actor_id}")
    def patch_actor(actor_id: int, body: ActorPatch):
        db.update_actor(actor_id, enabled=body.enabled, name=body.name)
        return {"ok": True}

    @router.delete("/actors/{actor_id}")
    def delete_actor(actor_id: int):
        db.delete_actor(actor_id)
        return {"ok": True}

    @router.get("/actors/search")
    async def search_actors_api(keyword: str, page: int = 1):
        if not keyword.strip():
            return {"results": []}
        async with httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0"}, timeout=30
        ) as client:
            results = await search_actors(client, keyword.strip(), page)
        watched = {a["actor_id"] for a in db.list_actors()}
        # 站点 API 不按匹配度排序, 精确匹配的条目可能被相似名淹没:
        # 按 精确匹配 > 前缀匹配 > 其他 重排, 大小写不敏感
        kw = keyword.strip().casefold()

        def rank(r):
            name = r.name.casefold()
            if name == kw:
                return 0
            if name.startswith(kw):
                return 1
            return 2

        results.sort(key=rank)
        return {
            "results": [
                {**r.__dict__, "watched": r.actor_id in watched} for r in results
            ]
        }

    # ---------- 设置 ----------
    @router.get("/settings")
    def get_settings():
        return db.get_settings()

    @router.put("/settings")
    def save_settings(settings: dict):
        db.save_settings(settings)
        return {"ok": True}

    @router.post("/settings/test-notify")
    async def test_notify():
        notifiers = build_all(db.get_settings().get("notifiers", {}))
        if not notifiers:
            raise HTTPException(400, "没有已启用且配置完整的通知渠道")
        results = {}
        for n in notifiers:
            try:
                await n.send("Eventernote Watcher 测试", "这是一条测试推送，配置成功 ✅")
                results[n.name] = {"ok": True}
            except Exception as e:
                results[n.name] = {"ok": False, "error": str(e)}
        return results

    # ---------- 活动 & 通知历史 ----------
    @router.get("/events")
    def list_events(actor_id: int | None = None, future_only: bool = False):
        return db.list_events(actor_id, future_only)

    @router.get("/notifications")
    def list_notifications(limit: int = 100):
        return db.list_notifications(limit)

    # ---------- 配置导出/导入 ----------
    @router.get("/export")
    def export_config():
        """导出演者列表 + 设置 + 活动快照。文件含通知渠道 token, 注意保管。"""
        actors = db.list_actors()
        snapshot = db.export_snapshot()
        return {
            "version": 2,
            "exported_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
            "actors": [
                {
                    "actor_id": a["actor_id"], "name": a["name"],
                    "enabled": bool(a["enabled"]), "baselined": bool(a["baselined"]),
                }
                for a in actors
            ],
            "settings": db.get_settings(),
            "snapshot": snapshot,
        }

    @router.post("/import")
    def import_config(body: dict):
        """导入配置: 以文件为准替换出演者列表, 合并设置; v2 格式含活动快照,
        恢复后无需重建基线、无漏报窗口。v1 旧格式(无快照)仍可导入, 走重建基线路径。"""
        if not isinstance(body, dict) or "actors" not in body or "settings" not in body:
            raise HTTPException(400, "文件格式不正确: 需要 actors 和 settings 字段")
        actors = body["actors"]
        if not isinstance(actors, list):
            raise HTTPException(400, "actors 字段必须是数组")
        for a in actors:
            if not isinstance(a, dict) or "actor_id" not in a or "name" not in a:
                raise HTTPException(400, "actors 中存在缺少 actor_id/name 的条目")

        # 替换出演者列表
        keep = {a["actor_id"] for a in actors}
        for row in db.list_actors():
            if row["actor_id"] not in keep:
                db.delete_actor(row["actor_id"])
        for a in actors:
            if db.add_actor(a["actor_id"], a["name"]):
                if not a.get("enabled", True):
                    db.update_actor(a["actor_id"], enabled=False)
            else:
                db.update_actor(a["actor_id"], enabled=a.get("enabled", True))

        # 恢复活动快照(v2): 恢复 baselined 状态与已见活动, 迁移后无缝衔接
        snapshot = body.get("snapshot")
        if snapshot:
            db.import_snapshot(snapshot)
            for a in actors:
                if a.get("baselined"):
                    db.set_baselined(a["actor_id"], True)

        # 合并设置(以导入文件为准)
        db.save_settings(body["settings"])
        return {"ok": True, "actor_count": len(actors), "snapshot_restored": bool(snapshot)}

    # ---------- 调度 ----------
    @router.get("/calendar.ics")
    def calendar_ics(all: bool = False):
        ics = generate_ics(db, include_past=all)
        return Response(
            content=ics,
            media_type="text/calendar; charset=utf-8",
            headers={"Content-Disposition": 'inline; filename="calendar.ics"'},
        )

    @router.post("/watch/simulate")
    async def simulate_notify():
        """用真实活动数据走一遍完整通知流程, 验证通知内容/格式用。"""
        result = await watcher.simulate_notify()
        if not result.get("ok") and result.get("error"):
            raise HTTPException(400, result["error"])
        return result

    @router.post("/watch/now")
    async def watch_now():
        if watcher.running:
            raise HTTPException(409, "抓取正在进行中")
        summary = await watcher.run_once()
        return summary

    @router.get("/status")
    def status():
        settings = db.get_settings()
        return {
            "running": watcher.running,
            "last_run_at": watcher.last_run_at,
            "next_run_at": watcher.next_run_at,
            "poll_schedule": settings.get("poll_schedule", ""),
            "poll_interval_hours": settings.get("poll_interval_hours"),
            "actor_count": len(db.list_actors()),
            # 完整订阅链接(含 token), 已在 Basic Auth 保护之内
            "calendar_url": _calendar_url(),
        }

    return router
