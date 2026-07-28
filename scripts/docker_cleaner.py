#!/usr/bin/env python3

import argparse
import os
import sys
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.utilities import run_on_node  # type: ignore
else:
    from .utilities import run_on_node

parser = argparse.ArgumentParser(description="Clean up dangling docker images.")

parser.add_argument('--images', nargs='+', type=str, required=True, help='Image+tag list (space-separated)')
parser.add_argument('--nodes', nargs='+', type=str, required=False, help='Node list (space-separated)')

args = parser.parse_args()
images = args.images
nodes = [None]
if args.nodes:
    nodes = nodes + args.nodes
print(images)
print(nodes)
# Keep reusable workload images between per-app sims unless explicitly disabled.
if os.environ.get("SCARAB_KEEP_DOCKER_IMAGES", "1") == "1":
    print(f"docker_cleaner: keeping images (SCARAB_KEEP_DOCKER_IMAGES=1): {images}")
else:
    for image_tag in images:
        for node in nodes:
            run_on_node(["docker", "rmi", image_tag], node, text=True)
