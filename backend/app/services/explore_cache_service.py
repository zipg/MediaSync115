"""探索页（豆瓣 / TMDB / 猫眼榜单）缓存：内存热层 + 磁盘持久层 + stale-while-revalidate。

背景问题：首页榜单原先只有各 explore service 里的**进程内 TTL 缓存**——
- 进程一重启缓存全空，第一次打开必然冷启动；
- TTL 到期时请求会**阻塞等上游抓取**（每个源 8~9 个分区并发抓），页面表现为"空白加载"。

本模块提供页面级缓存：
1. **磁盘持久化**：进程重启后仍是热缓存（原子写，写失败只告警不影响请求）；
2. **stale-while-revalidate**：只要有过成功结果就**立即返回**，同时在后台刷新（同 key 去重）；
3. **刷新失败保留旧值**：不会把用户从"有内容"打回"空白"；
4. 返回值带 `cache` 元信息（hit / stale / saved_at / age_seconds），前端可据此提示"正在后台更新"。
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 15 * 60
# 旧值最多还能用多久；超过就当作没有缓存（避免端出非常陈旧的数据）
DEFAULT_STALE_TTL_SECONDS = 7 * 24 * 3600
# 单条缓存上限，超过不落盘（只放内存），避免异常大响应撑爆磁盘
MAX_DISK_ENTRY_BYTES = 8 * 1024 * 1024


def default_cache_dir() -> Path:
    """缓存目录：环境变量 EXPLORE_CACHE_DIR 优先，默认与 SQLite 同级的 data/ 目录。"""
    raw = str(os.getenv("EXPLORE_CACHE_DIR") or "").strip()
    if raw:
        return Path(raw)
    return Path("./data/explore_cache")


@dataclass
class CacheEntry:
    """一条缓存。payload 是原始响应（JSON 可序列化）。"""

    payload: Any
    saved_at: float
    expires_at: float
    from_disk: bool = False

    @property
    def fresh(self) -> bool:
        return time.time() < self.expires_at

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.saved_at)

    def meta(self, *, hit: bool, stale: bool) -> dict[str, Any]:
        return {
            "hit": hit,
            "stale": stale,
            "saved_at": self.saved_at,
            "age_seconds": round(self.age_seconds, 1),
            "source": "disk" if self.from_disk else "memory",
        }


class ExploreCacheService:
    """探索页缓存。线程/协程安全的关键点：同 key 并发只触发一次上游抓取。"""

    def __init__(
        self,
        cache_dir: Path | str | None = None,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        stale_ttl_seconds: int = DEFAULT_STALE_TTL_SECONDS,
    ) -> None:
        self._dir = Path(cache_dir) if cache_dir is not None else default_cache_dir()
        self._ttl = int(ttl_seconds)
        self._stale_ttl = int(stale_ttl_seconds)
        self._memory: dict[str, CacheEntry] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._refreshing: set[str] = set()
        self._disk_ready = False

    # ------------------------------------------------------------------ 磁盘
    def _ensure_dir(self) -> bool:
        if self._disk_ready:
            return True
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._disk_ready = True
        except Exception:  # noqa: BLE001
            logger.warning("探索缓存目录不可用：%s（退化为纯内存缓存）", self._dir, exc_info=True)
        return self._disk_ready

    def _path_for(self, key: str) -> Path:
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
        return self._dir / f"{digest}.json"

    def _load_disk(self, key: str) -> CacheEntry | None:
        if not self._ensure_dir():
            return None
        path = self._path_for(key)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            logger.warning("探索缓存读取失败（忽略该条）：%s", path, exc_info=True)
            return None
        if str(raw.get("key") or "") != key:
            return None
        saved_at = float(raw.get("saved_at") or 0.0)
        expires_at = float(raw.get("expires_at") or 0.0)
        if time.time() - saved_at > self._stale_ttl:
            return None
        return CacheEntry(payload=raw.get("payload"), saved_at=saved_at,
                          expires_at=expires_at, from_disk=True)

    def _save_disk(self, key: str, entry: CacheEntry) -> None:
        if not self._ensure_dir():
            return
        try:
            blob = json.dumps({"key": key, "saved_at": entry.saved_at,
                               "expires_at": entry.expires_at, "payload": entry.payload},
                              ensure_ascii=False)
        except (TypeError, ValueError):
            logger.warning("探索缓存不可序列化，跳过落盘：%s", key)
            return
        if len(blob.encode("utf-8")) > MAX_DISK_ENTRY_BYTES:
            logger.warning("探索缓存条目过大，跳过落盘：%s", key)
            return
        path = self._path_for(key)
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(blob, encoding="utf-8")
            os.replace(tmp, path)          # 原子替换，避免读到半截 JSON
        except Exception:  # noqa: BLE001
            logger.warning("探索缓存落盘失败（不影响本次请求）：%s", path, exc_info=True)

    # ------------------------------------------------------------------ 读写
    def get(self, key: str) -> CacheEntry | None:
        """取缓存：内存优先，其次磁盘（磁盘命中会回填内存）。"""
        entry = self._memory.get(key)
        if entry is not None:
            return entry
        entry = self._load_disk(key)
        if entry is not None:
            self._memory[key] = entry
        return entry

    def set(self, key: str, payload: Any, *, ttl_seconds: int | None = None) -> CacheEntry:
        ttl = int(ttl_seconds or self._ttl)
        now = time.time()
        entry = CacheEntry(payload=payload, saved_at=now, expires_at=now + ttl)
        self._memory[key] = entry
        self._save_disk(key, entry)
        return entry

    def clear(self, key: str | None = None) -> None:
        if key is None:
            self._memory.clear()
            return
        self._memory.pop(key, None)

    # ------------------------------------------------------------------ 刷新
    def refresh_in_background(
        self,
        key: str,
        fetcher: Callable[[], Awaitable[Any]],
        *,
        ttl_seconds: int | None = None,
    ) -> bool:
        """后台刷新（同 key 去重）。刷新失败保留旧值。"""
        if key in self._refreshing:
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False

        self._refreshing.add(key)

        async def _run() -> None:
            try:
                payload = await fetcher()
                if payload is not None:
                    self.set(key, payload, ttl_seconds=ttl_seconds)
                    logger.info("探索缓存后台刷新完成：%s", key)
            except Exception:  # noqa: BLE001
                logger.warning("探索缓存后台刷新失败，保留旧值：%s", key, exc_info=True)
            finally:
                self._refreshing.discard(key)

        loop.create_task(_run())
        return True

    async def get_or_fetch(
        self,
        key: str,
        fetcher: Callable[[], Awaitable[Any]],
        *,
        ttl_seconds: int | None = None,
        stale_ttl_seconds: int | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """返回 {"payload": ..., "cache": {...}}。

        - 有过成功结果且未过期 → 直接返回（hit=True, stale=False）
        - 有过结果但已过期   → 立即返回旧值（stale=True）并后台刷新
        - refresh=True      → 强制同步抓取（用户主动点刷新时用）
        - 没有结果          → 同步抓取（保持原有冷启动行为）
        """
        if not refresh:
            entry = self.get(key)
            if entry is not None:
                stale = not entry.fresh
                if stale:
                    self.refresh_in_background(key, fetcher, ttl_seconds=ttl_seconds)
                return {"payload": entry.payload, "cache": entry.meta(hit=True, stale=stale)}

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if not refresh:                     # 等锁期间可能已被别人填好
                entry = self.get(key)
                if entry is not None and entry.fresh:
                    return {"payload": entry.payload, "cache": entry.meta(hit=True, stale=False)}
            payload = await fetcher()
            entry = self.set(key, payload, ttl_seconds=ttl_seconds)
            return {"payload": payload, "cache": entry.meta(hit=False, stale=False)}

    # ------------------------------------------------------------------ 观测
    def stats(self) -> dict[str, Any]:
        now = time.time()
        return {
            "dir": str(self._dir),
            "memory_entries": len(self._memory),
            "fresh_entries": sum(1 for e in self._memory.values() if e.expires_at > now),
            "refreshing": sorted(self._refreshing),
            "ttl_seconds": self._ttl,
            "stale_ttl_seconds": self._stale_ttl,
        }


explore_cache_service = ExploreCacheService()


def explore_page_cache(
    key_builder: Callable[[dict[str, Any]], str],
    *,
    ttl_seconds: int | None = None,
    stale_ttl_seconds: int | None = None,
) -> Callable:
    """给探索页接口套一层页面级缓存（stale-while-revalidate）。

    用法（放在 @router.get 之下、被包装函数之上）::

        @router.get("/explore/sections")
        @explore_page_cache(lambda kw: f"explore:sections:{kw['source']}:{kw['limit']}")
        async def get_explore_sections(...):
            ...

    行为：
    - 冷启动（无缓存）：照旧同步抓取，行为与原来一致；
    - 有新鲜缓存：直接返回，不碰上游；
    - 有陈旧缓存：**立刻返回旧值**并后台刷新（同 key 去重），响应体多一个 `cache` 字段；
    - `refresh=true`：保持原语义，强制同步刷新。

    说明：`functools.wraps` 保留原签名，FastAPI 的参数校验/依赖注入不受影响。
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                key = key_builder(kwargs)
            except Exception:  # noqa: BLE001
                logger.warning("探索缓存 key 构造失败，退化为直通：%s", func.__name__, exc_info=True)
                return await func(*args, **kwargs)

            refresh = bool(kwargs.get("refresh"))

            async def _fetch() -> Any:
                return await func(*args, **kwargs)

            result = await explore_cache_service.get_or_fetch(
                key,
                _fetch,
                ttl_seconds=ttl_seconds,
                stale_ttl_seconds=stale_ttl_seconds,
                refresh=refresh,
            )
            payload = result.get("payload")
            if isinstance(payload, dict):
                return {**payload, "cache": result.get("cache")}
            return payload

        return wrapper

    return decorator
