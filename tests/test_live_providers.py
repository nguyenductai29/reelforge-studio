"""Live provider smoke tests. Skipped unless REELFORGE_LIVE_TESTS=1 is set in the shell: they cost money.

    REELFORGE_LIVE_TESTS=1 python -m unittest tests.test_live_providers -v

They load .env.runtime (see .env.runtime.example), pick providers like
``python -m app.provider_check``, and send one small request per modality.
CI never sets the flag, so the normal suite stays offline.
"""
import os
import unittest

LIVE = os.environ.get("REELFORGE_LIVE_TESTS") == "1"


@unittest.skipUnless(LIVE, "live provider tests need REELFORGE_LIVE_TESTS=1")
class LiveProviderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.runtime_env import load_runtime_env

        load_runtime_env()

    def run_smoke(self, function, only):
        from app.provider_check import check_text, check_video

        check = check_text() if only == "text" else check_video()
        if not check.ready:
            self.skipTest(f"{only} provider not configured: {'; '.join(check.issues)}")
        lines = []
        code = function(out=lines.append)
        self.assertEqual(code, 0, "\n".join(lines))

    def test_text_provider(self):
        from app.smoke_test import smoke_text

        self.run_smoke(smoke_text, "text")

    @unittest.skipUnless(os.environ.get("REELFORGE_LIVE_VIDEO") == "1",
                         "video generation is slower and pricier; also set REELFORGE_LIVE_VIDEO=1")
    def test_video_provider(self):
        from app.smoke_test import smoke_video

        self.run_smoke(smoke_video, "video")


if __name__ == "__main__":
    unittest.main()
