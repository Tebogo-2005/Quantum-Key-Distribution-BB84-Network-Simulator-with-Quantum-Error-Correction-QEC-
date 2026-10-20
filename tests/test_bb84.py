"""Physics tests for the BB84 photon circuits and the 3-qubit bit-flip code.

Every assertion is deterministic: analytic state vectors/density matrices and
exact outcome averages replace shot noise, so the suite cannot flake. Where
Eve's random basis would make a single run conditional, the test averages over
all eight equally likely basis patterns instead of sampling.
"""

from __future__ import annotations

import itertools

import numpy as np
import pennylane as qml
import pytest

from src.quantum_core.bb84 import (
    Basis,
    apply_quantum_channel,
    build_protected_bit_circuit,
    decode_bit_flip,
    encode_bit_flip,
    index_to_bits,
    measure_photon,
    photon_outcome_probabilities,
    prepare_photon,
    transmit_photon,
    transmit_protected_bit,
)
from src.quantum_core.states import photon_state

ATOL = 1e-10
EVE_PATTERNS = tuple(itertools.product((Basis.Z, Basis.X), repeat=3))


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def single_photon_run(
    bit: int,
    basis: str,
    measurement_basis: str,
    *,
    noise_probability: float = 0.0,
    eavesdropped: bool = False,
    phase_flip_probability: float | None = None,
    eve_basis: object = None,
) -> np.ndarray:
    """Return ``[P(0), P(1)]`` for one prepared photon after the channel."""
    device = qml.device("default.mixed", wires=1)

    @qml.qnode(device)
    def circuit() -> "qml.measurements.ProbabilityMP":
        prepare_photon(bit, basis, wires=0)
        apply_quantum_channel(
            0,
            noise_probability,
            eavesdropped=eavesdropped,
            phase_flip_probability=phase_flip_probability,
            eve_basis=eve_basis,
        )
        return measure_photon(measurement_basis, wires=0)

    return np.asarray(circuit(), dtype=float)


def block_recovery(bit: int, noise_probability: float, eve_basis: object = None) -> float:
    """Exact probability that a block decodes back to ``bit``."""
    circuit = build_protected_bit_circuit(
        bit,
        noise_probability,
        eavesdropped=eve_basis is not None,
        eve_basis=eve_basis,
    )
    probabilities = np.asarray(circuit(), dtype=float)
    return float(
        sum(
            probability
            for outcome, probability in enumerate(probabilities)
            if decode_bit_flip(index_to_bits(outcome, 3)).logical_bit == bit
        )
    )


# --------------------------------------------------------------------------- #
# 1. State preparation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bit", [0, 1])
@pytest.mark.parametrize("basis", ["Z", "X", "rectilinear", "diagonal"])
def test_prepare_photon_matches_analytic_state(bit: int, basis: str) -> None:
    device = qml.device("default.mixed", wires=1)

    @qml.qnode(device)
    def circuit() -> "qml.measurements.DensityMatrixMP":
        prepare_photon(bit, basis, wires=0)
        return qml.density_matrix(wires=0)

    np.testing.assert_allclose(circuit(), photon_state(bit, basis), atol=ATOL)


def test_basis_aliases_and_coercion() -> None:
    assert Basis.Z is Basis.RECTILINEAR
    assert Basis.X is Basis.DIAGONAL
    assert Basis.coerce("diagonal") is Basis.X
    assert Basis.coerce(" Rectilinear ") is Basis.Z
    assert Basis.coerce("hadamard") is Basis.X


@pytest.mark.parametrize("bit", [2, -1, 1.0, "1", None])
def test_prepare_photon_rejects_invalid_bits(bit: object) -> None:
    with pytest.raises(ValueError):
        prepare_photon(bit, "Z", wires=0)


def test_prepare_photon_rejects_unknown_basis() -> None:
    with pytest.raises(ValueError, match="unknown basis"):
        prepare_photon(0, "circular", wires=0)


def test_prepare_photon_rejects_multiple_wires() -> None:
    with pytest.raises(ValueError, match="exactly one wire"):
        prepare_photon(0, "Z", wires=[0, 1])


# --------------------------------------------------------------------------- #
# 2. Measurement
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("bit", "basis"), [(0, "Z"), (1, "Z"), (0, "X"), (1, "X")])
def test_measurement_in_preparation_basis_is_deterministic(
    bit: int, basis: str
) -> None:
    probabilities = single_photon_run(bit, basis, basis)

    expected = np.array([1.0, 0.0]) if bit == 0 else np.array([0.0, 1.0])
    np.testing.assert_allclose(probabilities, expected, atol=ATOL)


@pytest.mark.parametrize(("bit", "basis"), [(0, "Z"), (1, "Z"), (0, "X"), (1, "X")])
def test_measurement_in_conjugate_basis_is_uniform(bit: int, basis: str) -> None:
    conjugate = "X" if basis == "Z" else "Z"

    np.testing.assert_allclose(
        single_photon_run(bit, basis, conjugate), [0.5, 0.5], atol=ATOL
    )


# --------------------------------------------------------------------------- #
# 3. Channel noise and eavesdropping
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("basis", ["Z", "X"])
@pytest.mark.parametrize("probability", [0.0, 0.1, 0.35])
def test_thermal_noise_causes_bit_errors_in_either_basis(
    basis: str, probability: float
) -> None:
    probabilities = single_photon_run(0, basis, basis, noise_probability=probability)

    np.testing.assert_allclose(
        probabilities, [1.0 - probability, probability], atol=ATOL
    )


def test_channel_is_identity_when_probabilities_vanish() -> None:
    np.testing.assert_allclose(
        single_photon_run(1, "X", "X", noise_probability=0.0, phase_flip_probability=0.0),
        [0.0, 1.0],
        atol=ATOL,
    )


@pytest.mark.parametrize(("basis", "eve_basis"), [("Z", "X"), ("X", "Z")])
def test_eve_who_guesses_the_wrong_basis_randomises_the_bit(
    basis: str, eve_basis: str
) -> None:
    probabilities = single_photon_run(0, basis, basis, eavesdropped=True, eve_basis=eve_basis)

    np.testing.assert_allclose(probabilities, [0.5, 0.5], atol=ATOL)


@pytest.mark.parametrize(("basis", "eve_basis"), [("Z", "Z"), ("X", "X")])
def test_eve_who_guesses_the_right_basis_does_not_disturb_the_bit(
    basis: str, eve_basis: str
) -> None:
    probabilities = single_photon_run(1, basis, basis, eavesdropped=True, eve_basis=eve_basis)

    np.testing.assert_allclose(probabilities, [0.0, 1.0], atol=ATOL)


@pytest.mark.parametrize("basis", ["Z", "X"])
def test_random_basis_intercept_resend_gives_25_percent_qber(basis: str) -> None:
    """Average over Eve's two equally likely basis choices."""
    error_rates = [
        single_photon_run(0, basis, basis, eavesdropped=True, eve_basis=eve_basis)[1]
        for eve_basis in ("Z", "X")
    ]

    assert np.mean(error_rates) == pytest.approx(0.25, abs=ATOL)


@pytest.mark.parametrize("noise_probability", [0.0, 0.1, 0.2])
@pytest.mark.parametrize("basis", ["Z", "X"])
def test_eve_and_noise_combine_to_quarter_plus_half_p(
    basis: str, noise_probability: float
) -> None:
    error_rates = [
        single_photon_run(
            0,
            basis,
            basis,
            noise_probability=noise_probability,
            eavesdropped=True,
            eve_basis=eve_basis,
        )[1]
        for eve_basis in ("Z", "X")
    ]

    expected = 0.25 + 0.5 * noise_probability
    assert np.mean(error_rates) == pytest.approx(expected, abs=ATOL)


def test_eve_basis_requires_eavesdropping() -> None:
    with pytest.raises(ValueError, match="eavesdropped=True"):
        apply_quantum_channel(0, 0.1, eavesdropped=False, eve_basis="X")


def test_eve_basis_count_must_match_wires() -> None:
    device = qml.device("default.mixed", wires=2)

    @qml.qnode(device)
    def circuit() -> "qml.measurements.ProbabilityMP":
        apply_quantum_channel([0, 1], 0.0, eavesdropped=True, eve_basis=["X"])
        return qml.probs(wires=[0, 1])

    with pytest.raises(ValueError, match="2 bases"):
        circuit()


@pytest.mark.parametrize("probability", [-0.1, 1.1, float("nan")])
def test_channel_rejects_invalid_probabilities(probability: float) -> None:
    with pytest.raises(ValueError, match="must be in"):
        apply_quantum_channel(0, probability)


def test_phase_flip_probability_can_be_isolated() -> None:
    """Pure bit-flip noise must not disturb a diagonal-basis measurement."""
    np.testing.assert_allclose(
        single_photon_run(
            0, "X", "X", noise_probability=0.4, phase_flip_probability=0.0
        ),
        [1.0, 0.0],
        atol=ATOL,
    )


def test_wire_strings_are_rejected() -> None:
    with pytest.raises(TypeError, match="not a string"):
        apply_quantum_channel("0", 0.1)


# --------------------------------------------------------------------------- #
# 4. One photon, end to end
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("bit", "basis", "measurement_basis", "expected"),
    [
        (0, "Z", "Z", (1.0, 0.0)),
        (1, "Z", "Z", (0.0, 1.0)),
        (0, "X", "X", (1.0, 0.0)),
        (1, "X", "X", (0.0, 1.0)),
        (0, "Z", "X", (0.5, 0.5)),
        (1, "Z", "X", (0.5, 0.5)),
    ],
)
def test_photon_outcome_probabilities_describe_an_ideal_link(
    bit: int, basis: str, measurement_basis: str, expected: tuple[float, float]
) -> None:
    probabilities = photon_outcome_probabilities(bit, basis, measurement_basis, 0.0)

    np.testing.assert_allclose(probabilities, expected, atol=ATOL)


def test_photon_outcome_probabilities_include_channel_noise() -> None:
    np.testing.assert_allclose(
        photon_outcome_probabilities(0, "Z", "Z", 0.25), (0.75, 0.25), atol=ATOL
    )


def test_photon_outcome_probabilities_include_eve() -> None:
    np.testing.assert_allclose(
        photon_outcome_probabilities(
            0, "Z", "Z", 0.0, eavesdropped=True, eve_basis="X"
        ),
        (0.5, 0.5),
        atol=ATOL,
    )


def test_transmit_photon_is_reproducible_for_a_seed() -> None:
    first = transmit_photon(1, "Z", "Z", 0.1, rng=np.random.default_rng(5))
    second = transmit_photon(1, "Z", "Z", 0.1, rng=np.random.default_rng(5))

    assert first == second
    assert first.bit_sent == 1
    assert first.basis is Basis.Z
    assert first.outcome_probabilities == pytest.approx((0.1, 0.9), abs=ATOL)


def test_transmit_photon_rejects_invalid_probabilities() -> None:
    with pytest.raises(ValueError, match="must be in"):
        transmit_photon(0, "Z", "Z", 1.5)


# --------------------------------------------------------------------------- #
# 5. 3-qubit bit-flip code
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("bit", "index"), [(0, 0), (1, 7)])
def test_encoder_produces_logical_codewords(bit: int, index: int) -> None:
    device = qml.device("default.qubit", wires=3)

    @qml.qnode(device)
    def circuit() -> "qml.measurements.StateMP":
        encode_bit_flip(bit, wires=[0, 1, 2])
        return qml.state()

    state = circuit()
    assert int(np.argmax(np.abs(state))) == index
    assert state[index] == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("received", "position", "logical"),
    [
        ((0, 0, 0), None, 0),
        ((1, 1, 1), None, 1),
        ((1, 0, 0), 0, 0),
        ((0, 1, 0), 1, 0),
        ((0, 0, 1), 2, 0),
        ((0, 1, 1), 0, 1),
        ((1, 0, 1), 1, 1),
        ((1, 1, 0), 2, 1),
    ],
)
def test_syndrome_decoder_corrects_every_single_error(
    received: tuple[int, int, int], position: int | None, logical: int
) -> None:
    correction = decode_bit_flip(received)

    assert correction.error_position == position
    assert correction.logical_bit == logical
    assert correction.corrected == tuple(logical for _ in range(3))


def test_decoder_reports_uncorrectable_double_error() -> None:
    correction = decode_bit_flip((1, 1, 0))

    assert correction.error_position == 2
    assert correction.logical_bit == 1
    assert correction.corrected != (1, 1, 0)


def test_decoder_rejects_wrong_block_size() -> None:
    with pytest.raises(ValueError, match="exactly 3 bits"):
        decode_bit_flip((0, 1))


def test_encoder_requires_three_wires() -> None:
    with pytest.raises(ValueError, match="exactly 3 wires"):
        encode_bit_flip(0, wires=(0, 1))


def test_physical_bit_flip_is_corrected_by_the_decoder() -> None:
    device = qml.device("default.mixed", wires=[0, 1, 2])

    @qml.qnode(device)
    def circuit() -> "qml.measurements.ProbabilityMP":
        encode_bit_flip(0, wires=[0, 1, 2])
        qml.PauliX(wires=1)
        return qml.probs(wires=[0, 1, 2])

    probabilities = np.asarray(circuit(), dtype=float)
    received = index_to_bits(int(np.argmax(probabilities)), 3)
    correction = decode_bit_flip(received)

    assert received == (0, 1, 0)
    assert correction.error_position == 1
    assert correction.logical_bit == 0


@pytest.mark.parametrize("probability", [0.0, 0.01, 0.05, 0.1, 0.25])
def test_recovery_probability_matches_three_p_squared_minus_two_p_cubed(
    probability: float,
) -> None:
    expected = 1.0 - (3.0 * probability**2 - 2.0 * probability**3)

    assert block_recovery(0, probability) == pytest.approx(expected, abs=ATOL)


def test_code_is_neutral_at_the_fifty_percent_crossover() -> None:
    assert block_recovery(0, 0.5) == pytest.approx(0.5, abs=ATOL)


def test_code_cannot_remove_eve_information() -> None:
    """Averaged over Eve's eight basis patterns, the code still fails 15.625%."""
    average = np.mean([block_recovery(0, 0.0, eve_basis) for eve_basis in EVE_PATTERNS])

    assert average == pytest.approx(1.0 - 0.15625, abs=ATOL)


@pytest.mark.parametrize("noise_probability", [0.0, 0.1])
def test_code_performance_under_eve_plus_noise(noise_probability: float) -> None:
    per_photon_error = 0.25 + 0.5 * noise_probability
    expected = 1.0 - (3.0 * per_photon_error**2 - 2.0 * per_photon_error**3)
    average = np.mean(
        [
            block_recovery(0, noise_probability, eve_basis)
            for eve_basis in EVE_PATTERNS
        ]
    )

    assert average == pytest.approx(expected, abs=ATOL)


def test_one_eavesdropped_photon_is_still_corrected() -> None:
    """Two clean qubits outvote one fully randomised qubit."""
    assert block_recovery(0, 0.0, eve_basis=("Z", "X", "Z")) == pytest.approx(1.0, abs=ATOL)


def test_transmit_protected_bit_is_deterministic_for_a_seed() -> None:
    first = transmit_protected_bit(0, 0.1, rng=np.random.default_rng(2026))
    second = transmit_protected_bit(0, 0.1, rng=np.random.default_rng(2026))

    assert first == second
    assert first.recovery_probability == pytest.approx(0.972, abs=ATOL)
    assert first.success == (first.bit_recovered == first.bit_sent)


def test_transmit_protected_bit_reports_sampled_outcome() -> None:
    result = transmit_protected_bit(1, 0.02, rng=np.random.default_rng(11))
    repeated = decode_bit_flip(result.bits_received)

    assert result.bit_sent == 1
    assert len(result.bits_received) == 3
    assert result.syndrome == repeated.syndrome
    assert result.error_position == repeated.error_position
    assert result.bit_recovered == repeated.logical_bit
    assert result.success is (result.bit_recovered == result.bit_sent)
