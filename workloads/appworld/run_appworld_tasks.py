#!/usr/bin/env python3
"""
Drive AppWorld through its own ground-truth task solutions.

This exercises the real CPU-bound execution path of the AppWorld agentic
environment (task supervisor, simulated per-app FastAPI backends, SQLModel/
SQLite-backed app state, request/response validation) without depending on
any live LLM API call. It mirrors what `appworld verify` does internally
(appworld/verify.py: verify_tasks()), minus the pytest wrapper: for each task
id it loads the task's ground-truth solution code (the reference Python
program that calls AppWorld's simulated app APIs to complete the task) and
executes it inside the AppWorld sandbox, then evaluates it.

Task ids below were hand-picked (one per distinct required-app combination,
from the "difficulty 1" / "_1" variant of each scenario) to give SimPoint
clustering phase diversity across several different simulated apps (Spotify,
Venmo, Phone, SimpleNote, file_system) while keeping the total dynamic
instruction count of the run bounded (~35-40B instructions across 6 tasks,
measured with valgrind --tool=cachegrind), so fingerprinting/tracing stays
tractable.
"""
import argparse
import sys
import time

from appworld import AppWorld, load_task_ids

DEFAULT_TASK_IDS = [
    "82e2fac_1",  # spotify
    "2a163ab_1",  # phone, venmo
    "6104387_1",  # file_system, spotify
    "29caf6f_1",  # phone, simple_note
    "22cc237_1",  # phone, simple_note, venmo
    "76f2c72_1",  # file_system
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--splits", nargs="+", default=["train", "dev"])
    parser.add_argument("--repeat", type=int, default=1, help="Repeat the whole task set N times (to extend total dynamic instruction count).")
    parser.add_argument("--experiment-name", default="ifuse_trace")
    parser.add_argument("--limit", type=int, default=None, help="Only run the first N task ids (across all splits combined).")
    parser.add_argument("--task-ids", nargs="+", default=None, help="Explicit list of task ids to run instead of the default hand-picked set.")
    parser.add_argument("--all", action="store_true", help="Run the full train+dev task set instead of the default hand-picked subset.")
    args = parser.parse_args()

    if args.task_ids:
        task_ids = list(args.task_ids)
    elif args.all:
        task_ids = []
        for split in args.splits:
            task_ids.extend(load_task_ids(split))
        if args.limit:
            task_ids = task_ids[: args.limit]
    else:
        task_ids = DEFAULT_TASK_IDS

    print(f"[run_appworld_tasks] running {len(task_ids)} task ids, repeat={args.repeat}: {task_ids}", file=sys.stderr)

    passed = 0
    total = 0
    t0 = time.perf_counter()
    for rep in range(args.repeat):
        for task_id in task_ids:
            total += 1
            try:
                with AppWorld(
                    task_id=task_id,
                    experiment_name=args.experiment_name,
                    raise_on_failure=False,
                    ground_truth_mode="full",
                ) as world:
                    code = world.task.ground_truth.compiled_solution_code
                    code = code + "\nsolution(apis, requester)"
                    world.execute(code)
                    tracker = world.evaluate()
                    passed += int(tracker.success)
            except Exception as e:
                print(f"[run_appworld_tasks] task {task_id} rep {rep} raised: {e}", file=sys.stderr)
    t1 = time.perf_counter()
    print(f"[run_appworld_tasks] passed {passed}/{total} in {t1 - t0:.1f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
