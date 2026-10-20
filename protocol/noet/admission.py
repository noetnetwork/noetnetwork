"""Bound queued/active verifier envelopes; shared requests retain one reservation."""
import threading
from .verifier_transport import check_request


class AdmissionFull(Exception):
    """Capacity is temporarily unavailable; this is not a proof verdict."""


class _Reservation:
    def __init__(self, budget, message):
        self.budget = budget
        self.message = message
        self.active = False

    def __del__(self):
        with self.budget._lock:
            if self.active:
                self.budget._bytes -= len(self.message)
                self.budget._count -= 1
                self.active = False


class AdmissionBudget:
    def __init__(self, max_bytes=128 * 1024 * 1024, max_count=16):
        if type(max_bytes) is not int or type(max_count) is not int or max_bytes < 8 or max_count < 1:
            raise ValueError('Invalid verifier admission limits')
        self.max_bytes = max_bytes
        self.max_count = max_count
        self._bytes = 0
        self._count = 0
        self._lock = threading.Lock()

    def snapshot(self):
        with self._lock:
            return {'bytes': self._bytes, 'requests': self._count}

    def admit(self, message, reservation=None):
        check_request(message)
        if reservation is not None:
            if not isinstance(reservation, _Reservation) or reservation.budget is not self or reservation.message is not message or not reservation.active:
                raise ValueError('Retry does not own this admission reservation')
            return reservation
        result = _Reservation(self, message)
        with self._lock:
            if self._count >= self.max_count or len(message) > self.max_bytes - self._bytes:
                raise AdmissionFull('Verifier request capacity is exhausted')
            self._count += 1
            self._bytes += len(message)
            result.active = True
        return result
