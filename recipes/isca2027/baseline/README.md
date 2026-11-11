# Baseline

Golden Cove with no load-latency mechanism. Every other recipe is measured against it.

| Item | Value |
|---|---|
| Scarab | codverch/ifuse branch `isca2027-baseline` @ `89b5145` ("Use Golden Cove parameters as PARAMS.in") |
| Binary | `scarab_89b5145_0.opt` |
| Build tree | `/hot/deepanjm/isca2027/scarab-baseline` |
| Configurations | `baseline` |
| Experiment | `/hot/deepanjm/isca2027/simulations/isca2027-baseline` |

## Run

```bash
./sci --build-scarab isca2027/recipe-baseline
./sci --sim isca2027/recipe-baseline
./sci --collect-stats isca2027/recipe-baseline
```

## Validation

The other recipes' "mechanism off" modes reproduce this experiment cycle for cycle: RFP with
`--rfp_on 0` and I-Fuse's `nofuse` configuration. That was first checked on a 3M-instruction window and
then on full runs.
