"""Cross-cutting utilities: configuration, randomness, metrics and reporting.

Planned modules
---------------
``config``
    Frozen dataclasses mirroring the TOML schema in ``ARCHITECTURE.md`` §9 and
    a ``load_config`` function built on the standard-library ``tomllib`` (no
    YAML dependency).
``rng``
    Deterministic ``numpy.random.Generator`` hierarchy derived from one master
    ``SeedSequence``; one independent stream per node and per purpose.
``metrics``
    QBER estimation with finite-sample bounds, binary entropy, secret-key rate,
    block-error statistics.
``plotting``
    Matplotlib figures: QBER vs. noise, QBER vs. distance, key rate vs. Eve
    interception fraction. Headless-safe (``Agg`` backend).
``errors``
    Error taxonomy: ``SimulationError``, ``ConfigurationError``,
    ``ChannelError``, ``ProtocolAbort`` (with a machine-readable reason code).
``logging``
    Structured run manifest (config hash, seed, versions) for reproducibility.

Allowed dependencies: NumPy, Matplotlib, standard library.
"""

__all__: list[str] = []
