#!/usr/bin/env python3
"""Fix McPAT bank counts in existing mcpat_infile.xml and regenerate mcpat.out."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

MCPAT = Path("/users/deepmish/toolchain/bin/mcpat")
CACTI = Path("/users/deepmish/toolchain/bin/cacti")
INFRA = Path("/users/deepmish/scarab-infra")
IMAGE = None


def docker_image() -> str:
    global IMAGE
    if IMAGE:
        return IMAGE
    h = subprocess.check_output(
        ["git", "-C", str(INFRA), "rev-parse", "--short", "HEAD"], text=True
    ).strip()
    IMAGE = f"allbench_traces:{h}"
    return IMAGE


def fix_xml(xml: Path) -> bool:
    t = xml.read_text()
    orig = t

    def banks1(m: re.Match) -> str:
        parts = m.group(2).split(",")
        if len(parts) >= 4 and parts[3].strip() != "1":
            parts[3] = "1"
        return f'{m.group(1)}value="{",".join(parts)}"'

    t = re.sub(
        r'(name="(?:icache_config|dcache_config)"\s+)value="([^"]+)"',
        banks1,
        t,
    )
    if t != orig:
        xml.write_text(t)
        return True
    return False


def rerun_mcpat(sim_dir: Path) -> bool:
    xml = sim_dir / "mcpat_infile.xml"
    if not xml.is_file():
        return False
    fix_xml(xml)
    out = sim_dir / "mcpat.out"
    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{sim_dir}:/work",
        "-v",
        f"{MCPAT.parent}:/binaries:ro",
        docker_image(),
        "bash",
        "-lc",
        "/binaries/mcpat -infile /work/mcpat_infile.xml -print_level 1 > /work/mcpat.out 2>/work/mcpat.err; "
        "ec=$?; "
        # Also try power_intf.pl if present
        "if [[ -x /work/../../scarab_stage/helios/scarab/bin/power/power_intf.pl ]]; then true; fi; "
        "exit $ec",
    ]
    # Simpler: just mcpat
    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{sim_dir.resolve()}:/work",
        "-v",
        f"{MCPAT.parent.resolve()}:/binaries:ro",
        docker_image(),
        "bash",
        "-lc",
        "cd /work && /binaries/mcpat -infile mcpat_infile.xml -print_level 1 > mcpat.out 2>mcpat.err; "
        "tail -5 mcpat.err; wc -c mcpat.out; grep -q 'Helios Fusion' mcpat.out",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    ok = out.is_file() and out.stat().st_size > 1000 and "Helios Fusion" in out.read_text(
        errors="ignore"
    )
    print(f"{'OK' if ok else 'FAIL'} {sim_dir} size={out.stat().st_size if out.exists() else 0}")
    if not ok:
        print(r.stdout[-500:], r.stderr[-500:])
    return ok


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "/users/deepmish/scarab/src/simulations")
    oks = fails = 0
    for xml in sorted(root.rglob("mcpat_infile.xml")):
        if rerun_mcpat(xml.parent):
            oks += 1
        else:
            fails += 1
    print(f"done ok={oks} fail={fails}")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
