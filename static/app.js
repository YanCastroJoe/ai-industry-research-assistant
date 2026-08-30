const form = document.querySelector('#docflow-form');
const message = document.querySelector('#form-message');
const cardHost = document.querySelector('#card');
const emptyState = document.querySelector('#empty-state');
const taskList = document.querySelector('#task-list');
const sampleButton = document.querySelector('#sample-button');
const refreshEvaluationButton = document.querySelector('#refresh-evaluation');
const jobProgress = document.querySelector('#job-progress');
let activeTaskId = null;

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
  const response = await fetch(url, options);
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || '请求失败，请稍后重试。');
  }
  return response;
}

function escapeHtml(value) {
  const element = document.createElement('div');
  element.textContent = value || '';
  return element.innerHTML;
}

function renderRiskRegister(markdown, host) {
  const lines = markdown.split('\n').map((line) => line.trim()).filter(Boolean);
  const title = lines.find((line) => line.startsWith('## '));
  const tableLines = lines.filter((line) => line.startsWith('|'));
  if (tableLines.length < 3) {
    host.textContent = markdown;
    return;
  }
  const cells = (line) => line.split('|').slice(1, -1).map((cell) => escapeHtml(cell.trim()));
  const headers = cells(tableLines[0]);
  const rows = tableLines.slice(2).map((line) => cells(line));
  host.innerHTML = `${title ? `<p class="artifact-heading">${escapeHtml(title.slice(3))}</p>` : ''}<div class="risk-table-scroll"><table class="risk-table"><thead><tr>${headers.map((cell) => `<th>${cell}</th>`).join('')}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((cell) => `<td>${cell}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
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
  const cards = [
    [metrics.evaluated_count ?? 0, '有效任务样本'],
    [formatRate(metrics.citation_pass_rate), '引用通过率'],
    [formatRate(metrics.tool_success_rate), '工具成功率'],
    [formatRate(metrics.approval_rate), '人工通过率'],
    [metrics.latency_p95_ms === null ? '--' : `${metrics.latency_p95_ms} ms`, 'P95 运行耗时'],
    [formatRate(metrics.retry_task_rate), '发生重试的任务'],
  ];
  document.querySelector('.evaluation-metrics').innerHTML = cards.map(([value, label]) => `<article><strong>${escapeHtml(String(value))}</strong><span>${escapeHtml(label)}</span></article>`).join('');

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
    row.innerHTML = `<td><button type="button" class="evaluation-task-link">${escapeHtml(task.title)}</button><small>#${escapeHtml(String(task.task_id))} · ${escapeHtml(task.planner_mode || '未执行')}</small></td><td><span class="run-status ${escapeHtml(task.status)}">${escapeHtml(statusLabels[task.status] || task.status)}</span></td><td>${task.elapsed_ms === null || task.elapsed_ms === undefined ? '--' : `${escapeHtml(String(task.elapsed_ms))} ms`}</td><td>${task.citation_passed === null || task.citation_passed === undefined ? '--' : task.citation_passed ? '通过' : '未通过'}</td><td class="${task.issues?.length ? 'diagnostic-warning' : 'diagnostic-ok'}">${escapeHtml(issues)}</td>`;
    row.querySelector('.evaluation-task-link').addEventListener('click', async () => {
      renderTask(await (await request(`/api/docflow/tasks/${task.task_id}`)).json());
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
    flowItems.push({
      key: 'context', label: 'Context', subtitle: `${task.result.context.layers?.length || 0} 层上下文`, status: 'completed',
      detail: `<div class="trace-detail-head"><div><small>CONTEXT PREFLIGHT</small><h4>上下文分层与约束</h4></div><span class="trace-state completed">已完成</span></div><p>受众：${escapeHtml(task.result.context.audience)} · 焦点：${escapeHtml(task.result.context.focus)} · Evidence Budget：${escapeHtml(String(task.result.context.evidence_budget))}</p>`,
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
      detail: `<div class="trace-detail-head"><div><small>STEP ${escapeHtml(String(sequence))} · ${escapeHtml(latest.phase || 'runtime')}</small><h4>${escapeHtml(toolLabels[latest.tool_name] || latest.tool_name)}</h4><code>${escapeHtml(latest.tool_name)}</code></div><span class="trace-state ${escapeHtml(status)}">${escapeHtml(traceStatusLabels[status] || status)}</span></div><div class="trace-io"><div><strong>Input</strong><pre>${escapeHtml(formatPayload(latest.input))}</pre></div><div><strong>Output</strong><pre>${escapeHtml(formatPayload((completed || latest).output))}</pre></div></div>${latest.error ? `<p class="trace-error">${escapeHtml(latest.error)}</p>` : ''}<p class="trace-meta">${attempts.length} 次尝试 · 累计 ${elapsed} ms${attempts.length > 1 ? ' · 已记录重试轨迹' : ''}</p>`,
    });
  });

  const selectItem = (button, item) => {
    host.querySelectorAll('.flow-node').forEach((candidate) => candidate.classList.toggle('active', candidate === button));
    detail.innerHTML = item.detail;
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

function renderTask(task) {
  activeTaskId = task.id;
  const node = document.querySelector('#card-template').content.cloneNode(true);
  node.querySelector('.card-title').textContent = task.title;
  node.querySelector('.card-goal').textContent = `目标：${task.goal}`;
  const mode = task.result?.insights?.mode;
  const plannerMode = task.result?.planner?.mode || task.runs?.[0]?.planner_mode || '未执行';
  const memoryUsed = task.result?.memory?.items_used || 0;
  const contextLayers = task.result?.context?.layers?.length || 0;
  node.querySelector('.task-mode').textContent = `${mode === 'model' ? '模型语义分析' : '本地规则分析'} · Planner：${plannerMode} · Context：${contextLayers} 层 · Session Memory：${memoryUsed} 条`;
  const metrics = task.result?.metrics || {};
  const metricHost = node.querySelector('.run-metrics');
  const metricItems = metrics.elapsed_ms === undefined
    ? [['--', '任务耗时'], ['--', '工具步骤'], ['--', '尝试次数'], ['--', '重试次数'], ['--', '工具成功率']]
    : [[`${metrics.elapsed_ms} ms`, '任务耗时'], [metrics.executed_steps, '工具步骤'], [metrics.attempts, '尝试次数'], [metrics.retry_count, '重试次数'], [`${Math.round((metrics.tool_success_rate || 0) * 100)}%`, '工具成功率']];
  metricHost.innerHTML = metricItems.map(([value, label]) => `<div class="metric"><strong>${escapeHtml(String(value))}</strong><small>${escapeHtml(label)}</small></div>`).join('');
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
  node.querySelector('.artifact.document').textContent = artifacts.weekly_report_markdown || '尚未生成项目周报。';
  if (artifacts.risk_register_markdown) {
    node.querySelector('.table-wrap').hidden = false;
    renderRiskRegister(artifacts.risk_register_markdown, node.querySelector('.artifact.table'));
  }
  if (artifacts.slide_outline_markdown) {
    node.querySelector('.slides-wrap').hidden = false;
    node.querySelector('.artifact.slides').textContent = artifacts.slide_outline_markdown;
  }

  const verification = task.result?.verification || {};
  const verificationResult = node.querySelector('.verification-result');
  const citationMessage = verification.passed
    ? `通过：发现 ${verification.reference_count || 0} 处引用，均可在当前工作区证据中追溯。`
    : `未通过：存在无法追溯的引用 ${verification.invalid_citations?.join('、') || '或未生成引用'}。`;
  const warnings = verification.warnings || [];
  verificationResult.textContent = warnings.length
    ? `${citationMessage} 需人工复核：${warnings.join('；')}`
    : citationMessage;
  verificationResult.classList.toggle('verification-failed', !verification.passed);
  verificationResult.classList.toggle('verification-warning', verification.passed && warnings.length > 0);
  const evidenceList = node.querySelector('.evidence-list');
  (task.result?.evidence || []).forEach((evidence) => {
    const item = document.createElement('div');
    item.className = 'evidence-item';
    item.innerHTML = `<strong>[${escapeHtml(evidence.id)}]</strong> ${escapeHtml(evidence.excerpt)}<small>${escapeHtml(evidence.source_location)}</small>`;
    evidenceList.append(item);
  });

  const review = node.querySelector('.review');
  const reviewNote = node.querySelector('.review-note');
  const exportButton = node.querySelector('.export');
  const exportMessage = node.querySelector('.export-message');
  const reviewMessage = node.querySelector('.review-message');
  if (task.status === 'approved') {
    review.querySelector('textarea').hidden = true;
    review.querySelector('.approve').hidden = true;
    review.querySelector('.reject').hidden = true;
    exportButton.hidden = false;
    exportButton.addEventListener('click', () => downloadReport(task.id, exportButton, exportMessage));
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

async function loadTasks() {
  const tasks = await (await request('/api/docflow/tasks')).json();
  taskList.replaceChildren(...tasks.map((task) => {
    const item = document.createElement('article');
    item.className = `task ${task.id === activeTaskId ? 'active' : ''}`;
    item.innerHTML = `<h3>${escapeHtml(task.title)}</h3><p><span class="task-status">${escapeHtml(statusLabels[task.status] || task.status)}</span>${escapeHtml(task.goal)}</p>`;
    item.addEventListener('click', async () => renderTask(await (await request(`/api/docflow/tasks/${task.id}`)).json()));
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

function downloadReport(taskId, button, output) {
  button.disabled = true;
  output.textContent = '正在生成下载文件…';
  const anchor = document.createElement('a');
  anchor.href = `/api/docflow/tasks/${taskId}/export`;
  anchor.download = `docflow-${taskId}.md`;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  output.textContent = '已开始下载 Markdown 文档。';
  button.disabled = false;
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  message.textContent = '';
  const submit = document.querySelector('#submit');
  const title = document.querySelector('#title').value;
  const goal = document.querySelector('#goal').value;
  const text = document.querySelector('#text').value;
  const sessionId = document.querySelector('#session-id').value.trim() || 'default';
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
  } catch (error) {
    message.textContent = error.message;
  } finally {
    submit.disabled = false;
    submit.textContent = '运行 Agent';
  }
});

sampleButton.addEventListener('click', () => {
  document.querySelector('#title').value = '智能客服项目第 3 周周报';
  document.querySelector('#session-id').value = 'customer-service-demo';
  document.querySelector('#run-mode').value = 'async';
  document.querySelector('#memory').value = '面向管理层，优先展示风险、负责人和截止时间';
  document.querySelector('#audience').value = '管理层';
  document.querySelector('#context-focus').value = 'risk';
  document.querySelector('#evidence-limit').value = '9';
  document.querySelector('#citation-policy').value = 'strict';
  document.querySelector('#memory-enabled').checked = true;
  document.querySelector('#goal').value = '基于材料生成本周项目周报、风险清单和三页汇报大纲；每条结论标注来源。';
  document.querySelector('#text').value = `项目：智能客服知识库升级
本周进展：完成售后 FAQ 清洗与检索链路联调，24 条核心问法通过验收。
风险：退款政策文档仍有两个版本，负责人李明需在周五前确认最终口径。
行动：王芳负责补充退货运费边界案例，下周二完成回归测试。
会议结论：所有面向客户的回答必须附带当前知识库来源。`;
  document.querySelector('#goal').focus();
});

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

loadTasks().catch((error) => { taskList.textContent = error.message; });
