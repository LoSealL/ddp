import './style.css';

// ── Types ────────────────────────────────────
type JobStatus = 'initializing' | 'pending' | 'running' | 'done' | 'failed' | 'timeout' | 'cancelled';
type Lang = 'en' | 'zh';

interface Job {
  id: string;
  user_id: number;
  name: string;
  image: string;
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
  gpus: number;
  gpu_mem_mb: number | null;
  output_path: string;
  ssh_port: number | null;
  ssh_password: string | null;
}

interface Output {
  key: string;
  size: number;
  s3_uri: string;
  download_url: string;
}

interface GpuStatus {
  uuid: string;
  node: string;
  index: number;
  type: string;
  mem_total: number;
  mem_used: number;
  cores_total: number;
  cores_used: number;
  shared: number;
  enabled?: boolean;
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
    entryCommand: "Entry Command", entryCommandHint: "Runs in /workspace of the GPU pod at the scheduled time.",
    image: "Image", imageHint: "Base environment shared by the debug pod and the GPU run.",
    outputPath: "Output Dir", outputPathHint: "Absolute path, or relative to /workspace. Harvested to S3 after the run.",
    sshAccess: "SSH Access (debug pod)", sshCmd: "Command", sshPassword: "Password", sshHint: "Your /workspace persists and is shared across all your jobs. Files in output/ are harvested to S3 after each run.",
    scheduledStart: "Scheduled Start (local time)", scheduledStartHint: "Platform fires within ~30s of this time.",
    maxRuntime: "Max Runtime (minutes)", maxRuntimeHint: "Hard kill at this point. Outputs collected up to then.",
    gpus: "GPUs", gpusHint: "vGPU slices via HAMi. 0 = CPU only.",
    gpuMem: "GPU Memory (MB, optional)", gpuMemHint: "Leave empty for a full-memory vGPU slice.",
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
    editJob: "Edit", saved: "Saved.",
    deleteJob: "Delete", deleteConfirm: "Delete this job and all its resources?",
    logs: "Logs", noLogs: "No logs yet.", outputArtifacts: "Output Artifacts",
    downloadLogs: "Download Logs", downloadCode: "Download Code",
    failed: "Failed", files: "file(s)", min: "min",
    st_initializing: "initializing",
    st_pending: "pending", st_running: "running", st_done: "done",
    st_failed: "failed", st_timeout: "timeout", st_cancelled: "cancelled",
    langLabel: "中文",
    queued: "Job queued — it will run in the next allowed time window.",
    admin: "Admin", adminUsers: "Users", adminParams: "Parameters",
    adminLogs: "Logs", adminMonitor: "Monitoring",
    gpuQuota: "GPU Quota", storageQuota: "Storage (GB)",
    save: "Save", delete: "Delete",
    created: "Created", isAdmin: "Admin",
    timeWindow: "Allowed Running Window",
    timeWindowStart: "Start Time", timeWindowEnd: "End Time",
    timeWindowRepeat: "Repeat", repeatDaily: "Daily", repeatWeekdays: "Weekdays", repeatWeekly: "Weekly",
    gpuDefaultQuota: "Default GPU Quota",
    storageDefaultQuota: "Default Storage (GB)", gpuDevices: "GPU Devices",
    gpuUuid: "UUID", gpuEnabled: "Enabled", gpuType: "Type",
    gpuMemTotal: "Memory Total (MB)",
    gpuMemUsed: "Memory Used (MB)", gpuCoresTotal: "Cores Total", gpuCoresUsed: "Cores Used",
    paramsSaved: "Parameters saved.", paramsError: "Failed to save parameters.",
    level: "Level", category: "Category", timestamp: "Time", message: "Message",
    allLevels: "All Levels", allCategories: "All Categories",
    prev: "Prev", next: "Next", page: "Page",
    totalJobs: "Total Jobs", gpuStatus: "GPU Status", s3Storage: "S3 Storage",
    bucket: "Bucket", objects: "Objects", totalSize: "Total Size",
    memoryUsed: "Memory", coresUsed: "Cores",
    backToJobs: "Back to Jobs",
    endpoint: "Endpoint", noLogsAdmin: "No logs", noGpus: "No GPU devices configured",
    errorLoading: "Error loading data",
    gpuPool: "GPU Pool", free: "free", sharing: "sharing", gpuFree: "available", gpuDisabled: "disabled",
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
    entryCommand: "入口命令", entryCommandHint: "调度时间到时在 GPU Pod 的 /workspace 中执行。",
    image: "镜像", imageHint: "调试 Pod 与 GPU 运行共用同一基础环境。",
    outputPath: "产物目录", outputPathHint: "绝对路径，或相对 /workspace。运行结束后收割到 S3。",
    sshAccess: "SSH 访问（调试 Pod）", sshCmd: "命令", sshPassword: "密码", sshHint: "/workspace 在你名下所有作业间持久共享。output/ 里的文件会在每次运行后收割到 S3。",
    scheduledStart: "计划启动时间 (本地)", scheduledStartHint: "平台在此时间后约 30 秒内触发。",
    maxRuntime: "最大运行时长 (分钟)", maxRuntimeHint: "超时强制终止，已生成的产物仍会收集。",
    gpus: "GPU 数量", gpusHint: "HAMi vGPU 切分。0 = 仅用 CPU。",
    gpuMem: "GPU 显存 (MB，可选)", gpuMemHint: "留空表示整显存的 vGPU 切片。",
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
    editJob: "修改", saved: "已保存。",
    deleteJob: "删除", deleteConfirm: "删除此作业及所有相关资源？",
    logs: "日志", noLogs: "暂无日志。", outputArtifacts: "产物文件",
    downloadLogs: "下载日志", downloadCode: "下载代码",
    failed: "操作失败", files: "个文件", min: "分钟",
    st_initializing: "初始化中",
    st_pending: "等待中", st_running: "运行中", st_done: "已完成",
    st_failed: "失败", st_timeout: "超时", st_cancelled: "已取消",
    langLabel: "EN",
    queued: "作业已排队 — 将在下一个允许的时间窗口内运行。",
    admin: "管理", adminUsers: "用户", adminParams: "参数",
    adminLogs: "日志", adminMonitor: "监控",
    gpuQuota: "GPU 配额", storageQuota: "存储 (GB)",
    save: "保存", delete: "删除",
    created: "创建时间", isAdmin: "管理员",
    timeWindow: "允许运行时间段",
    timeWindowStart: "开始时间", timeWindowEnd: "结束时间",
    timeWindowRepeat: "重复", repeatDaily: "每天", repeatWeekdays: "工作日", repeatWeekly: "每周",
    gpuDefaultQuota: "默认 GPU 配额",
    storageDefaultQuota: "默认存储 (GB)", gpuDevices: "GPU 设备",
    gpuUuid: "UUID", gpuEnabled: "启用", gpuType: "型号",
    gpuMemTotal: "显存总量 (MB)",
    gpuMemUsed: "已用显存 (MB)", gpuCoresTotal: "总算力", gpuCoresUsed: "已用算力",
    paramsSaved: "参数已保存。", paramsError: "保存参数失败。",
    level: "级别", category: "类别", timestamp: "时间", message: "消息",
    allLevels: "所有级别", allCategories: "所有类别",
    prev: "上一页", next: "下一页", page: "页码",
    totalJobs: "作业总数", gpuStatus: "GPU 状态", s3Storage: "S3 存储",
    bucket: "存储桶", objects: "对象数", totalSize: "总大小",
    memoryUsed: "显存", coresUsed: "算力",
    backToJobs: "返回作业",
    endpoint: "端点", noLogsAdmin: "暂无日志", noGpus: "未配置 GPU 设备",
    errorLoading: "加载失败",
    gpuPool: "GPU 资源池", free: "空闲", sharing: "共享容器", gpuFree: "可用", gpuDisabled: "已禁用",
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
  if ($('app-view').style.display !== 'none') { renderJobs(); refreshGpus(); }
}

// ── State ────────────────────────────────────
const API = '/api/jobs';
let refreshTimer: ReturnType<typeof setInterval> | null = null;
let allJobs: Job[] = [];
let isAdmin = false;
let gpuQuota = 0;
let execMode = 'mock';
let imageChoices: string[] = [];
let windowStart = '00:00';
let windowEnd = '23:59';
let gpuDefault = 0;
let gpuTimer: ReturnType<typeof setInterval> | null = null;
let adminViewActive = false;
let adminTab: 'users' | 'params' | 'logs' | 'monitor' = 'users';
let logsPage = 0;
const LOGS_PER_PAGE = 50;
let logsLevel = '';
let logsCategory = '';

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
      const user: { id: number; username: string; is_admin: number; mode?: string; gpu_quota?: number; images?: string[];
                    time_window_start?: string; time_window_end?: string; gpu_default_quota?: number } = await resp.json();
      isAdmin = !!user.is_admin;
      execMode = user.mode || 'mock';
      gpuQuota = user.gpu_quota ?? 0;
      if (user.images?.length) imageChoices = user.images;
      windowStart = user.time_window_start || windowStart;
      windowEnd = user.time_window_end || windowEnd;
      gpuDefault = user.gpu_default_quota ?? 0;
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
  // Reset admin state — always land on the jobs page after login
  adminViewActive = false;
  const layout = document.querySelector('.layout') as HTMLElement;
  if (layout) layout.style.display = '';
  $('admin-view').style.display = 'none';
  const adminBtn = $('btn-admin');
  if (adminBtn) {
    adminBtn.style.display = isAdmin ? '' : 'none';
    adminBtn.textContent = t('admin');
  }
  const gpusInput = $<HTMLInputElement>('gpus');
  gpusInput.max = String(gpuQuota);
  $('image-select').innerHTML = imageChoices.map(i => `<option value="${i}">${i}</option>`).join('');
  setDefaultTime();
  refreshJobs();
  refreshGpus();
  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = setInterval(refreshJobs, 5000);
  if (gpuTimer) clearInterval(gpuTimer);
  gpuTimer = setInterval(refreshGpus, 60000);
}

function showAuthView(): void {
  $('auth-view').style.display = '';
  $('app-view').style.display = 'none';
  if (refreshTimer) { clearInterval(refreshTimer); refreshTimer = null; }
  if (gpuTimer) { clearInterval(gpuTimer); gpuTimer = null; }
}

// ── Admin panel ──────────────────────────────
function toggleAdminView(): void {
  adminViewActive = !adminViewActive;
  const layout = document.querySelector('.layout') as HTMLElement;
  const adminView = $('admin-view');
  const adminBtn = $('btn-admin');
  if (adminViewActive) {
    if (layout) layout.style.display = 'none';
    adminView.style.display = '';
    adminBtn.textContent = t('backToJobs');
    switchAdminTab(adminTab);
  } else {
    if (layout) layout.style.display = '';
    adminView.style.display = 'none';
    adminBtn.textContent = t('admin');
  }
}

function switchAdminTab(tab: 'users' | 'params' | 'logs' | 'monitor'): void {
  adminTab = tab;
  document.querySelectorAll('.admin-tab').forEach(el => el.classList.remove('active'));
  const tabBtn = $(`admin-tab-${tab}`);
  if (tabBtn) tabBtn.classList.add('active');
  if (tab === 'users') renderAdminUsers();
  if (tab === 'params') renderAdminParams();
  if (tab === 'logs') renderAdminLogs();
  if (tab === 'monitor') renderAdminMonitor();
}

async function renderAdminUsers(): Promise<void> {
  const content = $('admin-content');
  content.innerHTML = '<div class="empty"><div class="spinner"></div></div>';
  const resp = await fetch('/api/admin/users');
  if (!resp.ok) { content.innerHTML = `<div class="empty">${t('errorLoading')}</div>`; return; }
  const users: any[] = await resp.json();
  content.innerHTML = `
    <table class="users-table">
      <thead><tr>
        <th>ID</th><th>${t('username')}</th><th>${t('isAdmin')}</th>
        <th>${t('gpuQuota')}</th><th>${t('storageQuota')}</th>
        <th>${t('created')}</th><th></th>
      </tr></thead>
      <tbody>
        ${users.map(u => `
          <tr data-uid="${u.id}">
            <td>${u.id}</td>
            <td>${escapeHtml(u.username)}</td>
            <td><input type="checkbox" class="u-admin" ${u.is_admin ? 'checked' : ''} /></td>
            <td><input type="number" class="u-gpu" value="${u.gpu_quota_override ?? ''}" placeholder="default" min="0" /></td>
            <td><input type="number" class="u-storage" value="${u.storage_quota_override_gb ?? ''}" placeholder="default" min="0" step="0.5" /></td>
            <td>${u.created_at ? new Date(u.created_at).toLocaleDateString() : '—'}</td>
            <td>
              <button class="btn-save" data-save-uid="${u.id}">${t('save')}</button>
              <button class="btn-delete-user" data-del-uid="${u.id}">${t('delete')}</button>
            </td>
          </tr>`).join('')}
      </tbody>
    </table>`;
}

function gpuRowHtml(g: GpuStatus & { enabled?: boolean }): string {
  const enabled = g.enabled !== false;
  const GiB = 1024 ** 3;
  return `
    <tr class="gpu-device-row" data-uuid="${escapeHtml(g.uuid)}">
      <td><input type="checkbox" class="g-enabled" ${enabled ? 'checked' : ''} /></td>
      <td>${escapeHtml(g.node)} #${g.index}</td>
      <td>${escapeHtml(g.type)}</td>
      <td>${(g.mem_total / GiB).toFixed(0)} GB</td>
      <td>${g.cores_total}</td>
    </tr>`;
}

async function renderAdminParams(): Promise<void> {
  const content = $('admin-content');
  content.innerHTML = '<div class="empty"><div class="spinner"></div></div>';
  const [resp, gpuResp] = await Promise.all([fetch('/api/admin/params'), fetch('/api/gpus')]);
  if (!resp.ok) { content.innerHTML = `<div class="empty">${t('errorLoading')}</div>`; return; }
  const p: any = await resp.json();
  const gpus: GpuStatus[] = gpuResp.ok ? ((await gpuResp.json()).gpus || []) : [];
  content.innerHTML = `
    <form class="params-form" id="params-form">
      <h2 class="admin-section-title">${t('timeWindow')}</h2>
      <div class="field-row">
        <div class="field">
          <label>${t('timeWindowStart')}</label>
          <input type="time" name="time_window_start" value="${p.time_window_start}" />
        </div>
        <div class="field">
          <label>${t('timeWindowEnd')}</label>
          <input type="time" name="time_window_end" value="${p.time_window_end}" />
        </div>
      </div>
      <div class="field">
        <label>${t('timeWindowRepeat')}</label>
        <select name="time_window_repeat">
          <option value="daily" ${p.time_window_repeat === 'daily' ? 'selected' : ''}>${t('repeatDaily')}</option>
          <option value="weekdays" ${p.time_window_repeat === 'weekdays' ? 'selected' : ''}>${t('repeatWeekdays')}</option>
          <option value="weekly" ${p.time_window_repeat === 'weekly' ? 'selected' : ''}>${t('repeatWeekly')}</option>
        </select>
      </div>
      <div class="field-row">
        <div class="field">
          <label>${t('gpuDefaultQuota')}</label>
          <input type="number" name="gpu_default_quota" value="${p.gpu_default_quota}" min="0" />
        </div>
        <div class="field">
          <label>${t('storageDefaultQuota')}</label>
          <input type="number" name="storage_default_quota_gb" value="${p.storage_default_quota_gb}" min="0" step="0.5" />
        </div>
      </div>
      <div class="field">
        <label>${t('gpuDevices')}</label>
        <table class="users-table" id="gpu-devices-table">
          <thead><tr>
            <th>${t('gpuEnabled')}</th><th>Node</th>
            <th>${t('gpuType')}</th><th>${t('gpuMemTotal')}</th><th>${t('gpuCoresTotal')}</th>
          </tr></thead>
          <tbody id="gpu-devices-body">
            ${gpus.map(g => gpuRowHtml(g)).join('') || `<tr><td colspan="5" style="text-align:center;color:var(--text-dim)">${t('noGpus')}</td></tr>`}
          </tbody>
        </table>
      </div>
      <button type="submit" class="btn-submit">${t('save')}</button>
      <div id="params-status" style="margin-top:8px;"></div>
    </form>`;
}

async function renderAdminLogs(): Promise<void> {
  const content = $('admin-content');
  const params = new URLSearchParams();
  if (logsLevel) params.set('level', logsLevel);
  if (logsCategory) params.set('category', logsCategory);
  params.set('limit', String(LOGS_PER_PAGE));
  params.set('offset', String(logsPage * LOGS_PER_PAGE));
  const resp = await fetch(`/api/admin/logs?${params}`);
  if (!resp.ok) { content.innerHTML = `<div class="empty">${t('errorLoading')}</div>`; return; }
  const data: { logs: any[]; total: number } = await resp.json();
  const totalPages = Math.max(1, Math.ceil(data.total / LOGS_PER_PAGE));
  content.innerHTML = `
    <div class="logs-filters">
      <select id="logs-level-filter">
        <option value="">${t('allLevels')}</option>
        <option value="INFO" ${logsLevel === 'INFO' ? 'selected' : ''}>INFO</option>
        <option value="WARNING" ${logsLevel === 'WARNING' ? 'selected' : ''}>WARNING</option>
        <option value="ERROR" ${logsLevel === 'ERROR' ? 'selected' : ''}>ERROR</option>
        <option value="DEBUG" ${logsLevel === 'DEBUG' ? 'selected' : ''}>DEBUG</option>
      </select>
      <select id="logs-category-filter">
        <option value="">${t('allCategories')}</option>
        <option value="auth" ${logsCategory === 'auth' ? 'selected' : ''}>auth</option>
        <option value="job" ${logsCategory === 'job' ? 'selected' : ''}>job</option>
        <option value="admin" ${logsCategory === 'admin' ? 'selected' : ''}>admin</option>
        <option value="system" ${logsCategory === 'system' ? 'selected' : ''}>system</option>
      </select>
    </div>
    <table class="logs-table">
      <thead><tr>
        <th>${t('timestamp')}</th><th>${t('level')}</th><th>${t('category')}</th><th>${t('message')}</th>
      </tr></thead>
      <tbody>
        ${data.logs.length ? data.logs.map(l => `
          <tr>
            <td>${new Date(l.timestamp).toLocaleString()}</td>
            <td class="log-level-${l.level}">${l.level}</td>
            <td>${l.category}</td>
            <td>${escapeHtml(l.message)}</td>
          </tr>`).join('') : `<tr><td colspan="4" style="text-align:center;color:var(--text-dim)">${t('noLogsAdmin')}</td></tr>`}
      </tbody>
    </table>
    <div class="logs-pagination">
      <button id="logs-prev" ${logsPage === 0 ? 'disabled' : ''}>${t('prev')}</button>
      <span>${t('page')} ${logsPage + 1} / ${totalPages}</span>
      <button id="logs-next" ${data.logs.length < LOGS_PER_PAGE ? 'disabled' : ''}>${t('next')}</button>
    </div>`;

  const levelFilter = document.getElementById('logs-level-filter') as HTMLSelectElement | null;
  if (levelFilter) levelFilter.addEventListener('change', e => {
    logsLevel = (e.target as HTMLSelectElement).value; logsPage = 0; renderAdminLogs();
  });
  const catFilter = document.getElementById('logs-category-filter') as HTMLSelectElement | null;
  if (catFilter) catFilter.addEventListener('change', e => {
    logsCategory = (e.target as HTMLSelectElement).value; logsPage = 0; renderAdminLogs();
  });
  const prevBtn = document.getElementById('logs-prev') as HTMLButtonElement | null;
  if (prevBtn && !prevBtn.disabled) prevBtn.addEventListener('click', () => {
    if (logsPage > 0) { logsPage--; renderAdminLogs(); }
  });
  const nextBtn = document.getElementById('logs-next') as HTMLButtonElement | null;
  if (nextBtn && !nextBtn.disabled) nextBtn.addEventListener('click', () => {
    logsPage++; renderAdminLogs();
  });
}

async function renderAdminMonitor(): Promise<void> {
  const content = $('admin-content');
  content.innerHTML = '<div class="empty"><div class="spinner"></div></div>';
  const resp = await fetch('/api/admin/monitoring');
  if (!resp.ok) { content.innerHTML = `<div class="empty">${t('errorLoading')}</div>`; return; }
  const data: {
    jobs: Record<string, number>;
    gpus: any[];
    s3: { bucket: string; endpoint: string; object_count: number; total_size_bytes: number };
  } = await resp.json();

  const totalJobs = Object.values(data.jobs).reduce((a, b) => a + b, 0);
  const statusColors: Record<string, string> = {
    done: 'var(--green)', failed: 'var(--red)', running: 'var(--blue)',
    timeout: 'var(--orange)', pending: 'var(--orange)', cancelled: 'var(--gray)',
  };
  content.innerHTML = `
    <h2 class="admin-section-title">${t('totalJobs')} (${totalJobs})</h2>
    <div class="monitor-grid">
      ${Object.entries(data.jobs).map(([status, count]) => `
        <div class="monitor-card">
          <div class="label">${statusLabel(status)}</div>
          <div class="value" style="color: ${statusColors[status] || 'var(--text)'}">${count}</div>
        </div>`).join('')}
    </div>
    <h2 class="admin-section-title">${t('gpuStatus')}</h2>
    ${data.gpus.length ? data.gpus.map((g: any) => {
      const GiB = 1024 ** 3;
      const memPct = g.mem_total > 0 ? (g.mem_used / g.mem_total * 100) : 0;
      const corePct = g.cores_total > 0 ? (g.cores_used / g.cores_total * 100) : 0;
      return `
        <div class="gpu-card">
          <div class="gpu-name">${escapeHtml(g.node)} #${g.index} <span style="color:var(--text-dim);font-weight:400">${escapeHtml(g.type)}</span></div>
          <div>${t('memoryUsed')}: ${(g.mem_used / GiB).toFixed(1)}/${(g.mem_total / GiB).toFixed(0)} GB · ${g.shared} ${t('sharing')}</div>
          <div class="gpu-bar"><div class="gpu-bar-fill" style="width:${memPct}%"></div></div>
          <div style="margin-top:8px">${t('coresUsed')}: ${g.cores_used}/${g.cores_total}</div>
          <div class="gpu-bar"><div class="gpu-bar-fill" style="width:${corePct}%"></div></div>
        </div>`;
    }).join('') : `<div class="empty">${t('noGpus')}</div>`}
    <h2 class="admin-section-title">${t('s3Storage')}</h2>
    <div class="s3-card">
      <div class="kv-row"><span class="k">${t('bucket')}</span><span class="v">${escapeHtml(data.s3.bucket)}</span></div>
      <div class="kv-row"><span class="k">${t('endpoint')}</span><span class="v">${escapeHtml(data.s3.endpoint)}</span></div>
      <div class="kv-row"><span class="k">${t('objects')}</span><span class="v">${data.s3.object_count}</span></div>
      <div class="kv-row"><span class="k">${t('totalSize')}</span><span class="v">${formatSize(data.s3.total_size_bytes)}</span></div>
    </div>`;
}

// ── Form defaults from admin config ──────────
function _winMinutes(): { s: number; e: number } {
  const [sh, sm] = windowStart.split(':').map(Number);
  const [eh, em] = windowEnd.split(':').map(Number);
  return { s: sh * 60 + sm, e: eh * 60 + em };
}

function setDefaultTime(): void {
  const { s, e } = _winMinutes();
  const now = new Date();
  const t = now.getHours() * 60 + now.getMinutes();
  const inWindow = s <= e ? (t >= s && t < e) : (t >= s || t < e);
  let d: Date;
  if (inWindow) {
    d = new Date(Date.now() + 2 * 60 * 1000);
  } else {
    d = new Date(now);
    d.setHours(Math.floor(s / 60), s % 60, 0, 0);
    if (d.getTime() <= now.getTime()) d.setDate(d.getDate() + 1);
  }
  const pad = (n: number) => String(n).padStart(2, '0');
  const val = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  $<HTMLInputElement>('scheduled_at').value = val;
  // max runtime defaults to the full window length
  const dur = ((e - s) + 1440) % 1440 || 1440;
  $<HTMLInputElement>('timeout_minutes').value = String(dur);
  $<HTMLInputElement>('gpus').value = String(Math.min(gpuDefault, gpuQuota));
  $('gpu-mem-field').style.display = Math.min(gpuDefault, gpuQuota) > 0 ? '' : 'none';
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
    const result = await resp.json().catch(() => ({} as { queued?: boolean }));
    if (result.queued) alert(t('queued'));
    form.reset();
    setDefaultTime();
    await refreshJobs();
  } finally {
    btn.disabled = false; btn.textContent = t('scheduleJob');
  }
}

// ── GPU dashboard ────────────────────────────
async function refreshGpus(): Promise<void> {
  const el = $('gpu-dashboard');
  try {
    const resp = await fetch('/api/gpus');
    if (!resp.ok) throw new Error();
    const data: { gpus: GpuStatus[]; error?: string } = await resp.json();
    if (data.error || !data.gpus.length) {
      el.innerHTML = execMode === 'mock' ? '' : `<div class="gpu-dash-summary">${t('noGpus')}</div>`;
      return;
    }
    const GiB = 1024 ** 3;
    const freeCount = data.gpus.filter(g => g.enabled !== false && g.mem_total - g.mem_used > GiB).length;
    el.innerHTML = `
      <div class="gpu-dash-summary">${t('gpuPool')}: <strong>${freeCount}</strong> / ${data.gpus.length} ${t('gpuFree')}</div>
      <div class="gpu-dash">
        ${data.gpus.map(g => {
          const off = g.enabled === false;
          const free = (g.mem_total - g.mem_used) / GiB;
          const total = g.mem_total / GiB;
          const pct = g.mem_total > 0 ? (g.mem_used / g.mem_total * 100) : 0;
          return `
            <div class="gpu-tile${off ? ' disabled' : ''}">
              <div class="t"><span>${g.node} #${g.index}</span><span class="${off ? '' : free > 1 ? 'free' : 'full'}">${off ? t('gpuDisabled') : `${free.toFixed(0)}G ${t('free')}`}</span></div>
              <div class="gpu-bar"><div class="gpu-bar-fill" style="width:${pct}%;${pct > 90 ? 'background:var(--red)' : ''}"></div></div>
              <div class="sub">${g.mem_used / GiB | 0}/${total.toFixed(0)}G · ${g.cores_used}/${g.cores_total} ${t('coresUsed')} · ${g.shared} ${t('sharing')}</div>
            </div>`;
        }).join('')}
      </div>`;
  } catch {
    el.innerHTML = '';
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
    (j.image || '').toLowerCase().includes(q)
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
            <span>🖼 ${escapeHtml(j.image || '')}</span>
            <span>⏰ ${scheduled}</span>
            ${j.started_at ? `<span>▶ ${started}</span>` : ''}
            ${j.gpus ? `<span>🎮 ${j.gpus} GPU${j.gpu_mem_mb ? ` · ${j.gpu_mem_mb}MB` : ''}</span>` : ''}
            ${j.output_count ? `<span>📦 ${j.output_count} ${t('files')}</span>` : ''}
          </div>
        </div>
        <span class="status status-${j.status}">${statusLabel(j.status)}</span>
        ${j.status === 'running' ? '' : `<button class="btn-delete-card" data-delete-id="${j.id}" title="${t('deleteJob')}">✕</button>`}
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
        <div class="kv"><span class="k">${t('image')}</span><span class="v">${escapeHtml(job.image || '—')}</span></div>
        <div class="kv"><span class="k">${t('entryCmd')}</span><span class="v"><code>${escapeHtml(job.entry_command || '—')}</code></span></div>
        <div class="kv"><span class="k">${t('maxRuntimeLabel')}</span><span class="v">${job.timeout_minutes || '—'} ${t('min')}</span></div>
        <div class="kv"><span class="k">${t('gpus')}</span><span class="v">${job.gpus ? `${job.gpus}${job.gpu_mem_mb ? ` · ${job.gpu_mem_mb} MB` : ''}` : '0 (CPU)'}</span></div>
        <div class="kv"><span class="k">${t('scheduledUtc')}</span><span class="v">${job.scheduled_at ? new Date(job.scheduled_at).toLocaleString() : '—'}</span></div>
        <div class="kv"><span class="k">${t('started')}</span><span class="v">${job.started_at ? new Date(job.started_at).toLocaleString() : '—'}</span></div>
        <div class="kv"><span class="k">${t('finished')}</span><span class="v">${job.finished_at ? new Date(job.finished_at).toLocaleString() : '—'}</span></div>
        <div class="kv"><span class="k">${t('outputs')}</span><span class="v">${job.output_count || 0} ${t('files')}</span></div>
        <div class="kv"><span class="k">${t('s3Prefix')}</span><span class="v"><code>${escapeHtml(job.s3_prefix || '—')}</code></span></div>
      </div>
    </div>`;

  if (job.ssh_port && (job.status === 'initializing' || job.status === 'pending' || job.status === 'running')) {
    const cmd = `ssh root@${location.hostname} -p ${job.ssh_port}`;
    html += `
      <div class="modal-section"><h4>${t('sshAccess')}</h4>
        <div class="kv-grid">
          <div class="kv"><span class="k">${t('sshCmd')}</span><span class="v"><code>${cmd}</code></span></div>
          <div class="kv"><span class="k">${t('sshPassword')}</span><span class="v"><code>${escapeHtml(job.ssh_password || '')}</code></span></div>
        </div>
        <div class="hint" style="margin-top:8px">${t('sshHint')}</div>
      </div>`;
  }
  if (job.status === 'pending' || job.status === 'initializing' || job.status === 'running') {
    html += `<div class="modal-section">
      ${job.status === 'pending' ? `<button class="btn-cancel" data-edit-id="${job.id}">${t('editJob')}</button>` : ''}
      <button class="btn-cancel" data-job-id="${job.id}">${t('cancelJob')}</button></div>`;
  }
  if (job.error) {
    html += `<div class="modal-section"><h4>${t('error')}</h4><div class="error-box">${escapeHtml(job.error)}</div></div>`;
  }

  html += `<div class="modal-section"><h4>${t('logs')}${logs ? ` <button class="btn-cancel" data-log-download="${job.id}">${t('downloadLogs')}</button>` : ''}</h4><div class="log-box">${logs ? ansiToHtml(logs) : `<span style="color:var(--text-dim)">${t('noLogs')}</span>`}</div></div>`;

  if (outputs.length) {
    html += `<div class="modal-section"><h4>${t('outputArtifacts')}</h4><ul class="output-list">`;
    outputs.forEach(o => {
      const fname = o.key.split('/').pop() || o.key;
      html += `<li><a href="${o.download_url}" download>${escapeHtml(fname)}</a><span class="size">${formatSize(o.size)}</span></li>`;
    });
    html += `</ul></div>`;
  }

  body.innerHTML = html;
}

// ── Edit pending job ─────────────────────────
function toLocalInput(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function showEditForm(jobId: string): void {
  const job = allJobs.find(j => j.id === jobId);
  if (!job) return;
  $('modal-body').innerHTML = `
    <form id="edit-form" data-job-id="${job.id}">
      <div class="field"><label>${t('jobName')}</label><input name="name" value="${escapeHtml(job.name)}" required /></div>
      <div class="field"><label>${t('entryCommand')}</label><input name="entry_command" value="${escapeHtml(job.entry_command)}" required /></div>
      <div class="field"><label>${t('scheduledStart')}</label><input type="datetime-local" name="scheduled_at" value="${toLocalInput(job.scheduled_at)}" required /></div>
      <div class="field"><label>${t('maxRuntime')}</label><input type="number" name="timeout_minutes" value="${job.timeout_minutes}" min="1" max="1440" /></div>
      <div class="field"><label>${t('outputPath')}</label><input name="output_path" value="${escapeHtml(job.output_path || 'output')}" /></div>
      <div class="field"><label>${t('gpus')}</label><input type="number" name="gpus" value="${job.gpus}" min="0" max="${gpuQuota}" /></div>
      <div class="field"><label>${t('gpuMem')}</label><input type="number" name="gpu_mem_mb" value="${job.gpu_mem_mb ?? ''}" min="0" step="1024" /></div>
      <button type="submit" class="btn-submit">${t('save')}</button>
    </form>`;
}

async function saveEdit(e: SubmitEvent): Promise<void> {
  e.preventDefault();
  const form = e.target as HTMLFormElement;
  const jobId = form.dataset.jobId!;
  const fd = new FormData(form);
  if (!(fd.get('gpu_mem_mb') as string).trim()) fd.delete('gpu_mem_mb');
  const resp = await fetch(`${API}/${jobId}`, { method: 'PATCH', body: fd });
  if (resp.ok) {
    await refreshJobs();
    openModal(jobId);
  } else {
    const err = await resp.json().catch(() => ({} as Record<string, string>));
    alert(err.detail || t('failed'));
  }
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

async function deleteJob(jobId: string): Promise<void> {
  if (!confirm(t('deleteConfirm'))) return;
  const resp = await fetch(`${API}/${jobId}`, { method: 'DELETE' });
  if (resp.ok) { closeModal(); await refreshJobs(); }
  else { const err = await resp.json().catch(() => ({} as Record<string, string>)); alert(err.detail || t('failed')); }
}

// ── ANSI color rendering ─────────────────────
const ANSI_COLORS: Record<number, string> = {
  30: '#6b7280', 31: '#f87171', 32: '#4ade80', 33: '#facc15',
  34: '#60a5fa', 35: '#e879f9', 36: '#22d3ee', 37: '#e5e5e5',
  90: '#9ca3af', 91: '#fca5a5', 92: '#86efac', 93: '#fde047',
  94: '#93c5fd', 95: '#f0abfc', 96: '#67e8f9', 97: '#ffffff',
};

function ansiToHtml(src: string): string {
  let out = '';
  let style = '';
  let buf = '';
  const flush = () => {
    if (!buf) return;
    const esc = escapeHtml(buf);
    out += style ? `<span style="${style}">${esc}</span>` : esc;
    buf = '';
  };
  let i = 0;
  while (i < src.length) {
    if (src[i] === '\x1b') {
      const m = /^\x1b\[([0-9;]*)m/.exec(src.slice(i));
      if (m) {
        flush();
        for (const p of m[1].split(';')) {
          const n = parseInt(p || '0', 10);
          if (n === 0) style = '';
          else if (n === 1) style += 'font-weight:600;';
          else if (ANSI_COLORS[n]) {
            style = style.replace(/color:[^;]+;?/g, '') + `color:${ANSI_COLORS[n]};`;
          }
        }
        i += m[0].length;
        continue;
      }
      const m2 = /^\x1b(\[[0-9;?]*[A-Za-z]|\][^\x07]*\x07|.)/.exec(src.slice(i));
      i += m2 ? m2[0].length : 1;  // drop unknown escapes/control seqs
      continue;
    }
    const c = src[i++];
    if (c >= ' ' || c === '\n' || c === '\t') buf += c;  // drop control chars
  }
  flush();
  return out;
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

  $('tab-login').addEventListener('click', () => switchAuthTab('login'));
  $('tab-register').addEventListener('click', () => switchAuthTab('register'));
  $('login-form').addEventListener('submit', doLogin);
  $('register-form').addEventListener('submit', doRegister);
  $('lang-toggle-auth').addEventListener('click', toggleLang);
  $('lang-toggle-app').addEventListener('click', toggleLang);
  $('btn-logout').addEventListener('click', doLogout);
  $('job-form').addEventListener('submit', submitJob);
  $('gpus').addEventListener('input', () => {
    $('gpu-mem-field').style.display = parseInt($<HTMLInputElement>('gpus').value) > 0 ? '' : 'none';
  });
  $('job-search').addEventListener('input', filterJobs);
  $('job-status-filter').addEventListener('change', filterJobs);
  $('modal-close').addEventListener('click', closeModal);
  $('modal').addEventListener('click', e => { if (e.target === $('modal')) closeModal(); });

  $('job-list').addEventListener('click', e => {
    const delBtn = (e.target as HTMLElement).closest('.btn-delete-card') as HTMLElement | null;
    if (delBtn?.dataset.deleteId) { deleteJob(delBtn.dataset.deleteId); return; }
    const card = (e.target as HTMLElement).closest('.job-card') as HTMLElement | null;
    if (card?.dataset.jobId) openModal(card.dataset.jobId);
  });

  $('modal-body').addEventListener('click', e => {
    const btn = (e.target as HTMLElement).closest('button') as HTMLElement | null;
    if (btn?.dataset.editId) { showEditForm(btn.dataset.editId); return; }
    if (btn?.dataset.jobId) cancelJob(btn.dataset.jobId);
    if (btn?.dataset.logDownload) {
      const a = document.createElement('a');
      a.href = `${API}/${btn.dataset.logDownload}/logs/download`;
      a.click();
    }
  });

  $('modal-body').addEventListener('submit', e => {
    if ((e.target as HTMLFormElement).id === 'edit-form') saveEdit(e as SubmitEvent);
  });

  // Admin panel handlers
  $('btn-admin').addEventListener('click', toggleAdminView);
  $('admin-tab-users').addEventListener('click', () => switchAdminTab('users'));
  $('admin-tab-params').addEventListener('click', () => switchAdminTab('params'));
  $('admin-tab-logs').addEventListener('click', () => switchAdminTab('logs'));
  $('admin-tab-monitor').addEventListener('click', () => switchAdminTab('monitor'));

  $('admin-content').addEventListener('click', async (e) => {
    const target = e.target as HTMLElement;
    const saveBtn = target.closest('[data-save-uid]') as HTMLElement | null;
    if (saveBtn) {
      const uid = saveBtn.dataset.saveUid!;
      const row = target.closest('tr') as HTMLTableRowElement;
      const isAdm = (row.querySelector('.u-admin') as HTMLInputElement).checked ? 1 : 0;
      const gpuVal = (row.querySelector('.u-gpu') as HTMLInputElement).value;
      const storageVal = (row.querySelector('.u-storage') as HTMLInputElement).value;
      const body: any = { is_admin: isAdm };
      body.gpu_quota_override = gpuVal === '' ? null : parseInt(gpuVal);
      body.storage_quota_override_gb = storageVal === '' ? null : parseFloat(storageVal);
      const r = await fetch(`/api/admin/users/${uid}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      if (r.ok) renderAdminUsers();
      else { const err = await r.json().catch(() => ({})); alert(err.detail || 'Failed'); }
    }
    const delBtn = target.closest('[data-del-uid]') as HTMLElement | null;
    if (delBtn) {
      if (!confirm(t('delete') + '?')) return;
      const uid = delBtn.dataset.delUid!;
      const r = await fetch(`/api/admin/users/${uid}`, { method: 'DELETE' });
      if (r.ok) renderAdminUsers();
      else { const err = await r.json().catch(() => ({})); alert(err.detail || 'Failed'); }
    }
  });

  $('admin-content').addEventListener('submit', async (e) => {
    const form = e.target as HTMLFormElement;
    if (form.id !== 'params-form') return;
    e.preventDefault();
    const fd = new FormData(form);
    const body: any = {};
    body.time_window_start = fd.get('time_window_start');
    body.time_window_end = fd.get('time_window_end');
    body.time_window_repeat = fd.get('time_window_repeat');
    body.gpu_default_quota = parseInt(fd.get('gpu_default_quota') as string);
    body.storage_default_quota_gb = parseFloat(fd.get('storage_default_quota_gb') as string);
    // Collect GPU devices from table rows
    const gpuRows = document.querySelectorAll('#gpu-devices-body .gpu-device-row');
    body.gpu_devices = Array.from(gpuRows).map(row => {
      const el = row as HTMLElement;
      return {
        uuid: el.dataset.uuid,
        enabled: (el.querySelector('.g-enabled') as HTMLInputElement).checked,
      };
    });
    const r = await fetch('/api/admin/params', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    const st = $('params-status');
    if (st) {
      if (r.ok) { st.textContent = t('paramsSaved'); st.style.color = 'var(--green)'; }
      else { const err = await r.json().catch(() => ({})); st.textContent = err.detail || t('paramsError'); st.style.color = 'var(--red)'; }
    }
  });

  checkAuth();
});
