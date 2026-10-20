"""BB84 photon circuits: preparation, fibre channel, measurement, bit-flip QEC.

Everything in this module is a PennyLane *quantum function*: it appends
operations to the active tape, so it must be called inside a ``qml.QNode``.
A complete single-photon run looks like::

    dev = qml.device("default.mixed", wires=1)

    @qml.qnode(dev)
    def link(bit, basis, p, eve):
        prepare_photon(bit, basis, wires=0)
        apply_quantum_channel(wires=0, noise_probability=p, eavesdropped=eve)
        return measure_photon(basis, wires=0)

``default.mixed`` is required whenever a channel is applied. A pure-state
device such as ``default.qubit`` is only valid when both noise probabilities
and eavesdropping are zero.

Eve's attack is modelled as **complete dephasing in her randomly chosen
basis**. That is the exact unconditional description of "measure, then forward
the outcome": Bob never learns Eve's result, so his statistics are those of the
channel averaged over her outcomes. It is equivalent to a mid-circuit
measurement for every quantity this simulator reports, while staying analytic
and free of mid-circuit-measurement device constraints.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pennylane as qml

from .states import Basis, coerce_bit

__all__ = [
    "Basis",
    "BitFlipCorrection",
    "PhotonTransmission",
    "ProtectedTransmission",
    "apply_quantum_channel",
    "build_photon_circuit",
    "build_protected_bit_circuit",
    "decode_bit_flip",
    "encode_bit_flip",
    "index_to_bits",
    "measure_photon",
    "photon_outcome_probabilities",
    "prepare_photon",
    "transmit_photon",
    "transmit_protected_bit",
]

#: Probability of a Z error that makes a single qubit completely dephased.
_EVE_DEPHASING_PROBABILITY = 0.5

#: Syndrome -> index of the flipped qubit inside the 3-qubit block.
_SYNDROME_TO_POSITION: dict[tuple[int, int], int | None] = {
    (0, 0): None,
    (1, 0): 0,
    (1, 1): 1,
    (0, 1): 2,
}


# --------------------------------------------------------------------------- #
# Validation helpers
# --------------------------------------------------------------------------- #


def _as_wire_list(wires: "int | Sequence[int]") -> list[int]:
    """Normalise a wire argument to a non-empty list of unique labels."""
    if isinstance(wires, (str, bytes)):
        raise TypeError("wires must be an int or a sequence of ints, not a string")
    if isinstance(wires, (int, np.integer)) and not isinstance(wires, bool):
        labels = [int(wires)]
    else:
        try:
            labels = [int(wire) for wire in wires]
        except TypeError:
            raise TypeError("wires must be an int or a sequence of ints") from None
    if not labels:
        raise ValueError("at least one wire is required")
    if len(set(labels)) != len(labels):
        raise ValueError(f"wires must be unique, got {labels}")
    if any(label < 0 for label in labels):
        raise ValueError(f"wires must be non-negative, got {labels}")
    return labels


def _as_single_wire(wires: "int | Sequence[int]") -> int:
    """A single photon occupies exactly one wire."""
    labels = _as_wire_list(wires)
    if len(labels) != 1:
        raise ValueError(f"a photon occupies exactly one wire, got {labels}")
    return labels[0]


def _probability(name: str, value: float) -> float:
    """Validate a probability, rejecting NaN and out-of-range values."""
    probability = float(value)
    if not 0.0 <= probability <= 1.0:
        raise ValueError(f"{name} must be in [0, 1], got {value!r}")
    return probability


def index_to_bits(index: int, width: int) -> tuple[int, ...]:
    """Convert a PennyLane basis-state index to bits, wire 0 first.

    PennyLane treats wire 0 as the most significant bit (verified on 0.45.1),
    so index 1 on three wires is the state ``|001>``.
    """
    return tuple((index >> (width - 1 - position)) & 1 for position in range(width))


# --------------------------------------------------------------------------- #
# Alice, the channel, and Bob
# --------------------------------------------------------------------------- #


def prepare_photon(
    bit: "int | bool", basis: "Basis | str", wires: "int | Sequence[int]" = 0
) -> None:
    """Apply Alice's state preparation for ``bit`` in ``basis``.

    Assumes the wire starts in ``|0>`` (the QNode default). Rectilinear maps
    0/1 to ``|0>``/``|1>``; diagonal maps them to ``|+>``/``|->`` by applying
    a Hadamard after the computational preparation.

    This function returns ``None`` by design: like every PennyLane quantum
    function it mutates the active tape, which is what makes it composable
    inside a QNode.
    """
    wire = _as_single_wire(wires)
    bit = coerce_bit(bit)
    basis = Basis.coerce(basis)

    if bit:
        qml.PauliX(wires=wire)
    if basis is Basis.DIAGONAL:
        qml.Hadamard(wires=wire)


def apply_quantum_channel(
    wires: "int | Sequence[int]",
    noise_probability: float,
    eavesdropped: bool = False,
    *,
    phase_flip_probability: float | None = None,
    eve_basis: "Basis | str | Sequence[Basis | str] | None" = None,
    rng: np.random.Generator | None = None,
) -> None:
    """Transmit photons through a noisy, possibly eavesdropped fibre.

    Parameters
    ----------
    wires:
        One wire per physical photon. Noise is applied independently per wire,
        which models three photons suffering independent thermal errors.
    noise_probability:
        Bit-flip (``X``) probability per photon. This is the ``p`` that enters
        the repetition-code analysis ``3p^2 - 2p^3``.
    eavesdropped:
        Insert Eve's intercept-resend tap before the thermal noise.
    phase_flip_probability:
        Phase-flip (``Z``) probability per photon. Defaults to
        ``noise_probability`` so a single ``p`` describes both error types; set
        it to ``0.0`` to isolate pure bit-flip noise.
    eve_basis:
        Fix Eve's basis (one value or one per wire) instead of sampling it
        uniformly. Intended for deterministic tests.
    rng:
        Generator used for Eve's basis choices. Pass a seeded generator for
        reproducible runs; when omitted, fresh entropy is used.

    Notes
    -----
    Stage order is Eve first, then thermal noise, matching
    ``ARCHITECTURE.md`` §4.2. Eve's measurement is represented by complete
    dephasing in her chosen basis (``H`` - ``PhaseFlip(1/2)`` - ``H`` for the
    diagonal basis), which is the exact channel seen by a Bob who does not
    learn her outcome.
    """
    wire_list = _as_wire_list(wires)
    bit_flip = _probability("noise_probability", noise_probability)
    phase_flip = (
        bit_flip
        if phase_flip_probability is None
        else _probability("phase_flip_probability", phase_flip_probability)
    )

    if eavesdropped:
        eve_bases = _resolve_eve_bases(len(wire_list), eve_basis, rng)
    elif eve_basis is not None:
        raise ValueError("eve_basis is only meaningful when eavesdropped=True")
    else:
        eve_bases = ()

    for position, wire in enumerate(wire_list):
        if eavesdropped:
            diagonal = eve_bases[position] is Basis.DIAGONAL
            if diagonal:
                qml.Hadamard(wires=wire)
            qml.PhaseFlip(_EVE_DEPHASING_PROBABILITY, wires=wire)
            if diagonal:
                qml.Hadamard(wires=wire)

        if bit_flip > 0.0:
            qml.BitFlip(bit_flip, wires=wire)
        if phase_flip > 0.0:
            qml.PhaseFlip(phase_flip, wires=wire)


def measure_photon(
    basis: "Basis | str", wires: "int | Sequence[int]" = 0
) -> "qml.measurements.ProbabilityMP":
    """Apply Bob's basis rotation and return the projective measurement.

    Bob's random basis choice belongs to the caller (``Basis`` selection is a
    classical decision); this function implements the measurement itself.
    The returned probabilities are ordered ``[P(0), P(1)]`` in ``basis``.

    Returns
    -------
    qml.measurements.ProbabilityMP
        A ``qml.probs`` measurement process, to be returned from the QNode.
    """
    wire = _as_single_wire(wires)
    basis = Basis.coerce(basis)

    if basis is Basis.DIAGONAL:
        qml.Hadamard(wires=wire)
    return qml.probs(wires=wire)


def _resolve_eve_bases(
    count: int,
    eve_basis: "Basis | str | Sequence[Basis | str] | None",
    rng: np.random.Generator | None,
) -> tuple[Basis, ...]:
    """Return one Eve basis per wire, sampling uniformly when unconstrained."""
    if eve_basis is None:
        generator = rng if rng is not None else np.random.default_rng()
        draws = generator.integers(0, 2, size=count)
        return tuple(
            Basis.DIAGONAL if draw else Basis.RECTILINEAR for draw in draws
        )
    if isinstance(eve_basis, (Basis, str)):
        return (Basis.coerce(eve_basis),) * count

    bases = tuple(Basis.coerce(value) for value in eve_basis)
    if len(bases) != count:
        raise ValueError(
            f"eve_basis must provide {count} bases, got {len(bases)}"
        )
    return bases


# --------------------------------------------------------------------------- #
# One photon, end to end
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PhotonTransmission:
    """One photon's trip through the link, as recorded by Bob's detector."""

    bit_sent: int
    basis: Basis
    measurement_basis: Basis
    outcome_probabilities: tuple[float, float]
    bit_measured: int


def build_photon_circuit(
    bit: "int | bool",
    basis: "Basis | str",
    measurement_basis: "Basis | str",
    noise_probability: float,
    *,
    eavesdropped: bool = False,
    phase_flip_probability: float | None = None,
    eve_basis: "Basis | str | None" = None,
    wire: "int | Sequence[int]" = 0,
    device: "qml.devices.Device | None" = None,
) -> "qml.QNode":
    """Build the complete prepare -> channel -> measure QNode for one photon.

    The returned QNode yields ``[P(0), P(1)]`` in ``measurement_basis``, so a
    caller obtains Bob's exact outcome distribution without repeating the gate
    sequence. ``eve_basis`` is fixed for the whole circuit; when it is omitted
    while ``eavesdropped`` is true, a fresh basis is drawn on every execution.
    That is statistically correct but not reproducible, so pass ``eve_basis``
    when a run must be repeatable.
    """
    wire = _as_single_wire(wire)
    logical_bit = coerce_bit(bit)
    prepared = Basis.coerce(basis)
    measured = Basis.coerce(measurement_basis)

    if device is None:
        device = qml.device("default.mixed", wires=wire)

    @qml.qnode(device)
    def circuit() -> "qml.measurements.ProbabilityMP":
        prepare_photon(logical_bit, prepared, wires=wire)
        apply_quantum_channel(
            wire,
            noise_probability,
            eavesdropped=eavesdropped,
            phase_flip_probability=phase_flip_probability,
            eve_basis=eve_basis,
        )
        return measure_photon(measured, wires=wire)

    return circuit


def photon_outcome_probabilities(
    bit: "int | bool",
    basis: "Basis | str",
    measurement_basis: "Basis | str",
    noise_probability: float,
    *,
    eavesdropped: bool = False,
    phase_flip_probability: float | None = None,
    eve_basis: "Basis | str | None" = None,
    wire: "int | Sequence[int]" = 0,
    device: "qml.devices.Device | None" = None,
) -> tuple[float, float]:
    """Return the exact ``[P(0), P(1)]`` for one photon after the channel.

    This is the cacheable unit behind a Monte Carlo run. Photons are
    independent and the channel is memoryless, so the distribution depends
    only on the discrete choice of bit, preparation basis, measurement basis
    and (when she is present) Eve's basis. One circuit per distinct tuple
    therefore reproduces the statistics of one circuit per photon, which is
    what keeps a 512-photon session fast without hard-coding the physics.
    """
    circuit = build_photon_circuit(
        bit,
        basis,
        measurement_basis,
        noise_probability,
        eavesdropped=eavesdropped,
        phase_flip_probability=phase_flip_probability,
        eve_basis=eve_basis,
        wire=wire,
        device=device,
    )
    probabilities = np.asarray(circuit(), dtype=float)
    total = float(probabilities.sum())
    if not np.isfinite(total) or total <= 0.0:
        raise RuntimeError("the photon circuit returned an invalid probability distribution")
    return (float(probabilities[0] / total), float(probabilities[1] / total))


def transmit_photon(
    bit: "int | bool",
    basis: "Basis | str",
    measurement_basis: "Basis | str",
    noise_probability: float,
    *,
    eavesdropped: bool = False,
    phase_flip_probability: float | None = None,
    eve_basis: "Basis | str | None" = None,
    wire: "int | Sequence[int]" = 0,
    device: "qml.devices.Device | None" = None,
    rng: np.random.Generator | None = None,
) -> PhotonTransmission:
    """Prepare, transmit and measure one photon; draw a single outcome.

    One outcome is sampled from the exact distribution with ``rng`` (fresh
    entropy when omitted), so a seeded generator reproduces a run bit for bit.
    Use :func:`photon_outcome_probabilities` instead when many photons share
    the same discrete parameters and one evaluation should serve them all.
    """
    generator = rng if rng is not None else np.random.default_rng()
    probabilities = photon_outcome_probabilities(
        bit,
        basis,
        measurement_basis,
        noise_probability,
        eavesdropped=eavesdropped,
        phase_flip_probability=phase_flip_probability,
        eve_basis=eve_basis,
        wire=wire,
        device=device,
    )

    return PhotonTransmission(
        bit_sent=coerce_bit(bit),
        basis=Basis.coerce(basis),
        measurement_basis=Basis.coerce(measurement_basis),
        outcome_probabilities=probabilities,
        bit_measured=int(generator.choice(2, p=probabilities)),
    )


# --------------------------------------------------------------------------- #
# 3-qubit bit-flip code
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class BitFlipCorrection:
    """Result of decoding one 3-qubit block."""

    received: tuple[int, int, int]
    syndrome: tuple[int, int]
    error_position: int | None
    corrected: tuple[int, int, int]
    logical_bit: int


@dataclass(frozen=True, slots=True)
class ProtectedTransmission:
    """One sampled logical-bit transmission through the protected channel.

    ``recovery_probability`` is exact, not a sampled estimate: it is the
    probability mass of all 3-qubit outcomes that decode back to the sent bit.
    """

    bit_sent: int
    bits_received: tuple[int, int, int]
    syndrome: tuple[int, int]
    error_position: int | None
    bit_recovered: int
    success: bool
    recovery_probability: float


def encode_bit_flip(
    bit: "int | bool", wires: "Sequence[int]" = (0, 1, 2)
) -> None:
    """Prepare the logical state ``|b_L> = |bbb>`` on three physical qubits.

    The data wire is prepared first, then fanned out with two CNOTs — the
    textbook encoder of ``ARCHITECTURE.md`` §8.1. All three wires must start in
    ``|0>`` except the data wire, which this function prepares.

    Only the rectilinear basis is supported. Encoding a diagonal-basis state
    with this circuit produces ``|+++>``/``|--->``, which protects against
    ``Z`` errors rather than against the bit flips of the thermal channel; use
    a phase-flip or Shor code for that case (§8.5).
    """
    wire_list = _as_wire_list(wires)
    if len(wire_list) != 3:
        raise ValueError(f"the bit-flip code needs exactly 3 wires, got {wire_list}")

    prepare_photon(coerce_bit(bit), Basis.RECTILINEAR, wires=wire_list[0])
    qml.CNOT(wires=[wire_list[0], wire_list[1]])
    qml.CNOT(wires=[wire_list[0], wire_list[2]])


def decode_bit_flip(bits: "Sequence[int]") -> BitFlipCorrection:
    """Syndrome-decode one measured 3-qubit block.

    Syndrome convention (``ARCHITECTURE.md`` §8.2): ``S1 = Z0 Z1`` and
    ``S2 = Z1 Z2``, reported as parity bits where ``1`` means the ``-1``
    eigenvalue (odd parity). ``error_position`` is the index inside the block,
    not an absolute wire label.
    """
    received = tuple(coerce_bit(bit) for bit in bits)
    if len(received) != 3:
        raise ValueError(f"the bit-flip code needs exactly 3 bits, got {len(received)}")

    first, second, third = received
    syndrome = (first ^ second, second ^ third)
    error_position = _SYNDROME_TO_POSITION[syndrome]

    corrected = list(received)
    if error_position is not None:
        corrected[error_position] ^= 1

    return BitFlipCorrection(
        received=received,
        syndrome=syndrome,
        error_position=error_position,
        corrected=tuple(corrected),
        logical_bit=corrected[0],
    )


def build_protected_bit_circuit(
    bit: "int | bool",
    noise_probability: float,
    *,
    eavesdropped: bool = False,
    phase_flip_probability: float | None = None,
    eve_basis: "Basis | str | Sequence[Basis | str] | None" = None,
    wires: "Sequence[int]" = (0, 1, 2),
    device: "qml.devices.Device | None" = None,
    rng: np.random.Generator | None = None,
) -> "qml.QNode":
    """Build the encode -> channel -> measure QNode for one logical bit.

    A ``default.mixed`` device is created when ``device`` is omitted; a device
    passed by the caller must support channels. The returned QNode produces the
    8 outcome probabilities of the three physical qubits, in ``qml.probs``
    ordering with wire 0 as the most significant bit. Only the rectilinear
    bit-flip code is implemented (see :func:`encode_bit_flip`).

    Eve's bases are drawn afresh on every execution of the QNode; pass
    ``eve_basis`` to freeze a single attack pattern.
    """
    wire_list = _as_wire_list(wires)
    if len(wire_list) != 3:
        raise ValueError(f"the bit-flip code needs exactly 3 wires, got {wire_list}")
    logical_bit = coerce_bit(bit)

    if device is None:
        device = qml.device("default.mixed", wires=wire_list)

    @qml.qnode(device)
    def circuit() -> "qml.measurements.ProbabilityMP":
        encode_bit_flip(logical_bit, wires=wire_list)
        apply_quantum_channel(
            wire_list,
            noise_probability,
            eavesdropped=eavesdropped,
            phase_flip_probability=phase_flip_probability,
            eve_basis=eve_basis,
            rng=rng,
        )
        return qml.probs(wires=wire_list)

    return circuit


def transmit_protected_bit(
    bit: "int | bool",
    noise_probability: float,
    *,
    eavesdropped: bool = False,
    phase_flip_probability: float | None = None,
    eve_basis: "Basis | str | Sequence[Basis | str] | None" = None,
    wires: "Sequence[int]" = (0, 1, 2),
    device: "qml.devices.Device | None" = None,
    rng: np.random.Generator | None = None,
) -> ProtectedTransmission:
    """Encode, transmit and decode one logical bit; return a sampled result.

    One outcome is drawn from the exact 3-qubit distribution with ``rng`` (a
    fresh generator when omitted). ``recovery_probability`` is computed from
    the full distribution of this run, so a single call reports the code's
    performance for the channel realisation it actually used. When Eve's basis
    is left random, that value is conditional on the three basis choices drawn
    for the run: fix ``eve_basis`` or average over runs for the ensemble value.
    """
    logical_bit = coerce_bit(bit)
    generator = rng if rng is not None else np.random.default_rng()

    circuit = build_protected_bit_circuit(
        logical_bit,
        noise_probability,
        eavesdropped=eavesdropped,
        phase_flip_probability=phase_flip_probability,
        eve_basis=eve_basis,
        wires=wires,
        device=device,
        rng=generator,
    )
    probabilities = np.asarray(circuit(), dtype=float)
    total = probabilities.sum()
    if not np.isfinite(total) or total <= 0.0:
        raise RuntimeError("the circuit returned an invalid probability distribution")
    probabilities = probabilities / total

    index = int(generator.choice(probabilities.size, p=probabilities))
    bits = index_to_bits(index, 3)
    correction = decode_bit_flip(bits)

    recovery = sum(
        probability
        for outcome, probability in enumerate(probabilities)
        if decode_bit_flip(index_to_bits(outcome, 3)).logical_bit == logical_bit
    )

    return ProtectedTransmission(
        bit_sent=logical_bit,
        bits_received=correction.received,
        syndrome=correction.syndrome,
        error_position=correction.error_position,
        bit_recovered=correction.logical_bit,
        success=correction.logical_bit == logical_bit,
        recovery_probability=float(recovery),
    )
