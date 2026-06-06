"""Benchmark Blender cold startup cost and parallel startup contention."""
import concurrent.futures as cf
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BLENDER = HERE.parent.parent / "blender-5.0.0-windows-x64" / "blender.exe"


def one_startup(i: int) -> tuple[int, float, int]:
    t = time.time()
    p = subprocess.run(
        [str(BLENDER), "--background", "--factory-startup",
         "--python-expr", "print('ok')"],
        capture_output=True, text=True, timeout=120,
    )
    return i, time.time() - t, p.returncode


def main():
    print("--- sequential, 3 cold startups ---")
    for i in range(3):
        idx, dt, rc = one_startup(i)
        print(f"  run {idx}: {dt:.2f}s  exit={rc}")

    for n in (4, 10):
        print(f"\n--- parallel x{n} cold startups ---")
        t = time.time()
        with cf.ThreadPoolExecutor(max_workers=n) as ex:
            futs = [ex.submit(one_startup, i) for i in range(n)]
            for f in cf.as_completed(futs):
                idx, dt, rc = f.result()
                print(f"  run {idx}: {dt:.2f}s  exit={rc}")
        print(f"  wall total: {time.time()-t:.2f}s")


if __name__ == "__main__":
    main()
