# I-Fuse

I-Fuse pairs two loads that touch the same cache block. The first load (LD1) reads the block, and the
second load's (LD2's) consumers wake from that data one cycle later. LD2 still generates its own address
to check the pairing; a wrong pairing flushes.

| Item | Value |
|---|---|
| Scarab | codverch/ifuse branch `isca2027-ifuse` @ `e64f41b` |
| Binary | `scarab_e64f41b_0.opt` |
| Build tree | `/hot/deepanjm/isca2027/scarab-isca2027-ifuse` |
| Configurations | `nofuse` (`--ifuse_runtime_training_enabled 0`), `ifuse` |
| Experiment | `/hot/deepanjm/isca2027/simulations/isca2027-ifuse` |

`nofuse` is the same binary with fusion off. It must reproduce `isca2027-baseline` cycle for cycle.

## Run

```bash
./sci --build-scarab isca2027/recipe-ifuse
./sci --sim isca2027/recipe-ifuse
./sci --collect-stats isca2027/recipe-ifuse
```

## Provenance and validation

- `isca2027-ifuse` was produced by `1532c62`. Its file tree is identical to the branch's first commit,
  `f94463f` "feat: port I-Fuse onto baseline 89b5145".
- The commits after it add statistics and off-by-default switches only: per-load dumps, LD2 source
  levels, and the RFP-timing ablation flag `--ifuse_ld2_wake_at_agu`.
- A build of `5e2849a` (previously `c0031f3`) reproduced `isca2027-ifuse` cycles exactly on the
  heaviest SimPoint of all 11 applications.
- A build of the tip tree reproduced memcached SimPoint 0 exactly, in both `nofuse` and `ifuse`.
- The branch's commits were re-authored (codverch). Each commit's file tree is unchanged.

## Known issue

I-Fuse's store check covers only stores between LD1 and LD2. An older store that writes LD2's bytes
and has not committed when LD1 reads the cache is not caught, so LD2's consumers can receive stale
data. In memcached SimPoint 0, 4.6% of fused LD2s woke before such a store executed, and they carry
57% of the cycles only I-Fuse saves. A fix and its effect are pending.
