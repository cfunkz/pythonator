#!/usr/bin/env python3
"""
Update Launcher - Standalone script to apply updates.

This script is launched by the main app and:
1. Waits for the main app to exit
2. Backs up current installation
3. Copies new files (preserving user data)
4. Restarts the application

Usage:
    python update_launcher.pyw <staging_path> <app_path> <pid> <preserve_list>

Arguments:
    staging_path: Directory containing extracted update
    app_path: Directory of current installation
    pid: PID of main app to wait for
    preserve_list: Comma-separated list of files/folders to preserve
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def wait_for_exit(pid: int, timeout: float = 30.0) -> bool:
    """Wait for a process to exit."""
    start = time.monotonic()
    
    while time.monotonic() - start < timeout:
        try:
            if os.name == "nt":
                # Windows: check if process exists
                import ctypes
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                handle = ctypes.windll.kernel32.OpenProcess(
                    PROCESS_QUERY_LIMITED_INFORMATION, False, pid
                )
                if handle:
                    ctypes.windll.kernel32.CloseHandle(handle)
                else:
                    return True  # Process doesn't exist
            else:
                # Unix: send signal 0 to check if process exists
                os.kill(pid, 0)
        except (OSError, ProcessLookupError):
            return True  # Process doesn't exist
        
        time.sleep(0.5)
    
    return False


def backup_current(app_path: Path) -> Path:
    """Create backup of current installation."""
    backup_path = app_path.parent / f"{app_path.name}_backup"
    
    # Remove old backup if exists
    if backup_path.exists():
        shutil.rmtree(backup_path, ignore_errors=True)
    
    # Copy current to backup (excluding large/unnecessary files)
    shutil.copytree(
        app_path,
        backup_path,
        ignore=shutil.ignore_patterns(
            "*.log", "__pycache__", "*.pyc", ".venv", "logs"
        ),
        dirs_exist_ok=True
    )
    
    return backup_path


def apply_update(
    staging_path: Path,
    app_path: Path,
    preserve: list[str]
) -> None:
    """
    Apply update from staging to app directory.
    
    Preserves specified files/folders.
    """
    # Save preserved items to temp location
    temp_preserve = app_path.parent / "_preserve_temp"
    if temp_preserve.exists():
        shutil.rmtree(temp_preserve)
    temp_preserve.mkdir()
    
    for item in preserve:
        src = app_path / item
        if src.exists():
            dst = temp_preserve / item
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
    
    # Remove old app files (but not user data folders)
    for item in app_path.iterdir():
        if item.name in preserve:
            continue
        if item.is_dir():
            shutil.rmtree(item, ignore_errors=True)
        else:
            try:
                item.unlink()
            except OSError:
                pass
    
    # Copy new files from staging
    for item in staging_path.iterdir():
        src = item
        dst = app_path / item.name
        
        # Skip if it's a preserved item
        if item.name in preserve:
            continue
        
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
    
    # Restore preserved items
    for item in preserve:
        src = temp_preserve / item
        if src.exists():
            dst = app_path / item
            if src.is_dir():
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
    
    # Cleanup
    shutil.rmtree(temp_preserve, ignore_errors=True)


def restart_app(app_path: Path) -> None:
    """Restart the application."""
    # Find the executable
    if os.name == "nt":
        # Windows: look for .exe
        exe_candidates = list(app_path.glob("*.exe"))
        # Prefer pythonator.exe if exists
        exe = None
        for candidate in exe_candidates:
            if "pythonator" in candidate.name.lower():
                exe = candidate
                break
        if not exe and exe_candidates:
            exe = exe_candidates[0]
        
        if exe:
            os.startfile(str(exe))
        else:
            # Fall back to running with Python
            main_py = app_path / "app.py"
            if main_py.exists():
                subprocess.Popen([sys.executable, str(main_py)], cwd=str(app_path))
    else:
        # Unix: look for shell script or run Python
        shell_script = app_path / "pythonator"
        main_py = app_path / "app.py"
        
        if shell_script.exists() and os.access(shell_script, os.X_OK):
            subprocess.Popen([str(shell_script)], cwd=str(app_path))
        elif main_py.exists():
            subprocess.Popen([sys.executable, str(main_py)], cwd=str(app_path))


def show_message(title: str, message: str, is_error: bool = False) -> None:
    """Show a message to the user."""
    try:
        if os.name == "nt":
            import ctypes
            MB_OK = 0x0
            MB_ICONERROR = 0x10
            MB_ICONINFORMATION = 0x40
            icon = MB_ICONERROR if is_error else MB_ICONINFORMATION
            ctypes.windll.user32.MessageBoxW(0, message, title, MB_OK | icon)
        else:
            # Try zenity, kdialog, or xmessage
            for cmd in [
                ["zenity", "--info" if not is_error else "--error", f"--text={message}"],
                ["kdialog", "--msgbox" if not is_error else "--error", message],
                ["xmessage", message],
            ]:
                if shutil.which(cmd[0]):
                    subprocess.run(cmd, check=False)
                    break
    except Exception:
        print(f"{title}: {message}")


def main() -> int:
    """Main entry point."""
    if len(sys.argv) < 5:
        print(f"Usage: {sys.argv[0]} <staging_path> <app_path> <pid> <preserve_list>")
        return 1
    
    staging_path = Path(sys.argv[1])
    app_path = Path(sys.argv[2])
    pid = int(sys.argv[3])
    preserve = sys.argv[4].split(",") if sys.argv[4] else []
    
    try:
        # Wait for main app to exit
        print(f"Waiting for process {pid} to exit...")
        if not wait_for_exit(pid, timeout=30.0):
            show_message(
                "Update Failed",
                "Timed out waiting for application to close.\n"
                "Please close Pythonator and try again.",
                is_error=True
            )
            return 1
        
        # Give a moment for file handles to be released
        time.sleep(1.0)
        
        # Create backup
        print("Creating backup...")
        backup_path = backup_current(app_path)
        
        # Apply update
        print("Applying update...")
        apply_update(staging_path, app_path, preserve)
        
        # Clean up staging
        staging_parent = staging_path.parent
        if staging_parent.name == "pythonator_update":
            shutil.rmtree(staging_parent, ignore_errors=True)
        
        # Remove backup on success
        shutil.rmtree(backup_path, ignore_errors=True)
        
        # Restart
        print("Restarting application...")
        restart_app(app_path)
        
        return 0
        
    except Exception as e:
        show_message(
            "Update Failed",
            f"An error occurred during update:\n{e}\n\n"
            "Your previous installation should still be intact.",
            is_error=True
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
