"""Output dispatcher interface."""


class OutputError(Exception):
    """Raised when a dispatcher can't be opened or send fails."""


class OutputDispatcher:
    """Receives 512-byte universes and writes them out.

    `send(universe)` is called once per orchestrator tick (~50 Hz);
    implementations are expected to be cheap-but-not-zero-cost and
    are safe to call from one thread.
    """

    name = "unknown"

    def send(self, universe):
        """Emit one universe. universe is a bytes / bytearray of 512.

        Implementations should treat send() failures as non-fatal and
        log; only raise on terminal conditions (cable unplugged, port
        gone). Implementations that own a closable resource (USB) must
        attempt a rate-limited reopen from send() themselves so one
        transient write error doesn't wedge output for the rest of the
        show; the main loop catches, logs, and retries on the next tick.
        """
        raise NotImplementedError

    def send_espnow_frame(self, frame: bytes) -> bool:
        """Emit a fully-formed NocturNation ESP-NOW frame (Epic 13).

        Transports that don't support a side channel for ESP-NOW
        broadcast return False; the orchestrator silently drops the
        emission in that case. USB dispatcher returns True after a
        successful write; Art-Net dispatcher returns False (no
        passthrough opcode wired today; if needed, a future opcode
        could be added to ArtDmxOverArtNetExtension).
        """
        return False

    def close(self):
        """Release the underlying resource (serial port, socket)."""
        raise NotImplementedError
