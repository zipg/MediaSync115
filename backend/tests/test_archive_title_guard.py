"""归档标题体检测试（含真实错位案例）。"""

from app.utils.archive_title_guard import title_mismatch


def test_real_regression_wodi_jiaowa_mislabeled_as_xiatajieke():
    """真实案例：卧底娇娃的剧集被匹配成「侠探杰克」。"""
    bad, reason = title_mismatch(
        "卧底娇娃.2026 - S01E25 - 第 25 集 - 2160p.WEB-DL.HEVC.HQ.AAC.mkv",
        "侠探杰克",
    )
    assert bad is True
    assert "标题体检不通过" in reason


def test_real_regression_lanxiang_mislabeled_as_xiatajieke():
    """真实案例：兰香如故被匹配成「侠探杰克」。"""
    bad, _ = title_mismatch("兰香如故(2026)-S01E40-第40集.mp4", "侠探杰克")
    assert bad is True


def test_matching_chinese_title_passes():
    bad, _ = title_mismatch("兰香如故 (2026) - S01E25.mkv", "兰香如故")
    assert bad is False


def test_partial_chinese_overlap_passes():
    bad, _ = title_mismatch("人民的名义.S01E01.1080p.mkv", "人民的名义")
    assert bad is False


def test_english_source_vs_chinese_title_not_flagged():
    """英文命名（译名/发行名）无法判断，宁可不报也不误伤。"""
    bad, _ = title_mismatch("Against.the.Current.S01E40.2160p.WEB-DL.mkv", "兰香如故")
    assert bad is False


def test_chinese_source_vs_english_title_not_flagged():
    bad, _ = title_mismatch("西部世界.S04E08.mkv", "Westworld")
    assert bad is False


def test_latin_mismatch_flagged():
    bad, _ = title_mismatch("Reacher.S04E01.2160p.mkv", "Westworld")
    assert bad is True


def test_latin_match_passes():
    bad, _ = title_mismatch("The.Bear.S03E01.1080p.WEB.mkv", "The Bear")
    assert bad is False


def test_empty_title_never_flagged():
    bad, _ = title_mismatch("anything.mkv", "")
    assert bad is False
