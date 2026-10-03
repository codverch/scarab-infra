# HPCA 2027 revision descriptors

## baseline: Golden Cove, ROB 352 vs 512

| Config | Params |
|--------|--------|
| `baseline-rob352` | `PARAMS.golden_cove` + `--node_table_size 352` |
| `baseline-rob512` | `PARAMS.golden_cove` as-is (`node_table_size 512`) |

- **Workloads:** SPEC CPU2017 speed_int Helios fixed-region traces
  (`gcc_s gcc_s_2 gcc_s_3 leela_s mcf_s omnetpp_s xalancbmk_s`) from
  [harry1332/helios-spec2017-fixed-region-20261002](https://huggingface.co/datasets/harry1332/helios-spec2017-fixed-region-20261002),
  downloaded to `/dev/shm/baseline/<app>/traces/simp/20.zip`.
- **Window (Helios methodology):** 500M instructions from instruction 1, no warmup.
- **Scarab:** branch `hpca2027-revision-baseline`.

```bash
./json/hpca2027-revision/baseline.sh            # register traces + launch all 14 sims
./json/hpca2027-revision/baseline.sh --status
./json/hpca2027-revision/baseline.sh --package  # -> scarab/results/hpca2027-revision-baseline/
```

`--register` (run automatically) symlinks the traces as
`/dev/shm/baseline/spec2017/speed_int_helios/<app>` and records them in
`workloads/workloads_db.json`. Rerun it after a reboot wipes `/dev/shm`.

Raw simulation output: `/users/deepmish/hpca2027-revision/simulations/<config>/<app>/20/`.

## helios: Helios at ROB 512 vs 352

| Config | Params |
|--------|--------|
| `rob512_baseline` | `PARAMS.in` (Golden Cove, `node_table_size 512`), Helios off |
| `rob512_helios` | `PARAMS.in`, `--helios_do_fusion 1` (other Helios knobs at branch defaults) |
| `rob352_baseline` | `PARAMS.in` + `--node_table_size 352`, Helios off |
| `rob352_helios` | `PARAMS.in` + `--node_table_size 352 --helios_do_fusion 1` |

- **Workloads / window:** same traces and methodology as `baseline` above
  (500M instructions from instruction 1, no warmup).
- **Scarab:** branch `hpca2027-revision-helios`.

```bash
./json/hpca2027-revision/helios.sh                     # register traces + build + launch all 28 sims
./json/hpca2027-revision/helios.sh --status
./json/hpca2027-revision/helios.sh --package <dir>     # e.g. scarab/results/hpca2027-revision-helios
```

Raw simulation output: `/users/deepmish/hpca2027-revision-runs/simulations/<config>/<app>/20/`.
