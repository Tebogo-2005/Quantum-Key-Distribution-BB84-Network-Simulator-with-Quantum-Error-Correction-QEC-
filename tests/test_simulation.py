"""Tests for the BB84 session loop: sifting, the sacrificed sample, the verdict.

Every test is deterministic: each run receives an explicit seed, and
assertions on error rates use samples large enough that the four-sigma band is
narrower than the tolerance. Nothing depends on wall-clock time or on the
global NumPy state, and no assertion depends on a value that an unrelated
change to the RNG stream would silently move.
"""

from __future__ import annotations

import logging

import numpy as np
import pytest

from src.network.simulation import (
    DEFAULT_KEY_LENGTH,
    SECURITY_THRESHOLD,
    SECURITY_WARNING,
    PublicChannel,
    check_qber,
    run_simulation,
    select_qber_sample,
)
from src.quantum_core.states import Basis


# --------------------------------------------------------------------------- #
# 1. Alice's source, Bob's measurement and the quantum link
# --------------------------------------------------------------------------- #


def test_default_run_prepares_512_random_bits_and_bases() -> None:
    result = run_simulation(seed=2026)

    assert DEFAULT_KEY_LENGTH == 512
    assert result.n_bits == 512
    assert len(result.alice_bits) == 512
    assert len(result.alice_bases) == 512
    assert len(result.bob_bases) == 512
    assert set(result.alice_bits) <= {0, 1}
    assert set(result.alice_bases) == {Basis.RECTILINEAR, Basis.DIAGONAL}
    assert set(result.bob_bases) == {Basis.RECTILINEAR, Basis.DIAGONAL}


def test_alice_and_bob_choose_their_bases_independently() -> None:
    result = run_simulation(4096, seed=11)
    agreement = sum(
        alice == bob for alice, bob in zip(result.alice_bases, result.bob_bases)
    )

    assert 1950 < result.alice_bases.count(Basis.RECTILINEAR) < 2150
    assert 1950 < result.bob_bases.count(Basis.RECTILINEAR) < 2150
    assert 1900 < agreement < 2200


# --------------------------------------------------------------------------- #
# 2. Sifting
# --------------------------------------------------------------------------- #


def test_sifting_keeps_exactly_the_matching_bases() -> None:
    result = run_simulation(512, seed=5)
    kept = set(result.sifted_positions)
    discarded = set(range(result.n_bits)) - kept

    assert all(result.alice_bases[i] == result.bob_bases[i] for i in kept)
    assert all(result.alice_bases[i] != result.bob_bases[i] for i in discarded)


def test_sift_rate_is_about_one_half() -> None:
    result = run_simulation(4096, seed=17)

    assert 0.47 < result.sift_rate < 0.53


def test_bases_are_announced_on_the_public_channel() -> None:
    result = run_simulation(256, seed=3)

    announcements = [message for message in result.transcript
                     if message.kind == "basis_announce"]

    assert [message.sender for message in announcements] == ["alice", "bob"]
    assert announcements[0].payload == result.alice_bases
    assert announcements[1].payload == result.bob_bases


# --------------------------------------------------------------------------- #
# 3. The sacrificed sample and the shared key
# --------------------------------------------------------------------------- #


def test_ideal_channel_produces_identical_keys() -> None:
    result = run_simulation(512, seed=7)

    assert result.qber.qber == 0.0
    assert result.qber.errors == 0
    assert result.alice_key == result.bob_key
    assert result.key_errors == 0
    assert not result.security_alarm


def test_ideal_run_exposes_the_shared_key() -> None:
    result = run_simulation(256, seed=29)

    assert result.shared_key == result.alice_key
    assert result.shared_key is not None


def test_eavesdropped_run_exposes_no_shared_key() -> None:
    """Alice's copy is not a shared key when Bob's differs."""
    result = run_simulation(1024, eavesdropped=True, seed=8)

    assert result.key_errors > 0
    assert result.shared_key is None


def test_sample_and_key_partition_the_sifted_key() -> None:
    result = run_simulation(512, seed=9)
    sifted = set(result.sifted_positions)
    sample = set(result.sample_positions)
    key = set(result.key_positions)

    assert sample | key == sifted
    assert not sample & key
    assert result.qber.sample_size == len(sample)
    assert result.key_length == result.sifted_length - result.qber.sample_size


def test_key_is_the_sifted_bits_minus_the_revealed_sample() -> None:
    result = run_simulation(512, seed=13)

    assert result.alice_key == tuple(result.alice_bits[i] for i in result.key_positions)
    assert result.bob_key == tuple(result.bob_bits[i] for i in result.key_positions)


def test_default_sample_is_twenty_percent_of_the_sifted_key() -> None:
    result = run_simulation(512, seed=13)

    assert result.qber.sample_size == int(round(0.2 * result.sifted_length))


def test_explicit_sample_size_is_honoured() -> None:
    result = run_simulation(256, seed=2, sample_size=7)

    assert result.qber.sample_size == min(7, result.sifted_length)


def test_sample_size_is_clamped_to_the_sifted_key() -> None:
    result = run_simulation(64, seed=2, sample_size=10_000)

    assert result.qber.sample_size == result.sifted_length
    assert result.key_length == 0


def test_sample_reveal_publishes_only_sacrificed_bits() -> None:
    result = run_simulation(512, seed=19)
    reveals = [message for message in result.transcript
               if message.kind == "sample_reveal"]

    assert len(reveals) == 1
    reveal = reveals[0].payload
    assert reveal.positions == result.sample_positions
    assert reveal.bits == tuple(result.alice_bits[i] for i in reveal.positions)
    assert not set(reveal.positions) & set(result.key_positions)


# --------------------------------------------------------------------------- #
# 4. QBER estimation and the security verdict
# --------------------------------------------------------------------------- #


def test_noise_drives_the_measured_qber_towards_p() -> None:
    result = run_simulation(4096, noise_probability=0.2, seed=21)

    assert result.qber.qber == pytest.approx(0.2, abs=0.06)


def test_eve_qber_is_close_to_twenty_five_percent() -> None:
    result = run_simulation(4096, eavesdropped=True, seed=31)

    assert result.qber.qber == pytest.approx(0.25, abs=0.06)
    assert result.qber.qber < 0.35
    assert result.security_alarm


def test_eve_triggers_the_critical_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.CRITICAL, logger="bb84.security"):
        result = run_simulation(2048, eavesdropped=True, seed=2026)

    assert result.security_alarm
    assert result.warning == SECURITY_WARNING
    assert result.qber.qber is not None
    assert result.qber.qber > SECURITY_THRESHOLD
    assert SECURITY_WARNING in caplog.text
    assert any(record.levelno == logging.CRITICAL for record in caplog.records)


def test_eve_warning_lives_in_the_result_not_only_in_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A caller that never configures logging must still see the verdict."""
    with caplog.at_level(logging.CRITICAL, logger="bb84.security"):
        result = run_simulation(2048, eavesdropped=True, seed=2026)

    assert SECURITY_WARNING in result.summary()
    assert result.qber.describe().endswith(SECURITY_WARNING)


def test_a_quiet_link_below_the_threshold_raises_no_alarm(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.CRITICAL, logger="bb84.security"):
        result = run_simulation(4096, noise_probability=0.02, seed=41)

    assert result.qber.qber is not None and result.qber.qber < SECURITY_THRESHOLD
    assert not result.security_alarm
    assert result.warning is None
    assert SECURITY_WARNING not in caplog.text


def test_qber_above_threshold_is_not_an_exception() -> None:
    """Aborts are results, not errors: the call returns normally."""
    result = run_simulation(1024, eavesdropped=True, seed=8)

    assert result.security_alarm is True
    assert result.key_length > 0


# --------------------------------------------------------------------------- #
# 5. check_qber and select_qber_sample
# --------------------------------------------------------------------------- #


def test_check_qber_counts_disagreements_over_the_given_positions() -> None:
    report = check_qber([0, 1, 1, 0, 1], [0, 0, 1, 1, 1], [0, 1, 2, 3, 4])

    assert report.sample_size == 5
    assert report.errors == 2
    assert report.qber == pytest.approx(0.4)
    assert report.alarm is True
    assert report.warning == SECURITY_WARNING


def test_check_qber_threshold_is_strict() -> None:
    exactly_at_threshold = check_qber([0] * 100, [0] * 89 + [1] * 11)

    assert exactly_at_threshold.qber == pytest.approx(0.11)
    assert exactly_at_threshold.alarm is False


def test_check_qber_flags_one_error_above_the_threshold(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.CRITICAL, logger="bb84.security"):
        report = check_qber([0] * 100, [0] * 88 + [1] * 12)

    assert report.alarm is True
    assert SECURITY_WARNING in caplog.text


def test_check_qber_without_positions_compares_every_supplied_bit() -> None:
    report = check_qber([0, 1], [1, 1])

    assert report.positions == (0, 1)
    assert report.errors == 1
    assert report.qber == pytest.approx(0.5)


def test_check_qber_on_an_empty_sample_is_inconclusive() -> None:
    report = check_qber([0, 1], [1, 1], [])

    assert report.qber is None
    assert report.alarm is False
    assert report.upper_bound is None
    assert "nothing to sample" in report.describe()


def test_finite_sample_bound_tightens_with_the_sample_size() -> None:
    small = check_qber([0] * 32, [0] * 32)
    large = check_qber([0] * 320, [0] * 320)

    assert small.upper_bound > large.upper_bound
    assert small.is_conclusive is False
    assert large.is_conclusive is True


@pytest.mark.parametrize("threshold", [0.0, 1.0, -0.1, 1.1])
def test_check_qber_rejects_invalid_thresholds(threshold: float) -> None:
    with pytest.raises(ValueError, match="threshold"):
        check_qber([0], [0], [0], threshold=threshold)


def test_check_qber_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError, match="equal length"):
        check_qber([0, 1], [0])


@pytest.mark.parametrize("positions", [[2], [-1], [0, 0]])
def test_check_qber_rejects_invalid_positions(positions: list[int]) -> None:
    with pytest.raises(ValueError):
        check_qber([0, 1], [0, 1], positions)


def test_check_qber_rejects_non_integer_positions() -> None:
    """``int(1.0)`` would silently compare the wrong bit."""
    with pytest.raises(TypeError, match="integers"):
        check_qber([0, 1], [0, 1], [1.0])  # type: ignore[list-item]


def test_select_qber_sample_is_uniform_without_replacement() -> None:
    sample = select_qber_sample(tuple(range(100)), 10, rng=np.random.default_rng(4))

    assert len(sample) == 10
    assert list(sample) == sorted(sample)
    assert len(set(sample)) == 10
    assert all(0 <= position < 100 for position in sample)


def test_select_qber_sample_edge_cases() -> None:
    rng = np.random.default_rng(0)

    assert select_qber_sample((1, 2, 3), 0, rng=rng) == ()
    with pytest.raises(ValueError, match="size"):
        select_qber_sample((1, 2, 3), 4, rng=rng)
    with pytest.raises(ValueError, match="unique"):
        select_qber_sample((1, 1, 2), 2, rng=rng)


# --------------------------------------------------------------------------- #
# 6. Determinism, validation and edge cases
# --------------------------------------------------------------------------- #


def test_identical_seeds_reproduce_the_whole_session() -> None:
    first = run_simulation(256, noise_probability=0.05, eavesdropped=True, seed=77)
    second = run_simulation(256, noise_probability=0.05, eavesdropped=True, seed=77)

    assert first == second
    assert first.alice_key == second.alice_key


def test_different_seeds_produce_different_runs() -> None:
    first = run_simulation(256, seed=1)
    second = run_simulation(256, seed=2)

    assert first.alice_bits != second.alice_bits
    assert first.alice_key != second.alice_key


def test_summary_reports_the_session_counters() -> None:
    summary = run_simulation(512, seed=23).summary()

    assert "512 photons" in summary
    assert "sifted" in summary
    assert "key" in summary


def test_single_bit_session_does_not_crash() -> None:
    result = run_simulation(1, seed=1)

    assert result.n_bits == 1
    assert result.key_length == 0
    if result.sifted_length == 0:
        assert result.qber.qber is None


def test_run_simulation_rejects_non_positive_lengths() -> None:
    with pytest.raises(ValueError, match="n_bits"):
        run_simulation(0)


def test_run_simulation_rejects_non_integer_lengths() -> None:
    with pytest.raises(TypeError, match="n_bits"):
        run_simulation(1.5)  # type: ignore[arg-type]


@pytest.mark.parametrize("noise", [-0.1, 1.1, float("nan")])
def test_run_simulation_rejects_invalid_noise(noise: float) -> None:
    with pytest.raises(ValueError, match="noise_probability"):
        run_simulation(8, noise_probability=noise)


@pytest.mark.parametrize("fraction", [0.0, -0.5, 1.5])
def test_run_simulation_rejects_invalid_sample_fractions(fraction: float) -> None:
    with pytest.raises(ValueError, match="sample_fraction"):
        run_simulation(8, sample_fraction=fraction)


def test_public_channel_rejects_unknown_message_kinds() -> None:
    with pytest.raises(ValueError, match="unknown message kind"):
        PublicChannel().broadcast("alice", "key_reveal", ())
