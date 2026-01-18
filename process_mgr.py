"""
Process Manager - Handles process lifecycle with custom Python support.

Design choices:
- QProcess for non-blocking I/O and Qt event loop integration
- Process groups (setsid) for clean child termination on Unix
- Incremental UTF-8 decoders handle partial codepoints across reads
- Custom Python path overrides venv auto-detection when set
- Strong isolation between concurrent processes
"""
from __future__ import annotations

import codecs
import os
import shlex
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Protocol

from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer

from config import APP_DIR, Bot, KILL_TIMEOUT_MS


class OutputCallback(Protocol):
    """Callback for process output."""
    def __call__(self, name: str, text: str) -> None: ...


class FinishedCallback(Protocol):
    """Callback for process completion."""
    def __call__(self, name: str, exit_code: int, crashed: bool) -> None: ...


@dataclass
class ProcessState:
    """State for a running process with isolated resources."""
    process: QProcess
    stdout_decoder: codecs.IncrementalDecoder = field(
        default_factory=lambda: codecs.getincrementaldecoder("utf-8")(errors="replace")
    )
    stderr_decoder: codecs.IncrementalDecoder = field(
        default_factory=lambda: codecs.getincrementaldecoder("utf-8")(errors="replace")
    )
    use_pgroup: bool = False
    stopping: bool = False


class ProcessManager(QObject):
    """
    Manages process lifecycle for bots with strong isolation.
    
    Each process runs in its own environment with:
    - Separate working directory
    - Isolated virtual environment
    - Independent I/O decoders
    - Clean process group management
    """

    def __init__(
        self,
        on_output: OutputCallback,
        on_finished: FinishedCallback,
    ):
        super().__init__()
        self._on_output = on_output
        self._on_finished = on_finished
        self._processes: dict[str, ProcessState] = {}

    @property
    def running(self) -> set[str]:
        """Set of currently running bot names."""
        return set(self._processes.keys())

    def is_running(self, name: str) -> bool:
        """Check if a bot is running."""
        return name in self._processes

    def get_pid(self, name: str) -> int:
        """Get PID for a running bot."""
        state = self._processes.get(name)
        return state.process.processId() or 0 if state else 0

    def start(self, bot: Bot) -> bool:
        """Start a bot. Returns True on success."""
        if bot.name in self._processes:
            self._log(bot.name, "[runner] Already running", "31")
            return False
        return self._start_custom(bot) if bot.custom_cmd else self._start_script(bot)

    def stop(self, name: str) -> None:
        """Stop gracefully, then force kill after timeout."""
        state = self._processes.get(name)
        if not state:
            return
        
        state.stopping = True
        pid = state.process.processId() or 0
        
        # Graceful: SIGTERM to process group on Unix
        if os.name != "nt" and pid and state.use_pgroup:
            try:
                os.killpg(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        
        state.process.terminate()
        QTimer.singleShot(KILL_TIMEOUT_MS, lambda: self._force_kill(name, pid))

    def stop_all(self) -> None:
        """Stop all running processes."""
        for name in list(self._processes.keys()):
            self.stop(name)

    # -------------------------------------------------------------------------
    # Start implementations
    # -------------------------------------------------------------------------

    def _start_script(self, bot: Bot) -> bool:
        """Start bot in standard script mode."""
        if not bot.entry:
            self._log(bot.name, "[runner] No entry specified", "31")
            return False
        
        entry = Path(bot.entry)
        if not entry.exists():
            self._log(bot.name, "[runner] Entry not found", "31")
            return False

        python = self._resolve_python(bot, entry)
        if not python:
            return False

        args = ["-u", str(entry)]
        if bot.flags:
            try:
                args.extend(shlex.split(bot.flags))
            except ValueError:
                args.extend(bot.flags.split())

        venv = self._get_venv(entry)
        return self._run(bot.name, python, args, entry.parent, venv)

    def _start_custom(self, bot: Bot) -> bool:
        """Start bot in custom command mode."""
        if not bot.flags.strip():
            self._log(bot.name, "[runner] No command specified", "31")
            return False

        cwd = Path(bot.entry).parent if bot.entry and Path(bot.entry).exists() else APP_DIR
        python = self._resolve_python(bot, cwd / "main.py")
        if not python:
            return False

        try:
            parts = shlex.split(bot.flags)
        except ValueError as e:
            self._log(bot.name, f"[runner] Invalid command: {e}", "31")
            return False

        # Handle python prefix
        if parts and parts[0] in ("python", "python3", Path(python).name):
            args = parts[1:]
        else:
            args = ["-m"] + parts

        venv = self._get_venv(cwd / "main.py")
        return self._run(bot.name, python, args, cwd, venv)

    def _resolve_python(self, bot: Bot, entry: Path) -> Optional[str]:
        """Resolve Python interpreter: custom path > venv > error."""
        # Custom Python path takes priority
        if bot.python_path:
            p = Path(bot.python_path)
            if p.exists():
                return str(p)
            self._log(bot.name, f"[runner] Python not found: {bot.python_path}", "31")
            return None

        # Auto-detect from venv
        venv = self._get_venv(entry)
        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if python.exists():
            return str(python)

        self._log(bot.name, "[runner] No venv - run Setup venv or set Python path", "31")
        return None

    def _get_venv(self, entry: Path) -> Path:
        """Get venv path for an entry file."""
        return entry.parent / ".venv"

    def _run(
        self, name: str, program: str, args: list[str],
        cwd: Path, venv: Optional[Path] = None
    ) -> bool:
        """Create and start QProcess with isolated environment."""
        proc = QProcess(self)
        use_pgroup = False

        # Unix: wrap with setsid for clean process group termination
        if os.name != "nt" and shutil.which("setsid"):
            cmd = " ".join(shlex.quote(x) for x in [program] + args)
            proc.setProgram("bash")
            proc.setArguments(["-lc", f"exec setsid {cmd}"])
            use_pgroup = True
        else:
            proc.setProgram(program)
            proc.setArguments(args)

        proc.setWorkingDirectory(str(cwd))
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)

        # Build isolated environment
        env = self._build_environment(venv)
        proc.setProcessEnvironment(env)

        # Connect signals with name capture
        proc.readyReadStandardOutput.connect(lambda n=name: self._on_stdout(n))
        proc.readyReadStandardError.connect(lambda n=name: self._on_stderr(n))
        proc.finished.connect(lambda code, status, n=name: self._handle_finished(n, code, status))

        # Create isolated state with fresh decoders
        self._processes[name] = ProcessState(
            process=proc,
            use_pgroup=use_pgroup,
        )
        
        proc.start()
        self._log(name, f"[runner] Started {datetime.now():%H:%M:%S}", "36")
        return True

    def _build_environment(self, venv: Optional[Path]) -> QProcessEnvironment:
        """Build isolated process environment."""
        env = QProcessEnvironment.systemEnvironment()
        
        # Python settings for clean output
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("PYTHONUTF8", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("TERM", "xterm-256color")
        env.insert("FORCE_COLOR", "1")
        env.remove("PYTHONHOME")  # Remove any conflicting Python home
        
        # Configure venv if present
        if venv and venv.exists():
            env.insert("VIRTUAL_ENV", str(venv))
            bindir = venv / ("Scripts" if os.name == "nt" else "bin")
            env.insert("PATH", str(bindir) + os.pathsep + env.value("PATH", ""))
        
        return env

    # -------------------------------------------------------------------------
    # I/O handlers
    # -------------------------------------------------------------------------

    def _on_stdout(self, name: str) -> None:
        """Handle stdout data."""
        state = self._processes.get(name)
        if not state:
            return
        
        data = bytes(state.process.readAllStandardOutput().data())
        if data:
            text = state.stdout_decoder.decode(data)
            if text:
                self._on_output(name, text)

    def _on_stderr(self, name: str) -> None:
        """Handle stderr data."""
        state = self._processes.get(name)
        if not state:
            return
        
        data = bytes(state.process.readAllStandardError().data())
        if data:
            text = state.stderr_decoder.decode(data)
            if text:
                self._on_output(name, text)

    def _handle_finished(self, name: str, code: int, status: QProcess.ExitStatus) -> None:
        """Handle process completion."""
        state = self._processes.get(name)
        if not state:
            return
        
        # Flush remaining output
        self._on_stdout(name)
        self._on_stderr(name)
        
        user_stop = state.stopping
        crashed = status == QProcess.ExitStatus.CrashExit
        del self._processes[name]

        if user_stop:
            self._log(name, "[runner] Stopped", "36")
        else:
            status_text = "CRASHED" if crashed else "Exited"
            color = "31" if crashed else "33"
            self._log(name, f"[runner] {status_text} (code={code})", color)
        
        self._on_finished(name, code, crashed and not user_stop)

    def _force_kill(self, name: str, pid: int) -> None:
        """Force kill if still running after grace period."""
        state = self._processes.get(name)
        if not state or state.process.state() == QProcess.ProcessState.NotRunning:
            return

        if os.name == "nt":
            if pid:
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        capture_output=True, timeout=5
                    )
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    pass
            state.process.kill()
        elif pid and state.use_pgroup:
            try:
                os.killpg(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                state.process.kill()
        else:
            state.process.kill()

    def _log(self, name: str, msg: str, color: str = "0") -> None:
        """Emit a colored log message."""
        self._on_output(name, f"\x1b[{color}m{msg}\x1b[0m\n")

    # -------------------------------------------------------------------------
    # Venv management
    # -------------------------------------------------------------------------

    def setup_venv(self, bot: Bot) -> bool:
        """Create a virtual environment for the bot."""
        entry = self._get_entry_path(bot)
        if not entry:
            return False

        venv = self._get_venv(entry)
        python_in_venv = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        
        if python_in_venv.exists():
            self._log(bot.name, "[runner] venv already exists", "33")
            return False

        # Prefer explicit python_path; otherwise use runner python
        creator = bot.python_path.strip() if bot.python_path else sys.executable
        if not creator or not Path(creator).exists():
            self._log(bot.name, f"[runner] Python not found: {creator}", "31")
            return False

        self._log(bot.name, "[runner] Creating venv...", "36")
        return self._run(bot.name, creator, ["-m", "venv", str(venv)], entry.parent)

    def install_deps(self, bot: Bot) -> bool:
        """Install dependencies into venv."""
        entry = self._get_entry_path(bot)
        if not entry:
            return False
        
        python = self._resolve_python(bot, entry)
        if not python:
            return False
        
        req_file = entry.parent / "requirements.txt"
        req_file.write_text(bot.reqs or "", encoding="utf-8")
        
        self._log(bot.name, "[runner] Installing dependencies...", "36")
        return self._run(
            bot.name, python,
            ["-m", "pip", "install", "-r", str(req_file)],
            entry.parent, self._get_venv(entry)
        )

    def _get_entry_path(self, bot: Bot) -> Optional[Path]:
        """Get valid entry path for bot."""
        if bot.entry and Path(bot.entry).exists():
            return Path(bot.entry)
        if bot.custom_cmd:
            return APP_DIR / "main.py"
        return None
