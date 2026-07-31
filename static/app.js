const form = document.querySelector('#docflow-form');
const message = document.querySelector('#form-message');
const cardHost = document.querySelector('#card');
const emptyState = document.querySelector('#empty-state');
const taskList = document.querySelector('#task-list');
let activeTaskId = null;

const statusLabels = {
  queued: '排队中', awaiting_review: '待人工审核', approved: '审核通过', rejected: '已驳回', failed: '运行失败',
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

function renderTask(task) {
  activeTaskId = task.id;
  const node = document.querySelector('#card-template').content.cloneNode(true);
  node.querySelector('.card-title').textContent = task.title;
  node.querySelector('.card-goal').textContent = `目标：${task.goal}`;
  const mode = task.result?.insights?.mode;
  node.querySelector('.task-mode').textContent = mode === 'model'
    ? '语义分析：已使用受证据约束的模型生成交付内容。'
    : '语义分析：当前使用本地规则生成；请配置模型密钥以获得更强的任务理解能力。';
  const status = node.querySelector('.status');
  status.textContent = statusLabels[task.status] || task.status;
  status.classList.add(task.status);

  const plan = node.querySelector('.plan');
  (task.plan || []).forEach((step) => {
    const item = document.createElement('li');
    item.innerHTML = `<strong>${escapeHtml(step.tool_name)}</strong>：${escapeHtml(step.purpose)}`;
    plan.append(item);
  });

  const steps = task.runs?.[0]?.steps || [];
  const traceList = node.querySelector('.trace-list');
  steps.forEach((step) => {
    const item = document.createElement('article');
    item.className = `trace ${step.status}`;
    const output = step.output?.preview ? (typeof step.output.preview === 'string' ? step.output.preview : JSON.stringify(step.output.preview)) : JSON.stringify(step.output || {});
    item.innerHTML = `<div><strong>${escapeHtml(step.sequence)}. ${escapeHtml(step.tool_name)}</strong><span>${escapeHtml(step.status)} · ${escapeHtml(String(step.elapsed_ms))} ms</span></div><p>${escapeHtml(output)}</p>${step.error ? `<p class="trace-error">${escapeHtml(step.error)}</p>` : ''}`;
    traceList.append(item);
  });

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
  } else {
    review.innerHTML = '<h3>人工审核</h3><p>当前任务尚未进入审核阶段。</p>';
  }
  cardHost.replaceChildren(node);
  cardHost.hidden = false;
  emptyState.hidden = true;
}

async function loadTasks() {
  const tasks = await (await request('/api/docflow/tasks')).json();
  taskList.replaceChildren(...tasks.map((task) => {
    const item = document.createElement('article');
    item.className = `task ${task.id === activeTaskId ? 'active' : ''}`;
    item.innerHTML = `<h3>${escapeHtml(task.title)}</h3><p>${escapeHtml(statusLabels[task.status] || task.status)} · ${escapeHtml(task.goal)}</p>`;
    item.addEventListener('click', async () => renderTask(await (await request(`/api/docflow/tasks/${task.id}`)).json()));
    return item;
  }));
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
  const selectedFile = document.querySelector('#file').files[0];
  submit.disabled = true;
  submit.textContent = 'Planner 正在生成计划…';
  try {
    if (goal.trim().length < 5) throw new Error('请至少输入 5 个字的协作目标。');
    let response;
    if (selectedFile) {
      const payload = new FormData();
      payload.append('file', selectedFile);
      payload.append('title', title || selectedFile.name);
      payload.append('goal', goal);
      response = await request('/api/docflow/tasks/file', { method: 'POST', body: payload });
    } else {
      if (text.trim().length < 20) throw new Error('请粘贴至少 20 个字的工作区材料，或上传一个文件。');
      response = await request('/api/docflow/tasks', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: title || '未命名协作任务', goal, text }) });
    }
    renderTask(await response.json());
    await loadTasks();
  } catch (error) {
    message.textContent = error.message;
  } finally {
    submit.disabled = false;
    submit.textContent = '运行 Agent';
  }
});

loadTasks().catch((error) => { taskList.textContent = error.message; });
