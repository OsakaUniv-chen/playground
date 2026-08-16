"""Emulate an Intel N100 mini-PC (the intended field machine) on this workstation.

The question this folder exists to answer is "which generator can hold the 4 Hz
tick on the cheap passively-cooled box we would actually bolt to the robot",
and the cheap box is an N100. Rather than guess with a paper scaling factor,
this module carves an N100-shaped machine out of the host CPU and runs the real
generators inside it.

    N100 (what we are emulating)          this emulation (Core Ultra 9 185H)
    ------------------------------------  -----------------------------------------
    4x Gracemont E-core, no SMT           4x Crestmont E-core (cpu12-15), no SMT
    one 4-core cluster, 2 MB shared L2    one 4-core cluster, 2 MB shared L2
    3.4 GHz max turbo (all-core)          scaling_min = scaling_max = 3.4 GHz
    AVX2, no AVX-512                      E-cores are AVX2-only as well
    6 MB L3                               24 MB L3, shared with the rest of the SoC
    1x DDR4-3200 / DDR5-4800              LPDDR5x, wider
    6 W package TDP, passive              no package power limit on 4 idle-ish cores

Crestmont is Gracemont's direct successor (~5% IPC apart) and cpu12-15 form one
physical E-core cluster behind a single 2 MB L2 -- the same shape as an N100's
whole compute die. The first four rows are therefore close to exact; the last
three are all *optimistic*, so a time measured here is a **lower bound** on the
real N100 time, not an unbiased estimate. That matters unevenly between the two
generators -- see the README's "how much to trust these numbers".

Usage (the pinning and the thread caps must both happen before numpy/numba/
acoular are imported, so this has to be the first thing a script does):

    import n100
    n100.activate_from_argv()      # honours --host / --cores on the command line
    import acoular                 # ... then everything else

The 3.4 GHz clamp is the one part that needs root. `activate()` does not try to
escalate: it reports whether the clamp is in place and prints the `n100_lock.sh`
command that sets it, and every result file records `freq_locked` so a run made
without the clamp can never be mistaken for one made with it.
"""
from __future__ import annotations

import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))

# ==== the target machine ==================================================
N100_KHZ = 3_400_000              # N100 max turbo; all-core == single-core
N100_NCORES = 4
# cpu12-15 on this host: one Crestmont E-core cluster, no SMT, shared 2 MB L2.
# (cpu0-11 are SMT P-cores at 4.8 GHz, cpu20-21 the 2.5 GHz LP-E pair -- neither
# is N100-shaped.) Override with --cores if the host topology ever changes.
DEFAULT_CORES = (12, 13, 14, 15)
TICK_MS = 250.0                   # the 4 Hz output tick the field pipeline owes
# ==========================================================================

# Capped before numpy/numba get a chance to size their pools off nproc (22 here).
# OPENBLAS is deliberately NOT in this list: acoular's configuration.py pins
# numba to a single thread whenever it finds numpy already loaded against a
# multi-threaded OpenBLAS, so OpenBLAS gets 1 and numba gets the 4 cores. That
# is acoular's own recommendation and its fastest configuration here.
_THREAD_VARS = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS",
                "VECLIB_MAXIMUM_THREADS", "NUMBA_NUM_THREADS")

# Importing any of these fixes thread-pool sizes (and, for acoular, the numba
# thread count), so activate() has to run first or it silently does nothing.
_TOO_LATE = ("numpy", "numba", "torch", "acoular")

STATE = {"active": False, "cores": None, "threads": None, "freq_locked": None,
         "freq_limits": None}


# --------------------------------------------------------------------------
# cpufreq inspection (read-only; the writing side lives in n100_lock.sh)
# --------------------------------------------------------------------------
def _cpufreq(cpu: int, attr: str):
    try:
        with open(f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/{attr}") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def freq_limits(cores=DEFAULT_CORES):
    """[(cpu, scaling_min_khz, scaling_max_khz), ...] for the emulated cores."""
    return [(c, _cpufreq(c, "scaling_min_freq"), _cpufreq(c, "scaling_max_freq"))
            for c in cores]


def is_locked(cores=DEFAULT_CORES, khz: int = N100_KHZ) -> bool:
    """True only if every emulated core is pinned to exactly `khz` (min == max).

    Clamping just the maximum is not enough: the governor would still be free to
    drop below 3.4 GHz and make the emulated N100 look slower than it is, so the
    lock sets both ends and this checks both.
    """
    lim = freq_limits(cores)
    return all(lo == khz and hi == khz for _, lo, hi in lim)


def lock_command(cores=DEFAULT_CORES, khz: int = N100_KHZ) -> str:
    return (f"sudo CORES='{' '.join(str(c) for c in cores)}' KHZ={khz} "
            f"bash {os.path.join(HERE, 'n100_lock.sh')} lock")


class FreqSampler:
    """Background poll of scaling_cur_freq on the emulated cores while we time.

    Cheap insurance against a silently-throttled run: if the reported mean lands
    well under 3.4 GHz, the timings are of some other machine than the one the
    report claims.  Sampling costs one small sysfs read per core per interval.
    """

    def __init__(self, cores=DEFAULT_CORES, interval: float = 0.1):
        self.cores = tuple(cores)
        self.interval = interval
        self._samples: list[float] = []
        self._stop = threading.Event()
        self._thread = None

    def _run(self):
        while not self._stop.wait(self.interval):
            vals = [_cpufreq(c, "scaling_cur_freq") for c in self.cores]
            vals = [v for v in vals if v]
            if vals:
                self._samples.append(max(vals) / 1000.0)   # MHz, busiest core

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> dict:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2 * self.interval + 1.0)
        s = self._samples
        if not s:
            return {"n": 0, "mean_mhz": None, "min_mhz": None, "max_mhz": None}
        return {"n": len(s), "mean_mhz": sum(s) / len(s),
                "min_mhz": min(s), "max_mhz": max(s)}


# --------------------------------------------------------------------------
# activation
# --------------------------------------------------------------------------
def parse_cores(spec: str):
    """'12-15' or '12,13,14,15' (or a mix) -> tuple[int, ...]."""
    out = []
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return tuple(out)


def activate(cores=DEFAULT_CORES, threads: int | None = None, verbose: bool = True) -> dict:
    """Pin this process to `cores` and cap every thread pool to len(cores).

    Raises if numpy/numba/torch/acoular are already imported: at that point the
    thread pools are sized and the cap would be a no-op that quietly produced
    wrong numbers, which is worse than failing.
    """
    late = [m for m in _TOO_LATE if m in sys.modules]
    if late:
        raise RuntimeError(
            f"n100.activate() must run before {', '.join(late)} are imported "
            "(thread-pool sizes and acoular's numba thread count are fixed at "
            "import time). Move the n100 import to the top of the script.")

    cores = tuple(cores)
    threads = threads or len(cores)
    for var in _THREAD_VARS:
        os.environ[var] = str(threads)
    os.environ["OPENBLAS_NUM_THREADS"] = "1"     # see _THREAD_VARS comment
    os.sched_setaffinity(0, set(cores))

    STATE.update(active=True, cores=cores, threads=threads,
                 freq_locked=is_locked(cores), freq_limits=freq_limits(cores))
    if verbose:
        print(banner())
    return dict(STATE)


def activate_from_argv(argv=None) -> dict:
    """activate() unless `--host` is on the command line. Reads --cores/--threads.

    Peeking at argv instead of parsing it properly is the price of having to run
    before numpy exists; each script re-declares these flags in its own argparse
    so `--help` still documents them.
    """
    argv = list(sys.argv if argv is None else argv)

    def _opt(name, default=None):
        if name in argv:
            i = argv.index(name)
            return argv[i + 1] if i + 1 < len(argv) else default
        for a in argv:
            if a.startswith(name + "="):
                return a.split("=", 1)[1]
        return default

    if "--host" in argv:
        STATE.update(active=False, cores=None, threads=None,
                     freq_locked=None, freq_limits=None)
        print("[n100] --host: running unconstrained on the full workstation "
              f"({os.cpu_count()} threads) -- these are NOT N100 numbers.")
        return dict(STATE)

    cores_spec = _opt("--cores")
    cores = parse_cores(cores_spec) if cores_spec else DEFAULT_CORES
    threads = _opt("--threads")
    return activate(cores, int(threads) if threads else None)


def add_arguments(ap):
    """Register the flags activate_from_argv() consumes, so --help lists them."""
    ap.add_argument("--host", action="store_true",
                    help="skip the N100 emulation and run on the whole workstation "
                         "(for a host-vs-N100 reference number, not a field result)")
    ap.add_argument("--cores", default=",".join(str(c) for c in DEFAULT_CORES),
                    help="host CPUs to emulate the N100 with; must be one full "
                         "E-core cluster (default: %(default)s)")
    ap.add_argument("--threads", type=int, default=None,
                    help="thread-pool cap (default: one per emulated core)")


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------
def label() -> str:
    if not STATE["active"]:
        return f"HOST ({os.cpu_count()} threads, unconstrained)"
    lock = "3.4GHz locked" if STATE["freq_locked"] else "UNLOCKED clock"
    return f"N100-sim ({len(STATE['cores'])}x E-core, {lock})"


def banner() -> str:
    if not STATE["active"]:
        return "[n100] inactive (host mode)"
    cores = STATE["cores"]
    lines = [f"[n100] emulating an Intel N100: cpus {','.join(map(str, cores))}, "
             f"{STATE['threads']} threads, target {N100_KHZ / 1e6:.1f} GHz"]
    if STATE["freq_locked"]:
        lines.append(f"[n100] clock locked at {N100_KHZ / 1e6:.1f} GHz -- good")
    else:
        lim = STATE["freq_limits"] or []
        shown = ", ".join(f"cpu{c}:{(lo or 0)/1e6:.1f}-{(hi or 0)/1e6:.1f}GHz"
                          for c, lo, hi in lim)
        lines.append(f"[n100] WARNING: clock NOT locked ({shown}).")
        lines.append(f"[n100]   these cores boost to 3.8 GHz, so results will be "
                     f"OPTIMISTIC by roughly 3.8/3.4 = 1.12x. To lock:")
        lines.append(f"[n100]   {lock_command(cores)}")
    return "\n".join(lines)


def describe() -> dict:
    """Machine description to embed in every result file."""
    d = {"mode": "n100-sim" if STATE["active"] else "host",
         "label": label(),
         "host_cpu": _host_cpu_model(),
         "target_khz": N100_KHZ,
         "tick_ms": TICK_MS}
    if STATE["active"]:
        d.update(cores=list(STATE["cores"]), threads=STATE["threads"],
                 freq_locked=bool(STATE["freq_locked"]),
                 freq_limits=[{"cpu": c, "min_khz": lo, "max_khz": hi}
                              for c, lo, hi in (STATE["freq_limits"] or [])])
    else:
        d.update(cores=None, threads=os.cpu_count(), freq_locked=None)
    return d


def _host_cpu_model() -> str:
    try:
        with open("/proc/cpuinfo") as fh:
            for line in fh:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return "unknown"


def project_to_target(ms: float, achieved_mhz: float | None) -> float | None:
    """Frequency-normalise a measurement taken without the hard clock lock.

    Only meaningful for a clock-bound run and only as a fallback -- the point of
    n100_lock.sh is not to need this. Returns None if we have no frequency
    samples to normalise against.
    """
    if not achieved_mhz:
        return None
    return ms * achieved_mhz / (N100_KHZ / 1000.0)


if __name__ == "__main__":
    activate_from_argv()
    print()
    print(f"host cpu   : {_host_cpu_model()}")
    print(f"affinity   : {sorted(os.sched_getaffinity(0))}")
    print(f"lock cmd   : {lock_command()}")
    smp = FreqSampler().start()
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 1.0:      # a second of spin, to see the clock
        pass
    print(f"clock under load: {smp.stop()}")
