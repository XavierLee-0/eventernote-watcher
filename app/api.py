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

    # ---------- 调度 ----------
    @router.get("/calendar.ics")
    def calendar_ics(all: bool = False):
        ics = generate_ics(db, include_past=all)
        return Response(
            content=ics,
            media_type="text/calendar; charset=utf-8",
            headers={"Content-Disposition": 'inline; filename="calendar.ics"'},
        )

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
            "poll_interval_hours": settings.get("poll_interval_hours"),
            "actor_count": len(db.list_actors()),
            # 完整订阅链接(含 token), 已在 Basic Auth 保护之内
            "calendar_url": _calendar_url(),
        }

    return router
