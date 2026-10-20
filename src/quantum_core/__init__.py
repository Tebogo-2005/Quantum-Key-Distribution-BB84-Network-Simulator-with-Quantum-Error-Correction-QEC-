"""Quantum core: state preparation, channels, measurement and error correction.

This layer owns the physics and is the only place allowed to import PennyLane.
It is network-agnostic: nothing here knows about Alice, Bob or Eve.

Modules
-------
``states``
    NumPy-only ``Basis`` enum, single-photon density matrices and bit coercion.
    Safe to import from the network layer without pulling in PennyLane.
``bb84``
    PennyLane quantum functions: Alice's preparation, the noisy and optionally
    eavesdropped channel, Bob's projective measurement, and the 3-qubit
    bit-flip encoder/decoder. Eve is modelled as dephasing in her randomly
    chosen basis, which is exact for every statistic Bob can observe.
    ``build_photon_circuit``, ``photon_outcome_probabilities`` and
    ``transmit_photon`` wrap one photon end to end, which is the entry point
    the network layer uses for unprotected BB84 runs.

Planned modules
---------------
``noise``
    Kraus/Pauli channel models: depolarizing, amplitude damping (fiber loss)
    and detector dark counts, once the network layer needs configurable noise.
``ecc``
    General ``BitFlipCode`` / ``PhaseFlipCode`` classes plus the 9-qubit Shor
    extension; the 3-qubit implementation currently lives in ``bb84``.
``metrics``
    State fidelity, trace distance, Helstrom guessing probability, QBER.

Allowed dependencies: PennyLane, NumPy. Never ``src.network``.
"""

__all__: list[str] = []
