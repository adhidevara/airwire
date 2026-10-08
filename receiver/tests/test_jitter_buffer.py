import numpy as np

from conftest import FRAME
from airwire.jitter_buffer import JitterBuffer


def make(clock, **kw) -> JitterBuffer:
    kw.setdefault("initial_depth", 2)
    return JitterBuffer(clock=clock, **kw)


def push(jb, packets, seqs, redundancy=True):
    for s in seqs:
        red = packets[s - 1] if redundancy and s > 0 else None
        jb.push(s, packets[s], red)


def test_preroll_waits_for_target(clock, packets):
    jb = make(clock)
    assert jb.pop() is None
    push(jb, packets, [0])
    assert jb.pop() is None
    push(jb, packets, [1])
    frame = jb.pop()
    assert frame.shape == (FRAME, 2)
    assert jb.playing and jb.stats.played == 1


def test_reordered_packets_play_in_order_without_concealment(clock, packets):
    jb = make(clock)
    push(jb, packets, [0, 2, 1, 3])
    for _ in range(4):
        assert jb.pop() is not None
    s = jb.stats
    assert (s.played, s.recovered, s.fec, s.concealed, s.underruns) == (4, 0, 0, 0, 0)


def test_preroll_accepts_an_earlier_packet(clock, packets):
    jb = make(clock)
    push(jb, packets, [1, 0])
    jb.pop()
    jb.pop()
    assert jb.stats.played == 2 and jb.stats.concealed == 0


def test_single_loss_recovered_from_redundant_copy(clock, packets):
    jb = make(clock)
    push(jb, packets, [0, 1, 3, 4])  # 2 lost, 3 carries a copy of 2
    for _ in range(5):
        jb.pop()
    assert jb.stats.recovered == 1
    assert jb.stats.fec == 0 and jb.stats.concealed == 0


def test_loss_without_redundancy_uses_fec_then_plc(clock, packets):
    jb = make(clock)
    push(jb, packets, [0, 1, 3], redundancy=False)  # 2 lost, 3 present -> FEC
    for _ in range(4):
        jb.pop()
    assert jb.stats.fec == 1

    jb = make(clock)
    push(jb, packets, [0, 1, 4], redundancy=False)  # 2 -> PLC (3 missing), 3 -> FEC from 4
    for _ in range(5):
        jb.pop()
    assert jb.stats.concealed == 1 and jb.stats.fec == 1


def test_underrun_conceals_raises_target_and_keeps_timeline(clock, packets):
    jb = make(clock)
    push(jb, packets, [0, 1])
    jb.pop(), jb.pop()
    frame = jb.pop()  # nothing buffered
    assert frame is not None and frame.shape == (FRAME, 2)
    assert jb.stats.underruns == 1 and jb.target == 3
    push(jb, packets, [2])
    jb.pop()
    # Frame 2 was played from its own packet, not skipped.
    assert jb.stats.played == 3 and jb.stats.recovered == 0 and jb.stats.fec == 0


def test_stall_goes_silent_and_prerolls_again(clock, packets):
    jb = make(clock)
    push(jb, packets, [0, 1])
    jb.pop(), jb.pop()
    results = [jb.pop() for _ in range(JitterBuffer.STALL_FRAMES + 1)]
    assert all(r is not None for r in results[:-1])
    assert results[-1] is None and not jb.playing
    assert jb.target == 3  # raised once per stall, not per frame
    push(jb, packets, [10])
    assert jb.pop() is None  # pre-roll again


def test_late_and_duplicate_packets(clock, packets):
    jb = make(clock)
    push(jb, packets, [0, 1, 2])
    jb.pop(), jb.pop()
    push(jb, packets, [0])
    push(jb, packets, [2])
    assert jb.stats.late == 1 and jb.stats.duplicates == 1


def test_reset_clears_buffer_but_keeps_target(clock, packets):
    jb = make(clock, initial_depth=4)
    push(jb, packets, range(6))
    jb.pop()
    jb.reset()
    assert jb.pop() is None and jb.depth == 0 and jb.target == 4


def test_surplus_buffer_is_trimmed_with_crossfade(clock, packets):
    jb = make(clock, drift_window=10)
    push(jb, packets, range(8))  # 8 buffered, target 2
    nxt = 8
    for _ in range(25):
        jb.pop()
        push(jb, packets, [nxt])
        nxt += 1
    assert jb.stats.dropped >= 2
    assert jb.depth < 8


def test_target_shrinks_after_stable_period(clock, packets):
    jb = make(clock, initial_depth=3, shrink_after_s=10)
    push(jb, packets, range(5))
    jb.pop()
    clock.t += 10.5
    jb.pop()
    assert jb.target == 2


def test_burst_backlog_skips_ahead(clock, packets):
    jb = make(clock, max_depth=4)
    push(jb, packets, range(20))
    frame = jb.pop()
    assert frame is not None
    assert jb.stats.dropped > 0
    assert jb.depth <= jb.target


def test_crossfade_output_is_int16(clock):
    jb = make(clock)
    a = np.full((FRAME, 2), 1000, np.int16)
    b = np.full((FRAME, 2), -1000, np.int16)
    out = jb._crossfade(a, b)
    assert out.dtype == np.int16 and out[0, 0] == 1000 and out[-1, 0] == -1000
