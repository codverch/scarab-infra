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
and `/users/vedlaksh/helios_results` — edit `scarab_path`/`root_dir` in `HELIOS.json` if different).

**2. Traces.** Two steps — download, then wire into the `helios_dc` suite:
- **Download** the HF dataset `harry1332/ifuse-final-datacenter-traces-20260624` (16 apps; the 5
  agentic apps store `traces_simp/<id>.zip`, the rest `traces_simp/trace/<id>.zip`), e.g.
  `hf download harry1332/ifuse-final-datacenter-traces-20260624 --repo-type dataset --local-dir <DL>`.
- **Wire** each app under `traces_dir` as the suite path `sci` derives from
  `workload_home = suite/subsuite/workload` (`local_runner.py:213`): the descriptors use
  `traces_dir: /dev/shm/baseline` and suite/subsuite `helios_dc/helios_dc`, so every app must be
  reachable at `/dev/shm/baseline/helios_dc/helios_dc/<app>/` (symlinks into `<DL>/<app>` are
  fine). `setup_scarab-3.sh` (HF dataset default now points at the harry1332 set) downloads the
  raw traces into `traces_dir`; creating the per-app `helios_dc/helios_dc/<app>/` suite links is
  the remaining wiring step.

**3. Materialize the group descriptors and build scarab from I-Fuse:**
```bash
cd scarab-infra
python3 -c "import json,pathlib; b=json.load(open('json/HELIOS.json')); [pathlib.Path('json/%s.json'%d['experiment']).write_text(json.dumps(d,indent=2)) for d in b['runs']]"
./sci --build-scarab HELIOS_T3      # builds I-Fuse@helios-2026 -> scarab_builds/scarab_current.opt
```

**4. Run all 6 groups** (baseline + each app's tuning = 32 sims, no full sweep):
```bash
for e in HELIOS_T3 HELIOS_T10 HELIOS_T1000 HELIOS_T10000 HELIOS_T30000 HELIOS_T65000; do
  ./sci --sim "$e" && ./sci --collect-stats "$e" && ./sci --visualize "$e"
done
```
(Equivalent to the one-liner in `HELIOS.json._run_loop`, which also does the materialize step.)

**5. Read results.** Each `--visualize` prints a `baseline` vs `helios_T*_ns` IPC table with
speedup %, weighted over the app's `helios_dc` simpoints. Match each app to its column via
`HELIOS.json → helios_optimal.per_app_optimal[].helios_config`.

## Expected headline

12/16 beat baseline (pagerank **+18.75%**, cc +9.38%, cd +5.71%, swe_agent +2.57%, bfs +2.28%,
dfs +1.69%, bc +0.53%, mongodb +0.39%, chemcrow +0.37%, langchain_web/mysql +0.01%,
postgres +0.00%). The other 4 run at T65000 and land ~baseline (tc −0.57%, rag_haystack −0.43%,
sssp_ego_fb −0.01%, toolformer 0.00%).
