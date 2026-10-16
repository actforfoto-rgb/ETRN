"""NORM ARB V7: Windows CMD + existing Python 3.12+, no PowerShell / installer.

The RDP key input is a normal Tk password entry with both Ctrl+V and
clipboard-button support. The token remains only in process memory and is
passed to a child process through its environment (never saved in files).
No orders, no Windows tasks, no services, no MT5 mutations.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
VENDOR = ROOT / "vendor"
MAX_STARTUP_OUTPUT = 3000


def safe_env(key: str | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env['PYTHONPATH'] = str(VENDOR) + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    if key is None:
        env.pop('FINAM_READONLY_API_KEY', None)
    else:
        env['FINAM_READONLY_API_KEY'] = key
    return env


def validate_key_text(value: str) -> tuple[bool, str]:
    key = value.strip()
    if not key:
        return False, 'Нет текста. Буфер RDP пуст или вставка не сработала.'
    if len(key) < 15:
        return False, f'Получено всего {len(key)} символов. Нужен полный Secret Key.'
    if not key.startswith('tapi_'):
        return False, 'Это не похоже на Secret Key Finam. Проверь, что скопирован ключ полностью.'
    return True, key


def get_key_from_dialog() -> str | None:
    try:
        import tkinter as tk
        from tkinter import ttk
    except (ImportError, OSError):
        print('GUI_UNAVAILABLE: в установленном Python нет tkinter. Никаких изменений.', flush=True)
        return None

    try:
        dialog = tk.Tk()
    except Exception:
        print('GUI_UNAVAILABLE: не удалось открыть окно ввода. Никаких изменений.', flush=True)
        return None

    dialog.title('NORM ARBITRAGE — Finam: только чтение')
    dialog.geometry('670x250')
    dialog.minsize(670, 250)
    dialog.maxsize(670, 250)
    dialog.attributes('-topmost', True)
    try:
        dialog.eval('tk::PlaceWindow . center')
    except Exception:
        pass
    result: dict[str, str | None] = {'value': None}
    frame = ttk.Frame(dialog, padding=18)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='Введите существующий Secret Key Finam с правами только чтения.', font=('Segoe UI', 11)).pack(anchor='w')
    ttk.Label(frame, text='Можно вставить Ctrl+V прямо в поле или нажать «ВСТАВИТЬ».', font=('Segoe UI', 9)).pack(anchor='w', pady=(6, 12))
    row = ttk.Frame(frame)
    row.pack(fill='x')
    entry = ttk.Entry(row, show='●', font=('Consolas', 12), width=52)
    entry.pack(side='left', fill='x', expand=True)
    message = tk.StringVar(value='Значение скрыто. Буфер обмена RDP должен быть доступен на VPS.')
    ttk.Label(frame, textvariable=message, wraplength=625).pack(anchor='w', pady=(12, 12))

    def paste() -> None:
        try:
            text = dialog.clipboard_get()
            if not text.strip():
                message.set('Буфер VPS пуст. Проверь копирование на VPS или введи ключ в поле.')
            else:
                entry.delete(0, 'end')
                entry.insert(0, text.strip())
                message.set(f'Вставлено {len(entry.get())} символов. Сам ключ скрыт.')
        except (tk.TclError, UnicodeError):
            message.set('Буфер VPS недоступен. Попробуй Ctrl+V в поле или введи ключ вручную.')

    def continue_now() -> None:
        valid, value = validate_key_text(entry.get())
        if not valid:
            message.set(value)
            return
        result['value'] = value
        entry.delete(0, 'end')
        dialog.destroy()

    def cancel() -> None:
        dialog.destroy()

    ttk.Button(row, text='ВСТАВИТЬ', command=paste, width=16).pack(side='left', padx=(10, 0))
    buttons = ttk.Frame(frame)
    buttons.pack(fill='x', pady=(8, 0))
    ttk.Button(buttons, text='Отмена', command=cancel).pack(side='right')
    ttk.Button(buttons, text='Продолжить', command=continue_now).pack(side='right', padx=(0, 12))
    dialog.bind('<Return>', lambda event: continue_now())
    dialog.protocol('WM_DELETE_WINDOW', cancel)
    entry.focus_set()
    dialog.mainloop()
    return result['value']


def run_python(name: str, *, key: str | None = None, args: tuple[str, ...] = (), timeout: int = 40) -> tuple[int, str]:
    argv = [sys.executable, '-S', str(ROOT / name), *args]
    try:
        result = subprocess.run(argv, cwd=ROOT, env=safe_env(key),
                                stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, encoding='utf-8', errors='replace', timeout=timeout)
        # All known scripts emit sanitized static JSON; do not print stderr,
        # broker responses, subprocess exception strings or raw environment.
        output = result.stdout.strip()
        try:
            items = [json.loads(line) for line in output.splitlines() if line.strip()]
            for item in items:
                if not isinstance(item, dict) or 'orders' not in item:
                    return result.returncode, 'SANITIZED_OUTPUT_NOT_VERIFIED'
            if items:
                return result.returncode, json.dumps(items[-1], ensure_ascii=False)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        return result.returncode, 'NO_SAFE_REPORT' if not output else 'SANITIZED_OUTPUT_NOT_VERIFIED'
    except subprocess.TimeoutExpired:
        return 124, 'NETWORK_TIMEOUT'
    except Exception:
        return 125, 'PYTHON_CHILD_LAUNCH_FAILED'


def process_alive(pid: int) -> bool:
    """Windows process-query only; NEVER sends a signal or terminates any PID."""
    if os.name != 'nt' or not 0 < pid < 2**31:
        return False
    try:
        import ctypes
        import ctypes.wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        open_process = kernel.OpenProcess
        open_process.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.DWORD]
        open_process.restype = ctypes.wintypes.HANDLE
        handle = open_process(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            get_exit = kernel.GetExitCodeProcess
            get_exit.argtypes = [ctypes.wintypes.HANDLE, ctypes.POINTER(ctypes.wintypes.DWORD)]
            get_exit.restype = ctypes.wintypes.BOOL
            code = ctypes.wintypes.DWORD(0)
            return bool(get_exit(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    except Exception:
        return False


def previous_pid_running() -> bool:
    """Never launch a second collector while an older V7 process lives.

    When staging a release side-by-side, also respect the original installed
    process pid file. The check only queries process state, never terminates.
    """
    paths=[ROOT/'service.pid']
    legacy=ROOT.parent/'NORM_ARB_V7_NATIVE'/'service.pid'
    if legacy != paths[0]:
        paths.append(legacy)
    for p in paths:
        if not p.is_file():
            continue
        try:
            if process_alive(int(p.read_text(encoding='ascii').strip())):
                return True
        except (ValueError,OSError,UnicodeError):
            continue
    return False


def start_worker(secret: str) -> int:
    logs = ROOT / 'logs'
    logs.mkdir(exist_ok=True)
    (ROOT / 'STOP_REQUESTED').unlink(missing_ok=True)
    env = safe_env(secret)
    flags = 0
    if os.name == 'nt':
        flags = (getattr(subprocess, 'DETACHED_PROCESS', 0x00000008)
                 | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0x00000200)
                 | 0x00004000)  # BELOW_NORMAL_PRIORITY_CLASS
    args = [sys.executable, '-S', str(ROOT / 'run_service.py'), '--round-seconds', '90']
    try:
        with open(logs / 'collector.stdout.log', 'ab') as out, open(logs / 'collector.stderr.log', 'ab') as err:
            worker = subprocess.Popen(args, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                      stdout=out, stderr=err, close_fds=True,
                                      creationflags=flags)
        time.sleep(2)
        if worker.poll() is not None:
            print('COLLECTOR_EXITED: служба не запустилась; смотри STATUS.cmd.', flush=True)
            return 6
        (ROOT / 'service.pid').write_text(str(worker.pid), encoding='ascii')
        print(f'COLLECTOR_STARTED PID={worker.pid} ; только чтение; настоящие рыночные обновления — смотри STATUS.cmd.', flush=True)
        print('СТАТУС: ' + str(ROOT / 'STATUS.cmd'), flush=True)
        print('ОСТАНОВКА: ' + str(ROOT / 'STOP.cmd'), flush=True)
        print('ДАННЫЕ: ' + str(ROOT / 'data'), flush=True)
        print('Автозапуск Windows НЕ настроен. Forex и MT5 НЕ затронуты.', flush=True)
        return 0
    except Exception:
        print('COLLECTOR_LAUNCH_FAILED: запуск не завершён; Forex не затронут.', flush=True)
        return 6


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Start bounded Finam read-only WebSocket capture; no PowerShell')
    parser.add_argument('--selftest', action='store_true')
    args = parser.parse_args(argv)
    print('NORM ARBITRAGE V7 — запуск без PowerShell, администратора и установок.', flush=True)
    print('Python: ' + sys.version.split()[0], flush=True)
    if sys.version_info < (3, 12) or sys.maxsize <= 2**32:
        print('UNSUPPORTED_PYTHON_VERSION_OR_ARCH: нужен уже установленный x64 Python 3.12+', flush=True)
        return 3
    from VERIFY_OFFLINE import verify
    integrity=verify()
    if integrity.get('status')!='RELEASE_INTEGRITY_OK':
        print('RELEASE_INTEGRITY_FAILED: исходники или библиотеки изменились.',flush=True)
        return 20
    print('RELEASE_INTEGRITY_OK',flush=True)
    if not VENDOR.is_dir() or not (VENDOR / 'websockets' / '__init__.py').is_file():
        print('PACKAGE_INCOMPLETE: нет встроенной библиотеки WebSocket.', flush=True)
        return 4
    try:
        test = subprocess.run([sys.executable, '-S', '-m', 'unittest', 'discover', '-s', 'tests', '-q'],
                              cwd=ROOT, env=safe_env(), stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25)
        if test.returncode:
            print('OFFLINE_TESTS_FAILED: проверка кода не пройдена.', flush=True)
            return 5
    except Exception:
        print('OFFLINE_TESTS_CANNOT_RUN: запуск остановлен.', flush=True)
        return 5
    print('OFFLINE_TESTS_OK (НЕ доказательство живых котировок).', flush=True)
    if args.selftest:
        print('SELFTEST_ONLY: подключения к Finam и запуска службы нет.', flush=True)
        return 0
    if os.name != 'nt':
        print('WINDOWS_RUNTIME_REQUIRED', flush=True)
        return 3
    if previous_pid_running():
        print('PID_RUNNING: не запускаю второй экземпляр. Проверь STATUS.cmd.', flush=True)
        return 0
    print('FINAM_TLS_CERTIFICATE_PREFLIGHT...', flush=True)
    tls_status, tls_msg = run_python('tls_probe.py', timeout=35)
    print(tls_msg, flush=True)
    if tls_status != 0:
        print('TLS_NOT_VERIFIED: ключ не запрашиваю; сборщик не запущен.', flush=True)
        return 10
    key = get_key_from_dialog()
    if key is None:
        print('KEY_INPUT_CANCELLED: ничего не запущено.', flush=True)
        return 7
    print('FINAM_READONLY_AUTH_CHECK...', flush=True)
    status, msg = run_python('auth_check.py', key=key, timeout=35)
    print(msg, flush=True)
    if status != 0:
        print('AUTH_FAILED: нет подключения; сборщик не запущен.', flush=True)
        return 8
    print('FINAM_WEBSOCKET_PROBE: до 25 секунд...', flush=True)
    status, msg = run_python('probe.py', key=key, args=('--seconds', '25'), timeout=65)
    print(msg, flush=True)
    if status != 0:
        print('WEBSOCKET_PROBE_FAILED: сборщик не запущен.', flush=True)
        return 9
    return start_worker(key)


if __name__ == '__main__':
    raise SystemExit(main())