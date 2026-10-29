"""Thin wrapper around a persistent ``exiftool -stay_open`` process."""

from __future__ import annotations

import html
import json
import subprocess
import threading
from pathlib import Path
from typing import Iterable

CONFIG_PATH = Path(__file__).parent / "exiftool_config.pl"


class ExifToolError(RuntimeError):
    pass


class ExifTool:
    def __init__(self, executable: str = "exiftool"):
        self.executable = executable
        self._proc: subprocess.Popen | None = None
        self._seq = 0
        self._stderr: list[str] = []
        self._lock = threading.Lock()

    def __enter__(self) -> "ExifTool":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def start(self) -> None:
        self._proc = subprocess.Popen(
            [self.executable, "-config", str(CONFIG_PATH), "-stay_open", "True", "-@", "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _drain_stderr(self) -> None:
        assert self._proc and self._proc.stderr
        for line in self._proc.stderr:
            self._stderr.append(line.rstrip("\n"))

    def close(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.stdin.write("-stay_open\nFalse\n")
            self._proc.stdin.flush()
            self._proc.wait(timeout=30)
        self._proc = None

    def execute(self, *args: str) -> tuple[str, list[str]]:
        """Run one exiftool command; returns (stdout, stderr lines)."""
        if self._proc is None:
            self.start()
        with self._lock:
            self._seq += 1
            marker = f"{{ready{self._seq}}}"
            self._stderr.clear()
            for a in args:
                if "\n" in a:
                    raise ValueError("argument contains a newline; escape it and pass -E")
                self._proc.stdin.write(a + "\n")
            self._proc.stdin.write(f"-execute{self._seq}\n")
            self._proc.stdin.flush()
            out: list[str] = []
            for line in self._proc.stdout:
                if line.rstrip("\n") == marker:
                    break
                out.append(line)
            # stderr is drained on another thread; give it a moment to catch up
            threading.Event().wait(0.01)
            return "".join(out), list(self._stderr)

    def read(self, files: Iterable[Path], tags: Iterable[str] = (), extra: Iterable[str] = ()) -> list[dict]:
        files = [str(f) for f in files]
        if not files:
            return []
        args = ["-j", "-n", "-G1", *extra, *(f"-{t}" for t in tags), *files]
        out, err = self.execute(*args)
        if not out.strip():
            raise ExifToolError("; ".join(err) or "exiftool returned nothing")
        return json.loads(out)

    def write(self, target: Path, args: Iterable[str]) -> None:
        """Write tags in place (-E: HTML entities in values are unescaped)."""
        out, err = self.execute("-E", "-overwrite_original", "-P", *args, str(target))
        if "1 image files updated" not in out and "1 image files created" not in out:
            raise ExifToolError(f"{target}: {(out + ' '.join(err)).strip()}")


def escape(value: str) -> str:
    """Escape a value for use with -E (keeps newlines and ampersands intact)."""
    return html.escape(value, quote=False).replace("\r\n", "\n").replace("\n", "&#xa;")
