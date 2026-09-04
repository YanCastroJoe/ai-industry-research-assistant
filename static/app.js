const form = document.querySelector('#docflow-form');
const message = document.querySelector('#form-message');
const cardHost = document.querySelector('#card');
const emptyState = document.querySelector('#empty-state');
const taskList = document.querySelector('#task-list');
const templateNotice = document.querySelector('#template-notice');
const refreshEvaluationButton = document.querySelector('#refresh-evaluation');
const jobProgress = document.querySelector('#job-progress');
const appShell = document.querySelector('.app-shell');
const sidebarResizer = document.querySelector('#sidebar-resizer');
const sidebarToggle = document.querySelector('#sidebar-toggle');
const historyList = document.querySelector('#history-list');
const navItems = [...document.querySelectorAll('[data-section-target]')];
const resultTabs = [...document.querySelectorAll('[data-result-target]')];
const workspaceGrid = document.querySelector('#workspace-grid');
const exampleTasks = [...document.querySelectorAll('[data-example]')];
const deleteDialog = document.querySelector('#delete-dialog');
const deleteDescription = document.querySelector('#delete-dialog-description');
const deleteFeedback = document.querySelector('#delete-dialog-feedback');
const cancelDeleteButton = document.querySelector('#cancel-delete');
const confirmDeleteButton = document.querySelector('#confirm-delete');
const openTemplateManagerButton = document.querySelector('#open-template-manager');
const closeTemplateManagerButton = document.querySelector('#close-template-manager');
const templateForm = document.querySelector('#template-form');
const templateFormMessage = document.querySelector('#template-form-message');
const customTemplateList = document.querySelector('#custom-template-list');
const templateLibraryList = document.querySelector('#template-library-list');
const taskContextMenu = document.querySelector('#task-context-menu');
const menuDeleteTaskButton = document.querySelector('#menu-delete-task');
const memoryList = document.querySelector('#memory-list');
const refreshMemoriesButton = document.querySelector('#refresh-memories');
const backendStatus = document.querySelector('#backend-status');
const databaseStatus = document.querySelector('#database-status');
const modelStatus = document.querySelector('#model-status');
const expectedMode = document.querySelector('#expected-mode');
let activeTaskId = null;
let pendingDeleteTask = null;
let contextMenuTask = null;
let customTemplates = [];

const VISITOR_SESSION_STORAGE_KEY = 'docflow.visitorSession.v1';
const VISITOR_SESSION_PATTERN = /^[A-Za-z0-9._:-]{8,100}$/;
let sessionRefreshTimer = null;

function createVisitorSessionId() {
  const token = window.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}-${Math.random().toString(16).slice(2)}`;
  return `visitor-${token}`;
}

function getActiveSessionId() {
  const input = document.querySelector('#session-id');
  let sessionId = input.value.trim();
  if (!VISITOR_SESSION_PATTERN.test(sessionId) || sessionId === 'default') {
    try { sessionId = localStorage.getItem(VISITOR_SESSION_STORAGE_KEY) || ''; } catch (_) { sessionId = ''; }
  }
  if (!VISITOR_SESSION_PATTERN.test(sessionId) || sessionId === 'default') sessionId = createVisitorSessionId();
  input.value = sessionId;
  try { localStorage.setItem(VISITOR_SESSION_STORAGE_KEY, sessionId); } catch (_) { /* Storage may be disabled. */ }
  return sessionId;
}

getActiveSessionId();

const demoExamples = {
  weekly: {
    title: '智能客服项目第 3 周周报',
    goal: '基于材料生成本周项目周报、风险清单和三页汇报大纲；每条结论标注来源。',
    text: `项目：智能客服知识库升级
本周进展：完成售后 FAQ 清洗与检索链路联调，24 条核心问法通过验收。
风险：退款政策文档仍有两个版本，负责人李明需在周五前确认最终口径。
行动：王芳负责补充退货运费边界案例，下周二完成回归测试。
会议结论：所有面向客户的回答必须附带当前知识库来源。`,
    audience: '管理层',
    focus: 'risk',
  },
  support: {
    title: '售后知识库上线验收',
    goal: '整理上线验收结论、未覆盖的知识边界和待修复事项，并为每项判断标注材料来源。',
    text: `验收范围：退款、退货运费、质量问题与物流时效四类售后问答。
测试结果：30 条固定问题中 28 条回答符合预期，质量问题与偏远地区运费各有 1 条口径不完整。
风险：旧版退款政策仍在知识库中，可能造成回答冲突。
行动：陈晨今天下线旧文档；刘洋周三补齐两条边界案例并执行回归测试。`,
    audience: '技术团队',
    focus: 'actions',
  },
  review: {
    title: 'Agent 功能需求评审纪要',
    goal: '提取评审结论、争议点、负责人和截止时间，形成可执行的需求纪要。',
    text: `评审主题：为文档 Agent 增加异步任务与失败恢复能力。
结论：一期采用任务队列和 SQLite Checkpoint，不引入外部消息队列。
争议：是否允许用户跳过人工审核直接导出，暂不开放。
行动：张伟本周完成接口设计；赵敏下周一补齐异常场景测试；产品负责人周五确认导出权限。`,
    audience: '项目团队',
    focus: 'actions',
  },
};

function setWorkspaceMode(mode) {
  workspaceGrid.classList.toggle('is-idle', mode === 'idle');
  workspaceGrid.classList.toggle('is-active', mode === 'active');
}

function loadTemplate(example, key = '') {
  resetWorkspace();
  document.querySelector('#title').value = example.title;
  document.querySelector('#session-id').value = getActiveSessionId();
  document.querySelector('#run-mode').value = 'async';
  document.querySelector('#memory').value = '面向目标受众，优先展示风险、负责人和截止时间';
  document.querySelector('#audience').value = example.audience;
  document.querySelector('#context-focus').value = example.focus;
  document.querySelector('#evidence-limit').value = '12';
  document.querySelector('#citation-policy').value = 'strict';
  document.querySelector('#memory-enabled').checked = true;
  document.querySelector('#goal').value = example.goal;
  document.querySelector('#text').value = example.text || example.source_text || '';
  exampleTasks.forEach((button) => button.classList.toggle('loaded', Boolean(key) && button.dataset.example === key));
  templateNotice.textContent = `已载入“${example.title}”模板，内容可继续修改后运行。`;
  templateNotice.hidden = false;
  activateMainView('workspace-view');
  setWorkspaceMode('idle');
  loadMemoriesForSession();
  document.querySelector('#goal').focus();
}

function loadDemoExample(key = 'weekly') {
  loadTemplate(demoExamples[key] || demoExamples.weekly, key);
}

const SIDEBAR_DEFAULT_WIDTH = 248;
const SIDEBAR_MIN_WIDTH = 220;
const SIDEBAR_MAX_WIDTH = 420;
const SIDEBAR_STORAGE_KEY = 'docflow.sidebarWidth';
const SIDEBAR_COLLAPSED_KEY = 'docflow.sidebarCollapsed';

function clampSidebarWidth(width) {
  return Math.min(SIDEBAR_MAX_WIDTH, Math.max(SIDEBAR_MIN_WIDTH, Math.round(width)));
}

function applySidebarWidth(width, persist = false) {
  const nextWidth = clampSidebarWidth(width);
  appShell.style.setProperty('--sidebar-width', `${nextWidth}px`);
  sidebarResizer.setAttribute('aria-valuenow', String(nextWidth));
  if (persist) {
    try { localStorage.setItem(SIDEBAR_STORAGE_KEY, String(nextWidth)); } catch (_) { /* Storage may be disabled. */ }
  }
  return nextWidth;
}

function setupSidebarResize() {
  let storedWidth = SIDEBAR_DEFAULT_WIDTH;
  try { storedWidth = Number.parseInt(localStorage.getItem(SIDEBAR_STORAGE_KEY), 10) || SIDEBAR_DEFAULT_WIDTH; } catch (_) { /* Use default. */ }
  applySidebarWidth(storedWidth);

  let dragStartX = 0;
  let dragStartWidth = storedWidth;

  const stopDragging = () => {
    if (!sidebarResizer.classList.contains('dragging')) return;
    sidebarResizer.classList.remove('dragging');
    document.body.classList.remove('sidebar-resizing');
    applySidebarWidth(Number.parseInt(sidebarResizer.getAttribute('aria-valuenow'), 10), true);
  };

  sidebarResizer.addEventListener('pointerdown', (event) => {
    if (window.innerWidth <= 900) return;
    event.preventDefault();
    dragStartX = event.clientX;
    dragStartWidth = Number.parseInt(sidebarResizer.getAttribute('aria-valuenow'), 10) || SIDEBAR_DEFAULT_WIDTH;
    sidebarResizer.classList.add('dragging');
    document.body.classList.add('sidebar-resizing');
    sidebarResizer.setPointerCapture?.(event.pointerId);
  });

  sidebarResizer.addEventListener('pointermove', (event) => {
    if (!sidebarResizer.classList.contains('dragging')) return;
    applySidebarWidth(dragStartWidth + event.clientX - dragStartX);
  });

  sidebarResizer.addEventListener('pointerup', stopDragging);
  sidebarResizer.addEventListener('pointercancel', stopDragging);
  sidebarResizer.addEventListener('dblclick', () => applySidebarWidth(SIDEBAR_DEFAULT_WIDTH, true));
  sidebarResizer.addEventListener('keydown', (event) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home'].includes(event.key)) return;
    event.preventDefault();
    const current = Number.parseInt(sidebarResizer.getAttribute('aria-valuenow'), 10) || SIDEBAR_DEFAULT_WIDTH;
    const next = event.key === 'Home' ? SIDEBAR_DEFAULT_WIDTH : current + (event.key === 'ArrowRight' ? 16 : -16);
    applySidebarWidth(next, true);
  });
}

setupSidebarResize();

function setSidebarCollapsed(collapsed, persist = true) {
  appShell.classList.toggle('sidebar-collapsed', collapsed);
  sidebarToggle.setAttribute('aria-expanded', String(!collapsed));
  sidebarToggle.setAttribute('aria-label', collapsed ? '展开侧边栏' : '收起侧边栏');
  sidebarToggle.textContent = collapsed ? '›' : '‹';
  if (persist) {
    try { localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(collapsed)); } catch (_) { /* Storage may be disabled. */ }
  }
}

function activateMainView(targetId) {
  document.querySelectorAll('.main-view').forEach((view) => {
    const active = view.id === targetId;
    view.hidden = !active;
    view.classList.toggle('active', active);
  });
  navItems.forEach((item) => item.classList.toggle('active', item.dataset.sectionTarget === targetId));
}

function openTemplateManager() {
  activateMainView('template-manager');
  window.scrollTo({ top: 0, behavior: 'smooth' });
  document.querySelector('#template-name').focus();
}

function resetWorkspace() {
  form.reset();
  document.querySelector('#session-id').value = getActiveSessionId();
  activeTaskId = null;
  taskList.querySelectorAll('.task.active').forEach((item) => item.classList.remove('active'));
  document.querySelectorAll('.advanced-settings[open]').forEach((section) => { section.open = false; });
  cardHost.replaceChildren();
  cardHost.hidden = true;
  emptyState.hidden = false;
  message.textContent = '';
  jobProgress.hidden = true;
  templateNotice.hidden = true;
  templateNotice.textContent = '';
  exampleTasks.forEach((button) => button.classList.remove('loaded'));
  setWorkspaceMode('idle');
  activateResultView('result');
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function activateResultView(target) {
  resultTabs.forEach((tab) => {
    const active = tab.dataset.resultTarget === target;
    tab.classList.toggle('active', active);
    tab.setAttribute('aria-selected', String(active));
  });
  cardHost.querySelectorAll('[data-result-view]').forEach((view) => {
    const active = view.dataset.resultView === target;
    view.hidden = !active;
    view.classList.toggle('active', active);
  });
}

try { setSidebarCollapsed(localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === 'true', false); } catch (_) { setSidebarCollapsed(false, false); }
sidebarToggle.addEventListener('click', () => setSidebarCollapsed(!appShell.classList.contains('sidebar-collapsed')));
navItems.forEach((item) => item.addEventListener('click', (event) => {
  event.preventDefault();
  if (item.dataset.sectionTarget === 'workspace-view') resetWorkspace();
  activateMainView(item.dataset.sectionTarget);
}));
resultTabs.forEach((tab) => tab.addEventListener('click', () => activateResultView(tab.dataset.resultTarget)));

const statusLabels = {
  queued: '排队中', running: '运行中', awaiting_review: '待人工审核', approved: '审核通过', rejected: '已驳回', failed: '运行失败',
};

const traceStatusLabels = { completed: '已完成', retrying: '重试中', failed: '失败' };
const toolLabels = {
  retrieve_documents: '检索证据',
  extract_facts: '事实抽取',
  derive_task_insights: '任务理解',
  compose_document: '生成周报',
  generate_risk_register: '风险清单',
  generate_slide_outline: '汇报大纲',
  verify_citations: '引用校验',
};

async function request(url, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set('X-DocFlow-Session', getActiveSessionId());
  const response = await fetch(url, { ...options, headers });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const detail = data.detail;
    const message = typeof detail === 'string' ? detail : detail?.message;
    throw new Error(message || '请求失败，请稍后重试。');
  }
  return response;
}

function setRuntimeChip(chip, text, state = 'ok') {
  chip.textContent = text;
  chip.classList.remove('status-ok', 'status-warning', 'status-error', 'status-unknown');
  chip.classList.add(`status-${state}`);
}

async function loadRuntimeStatus() {
  try {
    const readiness = await (await request('/ready')).json();
    setRuntimeChip(backendStatus, readiness.service?.ok ? '后端正常' : '后端异常', readiness.service?.ok ? 'ok' : 'error');
    setRuntimeChip(databaseStatus, readiness.database?.ok ? '数据库正常' : '数据库异常', readiness.database?.ok ? 'ok' : 'error');
    const runtime = readiness.runtime || {};
    if (!runtime.model_configured) {
      setRuntimeChip(modelStatus, '模型未配置', 'warning');
      expectedMode.textContent = '预计执行模式：本地规则（未配置模型）';
    } else if (runtime.model_reachability === 'reachable') {
      setRuntimeChip(modelStatus, '模型最近调用成功', 'ok');
      expectedMode.textContent = `模型：${runtime.model_name}；最近一次真实调用可达，失败时仍会自动降级`;
    } else if (runtime.model_reachability === 'unavailable') {
      setRuntimeChip(modelStatus, '模型最近调用失败', 'warning');
      expectedMode.textContent = `模型：${runtime.model_name}；最近一次调用失败，当前保留本地规则降级`;
    } else {
      setRuntimeChip(modelStatus, '模型已配置 · 待运行验证', 'unknown');
      expectedMode.textContent = '预计执行模式：优先模型；调用失败时自动降级为本地规则';
    }
  } catch (error) {
    setRuntimeChip(backendStatus, '后端不可用', 'error');
    setRuntimeChip(databaseStatus, '数据库状态未知', 'unknown');
    setRuntimeChip(modelStatus, '模型状态未知', 'unknown');
    expectedMode.textContent = `预计执行模式：状态检查失败（${error.message}）`;
  }
}

function formatMemoryTime(value) {
  if (!value) return '未知时间';
  const normalized = value.includes('T') ? value : `${value.replace(' ', 'T')}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}

function renderMemories(memories) {
  if (!memories.length) {
    memoryList.innerHTML = '<p class="memory-empty">当前会话没有 Memory，填写“协作偏好”并运行后会保存。</p>';
    return;
  }
  memoryList.replaceChildren(...memories.map((memory) => {
    const item = document.createElement('article');
    item.className = `memory-item ${memory.enabled ? '' : 'disabled'}`;
    const source = memory.source_task_id ? `任务 #${memory.source_task_id}` : '工作台手动输入';
    item.innerHTML = `<div class="memory-item-head"><strong>${escapeHtml(memory.memory_key)}</strong><span>${memory.enabled ? '已启用' : '已禁用'}</span></div><p>${escapeHtml(memory.content)}</p><small>来源：${escapeHtml(source)} · 创建：${escapeHtml(formatMemoryTime(memory.created_at))}<br>更新：${escapeHtml(formatMemoryTime(memory.updated_at || memory.created_at))}</small><div class="memory-item-actions"><button class="secondary memory-edit" type="button">编辑</button><button class="secondary memory-toggle" type="button">${memory.enabled ? '禁用' : '启用'}</button><button class="secondary memory-delete" type="button">删除</button></div>`;
    item.querySelector('.memory-edit').addEventListener('click', () => {
      if (item.querySelector('.memory-editor')) return;
      const editor = document.createElement('div');
      editor.className = 'memory-editor';
      editor.innerHTML = `<textarea aria-label="编辑 ${escapeHtml(memory.memory_key)}">${escapeHtml(memory.content)}</textarea><div class="memory-item-actions"><button type="button" class="memory-save">保存修改</button><button type="button" class="secondary memory-cancel">取消</button></div>`;
      editor.querySelector('.memory-cancel').addEventListener('click', () => editor.remove());
      editor.querySelector('.memory-save').addEventListener('click', async () => {
        const content = editor.querySelector('textarea').value.trim();
        if (content.length < 2) return;
        await request(`/api/docflow/memories/${memory.id}`, {
          method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content }),
        });
        await loadMemoriesForSession();
      });
      item.append(editor);
      editor.querySelector('textarea').focus();
    });
    item.querySelector('.memory-toggle').addEventListener('click', async () => {
      await request(`/api/docflow/memories/${memory.id}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled: !Boolean(memory.enabled) }),
      });
      await loadMemoriesForSession();
    });
    item.querySelector('.memory-delete').addEventListener('click', async () => {
      if (!window.confirm(`确定删除“${memory.memory_key}”吗？此操作不可撤销。`)) return;
      await request(`/api/docflow/memories/${memory.id}`, { method: 'DELETE' });
      await loadMemoriesForSession();
    });
    return item;
  }));
}

async function loadMemoriesForSession() {
  const sessionId = getActiveSessionId();
  refreshMemoriesButton.disabled = true;
  try {
    const memories = await (await request(`/api/docflow/memories/${encodeURIComponent(sessionId)}`)).json();
    renderMemories(memories);
  } catch (error) {
    memoryList.innerHTML = `<p class="memory-empty">Memory 加载失败：${escapeHtml(error.message)}</p>`;
  } finally {
    refreshMemoriesButton.disabled = false;
  }
}

function renderCustomTemplates() {
  const createTemplateButton = (template, className = 'example-task custom-template') => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = className;
    button.innerHTML = `<strong>${escapeHtml(template.name)}</strong><small>${escapeHtml(template.audience)} · 自定义</small>`;
    button.addEventListener('click', () => loadTemplate(template));
    return button;
  };
  customTemplateList.replaceChildren(...customTemplates.map((template) => createTemplateButton(template)));
  if (!customTemplates.length) {
    const empty = document.createElement('p');
    empty.className = 'template-library-empty';
    empty.textContent = '还没有自定义模板。';
    templateLibraryList.replaceChildren(empty);
    return;
  }
  templateLibraryList.replaceChildren(...customTemplates.map((template) => {
    const item = document.createElement('article');
    item.className = 'template-library-item';
    item.innerHTML = `<div><strong>${escapeHtml(template.name)}</strong><p>${escapeHtml(template.title)}</p><small>${escapeHtml(template.audience)} · ${escapeHtml(template.focus)}</small></div>`;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'secondary';
    button.textContent = '带入工作台';
    button.addEventListener('click', () => loadTemplate(template));
    item.append(button);
    return item;
  }));
}

async function loadCustomTemplates() {
  customTemplates = await (await request('/api/docflow/templates')).json();
  renderCustomTemplates();
}

function closeTaskContextMenu() {
  taskContextMenu.hidden = true;
  contextMenuTask = null;
}

function openTaskContextMenu(task, x, y) {
  if (['queued', 'running'].includes(task.status)) return;
  contextMenuTask = task;
  taskContextMenu.hidden = false;
  const menuWidth = 140;
  const menuHeight = 46;
  taskContextMenu.style.left = `${Math.max(8, Math.min(x, window.innerWidth - menuWidth - 8))}px`;
  taskContextMenu.style.top = `${Math.max(8, Math.min(y, window.innerHeight - menuHeight - 8))}px`;
  menuDeleteTaskButton.focus();
}

function openTaskMenuFromButton(task, button) {
  const rect = button.getBoundingClientRect();
  openTaskContextMenu(task, rect.right - 132, rect.bottom + 4);
}

function escapeHtml(value) {
  const element = document.createElement('div');
  element.textContent = value || '';
  return element.innerHTML;
}

function stripEvidenceMarkers(value) {
  return String(value || '').replace(/\s*\[E\d+\]/g, '').replace(/[ \t]+\n/g, '\n').trim();
}

function renderRiskRegister(markdown, host, showCitations = false) {
  const lines = markdown.split('\n').map((line) => line.trim()).filter(Boolean);
  const title = lines.find((line) => line.startsWith('## '));
  const tableLines = lines.filter((line) => line.startsWith('|'));
  if (tableLines.length < 3) {
    host.textContent = markdown;
    return;
  }
  const cells = (line) => line.split('|').slice(1, -1).map((cell) => escapeHtml(cell.trim()));
  let headers = cells(tableLines[0]);
  let rows = tableLines.slice(2).map((line) => cells(line));
  if (!showCitations) {
    const evidenceColumn = headers.findIndex((header) => header.includes('证据'));
    if (evidenceColumn >= 0) {
      headers = headers.filter((_, index) => index !== evidenceColumn);
      rows = rows.map((row) => row.filter((_, index) => index !== evidenceColumn));
    }
    rows = rows.map((row) => row.map(stripEvidenceMarkers));
  }
  host.innerHTML = `${title ? `<p class="artifact-heading">${escapeHtml(title.slice(3))}</p>` : ''}<div class="risk-table-scroll"><table class="risk-table"><thead><tr>${headers.map((cell) => `<th>${cell}</th>`).join('')}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((cell) => `<td>${cell}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}

function renderArtifacts(artifacts, node, showCitations = false) {
  const display = (value) => showCitations ? value : stripEvidenceMarkers(value);
  node.querySelector('.artifact.document').textContent = display(artifacts.weekly_report_markdown || '尚未生成项目周报。');
  if (artifacts.risk_register_markdown) {
    node.querySelector('.table-wrap').hidden = false;
    renderRiskRegister(artifacts.risk_register_markdown, node.querySelector('.artifact.table'), showCitations);
  }
  if (artifacts.slide_outline_markdown) {
    node.querySelector('.slides-wrap').hidden = false;
    node.querySelector('.artifact.slides').textContent = display(artifacts.slide_outline_markdown);
  }
}

function formatPayload(value, limit = 900) {
  const output = typeof value === 'string' ? value : JSON.stringify(value || {}, null, 2);
  return output.length > limit ? `${output.slice(0, limit)}\n…已截断` : output;
}

function formatRate(value) {
  return value === null || value === undefined ? '--' : `${Math.round(value * 100)}%`;
}

function formatEvaluationValue(metric, value) {
  if (value === null || value === undefined) return '--';
  return metric.includes('latency') ? `${value} ms` : formatRate(value);
}

function formatCost(value, currency) {
  if (value === null || value === undefined) return '未配置单价';
  return `${currency || ''} ${Number(value).toFixed(6)}`.trim();
}

function formatInteger(value) {
  return new Intl.NumberFormat('zh-CN').format(Number(value || 0));
}

function updateJobProgress(job) {
  const percent = Math.max(0, Math.min(100, Number(job.progress_percent || 0)));
  const labels = { queued: '任务排队中', running: 'Agent 正在执行', awaiting_review: '执行完成，等待审核', failed: '任务执行失败' };
  jobProgress.hidden = false;
  jobProgress.querySelector('.job-progress-label').textContent = labels[job.status] || statusLabels[job.status] || job.status;
  jobProgress.querySelector('.job-progress-value').textContent = `${percent}%`;
  jobProgress.querySelector('.job-progress-track i').style.width = `${percent}%`;
  const queue = job.queue || {};
  const step = job.current_tool ? `当前工具：${toolLabels[job.current_tool] || job.current_tool}` : '正在准备执行计划';
  jobProgress.querySelector('.job-progress-detail').textContent = job.status === 'queued'
    ? `队列中 ${queue.queued ?? 0} 个任务，运行中 ${queue.running ?? 0} 个任务`
    : `${step} · 已完成 ${job.completed_steps || 0}/${job.planned_steps || '--'} 步`;
}

const wait = (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));

async function pollJob(taskId) {
  const deadline = Date.now() + 120000;
  while (Date.now() < deadline) {
    const job = await (await request(`/api/docflow/jobs/${taskId}`)).json();
    updateJobProgress(job);
    if (job.terminal) {
      if (job.status === 'failed') throw new Error(job.error || 'Agent 运行失败，请查看运行轨迹。');
      return (await request(`/api/docflow/tasks/${taskId}`)).json();
    }
    await wait(350);
  }
  throw new Error('任务仍在后台执行，可稍后从“最近运行”中查看。');
}

async function loadEvaluationSummary() {
  const summary = await (await request('/api/docflow/evaluations/summary')).json();
  const metrics = summary.metrics || {};
  const costCoverage = metrics.cost_coverage_rate === null || metrics.cost_coverage_rate === undefined
    ? '无模型调用'
    : `计价覆盖 ${formatRate(metrics.cost_coverage_rate)}`;
  const rateLabel = metrics.cost_rate_labels?.length ? ` · ${metrics.cost_rate_labels.join(' / ')}` : '';
  const cards = [
    [`${metrics.successful_task_count ?? 0}/${metrics.terminal_count ?? 0}`, '终态任务执行成功'],
    [formatRate(metrics.execution_success_rate), '任务执行成功率'],
    [formatRate(metrics.degraded_task_rate), '模型降级任务率'],
    [formatRate(metrics.retry_task_rate), '发生重试的任务'],
    [formatRate(metrics.citation_pass_rate), '引用通过率'],
    [formatRate(metrics.approval_rate), '人工通过率'],
    [metrics.verifier_human_miss_rate === null ? '--' : `${metrics.verifier_human_miss_count || 0} 条`, 'Verifier 通过但人工驳回'],
    [metrics.latency_p50_ms === null ? '--' : `${metrics.latency_p50_ms} ms`, 'P50 运行耗时'],
    [metrics.latency_p95_ms === null ? '--' : `${metrics.latency_p95_ms} ms`, 'P95 运行耗时'],
    [formatInteger(metrics.model_usage?.total_tokens), '模型 Token 总量'],
    [formatCost(metrics.estimated_cost_total, metrics.cost_currency), `已计价成本 · ${costCoverage}${rateLabel}`],
  ];
  document.querySelector('.evaluation-metrics').innerHTML = cards.map(([value, label]) => `<article><strong>${escapeHtml(String(value))}</strong><span>${escapeHtml(label)}</span></article>`).join('');
  const modeLabels = { model: '模型模式', rules: '本地规则', rules_fallback: '规则降级' };
  document.querySelector('.mode-breakdown').replaceChildren(...(summary.mode_breakdown || []).map((item) => {
    const card = document.createElement('article');
    const cost = formatCost(item.estimated_cost_total, item.cost_currency);
    card.innerHTML = `<strong>${escapeHtml(modeLabels[item.mode] || item.mode)}</strong><span>${escapeHtml(String(item.task_count))} 个任务 · P50 ${escapeHtml(String(item.latency_p50_ms ?? '--'))} / P95 ${escapeHtml(String(item.latency_p95_ms ?? '--'))} ms</span><small>降级 ${escapeHtml(formatRate(item.degraded_task_rate))} · 重试 ${escapeHtml(formatRate(item.retry_task_rate))} · ${escapeHtml(formatInteger(item.total_tokens))} Tokens · ${escapeHtml(cost)}</small>`;
    return card;
  }));

  const stageLabels = { planner: '规划阶段', content: '内容理解阶段' };
  const stageItems = summary.model_stage_breakdown || [];
  document.querySelector('.model-stage-breakdown').replaceChildren(...(stageItems.length ? stageItems.map((item) => {
    const card = document.createElement('article');
    const coverage = item.cost_coverage_rate === null || item.cost_coverage_rate === undefined ? '--' : formatRate(item.cost_coverage_rate);
    card.innerHTML = `<div><strong>${escapeHtml(stageLabels[item.stage] || item.stage)}</strong><span>${escapeHtml(String(item.call_count))} 次调用 · 成功 ${escapeHtml(formatRate(item.success_rate))}</span></div><p>P50 ${escapeHtml(String(item.latency_p50_ms ?? '--'))} ms · P95 ${escapeHtml(String(item.latency_p95_ms ?? '--'))} ms</p><small>${escapeHtml(formatInteger(item.total_tokens))} Tokens · ${escapeHtml(formatCost(item.estimated_cost_total, item.cost_currency))} · 计价覆盖 ${escapeHtml(coverage)}</small>`;
    return card;
  }) : [Object.assign(document.createElement('p'), { className: 'model-stage-empty', textContent: '当前 Session 尚无真实模型调用；运行模型任务后显示 Planner 与内容理解阶段诊断。' })]));

  const gateHost = document.querySelector('.quality-gates');
  gateHost.replaceChildren(...(summary.quality_gates || []).map((gate) => {
    const item = document.createElement('article');
    const state = gate.passed === null ? 'no-data' : gate.passed ? 'passed' : 'failed';
    const target = gate.metric.includes('latency') ? `${gate.target} ms` : `${Math.round(gate.target * 100)}%`;
    item.className = `quality-gate ${state}`;
    item.innerHTML = `<span class="gate-icon">${gate.passed === null ? '–' : gate.passed ? '✓' : '!'}</span><div><strong>${escapeHtml(gate.name)}</strong><p>${escapeHtml(gate.description)}</p></div><small>${escapeHtml(formatEvaluationValue(gate.metric, gate.value))} / ${escapeHtml(gate.operator)} ${escapeHtml(target)}</small>`;
    return item;
  }));

  const tableBody = document.querySelector('.evaluation-table tbody');
  tableBody.replaceChildren(...(summary.recent_tasks || []).map((task) => {
    const row = document.createElement('tr');
    const issues = task.issues?.length ? task.issues.join('；') : '无异常';
    const mode = modeLabels[task.execution_mode] || task.execution_mode || task.planner_mode || '未执行';
    const modelUsage = task.model_call_count
      ? `${task.model_call_count} 次 · ${formatInteger(task.total_tokens)} Tokens<br><small>${task.model_latency_ms ?? '--'} ms · ${formatCost(task.estimated_cost, task.cost_currency)}</small>`
      : '无模型调用';
    row.innerHTML = `<td><button type="button" class="evaluation-task-link">${escapeHtml(task.title)}</button><small>#${escapeHtml(String(task.task_id))} · ${escapeHtml(task.updated_at || '')}</small></td><td><span class="run-status ${escapeHtml(task.status)}">${escapeHtml(statusLabels[task.status] || task.status)}</span></td><td>${escapeHtml(mode)}</td><td>${task.elapsed_ms === null || task.elapsed_ms === undefined ? '--' : `${escapeHtml(String(task.elapsed_ms))} ms`}</td><td>${modelUsage}</td><td class="${task.issues?.length ? 'diagnostic-warning' : 'diagnostic-ok'}">${escapeHtml(issues)}</td>`;
    row.querySelector('.evaluation-task-link').addEventListener('click', async () => {
      renderTask(await (await request(`/api/docflow/tasks/${task.task_id}`)).json());
      activateMainView('workspace-view');
      document.querySelector('#detail-panel').scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
    return row;
  }));
  document.querySelector('.evaluation-notice').textContent = summary.notice || '';
}

function renderContextManifest(context, node) {
  const summary = node.querySelector('.context-summary');
  const layers = node.querySelector('.context-layers');
  const policies = node.querySelector('.context-policies');
  const manifest = context || {};
  const badges = [
    ['受众', manifest.audience || '项目团队'],
    ['焦点', manifest.focus || '均衡呈现'],
    ['Memory', `${manifest.memory?.applied || 0}/${manifest.memory?.recalled || 0} 已应用`],
    ['证据预算', `${manifest.evidence_budget || 12} 条`],
    ['引用', manifest.citation_policy === 'strict' ? '严格校验' : '标准校验'],
  ];
  summary.innerHTML = badges.map(([label, value]) => `<span><small>${escapeHtml(label)}</small><strong>${escapeHtml(value)}</strong></span>`).join('');
  layers.replaceChildren(...(manifest.layers || []).map((layer, index) => {
    const item = document.createElement('article');
    item.className = `context-layer context-${escapeHtml(layer.key || '')}`;
    item.innerHTML = `<div><span>${String(index + 1).padStart(2, '0')}</span><strong>${escapeHtml(layer.label)}</strong></div><p>${escapeHtml(layer.role)}</p><small>${escapeHtml(String(layer.items || 0))} 项 · ${escapeHtml(String(layer.characters || 0))} 字符</small>`;
    return item;
  }));
  policies.replaceChildren(...(manifest.policies || []).map((policy) => {
    const item = document.createElement('li');
    item.textContent = policy;
    return item;
  }));
}

function renderExecutionFlow(task, node) {
  const host = node.querySelector('.execution-flow');
  const detail = node.querySelector('.trace-detail');
  const steps = task.runs?.[0]?.steps || [];
  const groups = [...steps.reduce((map, step) => {
    if (!map.has(step.sequence)) map.set(step.sequence, []);
    map.get(step.sequence).push(step);
    return map;
  }, new Map()).entries()];

  const flowItems = [];
  if (task.result?.context) {
    const memory = task.result.memory || {};
    flowItems.push({
      key: 'context', label: 'Context Builder', subtitle: `${task.result.context.layers?.length || 0} 层 · Memory ${memory.applied || 0}/${memory.recalled || 0}`, status: 'completed',
      detail: `<div class="trace-detail-head"><div><small>CONTEXT BUILDER</small><h4>Instruction · Sources · Session Memory · Evidence</h4></div><span class="trace-state completed">已完成</span></div><p>受众：${escapeHtml(memory.effective_audience || task.result.context.audience)} · 焦点：${escapeHtml(memory.effective_focus || task.result.context.focus)} · Memory：召回 ${escapeHtml(String(memory.recalled || 0))} 条 / 应用 ${escapeHtml(String(memory.applied || 0))} 条 · Evidence Budget：${escapeHtml(String(task.result.context.evidence_budget))}</p>`,
    });
  }
  groups.forEach(([sequence, attempts]) => {
    const latest = attempts[attempts.length - 1];
    const completed = attempts.find((item) => item.status === 'completed');
    const status = completed ? 'completed' : latest.status;
    const elapsed = attempts.reduce((total, item) => total + Number(item.elapsed_ms || 0), 0);
    flowItems.push({
      key: `step-${sequence}`,
      label: toolLabels[latest.tool_name] || latest.tool_name,
      subtitle: `${attempts.length} 次尝试 · ${elapsed} ms`,
      status,
      detail: `<div class="trace-detail-head"><div><small>STEP ${escapeHtml(String(sequence))} · ${escapeHtml(latest.phase || 'runtime')}</small><h4>${escapeHtml(toolLabels[latest.tool_name] || latest.tool_name)}</h4><code>${escapeHtml(latest.tool_name)}</code></div><span class="trace-state ${escapeHtml(status)}">${escapeHtml(traceStatusLabels[status] || status)}</span></div><div class="trace-io"><div><strong>Input</strong><pre>${escapeHtml(formatPayload(latest.input))}</pre></div><div><strong>Output</strong><pre>${escapeHtml(formatPayload((completed || latest).output))}</pre></div></div>${latest.error ? `<p class="trace-error">${escapeHtml(latest.error)}</p>` : ''}<p class="trace-meta">${attempts.length} 次尝试 · 累计 ${elapsed} ms${attempts.length > 1 ? ' · 已记录重试轨迹' : ''}</p>${traceLinkage(latest.tool_name, status, task)}`,
    });
  });

  const selectItem = (button, item) => {
    host.querySelectorAll('.flow-node').forEach((candidate) => candidate.classList.toggle('active', candidate === button));
    detail.innerHTML = item.detail;
    detail.querySelectorAll('[data-trace-target]').forEach((link) => {
      link.addEventListener('click', () => activateResultView(link.dataset.traceTarget));
    });
  };
  flowItems.forEach((item, index) => {
    if (index > 0) {
      const connector = document.createElement('span');
      connector.className = 'flow-connector';
      connector.innerHTML = '<i></i><b>›</b>';
      host.append(connector);
    }
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `flow-node ${item.status}`;
    button.innerHTML = `<span class="flow-index">${index === 0 && item.key === 'context' ? 'C' : String(index).padStart(2, '0')}</span><span><strong>${escapeHtml(item.label)}</strong><small>${escapeHtml(item.subtitle)}</small></span>`;
    button.addEventListener('click', () => selectItem(button, item));
    host.append(button);
    if (index === 0) selectItem(button, item);
  });
  if (!flowItems.length) detail.innerHTML = '<p>当前运行尚未产生执行轨迹。</p>';
}

function traceLinkage(toolName, status, task) {
  if (status === 'failed') {
    return '<div class="trace-linkage checkpoint"><span>Checkpoint</span><p>失败步骤已停止继续执行；可从最近成功检查点恢复，不必整条链路重跑。</p><button type="button" data-trace-target="review">查看恢复入口 →</button></div>';
  }
  if (['retrieve_documents', 'extract_facts', 'verify_citations'].includes(toolName)) {
    const evidenceCount = task.result?.evidence?.length || 0;
    return `<div class="trace-linkage evidence"><span>Evidence</span><p>此步骤与 ${escapeHtml(String(evidenceCount))} 条工作区证据及最终引用校验关联。</p><button type="button" data-trace-target="citations">核对证据 →</button></div>`;
  }
  if (['compose_document', 'generate_risk_register', 'generate_slide_outline'].includes(toolName)) {
    return '<div class="trace-linkage artifact-link"><span>Artifact</span><p>该步骤生成可交付内容；结论仍需经过引用门禁与人工审核。</p><button type="button" data-trace-target="result">查看产出 →</button></div>';
  }
  return '';
}

function renderDecisionPath(task, node) {
  const host = node.querySelector('.decision-grid');
  const reason = node.querySelector('.decision-reason');
  const run = task.runs?.[0] || {};
  const steps = run.steps || [];
  const completedSteps = new Set(steps.filter((step) => step.status === 'completed').map((step) => step.sequence)).size;
  const retryCount = steps.filter((step) => step.status === 'retrying').length;
  const evidenceCount = task.result?.evidence?.length || 0;
  const verification = task.result?.verification || {};
  const qualityKnown = verification.content_quality_passed !== undefined;
  const qualityPassed = verification.content_quality_passed !== false;
  const verificationLabel = !verification.passed
    ? '引用门禁未通过'
    : !qualityKnown
      ? '引用门禁通过'
      : qualityPassed
        ? '引用与内容检查通过'
        : '引用通过，内容需复核';
  const reviewLabels = {
    queued: '等待执行', running: '执行中', awaiting_review: '等待人工决策', approved: '已批准导出', rejected: '已驳回', failed: '可从检查点恢复',
  };
  const cards = [
    { index: '01', tone: 'plan', title: '计划', value: task.plan?.length ? `${task.plan.length} 步已校验` : '等待计划', meta: `Planner · ${task.result?.planner?.mode || run.planner_mode || '未执行'}`, target: 'trace' },
    { index: '02', tone: 'runtime', title: '执行', value: `${completedSteps}/${task.plan?.length || 0} 步完成`, meta: retryCount ? `${retryCount} 次重试已记录` : 'Trace 已持久化', target: 'trace' },
    { index: '03', tone: verification.passed && qualityPassed ? 'evidence' : 'danger', title: '证据', value: verificationLabel, meta: `${evidenceCount} 条证据 · ${verification.reference_count || 0} 处引用`, target: 'citations' },
    { index: '04', tone: task.status === 'approved' ? 'success' : task.status === 'failed' || task.status === 'rejected' ? 'danger' : 'review', title: '决策', value: reviewLabels[task.status] || task.status, meta: task.status === 'approved' ? '已开放 Markdown 导出' : '导出受审核状态控制', target: 'review' },
  ];
  host.replaceChildren(...cards.map((card) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `decision-card tone-${card.tone}`;
    button.dataset.decisionTarget = card.target;
    button.innerHTML = `<span>${card.index}</span><div><small>${escapeHtml(card.title)}</small><strong>${escapeHtml(card.value)}</strong><p>${escapeHtml(card.meta)}</p></div><b>→</b>`;
    button.addEventListener('click', () => activateResultView(card.target));
    return button;
  }));
  if (task.status === 'failed') {
    const failed = [...steps].reverse().find((step) => step.status === 'failed');
    reason.textContent = `${failed ? `第 ${failed.sequence} 步 ${toolLabels[failed.tool_name] || failed.tool_name} 失败；` : ''}已保留最近成功检查点，可进入人工审核页恢复。`;
  } else if (task.status === 'awaiting_review') {
    reason.textContent = verification.passed && qualityKnown && qualityPassed
      ? '工具执行、引用追溯与内容质量检查已完成；当前停在人工审核门禁，审核前不可导出。'
      : verification.passed && !qualityKnown
        ? '该历史任务已通过引用门禁，但未执行新版内容质量检查；请人工复核后再作审核决定。'
      : '工具执行已完成，但引用或内容质量检查仍需复核；请核对后再作审核决定。';
  } else if (task.status === 'approved') {
    reason.textContent = '执行、引用门禁与人工审核均已完成，当前结果允许导出。';
  } else if (task.status === 'rejected') {
    reason.textContent = '人工审核已驳回本次结果，当前结果不可导出。';
  } else {
    reason.textContent = '运行状态仍在变化；点击任一阶段可查看对应证据与决策依据。';
  }
}

function renderTask(task) {
  activeTaskId = task.id;
  setWorkspaceMode('active');
  const node = document.querySelector('#card-template').content.cloneNode(true);
  node.querySelector('.card-title').textContent = task.title;
  node.querySelector('.card-goal').textContent = `目标：${task.goal}`;
  const mode = task.result?.insights?.mode;
  const plannerMode = task.result?.planner?.mode || task.runs?.[0]?.planner_mode || '未执行';
  const execution = task.result?.execution || {};
  const memoryResult = task.result?.memory || {};
  const memoryRecalled = memoryResult.recalled ?? memoryResult.items_used ?? 0;
  const memoryApplied = memoryResult.applied ?? memoryResult.items_used ?? 0;
  const contextLayers = task.result?.context?.layers?.length || 0;
  const modeLabel = mode === 'model' ? '模型语义分析' : mode === 'rules_fallback' ? '本地规则降级' : '本地规则分析';
  node.querySelector('.task-mode').textContent = `实际执行：${modeLabel} · Planner：${plannerMode} · Context：${contextLayers} 层 · Session Memory：召回 ${memoryRecalled} 条 · 应用 ${memoryApplied} 条`;
  const modelCallSummary = node.querySelector('.model-call-summary');
  const modelCalls = Array.isArray(execution.model_calls) ? execution.model_calls : [];
  if (modelCalls.length) {
    const usage = execution.model_usage || {};
    const modelNames = [...new Set(modelCalls.map((call) => call.model).filter(Boolean))].join('、') || '未记录';
    const callStages = modelCalls.map((call) => `${call.stage || 'unknown'}:${call.status || 'unknown'}`).join(' · ');
    const costText = execution.estimated_cost === null || execution.estimated_cost === undefined
      ? '成本未配置费率'
      : `估算成本 ${execution.cost_currency || ''} ${Number(execution.estimated_cost).toFixed(6)}${execution.cost_rate_label ? `（${execution.cost_rate_label}）` : ''}`.trim();
    modelCallSummary.hidden = false;
    modelCallSummary.textContent = `真实模型调用：${callStages} · 模型 ${modelNames} · ${usage.total_tokens || 0} Tokens · 模型耗时 ${execution.model_latency_ms || 0} ms · ${costText}`;
  }
  const executionAlert = node.querySelector('.execution-alert');
  if (execution.degraded || mode === 'rules_fallback' || plannerMode === 'rules_fallback') {
    executionAlert.hidden = false;
    const fallbackReason = Array.isArray(execution.fallback_reasons) ? execution.fallback_reasons.filter(Boolean).join('；') : '';
    executionAlert.textContent = `本次已降级为本地规则。模型调用未完成，当前结果不应标记为模型输出。${fallbackReason ? `原因：${fallbackReason.slice(0, 240)}` : ''}`;
    setRuntimeChip(modelStatus, '模型本次调用降级', 'warning');
  } else if (mode === 'model') {
    setRuntimeChip(modelStatus, '模型本次调用成功', 'ok');
  }
  const appliedMemory = node.querySelector('.applied-memory');
  if (memoryRecalled > 0) {
    appliedMemory.hidden = false;
    appliedMemory.querySelector('.memory-summary').textContent = `已应用 Memory：${memoryApplied}/${memoryRecalled} 条`;
    const memoryApplication = appliedMemory.querySelector('.memory-application');
    const items = memoryResult.items || [];
    if (items.length) {
      memoryApplication.replaceChildren(...items.map((item) => {
        const detail = document.createElement('article');
        detail.innerHTML = `<strong>${escapeHtml(item.memory_key || '协作偏好')} · ${escapeHtml(item.focus || 'balanced')}</strong><p>${escapeHtml(item.content || '')}</p><small>影响范围：${escapeHtml((item.impacts || []).join('、') || '未应用到当前输出')}</small>`;
        return detail;
      }));
    } else {
      memoryApplication.innerHTML = '<article><strong>本次未应用召回内容</strong><p>召回记录未解析为受支持的结构偏好。</p><small>不会影响事实、Evidence 或引用。</small></article>';
    }
  }
  const metrics = task.result?.metrics || {};
  const metricHost = node.querySelector('.run-metrics');
  const metricItems = metrics.elapsed_ms === undefined
    ? [['--', '任务耗时'], ['--', '工具步骤'], ['--', '尝试次数'], ['--', '重试次数'], ['--', '工具成功率']]
    : [[`${metrics.elapsed_ms} ms`, '任务耗时'], [metrics.executed_steps, '工具步骤'], [metrics.attempts, '尝试次数'], [metrics.retry_count, '重试次数'], [`${Math.round((metrics.tool_success_rate || 0) * 100)}%`, '工具成功率']];
  metricHost.innerHTML = metricItems.map(([value, label]) => `<div class="metric"><strong>${escapeHtml(String(value))}</strong><small>${escapeHtml(label)}</small></div>`).join('');
  renderDecisionPath(task, node);
  renderContextManifest(task.result?.context, node);
  const status = node.querySelector('.status');
  status.textContent = statusLabels[task.status] || task.status;
  status.classList.add(task.status);

  const plan = node.querySelector('.plan');
  (task.plan || []).forEach((step) => {
    const item = document.createElement('li');
    item.innerHTML = `<strong>${escapeHtml(step.tool_name)}</strong>：${escapeHtml(step.purpose)}`;
    plan.append(item);
  });

  renderExecutionFlow(task, node);

  const artifacts = task.result?.artifacts || {};
  renderArtifacts(artifacts, node, false);
  const citationToggle = node.querySelector('.citation-toggle');
  citationToggle.addEventListener('change', () => renderArtifacts(artifacts, cardHost, citationToggle.checked));
  const downloadButton = node.querySelector('.download-output');
  const downloadStatus = node.querySelector('.download-status');
  if (task.status === 'approved') {
    downloadButton.disabled = false;
    downloadButton.textContent = '下载完整产出包 (.md)';
    downloadButton.addEventListener('click', () => downloadReport(task.id, downloadButton, downloadStatus));
  } else {
    downloadButton.title = '请先在“人工审核”页通过本次结果';
  }

  const verification = task.result?.verification || {};
  const gateHost = node.querySelector('.verification-gates');
  const verificationGates = [
    ['引用存在性', verification.citation_check?.passed ?? verification.citation_coverage],
    ['字段一致性', verification.field_consistency_check?.passed ?? verification.consistency_passed],
    ['语义支持性', verification.semantic_support_check?.passed ?? verification.content_quality_passed],
  ];
  gateHost.replaceChildren(...verificationGates.map(([label, passed]) => {
    const gate = document.createElement('article');
    gate.className = `verification-gate ${passed === undefined ? 'unknown' : passed ? 'passed' : 'failed'}`;
    gate.innerHTML = `<span>${passed === undefined ? '–' : passed ? '✓' : '!'}</span><div><strong>${escapeHtml(label)}</strong><small>${passed === undefined ? '历史任务未检查' : passed ? '通过' : '需要人工复核'}</small></div>`;
    return gate;
  }));
  const verificationResult = node.querySelector('.verification-result');
  const citationMessage = verification.passed
    ? `通过：发现 ${verification.reference_count || 0} 处引用，均可在当前工作区证据中追溯。`
    : `未通过：存在无法追溯的引用 ${verification.invalid_citations?.join('、') || '或未生成引用'}。`;
  const warnings = verification.warnings || [];
  const qualityMessage = verification.content_quality_passed === undefined
    ? '内容质量：该历史任务未执行新版规则检查。'
    : verification.content_quality_passed === false
      ? `内容质量未通过：${warnings.join('；')}`
      : '内容质量通过：负责人、日期、风险覆盖和汇报选材未发现规则异常。';
  verificationResult.textContent = `${citationMessage} ${qualityMessage}`;
  verificationResult.classList.toggle('verification-failed', !verification.passed);
  verificationResult.classList.toggle('verification-warning', verification.passed && verification.content_quality_passed === false);
  const evidenceList = node.querySelector('.evidence-list');
  (task.result?.evidence || []).forEach((evidence) => {
    const item = document.createElement('div');
    item.className = 'evidence-item';
    item.innerHTML = `<strong>[${escapeHtml(evidence.id)}]</strong> ${escapeHtml(evidence.excerpt)}<small>${escapeHtml(evidence.source_location)}</small>`;
    evidenceList.append(item);
  });
  const rawJson = node.querySelector('.raw-json');
  const copyRunJson = node.querySelector('.copy-run-json');
  const copyRunStatus = node.querySelector('.copy-run-status');
  rawJson.textContent = JSON.stringify(task.result || {}, null, 2);
  copyRunJson.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(rawJson.textContent);
      copyRunStatus.textContent = '已复制完整 JSON';
    } catch (_) {
      copyRunStatus.textContent = '复制失败，请手动选择文本';
    }
  });

  const review = node.querySelector('.review');
  const reviewNote = node.querySelector('.review-note');
  const exportMessage = node.querySelector('.export-message');
  const reviewMessage = node.querySelector('.review-message');
  if (task.status === 'approved') {
    review.querySelector('textarea').hidden = true;
    review.querySelector('.approve').hidden = true;
    review.querySelector('.reject').hidden = true;
    exportMessage.textContent = '审核已通过，请在“结果”页顶部下载完整产出包。';
  } else if (task.status === 'rejected') {
    review.innerHTML = `<h3>审核结果</h3><p>已驳回：${escapeHtml(task.reviewer_note || '未填写备注')}</p>`;
  } else if (task.status === 'awaiting_review') {
    node.querySelector('.approve').addEventListener('click', (event) => reviewTask(task.id, 'approve', reviewNote.value, event.currentTarget, reviewMessage));
    node.querySelector('.reject').addEventListener('click', (event) => reviewTask(task.id, 'reject', reviewNote.value, event.currentTarget, reviewMessage));
  } else if (task.status === 'failed') {
    review.querySelector('textarea').hidden = true;
    review.querySelector('.approve').hidden = true;
    review.querySelector('.reject').hidden = true;
    const retryButton = review.querySelector('.retry');
    retryButton.hidden = false;
    retryButton.addEventListener('click', () => retryTask(task.id, retryButton, reviewMessage));
  } else {
    review.innerHTML = '<h3>人工审核</h3><p>当前任务尚未进入审核阶段。</p>';
  }
  cardHost.replaceChildren(node);
  cardHost.hidden = false;
  emptyState.hidden = true;
  activateResultView('result');
}

async function retryTask(taskId, button, feedback) {
  button.disabled = true;
  button.textContent = '正在从检查点恢复…';
  feedback.textContent = '';
  try {
    const task = await (await request(`/api/docflow/tasks/${taskId}/retry`, { method: 'POST' })).json();
    renderTask(task);
    await loadTasks();
  } catch (error) {
    button.disabled = false;
    button.textContent = '从检查点恢复';
    feedback.textContent = `恢复失败：${error.message}`;
  }
}

function openDeleteDialog(task) {
  pendingDeleteTask = task;
  deleteDescription.textContent = `“${task.title}”（任务 #${task.id}）`;
  deleteFeedback.textContent = '';
  confirmDeleteButton.disabled = false;
  confirmDeleteButton.textContent = '确认删除';
  deleteDialog.hidden = false;
  cancelDeleteButton.focus();
}

function closeDeleteDialog() {
  if (confirmDeleteButton.disabled) return;
  deleteDialog.hidden = true;
  pendingDeleteTask = null;
  deleteFeedback.textContent = '';
}

async function confirmDeleteTask() {
  if (!pendingDeleteTask) return;
  const task = pendingDeleteTask;
  confirmDeleteButton.disabled = true;
  cancelDeleteButton.disabled = true;
  confirmDeleteButton.textContent = '正在删除…';
  deleteFeedback.textContent = '';
  try {
    await request(`/api/docflow/tasks/${task.id}`, { method: 'DELETE' });
    if (activeTaskId === task.id) resetWorkspace();
    deleteDialog.hidden = true;
    pendingDeleteTask = null;
    await loadTasks();
  } catch (error) {
    deleteFeedback.textContent = `删除失败：${error.message}`;
  } finally {
    confirmDeleteButton.disabled = false;
    cancelDeleteButton.disabled = false;
    confirmDeleteButton.textContent = '确认删除';
  }
}

async function loadTasks() {
  const tasks = await (await request('/api/docflow/tasks')).json();
  const sidebarTasks = tasks
    .filter((task) => !/^(automated demo check|\?+)$/i.test(task.title.trim()))
    .slice(0, 6);
  taskList.replaceChildren(...sidebarTasks.map((task) => {
    const item = document.createElement('article');
    const deleteDisabled = ['queued', 'running'].includes(task.status);
    item.className = `task ${task.id === activeTaskId ? 'active' : ''}`;
    item.innerHTML = `<div class="task-main"><h3>${escapeHtml(task.title)}</h3><p><span class="task-status">${escapeHtml(statusLabels[task.status] || task.status)}</span></p></div><button class="task-menu-trigger" type="button" aria-label="更多操作：${escapeHtml(task.title)}" title="${deleteDisabled ? '任务完成后可操作' : '更多操作'}" ${deleteDisabled ? 'disabled' : ''}>•••</button>`;
    if (!deleteDisabled) {
      item.querySelector('.task-menu-trigger').addEventListener('click', (event) => {
        event.stopPropagation();
        openTaskMenuFromButton(task, event.currentTarget);
      });
    }
    item.addEventListener('click', async (event) => {
      if (event.target.closest('.task-menu-trigger')) return;
      renderTask(await (await request(`/api/docflow/tasks/${task.id}`)).json());
      activateMainView('workspace-view');
      document.querySelector('#detail-panel').scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
    item.addEventListener('contextmenu', (event) => {
      if (deleteDisabled) return;
      event.preventDefault();
      openTaskContextMenu(task, event.clientX, event.clientY);
    });
    return item;
  }));
  historyList.replaceChildren(...tasks.map((task) => {
    const item = document.createElement('article');
    const deleteDisabled = ['queued', 'running'].includes(task.status);
    item.className = 'history-row';
    item.innerHTML = `<button class="history-item history-open" type="button"><span><strong>${escapeHtml(task.title)}</strong><small>任务 #${escapeHtml(String(task.id))}</small></span><span class="run-status ${escapeHtml(task.status)}">${escapeHtml(statusLabels[task.status] || task.status)}</span><b>查看详情 →</b></button><button class="history-menu-trigger" type="button" aria-label="更多操作：${escapeHtml(task.title)}" ${deleteDisabled ? 'disabled title="任务完成后可操作"' : 'title="更多操作"'}>•••</button>`;
    item.querySelector('.history-open').addEventListener('click', async () => {
      renderTask(await (await request(`/api/docflow/tasks/${task.id}`)).json());
      activateMainView('workspace-view');
      document.querySelector('#detail-panel').scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
    if (!deleteDisabled) item.querySelector('.history-menu-trigger').addEventListener('click', (event) => openTaskMenuFromButton(task, event.currentTarget));
    item.addEventListener('contextmenu', (event) => {
      if (deleteDisabled) return;
      event.preventDefault();
      openTaskContextMenu(task, event.clientX, event.clientY);
    });
    return item;
  }));
  loadEvaluationSummary().catch((error) => { document.querySelector('.evaluation-notice').textContent = `评测数据加载失败：${error.message}`; });
}

async function reviewTask(taskId, action, note, button, feedback) {
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = '正在保存审核结果…';
  feedback.textContent = '';
  try {
    const task = await (await request(`/api/docflow/tasks/${taskId}/review`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action, note }) })).json();
    renderTask(task);
    await loadTasks();
  } catch (error) {
    button.disabled = false;
    button.textContent = originalText;
    feedback.textContent = `审核未保存：${error.message}`;
  }
}

async function downloadReport(taskId, button, output) {
  button.disabled = true;
  output.textContent = '正在生成下载文件…';
  try {
    const response = await request(`/api/docflow/tasks/${taskId}/export`);
    const blob = await response.blob();
    const anchor = document.createElement('a');
    anchor.href = URL.createObjectURL(blob);
    anchor.download = `docflow-${taskId}.md`;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(anchor.href);
    output.textContent = '已开始下载 Markdown 文档。';
  } catch (error) {
    output.textContent = `下载失败：${error.message}`;
  } finally {
    button.disabled = false;
  }
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  message.textContent = '';
  const submit = document.querySelector('#submit');
  const title = document.querySelector('#title').value;
  const goal = document.querySelector('#goal').value;
  const text = document.querySelector('#text').value;
  const sessionId = getActiveSessionId();
  const runMode = document.querySelector('#run-mode').value;
  const memory = document.querySelector('#memory').value.trim();
  const contextConfig = {
    audience: document.querySelector('#audience').value,
    focus: document.querySelector('#context-focus').value,
    evidence_limit: Number(document.querySelector('#evidence-limit').value),
    memory_enabled: document.querySelector('#memory-enabled').checked,
    citation_policy: document.querySelector('#citation-policy').value,
  };
  const selectedFile = document.querySelector('#file').files[0];
  const idempotencyKey = window.crypto?.randomUUID?.() || `docflow-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  submit.disabled = true;
  setWorkspaceMode('active');
  cardHost.replaceChildren();
  cardHost.hidden = true;
  emptyState.hidden = false;
  submit.textContent = runMode === 'async' ? '正在提交后台任务…' : 'Planner 正在生成计划…';
  jobProgress.hidden = true;
  try {
    if (goal.trim().length < 5) throw new Error('请至少输入 5 个字的协作目标。');
    if (memory && contextConfig.memory_enabled) {
      await request('/api/docflow/memories', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, memory_key: '协作偏好', content: memory }),
      });
      await loadMemoriesForSession();
    }
    let response;
    if (selectedFile) {
      const payload = new FormData();
      payload.append('file', selectedFile);
      payload.append('title', title || selectedFile.name);
      payload.append('goal', goal);
      payload.append('session_id', sessionId);
      payload.append('audience', contextConfig.audience);
      payload.append('focus', contextConfig.focus);
      payload.append('evidence_limit', String(contextConfig.evidence_limit));
      payload.append('memory_enabled', String(contextConfig.memory_enabled));
      payload.append('citation_policy', contextConfig.citation_policy);
      response = await request(runMode === 'async' ? '/api/docflow/jobs/file' : '/api/docflow/tasks/file', {
        method: 'POST',
        headers: runMode === 'async' ? { 'Idempotency-Key': idempotencyKey } : {},
        body: payload,
      });
    } else {
      if (text.trim().length < 20) throw new Error('请粘贴至少 20 个字的工作区材料，或上传一个文件。');
      response = await request(runMode === 'async' ? '/api/docflow/jobs' : '/api/docflow/tasks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(runMode === 'async' ? { 'Idempotency-Key': idempotencyKey } : {}) },
        body: JSON.stringify({ title: title || '未命名协作任务', goal, text, session_id: sessionId, context_config: contextConfig }),
      });
    }
    const submitted = await response.json();
    if (runMode === 'async') updateJobProgress(submitted);
    const task = runMode === 'async' ? await pollJob(submitted.task_id) : submitted;
    renderTask(task);
    await loadTasks();
    document.querySelector('#detail-panel').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (error) {
    message.textContent = error.message;
  } finally {
    submit.disabled = false;
    submit.textContent = '运行 Agent';
  }
});

exampleTasks.forEach((button) => button.addEventListener('click', () => loadDemoExample(button.dataset.example)));
openTemplateManagerButton.addEventListener('click', openTemplateManager);
closeTemplateManagerButton.addEventListener('click', () => {
  resetWorkspace();
  activateMainView('workspace-view');
});
templateForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const submit = templateForm.querySelector('button[type="submit"]');
  submit.disabled = true;
  templateFormMessage.textContent = '正在保存…';
  try {
    const template = await (await request('/api/docflow/templates', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: document.querySelector('#template-name').value,
        title: document.querySelector('#template-title').value,
        goal: document.querySelector('#template-goal').value,
        source_text: document.querySelector('#template-source').value,
        audience: document.querySelector('#template-audience').value,
        focus: document.querySelector('#template-focus').value,
      }),
    })).json();
    templateForm.reset();
    templateFormMessage.textContent = `“${template.name}”已保存，可从侧边栏或右侧列表载入。`;
    await loadCustomTemplates();
  } catch (error) {
    templateFormMessage.textContent = `保存失败：${error.message}`;
  } finally {
    submit.disabled = false;
  }
});
menuDeleteTaskButton.addEventListener('click', () => {
  if (!contextMenuTask) return;
  const task = contextMenuTask;
  closeTaskContextMenu();
  openDeleteDialog(task);
});
cancelDeleteButton.addEventListener('click', closeDeleteDialog);
confirmDeleteButton.addEventListener('click', confirmDeleteTask);
deleteDialog.addEventListener('click', (event) => {
  if (event.target === deleteDialog) closeDeleteDialog();
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && !deleteDialog.hidden) closeDeleteDialog();
  else if (event.key === 'Escape' && !taskContextMenu.hidden) closeTaskContextMenu();
});
document.addEventListener('pointerdown', (event) => {
  if (!taskContextMenu.hidden && !event.target.closest('#task-context-menu') && !event.target.closest('.task-menu-trigger, .history-menu-trigger')) closeTaskContextMenu();
});
window.addEventListener('resize', closeTaskContextMenu);
window.addEventListener('scroll', closeTaskContextMenu, true);

refreshEvaluationButton.addEventListener('click', async () => {
  const originalText = refreshEvaluationButton.textContent;
  refreshEvaluationButton.disabled = true;
  refreshEvaluationButton.textContent = '正在刷新…';
  try {
    await loadEvaluationSummary();
  } finally {
    refreshEvaluationButton.disabled = false;
    refreshEvaluationButton.textContent = originalText;
  }
});
refreshMemoriesButton.addEventListener('click', loadMemoriesForSession);
async function refreshSessionScopedView() {
  getActiveSessionId();
  activeTaskId = null;
  cardHost.replaceChildren();
  cardHost.hidden = true;
  emptyState.hidden = false;
  await Promise.all([loadMemoriesForSession(), loadTasks()]);
}

const sessionInput = document.querySelector('#session-id');
sessionInput.addEventListener('input', () => {
  activeTaskId = null;
  cardHost.replaceChildren();
  cardHost.hidden = true;
  emptyState.hidden = false;
  taskList.replaceChildren();
  memoryList.innerHTML = '<p class="memory-empty">正在切换访客会话…</p>';
  clearTimeout(sessionRefreshTimer);
  const candidate = sessionInput.value.trim();
  if (VISITOR_SESSION_PATTERN.test(candidate) && candidate !== 'default') {
    sessionRefreshTimer = setTimeout(() => refreshSessionScopedView(), 250);
  }
});
sessionInput.addEventListener('change', () => {
  clearTimeout(sessionRefreshTimer);
  refreshSessionScopedView();
});
document.querySelector('.advanced-settings').addEventListener('toggle', (event) => {
  if (event.currentTarget.open) loadMemoriesForSession();
});

loadTasks().catch((error) => { taskList.textContent = error.message; });
loadCustomTemplates().catch((error) => { templateLibraryList.textContent = `模板加载失败：${error.message}`; });
loadRuntimeStatus();
