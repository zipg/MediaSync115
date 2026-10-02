"""选片内核测试：覆盖国产剧的三种常见发布形态与各类过滤规则。"""

from app.services.candidate_selector import (
    Prefs,
    parse_episodes,
    select,
)


def _c(**kw):
    base = {
        "title": kw.pop("title", "测试剧.S01E25.2160p.WEB-DL.HDR10.AAC.CHS.mp4"),
        "share_kind": "115",
        "total_size_bytes": 8 * 1024 ** 3,
        "has_chinese_subtitle": True,
        "created_at": "2026-10-02T10:00:00Z",
        "unlock_cost": 0,
        "use_count": 5,
    }
    base.update(kw)
    return base


# ---------------- 三种真实形态

def test_single_latest_episode_preferred_over_collection():
    """只缺最新一集：单集优先（合集要白搬 24 集）。"""
    cands = [
        _c(title="剧名.2026.S01E25.2160p.WEB-DL.CHS.mkv", episodes_csv="25"),
        _c(title="剧名.2026.全25集.4K合集", episodes_csv="1-25"),
    ]
    res = select(cands, needed={25}, prefs=Prefs())
    assert res["pick"] is not None
    assert parse_episodes(res["pick"].candidate)[0] == {25}
    assert res["needs_review"] is False
    assert "覆盖待补 [25]" in res["summary"]


def test_latest_two_episodes_pair_wins():
    """缺最近两集：含两集的候选优先于只含最新一集。"""
    cands = [
        _c(title="剧名.S01E25.2160p.mkv", episodes_csv="25"),
        _c(title="剧名.S01E24-E25.1080p.WEB-DL.CHS.mkv", episodes_csv="24,25"),
    ]
    res = select(cands, needed={24, 25}, prefs=Prefs())
    assert res["pick"] is not None
    assert parse_episodes(res["pick"].candidate)[0] == {24, 25}
    assert res["pick"].missing_after == set()


def test_full_collection_backfills_new_subscription():
    """新订阅缺很多集：全集合集胜出（覆盖最多待补集）。"""
    cands = [
        _c(title="剧名.2026.更新至25集.4K.WEB-DL.CHS", episodes_csv="1-25"),
        _c(title="剧名.S01E25.2160p.mkv", episodes_csv="25"),
    ]
    res = select(cands, needed=set(range(1, 11)), prefs=Prefs())
    assert res["pick"] is not None
    assert len(res["pick"].covered) == 10
    assert res["pick"].missing_after == set()


def test_collection_not_covering_needed_is_rejected():
    """合集只含已入库的集 → 不选（避免重复搬运）。"""
    cands = [_c(title="剧名.2026.1-20集合集", episodes_csv="1-20")]
    res = select(cands, needed={25}, prefs=Prefs())
    assert res["pick"] is None
    assert res["needs_review"] is True


# ---------------- 画质与偏好

def test_quality_tiebreak_4k_over_1080p():
    cands = [
        _c(title="剧名.S01E25.1080p.WEB-DL.CHS.mkv", episodes_csv="25"),
        _c(title="剧名.S01E25.2160p.WEB-DL.DV.CHS.mkv", episodes_csv="25"),
    ]
    res = select(cands, needed={25}, prefs=Prefs())
    assert "2160P" in str(res["pick"].candidate["title"]).upper()
    assert res["pick"].quality_score > 0


def test_remux_beats_webdl_when_same_resolution():
    cands = [
        _c(title="剧名.S01E25.2160p.WEB-DL.CHS.mkv", episodes_csv="25"),
        _c(title="剧名.S01E25.2160p.BluRay.REMUX.CHS.mkv", episodes_csv="25"),
    ]
    res = select(cands, needed={25}, prefs=Prefs())
    assert "REMUX" in str(res["pick"].candidate["title"]).upper()


def test_require_chinese_subtitle_filters_foreign_release():
    cands = [
        _c(title="剧名.S01E25.2160p.WEB-DL.NO-SUB.mkv", episodes_csv="25", has_chinese_subtitle=False),
        _c(title="剧名.S01E25.1080p.WEB-DL.CHS.mkv", episodes_csv="25", has_chinese_subtitle=True),
    ]
    res = select(cands, needed={25}, prefs=Prefs(require_chinese_subtitle=True))
    assert res["pick"] is not None
    assert res["pick"].candidate["has_chinese_subtitle"] is True


def test_iso_disc_filtered_by_default():
    cands = [_c(title="剧名.S01E25.2160p.BluRay.REMUX.ISO原盘", episodes_csv="25")]
    res = select(cands, needed={25}, prefs=Prefs())
    assert res["pick"] is None
    assert any("ISO" in x.reason for x in res["ranked"])


def test_speed_release_tag_excluded():
    cands = [_c(title="剧名.第25集.抢先版.2160p.mp4", episodes_csv="25")]
    res = select(cands, needed={25}, prefs=Prefs())
    assert res["pick"] is None


def test_unlock_cost_limit():
    cands = [_c(title="剧名.S01E25.2160p.mkv", episodes_csv="25", unlock_cost=99)]
    res = select(cands, needed={25}, prefs=Prefs(max_unlock_cost=50))
    assert res["pick"] is None


def test_size_limit():
    cands = [_c(title="剧名.S01E25.2160p.REMUX.mkv", episodes_csv="25", total_size_bytes=90 * 1024 ** 3)]
    res = select(cands, needed={25}, prefs=Prefs(max_size_gb=60))
    assert res["pick"] is None


def test_prefer_115_over_offline():
    """画质相同时优先 115 分享（离线需要额外下载等待）。"""
    cands = [
        _c(title="剧名.S01E25.2160p.mkv", episodes_csv="25", share_kind="offline", offline_type="ed2k"),
        _c(title="剧名.S01E25.2160p.mkv", episodes_csv="25", share_kind="115"),
    ]
    res = select(cands, needed={25}, prefs=Prefs())
    assert res["pick"].candidate["share_kind"] == "115"


def test_quality_outranks_share_kind():
    """4K 离线 vs 1080P 115：画质优先（离线只是轻微降权）。"""
    cands = [
        _c(title="剧名.S01E25.2160p.mkv", episodes_csv="25", share_kind="offline", offline_type="ed2k"),
        _c(title="剧名.S01E25.1080p.mkv", episodes_csv="25", share_kind="115"),
    ]
    res = select(cands, needed={25}, prefs=Prefs())
    assert "2160P" in str(res["pick"].candidate["title"]).upper()


# ---------------- 集数解析

def test_episode_parsing_variants():
    assert parse_episodes({"title": "剧名.2026.S01E177.2160p.WEB-DL.mkv"})[0] == {177}
    assert parse_episodes({"title": "剧名.S01E24-E25.1080p.mkv"})[0] == {24, 25}
    assert parse_episodes({"title": "剧名更新至25集"})[0] == set(range(1, 26))
    assert parse_episodes({"title": "剧名.全24集.4K合集"})[0] == set(range(1, 25))
    assert parse_episodes({"title": "剧名第25集"})[0] == {25}
    assert parse_episodes({"title": "剧名.01-25集.1080p"})[0] == set(range(1, 26))
    assert parse_episodes({"episodes_csv": "24,25"})[0] == {24, 25}
    assert parse_episodes({"episodes_csv": "1-10,12"})[0] == set(range(1, 11)) | {12}


def test_unknown_episode_info_marks_review():
    cands = [_c(title="剧名.2026.4K.WEB-DL.CHS", episodes_csv="")]
    res = select(cands, needed={25}, prefs=Prefs())
    assert res["needs_review"] is True
    assert "集数" in res["review_reason"]


def test_freshness_breaks_tie_between_identical_candidates():
    """覆盖与画质完全相同时，新发布的胜出（不应触发人工确认）。"""
    cands = [
        _c(title="剧名.S01E25.2160p.mkv", episodes_csv="25", created_at="2026-09-01T00:00:00Z"),
        _c(title="剧名.S01E25.2160p.mkv", episodes_csv="25", created_at="2026-10-02T12:00:00Z"),
    ]
    res = select(cands, needed={25}, prefs=Prefs())
    assert res["pick"] is not None
    assert str(res["pick"].candidate["created_at"]).startswith("2026-10-02")
