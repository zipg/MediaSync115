"""归档二级目录（电影/剧集下的分类文件夹）配置与解析"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

INVALID_FOLDER_CHARS = re.compile(r'[\\/:*?"<>|]')

ARCHIVE_COUNTRY_OPTION_GROUPS: list[dict[str, Any]] = [
    {
        "label": "华语地区",
        "countries": [
            {"code": "CN", "name": "中国大陆"},
            {"code": "HK", "name": "中国香港"},
            {"code": "TW", "name": "中国台湾"},
            {"code": "MO", "name": "中国澳门"},
            {"code": "SG", "name": "新加坡"},
        ],
    },
    {
        "label": "日韩",
        "countries": [
            {"code": "JP", "name": "日本"},
            {"code": "KR", "name": "韩国"},
            {"code": "KP", "name": "朝鲜"},
        ],
    },
    {
        "label": "北美",
        "countries": [
            {"code": "US", "name": "美国"},
            {"code": "CA", "name": "加拿大"},
            {"code": "MX", "name": "墨西哥"},
        ],
    },
    {
        "label": "欧洲",
        "countries": [
            {"code": "GB", "name": "英国"},
            {"code": "FR", "name": "法国"},
            {"code": "DE", "name": "德国"},
            {"code": "IT", "name": "意大利"},
            {"code": "ES", "name": "西班牙"},
            {"code": "RU", "name": "俄罗斯"},
            {"code": "SE", "name": "瑞典"},
            {"code": "NL", "name": "荷兰"},
            {"code": "BE", "name": "比利时"},
            {"code": "CH", "name": "瑞士"},
            {"code": "AT", "name": "奥地利"},
            {"code": "PL", "name": "波兰"},
            {"code": "DK", "name": "丹麦"},
            {"code": "NO", "name": "挪威"},
            {"code": "FI", "name": "芬兰"},
            {"code": "IE", "name": "爱尔兰"},
            {"code": "PT", "name": "葡萄牙"},
            {"code": "GR", "name": "希腊"},
            {"code": "CZ", "name": "捷克"},
        ],
    },
    {
        "label": "东南亚",
        "countries": [
            {"code": "TH", "name": "泰国"},
            {"code": "VN", "name": "越南"},
            {"code": "PH", "name": "菲律宾"},
            {"code": "ID", "name": "印度尼西亚"},
            {"code": "MY", "name": "马来西亚"},
        ],
    },
    {
        "label": "南亚·中东·其他",
        "countries": [
            {"code": "IN", "name": "印度"},
            {"code": "AU", "name": "澳大利亚"},
            {"code": "NZ", "name": "新西兰"},
            {"code": "BR", "name": "巴西"},
            {"code": "AR", "name": "阿根廷"},
            {"code": "TR", "name": "土耳其"},
            {"code": "IL", "name": "以色列"},
            {"code": "SA", "name": "沙特阿拉伯"},
            {"code": "AE", "name": "阿联酋"},
            {"code": "ZA", "name": "南非"},
            {"code": "EG", "name": "埃及"},
        ],
    },
]

ARCHIVE_TV_GENRE_OPTIONS: list[dict[str, Any]] = [
    {"id": 16, "name": "动画"},
    {"id": 18, "name": "剧情"},
    {"id": 35, "name": "喜剧"},
    {"id": 80, "name": "犯罪"},
    {"id": 99, "name": "纪录片"},
    {"id": 10759, "name": "动作冒险"},
    {"id": 10762, "name": "儿童"},
    {"id": 10763, "name": "新闻"},
    {"id": 10764, "name": "真人秀"},
    {"id": 10765, "name": "科幻奇幻"},
    {"id": 10766, "name": "肥皂剧"},
    {"id": 10767, "name": "脱口秀"},
    {"id": 10768, "name": "战争政治"},
]

MOVIE_MATCH_TYPE_OPTIONS: list[dict[str, str]] = [
    {"value": "country", "label": "按国家/地区"},
    {"value": "fallback", "label": "兜底（其余未匹配）"},
]

TV_MATCH_TYPE_OPTIONS: list[dict[str, str]] = [
    {"value": "country", "label": "按国家/地区"},
    {"value": "genre", "label": "按 TMDB 剧集类型"},
    {"value": "fallback", "label": "兜底（其余未匹配）"},
]

DEFAULT_ARCHIVE_SUBDIRS: dict[str, Any] = {
    "movie_root": "电影",
    "tv_root": "剧集",
    "movie_categories": [
        {
            "id": "cn",
            "name": "华语电影",
            "enabled": True,
            "match_countries": ["CN", "HK", "TW", "SG"],
        },
        {
            "id": "jk",
            "name": "日韩电影",
            "enabled": True,
            "match_countries": ["JP", "KR", "KP"],
        },
        {
            "id": "foreign",
            "name": "外语电影",
            "enabled": True,
            "is_fallback": True,
        },
    ],
    "tv_categories": [
        {
            "id": "doc",
            "name": "纪录片",
            "enabled": True,
            "match_genre_ids": [99],
        },
        {
            "id": "anime",
            "name": "动漫",
            "enabled": True,
            "match_genre_ids": [16],
        },
        {
            "id": "variety",
            "name": "综艺",
            "enabled": True,
            "match_genre_ids": [10764, 10767, 10763],
        },
        {
            "id": "cn",
            "name": "国产剧",
            "enabled": True,
            "match_countries": ["CN", "HK", "TW", "SG"],
        },
        {
            "id": "jk",
            "name": "日韩剧",
            "enabled": True,
            "match_countries": ["JP", "KR", "KP"],
        },
        {
            "id": "us_gb",
            "name": "美英剧",
            "enabled": True,
            "match_countries": ["US", "GB"],
        },
        {
            "id": "default",
            "name": "美英剧",
            "enabled": True,
            "is_fallback": True,
        },
    ],
}


def sanitize_folder_name(value: str, *, fallback: str = "未分类") -> str:
    text = INVALID_FOLDER_CHARS.sub(" ", str(value or "")).strip()
    return text or fallback


def resolve_root_folder_name(raw: dict[str, Any], key: str, default: str) -> str:
    """解析归档一级根目录名（movie_root / tv_root）。

    支持三种情况：
    - 配置里没有该键：沿用默认值，保持历史行为不变；
    - 显式传入空字符串：扁平模式，分类目录直接位于输出目录下，不再额外套一层根目录；
    - 其他取值：清洗非法字符后使用。
    """
    if key not in raw or raw.get(key) is None:
        return default
    text = INVALID_FOLDER_CHARS.sub(" ", str(raw.get(key))).strip()
    return text


def _normalize_country_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    countries: list[str] = []
    for item in value:
        code = str(item or "").upper().strip()
        if code and code not in countries:
            countries.append(code)
    return countries


def _normalize_genre_ids(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    ids: list[int] = []
    for item in value:
        try:
            gid = int(item)
        except (TypeError, ValueError):
            continue
        if gid not in ids:
            ids.append(gid)
    return ids


def _normalize_category_list(
    raw_items: Any,
    *,
    defaults: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list) or not raw_items:
        return deepcopy(defaults)

    default_by_id = {str(item.get("id") or ""): item for item in defaults}
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            continue
        item_id = str(raw.get("id") or "").strip() or f"custom_{index + 1}"
        if item_id in seen_ids:
            continue
        seen_ids.add(item_id)

        base = default_by_id.get(item_id, {})
        name = sanitize_folder_name(
            str(raw.get("name") or base.get("name") or ""),
            fallback=str(base.get("name") or "未分类"),
        )
        enabled = bool(raw.get("enabled", base.get("enabled", True)))
        category: dict[str, Any] = {
            "id": item_id,
            "name": name,
            "enabled": enabled,
        }
        if bool(raw.get("is_fallback", base.get("is_fallback"))):
            category["is_fallback"] = True

        countries = _normalize_country_list(
            raw.get("match_countries", base.get("match_countries"))
        )
        if countries:
            category["match_countries"] = countries

        genre_ids = _normalize_genre_ids(
            raw.get("match_genre_ids", base.get("match_genre_ids"))
        )
        if genre_ids:
            category["match_genre_ids"] = genre_ids

        normalized.append(category)

    if not normalized:
        return deepcopy(defaults)

    enabled_items = [item for item in normalized if item.get("enabled")]
    if not enabled_items:
        raise ValueError("至少需要启用一个二级分类目录")

    if not any(item.get("is_fallback") for item in enabled_items):
        raise ValueError("至少需要保留一个启用的兜底分类目录")

    non_fallback = [item for item in enabled_items if not item.get("is_fallback")]
    names = [str(item.get("name") or "") for item in non_fallback]
    if len(set(names)) != len(names):
        raise ValueError("启用的二级分类目录名称不能重复")

    return normalized


def normalize_archive_subdirs(raw: Any) -> dict[str, Any]:
    """校验并归一化归档二级目录配置"""
    if not isinstance(raw, dict):
        raw = {}

    movie_root = resolve_root_folder_name(
        raw, "movie_root", DEFAULT_ARCHIVE_SUBDIRS["movie_root"]
    )
    tv_root = resolve_root_folder_name(
        raw, "tv_root", DEFAULT_ARCHIVE_SUBDIRS["tv_root"]
    )
    movie_categories = _normalize_category_list(
        raw.get("movie_categories"),
        defaults=DEFAULT_ARCHIVE_SUBDIRS["movie_categories"],
    )
    tv_categories = _normalize_category_list(
        raw.get("tv_categories"),
        defaults=DEFAULT_ARCHIVE_SUBDIRS["tv_categories"],
    )
    return {
        "movie_root": movie_root,
        "tv_root": tv_root,
        "movie_categories": movie_categories,
        "tv_categories": tv_categories,
    }


def _extract_country_codes(detail: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    origin_country = detail.get("origin_country")
    if isinstance(origin_country, list):
        for country in origin_country:
            code = str(country).upper().strip()
            if code and code not in codes:
                codes.append(code)
    elif isinstance(origin_country, str) and origin_country.strip():
        code = origin_country.strip().upper()
        if code not in codes:
            codes.append(code)

    production_countries = detail.get("production_countries")
    if isinstance(production_countries, list):
        for pc in production_countries:
            if not isinstance(pc, dict):
                continue
            iso = str(pc.get("iso_3166_1") or "").upper().strip()
            if iso and iso not in codes:
                codes.append(iso)
    return codes


def _extract_genre_ids(detail: dict[str, Any]) -> list[int]:
    genres = detail.get("genres") if isinstance(detail.get("genres"), list) else []
    ids: list[int] = []
    for genre in genres:
        if not isinstance(genre, dict):
            continue
        try:
            gid = int(genre.get("id"))
        except (TypeError, ValueError):
            continue
        if gid not in ids:
            ids.append(gid)
    return ids


def _pick_category(
    categories: list[dict[str, Any]],
    *,
    country_codes: list[str] | None = None,
    genre_ids: list[int] | None = None,
    prefer_genre: bool = False,
) -> str:
    enabled = [item for item in categories if item.get("enabled")]
    if not enabled:
        return "未分类"

    country_codes = country_codes or []
    genre_ids = genre_ids or []

    if prefer_genre and genre_ids:
        for item in enabled:
            match_ids = item.get("match_genre_ids")
            if not isinstance(match_ids, list):
                continue
            if any(gid in match_ids for gid in genre_ids):
                return str(item.get("name") or "未分类")

    for item in enabled:
        if item.get("is_fallback"):
            continue
        match_countries = item.get("match_countries")
        if not isinstance(match_countries, list):
            continue
        if any(code in match_countries for code in country_codes):
            return str(item.get("name") or "未分类")

    for item in enabled:
        if item.get("is_fallback"):
            return str(item.get("name") or "未分类")

    return str(enabled[0].get("name") or "未分类")


def resolve_movie_category(
    detail: dict[str, Any],
    subdirs: dict[str, Any] | None = None,
) -> str:
    config = normalize_archive_subdirs(subdirs)
    return _pick_category(
        config["movie_categories"],
        country_codes=_extract_country_codes(detail),
    )


def resolve_tv_category(
    detail: dict[str, Any],
    subdirs: dict[str, Any] | None = None,
) -> str:
    config = normalize_archive_subdirs(subdirs)
    genre_ids = _extract_genre_ids(detail)
    country_codes = _extract_country_codes(detail)
    return _pick_category(
        config["tv_categories"],
        country_codes=country_codes,
        genre_ids=genre_ids,
        prefer_genre=True,
    )


def get_archive_subdir_options() -> dict[str, Any]:
    """返回归档二级目录可视化配置所需的选项"""
    return {
        "country_groups": ARCHIVE_COUNTRY_OPTION_GROUPS,
        "tv_genres": ARCHIVE_TV_GENRE_OPTIONS,
        "movie_match_types": MOVIE_MATCH_TYPE_OPTIONS,
        "tv_match_types": TV_MATCH_TYPE_OPTIONS,
    }
