"""Minimal RCON client for command execution."""

from __future__ import annotations

import socket
import struct


class RCONError(RuntimeError):
    """Raised when an RCON request fails."""


class RCONClient:
    """Small Source-style RCON client compatible with Minecraft."""

    AUTH = 3
    COMMAND = 2

    def __init__(self, host: str, port: int, password: str, timeout: int = 5):
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout
        self.socket: socket.socket | None = None

    def __enter__(self) -> "RCONClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def connect(self) -> None:
        self.socket = socket.create_connection(
            (self.host, self.port), timeout=self.timeout
        )
        self.socket.settimeout(self.timeout)
        try:
            self.authenticate()
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if self.socket:
            self.socket.close()
            self.socket = None

    def authenticate(self) -> None:
        response_id, _response_type, _payload = self._roundtrip(
            1, self.AUTH, self.password
        )
        if response_id == -1:
            raise RCONError("RCON authentication failed")

    def command(self, command: str) -> str:
        _response_id, _response_type, payload = self._roundtrip(
            2, self.COMMAND, command
        )
        return payload

    def _roundtrip(
        self, request_id: int, packet_type: int, payload: str
    ) -> tuple[int, int, str]:
        self._send_packet(request_id, packet_type, payload)
        return self._receive_packet()

    def _send_packet(self, request_id: int, packet_type: int, payload: str) -> None:
        if not self.socket:
            raise RCONError("RCON socket is not connected")
        payload_bytes = payload.encode("utf-8") + b"\x00\x00"
        packet = (
            struct.pack("<iii", len(payload_bytes) + 8, request_id, packet_type)
            + payload_bytes
        )
        self.socket.sendall(packet)

    def _receive_packet(self) -> tuple[int, int, str]:
        if not self.socket:
            raise RCONError("RCON socket is not connected")
        size_data = self._recv_exact(4)
        size = struct.unpack("<i", size_data)[0]
        if not 10 <= size <= 4 * 1024 * 1024:
            raise RCONError("Invalid RCON packet size")
        packet = self._recv_exact(size)
        request_id, packet_type = struct.unpack("<ii", packet[:8])
        payload = packet[8:-2].decode("utf-8", errors="replace")
        return request_id, packet_type, payload

    def _recv_exact(self, size: int) -> bytes:
        if not self.socket:
            raise RCONError("RCON socket is not connected")
        chunks = bytearray()
        while len(chunks) < size:
            chunk = self.socket.recv(size - len(chunks))
            if not chunk:
                raise RCONError("Unexpected EOF from RCON server")
            chunks.extend(chunk)
        return bytes(chunks)
