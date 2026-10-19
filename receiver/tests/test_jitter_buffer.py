import heapq

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


def test_underrun_conceals_and_keeps_timeline(clock, packets):
    jb = make(clock)
    push(jb, packets, [0, 1])
    jb.pop(), jb.pop()
    frame = jb.pop()  # nothing buffered
    assert frame is not None and frame.shape == (FRAME, 2)
    assert jb.stats.underruns == 1
    clock.t += 0.015
    push(jb, packets, [2])
    jb.pop()
    # Frame 2 was played from its own packet, not skipped.
    assert jb.stats.played == 3 and jb.stats.recovered == 0 and jb.stats.fec == 0
    # ...and its 15 ms lateness was recorded. Concealment already added 10 ms, so the
    # timeline is still 5 ms short and latency won't be cut.
    assert abs(min(jb._slack_sorted) - jb._offset + 0.005) < 1e-6


def test_stall_goes_silent_and_prerolls_again(clock, packets):
    jb = make(clock)
    push(jb, packets, [0, 1])
    jb.pop(), jb.pop()
    results = [jb.pop() for _ in range(JitterBuffer.STALL_FRAMES + 1)]
    assert all(r is not None for r in results[:-1])
    assert results[-1] is None and not jb.playing
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


def simulate(jb, clock, packets, arrivals, pop_start, pop_period=0.010):
    """Deliver frame s at arrivals[s] (seconds) while the sound card pops every 10 ms."""
    events = [(t, 0, s) for s, t in enumerate(arrivals)]
    n_pops = int((max(arrivals) - pop_start) / pop_period)
    events += [(pop_start + i * pop_period, 1, i) for i in range(n_pops)]
    heapq.heapify(events)
    base = clock.t
    while events:
        t, kind, s = heapq.heappop(events)
        clock.t = base + t
        if kind == 0:
            jb.push(s, packets[s % len(packets)], packets[(s - 1) % len(packets)] if s else None)
        else:
            jb.pop()


def test_surplus_latency_is_cut_on_a_calm_network(clock, packets):
    jb = make(clock)
    # Steady 10 ms arrivals, but play-out starts 100 ms late.
    simulate(jb, clock, packets, [s * 0.010 for s in range(800)], pop_start=0.100)
    assert jb.stats.dropped >= 8
    assert jb.stats.underruns == 0
    assert jb.wait_ms < 15 and jb.target <= 2


def test_bursty_capture_keeps_one_burst_of_buffer(clock, packets):
    jb = make(clock)
    # Frames leave the phone two at a time, every 20 ms.
    arrivals = [(s // 2 * 2 + 1) * 0.010 for s in range(800)]
    simulate(jb, clock, packets, arrivals, pop_start=0.150)
    assert jb.stats.underruns == 0
    assert jb.wait_ms < 25


def test_recurring_stalls_keep_enough_latency(clock, packets):
    jb = make(clock)
    # Every 2 s the network holds packets for 50 ms, then delivers them at once.
    arrivals = []
    for s in range(1200):
        t = s * 0.010
        stall_start = (t // 2.0) * 2.0 + 1.0
        arrivals.append(max(t, stall_start + 0.050) if stall_start <= t < stall_start + 0.050 else t)
    simulate(jb, clock, packets, arrivals, pop_start=0.120)
    assert jb.stats.dropped > 0          # the initial 120 ms was more than needed...
    assert jb.stats.underruns == 0       # ...but never cut below what the stalls need
    assert jb.wait_ms > 40


def test_clock_drift_is_compensated(clock, packets):
    jb = make(clock)
    # The phone's clock runs 0.1% fast (real crystals: ~0.01%): 6 extra frames over 6000.
    simulate(jb, clock, packets, [s * 0.00999 for s in range(6000)], pop_start=0.030)
    assert jb.stats.underruns == 0
    assert jb.stats.dropped >= 5
    assert jb.wait_ms < 30


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
