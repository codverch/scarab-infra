# Reproducing the HELIOS (store-store OFF) experiment

Baseline vs HELIOS at each app's optimal confidence tuning, Golden Cove, 16 datacenter apps.

## Two repos

| Repo | GitHub | Branch | Role |
|------|--------|--------|------|
| **I-Fuse** | `codverch/I-Fuse` | `helios-2026` | the HELIOS scarab simulator source + `PARAMS.in` (the Golden Cove config) |
| **scarab-infra** (this) | `codverch/scarab-infra` | `helios` | infrastructure to run experiment |

## Setup + run on a fresh node

**0. Bootstrap scarab-infra — `./setup-scarab-helios.sh`.** Downloads dependencies and 
clones the relevant repos. 

**1. Bootstrap scarab-infra — `./sci --init`.** This installs Docker, configures the docker
socket, installs Miniconda if absent, and creates/updates the `scarabinfra` conda env from
`quickstart_env.yaml` (`sci` re-execs itself inside that env). The scarab build runs inside a
Docker image, so no host PIN/clang toolchain is required. (`/users/vedlaksh/setup_scarab-3.sh` is
an optional host helper — build deps + a 200 GB tmpfs at `/dev/shm/baseline` + trace download; it
does **not** set up Docker or conda.)

**2. Run the experiment — `conda activate scarabinfra && python3 run_helios.py`** (from
`scarab-infra`; run inside the `scarabinfra` env so the graph step has matplotlib/numpy). This one command:
materializes the 6 group descriptors from `json/HELIOS.json`; builds scarab from I-Fuse if the
binary isn't cached (`scarab_builds/scarab_current.opt`); runs each group (`./sci --sim` /
`--collect-stats` / `--visualize` — 32 sims = baseline + each app's tuning, no full sweep); and
then **automatically writes `benefit_latency.png` + `fusion_breakdown.png`** into the campaign
`root_dir` via `helios_plots.py`. (Graphs can be regenerated alone with `python3 helios_plots.py`.)

**3. Results.** Per-group `--visualize` prints a `baseline` vs `helios_T*_ns` IPC table with
speedup %, weighted over each app's `helios_dc` simpoints; the two PNGs land in `root_dir`. Match
each app to its column via `HELIOS.json → helios_optimal.per_app_optimal[].helios_config`.

## Expected headline

12/16 beat baseline (pagerank **+18.75%**, cc +9.38%, cd +5.71%, swe_agent +2.57%, bfs +2.28%,
dfs +1.69%, bc +0.53%, mongodb +0.39%, chemcrow +0.37%, langchain_web/mysql +0.01%,
postgres +0.00%). The other 4 run at T65000 and land ~baseline (tc −0.57%, rag_haystack −0.43%,
sssp_ego_fb −0.01%, toolformer 0.00%).
