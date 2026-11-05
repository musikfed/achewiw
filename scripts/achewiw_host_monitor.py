#!/usr/bin/env python3
"""Read-only host metrics for ACHEWIW. No GPU jobs, Docker or service control.

Use: python3 scripts/achewiw_host_monitor.py --once
Optional future supervised collection: --watch --interval 5
Writes ONLY /1tbdisk/achewiw/data/monitor/host.json.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
from pathlib import Path
import subprocess
import time

OUTPUT = Path("/1tbdisk/achewiw/data/monitor/host.json")


def _cpu_times() -> tuple[int, int]:
    first = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0].split()
    assert first[0] == "cpu"
    fields = [int(n) for n in first[1:]]
    idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
    return (sum(fields), idle)


def cpu_sample() -> dict:
    total1, idle1 = _cpu_times()
    time.sleep(0.3)
    total2, idle2 = _cpu_times()
    delta = max(1, total2 - total1)
    used = max(0, delta - (idle2 - idle1))
    load = os.getloadavg()
    return {
        "usage_percent": round(min(100, max(0, used / delta * 100)), 1),
        "logical_processors": os.cpu_count(),
        "load_1": round(load[0], 2),
        "load_5": round(load[1], 2),
        "load_15": round(load[2], 2),
    }


def memory_sample() -> dict:
    values = {}
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition(":")
        if key in ("MemTotal", "MemAvailable"):
            values[key] = int(value.split()[0]) * 1024
    total = values["MemTotal"]
    used = max(0, total - values["MemAvailable"])
    return {
        "total_bytes": total,
        "used_bytes": used,
        "usage_percent": round(used * 100 / total, 1) if total else None,
    }


def _number(value: str):
    try:
        return float(value.strip())
    except (ValueError, AttributeError):
        return None


def gpu_sample() -> tuple[list[dict], str]:
    query = (
        "index,name,utilization.gpu,memory.used,memory.total,temperature.gpu"
    )
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=" + query, "--format=csv,noheader,nounits"],
            capture_output=True, text=True, check=True, timeout=4,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
        return [], "unavailable"
    cards = []
    try:
        for values in csv.reader(io.StringIO(result.stdout)):
            if len(values) != 6:
                continue
            index, name, util, used, total, temp = (v.strip() for v in values)
            cards.append({
                "index": int(index),
                "name": name[:100],
                "utilization_percent": _number(util),
                "memory_used_bytes": (round(_number(used) * 1024**2)
                                      if _number(used) is not None else None),
                "memory_total_bytes": (round(_number(total) * 1024**2)
                                       if _number(total) is not None else None),
                "temperature_c": _number(temp),
            })
    except ValueError:
        return [], "unavailable"
    return cards, "ok" if cards else "unavailable"


def collect() -> dict:
    cpu = cpu_sample()
    ram = memory_sample()
    gpus, gpu_status = gpu_sample()
    return {
        "schema_version": 1,
        "captured_at": time.time(),
        "cpu": cpu,
        "memory": ram,
        "gpu_status": gpu_status,
        "gpus": gpus,
    }


def atomic_write(snapshot: dict) -> None:
    # The parent should be writable by the project's user, never root-owned.
    OUTPUT.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if OUTPUT.parent.is_symlink() or OUTPUT.is_symlink():
        raise RuntimeError("Refusing symlink telemetry path")
    temp = OUTPUT.parent / (".host.json." + str(os.getpid()) + ".tmp")
    try:
        fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(snapshot, stream, ensure_ascii=False, allow_nan=False)
        os.replace(temp, OUTPUT)
    finally:
        if temp.exists():
            temp.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description="ACHEWIW host monitoring (read-only)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="one snapshot, no daemon")
    mode.add_argument("--watch", action="store_true", help="repeat until Ctrl+C")
    parser.add_argument("--interval", type=int, default=5, choices=range(3, 61),
                        metavar="3..60")
    args = parser.parse_args()
    try:
        while True:
            sample = collect()
            atomic_write(sample)
            print("ACHEWIW: host snapshot saved, GPU:", sample["gpu_status"], flush=True)
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
