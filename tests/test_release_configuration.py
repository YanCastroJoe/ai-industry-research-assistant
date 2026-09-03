import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReleaseConfigurationTests(unittest.TestCase):
    def test_public_compose_binds_locally_and_requires_demo_credentials(self):
        compose = (ROOT / "compose.public-demo.yml").read_text(encoding="utf-8")
        self.assertIn('${DOCFLOW_BIND_ADDRESS:-127.0.0.1}', compose)
        self.assertIn("DOCFLOW_DEMO_USERNAME is required", compose)
        self.assertIn("DOCFLOW_DEMO_PASSWORD is required", compose)
        self.assertIn("DOCFLOW_RATE_LIMIT_PER_MINUTE", compose)

    def test_environment_template_contains_no_real_secret(self):
        example = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("DOCFLOW_DEMO_MODE=false", example)
        self.assertIn("DOCFLOW_BIND_ADDRESS=127.0.0.1", example)
        self.assertIn("DOCFLOW_DEMO_PASSWORD=", example)

    def test_readme_has_no_fixed_public_ip(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("124.221.243.125", readme)
        self.assertIn("受限公网演示", readme)


if __name__ == "__main__":
    unittest.main()
