"""Single-photon state definitions shared by the circuits and the network layer.

This module is deliberately NumPy-only: importing it must never pull in
PennyLane, so ``src.network`` can use :class:`Basis` and :func:`photon_state`
without taking a dependency on the quantum SDK.

Conventions (see ``ARCHITECTURE.md`` §6.1):

======  ====================  =====================
 bit    rectilinear (Z)       diagonal (X)
======  ====================  =====================
 0      ``|0>``               ``|+>`` = (|0>+|1>)/√2
 1      ``|1>``               ``|->`` = (|0>-|1>)/√2
======  ====================  =====================
"""

from __future__ import annotations

from enum import StrEnum

import numpy as np

__all__ = ["Basis", "coerce_bit", "photon_state"]

_BASIS_LABELS = {
    "z": "Z",
    "rectilinear": "Z",
    "computational": "Z",
    "x": "X",
    "diagonal": "X",
    "hadamard": "X",
}

_ZERO = np.array([[1.0, 0.0], [0.0, 0.0]], dtype=complex)
_ONE = np.array([[0.0, 0.0], [0.0, 1.0]], dtype=complex)
_PLUS = np.array([[0.5, 0.5], [0.5, 0.5]], dtype=complex)
_MINUS = np.array([[0.5, -0.5], [-0.5, 0.5]], dtype=complex)


class Basis(StrEnum):
    """Photon polarization basis.

    ``RECTILINEAR`` (Z) encodes 0/1 as ``|0>``/``|1>``; ``DIAGONAL`` (X)
    encodes them as ``|+>``/``|->``. ``Z``/``X`` are aliases for call sites
    that prefer the physics shorthand.
    """

    RECTILINEAR = "Z"
    DIAGONAL = "X"
    Z = "Z"
    X = "X"

    @classmethod
    def coerce(cls, value: "Basis | str") -> "Basis":
        """Normalise ``"rectilinear"``, ``"z"``, ``"diagonal"``, ... to a member."""
        if isinstance(value, cls):
            return value
        key = str(value).strip().lower()
        try:
            return cls(_BASIS_LABELS[key])
        except KeyError:
            accepted = ", ".join(sorted(_BASIS_LABELS))
            raise ValueError(
                f"unknown basis {value!r}; expected one of: {accepted}"
            ) from None


def coerce_bit(value: "int | bool | np.integer") -> int:
    """Return ``0`` or ``1``, rejecting everything else.

    Booleans are accepted because ``True``/``False`` are natural stand-ins for
    a bit; floats such as ``1.0`` are rejected to avoid silent truncation.
    """
    if isinstance(value, (bool, np.bool_)):
        return int(value)
    if isinstance(value, (int, np.integer)) and not isinstance(value, bool):
        if int(value) in (0, 1):
            return int(value)
    raise ValueError(f"bit must be 0 or 1, got {value!r}")


def photon_state(bit: "int | bool", basis: "Basis | str") -> np.ndarray:
    """Return the 2x2 density matrix Alice prepares for ``bit`` in ``basis``.

    Analytic reference used by the tests to cross-check the gate-level
    preparation in :func:`src.quantum_core.bb84.prepare_photon`.
    """
    bit = coerce_bit(bit)
    basis = Basis.coerce(basis)
    if basis is Basis.RECTILINEAR:
        return (_ZERO if bit == 0 else _ONE).copy()
    return (_PLUS if bit == 0 else _MINUS).copy()
