# BFS weighted backend-bound verification

The workload result uses normalized SimPoint weights:

`BFS backend bound = sum((weight_i / sum(weights)) * backend_i)`

The DB weights sum to `1.0000003`, so the explicit contributions are:

| Cluster | DB weight | Backend bound (%) | Weighted contribution |
|---:|---:|---:|---:|
| 17 | 0.8859980 | 36.632450324 | 32.456267985 |
| 94 | 0.0288912 | 81.057680785 | 2.341852965 |
| 96 | 0.0674129 | 1.317026718 | 0.088784564 |
| 103 | 0.0176982 | 2.504480003 | 0.044324775 |

The normalized contribution sum is `34.931230288315%`, exactly matching the
BFS Backend bound value in `topdown_backend_stalls.csv`.
