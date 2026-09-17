import subprocess
import unittest
from unittest.mock import patch

from radar import sources


class GitHubCommandTests(unittest.TestCase):
    def test_gh_timeout_becomes_a_source_error(self):
        timeout = subprocess.TimeoutExpired(["gh", "issue", "list"], sources.GH_TIMEOUT_SECONDS)
        with patch("radar.sources.subprocess.run", side_effect=timeout):
            with self.assertRaisesRegex(RuntimeError, r"timed out after 30s"):
                sources.gh("issue", "list", "--state", "open")


if __name__ == "__main__":
    unittest.main()
