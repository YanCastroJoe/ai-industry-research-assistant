import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReleaseConfigurationTests(unittest.TestCase):
    def test_public_api_exposes_docflow_scope_without_legacy_research_routes(self):
        main_source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn('FastAPI(title="DocFlow 协作式文档 Agent"', main_source)
        self.assertNotIn('/api/research', main_source)
        self.assertNotIn('TaskRepository', main_source)

    def test_public_compose_binds_locally_and_requires_demo_credentials(self):
        compose = (ROOT / "compose.public-demo.yml").read_text(encoding="utf-8")
        self.assertIn('${DOCFLOW_BIND_ADDRESS:-127.0.0.1}', compose)
        self.assertIn("DOCFLOW_DEMO_USERNAME is required", compose)
        self.assertIn("DOCFLOW_DEMO_PASSWORD is required", compose)
        self.assertIn("DOCFLOW_RATE_LIMIT_PER_MINUTE", compose)
        self.assertIn("MODEL_INPUT_COST_PER_MILLION", compose)
        self.assertIn("MODEL_INPUT_CACHE_HIT_COST_PER_MILLION", compose)
        self.assertIn("MODEL_INPUT_CACHE_MISS_COST_PER_MILLION", compose)
        self.assertIn("MODEL_OUTPUT_COST_PER_MILLION", compose)
        self.assertIn("MODEL_COST_RATE_LABEL", compose)

    def test_environment_template_contains_no_real_secret(self):
        example = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("DOCFLOW_DEMO_MODE=false", example)
        self.assertIn("DOCFLOW_BIND_ADDRESS=127.0.0.1", example)
        self.assertIn("DOCFLOW_DEMO_PASSWORD=", example)
        self.assertIn("MODEL_COST_CURRENCY=CNY", example)
        self.assertIn("MODEL_INPUT_CACHE_HIT_COST_PER_MILLION=", example)
        self.assertIn("MODEL_INPUT_CACHE_MISS_COST_PER_MILLION=", example)
        self.assertIn("MODEL_COST_RATE_LABEL=", example)

    def test_readme_publishes_protected_demo_without_secret(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("https://", readme)
        self.assertIn("浏览器 HTTP Basic Auth", readme)
        self.assertIn("访问口令由本人随简历或面试邀请单独提供", readme)
        self.assertNotIn("DOCFLOW_DEMO_PASSWORD=", readme)
        self.assertIn("受限公网演示", readme)

    def test_model_acceptance_requires_real_calls_and_usage(self):
        script = (ROOT / "check-demo.ps1").read_text(encoding="utf-8")
        self.assertIn("[switch]$RequireModel", script)
        self.assertIn("model_path_complete", script)
        self.assertIn("model_call_count", script)
        self.assertIn("model_usage.total_tokens", script)
        self.assertIn("request_id", script)
        portable = (ROOT / "scripts" / "check_model_runtime.py").read_text(encoding="utf-8")
        self.assertIn("validate_model_run", portable)
        self.assertIn("model_path_complete", portable)
        self.assertIn("DOCFLOW_DEMO_PASSWORD", portable)
        self.assertNotIn('add_argument("--password"', portable)


if __name__ == "__main__":
    unittest.main()
