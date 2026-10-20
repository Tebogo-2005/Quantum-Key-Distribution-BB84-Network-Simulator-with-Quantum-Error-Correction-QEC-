# Architecture — BB84 QKD Network Simulator with Quantum Error Correction

| Field | Value |
| --- | --- |
| Status | Phases 1-3 delivered: scaffold, BB84 physics, 3-qubit code, 512-bit loop with sifting and QBER alarm |
| Runtime | CPython >= 3.11 (validated on 3.12) |
| Quantum SDK | PennyLane 0.45.1 (`default.qubit`, `default.mixed`) |
| Numerical stack | NumPy 2.3.5 (PennyLane 0.45 requires NumPy >= 2.0) |
| Simulation target | Discrete-variable, prepare-and-measure BB84 with a single-photon source |
| Security status | Educational simulator. Keys it produces must never protect real data. |

---

## 1. Purpose and non-goals

The simulator answers one engineering question end to end: **given a fiber link
with known loss and noise, and an adversary with a stated attack strategy, how
many provably private key bits can Alice and Bob agree on per second of
simulated time, and what does the measured QBER tell them?**

It models the full BB84 lifecycle — state preparation, transmission, Eve's
interception, measurement, basis sifting, QBER estimation, 3-qubit quantum
error correction, information reconciliation and privacy amplification — and
reports the observable quantities that a real deployment would monitor.

### Non-goals

- **Not a cryptographic library.** No key is exported for use outside the
  simulator; there is no AES/KDF integration in this repository.
- **Not a device-security model.** Detector blinding, Trojan-horse probing,
  timing side channels, dead time and afterpulsing are out of scope (§13
  records this as an explicit assumption).
- **Not a finite-key security proof.** Statistical bounds are implemented, but
  the composable-security claim of a real deployment requires a full
  finite-key analysis with epsilon parameters.
- **Not continuous-variable QKD.** Only discrete-variable polarization/fock
  encoding is modeled; CV-QKD is listed as an extension (§14).

### Threat model

| Component | Assumption |
| --- | --- |
| Eve's quantum capability | Arbitrary operations on the channel; may measure, re-prepare, or entangle with an ancilla; unlimited technology. |
| Eve's classical capability | Reads the entire public transcript, cannot forge or modify it (authenticated channel). |
| Eve's access | Only `src/network/channels.py` transport; no access to node memory, RNG state, or configuration. |
| Alice / Bob | Trusted devices, private RNG, correct protocol implementation. |
| Source | Ideal single-photon source (photon-number-splitting attacks are not modeled; decoy states are an extension). |

---

## 2. System topology

### 2.1 Physical layout

```
                          quantum lane: 3n single photons (n logical bits)
  ┌───────────────┐                        ┌────────────────────┐               ┌───────────────┐
  │     Alice     │  3n photons            │   Eve (optional)   │   surviving   │      Bob      │
  │               │ ─────────────────────► │                    │   photons     │               │
  │ RNG bits      │                        │ intercept-resend   │ ────────────► │ RNG basis     │
  │ RNG bases     │                        │ basis guess (1/2)  │               │ 3 detectors   │
  │ state prep    │                        │ re-prepare         │               │ QEC decode    │
  │ QEC encode    │                        │ fraction f         │               │ sifting       │
  └───────┬───────┘                        └─────────┬──────────┘               └───────┬───────┘
          │                                          │                                  │
          │        ═══════ authenticated classical channel (public) ═══════             │
          └──────────────────────────────────────────────────────────────────────────────┘
             basis announcement · detection report · sample reveal · parity leak · PA commit

  Physical channel (src/network/channels.py):  attenuation 10^(-alpha*L/10)
    -> Eve tap (quantum_core.noise.KrausChannel) -> Pauli noise -> Bob detector (dark counts)
```

The classical channel is drawn as a single bus because it is logically one
authenticated broadcast medium: every message is visible to Eve and to both
nodes, and the integrity tag makes tampering detectable (which aborts the
session rather than silently rerouting the key).

### 2.2 Layer stack

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Orchestration   src/network/simulation.py  run, sift, QBER, CLI               │
│                 src/network/protocol.py    (planned) session state machine    │
├──────────────────────────────────────────────────────────────────────────────┤
│ Network         src/network/node.py        (planned) Alice, Bob roles         │
│                 src/network/eavesdropper.py (planned) Eve attack strategies   │
│                 src/network/channels.py    (planned) Quantum/classical lanes  │
├──────────────────────────────────────────────────────────────────────────────┤
│ Quantum core    src/quantum_core/states.py Basis, states, density matrices    │
│                 src/quantum_core/bb84.py   prep, channel, measure, 3-qubit QEC│
│                 src/quantum_core/noise.py  (planned) generalized noise models │
│                 src/quantum_core/ecc.py    (planned) code classes, Shor-9     │
│                 src/quantum_core/metrics.py fidelity, Helstrom, QBER          │
├──────────────────────────────────────────────────────────────────────────────┤
│ Support         src/utils/{config,rng,metrics,plotting,errors,logging}.py     │
└──────────────────────────────────────────────────────────────────────────────┘
```

Dependency rule: **arrows point downwards only**. `src/quantum_core` never
imports `src.network`; `src/network` never imports PennyLane directly. This
keeps the physics testable without a network and the protocol testable with a
mock channel.

---

## 3. Module map

| Path | Responsibility | Key public symbols |
| --- | --- | --- |
| `src/network/simulation.py` | **Implemented.** Automated 512-bit loop, basis broadcast, sifting, sacrificed-sample QBER and the 11% verdict | `run_simulation`, `check_qber`, `select_qber_sample`, `PublicChannel`, `QberReport`, `SimulationResult` |
| `src/network/node.py` | Planned: role behaviour (sampling, encoding, measurement, decoding) | `Node`, `Alice`, `Bob` |
| `src/network/eavesdropper.py` | Planned: attack strategies on a single photon | `Eavesdropper`, `InterceptResend`, `PartialTap` |
| `src/network/channels.py` | Planned: transport (loss, noise, Eve insertion) and the authenticated transcript | `QuantumChannel`, `FiberChannel`, `ClassicalChannel` |
| `src/network/messages.py` | Planned: immutable public messages and their size accounting | `ClassicalMessage`, `MessageKind` |
| `src/network/protocol.py` | Planned: session state machine, abort conditions, key assembly | `BB84Session`, `SessionResult` |
| `src/quantum_core/states.py` | **Implemented.** NumPy-only basis enum, single-photon density matrices, bit coercion | `Basis`, `photon_state`, `coerce_bit` |
| `src/quantum_core/bb84.py` | **Implemented.** Preparation, noisy/eavesdropped channel, measurement, single-photon link, 3-qubit bit-flip code | `prepare_photon`, `apply_quantum_channel`, `measure_photon`, `photon_outcome_probabilities`, `build_protected_bit_circuit` |
| `src/quantum_core/noise.py` | Planned: depolarizing and amplitude-damping channels, dark counts | `apply_pauli`, `KrausChannel` |
| `src/quantum_core/ecc.py` | Planned: code classes, phase-flip and Shor-9 extensions | `BitFlipCode`, `PhaseFlipCode` |
| `src/quantum_core/metrics.py` | Fidelity, trace distance, Helstrom bound, entropy | `fidelity`, `binary_entropy` |
| `src/utils/config.py` | TOML loading and validation into frozen dataclasses | `SimulationConfig`, `load_config` |
| `src/utils/rng.py` | SeedSequence-derived generator hierarchy | `RngRegistry` |
| `src/utils/metrics.py` | QBER estimators, confidence bounds, key rate | `estimate_qber`, `secret_key_rate` |
| `src/utils/plotting.py` | Headless Matplotlib figures | `plot_qber_vs_noise`, `plot_key_rate_vs_distance` |
| `src/utils/errors.py` | Error taxonomy with reason codes | `ConfigurationError`, `ProtocolAbort` |
| `src/utils/logging.py` | Run manifest, structured JSONL events, key fingerprints | `RunManifest`, `EventLog` |

`src/utils/metrics.py` (classical statistics) and `src/quantum_core/metrics.py`
(quantum distances) are intentionally separate: they have different
dependencies and different test strategies.

---

## 4. Object model and interfaces

The following contracts are frozen for Phase 2. Implementations may add
private helpers but must not change these signatures without updating this
document and the tests that assert them. Phase 3 adds
``network/simulation.py`` alongside these contracts without changing them.

### 4.1 Value types

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping, Protocol

import numpy as np

Bit = int  # exactly 0 or 1


class Basis(StrEnum):
    Z = "Z"   # rectilinear: |0>, |1>
    X = "X"   # diagonal:    |+>, |->


class MessageKind(StrEnum):
    BASIS_ANNOUNCE = "basis_announce"        # 1 bit per block, per party
    DETECTION_REPORT = "detection_report"    # which of the 3 slots clicked
    SAMPLE_REVEAL = "sample_reveal"          # sacrificial QBER sample
    PARITY_LEAK = "parity_leak"              # EC syndrome / parity bits
    PA_COMMIT = "pa_commit"                  # hash family id + salt


@dataclass(frozen=True, slots=True)
class Photon:
    """One physical qubit in flight. Mixed states are first-class."""
    block_id: int          # index of the logical bit, 0..n-1
    slot: int              # 0..2 inside the 3-qubit block
    basis: Basis           # preparation basis (private until sifting)
    bit: Bit               # logical bit Alice encoded (private)
    rho: np.ndarray        # 2x2 density matrix in the computational basis
    lost: bool = False


@dataclass(frozen=True, slots=True)
class ClassicalMessage:
    sender: str
    kind: MessageKind
    payload: Mapping[str, object]
    auth_tag: bytes        # 128-bit tag; verification failure aborts the run
```

`rho` is a density matrix rather than a state vector because every photon that
passes Eve or a noisy channel is in a mixed state. Ideal preparations produce
pure matrices whose rank-1 structure is asserted in unit tests.

### 4.2 Channels

```python
class QuantumChannel(Protocol):
    def transmit(self, photon: Photon, rng: np.random.Generator) -> Photon:
        """Apply transmittance, the optional Eve tap and noise, in that order."""


class ClassicalChannel(Protocol):
    """Public, authenticated, eavesdroppable broadcast."""

    def publish(self, message: ClassicalMessage) -> None: ...
    def transcript(self) -> tuple[ClassicalMessage, ...]: ...
    def verify(self, message: ClassicalMessage) -> bool: ...
```

Stage order inside `FiberChannel.transmit` is fixed and documented because it
changes Eve's information:

1. **Attenuation** — photon survives with probability `eta = 10^(-alpha*L/10) * eta_det`.
   A lost photon is returned with `lost=True` and is never intercepted.
2. **Eve** — `Eavesdropper.intercept(photon, rng)`; may return a different state.
3. **Pauli noise** — bit flip, phase flip, depolarizing, applied to `rho`.
4. **Detection** — Bob's detector; dark counts are generated at measurement
   time in `Bob.measure`, not in the channel, because they are a detector
   property rather than a transport property.

### 4.3 Nodes and adversary

```python
class Eavesdropper(Protocol):
    def intercept(self, photon: Photon, rng: np.random.Generator) -> Photon: ...


class Node(Protocol):
    name: str
    def on_message(self, message: ClassicalMessage) -> None: ...
```

`SessionResult` (returned by `BB84Session.run`) carries:

| Field | Meaning |
| --- | --- |
| `n_blocks` | Logical bits Alice attempted |
| `n_photons` | Physical qubits emitted (`3 * n_blocks` when QEC is on) |
| `n_detected` | Photons that clicked Bob's detector |
| `n_sifted` | Blocks with matching basis and all 3 detections |
| `n_sample`, `n_key` | Blocks sacrificed for QBER / retained for the key |
| `qber_physical`, `qber_logical` | Pre-decoding and post-decoding error rates |
| `qber_bound` | One-sided finite-sample upper bound on the physical QBER |
| `logical_error_rate` | Observed post-decoding block error rate |
| `ec_leak_bits`, `pa_compression_bits` | Post-processing cost accounting |
| `key_bits`, `key_fingerprint` | Final key length and SHA-256 fingerprint (never the key) |
| `abort_reason` | `None` on success, otherwise a machine-readable code |

---

## 5. Protocol sequence

### 5.1 State machine

```
IDLE ──prepare──► EMITTED ──sift──► SIFTED ──sample──► ESTIMATED
                                                          │
                       ┌──────────────────────────────────┼─────────────────────┐
                       │ qber_bound > threshold           │ otherwise           │
                       ▼                                  ▼                     │
                   ABORTED                          DECODED ──EC──► RECONCILED ──PA──► COMPLETE
```

Every transition emits a structured event; `ABORTED` is a normal outcome, not
an exception. `ProtocolAbort` is raised only for operational faults
(unverifiable authentication tag, malformed message).

### 5.2 End-to-end sequence

```
 Alice                    QuantumChannel                  Bob                Public transcript
 ─────                    ──────────────                  ───                ─────────────────
 1. sample k_b, beta_b
 2. encode |k_b>_beta into 3 photons
 3. emit block b ────────► loss ─► Eve ─► X/Z noise ─────► 4. measure in beta'_b
                                                            (3 raw bits, dark counts)
                                                                                5. announce beta_b, beta'_b
 6. sift: keep blocks with beta_b = beta'_b and no loss
                                                                                7. reveal sample set T and bits
 8. QBER: physical (3 bits/block) and logical (after decode)
 9. decode key blocks K (majority vote / phase-flip decode)
10. abort if bound(qber_physical) > 0.11                                    ◄── 11. EC parity leak
12. privacy amplification sized by qber_physical                             ◄── 13. PA salt
14. verify fingerprints; output ell-bit key, or ABORTED
```

Steps 5-7, 11 and 13 are the only information that crosses the classical
channel; Alice's bits, bases and Bob's outcomes stay private until the
corresponding announcement, which happens strictly after measurement.

### 5.3 Classical message accounting

Let `n` be the number of blocks, `n_sample` the sacrificed sample size, `n_key` the retained key blocks.

| Message | Size | Leaks to Eve | Mitigation |
| --- | --- | --- | --- |
| Basis announcement | `2n` bits (1 per party per block) | Bases only | Announced after measurement |
| Detection report | `n` bits | Which slots clicked | Included in sifting |
| Sample reveal | `2 * n_sample` bits (or `n_sample` if one side reveals) | Sample bits, which are discarded | Sample removed from key |
| EC parity | `f * n_key * h2(e_L)` bits, `f` in [1.0, 1.22] | Linear constraints on the key | Subtracted in PA |
| PA commitment | ~128-bit salt + hash id | Nothing about the key | Universal hashing |
| Auth tags | 128 bits per message | Nothing (MAC) | Tag failure aborts |

QEC syndromes are **not** in this table: Bob computes them locally from his own
three measurement outcomes, so they never touch the classical channel. Phase 5
may add an optional public-syndrome mode for comparison; if enabled, its
leakage must be subtracted explicitly (§8.6).

### 5.4 Phase 3 implementation: sifting and the sacrificed sample

`network/simulation.py` implements steps 1-8 above for one unprotected link:

- Alice draws `n_bits` fair bits and `n_bits` fair bases; Bob draws his own
  bases independently, so `P(basis match) = 1/2` on a lossless link.
- Photons are independent and the channel is memoryless, so the exact outcome
  distribution depends only on `(bit, Alice basis, Bob basis, Eve basis)`.
  The session solves one `default.mixed` circuit per tuple (at most 16) and
  samples the individual bits from NumPy. The sampled distribution is
  identical to running one circuit per photon; only the cost changes.
- The QBER sample `T` is drawn uniformly without replacement from the sifted
  positions `S`, with `|T| = round(sample_fraction * |S|)` (20% by default,
  51 of roughly 256 bits for `n = 512`). `T` is revealed and discarded;
  `K = S \ T` is the key. Tests assert that the sample and the key are
  disjoint and that together they cover every sifted position.

**Detection power is not a security proof.** At `|T| = 51` the 11% threshold
means six or more errors. A full intercept-resend attack (`e = 25%`) produces
at least six errors with probability `1 - P(Bin(51, 1/4) <= 5) = 99.4%`, so a
512-bit demo catches Eve almost every time. A clean sample, however, only binds
the true QBER by `0 + sqrt(ln(1/delta)/(2*51)) = 26%` at `delta = 1e-3`, which
is above the threshold. The session therefore reports the point estimate and
the bound together and never calls itself secure; certification needs a sample
in the thousands (§7.2).

The alarm is a result, not an exception: `SimulationResult.security_alarm`,
`SimulationResult.warning` and a `CRITICAL` log line all carry the sentence
`"Eve is eavesdropping!"`. The comparison is strict (`qber > threshold`), so a
sample sitting exactly on 11% does not alarm. The emitter is the
`bb84.security` logger, so a host application can route or filter it; the
verdict is always available on the result object as well.

---

## 6. Quantum model

### 6.1 Encoding

```
 bit | Z basis (rectilinear)          | X basis (diagonal)
 ----+--------------------------------+-------------------------------------
  0  | |0>                            | |+> = (|0> + |1>)/sqrt(2)
  1  | |1>                            | |-> = (|0> - |1>)/sqrt(2)
```

In density-matrix form with `rho = |psi><psi|`:

```
rho_Z(0) = [[1, 0], [0, 0]]      rho_Z(1) = [[0, 0], [0, 1]]
rho_X(0) = 1/2 [[1, 1], [1, 1]]  rho_X(1) = 1/2 [[1, -1], [-1, 1]]
```

Bloch vectors are `+z`, `-z`, `+x`, `-x` respectively, which gives a cheap
exact assertion in tests: `Tr(rho * sigma_i)` must equal the expected component
within `1e-12`.

### 6.2 Measurement

Bob's measurement in basis `beta` is described by projectors `Pi_0^beta`,
`Pi_1^beta`. The Born rule gives

$$
P(m \mid \beta) = \mathrm{Tr}\!\left(\Pi_m^{\beta}\, \rho\right),
\qquad
\Pi_m^{Z} = |m\rangle\langle m|,
\quad
\Pi_m^{X} = H\,|m\rangle\langle m|\,H .
$$

When Alice and Bob use the same basis the outcome is deterministic; when they
differ,

$$
P(\text{bit correct}) = P(\text{bit wrong}) = \tfrac12 .
$$

Those mismatched blocks are discarded during sifting, so they contribute no
errors to the key — this is exactly why the sifted QBER under a passive channel
is zero rather than 50%.

### 6.3 No-Cloning and why eavesdropping is detectable

A unitary `U` that copies two arbitrary states would require

$$
U\big(|\psi\rangle|0\rangle\big) = |\psi\rangle|\psi\rangle,
\qquad
U\big(|\phi\rangle|0\rangle\big) = |\phi\rangle|\phi\rangle,
$$

and unitarity preserves inner products:

$$
\langle\psi|\phi\rangle
= \big(\langle\psi|\phi\rangle\big)^2
\;\Longrightarrow\;
\langle\psi|\phi\rangle \in \{0, 1\}.
$$

Only orthogonal states can be cloned. Since `|<0|+>|^2 = 1/2`, Eve cannot
obtain a faithful copy of a photon whose basis she does not know. The best she
can do is a minimum-error discrimination (Helstrom) measurement, whose error
probability for two pure states with equal priors is

$$
p_{\text{err}}^{\text{Helstrom}}
= \frac{1 - \sqrt{1 - |\langle\psi|\phi\rangle|^2}}{2}
= \frac{1 - 1/\sqrt{2}}{2}
\approx 0.1464
$$

for `|0>` versus `|+>`. Any attack that extracts information therefore leaves
a statistical footprint, which the QBER estimate is designed to detect.

**How the simulator models this attack.** Eve's measure-and-forward step is
implemented as complete dephasing in her chosen basis (`H` - `PhaseFlip(1/2)` -
`H` for the diagonal basis). Bob never learns her outcome, so the state he
receives is the average over her outcomes, and dephasing is exactly that
channel. This is equivalent to a mid-circuit measurement for every statistic
the simulator reports, while keeping the simulation analytic and free of
mid-circuit-measurement device constraints.

### 6.4 PennyLane mapping

| Concept | PennyLane construct |
| --- | --- |
| Computational-basis state (bit `b`) | `qml.BasisState([b], wires=[0])` |
| Diagonal-basis state (bit `b`) | `qml.BasisState([b], wires=[0])` then `qml.Hadamard(0)` |
| X-basis measurement | `qml.Hadamard(0)` before `qml.sample(qml.PauliZ(0))` |
| 3-qubit encoding | `qml.CNOT(wires=[0, 1])`, `qml.CNOT(wires=[0, 2])` |
| Syndrome `Z1Z2` | `qml.expval(qml.PauliZ(0) @ qml.PauliZ(1))` |
| Correction | Python-side `qml.PauliX(wires=i)` selected by the syndrome table |
| Noisy evolution | `qml.BitFlip`, `qml.PhaseFlip`, `qml.DepolarizingChannel` on `default.mixed` |
| Shot-based sampling | `qml.sample()` with `shots=` only in integration tests |

Two implementation rules:

- **Analytic by default.** Unit tests use exact state vectors/density matrices
  and `shots=None`; Monte Carlo runs use the analytic per-photon Pauli model in
  `noise.py` for speed. `default.mixed` is reserved for validating small,
  fully explicit noisy circuits.
- **Index endianness.** PennyLane treats wire 0 as the most significant bit of
  the state-vector index: `BasisState([0, 0, 1])` occupies index 1, so
  `index = sum_i b_i * 2**(n-1-i)`. The simulator's own bitstrings index qubit 0
  first. Conversion happens in exactly one helper, `bb84.index_to_bits`,
  covered by a dedicated test; no other module may construct a basis index by
  hand. (Verified against PennyLane 0.45.1.)

---

## 7. QBER: definition, estimation and detection threshold

### 7.1 Definition

For sifted positions `S` (matching basis, block retained), with Alice's bit
`a_i` and Bob's decoded/observed bit `b_i`:

$$
\mathrm{QBER} = \frac{1}{|S|}\sum_{i \in S} \mathbb{1}\!\left[\,a_i \neq b_i\,\right]
= \frac{\#\text{errors}}{|S|}.
$$

The simulator computes **two** rates and never confuses them:

| Quantity | Computed from | Used for |
| --- | --- | --- |
| `qber_physical` | All 3 raw detector bits per block vs. Alice's repeated bit, before decoding | Security parameter, abort decision, PA sizing |
| `qber_logical` | Post-decoding block bits vs. Alice's bit | QEC performance, diagnostics only |

In a real run the key blocks are not used for estimation. A random subset `T`
(`sample_fraction`, default 0.2) is sacrificed; `K = S \ T` is retained.

### 7.2 Finite-sample bound

With a one-sided Hoeffding bound, the true error rate exceeds the estimate with
probability at most `delta` when

$$
e \;\le\; \hat{e} + \sqrt{\frac{\ln(1/\delta)}{2|T|}} .
$$

For `|T| = 1000` and `delta = 1e-9` the penalty is `sqrt(20.7/2000) = 0.102`,
which is unacceptable — this is the practical reason a simulator run needs
thousands of sifted blocks, and why a 100-block demo must be reported as
"insufficient sample, aborted" rather than "secure". `estimate_qber` returns
both the point estimate and the bound, and the session aborts when
`n_sample < min_sample_blocks` (default 200).

### 7.3 Intercept-resend attack

Eve measures each photon in a uniformly random basis and re-prepares the
outcome. Conditioned on a sifted block (Alice and Bob share a basis):

| Eve's basis | Probability | Bob's error probability |
| --- | --- | --- |
| Correct | 1/2 | 0 (she forwards the true state) |
| Wrong | 1/2 | 1/2 (re-prepared state projects randomly) |

$$
e_{\text{IR}} = \tfrac12 \cdot 0 + \tfrac12 \cdot \tfrac12 = \frac14 = 25\%.
$$

The detector therefore alarms at `25% >> 11%`, and Eve's extractable
information per bit is substantial. A partial tap with interception fraction
`f` yields `e = 0.25 f`, so an adversary who taps 40% of photons produces a 10%
QBER — below a naive 11% threshold, yet non-zero and accounted for by privacy
amplification. **Detection must never be a boolean "Eve present" verdict; the
measured QBER itself is the security parameter.**

### 7.4 Thresholds

The asymptotic secret-key fraction for one-way post-processing on a
single-photon BB84 link is

$$
r = 1 - h_2(e_{\text{ph}}) - f\,h_2(e_{\text{bit}}),
\qquad
h_2(e) = -e\log_2 e - (1-e)\log_2(1-e),
$$

where `e_ph` is the phase-error estimate from the complementary basis and
`e_bit` the bit-error estimate. With `e_ph = e_bit = e` and `f = 1`:

$$
r = 1 - 2h_2(e) \;\ge\; 0
\iff e \le e_{\max} \approx 11\% .
$$

| `e` | `h2(e)` | `r` (`f = 1.0`) | `r` (`f = 1.22`) | Outcome |
| --- | --- | --- | --- | --- |
| 0.00 | 0.000 | 1.000 | 1.000 | Ideal link |
| 0.02 | 0.141 | 0.718 | 0.686 | Healthy link |
| 0.05 | 0.286 | 0.427 | 0.364 | Degraded link |
| 0.10 | 0.469 | 0.062 | -0.041 | Only viable with QEC or better EC |
| 0.11 | 0.500 | 0.000 | -0.110 | Security threshold |
| 0.25 | 0.811 | -0.623 | -0.801 | Intercept-resend: abort |

`f` is the reconciliation inefficiency (`f = 1.0` is the Shannon limit,
`f = 1.22` is a realistic LDPC/Cascade value). Two-way reconciliation can push
the threshold towards 20%, but it is out of scope for Phase 2.

### 7.5 Error budget

For mean photon number `mu` per pulse, transmittance `eta`, intrinsic
misalignment `e_opt` and detector dark-count probability `p_dc`:

$$
e_{\text{obs}} = \frac{\eta\mu\, e_{\text{opt}} + \tfrac12 p_{dc}}
{\eta\mu + p_{dc} - \eta\mu\, p_{dc}}
\;\approx\; \frac{\eta\mu\, e_{\text{opt}} + p_{dc}/2}{\eta\mu + p_{dc}} .
$$

With `eta*mu = 0.1`, `e_opt = 0.02`, `p_dc = 1e-5`, the dark-count contribution
is `5e-6 / 0.10001 = 5e-5`, i.e. negligible; with `eta*mu = 1e-3` it becomes
`~2.5%` and dominates. The simulator reports the contribution of each term so
that operators can see which one is eating the key rate.

---

## 8. Quantum error correction: the 3-qubit repetition code

### 8.1 Encoding

A logical qubit `|psi> = alpha|0> + beta|1>` is spread over three physical
qubits:

$$
|0\rangle_L = |000\rangle,
\qquad
|1\rangle_L = |111\rangle,
\qquad
|\psi\rangle_L = \alpha|000\rangle + \beta|111\rangle .
$$

Circuit (data qubit `q0`, ancillas `q1`, `q2`):

```
|psi> ──■──────   |psi>_L = alpha|000> + beta|111>
|0>   ──X──────
|0>   ──X──────
        CNOT(q0,q1) and CNOT(q0,q2)
```

The code space is the `+1` eigenspace of the stabilizer generators

$$
S_1 = Z_1 Z_2, \qquad S_2 = Z_2 Z_3,
$$

with logical operators `X_L = X_1X_2X_3` and `Z_L = Z_1Z_2Z_3`. The code has
distance `d = 3`, so it corrects `t = floor((d-1)/2) = 1` bit flip and detects
two.

### 8.2 Syndrome extraction and decoding

Both stabilizers are measured on Bob's side from the three raw outcomes. Each
generator has eigenvalues `+1` (even parity) or `-1` (odd parity):

| Error | `Z1Z2` | `Z2Z3` | Correction |
| --- | --- | --- | --- |
| `I` | +1 | +1 | none |
| `X1` | -1 | +1 | `X1` |
| `X2` | -1 | -1 | `X2` |
| `X3` | +1 | -1 | `X3` |

The four syndromes are orthogonal, so the decoder is a pure lookup table — no
optimization or belief propagation is required. Because the outcomes are used
locally, no syndrome bit is published.

**Indexing trap.** The table uses 1-based physics labels (`X1` = a flip on the
first data qubit). PennyLane wires are 0-based (`wire = label - 1`), so applying
the `X1` row to `qml.PauliX(wires=1)` is off by one. Verified with PennyLane
0.45.1: a flip on wire 1 produces the `X2` row `(-1, -1)`. `tests/test_ecc.py`
must assert the full mapping, and the decoder must take the wire index from a
single translation helper rather than from the table row number.

### 8.3 Why it works: Knill-Laflamme condition

The code corrects the error set `{I, X1, X2, X3}` if and only if

$$
P E_a^{\dagger} E_b P = c_{ab} P,
\qquad c_{ab} = \delta_{ab},
$$

where `P` projects onto the code space. For `a != b`, `X_iX_j` flips two
qubits: `X_iX_j|000> = |110>` and `X_iX_j|111> = |001>`, both orthogonal to the
code space, so all off-diagonal terms vanish. For `a = b` the projector
returns `P`. Hence every single bit flip is correctable and distinguishable.

### 8.4 Residual logical error

If two or more physical qubits flip, the syndrome points to a single-qubit
correction that is itself wrong, producing a logical error. For independent
bit flips with probability `p`:

$$
p_L = \binom{3}{2} p^2(1-p) + p^3 = 3p^2 - 2p^3 .
$$

`p_L < p` exactly when `p < 1/2`; at `p = 1/2` the code is neutral, and above
it the code amplifies noise. This is why a QEC layer must always be gated on
the measured physical error rate.

| `p` | `p_L = 3p^2 - 2p^3` | Gain |
| --- | --- | --- |
| 0.01 | 2.98e-4 | 33x |
| 0.05 | 7.25e-3 | 6.9x |
| 0.10 | 0.028 | 3.6x |
| 0.25 | 0.156 | 1.6x |
| 0.50 | 0.500 | none |

### 8.5 The dual code, and why one axis is not enough

Conjugating the encoder with `H` on every qubit exchanges `X` and `Z`:

$$
|0\rangle_L = |+++\rangle,
\qquad
|1\rangle_L = |---\rangle,
\qquad
S_1 = X_1X_2,\; S_2 = X_2X_3 .
$$

This **phase-flip code** corrects single `Z` errors and is blind to `X` errors:
`X_i` commutes with both stabilizers and acts as the logical `Z_L`, so a single
bit flip becomes an undetectable logical phase error. The two codes are duals,
not alternatives that can be combined by relabeling.

For BB84 this has a direct consequence:

| Block basis | Error that matters | Code that corrects it | What the other code does |
| --- | --- | --- | --- |
| Z | bit flip `X` | bit-flip code | phase-flip code turns `X` into logical `Z_L` |
| X | phase flip `Z` | phase-flip code | bit-flip code turns `Z` into logical `Z_L` |

The architecture therefore supports `code = "bit_flip_3" | "phase_flip_3" |
"none"` and, in Phase 5, a `"shor_9"` mode. **No single 3-qubit code protects
both bases.** Under depolarizing noise, where `X`, `Y` and `Z` all occur, the
bit-flip code leaves the `Z` component untouched and the logical error is
`~2p/3 + O(p^2)` — worse than the unprotected channel. Parameterizing a single
3-qubit code as "general QEC" would be physically wrong, so the simulator makes
the choice explicit and reports the logical error per basis.

### 8.6 Interaction with security analysis

QEC reduces the *observed* error rate without reducing Eve's *information*.
Two numbers make the risk concrete, for full intercept-resend on all three
physical photons (`p = 0.25`):

- pre-decoding physical QBER: `25%` — far above threshold, session aborts;
- post-decoding logical error: `3(0.25)^2 - 2(0.25)^3 = 15.6%`.

Now let Eve tap only one photon in three (`p = 0.25/3 = 8.3%`):

- pre-decoding physical QBER: `8.3%`, below a naive 11% alarm;
- post-decoding logical error: `3(0.0833)^2 - 2(0.0833)^3 = 2.0%`.

A pipeline that used the 2% figure for privacy amplification would compress
almost nothing and emit a key while Eve holds information about one third of the
blocks. **Security is therefore parameterized by `qber_physical` (and its
confidence bound), never by `qber_logical`.** QEC's legitimate benefit is
reducing the *bit-error* term in reconciliation, where each corrected error
saves `f * h2(e)` bits of parity leakage; it never substitutes for
eavesdropping detection or privacy amplification.

### 8.7 Cost of encoding: the eta-cubed penalty

A block yields a key bit only if **all three** photons survive and are detected
(one lost photon leaves a two-outcome ambiguity that v1 discards rather than
guesses). With per-photon transmittance `eta`:

$$
P(\text{block usable} \mid \text{basis match}) = \eta^3,
\qquad
\langle n_{\text{sifted}}\rangle = \tfrac12\, n\, \eta^3 .
$$

At `alpha = 0.2 dB/km`:

| Link length | `eta` | `eta^3` | Sifted blocks per 10,000 emitted (ideal detector) |
| --- | --- | --- | --- |
| 10 km | 0.631 | 0.251 | 1,255 |
| 25 km | 0.316 | 0.0316 | 158 |
| 50 km | 0.100 | 0.001 | 5 |

Encoding costs a factor `eta^2` in rate relative to unprotected transmission
(`0.5*n*eta`). It pays off only when the reduction in reconciliation leakage
exceeds that factor — for example a 10 km link at `e = 0.10`, `f = 1.22`, where
the unprotected key rate is negative and the coded link still produces about
`0.038` secret bits per emitted block. The session therefore reports both
configurations side by side rather than declaring QEC "always better".

Erasure-aware decoding (accepting a block when the two surviving outcomes agree)
would recover part of the loss penalty; it is deliberately deferred because it
changes the security analysis and must be proved, not assumed.

---

## 9. Configuration contract

Configuration is TOML, parsed with the standard-library `tomllib` (Python
3.11+), so the dependency list stays at four packages. The file is validated
into frozen dataclasses; unknown keys are an error, not a warning.

```toml
[run]
seed = 20261021
output_dir = "results"
log_level = "INFO"

[source]
blocks = 4096               # logical bits; emits 3 * blocks physical photons
basis_bias_z = 0.5          # P(Alice chooses Z)

[channel]
length_km = 10.0
attenuation_db_per_km = 0.2
detector_efficiency = 1.0
noise_model = "bit_flip"    # none | bit_flip | phase_flip | depolarizing | combined
bit_flip_prob = 0.01
phase_flip_prob = 0.0
dark_count_prob = 1.0e-5

[qec]
enabled = true
code = "bit_flip_3"         # none | bit_flip_3 | phase_flip_3

[eve]
enabled = true
strategy = "intercept_resend"
intercept_fraction = 1.0
basis_guess = "random"      # random | z | x

[protocol]
sample_fraction = 0.2
min_sample_blocks = 200
alarm_threshold = 0.11
ec_efficiency = 1.22
security_margin_bits = 64
```

Validation invariants (enforced at load time, unit-tested):

- all probabilities in `[0, 1]`; `sample_fraction` in `(0, 1)`;
- `blocks >= 1`, `length_km >= 0`, `ec_efficiency >= 1.0`;
- `code != "shor_9"` until Phase 5 (fail fast rather than silently degrade);
- `security_margin_bits <= 0.5 * expected key bits`, otherwise warn;
- `min_sample_blocks <= sample_fraction * expected sifted blocks`.

---

## 10. Determinism, observability and failure handling

### 10.1 Randomness

One master seed feeds a `SeedSequence`, which spawns independent child streams
per purpose: `alice.bits`, `alice.basis`, `alice.syndrome`, `bob.basis`,
`bob.dark_counts`, `channel.loss`, `channel.noise`, `eve.basis`,
`eve.outcome`, `protocol.sample`, `pa.salt`. Rules:

- no module-level `np.random` calls and no `random` module usage;
- every stochastic function takes an explicit `numpy.random.Generator`;
- identical config and seed must produce a bit-identical key fingerprint, and
  this is an integration test.

### 10.2 Observability

- `RunManifest`: seed, resolved config, config SHA-256, library versions, git
  commit if available. Written next to the results so a run can be reproduced.
- `EventLog`: one JSON object per protocol transition (`session_started`,
  `emitted`, `sifted`, `estimated`, `aborted`, `completed`) with counters.
- Key material is never logged. Only lengths and SHA-256 fingerprints of the
  final key appear in logs and results.

### 10.3 Error taxonomy

| Exception | Raised when | Recovery |
| --- | --- | --- |
| `ConfigurationError` | Unknown key, out-of-range value, unsupported combination | Fail fast at startup |
| `ChannelError` | Malformed photon state (non-Hermitian or non-positive `rho`) | Bug; abort run |
| `ProtocolAbort` | Auth tag mismatch, EC failure, insufficient sample | Session ends with reason code; no key |

QBER above threshold is **not** an error path: it is the intended, measured
outcome and is returned as `SessionResult.abort_reason =
"QBER_ABOVE_THRESHOLD"`.

---

## 11. Testing strategy

| Layer | Test type | Representative assertion |
| --- | --- | --- |
| `states` | Unit, exact | `rho_X(0)` has Bloch vector `+x`; squared overlap of Z and X zero states is `0.5` |
| `noise` | Unit, exact | Kraus completeness `sum K_i^dag K_i = I`; trace preserved after noise |
| `bb84` (states) | Unit, exact | Density matrix equals analytic `photon_state` in both bases |
| `bb84` (channel) | Exact | QBER `= p` per basis; Eve plus noise `= 0.25 + p/2` |
| `bb84` (QEC) | Unit, exact | All four syndrome rows; recovery `= 1 - 3p^2 + 2p^3` |
| `bb84` (QEC) | Exact average over 2^3 Eve patterns | Intercept-resend leaves 15.625% logical error |
| `protocol` | Integration | No Eve, no noise -> `qber_physical = 0`, Alice/Bob keys match |
| `protocol` | Integration | Full intercept-resend -> abort with `QBER_ABOVE_THRESHOLD` |
| `protocol` | Determinism | Same seed -> identical key fingerprint; different seed -> different |
| `simulation` (sifting) | Integration | Sifted positions are exactly the matching-basis positions; sample and key partition them |
| `simulation` (QBER) | Integration | Passive `p = 0.2` -> measured `0.2 +/- 0.06`; intercept-resend -> `"Eve is eavesdropping!"` |
| `simulation` (boundary) | Unit, exact | `qber = 0.11` does not alarm, `0.12` does; the Hoeffding bound tightens as the sample grows |
| `simulation` (determinism) | Integration | Same seed -> identical `SimulationResult`, including Eve's basis draws |
| `utils.metrics` | Unit | `h2(0) = h2(1) = 0`, `h2(0.5) = 1`, symmetric about 0.5 |
| End to end | `slow` | QBER sweeps from 0 to 0.25 cross the 11% threshold monotonically |

Statistical tests use a fixed seed and a sample size chosen so that the 3-sigma
band is narrower than the tolerance; they must never depend on wall-clock time
or on `np.random` global state. Analytic results (fidelity, syndrome tables,
entropy) are tested exactly, not statistically.

---

## 12. Design decisions and invariants

| # | Decision | Rationale | Rejected alternative |
| --- | --- | --- | --- |
| 1 | PennyLane as the quantum SDK | Pure-Python/NumPy stack, `default.mixed` density matrices for noisy channels, differentiable circuits, small install | Qiskit (heavier transpiler stack, no benefit for 1-3 qubit circuits) |
| 2 | Density matrices for in-flight photons | Eve's attack and channel noise create mixed states; state vectors cannot represent them | State-vector-only simulation (would force trajectory averaging at every layer) |
| 3 | Analytic Pauli sampling for Monte Carlo, `default.mixed` for validation | Exact and fast at 12k photons, while still validating the physics explicitly on small circuits | Full density-matrix evolution for every photon (orders of magnitude slower) |
| 4 | `src/` as import root, no install step | `python -m src...` from the repo root works with zero packaging; pytest gets `pythonpath = ["."]` | Editable install (extra build config and failure mode for a teaching repo) |
| 5 | TOML config via stdlib `tomllib` | No PyYAML dependency; strict parsing | YAML (untyped, extra dependency) |
| 6 | 3-qubit code selected per basis, with honest limits documented | Repetition codes protect one Pauli axis only; hiding that would produce a physically wrong simulator | Claiming a single 3-qubit code handles depolarizing noise |
| 7 | Syndrome decoded from Bob's private outcomes | Zero classical leakage for the QEC layer | Public syndrome discussion (leaks 2 bits per key bit) |
| 8 | Security parameter is the pre-decoding physical QBER | QEC lowers the observed error rate without lowering Eve's information | Post-decoding QBER (would overestimate the key rate) |
| 9 | Lost photons discard the whole block in v1 | A 2-of-3 outcome is ambiguous without erasure-aware decoding | Majority vote over survivors (silently wrong) |
| 10 | Aborts are `SessionResult`s, not exceptions | "No key" is a legitimate protocol outcome and must be plottable | Raising for QBER above threshold (conflates physics with bugs) |

Invariants enforced by tests:

1. `n_photons == 3 * n_blocks` when QEC is enabled, `n_blocks` otherwise.
2. `0 <= qber_physical, qber_logical <= 1`; an ideal channel gives 0.
3. Alice's and Bob's final keys are equal iff `abort_reason is None`.
4. No message published before the corresponding measurement or basis choice.
5. `session.public_transcript` never contains `Photon.bit`, `Photon.basis`, or
   raw key bits before the announcement step.
6. Decoding never reads Alice's private data; it consumes only Bob's outcomes
   and the announced basis.

---

## 13. Explicit assumptions and limitations

- **Single-photon source.** Photon-number-splitting and decoy-state defenses are
  not modeled; a weak coherent source would require the decoy-state extension.
- **No detector side channels.** Efficiencies are scalar constants; no dead
  time, afterpulsing, or blinding.
- **No fiber polarization drift.** The channel is a Pauli channel with static
  parameters; time-varying birefringence is a Phase 5 extension.
- **No authentication implementation.** The classical channel models 128-bit
  tags as an oracle; no MAC or pre-shared key management is provided.
- **Asymptotic key-rate formula.** Finite-key composable security is reported as
  a bound only, not proved.
- **Educational output.** The key is a simulation artifact and must not be used
  for real encryption, authentication, or any production purpose.

---

## 14. Roadmap

| Phase | Deliverable | Exit criterion |
| --- | --- | --- |
| 1 (this) | Scaffold, pins, architecture contract | `pytest` green on structure tests; imports resolve |
| 2 (delivered) | Photon API and 3-qubit code in `quantum_core/bb84.py`; general `noise.py`/`ecc.py` classes deferred | Exact physics tests pass; recovery matches `1 - 3p^2 + 2p^3` |
| 3 (delivered) | `network/simulation.py`: automated 512-bit loop, open public channel, sifting, sacrificed-sample QBER, 11% alarm | Ideal link yields matching keys; intercept-resend emits `"Eve is eavesdropping!"` |
| 4 | `utils`: metrics, plots, CLI, manifests | QBER/key-rate sweep figures reproduced from a seed |
| 5 | Hardening: decoy states, Shor-9 code, finite-key bounds, erasure decoding | Documented security accounting per mode |

---

## 15. Glossary and references

**QBER** — quantum bit error rate, the fraction of discordant bits in the
sifted key. **Sifting** — discarding blocks where the announced bases differ.
**IR / intercept-resend** — Eve measures and re-prepares each photon.
**EC** — information reconciliation (error correction over the public channel).
**PA** — privacy amplification (universal hashing that removes Eve's partial
information). **Syndrome** — the stabilizer measurement outcome that identifies
an error. **Transmittance** — probability that a photon survives the link and is
detected.

1. C. H. Bennett and G. Brassard, "Quantum Cryptography: Public Key Distribution
   and Coin Tossing", *Proc. IEEE ICCSSP*, 1984.
2. M. A. Nielsen and I. L. Chuang, *Quantum Computation and Quantum
   Information*, Cambridge University Press, 2010 (ch. 10: error correction).
3. P. W. Shor and J. Preskill, "Simple Proof of Security of the BB84 Quantum Key
   Distribution Protocol", *Phys. Rev. Lett.* 85, 441 (2000).
4. E. Knill and R. Laflamme, "Theory of Quantum Error-Correcting Codes",
   *Phys. Rev. A* 55, 900 (1997).
5. V. Scarani et al., "The Security of Practical Quantum Key Distribution",
   *Rev. Mod. Phys.* 81, 1301 (2009).
6. N. Gisin, G. Ribordy, W. Tittel, H. Zbinden, "Quantum Cryptography",
   *Rev. Mod. Phys.* 74, 145 (2002).
7. X. Ma, B. Qi, Y. Zhao, H.-K. Lo, "Practical Decoy State for Quantum Key
   Distribution", *Phys. Rev. A* 72, 012326 (2005).
