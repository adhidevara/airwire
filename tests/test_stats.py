from airwire.stats import NetStats


def test_loss_over_interval(clock):
    st = NetStats(clock=clock)
    for seq in range(10):
        if seq not in (3, 7):
            st.on_audio(seq, 100, now=clock.t + seq * 0.01)
    clock.t += 0.5
    st.sample()
    assert abs(st.loss - 0.2) < 1e-9
    assert st.packets_per_s == 8 / 0.5


def test_jitter_zero_for_perfect_pacing_and_positive_otherwise(clock):
    st = NetStats(clock=clock)
    for seq in range(50):
        st.on_audio(seq, 100, now=seq * 0.010)
    assert st.jitter_ms < 1e-6

    st.reset()
    for seq in range(50):
        st.on_audio(seq, 100, now=seq * 0.010 + (0.004 if seq % 2 else 0))
    assert st.jitter_ms > 1.0


def test_rtt_smoothing():
    st = NetStats()
    st.on_pong(0, now_ns=10_000_000)
    assert st.rtt_ms == 10
    st.on_pong(0, now_ns=2_000_000)
    assert st.rtt_ms == 8
