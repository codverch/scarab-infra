# I-Fuse + RFP

I-Fuse and RFP in one core. RFP predicts and prefetches only the loads that I-Fuse did not classify as
LD1 or LD2 (`--rfp_skip_ifuse_pairs 1`, the default), at the lowest L1-D priority.

| Item | Value |
|---|---|
| Scarab | codverch/ifuse branch `isca2027-rfp-ifuse` @ `f013b38` |
| Binary | `scarab_f013b38_0.opt` |
| Build tree | `/hot/deepanjm/isca2027/scarab-rfp-ifuse` |
| Configurations | `off`, `ifuse_only`, `rfp_only`, `rfp_ifuse` |
| Experiment | `/hot/deepanjm/isca2027/simulations/isca2027-rfp-ifuse` |
| Results | codverch/ifuse branch `isca2027-rfp-ifuse`, folder `isca2027-rfp-ifuse/` |

`rfp_ifuse` and `rfp_only` use the RFP parameters of `../rfp`. `rfp_ifuse` and `ifuse_only` use the
I-Fuse defaults of `../ifuse`. `off` disables both and reproduces `isca2027-baseline` on all 11
applications.

## Run on phoebe-login

```bash
./sci --build-scarab isca2027/recipe-rfp-ifuse
./sci --sim isca2027/recipe-rfp-ifuse
./sci --collect-stats isca2027/recipe-rfp-ifuse
```

## Run on the pod (how the published results were made)

The pod has 256 cores but no Docker or Apptainer, so `sci --sim` cannot run there.
`podrun.sh` issues the same Scarab command as `common/scripts/run_memtrace_single_simpoint.sh` for
`explicit_roi` traces, with the power model off. `queue.txt` lists the 160 runs.

1. Copy the traces, the binary, `libdynamorio.so` and `libdrpreload.so`
   (from `src/build/opt/deps/dynamorio/lib64/release`), and `src/PARAMS.golden_cove`
   to `/localdisk/deepanjm/isca2027-sims/{traces,bin,bin/lib}`.
2. `xargs -P 170 -L 1 ./podrun.sh < queue.txt`
3. Copy `simulations/isca2027-rfp-ifuse` back to the experiment directory above, touch `DONE` in each
   run directory, and run `./sci --collect-stats isca2027/recipe-rfp-ifuse`.

## Verification

See the results README. In short: off reproduces the baseline, each mechanism alone reproduces its own
branch cycle for cycle, an assert proves RFP never serves an I-Fuse load, and runs are deterministic
across hosts.
