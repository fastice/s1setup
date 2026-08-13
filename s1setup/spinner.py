"""Simple terminal spinner for long-running pipeline steps."""
import sys
import threading


class Spinner:
    """Animated terminal spinner running in a background thread.

    Usage::

        sp = Spinner().start()
        do_long_work()
        sp.stop()

    The spinner writes a single rotating character to stdout using ``\\r``
    so it stays on the current line.  Calling ``stop()`` clears the character
    and blocks until the background thread has exited.
    """

    _FRAMES = ['|', '/', '-', '\\']

    def __init__(self, interval: float = 0.1) -> None:
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._spin, daemon=True)

    def _spin(self) -> None:
        idx = 0
        while not self._stop.wait(self._interval):
            sys.stdout.write(f'\r  {self._FRAMES[idx % len(self._FRAMES)]}  ')
            sys.stdout.flush()
            idx += 1

    def start(self) -> 'Spinner':
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()
        sys.stdout.write('\r     \r')   # erase spinner character
        sys.stdout.flush()
