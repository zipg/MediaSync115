"""归档标题体检：防止 TMDB 标题张冠李戴导致目录错位。

背景：实测出现过 tmdb_id 正确、但标题被解析成另一部剧（例如 卧底娇娃 → 侠探杰克），
导致 25 个剧集被归档进错误的剧目录。本模块提供保守的中日韩标题一致性判断：
仅当「源文件名」与「匹配标题」都含中文且**毫无共同汉字**时才判定为可疑，
避免误伤纯英文命名（如 Against.the.Current.S01E40 这类译名/英文名）。
"""

from __future__ import annotations

import re

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def _cjk_chars(text: str) -> set[str]:
    return set(_CJK.findall(str(text or "")))


def _normalize_latin(text: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", str(text or "").lower())


def title_mismatch(source_name: str, matched_title: str) -> tuple[bool, str]:
    """判断匹配标题是否与源文件名明显不一致。

    返回 (是否可疑, 原因)。仅在证据充分时判定可疑，宁可不报也不误伤。
    """
    src_ttl = str(matched_title or "").strip()
    if not src_ttl:
        return False, ""

    src_cjk, ttl_cjk = _cjk_chars(source_name), _cjk_chars(src_ttl)
    if src_cjk and ttl_cjk:
        if src_cjk & ttl_cjk:
            return False, ""
        # 双方都有中文却没有一个共同汉字 → 极可能是张冠李戴
        return True, (
            f"标题体检不通过：源文件名不含匹配标题中的任何汉字"
            f"（匹配标题「{src_ttl[:24]}」）"
        )

    # 纯拉丁对纯拉丁：比较前 6 个字母
    src_lat, ttl_lat = _normalize_latin(source_name), _normalize_latin(src_ttl)
    if src_lat and ttl_lat and not src_cjk and not ttl_cjk:
        if ttl_lat[:6] and ttl_lat[:6] not in src_lat and src_lat[:6] not in ttl_lat:
            return True, f"标题体检不通过：源文件名与匹配标题「{src_ttl[:24]}」无共同关键词"

    return False, ""
