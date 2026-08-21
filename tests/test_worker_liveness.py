import asyncio
import os
import time
import unittest
from pathlib import Path
from services.worker import _touch_heartbeat


class TestWorkerLiveness(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.test_heartbeat_path = Path("/tmp/buyerly-worker-heartbeat")
        self.test_heartbeat_path.unlink(missing_ok=True)

    def tearDown(self):
        self.test_heartbeat_path.unlink(missing_ok=True)

    async def test_touch_heartbeat_creates_and_updates_mtime(self):
        self.assertFalse(self.test_heartbeat_path.is_file())
        await _touch_heartbeat()
        self.assertTrue(self.test_heartbeat_path.is_file())

        mtime1 = self.test_heartbeat_path.stat().st_mtime
        await asyncio.sleep(0.05)
        await _touch_heartbeat()
        mtime2 = self.test_heartbeat_path.stat().st_mtime
        self.assertGreaterEqual(mtime2, mtime1)

    def test_healthcheck_evaluation_logic(self):
        # 1. Missing file -> Unhealthy
        self.test_heartbeat_path.unlink(missing_ok=True)
        is_healthy = self.test_heartbeat_path.is_file() and (time.time() - self.test_heartbeat_path.stat().st_mtime) < 45
        self.assertFalse(is_healthy)

        # 2. Fresh file -> Healthy
        self.test_heartbeat_path.touch()
        is_healthy = self.test_heartbeat_path.is_file() and (time.time() - self.test_heartbeat_path.stat().st_mtime) < 45
        self.assertTrue(is_healthy)

        # 3. Stale file (60 seconds old) -> Unhealthy
        stale_time = time.time() - 60
        os.utime(self.test_heartbeat_path, (stale_time, stale_time))
        is_healthy = self.test_heartbeat_path.is_file() and (time.time() - self.test_heartbeat_path.stat().st_mtime) < 45
        self.assertFalse(is_healthy)


if __name__ == "__main__":
    unittest.main()
