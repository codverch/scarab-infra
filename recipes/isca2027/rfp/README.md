# RFP

Register File Prefetching (Shukla et al., ISCA 2022), the final model closest to the paper. Each load's
address is predicted after rename from a PC-indexed stride table. The prefetch then reads the cache on a
free load port and writes the load's physical register.

| Item | Value |
|---|---|
| Scarab | codverch/ifuse branch `isca2027-rfp` @ `1aaf11b` |
| Binary | `scarab_1aaf11b_0.opt` |
| Build tree | `/hot/deepanjm/isca2027/scarab-rfp` |
| Configurations | `rfp` |
| Experiment | `/hot/deepanjm/isca2027/simulations/isca2027-rfp-final` |

## Model

The paper-vs-model table, with every departure and its direction, is `RFP_PAPER_VS_MODEL.md` in the
build tree. Main settings:

- 1K-entry, 8-way prefetch table with the full 64-bit virtual address.
- Hermes/Pythia PC hash (`--rfp_pc_hash 2`); the paper does not name an index.
- 8-bit stride, 1-bit confidence trained at retirement.
- Memory-dependence prediction for unknown-address stores.
- L1-missing prefetches continue to L2/LLC and train the lower-level prefetchers like demand loads.

The L1-D latency is 4 cycles, the same core as every other recipe, so a served L1 hit saves at most 3
cycles. The paper uses 5.

## Run

```bash
./sci --build-scarab isca2027/recipe-rfp
./sci --sim isca2027/recipe-rfp
./sci --collect-stats isca2027/recipe-rfp
```

## Provenance and validation

- The model is commit `077a113` (previously `26ac1d4`), which produced `isca2027-rfp-final`.
- The two commits after it (`182fa05` "count served loads and load latency by cache level" and `1aaf11b`,
  the per-load dump) only add statistics.
- A build of the same tree reproduced `isca2027-rfp-final` cycles exactly on the heaviest SimPoint of
  appworld, bfs and cachebench.
- The branch's commits were re-authored (codverch) and the `wip` port commit folded into
  `feat: port RFP onto baseline 89b5145`. Each commit's file tree is unchanged.
- The experiment name `isca2027-rfp` belongs to an older RFP model (`json/isca2027/rfp.json`), so this
  recipe keeps `isca2027-rfp-final`.
