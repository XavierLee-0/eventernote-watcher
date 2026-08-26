"""轮询调度 + 新活动检测与通知（核心业务，不依赖 Web 框架）。"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime

import httpx

from .db import Database
from .fetcher import fetch_actor_events
from .notifiers import build_all

log = logging.getLogger(__name__)


class Watcher:
    def __init__(self, db: Database):
        self.db = db
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._running = False
        self._last_run_at: str | None = None

    # ---------- 生命周期 ----------
    async def start(self):
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def trigger_now(self):
        """WebUI 点“立即抓取”时唤醒轮询循环。"""
        self._wake.set()

    # ---------- 轮询 ----------
    async def _loop(self):
        while True:
            try:
                await self.run_once()
            except Exception:
                log.exception("watch cycle failed")
            settings = self.db.get_settings()
            hours = float(settings.get("poll_interval_hours", 8))
            delay = hours * 3600 * random.uniform(0.9, 1.1)
            # 可被 trigger_now 提前唤醒
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    # ---------- 单轮 ----------
    async def run_once(self) -> dict:
        """抓取所有启用的出演者，新活动合并通知。返回摘要。"""
        self._running = True
        summary = {"checked": 0, "failed": 0, "new_events": 0, "notified": False}
        try:
            actors = [a for a in self.db.list_actors() if a["enabled"]]
            self._last_run_at = datetime.now().isoformat(timespec="seconds")
            settings = self.db.get_settings()
            lo, hi = settings.get("fetch_delay_seconds", [3, 8])
            async with httpx.AsyncClient(
                headers={"User-Agent": "Mozilla/5.0"}, timeout=30, follow_redirects=True
            ) as client:
                new_items: list[tuple[dict, list]] = []  # (actor, [Event])
                for i, actor in enumerate(actors):
                    if i > 0:
                        await asyncio.sleep(random.uniform(lo, hi))
                    try:
                        events = await fetch_actor_events(client, actor["actor_id"], actor["name"])
                        summary["checked"] += 1
                    except Exception as e:
                        summary["failed"] += 1
                        self.db.record_fetch(actor["actor_id"], False, str(e))
                        log.warning("fetch %s failed: %s", actor["name"], e)
                        continue
                    self.db.record_fetch(actor["actor_id"], True, None)
                    seen = self.db.get_seen_ids(actor["actor_id"])
                    fresh = [ev for ev in events if ev.event_id not in seen]
                    self.db.mark_seen(actor["actor_id"], events)
                    if actor["baselined"] and fresh:
                        new_items.append((actor, fresh))
                    elif not actor["baselined"]:
                        # 首次抓取：只建基线
                        self.db.update_actor(actor["actor_id"], baselined=True)
                        log.info("baseline for %s: %d events", actor["name"], len(events))
            if new_items:
                summary["new_events"] = sum(len(f) for _, f in new_items)
                summary["notified"] = await self._notify(new_items)
            return summary
        finally:
            self._running = False

    async def _notify(self, new_items: list[tuple[dict, list]]) -> bool:
        total = sum(len(f) for _, f in new_items)
        actor_names = ", ".join(a["name"] for a, _ in new_items)
        title = f"Eventernote: {len(new_items)} 位出演者有 {total} 场新活动"
        lines = []
        for actor, fresh in new_items:
            lines.append(f"## {actor['name']}")
            for ev in fresh:
                when = ev.date or "日期未定"
                place = f" @ {ev.place}" if ev.place else ""
                lines.append(f"- [{ev.name}]({ev.url()})  \n  {when}{place}")
            lines.append("")
        content = "\n".join(lines)

        notifiers = build_all(self.db.get_settings().get("notifiers", {}))
        if not notifiers:
            log.warning("new events but no notifier enabled/configured")
            self.db.log_notification(None, None, "(none)", title, False, "无可用通知渠道")
            return False
        ok_any = False
        for n in notifiers:
            try:
                await n.send(title, content)
                self.db.log_notification(None, None, n.name, title, True)
                ok_any = True
            except Exception as e:
                log.error("notify via %s failed: %s", n.name, e)
                self.db.log_notification(None, None, n.name, title, False, str(e))
        return ok_any

    @property
    def running(self) -> bool:
        return self._running

    @property
    def last_run_at(self) -> str | None:
        return self._last_run_at
