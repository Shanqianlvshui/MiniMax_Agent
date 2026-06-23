import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional


CommandRunner = Callable[[list[str], int], tuple[int, str, str]]


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    command: list[str]
    summary: str
    stdout: str = ""
    stderr: str = ""
    exit_code: Optional[int] = None


class MmxCliToolProvider:
    def __init__(
        self,
        executable: str = "mmx",
        runner: Optional[CommandRunner] = None,
    ):
        self.executable = executable
        self.runner = runner or self._run_subprocess

    def auth_status(self) -> ToolResult:
        return self._run(["auth", "status"])

    def quota(self) -> ToolResult:
        return self._run(["quota"])

    def _run(self, args: list[str]) -> ToolResult:
        command = [self.executable, *args]
        try:
            exit_code, stdout, stderr = self.runner(command, 30)
        except FileNotFoundError:
            return ToolResult(
                ok=False,
                command=command,
                summary=f"{self.executable} is not installed or not on PATH",
            )

        action = " ".join(command)
        if exit_code == 0:
            return ToolResult(
                ok=True,
                command=command,
                summary=f"{action} completed",
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
            )

        return ToolResult(
            ok=False,
            command=command,
            summary=f"{action} failed with exit code {exit_code}",
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
        )

    @staticmethod
    def _run_subprocess(command: list[str], timeout_seconds: int) -> tuple[int, str, str]:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
        )
        return completed.returncode, completed.stdout, completed.stderr
