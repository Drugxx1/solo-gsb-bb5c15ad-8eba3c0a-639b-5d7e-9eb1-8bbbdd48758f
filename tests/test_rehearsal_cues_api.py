"""排练翻页提示接口的端到端测试（FastAPI TestClient + 临时 SQLite）。"""

from __future__ import annotations

import importlib
import sys

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """以临时 DATA_DIR 重新加载 app.main，得到隔离的服务实例。"""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    sys.modules.pop("app.main", None)
    main = importlib.import_module("app.main")
    with TestClient(main.app) as c:
        yield c


def _create_plan(client) -> str:
    # 方案两页：页 1 = 小节 0..1（断页在小节 2，静默 5），页 2 = 小节 2..3
    resp = client.post(
        "/api/v1/plans",
        json={
            "measure_heights": [3, 3, 4, 2],
            "silence_seconds": [2, 5, 1, 0],
            "page_height": 6,
            "min_turn_time": 2,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_cues_happy_path_with_repeat(client):
    plan_id = _create_plan(client)
    resp = client.post(
        f"/api/v1/plans/{plan_id}/rehearsal/cues",
        json={
            "beats_per_measure": [4, 4, 4, 4],
            "tempo_bpm": 120,
            "repeats": [
                {"start_measure": 1, "end_measure": 2, "play_count": 2}
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == plan_id
    assert body["total_duration"] == pytest.approx(12.0)
    assert body["min_turn_time"] == 2.0
    assert body["cues"] == [
        {
            "time_seconds": 4.0,
            "from_page": 1,
            "to_page": 2,
            "from_measure": 1,
            "from_playthrough": 1,
            "to_measure": 2,
            "to_playthrough": 1,
            "direction": "forward",
            "silence_seconds": 5.0,
            "sufficient": True,
        },
        {
            "time_seconds": 6.0,
            "from_page": 2,
            "to_page": 1,
            "from_measure": 2,
            "from_playthrough": 1,
            "to_measure": 1,
            "to_playthrough": 2,
            "direction": "backward",
            "silence_seconds": 1.0,
            "sufficient": False,
        },
        {
            "time_seconds": 8.0,
            "from_page": 1,
            "to_page": 2,
            "from_measure": 1,
            "from_playthrough": 2,
            "to_measure": 2,
            "to_playthrough": 2,
            "direction": "forward",
            "silence_seconds": 5.0,
            "sufficient": True,
        },
    ]


def test_cues_without_repeats_single_boundary(client):
    plan_id = _create_plan(client)
    resp = client.post(
        f"/api/v1/plans/{plan_id}/rehearsal/cues",
        json={"beats_per_measure": [4, 4, 4, 4], "tempo_bpm": 60},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_duration"] == pytest.approx(16.0)
    assert len(body["cues"]) == 1
    cue = body["cues"][0]
    # 60 BPM × 4 拍 -> 每小节 4 秒，进入小节 2 的边界为 8 秒
    assert cue["time_seconds"] == pytest.approx(8.0)
    assert cue["direction"] == "forward"
    assert (cue["from_measure"], cue["to_measure"]) == (1, 2)
    assert (cue["from_page"], cue["to_page"]) == (1, 2)
    assert cue["sufficient"] is True


def test_cues_repeats_optional(client):
    plan_id = _create_plan(client)
    resp = client.post(
        f"/api/v1/plans/{plan_id}/rehearsal/cues",
        json={
            "beats_per_measure": [4, 4, 4, 4],
            "tempo_bpm": 120,
            "repeats": [],
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["cues"][0]["time_seconds"] == pytest.approx(4.0)


def test_cues_plan_not_found(client):
    resp = client.post(
        "/api/v1/plans/does-not-exist/rehearsal/cues",
        json={"beats_per_measure": [4], "tempo_bpm": 120},
    )
    assert resp.status_code == 404
    assert "规划结果不存在" in resp.json()["detail"]


def test_cues_beats_length_mismatch_is_422(client):
    plan_id = _create_plan(client)
    resp = client.post(
        f"/api/v1/plans/{plan_id}/rehearsal/cues",
        json={"beats_per_measure": [4, 4, 4], "tempo_bpm": 120},
    )
    assert resp.status_code == 422
    assert "数组长度" in resp.json()["detail"]


def test_cues_overlapping_repeats_is_422(client):
    plan_id = _create_plan(client)
    resp = client.post(
        f"/api/v1/plans/{plan_id}/rehearsal/cues",
        json={
            "beats_per_measure": [4, 4, 4, 4],
            "tempo_bpm": 120,
            "repeats": [
                {"start_measure": 0, "end_measure": 2, "play_count": 2},
                {"start_measure": 1, "end_measure": 3, "play_count": 2},
            ],
        },
    )
    assert resp.status_code == 422
    assert "重叠或嵌套" in resp.json()["detail"]


def test_cues_reject_basic_validation_errors(client):
    plan_id = _create_plan(client)
    base = {"beats_per_measure": [4, 4, 4, 4], "tempo_bpm": 120}

    r = client.post(
        f"/api/v1/plans/{plan_id}/rehearsal/cues",
        json=dict(base, tempo_bpm=0),
    )
    assert r.status_code == 422

    r = client.post(
        f"/api/v1/plans/{plan_id}/rehearsal/cues",
        json=dict(base, beats_per_measure=[4, 4, -4, 4]),
    )
    assert r.status_code == 422

    r = client.post(
        f"/api/v1/plans/{plan_id}/rehearsal/cues",
        json=dict(base, extra_field=1),
    )
    assert r.status_code == 422

    # 不接受时间戳字段（提示接口只收排练参数）
    r = client.post(
        f"/api/v1/plans/{plan_id}/rehearsal/cues",
        json=dict(base, timestamps=[0]),
    )
    assert r.status_code == 422


def test_single_page_plan_returns_empty_cues(client):
    resp = client.post(
        "/api/v1/plans",
        json={
            "measure_heights": [1, 1, 1],
            "silence_seconds": [0, 0, 0],
            "page_height": 10,
            "min_turn_time": 0,
        },
    )
    plan_id = resp.json()["id"]
    resp = client.post(
        f"/api/v1/plans/{plan_id}/rehearsal/cues",
        json={
            "beats_per_measure": [4, 4, 4],
            "tempo_bpm": 120,
            "repeats": [
                {"start_measure": 0, "end_measure": 2, "play_count": 2}
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    # 整篇只有一页，重复演奏也不产生任何翻页提示
    assert resp.json()["cues"] == []