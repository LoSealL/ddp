import './style.css';

// ── Types ────────────────────────────────────
type JobStatus = 'pending' | 'running' | 'done' | 'failed' | 'timeout' | 'cancelled';
type Lang = 'en' | 'zh';

interface Job {
  id: string;
  user_id: number;
  name: string;
  filename: string;
  entry_command: string;
  scheduled_at: string;
  timeout_minutes: number;
  status: JobStatus;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  s3_prefix: string | null;
  output_count: number;
  error: string | null;
}

interface Output {
  key: string;
  size: number;
  s3_uri: string;
}

// ── Element helper ───────────────────────────
function $<T extends HTMLElement = HTMLElement>(id: string): T {
  const el = document.getElementById(id);
  if (!el) throw new Error(`Element #${id} not found`);
  return el as T;
}

// ── i18n ─────────────────────────────────────
const I18N: Record<Lang, Record<string, string>> = {
  en: {
    tagline: "Delayed Dispatch Platform",
    login: "Login", register: "Register",
    username: "Username", password: "Password",
    loginFailed: "Login failed", registerFailed: "Registration failed",
    signingIn: "Signing in...", creating: "Creating...",
    logout: "Logout", signedInAs: "Signed in as",
    submitJob: "Submit Job", jobs: "Jobs",
    jobName: "Job Name", projectZip: "Project (zip)",
    dropzoneHint: "Click or drag a .zip here",
    entryCommand: "Entry Command", entryCommandHint: "Shell command to run inside the project root.",
    scheduledStart: "Scheduled Start (local time)", scheduledStartHint: "Platform fires within ~30s of this time.",
    maxRuntime: "Max Runtime (minutes)", maxRuntimeHint: "Hard kill at this point. Outputs collected up to then.",
    scheduleJob: "Schedule Job", scheduling: "Scheduling...",
    error: "Error", unknown: "Unknown",
    noJobs: "No jobs yet. Submit one from the left.",
    noJobsMatch: "No jobs match your search.",
    searchPlaceholder: "Search by name or file...",
    allStatuses: "All Statuses",
    loading: "Loading...", jobDetail: "Job Detail",
    status: "Status", originalFile: "Original File", entryCmd: "Entry Command",
    maxRuntimeLabel: "Max Runtime", scheduledUtc: "Scheduled (UTC)",
    started: "Started", finished: "Finished", outputs: "Outputs", s3Prefix: "S3 Prefix",
    cancelJob: "Cancel Job", cancelConfirm: "Cancel this job?",
    logs: "Logs", noLogs: "No logs yet.", outputArtifacts: "Output Artifacts",
    failed: "Failed", files: "file(s)", min: "min",
    st_pending: "pending", st_running: "running", st_done: "done",
    st_failed: "failed", st_timeout: "timeout", st_cancelled: "cancelled",
    langLabel: "中文",
  },
  zh: {
    tagline: "延迟调度平台",
    login: "登录", register: "注册",
    username: "用户名", password: "密码",
    loginFailed: "登录失败", registerFailed: "注册失败",
    signingIn: "登录中...", creating: "创建中...",
    logout: "登出", signedInAs: "当前用户",
    submitJob: "提交作业", jobs: "作业列表",
    jobName: "作业名称", projectZip: "项目文件 (zip)",
    dropzoneHint: "点击或拖拽 .zip 文件到此处",
    entryCommand: "入口命令", entryCommandHint: "在项目根目录执行的 Shell 命令。",
    scheduledStart: "计划启动时间 (本地)", scheduledStartHint: "平台在此时间后约 30 秒内触发。",
    maxRuntime: "最大运行时长 (分钟)", maxRuntimeHint: "超时强制终止，已生成的产物仍会收集。",
    scheduleJob: "调度作业", scheduling: "调度中...",
    error: "错误", unknown: "未知",
    noJobs: "暂无作业。从左侧提交一个。",
    noJobsMatch: "没有匹配的作业。",
    searchPlaceholder: "按名称或文件搜索...",
    allStatuses: "全部状态",
    loading: "加载中...", jobDetail: "作业详情",
    status: "状态", originalFile: "原始文件", entryCmd: "入口命令",
    maxRuntimeLabel: "最大运行时长", scheduledUtc: "计划时间 (UTC)",
    started: "开始时间", finished: "结束时间", outputs: "产物数量", s3Prefix: "S3 路径",
    cancelJob: "取消作业", cancelConfirm: "确定取消此作业？",
    logs: "日志", noLogs: "暂无日志。", outputArtifacts: "产物文件",
    failed: "操作失败", files: "个文件", min: "分钟",
    st_pending: "等待中", st_running: "运行中", st_done: "已完成",
    st_failed: "失败", st_timeout: "超时", st_cancelled: "已取消",
    langLabel: "EN",
  },
};

let LANG: Lang = (localStorage.getItem('ddp-lang') as Lang) || (navigator.language.startsWith('zh') ? 'zh' : 'en');

function t(key: string): string { return I18N[LANG][key] || key; }

function applyI18n(): void {
  document.documentElement.lang = LANG === 'zh' ? 'zh-CN' : 'en';
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = (el as HTMLElement).dataset.i18n!;
    if (I18N[LANG][key]) el.textContent = I18N[LANG][key];
  });
  document.querySelectorAll('[data-i18n-ph]').forEach(el => {
    const key = (el as HTMLElement).dataset.i18nPh!;
    if (I18N[LANG][key]) (el as HTMLInputElement).placeholder = I18N[LANG][key];
  });
  const authToggle = document.getElementById('lang-toggle-auth');
  const appToggle = document.getElementById('lang-toggle-app');
  if (authToggle) authToggle.textContent = I18N[LANG].langLabel;
  if (appToggle) appToggle.textContent = I18N[LANG].langLabel;
}

function toggleLang(): void {
  LANG = LANG === 'en' ? 'zh' : 'en';
  localStorage.setItem('ddp-lang', LANG);
  applyI18n();
  if ($('app-view').style.display !== 'none') renderJobs();
}

// ── State ────────────────────────────────────
const API = '/api/jobs';
let refreshTimer: ReturnType<typeof setInterval> | null = null;
let allJobs: Job[] = [];

// ── Auth ─────────────────────────────────────
function switchAuthTab(tab: 'login' | 'register'): void {
  $('tab-login').classList.toggle('active', tab === 'login');
  $('tab-register').classList.toggle('active', tab === 'register');
  $('login-form').style.display = tab === 'login' ? '' : 'none';
  $('register-form').style.display = tab === 'register' ? '' : 'none';
  hideAuthError();
}

function showAuthError(msg: string): void {
  const el = $('auth-error');
  el.textContent = msg;
  el.classList.add('show');
}

function hideAuthError(): void {
  $('auth-error').classList.remove('show');
}

async function doLogin(e: SubmitEvent): Promise<void> {
  e.preventDefault();
  hideAuthError();
  const btn = $<HTMLButtonElement>('login-btn');
  btn.disabled = true; btn.textContent = t('signingIn');
  try {
    const fd = new FormData(e.target as HTMLFormElement);
    const resp = await fetch('/api/auth/login', { method: 'POST', body: fd });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({} as Record<string, string>));
      showAuthError(err.detail || t('loginFailed'));
      return;
    }
    await checkAuth();
  } finally {
    btn.disabled = false; btn.textContent = t('login');
  }
}

async function doRegister(e: SubmitEvent): Promise<void> {
  e.preventDefault();
  hideAuthError();
  const btn = $<HTMLButtonElement>('register-btn');
  btn.disabled = true; btn.textContent = t('creating');
  try {
    const fd = new FormData(e.target as HTMLFormElement);
    const resp = await fetch('/api/auth/register', { method: 'POST', body: fd });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({} as Record<string, string>));
      showAuthError(err.detail || t('registerFailed'));
      return;
    }
    await checkAuth();
  } finally {
    btn.disabled = false; btn.textContent = t('register');
  }
}

async function doLogout(): Promise<void> {
  await fetch('/api/auth/logout', { method: 'POST' });
  showAuthView();
}

async function checkAuth(): Promise<boolean> {
  try {
    const resp = await fetch('/api/auth/me');
    if (resp.ok) {
      const user: { id: number; username: string } = await resp.json();
      showAppView(user.username);
      return true;
    }
  } catch { /* ignore */ }
  showAuthView();
  return false;
}

function showAppView(username: string): void {
  $('auth-view').style.display = 'none';
  $('app-view').style.display = '';
  $('nav-username').textContent = username;
  setDefaultTime();
  refreshJobs();
  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = setInterval(refreshJobs, 5000);
}

function showAuthView(): void {
  $('auth-view').style.display = '';
  $('app-view').style.display = 'none';
  if (refreshTimer) { clearInterval(refreshTimer); refreshTimer = null; }
}

// ── File upload ──────────────────────────────
function setupUpload(): void {
  const dropzone = $('dropzone');
  const fileInput = $<HTMLInputElement>('file-input');
  const fileDisplay = $('file-display');

  dropzone.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', () => {
    if (fileInput.files?.length) fileDisplay.textContent = fileInput.files[0].name;
  });
  dropzone.addEventListener('dragover', e => { e.preventDefault(); dropzone.classList.add('dragover'); });
  dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
  dropzone.addEventListener('drop', e => {
    e.preventDefault();
    dropzone.classList.remove('dragover');
    const files = e.dataTransfer?.files;
    if (files && files.length && files[0].name.endsWith('.zip')) {
      fileInput.files = files;
      fileDisplay.textContent = files[0].name;
    }
  });
}

// ── Default scheduled_at ─────────────────────
function setDefaultTime(): void {
  const now = new Date(Date.now() + 2 * 60 * 1000);
  const pad = (n: number) => String(n).padStart(2, '0');
  const val = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}T${pad(now.getHours())}:${pad(now.getMinutes())}`;
  $<HTMLInputElement>('scheduled_at').value = val;
}

// ── Submit ───────────────────────────────────
async function submitJob(e: SubmitEvent): Promise<void> {
  e.preventDefault();
  const btn = $<HTMLButtonElement>('submit-btn');
  const form = e.target as HTMLFormElement;
  const fd = new FormData(form);
  btn.disabled = true; btn.textContent = t('scheduling');
  try {
    const resp = await fetch(API, { method: 'POST', body: fd });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      alert(t('error') + ': ' + (err.detail || t('unknown')));
      return;
    }
    form.reset();
    setDefaultTime();
    $('file-display').textContent = '';
    await refreshJobs();
  } finally {
    btn.disabled = false; btn.textContent = t('scheduleJob');
  }
}

// ── Job list ─────────────────────────────────
function statusLabel(s: string): string { return t('st_' + s) || s; }

async function refreshJobs(): Promise<void> {
  const resp = await fetch(API);
  if (resp.status === 401) { showAuthView(); return; }
  allJobs = await resp.json();
  renderJobs();
}

function filterJobs(): void { renderJobs(); }

function renderJobs(): void {
  const q = ($<HTMLInputElement>('job-search').value || '').trim().toLowerCase();
  const statusFilter = $<HTMLSelectElement>('job-status-filter').value || '';
  let jobs = allJobs;
  if (statusFilter) jobs = jobs.filter(j => j.status === statusFilter);
  if (q) jobs = jobs.filter(j =>
    (j.name || '').toLowerCase().includes(q) ||
    (j.filename || '').toLowerCase().includes(q)
  );

  const list = $('job-list');
  $('job-count').textContent = String(jobs.length);

  if (!jobs.length) {
    list.innerHTML = `<div class="empty"><div class="icon">📭</div><div>${q ? t('noJobsMatch') : t('noJobs')}</div></div>`;
    return;
  }

  list.innerHTML = jobs.map(j => {
    const scheduled = j.scheduled_at ? new Date(j.scheduled_at).toLocaleString() : '—';
    const started = j.started_at ? new Date(j.started_at).toLocaleString() : '—';
    return `
      <div class="job-card" data-job-id="${j.id}">
        <div class="info">
          <div class="name">${escapeHtml(j.name)}</div>
          <div class="meta">
            <span>📁 ${escapeHtml(j.filename || '')}</span>
            <span>⏰ ${scheduled}</span>
            ${j.started_at ? `<span>▶ ${started}</span>` : ''}
            ${j.output_count ? `<span>📦 ${j.output_count} ${t('files')}</span>` : ''}
          </div>
        </div>
        <span class="status status-${j.status}">${statusLabel(j.status)}</span>
      </div>`;
  }).join('');
}

// ── Modal detail ─────────────────────────────
async function openModal(jobId: string): Promise<void> {
  const modal = $('modal');
  const body = $('modal-body');
  const title = $('modal-title');
  modal.classList.add('open');
  body.innerHTML = '<div style="text-align:center;padding:40px;"><div class="spinner"></div></div>';
  title.textContent = t('loading');

  const [jobResp, logResp, outResp] = await Promise.all([
    fetch(`${API}/${jobId}`),
    fetch(`${API}/${jobId}/logs`),
    fetch(`${API}/${jobId}/outputs`),
  ]);
  const job: Job = await jobResp.json();
  const logs: string = (await logResp.json()).logs;
  const outputs: Output[] = (await outResp.json()).outputs;

  title.textContent = job.name;

  let html = `
    <div class="modal-section">
      <div class="kv-grid">
        <div class="kv"><span class="k">${t('status')}</span><span class="v"><span class="status status-${job.status}">${statusLabel(job.status)}</span></span></div>
        <div class="kv"><span class="k">${t('originalFile')}</span><span class="v">${escapeHtml(job.filename || '—')}</span></div>
        <div class="kv"><span class="k">${t('entryCmd')}</span><span class="v"><code>${escapeHtml(job.entry_command || '—')}</code></span></div>
        <div class="kv"><span class="k">${t('maxRuntimeLabel')}</span><span class="v">${job.timeout_minutes || '—'} ${t('min')}</span></div>
        <div class="kv"><span class="k">${t('scheduledUtc')}</span><span class="v">${job.scheduled_at ? new Date(job.scheduled_at).toLocaleString() : '—'}</span></div>
        <div class="kv"><span class="k">${t('started')}</span><span class="v">${job.started_at ? new Date(job.started_at).toLocaleString() : '—'}</span></div>
        <div class="kv"><span class="k">${t('finished')}</span><span class="v">${job.finished_at ? new Date(job.finished_at).toLocaleString() : '—'}</span></div>
        <div class="kv"><span class="k">${t('outputs')}</span><span class="v">${job.output_count || 0} ${t('files')}</span></div>
        <div class="kv"><span class="k">${t('s3Prefix')}</span><span class="v"><code>${escapeHtml(job.s3_prefix || '—')}</code></span></div>
      </div>
    </div>`;

  if (job.status === 'pending') {
    html += `<div class="modal-section"><button class="btn-cancel" data-job-id="${job.id}">${t('cancelJob')}</button></div>`;
  }
  if (job.error) {
    html += `<div class="modal-section"><h4>${t('error')}</h4><div class="error-box">${escapeHtml(job.error)}</div></div>`;
  }

  html += `<div class="modal-section"><h4>${t('logs')}</h4><div class="log-box">${logs ? escapeHtml(logs) : `<span style="color:var(--text-dim)">${t('noLogs')}</span>`}</div></div>`;

  if (outputs.length) {
    html += `<div class="modal-section"><h4>${t('outputArtifacts')}</h4><ul class="output-list">`;
    outputs.forEach(o => {
      const downloadUrl = `/s3/${o.key}`;
      const fname = o.key.split('/').pop() || o.key;
      html += `<li><a href="${downloadUrl}" download>${escapeHtml(fname)}</a><span class="size">${formatSize(o.size)}</span></li>`;
    });
    html += `</ul></div>`;
  }

  body.innerHTML = html;
}

function closeModal(): void {
  $('modal').classList.remove('open');
}

async function cancelJob(jobId: string): Promise<void> {
  if (!confirm(t('cancelConfirm'))) return;
  const resp = await fetch(`${API}/${jobId}`, { method: 'DELETE' });
  if (resp.ok) { closeModal(); await refreshJobs(); }
  else { const err = await resp.json().catch(() => ({} as Record<string, string>)); alert(err.detail || t('failed')); }
}

// ── Utils ────────────────────────────────────
function escapeHtml(s: string | null | undefined): string {
  if (!s) return '';
  const div = document.createElement('div');
  div.textContent = String(s);
  return div.innerHTML;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / 1024 / 1024).toFixed(1) + ' MB';
}

// ── Init ─────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  applyI18n();
  setupUpload();

  $('tab-login').addEventListener('click', () => switchAuthTab('login'));
  $('tab-register').addEventListener('click', () => switchAuthTab('register'));
  $('login-form').addEventListener('submit', doLogin);
  $('register-form').addEventListener('submit', doRegister);
  $('lang-toggle-auth').addEventListener('click', toggleLang);
  $('lang-toggle-app').addEventListener('click', toggleLang);
  $('btn-logout').addEventListener('click', doLogout);
  $('job-form').addEventListener('submit', submitJob);
  $('job-search').addEventListener('input', filterJobs);
  $('job-status-filter').addEventListener('change', filterJobs);
  $('modal-close').addEventListener('click', closeModal);
  $('modal').addEventListener('click', e => { if (e.target === $('modal')) closeModal(); });

  $('job-list').addEventListener('click', e => {
    const card = (e.target as HTMLElement).closest('.job-card') as HTMLElement | null;
    if (card?.dataset.jobId) openModal(card.dataset.jobId);
  });

  $('modal-body').addEventListener('click', e => {
    const btn = (e.target as HTMLElement).closest('.btn-cancel') as HTMLElement | null;
    if (btn?.dataset.jobId) cancelJob(btn.dataset.jobId);
  });

  checkAuth();
});
