"""Start a Windows child suspended, attach it to a kill-on-close Job, then resume.

Only processes created here are ever assigned or terminated. The Job handle is held
by the application process, so an application crash also closes it.
"""
from __future__ import annotations

import os
import subprocess
import sys


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes as w
    import msvcrt

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    HANDLE = w.HANDLE
    SIZE_T = ctypes.c_size_t
    ULONG_PTR = SIZE_T

    class STARTUPINFO(ctypes.Structure):
        _fields_ = [("cb", w.DWORD), ("lpReserved", w.LPWSTR), ("lpDesktop", w.LPWSTR),
                    ("lpTitle", w.LPWSTR), ("dwX", w.DWORD), ("dwY", w.DWORD),
                    ("dwXSize", w.DWORD), ("dwYSize", w.DWORD), ("dwXCountChars", w.DWORD),
                    ("dwYCountChars", w.DWORD), ("dwFillAttribute", w.DWORD),
                    ("dwFlags", w.DWORD), ("wShowWindow", w.WORD), ("cbReserved2", w.WORD),
                    ("lpReserved2", ctypes.c_void_p), ("hStdInput", HANDLE),
                    ("hStdOutput", HANDLE), ("hStdError", HANDLE)]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [("hProcess", HANDLE), ("hThread", HANDLE),
                    ("dwProcessId", w.DWORD), ("dwThreadId", w.DWORD)]

    class BASIC_LIMIT(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64), ("LimitFlags", w.DWORD),
                    ("MinimumWorkingSetSize", SIZE_T), ("MaximumWorkingSetSize", SIZE_T),
                    ("ActiveProcessLimit", w.DWORD), ("Affinity", ULONG_PTR),
                    ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in
                    ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                     "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class EXTENDED_LIMIT(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC_LIMIT), ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", SIZE_T), ("JobMemoryLimit", SIZE_T),
                    ("PeakProcessMemoryUsed", SIZE_T), ("PeakJobMemoryUsed", SIZE_T)]

    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
    k32.CreateJobObjectW.restype = HANDLE
    k32.SetInformationJobObject.argtypes = [HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    k32.SetInformationJobObject.restype = w.BOOL
    k32.CreateProcessW.argtypes = [w.LPCWSTR, w.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
                                   w.BOOL, w.DWORD, ctypes.c_void_p, w.LPCWSTR,
                                   ctypes.POINTER(STARTUPINFO), ctypes.POINTER(PROCESS_INFORMATION)]
    k32.CreateProcessW.restype = w.BOOL
    k32.AssignProcessToJobObject.argtypes = [HANDLE, HANDLE]
    k32.AssignProcessToJobObject.restype = w.BOOL
    k32.ResumeThread.argtypes = [HANDLE]
    k32.ResumeThread.restype = w.DWORD
    k32.GetExitCodeProcess.argtypes = [HANDLE, ctypes.POINTER(w.DWORD)]
    k32.GetExitCodeProcess.restype = w.BOOL
    k32.WaitForSingleObject.argtypes = [HANDLE, w.DWORD]
    k32.WaitForSingleObject.restype = w.DWORD
    k32.TerminateProcess.argtypes = [HANDLE, w.UINT]
    k32.TerminateProcess.restype = w.BOOL
    k32.CloseHandle.argtypes = [HANDLE]
    k32.CloseHandle.restype = w.BOOL
    k32.SetHandleInformation.argtypes = [HANDLE, w.DWORD, w.DWORD]
    k32.SetHandleInformation.restype = w.BOOL
    k32.GetStdHandle.argtypes = [w.DWORD]
    k32.GetStdHandle.restype = HANDLE

    def _check(value, label):
        if not value:
            raise ctypes.WinError(ctypes.get_last_error(), f"{label} failed")
        return value


class OwnedProcess:
    def __init__(self, args: list[str], cwd: str, log_file):
        self._job = None
        self._process = None
        self.pid = None
        if sys.platform != "win32":
            self._process = subprocess.Popen(args, cwd=cwd, stdout=log_file, stderr=log_file)
            self.pid = self._process.pid
            return
        self._create_windows(args, cwd, log_file)

    def _create_windows(self, args, cwd, log_file):
        log_handle = HANDLE(msvcrt.get_osfhandle(log_file.fileno()))
        _check(k32.SetHandleInformation(log_handle, 1, 1), "SetHandleInformation")
        job = _check(k32.CreateJobObjectW(None, None), "CreateJobObjectW")
        info = PROCESS_INFORMATION()
        try:
            limits = EXTENDED_LIMIT()
            limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            _check(k32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)),
                   "SetInformationJobObject")
            startup = STARTUPINFO()
            startup.cb = ctypes.sizeof(startup)
            startup.dwFlags = 0x100  # STARTF_USESTDHANDLES
            startup.hStdInput = k32.GetStdHandle(w.DWORD(-10).value)
            startup.hStdOutput = log_handle
            startup.hStdError = log_handle
            command = ctypes.create_unicode_buffer(subprocess.list2cmdline(args))
            _check(k32.CreateProcessW(args[0], command, None, None, True,
                                      0x4 | 0x08000000, None, cwd,
                                      ctypes.byref(startup), ctypes.byref(info)), "CreateProcessW")
            try:
                _check(k32.AssignProcessToJobObject(job, info.hProcess), "AssignProcessToJobObject")
                if k32.ResumeThread(info.hThread) == 0xFFFFFFFF:
                    raise ctypes.WinError(ctypes.get_last_error())
            except BaseException:
                k32.TerminateProcess(info.hProcess, 1)
                raise
            self._job, self._process, self.pid = job, info.hProcess, info.dwProcessId
            job = None
            info.hProcess = None
        finally:
            if info.hThread:
                k32.CloseHandle(info.hThread)
            if info.hProcess:
                k32.CloseHandle(info.hProcess)
            if job:
                k32.CloseHandle(job)

    def poll(self):
        if sys.platform != "win32":
            return self._process.poll()
        code = w.DWORD()
        _check(k32.GetExitCodeProcess(self._process, ctypes.byref(code)), "GetExitCodeProcess")
        return None if code.value == 259 else code.value  # STILL_ACTIVE

    def terminate(self):
        if sys.platform != "win32":
            self._process.terminate()
        elif self.poll() is None:
            _check(k32.TerminateProcess(self._process, 1), "TerminateProcess")

    kill = terminate

    def wait(self, timeout: float = 5):
        if sys.platform != "win32":
            return self._process.wait(timeout=timeout)
        result = k32.WaitForSingleObject(self._process, int(timeout * 1000))
        if result == 258:
            raise subprocess.TimeoutExpired(str(self.pid), timeout)
        if result != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        return self.poll()

    def close(self):
        if sys.platform == "win32":
            if self._job:
                k32.CloseHandle(self._job)
                self._job = None
            if self._process:
                k32.CloseHandle(self._process)
                self._process = None
