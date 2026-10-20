# BB84 Quantum Key Distribution Simulator with Quantum Error Correction

A from-scratch simulation of the **BB84 prepare-and-measure quantum key
distribution protocol**: Alice encodes key bits in photon polarization, Bob
measures in independently chosen bases, Eve intercepts and resends, and the
two parties sift, measure the quantum bit error rate (QBER) and decide whether
the link can be trusted. A **3-qubit bit-flip code** demonstrates what quantum
error correction can — and cannot — do about physical noise.

Built with PennyLane density-matrix circuits and a strict layer boundary
between physics and protocol orchestration.

| | |
| --- | --- |
| Status | Phases 1–3 delivered: architecture, BB84 physics, 3-qubit QEC, 512-bit session loop |
| Runtime | CPython 3.11+ (validated on 3.12) |
| Quantum SDK | PennyLane 0.45.1 (`default.qubit`, `default.mixed`) |
| Numerical stack | NumPy 2.3.5 (PennyLane 0.45 requires NumPy >= 2.0) |
| Tests | 143 deterministic tests, no shots, ~2 s |

> **Security notice.** This is a teaching and research simulator. There is no
> key management, no message authentication, and no finite-key security proof.
> Any key it prints is a simulation artifact and **must never protect real
> data**.

---

## Contents

- [Quick start](#quick-start)
- [Command line](#command-line)
- [What the simulator does](#what-the-simulator-does)
- [Verified physics](#verified-physics)
- [Quantum error correction](#quantum-error-correction)
- [Repository layout](#repository-layout)
- [Public API](#public-api)
- [How sifting and the QBER test work](#how-sifting-and-the-qber-test-work)
- [Determinism](#determinism)
- [Testing](#testing)
- [Limitations](#limitations)
- [Roadmap](#roadmap)
- [References](#references)

---

## Quick start

```bash
git clone https://github.com/Tebogo-2005/Quantum-Key-Distribution-BB84-Network-Simulator-with-Quantum-Error-Correction-QEC-.git
cd Quantum-Key-Distribution-BB84-Network-Simulator-with-Quantum-Error-Correction-QEC-

python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python -m src.network.simulation --seed 2026
```

No package install is needed: dependencies come from `requirements.txt`, and
imports resolve from the repository root (the `pyproject.toml` pytest
configuration adds it to the import path for the test run).

### Python

```python
from src.network.simulation import run_simulation

session = run_simulation(512, seed=2026)   # 512 bits, 512 bases, no Eve
print(session.summary())

session.qber.qber          # 0.0
len(session.shared_key)    # 204 bits, identical on both sides
```

Turn Eve on and the same call raises the alarm:

```python
attack = run_simulation(2048, eavesdropped=True, seed=2026)

attack.security_alarm      # True
attack.warning             # 'Eve is eavesdropping!'
attack.qber.qber           # 0.2709  (theory: 0.25)
attack.shared_key          # None — Alice's and Bob's copies differ
```

The warning is emitted at `CRITICAL` level on the `bb84.security` logger, and
is also returned on the result object so a host application that never
configures logging still sees the verdict.

---

## Command line

```bash
python -m src.network.simulation --seed 2026
python -m src.network.simulation --bits 2048 --eve --seed 2026
python -m src.network.simulation --bits 4096 --noise 0.02 --seed 41
```

| Flag | Meaning | Default |
| --- | --- | --- |
| `-n`, `--bits` | Key bits Alice prepares (one photon each) | 512 |
| `-p`, `--noise` | Per-photon noise probability | 0.0 |
| `--eve` | Enable intercept-resend | off |
| `--seed` | Seed for the single protocol RNG | fresh entropy |

Exit code `0` means the link passed the QBER test, `1` means the alarm fired.

A clean 512-bit run:

```
BB84 session: 512 photons, noise p=0, Eve=off, seed=2026
  sifted      255 bits ( 49.8% basis match)
  revealed     51 bits sacrificed for QBER
  key         204 bits, 0 disagreements
  QBER: 0/51 = 0.00% (alarm above 11.00%) -> below threshold
  note: one-sided bound 26.0% exceeds the 11.0% threshold; this run detects attacks, it does not certify security
```

An eavesdropped 2048-bit run:

```
CRITICAL bb84.security: Eve is eavesdropping! 55 of 203 revealed bits disagree (QBER 27.09% > 11.00% threshold).
BB84 session: 2048 photons, noise p=0, Eve=on, seed=2026
  sifted     1015 bits ( 49.6% basis match)
  revealed    203 bits sacrificed for QBER
  key         812 bits, 224 disagreements
  QBER: 55/203 = 27.09% (alarm above 11.00%) -> Eve is eavesdropping!
  note: one-sided bound 40.1% exceeds the 11.0% threshold; this run detects attacks, it does not certify security
  SECURITY ALARM: Eve is eavesdropping!
```

The `note:` line is deliberate. A 512-bit demo can catch an eavesdropper but
cannot certify a link: see [How sifting and the QBER test work](#how-sifting-and-the-qber-test-work).

---

## What the simulator does

```
 Alice                                Eve (optional)              Bob
 ─────                                ──────────────              ───
 random bit  b   ──┐
 random basis β  ──┴─► |b⟩_β  ──────► measure in β'  ──────► measure in β_bob
                                      re-prepare |b'⟩_β'          (random basis)
                                                                       │
      ◄──────────── open classical channel: announce β, β_bob ─────────┘
                                                                       │
 sift: keep positions with β == β_bob  ─────────────────────────────────┤
 reveal a random 20% of the sifted bits, compare, discard them ────────┤
 retain the rest as the key                                             ▼
```

1. **Prepare** — Alice draws 512 random bits and 512 random bases and applies
   `prepare_photon`, which maps `(bit, basis)` to `|0⟩`, `|1⟩`, `|+⟩` or `|−⟩`.
2. **Transmit** — the link applies Eve's intercept-resend tap (if enabled) and
   then bit-flip/phase-flip noise, all as density-matrix channels.
3. **Measure** — Bob draws his own random bases and measures projectively.
4. **Sift** — both sides publish their bases on an open channel; mismatched
   positions are discarded. On a lossless link this keeps ~50% of the bits.
5. **Estimate** — a random subset of the sifted key is revealed and compared.
   Those bits are destroyed; `QBER = errors / sample` is compared with the
   **11% Shor–Preskill threshold**.
6. **Verdict** — above the threshold the run sets `security_alarm` and logs
   `Eve is eavesdropping!`. Below it the run reports the key, together with a
   finite-sample bound on the true error rate.

---

## Verified physics

Every number below is asserted by the test suite against analytic results,
not just observed once. Where randomness is involved the tests average over
all equally likely Eve basis patterns or use a seeded run large enough that
the tolerance is several standard deviations wide.

| Scenario | Expected QBER | Where it is tested |
| --- | --- | --- |
| Ideal channel, matching bases | `0` | `test_measurement_in_preparation_basis_is_deterministic` |
| Channel bit-flip probability `p` | `p` in either basis | `test_thermal_noise_causes_bit_errors_in_either_basis` |
| Eve guesses Bob's basis | `0` | `test_eve_who_guesses_the_right_basis_does_not_disturb_the_bit` |
| Eve guesses the other basis | `0.5` | `test_eve_who_guesses_the_wrong_basis_randomises_the_bit` |
| Full intercept-resend, averaged | `0.25` | `test_random_basis_intercept_resend_gives_25_percent_qber` |
| Eve plus noise | `0.25 + p/2` | `test_eve_and_noise_combine_to_quarter_plus_half_p` |
| Session, passive link, `p = 0.2` | `0.2 +/- 0.06` | `test_noise_drives_the_measured_qber_towards_p` |
| Session, intercept-resend | `0.25 +/- 0.06` | `test_eve_qber_is_close_to_twenty_five_percent` |

A 100-seed sweep of 1024-bit sessions reproduces the analytic means:

```
noise p = 0.10              0.0975   (theory 0.10)
intercept-resend            0.2476   (theory 0.25)
intercept-resend + p = 0.10 0.3002   (theory 0.30)
```

Eve is modelled as **complete dephasing in her randomly chosen basis** rather
than a mid-circuit measurement. Since Bob never learns her outcome, dephasing
is exactly the measure-and-forward channel for every statistic he can observe
and it keeps the simulation analytic. See `ARCHITECTURE.md` §6.3.

---

## Quantum error correction

The 3-qubit repetition code protects a logical qubit against a single bit
flip:

```
|0⟩_L = |000⟩          |1⟩_L = |111⟩
stabilizers: Z1Z2, Z2Z3        distance: d = 3 (corrects t = 1)
```

Encoding is `prepare_photon` plus two CNOTs; decoding is a syndrome lookup on
Bob's own three outcomes, so no syndrome bit is ever published.

| Error | `Z1Z2` | `Z2Z3` | Correction |
| --- | --- | --- | --- |
| none | +1 | +1 | none |
| X1 | −1 | +1 | X1 |
| X2 | −1 | −1 | X2 |
| X3 | +1 | −1 | X3 |

```python
import numpy as np
from src.quantum_core.bb84 import transmit_protected_bit

result = transmit_protected_bit(0, 0.10, rng=np.random.default_rng(7))
result.bits_received          # e.g. (0, 0, 0)
result.recovery_probability   # 0.972 = 1 - (3p² - 2p³)
```

Residual logical error is `p_L = 3p² − 2p³`: 2.98e-4 at `p = 0.01`, 2.8% at
`p = 0.10`, and neutral at `p = 0.5`. The code cannot remove Eve's
information: under full intercept-resend the post-decoding logical error is
`15.6%`, which is why security is parameterized by the **pre-decoding**
physical QBER and never by the corrected rate (`ARCHITECTURE.md` §8.6).

The bit-flip code protects the `Z` basis only; a phase flip passes through it
as a logical `Z` error. The dual phase-flip code is documented but not yet
implemented (`ecc.py` on the roadmap). **No single 3-qubit code protects both
BB84 bases.**

---

## Repository layout

```
.
├── ARCHITECTURE.md            design contract: topology, interfaces, math, ADRs
├── requirements.txt           four pinned dependencies
├── pyproject.toml             pytest configuration (pythonpath, strict markers)
├── src
│   ├── network
│   │   └── simulation.py      session loop, sifting, QBER verdict, CLI
│   ├── quantum_core
│   │   ├── states.py          NumPy-only Basis enum and analytic states
│   │   └── bb84.py            preparation, channels, measurement, 3-qubit code
│   └── utils                  shared helpers (planned: config, rng, metrics, plotting)
└── tests
    ├── test_scaffolding.py    structural checks and pinned versions
    ├── test_bb84.py           86 physics tests
    └── test_simulation.py     session, sifting, QBER and determinism tests
```

Dependency rule: **arrows point downwards only**. `src/quantum_core` never
imports `src.network`; `src/network` never imports PennyLane. This keeps the
physics testable without a network and the protocol testable with a mock link.
`states.py` is deliberately NumPy-only so the network layer can use `Basis`
without pulling in the quantum SDK.

---

## Public API

### Quantum core — `src.quantum_core`

| Symbol | Purpose |
| --- | --- |
| `Basis`, `Basis.coerce`, `coerce_bit` | Normalise `"Z"`, `"rectilinear"`, `"X"`, `"diagonal"`, `0/1` |
| `photon_state(bit, basis)` | Analytic 2×2 density matrix, used as the test oracle |
| `prepare_photon(bit, basis, wires=0)` | Alice's state preparation (quantum function) |
| `apply_quantum_channel(wires, p, eavesdropped=False, ...)` | Eve tap plus bit/phase-flip noise |
| `measure_photon(basis, wires=0)` | Bob's basis rotation and projective measurement |
| `photon_outcome_probabilities(...)` | Exact `[P(0), P(1)]` for one photon |
| `transmit_photon(...)` | Prepare, transmit, measure; returns a sampled outcome |
| `encode_bit_flip`, `decode_bit_flip` | 3-qubit encoder and syndrome decoder |
| `build_protected_bit_circuit`, `transmit_protected_bit` | Full protected-channel QNode and sampler |

Quantum functions append gates to the active tape and must be called inside a
QNode. Use `default.mixed` whenever a channel is applied:

```python
import pennylane as qml
from src.quantum_core.bb84 import prepare_photon, apply_quantum_channel, measure_photon

device = qml.device("default.mixed", wires=1)

@qml.qnode(device)
def link(bit, basis, noise, eve):
    prepare_photon(bit, basis, wires=0)
    apply_quantum_channel(0, noise, eavesdropped=eve)
    return measure_photon(basis, wires=0)

print(link(1, "X", 0.0, False))          # [0. 1.]
print(link(0, "Z", 0.0, True))           # [0.5 0.5] — Eve guessed the wrong basis
```

### Network — `src.network.simulation`

| Symbol | Purpose |
| --- | --- |
| `run_simulation(...)` | One complete session; returns every intermediate value |
| `select_qber_sample(positions, size, rng=...)` | Uniform sample without replacement |
| `check_qber(alice_bits, bob_bits, positions, ...)` | QBER report and security verdict |
| `PublicChannel`, `PublicMessage`, `SampleReveal` | Open broadcast bus and its transcript |
| `QberReport` | Errors, QBER, alarm, warning, Hoeffding bound |
| `SimulationResult` | Keys, index sets, transcript, `shared_key`, `summary()` |

`SimulationResult.shared_key` returns `None` when Alice's and Bob's copies
differ, rather than handing back a key Bob does not have.

---

## How sifting and the QBER test work

With independent fair bases, `P(basis match) = 1/2`, so a 512-bit run keeps
about 256 bits. The simulator then sacrifices `sample_fraction` of them
(20% by default, 51 bits) to estimate the error rate:

```
QBER = (number of disagreements) / (sample size)
```

The revealed bits are removed from the key, and a test asserts that the sample
and the key are disjoint and together cover every sifted position.

**Detection, not certification.** At 51 samples, crossing 11% means six or
more errors. A full intercept-resend attack produces that with probability

```
1 - P(Bin(51, 1/4) <= 5) = 99.4%
```

so a 512-bit demo catches Eve almost every time. But a clean 51-bit sample
only bounds the true QBER by

```
qber + sqrt(ln(1/delta) / (2 n)) = 0 + sqrt(ln(1000)/102) = 26%   (delta = 1e-3)
```

which is above the threshold. `QberReport.upper_bound` reports this, and the
summary says so explicitly. Certification needs thousands of samples
(`ARCHITECTURE.md` §7.2).

The comparison is strict (`qber > threshold`), so a sample sitting exactly on
11% does not alarm — the secret-key rate is exactly zero there, but the
measurement itself is not evidence of an attack.

---

## Determinism

One seed drives a single `numpy.random.Generator` in protocol order: Alice's
bits, Alice's bases, Bob's bases, Eve's bases, then the QBER sample.

```python
run_simulation(256, eavesdropped=True, seed=77) == \
run_simulation(256, eavesdropped=True, seed=77)   # True, field by field
```

There are no module-level RNG calls and no use of the `random` module. Same
seed, same result — including which photons Eve attacks.

---

## Testing

```bash
pytest -q
# 143 passed in 1.88s
```

The total is 7 structural tests, 86 physics tests and 50 session tests.

- **Exact, not statistical, wherever possible.** State preparations are
  compared with analytic density matrices; the full syndrome table is checked
  row by row; QEC recovery is compared with `1 - 3p² + 2p³`.
- **No shots.** Circuits run analytically (`shots=None`), so the suite cannot
  flake.
- **Eve's randomness is enumerated**, not sampled: the 3-qubit tests average
  over all eight equally likely basis patterns, which is why the expected
  `15.625%` logical error is reproduced exactly.
- **Structural tests** assert the pinned versions, the architecture topics and
  the package layout.

Markers `physics` and `slow` are configured in `pyproject.toml` for future
integration sweeps.

---

## Limitations

- **No loss or detectors.** The link is unitary-plus-Pauli: no attenuation,
  transmittance, dark counts, dead time or afterpulsing. The `eta^3` cost of
  encoding three photons is documented but not simulated yet.
- **No authentication.** The classical channel is an unauthenticated
  broadcast; 128-bit MACs are described in the architecture but not
  implemented.
- **No reconciliation or privacy amplification.** Post-processing stops at the
  sifted key; the `shared_key` property is pre-reconciliation.
- **QEC is not wired into the session loop.** `transmit_protected_bit`
  simulates a protected logical bit, but `run_simulation` currently sends one
  photon per bit.
- **One code, one axis.** Only the bit-flip code exists, and it protects `Z`
  states. The phase-flip dual and the 9-qubit Shor code are roadmap items.
- **Single-photon source.** Photon-number-splitting and decoy states are out
  of scope.
- **Asymptotic security.** The 11% threshold and the Hoeffding bound are
  asymptotic or one-sided estimates, not a composable finite-key proof.
- **Not for production.** No key is exported for real use.

---

## Roadmap

| Phase | Deliverable | Status |
| --- | --- | --- |
| 1 | Scaffold, pinned dependencies, architecture contract | delivered |
| 2 | BB84 photon circuits and 3-qubit bit-flip code | delivered |
| 3 | 512-bit session loop, public channel, sifting, QBER alarm | delivered |
| 4 | Classical metrics, plots, run manifests, CLI polish | planned |
| 5 | Reconciliation, privacy amplification, decoy states, Shor-9, erasure decoding | planned |

---

## References

1. C. H. Bennett and G. Brassard, "Quantum Cryptography: Public Key
   Distribution and Coin Tossing", *Proc. IEEE ICCSSP*, 1984.
2. P. W. Shor and J. Preskill, "Simple Proof of Security of the BB84 Quantum
   Key Distribution Protocol", *Phys. Rev. Lett.* 85, 441 (2000).
3. M. A. Nielsen and I. L. Chuang, *Quantum Computation and Quantum
   Information*, Cambridge University Press, 2010 (ch. 10).
4. E. Knill and R. Laflamme, "Theory of Quantum Error-Correcting Codes",
   *Phys. Rev. A* 55, 900 (1997).
5. V. Scarani et al., "The Security of Practical Quantum Key Distribution",
   *Rev. Mod. Phys.* 81, 1301 (2009).
6. X. Ma, B. Qi, Y. Zhao, H.-K. Lo, "Practical Decoy State for Quantum Key
   Distribution", *Phys. Rev. A* 72, 012326 (2005).

For the full design — topology, frozen interfaces, message accounting, QBER
math, syndrome tables and ADRs — see [`ARCHITECTURE.md`](ARCHITECTURE.md).
