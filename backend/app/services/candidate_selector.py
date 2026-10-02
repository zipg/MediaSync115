"""确定性选片内核（source-agnostic）。

用于「缺集驱动」的剧集资源挑选：给定待补集数集合 + 候选分享列表，
按覆盖度 → 画质 → 新鲜度 → 成本依次打分，选出最合适的一条（或判定需人工确认）。

设计目标：
- 零 AI：所有判断基于结构化字段（集数、画质标签、字幕、体积、时间、解锁成本）。
- 与癫影/盘搜等来源解耦：候选统一归一化为 dict 后再打分。
- 明确区分「全集合集 / 最近两集 / 只有最新一集」三种常见形态，且不会重复搬运已入库的集。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

# ---------------------------------------------------------------- 画质标签

RESOLUTION_RANK = {
    "8K": 80, "4320P": 80, "4K": 70, "2160P": 70,
    "1440P": 50, "2K": 50, "1080P": 40, "720P": 20, "576P": 10, "480P": 5,
}
SOURCE_RANK = {
    "REMUX": 30, "BLURAY-REMUX": 30, "UHD-BLURAY": 26, "BLURAY": 24,
    "WEB-DL": 18, "WEBRIP": 14, "HDTV": 6, "HDTVRIP": 4, "CAM": -100, "TS": -100,
}
HDR_RANK = {"DOLBY VISION": 12, "DOVI": 12, "DV": 12, "HDR10+": 9, "HDR10": 6, "HDR": 6, "SDR": 0}

DEFAULT_EXCLUDE_TAGS = ("CAM", "TS", "抢先版", "枪版")
ISO_HINTS = (".iso", ".img", "原盘", "BDMV", "ISO原盘")


def _norm_text(value: Any) -> str:
    return str(value or "").upper().replace("_", "-")


def extract_quality(candidate: dict[str, Any]) -> dict[str, Any]:
    """从候选里提取画质信息（优先用来源自带的结构化 tag）。"""
    raw_tag = candidate.get("tag_decoded")
    tag: dict[str, Any] = raw_tag if isinstance(raw_tag, dict) else {}
    blob = " ".join(
        str(candidate.get(k) or "")
        for k in ("title", "name", "file_name", "resource_name", "quality", "resolution", "raw_text", "tags")
    )
    blob_norm = _norm_text(blob)

    resolution = str(tag.get("resolution") or "").upper()
    if not resolution:
        for label in ("8K", "4320P", "2160P", "4K", "1440P", "1080P", "720P", "480P"):
            if label in blob_norm:
                resolution = label
                break
    source = str(tag.get("source") or "").upper()
    if not source:
        for label in ("REMUX", "UHD-BLURAY", "BLURAY", "WEB-DL", "WEBRIP", "HDTV"):
            if label in blob_norm:
                source = label
                break
    hdr = str(tag.get("hdr") or "").upper()
    if not hdr:
        for label in ("DOLBY VISION", "HDR10+", "HDR10", "DV", "HDR"):
            if label in blob_norm:
                hdr = label
                break

    chn_sub = bool(candidate.get("has_chinese_subtitle"))
    subtitle_kind = str(candidate.get("subtitle_kind") or "").lower()
    if not chn_sub:
        chn_sub = ("CHS" in blob_norm) or ("中字" in blob) or ("简中" in blob) or ("中文字幕" in blob) or ("chs" in subtitle_kind)

    return {
        "resolution": resolution or "UNKNOWN",
        "source": source,
        "hdr": hdr,
        "has_chinese_subtitle": chn_sub,
        "is_iso": any(h.lower() in blob.lower() for h in ISO_HINTS),
        "size_bytes": int(candidate.get("total_size_bytes") or candidate.get("size_bytes") or 0),
        "share_kind": str(candidate.get("share_kind") or "115").lower(),
        "offline_type": str(candidate.get("offline_type") or "").lower(),
    }


def quality_score(q: dict[str, Any], prefs: "Prefs") -> tuple[float, list[str]]:
    """画质分数（越高越好）+ 说明片段。"""
    score = 0.0
    notes: list[str] = []
    res = q["resolution"].upper()
    score += RESOLUTION_RANK.get(res, 0)
    if res:
        notes.append(res)
    src = q["source"].upper()
    if src:
        score += SOURCE_RANK.get(src, SOURCE_RANK.get(src.replace("BLU-RAY", "BLURAY"), 10))
        notes.append(src)
    hdr = q["hdr"].upper()
    if hdr:
        score += HDR_RANK.get(hdr, 4)
        notes.append(hdr)
    if q["has_chinese_subtitle"]:
        score += prefs.chinese_sub_bonus
        notes.append("中字")
    if prefs.prefer_115 and q["share_kind"] == "115":
        score += 6
    if q["share_kind"] == "offline":
        score -= 4
        if q["offline_type"]:
            notes.append(q["offline_type"])
    return score, notes


# ---------------------------------------------------------------- 集数解析

SE_RE = re.compile(r"\bS(\d{1,2})\s*[. _-]?\s*E(?:P)?(\d{1,4})(?:\s*[-~至到]\s*E?(\d{1,4}))?", re.I)
E_ONLY_RE = re.compile(r"\bE(?:P)?(\d{1,4})(?:\s*[-~至到]\s*E?(\d{1,4}))?\b", re.I)
CN_UPDATE_RE = re.compile(r"(?:更新至|更新到|全集|全|共)\s*第?\s*(\d{1,4})\s*集")
CN_RANGE_RE = re.compile(r"第?\s*(\d{1,4})\s*[-~至到]\s*(\d{1,4})\s*集")
CN_SINGLE_RE = re.compile(r"第\s*(\d{1,4})\s*集")
CN_BARE_RE = re.compile(r"(\d{1,4})\s*集")
COLLECTION_WORDS = ("合集", "全集", "全", "打包", "合辑")


def parse_episode_csv(value: str) -> set[int] | None:
    """解析癫影的 episodes_csv，如 '1-25'、'24,25'、'1-10,12'。"""
    text = str(value or "").strip()
    if not text:
        return None
    eps: set[int] = set()
    for part in re.split(r"[,\s]+", text):
        if not part:
            continue
        m = re.fullmatch(r"(\d{1,4})\s*[-~]\s*(\d{1,4})", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a <= b and b - a < 2000:
                eps.update(range(a, b + 1))
            continue
        if part.isdigit():
            eps.add(int(part))
    return eps or None


def parse_episodes(candidate: dict[str, Any]) -> tuple[set[int], bool, bool]:
    """返回 (集数集合, 是否合集, 信息是否完整)。

    优先使用结构化 episodes_csv；否则从标题/文件名解析中/英集数写法。
    """
    csv_eps = parse_episode_csv(str(candidate.get("episodes_csv") or ""))
    if csv_eps:
        is_collection = len(csv_eps) > 1 and (candidate.get("episode_count") or len(csv_eps)) > 1
        return csv_eps, bool(is_collection), True

    title = " ".join(str(candidate.get(k) or "") for k in ("title", "name", "file_name", "resource_name"))

    m = SE_RE.search(title)
    if m:
        start = int(m.group(2))
        end = int(m.group(3)) if m.group(3) else start
        if end < start:
            start, end = end, start
        eps = set(range(start, end + 1)) if end - start < 2000 else {start}
        return eps, len(eps) > 1, True

    m = CN_UPDATE_RE.search(title)
    if m:
        end = int(m.group(1))
        return set(range(1, end + 1)), end > 1, True

    m = CN_RANGE_RE.search(title)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if b < a:
            a, b = b, a
        eps = set(range(a, b + 1)) if b - a < 2000 else {a}
        return eps, len(eps) > 1, True

    m = CN_SINGLE_RE.search(title)
    if m:
        return {int(m.group(1))}, False, True

    m = E_ONLY_RE.search(title)
    if m:
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else start
        if end < start:
            start, end = end, start
        eps = set(range(start, end + 1)) if end - start < 2000 else {start}
        return eps, len(eps) > 1, True

    m = CN_BARE_RE.search(title)
    if m:
        n = int(m.group(1))
        if any(w in title for w in COLLECTION_WORDS):
            return set(range(1, n + 1)), True, True
        return {n}, False, True

    if any(w in title for w in COLLECTION_WORDS) and candidate.get("episode_count"):
        try:
            n = int(candidate["episode_count"])
            if n > 1:
                return set(range(1, n + 1)), True, True
        except Exception:
            pass

    return set(), False, False  # 解析不出集数 → 信息不完整


# ---------------------------------------------------------------- 选片

@dataclass
class Prefs:
    min_resolution: str = "1080P"          # 低于该分辨率直接过滤（UNKNOWN 放行但降权）
    require_chinese_subtitle: bool = False
    allow_iso: bool = False
    min_size_gb: float = 0.0
    max_size_gb: float = 0.0               # 0 表示不限
    exclude_tags: tuple[str, ...] = DEFAULT_EXCLUDE_TAGS
    prefer_115: bool = True
    chinese_sub_bonus: float = 8.0
    max_unlock_cost: int = 0               # 0 表示不限
    newest_bias_hours: int = 72            # 72 小时内发布算"新鲜"


@dataclass
class Scored:
    candidate: dict[str, Any]
    episodes: set[int]
    is_collection: bool
    covered: set[int]
    missing_after: set[int]
    extra: set[int]
    quality_score: float = 0.0
    accepted: bool = True
    reason: str = ""
    notes: list[str] = field(default_factory=list)
    freshness: float = 0.0
    unlock_cost: int = 0
    use_count: int = 0

    @property
    def rank_key(self) -> tuple:
        # 覆盖优先：剩余缺集越少越好；其次废料（多搬的集）越少越好；
        # 然后画质、新鲜度、解锁成本、热度。
        return (
            0 if self.accepted else 1,
            len(self.missing_after),
            len(self.extra),
            -self.quality_score,
            -self.freshness,
            self.unlock_cost,
            -self.use_count,
        )


def _parse_time(value: Any) -> float:
    if not value:
        return 0.0
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return 0.0


def normalize_candidate(raw: dict[str, Any]) -> dict[str, Any]:
    """把不同来源的候选统一成内核字段（兼容癫影 openapi / 内部接口 / UI 抽取）。"""
    out = dict(raw)
    out.setdefault("title", raw.get("title") or raw.get("file_name") or raw.get("resource_name") or raw.get("name"))
    if not out.get("episodes_csv") and raw.get("episodes"):
        try:
            out["episodes_csv"] = ",".join(str(int(x)) for x in raw["episodes"])
        except Exception:
            pass
    q = extract_quality(raw)
    out.setdefault("share_kind", q["share_kind"])
    out["_quality"] = q
    return out


def select(
    candidates: Iterable[dict[str, Any]],
    needed: Iterable[int],
    prefs: Prefs | None = None,
    *,
    show_title: str = "",
) -> dict[str, Any]:
    """挑选候选。

    needed: 待补集数集合（例如 {24, 25}）。空集合表示"只按画质挑最新的"。
    返回 {pick, ranked, needs_review, review_reason, summary}
    """
    prefs = prefs or Prefs()
    needed_set = {int(x) for x in needed}
    scored: list[Scored] = []
    now = datetime.now(timezone.utc).timestamp()

    for raw in candidates:
        cand = normalize_candidate(raw)
        q = cand["_quality"]
        eps, is_collection, complete = parse_episodes(cand)
        item = Scored(
            candidate=cand, episodes=eps, is_collection=is_collection,
            covered=set(), missing_after=set(needed_set), extra=set(),
        )
        item.unlock_cost = int(cand.get("unlock_cost") or 0)
        item.use_count = int(cand.get("use_count") or 0)
        ts = _parse_time(cand.get("created_at"))
        item.freshness = ts

        # ---- 硬过滤
        if q["is_iso"] and not prefs.allow_iso:
            item.accepted = False
            item.reason = "ISO 原盘，已按偏好跳过"
            scored.append(item)
            continue
        blob = _norm_text(cand.get("title"))
        hit_tag = next((t for t in prefs.exclude_tags if _norm_text(t) in blob), None)
        if hit_tag:
            item.accepted = False
            item.reason = f"命中排除标签 {hit_tag}"
            scored.append(item)
            continue
        if prefs.require_chinese_subtitle and not q["has_chinese_subtitle"]:
            item.accepted = False
            item.reason = "缺少中文字幕"
            scored.append(item)
            continue
        if prefs.max_unlock_cost and item.unlock_cost > prefs.max_unlock_cost:
            item.accepted = False
            item.reason = f"解锁成本 {item.unlock_cost} 超出上限"
            scored.append(item)
            continue
        size_gb = q["size_bytes"] / 1024 ** 3 if q["size_bytes"] else 0
        if prefs.max_size_gb and size_gb > prefs.max_size_gb:
            item.accepted = False
            item.reason = f"体积 {size_gb:.1f}GB 超出上限"
            scored.append(item)
            continue
        if prefs.min_size_gb and size_gb and size_gb < prefs.min_size_gb:
            item.accepted = False
            item.reason = f"体积 {size_gb:.1f}GB 低于下限"
            scored.append(item)
            continue

        # ---- 覆盖度
        if needed_set:
            item.covered = eps & needed_set
            item.missing_after = needed_set - item.covered
            item.extra = eps - needed_set
            if not item.covered:
                item.accepted = False
                item.reason = "未覆盖任何待补集数"
                scored.append(item)
                continue
        else:
            item.covered = eps
            item.missing_after = set()

        qs, notes = quality_score(q, prefs)
        item.quality_score = qs
        item.notes = notes
        if ts and (now - ts) <= prefs.newest_bias_hours * 3600:
            item.notes.append("新发布")
        if not complete:
            item.notes.append("集数信息缺失")
        scored.append(item)

    scored.sort(key=lambda x: x.rank_key)
    pick = next((x for x in scored if x.accepted), None)

    needs_review = False
    review_reason = ""
    if pick is None:
        needs_review, review_reason = True, "没有可用候选（全部被过滤或未覆盖待补集数）"
    elif not parse_episodes(pick.candidate)[2]:
        needs_review, review_reason = True, "首选候选缺少集数信息，需人工确认"
    else:
        accepted = [x for x in scored if x.accepted]
        if len(accepted) >= 2:
            top, second = accepted[0], accepted[1]
            same_coverage = top.missing_after == second.missing_after and top.extra == second.extra
            if same_coverage and abs(top.quality_score - second.quality_score) < 1e-6:
                needs_review, review_reason = True, "两条候选覆盖与画质完全相同，需人工选择"
    if show_title and pick is not None:
        title_text = _norm_text(pick.candidate.get("title"))
        if show_title and not any(ch in str(pick.candidate.get("title") or "") for ch in show_title[:4]):
            needs_review, review_reason = True, "候选标题与订阅剧名匹配存疑，需人工确认"

    return {
        "pick": pick,
        "ranked": scored,
        "needs_review": needs_review,
        "review_reason": review_reason,
        "summary": _summarize(needed_set, pick, scored),
    }


def _summarize(needed: set[int], pick: Scored | None, ranked: list[Scored]) -> str:
    if pick is None:
        return f"待补 {sorted(needed) or '全部'}：无可用候选（共评估 {len(ranked)} 条）"
    eps = sorted(pick.episodes)
    covered = sorted(pick.covered)
    return (
        f"待补 {sorted(needed) or '全部'} → 选中「{str(pick.candidate.get('title'))[:60]}」"
        f"（含 {eps[0]}-{eps[-1]} 集，覆盖待补 {covered}，需多搬 {len(pick.extra)} 集，"
        f"画质分 {pick.quality_score:.0f}：{'/'.join(pick.notes)}）"
    )
