"""BB84 quantum key distribution network simulator with quantum error correction.

Import root for the simulator. Modules are imported as ``src.<layer>.<module>``
and every entry point is run from the repository root, for example::

    python -m src.network.protocol --config configs/default.toml

The package contract (responsibilities, allowed dependencies and data types) is
defined in ``ARCHITECTURE.md``; this file only declares the distribution
metadata.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
