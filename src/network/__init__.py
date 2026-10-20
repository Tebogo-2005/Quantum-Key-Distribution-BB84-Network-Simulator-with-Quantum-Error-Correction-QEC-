"""Network layer: QKD nodes, transport channels and the BB84 session protocol.

This layer owns *who talks to whom* and *in which order*. It must not contain
quantum-mechanical derivations or import PennyLane; those live in
:mod:`src.quantum_core`.

Implemented modules
-------------------
``simulation``
    One automated BB84 run: Alice's random bits and bases, the memoised
    single-photon link, the open :class:`PublicChannel`, sifting, the
    sacrificed QBER sample and the 11% security verdict. Also the CLI
    (``python -m src.network.simulation``).

Planned modules
---------------
``node``
    ``Node`` abstract base class plus ``Alice`` (source, encoder, sifting) and
    ``Bob`` (measurement, decoding, sifting, QBER sampling).
``eavesdropper``
    ``Eve`` attack strategies: intercept-resend, basis guessing, partial tap.
``channels``
    ``QuantumChannel`` (loss, noise, optional Eve tap) and the authenticated
    ``ClassicalChannel`` used for public discussion.
``protocol``
    ``BB84Session`` finite state machine that drives a full run and emits a
    ``SessionResult``; ``simulation.run_simulation`` is the Phase 3 subset.
``messages``
    Immutable classical-channel message records (basis announcement, detection
    report, sample reveal, parity leakage, final-key confirmation).

Allowed dependencies: ``src.quantum_core``, ``src.utils``, NumPy.
"""

__all__: list[str] = []
