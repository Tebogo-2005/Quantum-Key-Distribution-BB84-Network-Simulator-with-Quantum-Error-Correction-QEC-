"""End-to-end BB84 session: Alice's random key, the quantum link, sifting, QBER.

This is the orchestration layer. It decides *who talks to whom and in which
order*, and contains no quantum-mechanical arithmetic: every outcome
distribution comes from :mod:`src.quantum_core.bb84`, and PennyLane is never
imported here (``ARCHITECTURE.md`` §2.2).

One call to :func:`run_simulation` executes the prepare-and-measure loop:

1. Alice draws ``n_bits`` random key bits and ``n_bits`` random bases.
2. Each photon is prepared in Alice's basis, sent through the noisy (and
   optionally eavesdropped) link, and measured by Bob in *his* independently
   drawn random basis.
3. Both parties broadcast their bases on an open classical channel. Positions
   whose bases disagree are discarded — this is **sifting**.
4. A random subset of the sifted bits is revealed and compared. The revealed
   bits are sacrificed; what remains is the shared secret key, which never
   crosses the channel.

Why bits are sacrificed
-----------------------
A revealed bit is public, so it can no longer be part of the key. QBER
estimation therefore destroys ``sample_fraction`` of the sifted key. The
sample is drawn uniformly at random and kept disjoint from the key, an
invariant asserted by the tests. The estimate is a point estimate;
:class:`QberReport` also carries the one-sided Hoeffding bound, because a
512-bit run is far too small to certify a link (``ARCHITECTURE.md`` §7.2).

Security verdict
----------------
Below the ~11% Shor-Preskill threshold the observed error rate is compatible
with the configured noise. Above it, the errors cannot be explained by
plausible physical noise and match an intercept-resend attack, whose signature
is 25%, so the run logs ``"Eve is eavesdropping!"`` at CRITICAL level and sets
:attr:`SimulationResult.security_alarm`. This is a warning, not an exception:
"no key" is a legitimate, plottable protocol outcome (``ARCHITECTURE.md`` §12).
"""

from __future__ import annotations

import argparse
import logging
import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

import numpy as np

from ..quantum_core.bb84 import photon_outcome_probabilities
from ..quantum_core.states import Basis, coerce_bit

__all__ = [
    "DEFAULT_CONFIDENCE_DELTA",
    "DEFAULT_KEY_LENGTH",
    "DEFAULT_SAMPLE_FRACTION",
    "SECURITY_THRESHOLD",
    "SECURITY_WARNING",
    "PublicChannel",
    "PublicMessage",
    "QberReport",
    "SampleReveal",
    "SimulationResult",
    "check_qber",
    "run_simulation",
    "select_qber_sample",
]

#: Alice's default key array length, in bits (one photon per bit before QEC).
DEFAULT_KEY_LENGTH = 512

#: Fraction of the sifted key sacrificed to estimate the QBER.
DEFAULT_SAMPLE_FRACTION = 0.2

#: Shor-Preskill one-way threshold: above it no secret key can be extracted.
SECURITY_THRESHOLD = 0.11

#: One-sided confidence level used for the finite-sample QBER bound.
DEFAULT_CONFIDENCE_DELTA = 1e-3

#: The exact sentence emitted when the sample fails the test.
SECURITY_WARNING = "Eve is eavesdropping!"

SECURITY_LOGGER = logging.getLogger("bb84.security")

_MESSAGE_KINDS = ("basis_announce", "sample_reveal")


# --------------------------------------------------------------------------- #
# Classical channel
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PublicMessage:
    """One broadcast on the open classical channel, visible to everyone.

    ``payload`` is ``tuple[Basis, ...]`` for ``"basis_announce"`` and a
    :class:`SampleReveal` for ``"sample_reveal"``.
    """

    sender: str
    kind: str
    payload: object


@dataclass(frozen=True, slots=True)
class SampleReveal:
    """Alice's public disclosure of the sacrificed positions and their bits."""

    positions: tuple[int, ...]
    bits: tuple[int, ...]

    def as_mapping(self) -> dict[int, int]:
        """Return ``{position: bit}`` for convenient lookups."""
        return dict(zip(self.positions, self.bits))


class PublicChannel:
    """Open, unauthenticated broadcast medium shared by Alice, Bob and Eve.

    Models the classical lane of BB84: every message reaches all parties and
    the order is fixed by the protocol. Authentication is out of scope
    (``ARCHITECTURE.md`` §13), so the channel records the transcript but does
    not tag or verify messages. Keeping the transcript here is what lets tests
    assert that key bits are never published.
    """

    def __init__(self) -> None:
        self._messages: list[PublicMessage] = []

    def broadcast(self, sender: str, kind: str, payload: object = None) -> PublicMessage:
        """Publish one message, append it to the transcript and return it."""
        if kind not in _MESSAGE_KINDS:
            raise ValueError(f"unknown message kind {kind!r}; expected one of {_MESSAGE_KINDS}")
        message = PublicMessage(sender=sender, kind=kind, payload=payload)
        self._messages.append(message)
        return message

    @property
    def transcript(self) -> tuple[PublicMessage, ...]:
        """Every message published so far, in protocol order."""
        return tuple(self._messages)

    def by_kind(self, kind: str) -> tuple[PublicMessage, ...]:
        """Return the messages of one kind, in publication order."""
        return tuple(message for message in self._messages if message.kind == kind)

    def __len__(self) -> int:
        return len(self._messages)

    def __iter__(self) -> Iterator[PublicMessage]:
        return iter(self._messages)


# --------------------------------------------------------------------------- #
# QBER estimation
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class QberReport:
    """Outcome of the QBER test on the revealed sample.

    ``qber`` is ``None`` only when there was nothing to sample (no sifted
    bits), which happens for degenerate inputs such as ``n_bits=1`` with a
    basis mismatch. ``warning`` carries :data:`SECURITY_WARNING` when the
    alarm fires and is otherwise ``None``.
    """

    positions: tuple[int, ...]
    errors: int
    qber: float | None
    threshold: float
    delta: float
    alarm: bool
    warning: str | None

    @property
    def sample_size(self) -> int:
        """Number of bits sacrificed for the estimate."""
        return len(self.positions)

    @property
    def upper_bound(self) -> float | None:
        """One-sided Hoeffding bound on the true QBER, or ``None`` if empty.

        ``e <= qber + sqrt(ln(1/delta) / (2 n))`` holds with probability at
        least ``1 - delta`` (``ARCHITECTURE.md`` §7.2). For a 512-bit run the
        penalty is tens of percent: the bound proves that the sample is too
        small to certify the link, not that the link is safe.
        """
        if self.qber is None:
            return None
        penalty = math.sqrt(math.log(1.0 / self.delta) / (2.0 * self.sample_size))
        return min(1.0, self.qber + penalty)

    @property
    def is_conclusive(self) -> bool:
        """True when the bound itself stays below the alarm threshold."""
        bound = self.upper_bound
        return bound is not None and bound <= self.threshold

    def describe(self) -> str:
        """One-line human-readable verdict."""
        if self.qber is None:
            return "QBER: no sifted bits, nothing to sample"
        verdict = self.warning if self.alarm else "below threshold"
        return (
            f"QBER: {self.errors}/{self.sample_size} = {100.0 * self.qber:.2f}% "
            f"(alarm above {100.0 * self.threshold:.2f}%) -> {verdict}"
        )


def select_qber_sample(
    positions: Sequence[int], size: int, *, rng: np.random.Generator
) -> tuple[int, ...]:
    """Draw ``size`` distinct positions uniformly at random, sorted ascending.

    Sampling is uniform over the sifted key and independent of the bit values,
    so the estimate is unbiased; sorting only makes the transcript readable.
    """
    pool = _indices("positions", positions)
    if len(set(pool)) != len(pool):
        raise ValueError("positions must be unique")
    if isinstance(size, bool) or not isinstance(size, (int, np.integer)):
        raise TypeError(f"size must be an int, got {size!r}")
    size = int(size)
    if not 0 <= size <= len(pool):
        raise ValueError(f"size must be in [0, {len(pool)}], got {size}")
    if size == 0:
        return ()
    chosen = rng.choice(len(pool), size=size, replace=False)
    return tuple(sorted(pool[int(index)] for index in chosen))


def check_qber(
    alice_bits: Sequence[int],
    bob_bits: Sequence[int],
    positions: Sequence[int] | None = None,
    *,
    threshold: float = SECURITY_THRESHOLD,
    delta: float = DEFAULT_CONFIDENCE_DELTA,
) -> QberReport:
    """Compare Alice's and Bob's bits on ``positions`` and judge the channel.

    ``positions`` are indices into the two bit sequences. ``None`` compares
    every supplied position, which is convenient for ad-hoc diagnostics; a
    protocol run always passes the sacrificed sample instead, because revealed
    bits leave the key.

    When the measured QBER exceeds ``threshold`` the function logs
    ``"Eve is eavesdropping!"`` at CRITICAL level and returns a report with
    ``alarm=True``. The comparison is strict: exactly 11% does not alarm,
    matching the zero-crossing of the secret-key rate at the threshold.
    """
    alice = tuple(coerce_bit(bit) for bit in alice_bits)
    bob = tuple(coerce_bit(bit) for bit in bob_bits)
    if len(alice) != len(bob):
        raise ValueError(
            f"alice_bits and bob_bits must have equal length, got {len(alice)} and {len(bob)}"
        )
    if not 0.0 < threshold < 1.0:
        raise ValueError(f"threshold must be in (0, 1), got {threshold!r}")
    if not 0.0 < delta < 1.0:
        raise ValueError(f"delta must be in (0, 1), got {delta!r}")

    if positions is None:
        selected = tuple(range(len(alice)))
    else:
        selected = _indices("positions", positions)
        if len(set(selected)) != len(selected):
            raise ValueError("positions must be unique")
        for position in selected:
            if not 0 <= position < len(alice):
                raise ValueError(
                    f"position {position} is outside [0, {len(alice)})"
                )

    errors = sum(alice[position] != bob[position] for position in selected)
    qber = None if not selected else errors / len(selected)
    alarm = qber is not None and qber > threshold
    warning = SECURITY_WARNING if alarm else None

    if alarm:
        SECURITY_LOGGER.critical(
            "%s %d of %d revealed bits disagree (QBER %.2f%% > %.2f%% threshold).",
            SECURITY_WARNING,
            errors,
            len(selected),
            100.0 * qber,
            100.0 * threshold,
        )

    return QberReport(
        positions=selected,
        errors=errors,
        qber=qber,
        threshold=threshold,
        delta=delta,
        alarm=alarm,
        warning=warning,
    )


# --------------------------------------------------------------------------- #
# The session
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SimulationResult:
    """Everything one BB84 run produced, including the public transcript.

    ``alice_key`` and ``bob_key`` are what the two parties hold after sifting
    and after removing the sacrificed sample. They are reported here because
    this is a simulator, not a key store; only ``transcript`` models what an
    eavesdropper could learn.
    """

    n_bits: int
    alice_bits: tuple[int, ...]
    alice_bases: tuple[Basis, ...]
    bob_bases: tuple[Basis, ...]
    bob_bits: tuple[int, ...]
    sifted_positions: tuple[int, ...]
    sample_positions: tuple[int, ...]
    key_positions: tuple[int, ...]
    alice_key: tuple[int, ...]
    bob_key: tuple[int, ...]
    qber: QberReport
    transcript: tuple[PublicMessage, ...]
    noise_probability: float
    eavesdropped: bool
    seed: int | None

    @property
    def sifted_length(self) -> int:
        """Bits kept after the basis comparison."""
        return len(self.sifted_positions)

    @property
    def key_length(self) -> int:
        """Bits left after the QBER sample is discarded."""
        return len(self.alice_key)

    @property
    def sift_rate(self) -> float:
        """Fraction of emitted photons that survived sifting."""
        return self.sifted_length / self.n_bits

    @property
    def key_errors(self) -> int:
        """Disagreements inside the retained key (before reconciliation)."""
        return sum(alice != bob for alice, bob in zip(self.alice_key, self.bob_key))

    @property
    def shared_key(self) -> tuple[int, ...] | None:
        """The key both parties hold, or ``None`` when their copies differ.

        A mismatch means the block needs information reconciliation before it
        can be used; under intercept-resend the copies differ by roughly the
        QBER. Returning ``None`` rather than Alice's copy keeps a caller from
        silently using a key Bob does not have.
        """
        return self.alice_key if self.key_errors == 0 else None

    @property
    def security_alarm(self) -> bool:
        """True when the QBER sample exceeded the threshold."""
        return self.qber.alarm

    @property
    def warning(self) -> str | None:
        """The critical warning text, or ``None`` when the link passed."""
        return self.qber.warning

    def summary(self) -> str:
        """Multi-line report suitable for a CLI or a notebook cell."""
        lines = [
            f"BB84 session: {self.n_bits} photons, "
            f"noise p={self.noise_probability:g}, "
            f"Eve={'on' if self.eavesdropped else 'off'}, seed={self.seed}",
            f"  sifted    {self.sifted_length:5d} bits ({100.0 * self.sift_rate:5.1f}% basis match)",
            f"  revealed  {self.qber.sample_size:5d} bits sacrificed for QBER",
            f"  key       {self.key_length:5d} bits, {self.key_errors} disagreements",
            f"  {self.qber.describe()}",
        ]
        if self.qber.qber is not None and not self.qber.is_conclusive:
            lines.append(
                f"  note: one-sided bound {100.0 * self.qber.upper_bound:.1f}% exceeds the "
                f"{100.0 * self.qber.threshold:.1f}% threshold; this run detects attacks, "
                "it does not certify security"
            )
        if self.security_alarm:
            lines.append(f"  SECURITY ALARM: {self.warning}")
        return "\n".join(lines)


class _PhotonLink:
    """Memoised single-photon link used by :func:`run_simulation`.

    Photons are independent and the channel is memoryless, so the outcome
    distribution depends only on ``(bit, Alice basis, Bob basis, Eve basis)``:
    at most sixteen tuples. The link solves one exact density-matrix circuit
    per tuple and samples the individual bits from NumPy, which reproduces the
    distribution of one circuit per photon at a fraction of the cost
    (``ARCHITECTURE.md`` ADR 3, with the probabilities computed by the circuit
    rather than hard-coded).

    Eve's basis, when she is present, is drawn per photon from the same seeded
    generator that drives Bob's bases and the QBER sample, so one seed
    reproduces her choices and Bob's clicks together.
    """

    def __init__(
        self,
        noise_probability: float,
        *,
        eavesdropped: bool,
        phase_flip_probability: float | None,
        rng: np.random.Generator,
        device: object | None = None,
    ) -> None:
        self._noise_probability = noise_probability
        self._eavesdropped = eavesdropped
        self._phase_flip_probability = phase_flip_probability
        self._rng = rng
        self._device = device
        self._cache: dict[tuple[int, Basis, Basis, Basis | None], tuple[float, float]] = {}

    def measure(self, bit: int, basis: Basis, measurement_basis: Basis) -> int:
        """Return Bob's outcome for one photon."""
        eve_basis = self._draw_eve_basis()
        cache_key = (bit, basis, measurement_basis, eve_basis)
        probabilities = self._cache.get(cache_key)
        if probabilities is None:
            probabilities = photon_outcome_probabilities(
                bit,
                basis,
                measurement_basis,
                self._noise_probability,
                eavesdropped=self._eavesdropped,
                phase_flip_probability=self._phase_flip_probability,
                eve_basis=eve_basis,
                device=self._device,
            )
            self._cache[cache_key] = probabilities
        return int(self._rng.choice(2, p=probabilities))

    def _draw_eve_basis(self) -> Basis | None:
        if not self._eavesdropped:
            return None
        return Basis.DIAGONAL if int(self._rng.integers(0, 2)) else Basis.RECTILINEAR

    @property
    def distinct_circuits(self) -> int:
        """How many exact distributions this link had to solve."""
        return len(self._cache)


def run_simulation(
    n_bits: int = DEFAULT_KEY_LENGTH,
    *,
    noise_probability: float = 0.0,
    eavesdropped: bool = False,
    phase_flip_probability: float | None = None,
    sample_fraction: float = DEFAULT_SAMPLE_FRACTION,
    sample_size: int | None = None,
    threshold: float = SECURITY_THRESHOLD,
    delta: float = DEFAULT_CONFIDENCE_DELTA,
    seed: int | None = None,
    device: object | None = None,
) -> SimulationResult:
    """Run one complete BB84 session and return every intermediate value.

    Parameters
    ----------
    n_bits:
        Length of Alice's random key array, and the number of photons sent.
    noise_probability:
        Per-photon bit-flip probability of the fibre. Phase flips default to
        the same value inside the channel, so the sifted QBER of a passive
        link is approximately ``noise_probability`` in either basis.
    eavesdropped:
        Insert Eve's intercept-resend tap (random basis per photon) before the
        thermal noise. On an otherwise perfect link this drives the QBER to
        approximately 25%.
    phase_flip_probability:
        Override the phase-flip probability; ``None`` uses
        ``noise_probability``.
    sample_fraction:
        Fraction of the sifted key revealed for the QBER estimate. Ignored
        when ``sample_size`` is given.
    sample_size:
        Absolute number of bits to reveal. Clamped to the sifted length.
    threshold:
        Alarm threshold for the QBER, 0.11 by default.
    delta:
        Confidence level for the one-sided finite-sample bound.
    seed:
        Seed for the single generator that drives Alice, Bob, Eve and the
        sample. ``None`` uses fresh entropy.
    device:
        Optional PennyLane device with one wire, forwarded to the photon
        circuits. A ``default.mixed`` device is created when omitted.

    Returns
    -------
    SimulationResult
        Includes both parties' keys, the sifted/sample/key index sets, the
        QBER report and the public transcript.

    Notes
    -----
    The generator is consumed in protocol order — Alice's bits, Alice's
    bases, Bob's bases, Eve's bases, then the QBER sample — so identical
    seeds produce identical results and a seed cannot be used to make a
    failing link pass.
    """
    n_bits = _positive_int("n_bits", n_bits)
    noise_probability = _probability("noise_probability", noise_probability)
    if phase_flip_probability is not None:
        phase_flip_probability = _probability(
            "phase_flip_probability", phase_flip_probability
        )
    if not 0.0 < sample_fraction <= 1.0:
        raise ValueError(f"sample_fraction must be in (0, 1], got {sample_fraction!r}")
    if sample_size is not None:
        sample_size = _positive_int("sample_size", sample_size)
    if not 0.0 < threshold < 1.0:
        raise ValueError(f"threshold must be in (0, 1), got {threshold!r}")

    rng = np.random.default_rng(seed)

    alice_bits = _random_bits(rng, n_bits)
    alice_bases = _random_bases(rng, n_bits)
    bob_bases = _random_bases(rng, n_bits)

    link = _PhotonLink(
        noise_probability,
        eavesdropped=eavesdropped,
        phase_flip_probability=phase_flip_probability,
        rng=rng,
        device=device,
    )
    bob_bits = tuple(
        link.measure(bit, a_basis, b_basis)
        for bit, a_basis, b_basis in zip(alice_bits, alice_bases, bob_bases)
    )

    channel = PublicChannel()
    channel.broadcast("alice", "basis_announce", alice_bases)
    channel.broadcast("bob", "basis_announce", bob_bases)

    sifted_positions = tuple(
        index
        for index, (a_basis, b_basis) in enumerate(zip(alice_bases, bob_bases))
        if a_basis == b_basis
    )
    revealed = _sample_count(len(sifted_positions), sample_fraction, sample_size)
    sample_positions = select_qber_sample(sifted_positions, revealed, rng=rng)
    channel.broadcast(
        "alice",
        "sample_reveal",
        SampleReveal(
            positions=sample_positions,
            bits=tuple(alice_bits[position] for position in sample_positions),
        ),
    )

    qber = check_qber(
        alice_bits,
        bob_bits,
        sample_positions,
        threshold=threshold,
        delta=delta,
    )

    sacrificed = set(sample_positions)
    key_positions = tuple(
        position for position in sifted_positions if position not in sacrificed
    )

    return SimulationResult(
        n_bits=n_bits,
        alice_bits=alice_bits,
        alice_bases=alice_bases,
        bob_bases=bob_bases,
        bob_bits=bob_bits,
        sifted_positions=sifted_positions,
        sample_positions=sample_positions,
        key_positions=key_positions,
        alice_key=tuple(alice_bits[position] for position in key_positions),
        bob_key=tuple(bob_bits[position] for position in key_positions),
        qber=qber,
        transcript=channel.transcript,
        noise_probability=noise_probability,
        eavesdropped=eavesdropped,
        seed=seed,
    )


# --------------------------------------------------------------------------- #
# Validation and small helpers
# --------------------------------------------------------------------------- #


def _positive_int(name: str, value: object) -> int:
    """Return ``value`` as an int >= 1, rejecting bools and floats."""
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise TypeError(f"{name} must be an int, got {value!r}")
    number = int(value)
    if number < 1:
        raise ValueError(f"{name} must be >= 1, got {number}")
    return number


def _probability(name: str, value: object) -> float:
    """Return ``value`` as a probability in [0, 1], rejecting NaN."""
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise ValueError(f"{name} must be in [0, 1], got {value!r}")
    return number


def _indices(name: str, values: Sequence[int]) -> tuple[int, ...]:
    """Return integer indices, rejecting bools and floats.

    ``int(1.7)`` would silently truncate to ``1`` and compare the wrong bit,
    so non-integers are an error rather than a coercion.
    """
    indices = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
            raise TypeError(f"{name} must contain integers, got {value!r}")
        indices.append(int(value))
    return tuple(indices)


def _sample_count(n_sifted: int, fraction: float, size: int | None) -> int:
    """Resolve the number of bits to reveal, never exceeding the key."""
    if n_sifted == 0:
        return 0
    if size is not None:
        return min(size, n_sifted)
    return max(1, min(int(round(fraction * n_sifted)), n_sifted))


def _random_bits(rng: np.random.Generator, count: int) -> tuple[int, ...]:
    """Alice's raw key: ``count`` fair coin flips."""
    return tuple(int(bit) for bit in rng.integers(0, 2, size=count))


def _random_bases(rng: np.random.Generator, count: int) -> tuple[Basis, ...]:
    """``count`` fair basis choices; Alice and Bob each draw their own."""
    draws = rng.integers(0, 2, size=count)
    return tuple(Basis.DIAGONAL if draw else Basis.RECTILINEAR for draw in draws)


# --------------------------------------------------------------------------- #
# Command line
# --------------------------------------------------------------------------- #


def main(argv: Sequence[str] | None = None) -> int:
    """Run one session from the command line; exit code 1 means alarm fired."""
    parser = argparse.ArgumentParser(
        prog="python -m src.network.simulation",
        description="Run one BB84 session and report sifting, QBER and the verdict.",
    )
    parser.add_argument(
        "-n",
        "--bits",
        type=int,
        default=DEFAULT_KEY_LENGTH,
        help=f"bits Alice prepares (default: {DEFAULT_KEY_LENGTH})",
    )
    parser.add_argument(
        "-p",
        "--noise",
        type=float,
        default=0.0,
        help="per-photon noise probability (default: 0.0)",
    )
    parser.add_argument(
        "--eve",
        action="store_true",
        help="enable Eve's intercept-resend attack",
    )
    parser.add_argument("--seed", type=int, default=None, help="seed for a reproducible run")
    arguments = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    result = run_simulation(
        arguments.bits,
        noise_probability=arguments.noise,
        eavesdropped=arguments.eve,
        seed=arguments.seed,
    )
    print(result.summary())
    return 1 if result.security_alarm else 0


if __name__ == "__main__":
    raise SystemExit(main())
