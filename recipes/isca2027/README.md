# ISCA 2027 recipes

Each recipe reproduces one experiment of the I-Fuse ISCA 2027 submission. A recipe is a folder holding
the `sci` descriptor and a README that names the exact Scarab commit, the run commands, and how the
result was validated.

| Recipe | Scarab branch (codverch/ifuse) | Commit | Binary | Experiment |
|---|---|---|---|---|
| [`baseline/`](baseline/) | `isca2027-baseline` | `89b5145` | `scarab_89b5145_0.opt` | `isca2027-baseline` |
| [`rfp/`](rfp/) | `isca2027-rfp` | `1aaf11b` | `scarab_1aaf11b_0.opt` | `isca2027-rfp-final` |
| [`ifuse/`](ifuse/) | `isca2027-ifuse` | `e64f41b` | `scarab_e64f41b_0.opt` | `isca2027-ifuse` |
| [`rfp-ifuse/`](rfp-ifuse/) | `isca2027-rfp-ifuse` | `f013b38` | `scarab_f013b38_0.opt` | `isca2027-rfp-ifuse` |

## Shared setup

All three use the same core, the same traces and the same windows, so their cycles compare directly.

- **Core:** Golden Cove, `src/PARAMS.in` of `89b5145` plus `--icache_size 32768 --power_intf_on 1`.
  Neither branch changes `PARAMS.in`; they only add their own parameters, which default to off.
  Checked on the run outputs: `PARAMS.out` of baseline, RFP, I-Fuse and no-fusion runs differ in
  no parameter other than the mechanism's own (`rfp_*`, `ifuse_*`, `load_cov_dump`, `bindir`).
- **Workloads:** 11 applications, 40 SimPoints; see `json/isca2027/README.md`.
- **Window:** every SimPoint runs 20M warmup instructions, then measures 10M on its own segment
  (`explicit_roi`).

## Run a recipe

`sci` reads descriptors from `json/`, so each recipe is linked there as `json/isca2027/recipe-<name>.json`.

```bash
export PATH=/hot/deepanjm/sqfs-env/bin:$PATH        # apptainer helpers on phoebe-login
./sci --build-scarab isca2027/recipe-rfp            # builds scarab_<commit>_0.opt from scarab_path
./sci --sim isca2027/recipe-rfp                     # runs every SimPoint
./sci --collect-stats isca2027/recipe-rfp           # writes <experiment>/collected_stats.csv
```

The build tree (`scarab_path`) must be checked out at the recipe's branch; `sci` names the binary
after that tree's HEAD.

Do not run `./sci --init` on phoebe-login: it prunes the shared conda environment and downloads traces.

## Layout

```
recipes/isca2027/
  README.md          this file
  CHANGELOG.md       changes to the recipes
  <recipe>/
    descriptor.json  the sci descriptor (linked as json/isca2027/recipe-<recipe>.json)
    README.md        model, commit, validation, expected results
```

The other files in `json/isca2027/` are earlier and side experiments (sensitivity studies, ablations,
per-load dumps); they stay for provenance.

## Conventions

Commits follow [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/):
`feat(isca2027): ...`, `fix: ...`, `docs: ...`, `chore: ...`. Every Scarab change behind a recipe lives on
that recipe's branch, so a commit hash identifies the exact model.
