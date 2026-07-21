const form = document.querySelector('#research-form');
const message = document.querySelector('#form-message');
const cardHost = document.querySelector('#card');
const emptyState = document.querySelector('#empty-state');
const taskList = document.querySelector('#task-list');
let activeTaskId = null;

const labels = { company: '公司材料', industry: '产业材料', macro: '宏观材料' };
const statusLabels = { pending_review: '待人工审核', approved: '审核通过', rejected: '已驳回' };

async function request(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || '请求失败，请稍后重试。');
  }
  return response;
}

function appendList(container, values, tag = 'li') {
  container.replaceChildren(...values.map((value) => {
    const item = document.createElement(tag);
    item.textContent = value;
    return item;
  }));
}

function renderTask(task) {
  activeTaskId = task.id;
  const node = document.querySelector('#card-template').content.cloneNode(true);
  const card = task.card;
  node.querySelector('.card-type').textContent = labels[card.material_type] || card.material_type;
  node.querySelector('.card-title').textContent = task.title;
  const status = node.querySelector('.status');
  status.textContent = statusLabels[task.status] || task.status;
  status.classList.add(task.status);
  node.querySelector('.summary').textContent = card.summary;
  const mode = node.querySelector('.mode-note');
  mode.textContent = card.analysis_mode === 'model' ? '模型模式：已使用配置的大模型进行结构化抽取。' : '演示模式：未配置模型密钥，使用本地规则生成可审核卡片。';
  mode.classList.toggle('demo', card.analysis_mode !== 'model');
  const facts = node.querySelector('.facts');
  card.facts.forEach((fact) => {
    const item = document.createElement('div'); item.className = 'fact';
    item.innerHTML = `<p><strong>事实：</strong>${escapeHtml(fact.claim)}</p><p class="evidence"><strong>原文证据：</strong>${escapeHtml(fact.evidence)}</p><small>${escapeHtml(fact.source_location)}</small>`;
    facts.append(item);
  });
  const industryAnalysis = card.industry_analysis || {};
  node.querySelector('.industry-judgment').textContent = industryAnalysis.industry_judgment || '未生成产业推演。';
  appendList(node.querySelector('.causal-chain'), industryAnalysis.causal_chain || [], 'li');
  appendList(node.querySelector('.direction-analysis'), industryAnalysis.direction_analysis || [], 'li');
  appendList(node.querySelector('.risk-reversals'), industryAnalysis.risk_reversals || [], 'li');
  const dimensions = node.querySelector('.dimensions');
  card.impact_dimensions.forEach((dimension) => { const chip = document.createElement('span'); chip.textContent = dimension; dimensions.append(chip); });
  appendList(node.querySelector('.chain'), card.impact_chain, 'li');
  appendList(node.querySelector('.verification'), card.verification_items, 'li');
  node.querySelector('.risk').textContent = `风险提示：${card.risk_notice}`;
  const review = node.querySelector('.review');
  const exportButton = node.querySelector('.export');
  const exportMessage = node.querySelector('.export-message');
  if (task.status === 'approved') {
    review.querySelector('textarea').hidden = true;
    review.querySelector('.approve').hidden = true;
    review.querySelector('.reject').hidden = true;
    exportButton.hidden = false;
    exportButton.addEventListener('click', () => downloadReport(task.id, exportButton, exportMessage));
  }
  else if (task.status === 'rejected') { review.innerHTML = `<h3>审核结果</h3><p>已驳回：${escapeHtml(task.reviewer_note || '未填写备注')}</p>`; }
  else {
    node.querySelector('.approve').addEventListener('click', (event) => reviewTask(task.id, 'approve', node.querySelector('.review-note').value, event.currentTarget));
    node.querySelector('.reject').addEventListener('click', (event) => reviewTask(task.id, 'reject', node.querySelector('.review-note').value, event.currentTarget));
  }
  cardHost.replaceChildren(node); cardHost.hidden = false; emptyState.hidden = true;
}

function escapeHtml(value) { const element = document.createElement('div'); element.textContent = value; return element.innerHTML; }

async function loadTasks() {
  const tasks = await (await request('/api/tasks')).json();
  taskList.replaceChildren(...tasks.map((task) => {
    const item = document.createElement('article'); item.className = `task ${task.id === activeTaskId ? 'active' : ''}`;
    item.innerHTML = `<h3>${escapeHtml(task.title)}</h3><p>${labels[task.material_type]} · ${statusLabels[task.status]}</p>`;
    item.addEventListener('click', () => renderTask(task)); return item;
  }));
}

async function reviewTask(taskId, action, note, button) {
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = '正在保存审核结果…';
  try {
    const task = await (await request(`/api/tasks/${taskId}/review`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action, note }) })).json();
    renderTask(task); await loadTasks();
  } catch (error) {
    button.disabled = false;
    button.textContent = originalText;
    window.alert(error.message);
  }
}

async function downloadReport(taskId, button, message) {
  button.disabled = true; message.textContent = '正在生成下载文件…';
  try {
    const anchor = document.createElement('a');
    anchor.href = `/api/tasks/${taskId}/export`;
    anchor.download = `research-${taskId}.md`;
    document.body.append(anchor); anchor.click(); anchor.remove();
    message.textContent = '已开始下载 Markdown 简报。';
  } catch (error) { message.textContent = `导出失败：${error.message}`; }
  finally { button.disabled = false; }
}

form.addEventListener('submit', async (event) => {
  event.preventDefault(); message.textContent = '';
  const submit = document.querySelector('#submit'); submit.disabled = true; submit.textContent = '正在生成…';
  try {
    const selectedFile = document.querySelector('#file').files[0];
    const text = document.querySelector('#text').value;
    if (!selectedFile && text.trim().length < 10) throw new Error('请粘贴至少 10 个字符的材料，或上传一个文件。');
    let response;
    if (selectedFile) {
      const payload = new FormData(); payload.append('file', selectedFile); payload.append('title', document.querySelector('#title').value || selectedFile.name); payload.append('material_type', document.querySelector('#material-type').value);
      response = await request('/api/research/file', { method: 'POST', body: payload });
    } else {
      response = await request('/api/research', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: document.querySelector('#title').value || '未命名研究', material_type: document.querySelector('#material-type').value, text }) });
    }
    const task = await response.json();
    renderTask(task); await loadTasks();
  } catch (error) { message.textContent = error.message; }
  finally { submit.disabled = false; submit.textContent = '生成证据研究卡片'; }
});

loadTasks().catch((error) => { taskList.textContent = error.message; });
