"""排练翻页提示核心的单元测试。运行：python -m pytest -q"""

from __future__ import annotations

import pytest

from app.rehearsal import RehearsalError, prepare_rehearsal


def _cue_tuples(cues):
    return [
        (
            cue.time_seconds,
            (cue.from_page, cue.to_page),
            (cue.from_measure, cue.from_playthrough),
            (cue.to_measure, cue.to_playthrough),
            cue.direction,
            cue.silence_seconds,
            cue.sufficient,
        )
        for cue in cues
    ]


def test_no_cues_on_single_page():
    # 整篇只有一页：任意相邻小节都同页，无提示
    r = prepare_rehearsal(4, [4, 4, 4, 4], 120, [], [0])
    assert r.build_page_turn_cues([2, 5, 1, 0], 2) == []


def test_single_forward_cue_without_repeats():
    # 页 1：小节 0..1，页 2：小节 2..3；120 BPM × 4 拍 -> 每小节 2 秒
    r = prepare_rehearsal(4, [4, 4, 4, 4], 120, [], [0, 2])
    cues = r.build_page_turn_cues([2, 5, 1, 0], 2)
    assert _cue_tuples(cues) == [
        (
            4.0,
            (1, 2),
            (1, 1),
            (2, 1),
            "forward",
            5.0,
            True,
        ),
    ]


def test_repeat_jump_back_cue_and_insufficient_silence():
    # 演奏序列：0, 1, 2, 1(遍2), 2(遍2), 3
    # 页 1：0..1，页 2：2..3 -> 提示依次在 t=4 前进、t=6 回翻、t=8 前进
    r = prepare_rehearsal(
        4, [4, 4, 4, 4], 120, [(1, 2, 2)], [0, 2]
    )
    cues = r.build_page_turn_cues([2, 5, 1, 0], min_turn_time=2)
    assert _cue_tuples(cues) == [
        (4.0, (1, 2), (1, 1), (2, 1), "forward", 5.0, True),
        # 回翻的静默取前一小节（小节 2）之后的静默 1 秒，低于最低翻页时间
        (6.0, (2, 1), (2, 1), (1, 2), "backward", 1.0, False),
        (8.0, (1, 2), (1, 2), (2, 2), "forward", 5.0, True),
    ]


def test_repeat_inside_one_page_generates_no_back_cue():
    # 页 1：0..2，重复段 1..2 完全落在首页，回跳同页不提示；仅进入小节 3 时翻页
    r = prepare_rehearsal(
        4, [4, 4, 4, 4], 120, [(1, 2, 2)], [0, 3]
    )
    cues = r.build_page_turn_cues([2, 5, 1, 0], 2)
    assert _cue_tuples(cues) == [
        # 序列 ...,2(遍2,t=8~10),3(t=10)：边界在 10 秒
        (10.0, (1, 2), (2, 2), (3, 1), "forward", 1.0, False),
    ]


def test_three_playthroughs_produce_two_back_cues():
    # n=3，重复段 0..1 演奏 3 遍，页 1：小节 0，页 2：小节 1..2
    # 序列：0,1, 0,1, 0,1, 2；每个 1->0 为回翻，每个 0->1 为前进
    r = prepare_rehearsal(3, [4, 4, 4], 120, [(0, 1, 3)], [0, 1])
    cues = r.build_page_turn_cues([3, 3, 0], 2)
    pages = [(c.from_page, c.to_page) for c in cues]
    assert pages == [(1, 2), (2, 1), (1, 2), (2, 1), (1, 2)]
    assert [c.direction for c in cues] == [
        "forward",
        "backward",
        "forward",
        "backward",
        "forward",
    ]
    # 遍号：第一次回翻前是小节 1 第 1 遍，最后一次前进到小节 1 第 3 遍
    assert (cues[1].from_measure, cues[1].from_playthrough) == (1, 1)
    assert (cues[-1].to_measure, cues[-1].to_playthrough) == (1, 3)
    # 最后 1(遍3)->2 同属第 2 页，且演奏结束不提示；最后一条提示后一小节为 1
    assert cues[-1].to_measure == 1
    assert [c.time_seconds for c in cues] == [2.0, 4.0, 6.0, 8.0, 10.0]


def test_silence_equal_to_min_turn_time_is_sufficient():
    r = prepare_rehearsal(2, [4, 4], 120, [], [0, 1])
    cues = r.build_page_turn_cues([2.0, 0.0], min_turn_time=2.0)
    assert len(cues) == 1
    assert cues[0].sufficient is True


def test_boundary_seconds_are_next_measure_start_times():
    # 60 BPM × 3 拍 -> 每小节 3 秒；断页在小节 2，边界为 6.0 秒
    r = prepare_rehearsal(3, [3, 3, 3], 60, [], [0, 2])
    cues = r.build_page_turn_cues([1, 1, 0], 1)
    assert cues[0].time_seconds == pytest.approx(6.0)


def test_silence_length_mismatch_raises():
    r = prepare_rehearsal(4, [4, 4, 4, 4], 120, [], [0, 2])
    with pytest.raises(RehearsalError, match="silence_seconds 数组长度"):
        r.build_page_turn_cues([2, 5, 1], 2)
