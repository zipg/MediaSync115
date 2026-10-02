"""归档扁平根目录与番号跳过规则测试。"""

from app.services.archive_subdir_config import normalize_archive_subdirs
from app.services.archive_service import ArchiveService, looks_like_catalog_code


def _flat_subdirs() -> dict:
    return {
        "movie_root": "",
        "tv_root": "",
        "movie_categories": [
            {"id": "mv", "name": "电影", "enabled": True, "is_fallback": True}
        ],
        "tv_categories": [
            {
                "id": "cn",
                "name": "国产剧",
                "enabled": True,
                "match_countries": ["CN", "HK", "TW", "SG"],
            },
            {"id": "anime", "name": "动漫", "enabled": True, "match_genre_ids": [16]},
            {
                "id": "en",
                "name": "英美剧",
                "enabled": True,
                "match_countries": ["US", "GB"],
                "is_fallback": True,
            },
        ],
    }


def test_flat_root_keeps_empty_name():
    """显式留空根目录名 => 扁平模式（不再回落成「剧集」/「电影」）。"""
    subdirs = normalize_archive_subdirs(_flat_subdirs())
    assert subdirs["tv_root"] == ""
    assert subdirs["movie_root"] == ""


def test_default_root_behaviour_unchanged():
    """未提供根目录名时保持历史行为。"""
    subdirs = normalize_archive_subdirs({})
    assert subdirs["tv_root"] == "剧集"
    assert subdirs["movie_root"] == "电影"


def test_explicit_root_name_still_sanitized():
    subdirs = normalize_archive_subdirs({"tv_root": "剧集", "movie_root": "电影"})
    assert subdirs["tv_root"] == "剧集"
    assert subdirs["movie_root"] == "电影"


def test_flat_tv_target_path():
    subdirs = normalize_archive_subdirs(_flat_subdirs())
    path = ArchiveService._build_target_desc(
        "tv", subdirs, "国产剧", "兰香如故 (2026)", season=1
    )
    assert path == "国产剧/兰香如故 (2026)/第1季"


def test_flat_tv_target_path_other_category():
    subdirs = normalize_archive_subdirs(_flat_subdirs())
    path = ArchiveService._build_target_desc(
        "tv", subdirs, "英美剧", "西部世界 (2016)", season=4
    )
    assert path == "英美剧/西部世界 (2016)/第4季"


def test_flat_movie_target_path():
    subdirs = normalize_archive_subdirs(_flat_subdirs())
    path = ArchiveService._build_target_desc("movie", subdirs, "电影", "沙丘 (2021)")
    assert path == "电影/沙丘 (2021)"


def test_legacy_nested_target_path_unchanged():
    subdirs = normalize_archive_subdirs({"tv_root": "剧集", "movie_root": "电影"})
    assert (
        ArchiveService._build_target_desc(
            "tv", subdirs, "国产剧", "兰香如故 (2026)", season=1
        )
        == "剧集/国产剧/兰香如故 (2026)/第1季"
    )
    assert (
        ArchiveService._build_target_desc("movie", subdirs, "外语电影", "沙丘 (2021)")
        == "电影/外语电影/沙丘 (2021)"
    )


def test_catalog_code_detection():
    """片商番号样式的文件名应被识别，避免误归档成随机剧集。"""
    assert looks_like_catalog_code("DASS-776.mp4") is True
    assert looks_like_catalog_code("SONE-446.mp4") is True
    assert looks_like_catalog_code("ipx00722_4k60fps.mp4") is True
    assert looks_like_catalog_code("START-446.mp4") is True
    assert looks_like_catalog_code("start-001.mp4") is True
    assert looks_like_catalog_code("hmn00676.mp4") is True
    assert looks_like_catalog_code("MADV-565.mp4") is True
    # 单字母前缀也属于番号/杂项命名（如 F02 / Y115）
    assert looks_like_catalog_code("F02.mp4") is True
    assert looks_like_catalog_code("Y115.mp4") is True
    assert looks_like_catalog_code("Y12.mp4") is True


def test_codec_token_not_treated_as_catalog_code():
    """x265/h264 这类编码标签文件名不应被当成番号跳过。"""
    assert looks_like_catalog_code("x265.mp4") is False
    assert looks_like_catalog_code("h264.mp4") is False
    assert looks_like_catalog_code("HEVC.mkv") is False


def test_normal_media_not_treated_as_catalog_code():
    assert looks_like_catalog_code("Westworld.S04E08.2160p.BluRay.REMUX.HEVC.mkv") is False
    assert looks_like_catalog_code("兰香如故.2026.S01E08.1080p.WEB-DL.mkv") is False
    assert looks_like_catalog_code("The.Bear.S03E01.1080p.WEB.mkv") is False
    assert looks_like_catalog_code("SE7EN.1995.1080p.mkv") is False
    assert looks_like_catalog_code("24.S01E01.mkv") is False
    assert looks_like_catalog_code("第一季.01.mp4") is False
