"""One resident HDR Shot process with a tiny local command channel.

Windows permits more than one ``QLocalServer`` listener for the same named
pipe, so a pipe alone is not a single-instance guard. A per-user Win32 mutex
provides atomic ownership; the pipe only asks the owner to show its window when
the Start menu shortcut is launched again.
"""
from __future__ import annotations

import ctypes
import hashlib
import os
import time
from collections.abc import Callable

from PySide6.QtNetwork import QLocalServer, QLocalSocket

ERROR_ALREADY_EXISTS = 183


def instance_name(scope: str) -> str:
    """Return a stable, non-sensitive per-user endpoint name."""
    identity = f"{os.path.normcase(os.path.abspath(scope))}|{os.environ.get('USERNAME', '')}"
    digest = hashlib.sha256(identity.encode("utf-8", "surrogatepass")).hexdigest()[:16]
    return f"HDRShot-{digest}"


class SingleInstance:
    """Own the process mutex and receive newline-delimited local commands."""

    def __init__(self, scope: str):
        self.name = instance_name(scope)
        self._mutex = None
        self._server: QLocalServer | None = None
        self._handler: Callable[[str], None] | None = None
        self._pending: list[str] = []

    def acquire(self, secondary_command: str = "show") -> bool:
        """Return ``True`` for the owner; notify and return ``False`` otherwise."""
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.GetLastError.restype = ctypes.c_ulong
        handle = kernel32.CreateMutexW(None, False, f"Local\\{self.name}")
        if not handle:
            raise OSError("could not create the HDR Shot instance mutex")
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            self._send_to_owner(secondary_command)
            return False

        self._mutex = handle
        server = QLocalServer()
        server.setSocketOptions(QLocalServer.UserAccessOption)
        if not server.listen(self.name):
            self.close()
            raise RuntimeError(f"could not create HDR Shot command endpoint: {server.errorString()}")
        server.newConnection.connect(self._receive)
        self._server = server
        return True

    def set_handler(self, handler: Callable[[str], None]) -> None:
        self._handler = handler
        pending, self._pending = self._pending, []
        for command in pending:
            handler(command)

    def _send_to_owner(self, command: str) -> bool:
        payload = (command.strip() + "\n").encode("utf-8")
        # The mutex is acquired just before the owner starts its pipe listener.
        # Retry that narrow launch race instead of starting a second full app.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            socket = QLocalSocket()
            socket.connectToServer(self.name)
            if socket.waitForConnected(100):
                socket.write(payload)
                socket.waitForBytesWritten(250)
                socket.disconnectFromServer()
                return True
            socket.abort()
            time.sleep(0.04)
        return False

    def _receive(self) -> None:
        if self._server is None:
            return
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket is None:
                continue
            if not socket.bytesAvailable():
                socket.waitForReadyRead(250)
            text = bytes(socket.readAll()).decode("utf-8", "replace")
            socket.disconnectFromServer()
            socket.deleteLater()
            for command in (line.strip() for line in text.splitlines()):
                if not command:
                    continue
                if self._handler is None:
                    self._pending.append(command)
                else:
                    self._handler(command)

    def close(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None
        if self._mutex:
            ctypes.windll.kernel32.CloseHandle(self._mutex)
            self._mutex = None
