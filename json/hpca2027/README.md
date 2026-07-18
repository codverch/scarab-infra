# HPCA 2027 simulation descriptors

Traces under `/dev/shm/baseline/simpoint_traces`.

## Window

| | Value |
|--|--|
| `full_warmup` | `0` |
| `inst_limit` | suite max SP + 1M (run each SP to EOF) |

| Launcher | Experiment dir | Config |
|----------|----------------|--------|
| `baseline.sh` | `simulations/baseline/{app}/{sp}/` | `baseline` only |
| `ideal_fusion_pass1.sh` | `simulations/ideal-fusion-pass1/{app}/{sp}/` | `pass1` — candidates → `/dev/shm/baseline/ideal_fusion_candidates/` |
| `ideal_fusion_pass2.sh` | `simulations/ideal-fusion-pass2/{app}/{sp}/` | `pass2` — reads candidates from `/dev/shm/baseline/ideal_fusion_candidates/` |
| `ideal_fusion_unbounded_pass1.sh` | `simulations/ideal-fusion-unbounded-pass1/{app}/{sp}/` | `pass1` — unbounded distance; candidates → `/dev/shm/baseline/ideal_fusion_candidates_unbounded/` (needs Scarab `hpca2027-unbounded-distance-ideal-fusion`) |
| `ideal_fusion_unbounded_pass2.sh` | `simulations/ideal-fusion-unbounded-pass2/{app}/{sp}/` | `pass2` — reads unbounded candidates from `/dev/shm/baseline/ideal_fusion_candidates_unbounded/` |
| `runtime_ifuse.sh` | `simulations/runtime-ifuse/{app}/{sp}/` | `runtime_ifuse` only (threshold=1000) |
| `runtime_ifuse_train_threshold_sweep.sh` | `simulations/runtime-ifuse-train-threshold-sweep/{config}/{app}/{sp}/` | `train_thresh_{10,100,1000,10000}` |
| `helios.sh` | `simulations/helios/{app}/{sp}/` | `helios` only (conf threshold=150, ±10) |
| `rfp.sh` | `simulations/rfp/{config}/{app}/{sp}/` | `baseline` (`--rfp_on 0`) vs `rfp` (`--rfp_on 1`); needs Scarab `hpca2027-rfp` |

## Fast path (recommended)

Do **not** rebuild Scarab/docker every time — that is what makes setup slow.

```bash
cd ~/scarab-infra
./json/hpca2027/baseline.sh          # register + sim + finalize (reuses cache)
./json/hpca2027/ideal_fusion_pass1.sh
./json/hpca2027/ideal_fusion_pass2.sh   # requires pass-1 candidates
./json/hpca2027/ideal_fusion_unbounded_pass1.sh --build   # Scarab unbounded-distance branch
./json/hpca2027/ideal_fusion_unbounded_pass2.sh           # requires unbounded pass-1 candidates
./json/hpca2027/runtime_ifuse.sh
./json/hpca2027/helios.sh

# Training-threshold sweep: promote PC pairs to FCT after N=10/100/1000/10000 obs
./json/hpca2027/runtime_ifuse_train_threshold_sweep.sh

# Register File Prefetch (requires Scarab on hpca2027-rfp):
./json/hpca2027/rfp.sh --build       # first time / after RFP source changes
./json/hpca2027/rfp.sh               # subsequent runs

# equivalent sci-only (after a one-time --dry-run if JSON is stale):
./sci --sim hpca2027/baseline
./sci --sim hpca2027/ideal_fusion_pass1
./sci --sim hpca2027/ideal_fusion_pass2
./sci --sim hpca2027/ideal_fusion_unbounded_pass1
./sci --sim hpca2027/ideal_fusion_unbounded_pass2
./sci --sim hpca2027/runtime_ifuse
./sci --sim hpca2027/helios
./sci --sim hpca2027/runtime_ifuse_train_threshold_sweep
./sci --sim hpca2027/rfp
./json/hpca2027/baseline.sh --finalize
./json/hpca2027/ideal_fusion_pass1.sh --finalize
./json/hpca2027/ideal_fusion_pass2.sh --finalize
./json/hpca2027/ideal_fusion_unbounded_pass1.sh --finalize
./json/hpca2027/ideal_fusion_unbounded_pass2.sh --finalize
./json/hpca2027/runtime_ifuse.sh --finalize
./json/hpca2027/helios.sh --finalize
./json/hpca2027/runtime_ifuse_train_threshold_sweep.sh --finalize
./json/hpca2027/rfp.sh --finalize
```

Ideal fusion workflow:

```bash
# Pass-1 writes candidates to tmpfs (not under simulations/)
./json/hpca2027/ideal_fusion_pass1.sh
./json/hpca2027/ideal_fusion_pass1.sh --check-candidates

# Pass-2 consumes those candidates; results under simulations/ideal-fusion-pass2/
./json/hpca2027/ideal_fusion_pass2.sh --check-candidates   # optional preflight
./json/hpca2027/ideal_fusion_pass2.sh
```

Unbounded-distance ideal fusion (Scarab `hpca2027-unbounded-distance-ideal-fusion`):

```bash
# Separate candidate tree so bounded runs are not overwritten
./json/hpca2027/ideal_fusion_unbounded_pass1.sh --build
./json/hpca2027/ideal_fusion_unbounded_pass1.sh --check-candidates
./json/hpca2027/ideal_fusion_unbounded_pass2.sh --check-candidates
./json/hpca2027/ideal_fusion_unbounded_pass2.sh
```

Only rebuild when Scarab source or the workload Dockerfile changed:

```bash
./json/hpca2027/ideal_fusion_pass1.sh --build
./json/hpca2027/ideal_fusion_pass2.sh --build
./json/hpca2027/ideal_fusion_unbounded_pass1.sh --build
./json/hpca2027/ideal_fusion_unbounded_pass2.sh --build
./json/hpca2027/runtime_ifuse.sh --build
./json/hpca2027/helios.sh --build
./json/hpca2027/runtime_ifuse_train_threshold_sweep.sh --build
./json/hpca2027/rfp.sh --build
# or:
./sci --build-scarab hpca2027/runtime_ifuse
./sci --build-scarab hpca2027/helios
./sci --build-scarab hpca2027/rfp
```

## Helios confidence defaults

From `scarab/src/general.param.def` (used by `helios.sh`):

| Knob | Value |
|------|-------|
| `helios_confidence_threshold` | `150` |
| `helios_confidence_increment` | `10` |
| `helios_confidence_decrement` | `10` |
| `helios_fusion_window` | `64` |
