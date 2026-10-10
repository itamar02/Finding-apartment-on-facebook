"""Per-provider request limits (per minute and per day), shared by every process.

Calls are logged in the `ai_calls` table, so the scanner's analysis run, the app server and a
manual analyze_posts.py all count against the same limits. Over the per-minute limit a call
waits (queues) until a slot frees up; over the per-day limit it raises RateLimited, and the
caller falls back to another provider or stops.
"""
import time
from datetime import datetime


class RateLimited(Exception):
    pass


class RateLimiter:
    def __init__(self, db, provider, per_minute=None, per_day=None, clock=time.time, sleep=time.sleep):
        self.db, self.provider = db, provider
        self.per_minute, self.per_day = per_minute or None, per_day or None
        self.clock, self.sleep = clock, sleep

    def _count_since(self, since):
        return self.db.execute("SELECT count(*), min(called_at) FROM ai_calls WHERE provider = ? AND called_at > ?",
                               (self.provider, since)).fetchone()

    def _midnight(self, now):
        local = datetime.fromtimestamp(now).astimezone()
        return local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    def remaining_today(self):
        if not self.per_day:
            return None
        return max(0, self.per_day - self._count_since(self._midnight(self.clock()))[0])

    def acquire(self, max_wait=300):
        """Wait for a free slot and record the call. Raises RateLimited when the day is used up."""
        waited = 0.0
        while True:
            now = self.clock()
            if self.per_day and self._count_since(self._midnight(now))[0] >= self.per_day:
                raise RateLimited(f"{self.provider}: daily limit of {self.per_day} requests reached")
            if self.per_minute:
                count, oldest = self._count_since(now - 60)
                if count >= self.per_minute:
                    pause = max(0.1, oldest + 60 - now + 0.05)
                    if waited + pause > max_wait:
                        raise RateLimited(f"{self.provider}: per-minute limit, waited too long")
                    self.sleep(pause)
                    waited += pause
                    continue
            with self.db:
                self.db.execute("INSERT INTO ai_calls (provider, called_at) VALUES (?, ?)", (self.provider, now))
                # The log only needs the last day.
                self.db.execute("DELETE FROM ai_calls WHERE called_at < ?", (now - 2 * 86400,))
            return
