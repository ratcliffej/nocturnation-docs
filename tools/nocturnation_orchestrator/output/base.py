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
        gone). The main loop will catch + reopen.
        """
        raise NotImplementedError

    def close(self):
        """Release the underlying resource (serial port, socket)."""
        raise NotImplementedError
