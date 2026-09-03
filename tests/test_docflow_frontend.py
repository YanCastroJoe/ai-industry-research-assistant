import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DocflowFrontendTests(unittest.TestCase):
    def test_run_decision_path_links_real_inspection_views(self) -> None:
        markup = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('class="decision-path"', markup)
        self.assertIn("renderDecisionPath(task, node)", script)
        self.assertIn("data-trace-target=\"citations\"", script)
        self.assertIn("data-trace-target=\"review\"", script)

    def test_decision_tones_do_not_collide_with_plan_list_selector(self) -> None:
        script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("decision-card tone-${card.tone}", script)
        self.assertNotIn("decision-card ${card.tone}", script)

    def test_workspace_uses_one_vertical_flow_and_hides_stale_idle_result(self) -> None:
        styles = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn("grid-template-columns: minmax(0,820px)", styles)
        self.assertIn(".workspace-grid.is-idle .result-panel { display: none; }", styles)
        self.assertNotIn("minmax(370px,440px) minmax(560px,1fr)", styles)

    def test_workspace_navigation_starts_a_fresh_run(self) -> None:
        script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("function resetWorkspace()", script)
        self.assertIn("form.reset();", script)
        self.assertIn("activeTaskId = null;", script)
        self.assertIn("cardHost.replaceChildren();", script)
        self.assertIn("setWorkspaceMode('idle');", script)
        self.assertIn("if (item.dataset.sectionTarget === 'workspace-view') resetWorkspace();", script)

    def test_browser_session_is_random_persistent_and_not_replaced_by_templates(self) -> None:
        markup = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertNotIn('value="default"', markup)
        self.assertIn("docflow.visitorSession.v1", script)
        self.assertIn("window.crypto?.randomUUID?.()", script)
        self.assertIn("headers.set('X-DocFlow-Session', getActiveSessionId())", script)
        self.assertNotIn("`${key}-demo`", script)
        self.assertIn("Promise.all([loadMemoriesForSession(), loadTasks()])", script)
        self.assertIn("sessionInput.addEventListener('input'", script)
        self.assertIn("taskList.replaceChildren()", script)

    def test_templates_download_and_citation_controls_are_explicit(self) -> None:
        markup = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertNotIn('id="sample-button"', markup)
        self.assertNotIn('class="template-main"', markup)
        self.assertIn('id="open-template-manager"', markup)
        self.assertIn('id="template-manager"', markup)
        self.assertIn('id="template-form"', markup)
        self.assertIn("/api/docflow/templates", script)
        self.assertIn('id="template-notice"', markup)
        self.assertIn('class="citation-toggle"', markup)
        self.assertIn('class="download-output"', markup)
        self.assertIn("stripEvidenceMarkers", script)
        self.assertIn("下载完整产出包 (.md)", script)

    def test_recent_runs_have_confirmed_delete_flow(self) -> None:
        markup = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="delete-dialog"', markup)
        self.assertIn('id="confirm-delete"', markup)
        self.assertIn('class="task-menu-trigger"', script)
        self.assertIn('class="history-menu-trigger"', script)
        self.assertIn("contextmenu", script)
        self.assertIn('id="task-context-menu"', markup)
        self.assertIn("openDeleteDialog(task)", script)
        self.assertIn("method: 'DELETE'", script)

    def test_memory_management_and_application_details_are_visible(self) -> None:
        markup = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="memory-list"', markup)
        self.assertIn('class="applied-memory"', markup)
        self.assertIn("Session Memory：召回", script)
        self.assertIn("loadMemoriesForSession", script)
        self.assertIn("method: 'PATCH'", script)
        self.assertIn("/api/docflow/memories/", script)

    def test_workflow_starts_with_context_builder_and_includes_verifier(self) -> None:
        markup = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn("Context Builder → SafePlanner → Tool Registry → Agent Runtime → Verifier → Human Review", markup)
        self.assertIn("Instruction · Sources · Session Memory · Evidence", markup)

    def test_runtime_status_degradation_and_verifier_layers_are_explicit(self) -> None:
        markup = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="backend-status"', markup)
        self.assertIn('id="database-status"', markup)
        self.assertIn('id="model-status"', markup)
        self.assertIn('id="expected-mode"', markup)
        self.assertIn('class="execution-alert"', markup)
        self.assertIn('class="verification-gates"', markup)
        self.assertIn('class="raw-json"', markup)
        self.assertIn('class="mode-breakdown"', markup)
        self.assertIn("本次已降级为本地规则", script)
        self.assertIn("引用存在性", script)
        self.assertIn("字段一致性", script)
        self.assertIn("语义支持性", script)
        self.assertIn("Verifier 通过但人工驳回", script)

    def test_structured_capability_error_is_shown_as_readable_message(self) -> None:
        script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("detail?.message", script)
        self.assertIn("throw new Error(message", script)

    def test_collapsed_sidebar_keeps_workspace_in_the_remaining_grid_column(self) -> None:
        styles = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")

        self.assertIn(
            ".app-shell.sidebar-collapsed { grid-template-columns: 72px minmax(0,1fr); }",
            styles,
        )
        self.assertNotIn(
            ".app-shell.sidebar-collapsed { grid-template-columns: 72px 0 minmax(0,1fr); }",
            styles,
        )


if __name__ == "__main__":
    unittest.main()
