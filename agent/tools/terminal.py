"""The Terminal_Executor tool: command execution and resource reporting.

Implements Requirement 9:

* :meth:`TerminalExecutor.run_command` -- run a shell command, returning
  stdout and stderr each capped at 1 megabyte, the exit code, and a
  timed-out flag; a command that exceeds the timeout is terminated and its
  partial output returned (R9.1, R9.6, R9.7).
* :meth:`TerminalExecutor.get_processes` -- list running processes with pid and
  command name (R9.2).
* :meth:`TerminalExecutor.check_disk_usage` -- used/available MB per mounted
  filesystem (R9.3).
* :meth:`TerminalExecutor.check_memory_usage` -- used/available memory in MB
  (R9.4).
* :meth:`TerminalExecutor.check_cpu_usage` -- processor utilization sampled over
  an interval, returned as a percentage in [0, 100] (R9.5).
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Optional, Protocol

from agent.models import ToolResult

# Maximum number of bytes of stdout/stderr retained from a command (R9.1).
MAX_OUTPUT_BYTES: int = 1_000_000

# Default command timeout in seconds (R9.7).
DEFAULT_COMMAND_TIMEOUT: float = 300.0

# Bytes per megabyte used for resource reporting conversions.
BYTES_PER_MB: int = 1024 * 1024


class _SupportsLogging(Protocol):
    """Minimal logger contract used for command auditing (R20.2, R20.3)."""

    def log_command(self, command: str, **kwargs: Any) -> Any: ...

    def log_command_result(self, exit_code: int, output: str, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class _CpuTimes:
    """Aggregate CPU jiffies parsed from ``/proc/stat``."""

    idle: int
    total: int


def _cap_bytes(raw: bytes) -> str:
    """Cap ``raw`` at :data:`MAX_OUTPUT_BYTES` bytes and decode to text (R9.1)."""
    if len(raw) > MAX_OUTPUT_BYTES:
        raw = raw[:MAX_OUTPUT_BYTES]
    return raw.decode("utf-8", errors="replace")


class TerminalExecutor:
    """Runs shell commands and reports system resource usage on the local VPS."""

    def __init__(self, logger: Optional[_SupportsLogging] = None) -> None:
        self._logger = logger

    # -- Command execution (R9.1, R9.6, R9.7) ------------------------------ #

    def run_command(
        self, command: str, timeout: float = DEFAULT_COMMAND_TIMEOUT
    ) -> ToolResult:
        """Execute ``command`` on the local shell and capture its output.

        Returns a :class:`~agent.models.ToolResult` whose ``data`` always
        carries ``stdout``, ``stderr``, ``exit_code`` and ``timed_out``. The
        result is successful only when the command completed within the timeout
        with a zero exit code; a non-zero exit returns the exit code and stderr
        (R9.6) and a timeout returns the partial output captured before
        termination (R9.7).
        """
        if self._logger is not None:
            self._logger.log_command(command)

        process = subprocess.Popen(  # noqa: S602 - shell execution is the tool's purpose
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        timed_out = False
        try:
            out_bytes, err_bytes = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            out_bytes, err_bytes = process.communicate()
            timed_out = True

        stdout = _cap_bytes(out_bytes or b"")
        stderr = _cap_bytes(err_bytes or b"")
        exit_code = process.returncode

        if self._logger is not None:
            self._logger.log_command_result(exit_code if exit_code is not None else -1, stdout)

        data = {
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "timed_out": timed_out,
        }
        if timed_out:
            return ToolResult.fail(
                f"Command terminated after exceeding the {timeout:g}s timeout.",
                data=data,
            )
        if exit_code != 0:
            return ToolResult.fail(
                f"Command exited with non-zero status {exit_code}: {stderr.strip()}",
                data=data,
            )
        return ToolResult.ok(data)

    # -- Process listing (R9.2) ------------------------------------------- #

    def get_processes(self) -> ToolResult:
        """Return running processes as ``{pid, name}`` records (R9.2)."""
        processes: list[dict[str, Any]] = []
        proc_root = "/proc"
        if os.path.isdir(proc_root):
            for entry in os.listdir(proc_root):
                if not entry.isdigit():
                    continue
                comm_path = os.path.join(proc_root, entry, "comm")
                try:
                    with open(comm_path, "r", encoding="utf-8", errors="replace") as handle:
                        name = handle.read().strip()
                except OSError:
                    # The process may have exited between listing and reading.
                    continue
                processes.append({"pid": int(entry), "name": name})
            return ToolResult.ok({"processes": processes})

        # Fallback for non-/proc hosts: use ``ps``.
        result = self.run_command("ps -e -o pid=,comm=")
        if not result.success:
            return ToolResult.fail("Failed to enumerate processes.", data=result.data)
        for line in result.data["stdout"].splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) == 2 and parts[0].isdigit():
                processes.append({"pid": int(parts[0]), "name": parts[1]})
        return ToolResult.ok({"processes": processes})

    # -- Disk usage (R9.3) ------------------------------------------------- #

    def check_disk_usage(self) -> ToolResult:
        """Return used/available MB for each mounted filesystem (R9.3)."""
        filesystems: list[dict[str, Any]] = []
        seen: set[str] = set()
        for device, mountpoint in self._iter_mounts():
            if mountpoint in seen:
                continue
            seen.add(mountpoint)
            try:
                usage = os.statvfs(mountpoint)
            except OSError:
                continue
            block = usage.f_frsize
            total = usage.f_blocks * block
            available = usage.f_bavail * block
            used = total - usage.f_bfree * block
            filesystems.append(
                {
                    "filesystem": device,
                    "mountpoint": mountpoint,
                    "used_mb": round(used / BYTES_PER_MB, 2),
                    "available_mb": round(available / BYTES_PER_MB, 2),
                }
            )
        return ToolResult.ok({"filesystems": filesystems})

    @staticmethod
    def _iter_mounts() -> list[tuple[str, str]]:
        """Yield ``(device, mountpoint)`` pairs from ``/proc/mounts``."""
        mounts: list[tuple[str, str]] = []
        try:
            with open("/proc/mounts", "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    fields = line.split()
                    if len(fields) >= 2:
                        device = fields[0]
                        mountpoint = fields[1].replace("\\040", " ")
                        mounts.append((device, mountpoint))
        except OSError:
            mounts.append(("/", "/"))
        return mounts

    # -- Memory usage (R9.4) ---------------------------------------------- #

    def check_memory_usage(self) -> ToolResult:
        """Return used/available memory in MB (R9.4)."""
        meminfo = self._read_meminfo()
        if meminfo is None:
            return ToolResult.fail("Memory information is unavailable on this host.")
        total_kb = meminfo.get("MemTotal", 0)
        available_kb = meminfo.get("MemAvailable")
        if available_kb is None:
            # Older kernels: approximate available as free + buffers + cached.
            available_kb = (
                meminfo.get("MemFree", 0)
                + meminfo.get("Buffers", 0)
                + meminfo.get("Cached", 0)
            )
        used_kb = max(0, total_kb - available_kb)
        return ToolResult.ok(
            {
                "total_mb": round(total_kb / 1024, 2),
                "used_mb": round(used_kb / 1024, 2),
                "available_mb": round(available_kb / 1024, 2),
            }
        )

    @staticmethod
    def _read_meminfo() -> Optional[dict[str, int]]:
        """Parse ``/proc/meminfo`` into a ``{key: kilobytes}`` mapping."""
        info: dict[str, int] = {}
        try:
            with open("/proc/meminfo", "r", encoding="utf-8") as handle:
                for line in handle:
                    key, _, rest = line.partition(":")
                    fields = rest.split()
                    if fields and fields[0].isdigit():
                        info[key.strip()] = int(fields[0])
        except OSError:
            return None
        return info or None

    # -- CPU usage (R9.5) -------------------------------------------------- #

    def check_cpu_usage(self, interval: float = 1.0) -> ToolResult:
        """Sample CPU utilization over ``interval`` seconds (R9.5).

        Returns a percentage clamped to the closed interval [0, 100].
        """
        first = self._read_cpu_times()
        if first is None:
            return self._cpu_usage_fallback()
        if interval > 0:
            time.sleep(interval)
        second = self._read_cpu_times()
        if second is None:
            return self._cpu_usage_fallback()

        idle_delta = second.idle - first.idle
        total_delta = second.total - first.total
        if total_delta <= 0:
            usage = 0.0
        else:
            usage = (1.0 - idle_delta / total_delta) * 100.0
        usage = max(0.0, min(100.0, usage))
        return ToolResult.ok({"cpu_percent": round(usage, 2)})

    @staticmethod
    def _read_cpu_times() -> Optional[_CpuTimes]:
        """Parse the aggregate ``cpu`` line from ``/proc/stat``."""
        try:
            with open("/proc/stat", "r", encoding="utf-8") as handle:
                first_line = handle.readline()
        except OSError:
            return None
        if not first_line.startswith("cpu "):
            return None
        fields = [int(value) for value in first_line.split()[1:]]
        if len(fields) < 5:
            return None
        # Fields: user nice system idle iowait irq softirq steal guest guest_nice
        idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
        total = sum(fields)
        return _CpuTimes(idle=idle, total=total)

    @staticmethod
    def _cpu_usage_fallback() -> ToolResult:
        """Estimate CPU usage from load average when ``/proc/stat`` is absent."""
        try:
            load1, _load5, _load15 = os.getloadavg()
            cpu_count = os.cpu_count() or 1
            usage = max(0.0, min(100.0, (load1 / cpu_count) * 100.0))
        except (OSError, AttributeError):
            usage = 0.0
        return ToolResult.ok({"cpu_percent": round(usage, 2)})
