"""Safety monitor for DrugCLIP — prevents HPC overload from runaway SSH calls.

Mirrors app_ProtPrep/modules/safety_monitor.py.
Thresholds: 20/min, 50/5min, 200/hr.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class SafetyMonitor:
    MAX_SSH_PER_MINUTE = 20
    MAX_SSH_PER_5MIN   = 50
    MAX_SSH_PER_HOUR   = 200

    def __init__(self, app_name: str = "drugclip", check_interval: int = 30):
        self.app_name = app_name
        self.check_interval = check_interval
        self.ssh_connection_times: list[datetime] = []
        self.start_time = datetime.now()
        self.shutdown_triggered = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def record_ssh_connection(self) -> None:
        now = datetime.now()
        self.ssh_connection_times.append(now)
        cutoff = now - timedelta(hours=1)
        self.ssh_connection_times = [t for t in self.ssh_connection_times if t > cutoff]

    def check_safety(self) -> tuple[bool, str]:
        now = datetime.now()
        per_minute = [t for t in self.ssh_connection_times if t > now - timedelta(minutes=1)]
        if len(per_minute) > self.MAX_SSH_PER_MINUTE:
            return False, f"Too many SSH connections in the last minute: {len(per_minute)} (max {self.MAX_SSH_PER_MINUTE})"
        per_5min = [t for t in self.ssh_connection_times if t > now - timedelta(minutes=5)]
        if len(per_5min) > self.MAX_SSH_PER_5MIN:
            return False, f"Too many SSH connections in the last 5 minutes: {len(per_5min)} (max {self.MAX_SSH_PER_5MIN})"
        if len(self.ssh_connection_times) > self.MAX_SSH_PER_HOUR:
            return False, f"Too many SSH connections in the last hour: {len(self.ssh_connection_times)} (max {self.MAX_SSH_PER_HOUR})"
        try:
            result = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=5)
            pids = set()
            for line in result.stdout.splitlines():
                if "python" in line.lower() and "webapp/app.py" in line:
                    parts = line.split()
                    if len(parts) > 1:
                        pids.add(parts[1])
            if len(pids) > 4:
                return False, f"Too many app processes detected: {len(pids)} (max 4)"
        except Exception as exc:
            logger.warning(f"Safety monitor: could not check process count: {exc}")
        return True, "All safety checks passed"

    def emergency_shutdown(self, reason: str) -> None:
        if self.shutdown_triggered:
            return
        self.shutdown_triggered = True
        logger.critical("=" * 70)
        logger.critical(f"EMERGENCY SHUTDOWN — {self.app_name}")
        logger.critical(f"Reason : {reason}")
        logger.critical(f"Time   : {datetime.now()}")
        logger.critical(f"SSH/1h : {len(self.ssh_connection_times)}")
        logger.critical("=" * 70)
        try:
            from webapp.modules.auth import send_email_via_hpc
            send_email_via_hpc(
                "yewmun.yip@crick.ac.uk",
                f"[ALERT] {self.app_name} emergency shutdown",
                (
                    f"{self.app_name} has shut itself down to prevent HPC overload.\n\n"
                    f"Reason : {reason}\n"
                    f"Time   : {datetime.now()}\n"
                    f"SSH/1h : {len(self.ssh_connection_times)}\n"
                    f"Uptime : {datetime.now() - self.start_time}\n\n"
                    f"Investigate before restarting.\n"
                    f"Logs: logs/prod/drugclip.log\n\n"
                    f"— DrugCLIP safety monitor"
                ),
            )
        except Exception as exc:
            logger.error(f"Safety monitor: could not send alert email: {exc}")
        time.sleep(2)
        try:
            self.stop()
        except Exception:
            pass
        logger.critical("Forcing process exit.")
        os._exit(1)

    def _monitor_loop(self) -> None:
        logger.info(
            f"Safety monitor started for {self.app_name} "
            f"(interval: {self.check_interval}s, "
            f"limits: {self.MAX_SSH_PER_MINUTE}/min, "
            f"{self.MAX_SSH_PER_5MIN}/5min, "
            f"{self.MAX_SSH_PER_HOUR}/hr)"
        )
        while not self._stop_event.is_set():
            try:
                is_safe, reason = self.check_safety()
                if not is_safe:
                    self.emergency_shutdown(reason)
                    break
                runtime = datetime.now() - self.start_time
                if int(runtime.total_seconds()) % 300 < self.check_interval:
                    logger.info(f"Safety OK — uptime {runtime}, SSH/1h: {len(self.ssh_connection_times)}")
            except Exception as exc:
                logger.error(f"Safety monitor loop error: {exc}")
            self._stop_event.wait(self.check_interval)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name=f"{self.app_name}-safety-monitor",
        )
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop_event.set()
        self._thread.join(timeout=5)
        self._thread = None


_monitor: SafetyMonitor | None = None


def get_safety_monitor() -> SafetyMonitor:
    global _monitor
    if _monitor is None:
        _monitor = SafetyMonitor(app_name="drugclip")
    return _monitor


def start_safety_monitor() -> SafetyMonitor:
    monitor = get_safety_monitor()
    monitor.start()
    return monitor
