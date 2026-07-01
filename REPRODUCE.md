# Reproducing the HELIOS (store-store OFF) experiment

Baseline vs HELIOS at each app's optimal confidence tuning, Golden Cove, 16 datacenter apps.

## Two repos

| Repo | GitHub | Branch | Role |
|------|--------|--------|------|
| **I-Fuse** | `codverch/I-Fuse` | `helios-2026` | the HELIOS scarab simulator source + `PARAMS.in` (the Golden Cove config) |
| **scarab-infra** (this) | `codverch/scarab-infra` | `main` | run harness (`sci`), workload DB, the `HELIOS.json` experiment |

## Setup + run on a fresh node

**0. Bootstrap scarab-infra — `./sci --init`.** This installs Docker, configures the docker
socket, installs Miniconda if absent, and creates/updates the `scarabinfra` conda env from
`quickstart_env.yaml` (`sci` re-execs itself inside that env). The scarab build runs inside a
Docker image, so no host PIN/clang toolchain is required. (`/users/vedlaksh/setup_scarab-3.sh` is
an optional host helper — build deps + a 200 GB tmpfs at `/dev/shm/baseline` + trace download; it
does **not** set up Docker or conda.)

**1. Clone both repos** (any two dirs; paths inside `HELIOS.json` assume `/users/vedlaksh/I-Fuse`
and `/users/vedlaksh/I-Fuse/helios_results` (results land in the I-Fuse checkout; `simulations/`
is gitignored, the light artifacts are committed) — edit `scarab_path`/`root_dir` in `HELIOS.json`
if your clone paths differ.

**2. Traces.** Two steps — download, then wire into the `helios_dc` suite:
- **Download** the HF dataset `harry1332/ifuse-final-datacenter-traces-20260624` (16 apps; the 5
  agentic apps store `traces_simp/<id>.zip`, the rest `traces_simp/trace/<id>.zip`), e.g.
  `hf download harry1332/ifuse-final-datacenter-traces-20260624 --repo-type dataset --local-dir <DL>`.
- **Wire** the download into the suite layout `sci` expects (`workload_home =
  suite/subsuite/workload`, `local_runner.py:213`):
  `<traces_dir>/helios_dc/helios_dc/<app>/traces/simp/<cluster_id>.zip`. This is **automatic** —
  `run_helios.py` (step 3) runs `wire_helios_traces.py`, which hard-links each app's DB-selected
  simpoint zips from the download into the suite layout (hard links: no extra space, resolve
  inside the Docker mount; idempotent). To wire by hand:
  `python3 wire_helios_traces.py [--src <download_dir>]` (auto-detects `<traces_dir>/new_traces_dl`).

**3. Run the experiment — `conda activate scarabinfra && python3 run_helios.py`** (from
`scarab-infra`; run inside the `scarabinfra` env so the graph step has matplotlib/numpy). This one command:
materializes the 6 group descriptors from `json/HELIOS.json`; builds scarab from I-Fuse if the
binary isn't cached (`scarab_builds/scarab_current.opt`); runs each group (`./sci --sim` /
`--collect-stats` / `--visualize` — 32 sims = baseline + each app's tuning, no full sweep); and
then **automatically writes `benefit_latency.png` + `fusion_breakdown.png`** into the campaign
`root_dir` via `helios_plots.py`. (Graphs can be regenerated alone with `python3 helios_plots.py`.)

**4. Results.** Per-group `--visualize` prints a `baseline` vs `helios_T*_ns` IPC table with
speedup %, weighted over each app's `helios_dc` simpoints; the two PNGs land in `root_dir`. Match
each app to its column via `HELIOS.json → helios_optimal.per_app_optimal[].helios_config`.

## Expected headline

12/16 beat baseline (pagerank **+18.75%**, cc +9.38%, cd +5.71%, swe_agent +2.57%, bfs +2.28%,
dfs +1.69%, bc +0.53%, mongodb +0.39%, chemcrow +0.37%, langchain_web/mysql +0.01%,
postgres +0.00%). The other 4 run at T65000 and land ~baseline (tc −0.57%, rag_haystack −0.43%,
sssp_ego_fb −0.01%, toolformer 0.00%).
