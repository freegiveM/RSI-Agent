/* RSI-Agent review console.
   Hash routes keep browser back/forward and refresh working without a build step:
     #/                         review list (?f=<outcome>&q=<search>)
     #/jobs/<id>?tab=findings   report tabs: findings | code | process | audit
     #/jobs/<id>?tab=code&file=<path>&line=<n>&end=<n>
   All persisted text (diff, claims, paths) is untrusted and goes through esc().
   Behaviour is wired with one delegated listener; no inline handlers. */
(() => {
  'use strict';

  const $main = document.getElementById('main');
  const $crumbs = document.getElementById('crumbs');
  const $live = document.getElementById('live');
  const $toast = document.getElementById('toast');

  const state = {
    jobs: null,
    details: new Map(),      // job_id -> {data, at}
    poll: null,
    lastListHash: '#/',
    listScroll: 0,
    treeOpen: new Map(),     // job_id -> Set(dir path)
    expanded: new Set(),     // "job|file|hunk" with all lines shown
    openEvents: new Set(),
    missedOpen: false,
    evolution: null,
  };

  /* ---------- vocabulary ---------- */
  const OUTCOME = {
    verified_risks: { tone: 'risk', tag: '已验证风险', stamp: ['已验证', 'VERIFIED'] },
    no_reportable_risks: { tone: 'ok', tag: '无可报告风险', stamp: ['无可报告', 'CLEAR'] },
    needs_attention: { tone: 'warn', tag: '需要处理', stamp: ['待处理', 'ATTENTION'] },
    in_progress: { tone: 'run', tag: '进行中', stamp: ['审查中', 'RUNNING'] },
  };
  const STATUS = {
    RECEIVED: '已接收', SNAPSHOTTED: '已快照', PRECHECKED: '已预检', ROUTED: '已路由',
    ANALYZING: '分析中', VERIFYING: '验证中', COMPLETED: '已完成', STALE: 'Head 已变化', FAILED: '失败',
  };
  const FAILURE = {
    PROVIDER_TIMEOUT: '模型调用超时', PROVIDER_AUTH: 'Provider 鉴权或配额失败', INVALID_OUTPUT: '模型输出不满足 Finding 契约',
    STALE_HEAD: 'PR 在审查期间产生新提交', COMMENT_FAILED: '评论发布失败', QUEUE_REDELIVERY_EXHAUSTED: '超过重试次数',
    AUDIT_WRITE_FAILED: '审计写入异常', TRANSIENT_EXHAUSTED: '临时错误重试耗尽',
  };
  const SEVERITY = { critical: ['严重', 'risk'], high: ['高', 'risk'], medium: ['中', 'warn'], low: ['低', 'plain'] };
  const VERIFY = { verified: ['已验证', 'risk'], rejected: ['已驳回', 'plain'], insufficient_evidence: ['证据不足', 'warn'], unverified: ['未验证', 'plain'] };
  const SURFACE = {
    input_sink: '输入到敏感操作', auth_boundary: '认证与授权边界', parser_serialization: '解析与序列化',
    state_concurrency: '状态与并发', dependency_config: '依赖与配置', business_regression: '业务回归',
  };
  const FEEDBACK = { accepted: '确认', false_positive: '误报', insufficient_evidence: '证据不足', missed_risk: '漏报' };
  const FILE_STATUS = { modified: ['M', '修改'], added: ['A', '新增'], deleted: ['D', '删除'], renamed: ['R', '重命名'], binary: ['B', '二进制'], unknown: ['?', '未知'] };
  const PARSE_REASON = {
    empty: '任务快照中没有 Diff 文本，只记录了文件名。',
    no_hunks: '该文件没有文本 hunk，可能只改了文件权限，或是空文件。',
    binary: '二进制文件，不展示内容。',
    unsupported: '无法解析这段 Diff 的格式。',
    not_reported: '该任务没有记录 Diff 解析结果。',
  };
  const TABS = [['findings', 'Finding'], ['code', '代码'], ['process', '过程'], ['audit', '审计']];

  const ICON = {
    back: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M10 3 5 8l5 5"/></svg>',
    arrow: '<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="m6 3 5 5-5 5"/></svg>',
    search: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><circle cx="7" cy="7" r="4.5"/><path d="m10.5 10.5 3 3"/></svg>',
    ext: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M9 3h4v4M13 3 7 9M11 9.5V13H3V5h3.5"/></svg>',
    refresh: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M13 8a5 5 0 1 1-1.5-3.6M13 2.5v2.8h-2.8"/></svg>',
    prev: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M10 3 5 8l5 5"/></svg>',
    next: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="m6 3 5 5-5 5"/></svg>',
  };

  /* ---------- helpers ---------- */
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const na = '<span class="muted">未报告</span>';
  const pad = n => String(n).padStart(2, '0');
  const shortSha = sha => String(sha || '').slice(0, 10);

  function fmtTime(iso, withSeconds = false) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    const base = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
    return withSeconds ? `${base}:${pad(d.getSeconds())}` : base;
  }
  const rtf = new Intl.RelativeTimeFormat('zh-CN', { numeric: 'auto' });
  function fromNow(iso) {
    const diff = (new Date(iso).getTime() - Date.now()) / 1000;
    if (Number.isNaN(diff)) return '';
    const abs = Math.abs(diff);
    if (abs < 60) return '刚刚';
    if (abs < 3600) return rtf.format(Math.round(diff / 60), 'minute');
    if (abs < 86400) return rtf.format(Math.round(diff / 3600), 'hour');
    if (abs < 86400 * 30) return rtf.format(Math.round(diff / 86400), 'day');
    return fmtTime(iso);
  }
  function fmtMs(ms) {
    if (ms == null || Number.isNaN(Number(ms))) return null;
    const v = Number(ms);
    if (v < 1000) return `${Math.round(v)} ms`;
    if (v < 60000) return `${(v / 1000).toFixed(1)} s`;
    return `${Math.floor(v / 60000)} 分 ${Math.round((v % 60000) / 1000)} 秒`;
  }
  const fmtNum = v => (v == null ? null : Number(v).toLocaleString('zh-CN'));

  function href(path, params) {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params || {})) if (v != null && v !== '') q.set(k, v);
    const s = q.toString();
    return '#' + path + (s ? '?' + s : '');
  }
  const jobHref = (id, params) => href('/jobs/' + encodeURIComponent(id), params);

  function parseRoute() {
    const raw = location.hash.replace(/^#/, '') || '/';
    const i = raw.indexOf('?');
    const path = i < 0 ? raw : raw.slice(0, i);
    const params = new URLSearchParams(i < 0 ? '' : raw.slice(i + 1));
    const m = path.match(/^\/jobs\/([^/]+)\/?$/);
    if (m) {
      const tab = TABS.some(([k]) => k === params.get('tab')) ? params.get('tab') : 'findings';
      return { name: 'job', id: decodeURIComponent(m[1]), tab, params };
    }
    return { name: 'list', params };
  }

  async function getJSON(url, options) {
    const res = await fetch(url, { headers: { Accept: 'application/json' }, ...options });
    if (!res.ok) {
      const err = new Error(`HTTP ${res.status}`);
      err.status = res.status;
      throw err;
    }
    return res.json();
  }

  let toastTimer = null;
  function toast(text, isError = false) {
    $toast.textContent = text;
    $toast.classList.toggle('err', isError);
    $toast.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => $toast.classList.remove('show'), 2600);
  }

  function setCrumbs(items) {
    $crumbs.innerHTML = items.map((item, i) => {
      const sep = i ? '<span class="sep">/</span>' : '';
      return sep + (item.href ? `<a href="${esc(item.href)}">${esc(item.label)}</a>` : `<span class="here" aria-current="page">${esc(item.label)}</span>`);
    }).join('');
  }

  function stopPolling() {
    clearTimeout(state.poll);
    state.poll = null;
    $live.hidden = true;
  }
  function schedulePoll(fn, ms) {
    clearTimeout(state.poll);
    $live.hidden = false;
    state.poll = setTimeout(() => {
      if (document.hidden) return schedulePoll(fn, ms);
      fn();
    }, ms);
  }

  const outcomeOf = job => OUTCOME[job.outcome] || OUTCOME.in_progress;
  function tag(label, tone = 'plain') { return `<span class="tag ${tone}">${esc(label)}</span>`; }

  /* ---------- router ---------- */
  let current = null;
  function render() {
    const route = parseRoute();
    const prev = current;
    current = route;
    stopPolling();
    if (prev && prev.name === 'list') state.listScroll = window.scrollY;
    if (route.name === 'list') {
      state.lastListHash = location.hash || '#/';
      renderList(route, prev && prev.name === 'job');
    } else {
      const sameJob = prev && prev.name === 'job' && prev.id === route.id;
      if (!sameJob) { state.missedOpen = false; state.openEvents.clear(); window.scrollTo(0, 0); }
      renderJob(route, sameJob ? prev : null);
    }
  }
  const isCurrent = route => current === route;

  /* ---------- list ---------- */
  const FILTERS = [['', '全部'], ['verified_risks', '已验证风险'], ['needs_attention', '需要处理'], ['no_reportable_risks', '无可报告风险'], ['in_progress', '进行中']];

  async function renderList(route, returning) {
    setCrumbs([{ label: '审查记录' }]);
    document.title = '审查记录 · RSI-Agent';
    if (state.jobs) drawList(route);
    else $main.innerHTML = listSkeleton();
    if (returning) requestAnimationFrame(() => window.scrollTo(0, state.listScroll));
    try {
      const data = await getJSON('/api/jobs');
      state.jobs = data.jobs || [];
    } catch (err) {
      if (!isCurrent(route)) return;
      if (!state.jobs) {
        $main.innerHTML = `<div class="empty"><strong>无法载入审查记录</strong>API 没有响应（${esc(err.message)}）。确认 <code>python -m rsi_agent.run_api</code> 正在运行。<p><button class="btn" data-action="reload">重试</button></p></div>`;
      }
      return;
    }
    if (!isCurrent(route)) return;
    drawList(route);
    if (state.jobs.some(job => job.outcome === 'in_progress')) schedulePoll(() => isCurrent(route) && renderList(route), 8000);
  }

  function listSkeleton() {
    return `<div class="ledger-head"><div><div class="sk" style="width:120px;height:11px"></div><div class="sk" style="width:260px;height:38px;margin-top:12px"></div></div></div>
      <ul class="rows">${Array.from({ length: 4 }, () => '<li class="row"><div style="padding:18px 0;border-bottom:1px solid var(--rule-2)"><div class="sk" style="width:46%;height:16px"></div><div class="sk" style="width:28%;height:11px;margin-top:8px"></div></div></li>').join('')}</ul>`;
  }

  function drawList(route) {
    const jobs = state.jobs || [];
    const f = route.params.get('f') || '';
    const q = route.params.get('q') || '';
    if ($main.dataset.view !== 'list') {
      const count = key => jobs.filter(job => job.outcome === key).length;
      $main.dataset.view = 'list';
      $main.dataset.job = '';
      $main.innerHTML = `
        <section class="reveal">
          <div class="ledger-head">
            <div>
              <div class="eyebrow">Review ledger</div>
              <h1>审查记录</h1>
              <p>每条记录对应一次 PR 快照的审查。结论、代码证据和运行审计都来自已持久化的数据。</p>
            </div>
            <div class="tally" id="tally"></div>
          </div>
          <div class="filters">
            <div class="seg" role="group" aria-label="按结论筛选" id="seg"></div>
            <label class="search">${ICON.search}<span class="sr-only" hidden>搜索</span>
              <input id="q" type="search" autocomplete="off" spellcheck="false" placeholder="搜索仓库、PR、Head SHA，或粘贴 Job ID 后回车" value="${esc(q)}" aria-label="搜索审查记录" />
              <span class="kbd" aria-hidden="true">/</span>
            </label>
          </div>
          <ul class="rows" id="rows"></ul>
          <div class="list-foot" id="list-foot"></div>
        </section>`;
      void count;
    }
    const counts = Object.fromEntries(FILTERS.map(([key]) => [key, key ? jobs.filter(job => job.outcome === key).length : jobs.length]));
    document.getElementById('tally').innerHTML = `
      <div><b>${jobs.length}</b><span>全部任务</span></div>
      <div class="risk"><b>${counts.verified_risks}</b><span>已验证风险</span></div>
      <div class="warn"><b>${counts.needs_attention}</b><span>需要处理</span></div>`;
    document.getElementById('seg').innerHTML = FILTERS
      .filter(([key]) => !key || counts[key] || key === f)
      .map(([key, label]) => `<button type="button" data-action="filter" data-f="${key}" aria-pressed="${key === f}">${label}<em>${counts[key]}</em></button>`).join('');
    const input = document.getElementById('q');
    if (document.activeElement !== input && input.value !== q) input.value = q;
    drawRows(jobs, f, q);
  }

  function matchJob(job, q) {
    if (!q) return true;
    const hay = `${job.repository} #${job.pr_number} ${job.head_sha} ${job.job_id} ${job.status}`.toLowerCase();
    return q.toLowerCase().split(/\s+/).filter(Boolean).every(term => hay.includes(term.replace(/^#/, '#')));
  }

  function visibleJobs(f, q) { return (state.jobs || []).filter(job => (!f || job.outcome === f) && matchJob(job, q)); }

  function drawRows(jobs, f, q) {
    const rows = visibleJobs(f, q);
    const $rows = document.getElementById('rows');
    if (!jobs.length) {
      $rows.innerHTML = `<li><div class="empty" style="margin-top:20px"><strong>还没有审查任务</strong>收到 GitHub Webhook 后，任务会出现在这里。</div></li>`;
    } else if (!rows.length) {
      const looksLikeId = /^[0-9a-f-]{8,}$/i.test(q.trim());
      $rows.innerHTML = `<li><div class="empty" style="margin-top:20px"><strong>没有匹配的记录</strong>${looksLikeId ? '按回车直接打开这个 Job ID。' : '换个关键词，或清除筛选条件。'}</div></li>`;
    } else {
      $rows.innerHTML = rows.map((job, i) => {
        const o = outcomeOf(job);
        const verified = job.verified_count ?? null;
        return `<li class="row ${o.tone}" style="--i:${Math.min(i, 12)}">
          <a href="${esc(jobHref(job.job_id))}">
            <span class="stripe" aria-hidden="true"></span>
            <span class="title"><strong>${esc(job.repository)} <span class="muted">#${esc(job.pr_number)}</span></strong>
              <span class="mono">${esc(shortSha(job.head_sha))}</span><span> · ${esc(job.policy_version)}</span></span>
            <span class="cell">${tag(o.tag, o.tone)}<small>${esc(STATUS[job.status] || job.status)}${job.failure?.class && job.status !== 'COMPLETED' ? ' · ' + esc(job.failure.class) : ''}</small></span>
            <span class="cell hide-s"><span class="num">${esc(job.finding_count || 0)}</span> Finding<small>${verified == null ? '' : `${verified} 条已验证`}</small></span>
            <span class="cell hide-s" title="${esc(fmtTime(job.created_at, true))}">${esc(fromNow(job.created_at))}<small>${esc(fmtTime(job.created_at))}</small></span>
            <span class="arrow">${ICON.arrow}</span>
          </a></li>`;
      }).join('');
    }
    document.getElementById('list-foot').innerHTML = jobs.length
      ? `<span>显示 ${rows.length} / ${jobs.length} 条，按最近更新排序</span><span>回车打开第一条 · Esc 从报告返回这里</span>` : '';
  }

  function updateListQuery(patch) {
    const route = parseRoute();
    const params = { f: route.params.get('f') || '', q: route.params.get('q') || '', ...patch };
    const next = href('/', params);
    history.replaceState(null, '', next);
    state.lastListHash = next;
    current = parseRoute();
    drawList(current);
  }

  /* ---------- job ---------- */
  async function loadJob(id, force = false) {
    const cached = state.details.get(id);
    if (cached && !force && Date.now() - cached.at < 4000) return cached.data;
    const data = await getJSON('/api/jobs/' + encodeURIComponent(id));
    state.details.set(id, { data, at: Date.now() });
    return data;
  }

  const signature = job => [job.status, job.events?.length, job.audit_events?.length, job.findings?.length, job.feedback?.length, job.nodes?.length].join('|');

  async function renderJob(route, prevRoute) {
    const cached = state.details.get(route.id);
    if (cached) drawJob(cached.data, route, prevRoute);
    else { $main.dataset.view = ''; $main.innerHTML = jobSkeleton(); setCrumbs([{ label: '审查记录', href: state.lastListHash }, { label: '载入中' }]); }
    let job;
    try {
      job = await loadJob(route.id, Boolean(cached));
    } catch (err) {
      if (!isCurrent(route)) return;
      if (!cached) {
        setCrumbs([{ label: '审查记录', href: state.lastListHash }, { label: '未找到' }]);
        $main.dataset.view = '';
        $main.innerHTML = `<a class="btn ghost back" href="${esc(state.lastListHash)}">${ICON.back}返回审查记录</a>
          <div class="empty"><strong>${err.status === 404 ? '找不到这个任务' : '无法载入报告'}</strong>
          ${err.status === 404 ? `Job ID <code>${esc(route.id)}</code> 不存在，可能来自另一个数据库文件。` : `API 没有响应（${esc(err.message)}）。`}</div>`;
      }
      return;
    }
    if (!isCurrent(route)) return;
    if (!cached || signature(cached.data) !== signature(job)) drawJob(job, route, null);
    if (job.outcome === 'in_progress') {
      schedulePoll(async () => {
        if (!isCurrent(route)) return;
        state.details.delete(route.id);
        renderJob(route, route);
      }, 4000);
    }
  }

  function jobSkeleton() {
    return `<div class="sk" style="width:140px;height:28px;margin-bottom:22px"></div>
      <div class="sk" style="width:180px;height:11px"></div><div class="sk" style="width:52%;height:40px;margin:14px 0 12px"></div>
      <div class="sk" style="width:70%;height:14px"></div>`;
  }

  function drawJob(job, route, prevRoute) {
    const sameShell = $main.dataset.view === 'job' && $main.dataset.job === job.job_id && $main.dataset.sig === signature(job);
    const tabLabel = TABS.find(([k]) => k === route.tab)[1];
    setCrumbs([{ label: '审查记录', href: state.lastListHash }, { label: `${job.repository} #${job.pr_number}`, href: jobHref(job.job_id) }, { label: tabLabel }]);
    document.title = `${job.repository} #${job.pr_number} · ${tabLabel} · RSI-Agent`;
    if (!sameShell) {
      $main.dataset.view = 'job';
      $main.dataset.job = job.job_id;
      $main.dataset.sig = signature(job);
      $main.innerHTML = `<a class="btn ghost back" href="${esc(state.lastListHash)}">${ICON.back}返回审查记录</a>
        ${verdictHtml(job)}
        <nav class="tabs" id="tabs" aria-label="报告分区"></nav>
        <div id="pane"></div>`;
    }
    drawTabs(job, route);
    const tabChanged = !prevRoute || prevRoute.tab !== route.tab || !sameShell;
    if (tabChanged) {
      const tabs = document.getElementById('tabs');
      const top = tabs.getBoundingClientRect().top + window.scrollY - 52;
      if (prevRoute && window.scrollY > top) window.scrollTo(0, top);
    }
    drawPane(job, route, tabChanged);
  }

  function verdictHtml(job) {
    const o = outcomeOf(job);
    const verified = job.findings.filter(f => f.verification_status === 'verified').length;
    const title = {
      verified_risks: `发现 <span class="mark">${verified}</span> 项已验证风险`,
      no_reportable_risks: '未发现可报告风险',
      needs_attention: job.status === 'STALE' ? 'PR 已更新，结论作废' : '审查未完成，需要处理',
      in_progress: '审查进行中',
    }[job.outcome] || esc(job.outcome);
    const lede = {
      verified_risks: `共 ${job.findings.length} 条 Finding，其中 ${verified} 条通过证据验证。点开每条可以直接定位到变更代码。`,
      no_reportable_risks: '检测器已完成，没有输出可报告的 Finding。这不等于代码绝对安全，只说明本轮路由的检测范围内没有通过证据门槛的问题。',
      needs_attention: '任务没有走到可信的结论。下面列出失败分类和处理建议，原始过程在“过程”和“审计”里。',
      in_progress: `当前状态：${STATUS[job.status] || job.status}。页面每 4 秒自动刷新，完成后会显示结论。`,
    }[job.outcome] || '';
    const route = Array.isArray(job.route) && job.route.length ? job.route.join(' → ') : null;
    return `<section class="reveal">
      <div class="verdict ${o.tone}">
        <div>
          <div class="eyebrow">${esc(job.repository)} · PR #${esc(job.pr_number)}</div>
          <h1>${title}</h1>
          <p class="lede">${esc(lede)}</p>
          <div class="actions" style="justify-content:flex-start;margin-top:18px">
            <a class="btn" href="https://github.com/${esc(job.repository)}/pull/${esc(job.pr_number)}" target="_blank" rel="noopener noreferrer">${ICON.ext}在 GitHub 打开</a>
            <button class="btn ghost" type="button" data-action="refresh">${ICON.refresh}刷新</button>
          </div>
        </div>
        <div class="stamp" aria-hidden="true"><span>${esc(o.stamp[0])}<small>${esc(o.stamp[1])}</small></span></div>
      </div>
      <dl class="meta">
        <div><dt>状态</dt><dd>${esc(STATUS[job.status] || job.status)}</dd></div>
        <div><dt>Head</dt><dd><button type="button" data-action="copy" data-copy="${esc(job.head_sha)}" title="复制完整 SHA">${esc(shortSha(job.head_sha))}</button></dd></div>
        <div><dt>策略</dt><dd>${esc(job.policy_version)}</dd></div>
        <div><dt>创建</dt><dd title="${esc(job.created_at)}">${esc(fmtTime(job.created_at))}</dd></div>
        <div><dt>尝试</dt><dd>${job.attempt_count != null ? esc(job.attempt_count) + ' 次' : na}</dd></div>
        <div><dt>路由</dt><dd title="${esc(route || '')}">${route ? esc(route) : na}</dd></div>
      </dl>
      ${failureNotice(job)}
    </section>`;
  }

  function failureNotice(job) {
    const f = job.failure || {};
    const g = job.failure_guidance || {};
    const label = f.class ? `${esc(f.class)}${FAILURE[f.class] ? '（' + esc(FAILURE[f.class]) + '）' : ''}` : '';
    if (job.status === 'FAILED' || job.status === 'STALE') {
      if (!f.class) {
        return `<div class="notice risk"><strong>任务失败，但没有记录失败分类</strong><p>这个任务早于失败分类功能创建。查看“过程”里的状态迁移判断卡在哪一步。</p></div>`;
      }
      return `<div class="notice risk"><strong>${label}</strong>
        ${f.error ? `<p><code>${esc(f.error)}</code></p>` : ''}
        <p>处理建议：${esc(g.action || '查看审计事件和错误详情')}。${g.retryable ? '可以自动重试。' : '不会自动重试。'}</p></div>`;
    }
    if (f.class && job.status === 'COMPLETED') {
      return `<div class="notice"><strong>早期尝试曾失败</strong><p>共尝试 ${esc(job.attempt_count ?? '多')} 次，之前一次记录为 ${label}${f.error ? `：<code>${esc(f.error)}</code>` : ''}。最终尝试已完成，结论以本页为准。</p></div>`;
    }
    return '';
  }

  function drawTabs(job, route) {
    const files = (job.diff?.files || []).length || job.changed_files.length;
    const failedEvents = (job.audit_events || []).filter(e => e.status === 'failed').length;
    const counts = { findings: job.findings.length, code: files, process: (job.events || []).length, audit: (job.audit_events || []).length };
    document.getElementById('tabs').innerHTML = TABS.map(([key, label]) => {
      const flag = (key === 'findings' && job.outcome === 'verified_risks') || (key === 'audit' && failedEvents);
      return `<a href="${esc(jobHref(job.job_id, { tab: key === 'findings' ? null : key }))}" ${key === route.tab ? 'aria-current="page"' : ''} class="${flag ? 'flag' : ''}">${label}<em>${counts[key]}</em></a>`;
    }).join('');
  }

  function drawPane(job, route, animate) {
    const pane = document.getElementById('pane');
    const html = { findings: findingsPane, code: codePane, process: processPane, audit: auditPane }[route.tab](job, route);
    pane.className = animate ? 'pane' : 'pane still';
    pane.innerHTML = html;
    if (route.tab === 'code') afterCode(job, route);
  }

  /* ---------- findings tab ---------- */
  function codeLink(job, f) {
    return jobHref(job.job_id, { tab: 'code', file: f.file, line: f.start_line, end: f.end_line });
  }

  function findingsPane(job) {
    const feedback = job.feedback || [];
    const forFinding = id => feedback.filter(item => item.finding_id === id);
    let body;
    if (job.findings.length) {
      body = job.findings.map((f, i) => {
        const sev = SEVERITY[String(f.severity).toLowerCase()] || [f.severity, 'plain'];
        const ver = VERIFY[f.verification_status] || [f.verification_status, 'plain'];
        const marks = forFinding(f.finding_id);
        const last = marks[marks.length - 1];
        return `<article class="finding ${f.verification_status === 'verified' ? 'risk' : ''}" id="finding-${i + 1}">
          <div class="idx">${pad(i + 1)}</div>
          <div>
            <div class="head">${tag('严重度 ' + sev[0], sev[1])}${tag(ver[0], ver[1])}${tag(SURFACE[f.risk_surface] || f.risk_surface, 'plain')}</div>
            <h3>${esc(f.claim)}</h3>
            <a class="loc" href="${esc(codeLink(job, f))}">${esc(f.file)}:${esc(f.start_line)}${f.end_line && f.end_line !== f.start_line ? '–' + esc(f.end_line) : ''} ${ICON.arrow}</a>
            <dl>
              <dt>证据</dt><dd class="evidence">${f.evidence_refs.length ? f.evidence_refs.map(ref => `<code>${esc(ref)}</code>`).join('') : '<span class="muted">未提供</span>'}</dd>
              <dt>Finding ID</dt><dd class="mono muted">${esc(f.finding_id)}</dd>
            </dl>
            <div class="ops">
              <a class="btn solid" href="${esc(codeLink(job, f))}">查看代码</a>
              <button class="btn" type="button" data-action="fb" data-kind="accepted" data-finding="${esc(f.finding_id)}">确认风险</button>
              <button class="btn" type="button" data-action="fb" data-kind="false_positive" data-finding="${esc(f.finding_id)}">标记误报</button>
              <button class="btn" type="button" data-action="fb" data-kind="insufficient_evidence" data-finding="${esc(f.finding_id)}">证据不足</button>
              ${last ? `<span class="done">已反馈：${esc(FEEDBACK[last.kind] || last.kind)} · ${esc(fmtTime(last.created_at))}</span>` : ''}
            </div>
          </div>
        </article>`;
      }).join('');
    } else {
      const copy = {
        no_reportable_risks: ['检测器已完成，没有输出可报告 Finding', '这不等于代码绝对安全。如果你确认存在漏报，可以在下方提交。'],
        needs_attention: ['本次审查没有产出可信结论', '失败原因见页面顶部，节点细节见“过程”和“审计”。'],
        in_progress: ['审查还在进行', 'Finding 会在验证完成后出现在这里。'],
      }[job.outcome] || ['没有 Finding', ''];
      body = `<div class="empty"><strong>${esc(copy[0])}</strong>${esc(copy[1])}</div>`;
    }
    const overall = feedback.filter(item => !item.finding_id);
    return `<section class="section">
        <h2>Finding <small>${job.findings.length ? '按审查输出顺序排列' : ''}</small></h2>
        ${body}
      </section>
      <section class="section">
        <h2>整体反馈 <small>反馈绑定 Head ${esc(shortSha(job.head_sha))}，只进入离线评测，不会直接改动策略</small></h2>
        <div class="ops" style="display:flex;gap:8px;flex-wrap:wrap">
          ${job.outcome === 'in_progress' ? '' : `<button class="btn" type="button" data-action="fb" data-kind="accepted">${job.findings.length ? '确认整体结论' : '确认无漏报'}</button>`}
          <button class="btn" type="button" data-action="missed-open" aria-expanded="${state.missedOpen}">提交漏报</button>
        </div>
        ${state.missedOpen ? `<form class="feedback-box" data-form="missed">
          <label for="missed-note">漏掉了什么风险？写上文件和行号，便于离线复现。</label>
          <textarea id="missed-note" name="note" required placeholder="例如：src/auth.py:42 新增的接口没有校验调用者权限"></textarea>
          <div class="row-ops"><span>提交后会记录为 missed_risk 事件</span>
            <span><button class="btn ghost" type="button" data-action="missed-close">取消</button> <button class="btn solid" type="submit">提交漏报</button></span></div>
        </form>` : ''}
        ${feedback.length ? `<ul class="history" style="margin-top:16px">${feedback.slice().reverse().map(item => `<li><time>${esc(fmtTime(item.created_at, true))}</time><span>${esc(FEEDBACK[item.kind] || item.kind)}${item.finding_id ? ` · <span class="mono muted">${esc(item.finding_id)}</span>` : ' · 整体'}${item.note ? ` — ${esc(item.note)}` : ''}</span></li>`).join('')}</ul>` : (overall.length ? '' : '<p class="muted" style="font-size:13px;margin-top:14px">这个 Head 还没有反馈。</p>')}
      </section>`;
  }

  async function sendFeedback(kind, findingId, note, button) {
    const job = state.details.get(current.id)?.data;
    if (!job) return;
    const eventId = 'ui-' + (crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2));
    const buttons = document.querySelectorAll('[data-action="fb"], form[data-form] button');
    buttons.forEach(b => { b.disabled = true; });
    try {
      await getJSON(`/api/jobs/${encodeURIComponent(job.job_id)}/feedback`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ event_id: eventId, kind, finding_id: findingId || null, note: note || '' }),
      });
      toast(`已记录：${FEEDBACK[kind] || kind}`);
      state.missedOpen = false;
      const fresh = await loadJob(job.job_id, true);
      if (current.name === 'job' && current.id === job.job_id) drawJob(fresh, current, current);
    } catch (err) {
      toast(`反馈提交失败（${err.message}）`, true);
      buttons.forEach(b => { b.disabled = false; });
    }
    void button;
  }

  /* ---------- code tab ---------- */
  function diffFiles(job) {
    return job.diff?.files?.length ? job.diff.files
      : job.changed_files.map(path => ({ path, status: 'unknown', parse_status: 'not_reported', hunks: [], finding_ids: [] }));
  }

  function pickFile(job, route) {
    const files = diffFiles(job);
    const wanted = route.params.get('file');
    if (wanted) return files.find(file => file.path === wanted) || { path: wanted, missing: true, hunks: [], finding_ids: [], status: 'unknown' };
    const firstFinding = job.findings[0];
    return (firstFinding && files.find(file => file.path === firstFinding.file))
      || files.find(file => file.finding_ids?.length) || files.find(file => file.hunks.length) || files[0] || null;
  }

  function buildTree(files) {
    const root = { dirs: new Map(), files: [] };
    for (const file of files) {
      const parts = String(file.path || '').split('/').filter(Boolean);
      if (!parts.length) continue;
      let node = root;
      let acc = '';
      for (const part of parts.slice(0, -1)) {
        acc = acc ? acc + '/' + part : part;
        if (!node.dirs.has(part)) node.dirs.set(part, { path: acc, dirs: new Map(), files: [] });
        node = node.dirs.get(part);
      }
      node.files.push({ ...file, name: parts[parts.length - 1] });
    }
    // Collapse single-child directory chains (a/b/c/) so deep paths take one row.
    const squash = node => {
      for (const [name, child] of [...node.dirs]) {
        let label = name;
        let cur = child;
        while (cur.files.length === 0 && cur.dirs.size === 1) {
          const [[n, c]] = [...cur.dirs];
          label += '/' + n;
          cur = c;
        }
        squash(cur);
        node.dirs.delete(name);
        node.dirs.set(label, cur);
      }
    };
    squash(root);
    return root;
  }

  const countFindings = node => node.files.reduce((n, f) => n + (f.finding_ids?.length || 0), 0) + [...node.dirs.values()].reduce((n, d) => n + countFindings(d), 0);

  function treeHtml(job, selected, filter) {
    const files = diffFiles(job).filter(file => {
      if (filter.onlyFindings && !(file.finding_ids?.length)) return false;
      return !filter.q || file.path.toLowerCase().includes(filter.q.toLowerCase());
    });
    if (!files.length) return '<div class="none">没有匹配的文件</div>';
    const open = treeOpenSet(job, files);
    const forceOpen = Boolean(filter.q || filter.onlyFindings);
    const branch = (node, depth) => {
      const dirs = [...node.dirs.entries()].sort(([a], [b]) => a.localeCompare(b));
      const fileRows = node.files.sort((a, b) => a.name.localeCompare(b.name)).map(file => {
        const st = FILE_STATUS[file.status] || FILE_STATUS.unknown;
        const n = file.finding_ids?.length || 0;
        return `<button type="button" class="file" style="--d:${depth}" data-action="file" data-path="${esc(file.path)}" aria-current="${selected && selected.path === file.path}" title="${esc(file.path)} · ${esc(st[1])}">
          <span class="st ${esc(file.status)}" aria-label="${esc(st[1])}">${st[0]}</span><span class="name">${esc(file.name)}</span>${n ? `<span class="fc" aria-label="${n} 个 Finding">${n}</span>` : ''}</button>`;
      }).join('');
      return dirs.map(([name, child]) => `<details data-dir="${esc(child.path)}" ${forceOpen || open.has(child.path) ? 'open' : ''}>
          <summary style="--d:${depth}">${esc(name)}/${countFindings(child) ? ` <span class="fc" style="font-size:10.5px;color:var(--seal)">${countFindings(child)}</span>` : ''}</summary>${branch(child, depth + 1)}</details>`).join('') + fileRows;
    };
    return branch(buildTree(files), 0);
  }

  function treeOpenSet(job, files) {
    if (!state.treeOpen.has(job.job_id)) {
      // Default: directories with findings or the selected file are open, the rest collapsed.
      const set = new Set();
      const selectedPath = parseRoute().params.get('file');
      for (const file of files) {
        if (!(file.finding_ids?.length) && file.path !== selectedPath) continue;
        const parts = file.path.split('/');
        for (let i = 1; i < parts.length; i++) set.add(parts.slice(0, i).join('/'));
      }
      state.treeOpen.set(job.job_id, set);
    }
    const set = state.treeOpen.get(job.job_id);
    // Squashed chains are keyed by their deepest path; keep every prefix in sync.
    return { has: path => set.has(path) };
  }

  function codePane(job, route) {
    const selected = pickFile(job, route);
    const total = diffFiles(job).length;
    const withFindings = diffFiles(job).filter(f => f.finding_ids?.length).length;
    return `<div class="code-layout">
      <aside class="tree-pane" aria-label="变更文件">
        <div class="tree-tools">
          <label class="search">${ICON.search}<input id="tree-q" type="search" autocomplete="off" spellcheck="false" placeholder="筛选 ${total} 个文件" aria-label="筛选文件" /></label>
          <div class="line">
            <label><input type="checkbox" id="only-findings" ${withFindings ? '' : 'disabled'} /> 只看有 Finding（${withFindings}）</label>
            <span class="links"><button type="button" data-action="tree-expand">展开</button> · <button type="button" data-action="tree-collapse">收起</button></span>
          </div>
        </div>
        <div class="tree" id="tree">${treeHtml(job, selected, { q: '', onlyFindings: false })}</div>
      </aside>
      <section class="diff-pane" id="diff" aria-live="polite">${diffHtml(job, selected, route)}</section>
    </div>`;
  }

  function lineRange(route) {
    const line = Number(route.params.get('line')) || null;
    const end = Number(route.params.get('end')) || line;
    return line ? { line, end: Math.max(line, end) } : null;
  }

  function diffHtml(job, file, route) {
    if (!file) return '<div class="diff-empty"><strong>没有变更文件</strong>这个任务的快照里没有记录文件。</div>';
    const files = diffFiles(job);
    const index = files.findIndex(item => item.path === file.path);
    const st = FILE_STATUS[file.status] || FILE_STATUS.unknown;
    const range = route.params.get('file') === file.path ? lineRange(route) : null;
    const fromFinding = Boolean(range);
    const head = `<div class="diff-head">
        <div class="path">${esc(file.path)}<small>${esc(st[1])}${file.status === 'renamed' && file.old_path ? ' · 原路径 ' + esc(file.old_path) : ''} · ${file.hunks.length} 个 hunk${file.finding_ids?.length ? ` · ${file.finding_ids.length} 个 Finding` : ''}</small></div>
        <div class="nav">
          ${fromFinding ? `<a class="btn ghost" href="${esc(jobHref(job.job_id))}">${ICON.back}回到 Finding</a>` : ''}
          <button class="btn ghost" type="button" data-action="step-file" data-step="-1" ${index <= 0 ? 'disabled' : ''} aria-label="上一个文件">${ICON.prev}</button>
          <button class="btn ghost" type="button" data-action="step-file" data-step="1" ${index < 0 || index >= files.length - 1 ? 'disabled' : ''} aria-label="下一个文件">${ICON.next}</button>
        </div></div>`;
    if (file.missing) return head + `<div class="diff-empty"><strong>快照中没有这个文件</strong><code>${esc(file.path)}</code> 不在本次 PR 的变更列表里。</div>`;
    if (!file.hunks.length) return head + `<div class="diff-empty"><strong>没有可展示的变更片段</strong>${esc(PARSE_REASON[file.parse_status] || PARSE_REASON.not_reported)}</div>`;
    const located = range && file.hunks.some(h => h.lines.some(l => l.new != null && l.new >= range.line && l.new <= range.end));
    const miss = range && !located ? `<div class="notice" style="margin:0 0 14px"><strong>第 ${esc(range.line)} 行不在变更片段内</strong><p>Finding 引用的是未修改的上下文行，或行号超出了 Diff 范围。下面显示该文件的全部变更。</p></div>` : '';
    const hunks = file.hunks.map((hunk, hi) => {
      const key = `${job.job_id}|${file.path}|${hi}`;
      const hit = range && hunk.lines.some(l => l.new != null && l.new >= range.line && l.new <= range.end);
      const limit = 120;
      const showAll = state.expanded.has(key) || hunk.lines.length <= limit + 40 || hit;
      const lines = showAll ? hunk.lines : hunk.lines.slice(0, limit);
      const rows = lines.map(l => {
        const focus = range && l.new != null && l.new >= range.line && l.new <= range.end;
        return `<div class="ln ${esc(l.kind)}${focus ? ' focus' : ''}"${focus && l.new === range.line ? ' id="focus-line"' : ''}><span class="o">${l.old ?? ''}</span><span class="n">${l.new ?? ''}</span><span class="t">${esc(l.text)}</span></div>`;
      }).join('');
      const linked = hunk.finding_ids?.length || 0;
      return `<div class="hunk${hit ? ' hit' : ''}" id="hunk-${hi}">
        <header><span>${esc(hunk.header)}</span>${linked ? `<b>关联 ${linked} 个 Finding</b>` : ''}</header>
        <div class="lines">${rows}</div>
        ${showAll ? '' : `<button class="more" type="button" data-action="more" data-key="${esc(key)}">展开剩余 ${hunk.lines.length - limit} 行</button>`}
      </div>`;
    }).join('');
    return head + `<div class="diff-body">${miss}${hunks}</div>`;
  }

  function afterCode(job, route) {
    const focus = document.getElementById('focus-line');
    if (focus) requestAnimationFrame(() => focus.scrollIntoView({ block: 'center', behavior: 'instant' in document.documentElement.style ? 'instant' : 'auto' }));
    void job; void route;
  }

  function selectFile(path) {
    const job = state.details.get(current.id)?.data;
    if (!job) return;
    const next = jobHref(job.job_id, { tab: 'code', file: path });
    history.replaceState(null, '', next);
    current = parseRoute();
    const file = pickFile(job, current);
    document.getElementById('diff').innerHTML = diffHtml(job, file, current);
    document.querySelectorAll('#tree .file').forEach(el => el.setAttribute('aria-current', String(el.dataset.path === path)));
    const head = document.querySelector('.diff-head');
    if (head && head.getBoundingClientRect().top < 0) document.getElementById('diff').scrollIntoView({ block: 'start' });
  }

  function redrawTree() {
    const job = state.details.get(current.id)?.data;
    if (!job) return;
    const q = document.getElementById('tree-q')?.value.trim() || '';
    const onlyFindings = document.getElementById('only-findings')?.checked || false;
    document.getElementById('tree').innerHTML = treeHtml(job, pickFile(job, current), { q, onlyFindings });
  }

  /* ---------- process tab ---------- */
  function processPane(job) {
    const events = job.events || [];
    let attempt = 1;
    const steps = events.map((event, i) => {
      const prev = events[i - 1];
      const delta = prev ? new Date(event.at) - new Date(prev.at) : null;
      const isLast = i === events.length - 1;
      const retry = event.from && event.to === 'RECEIVED';
      if (retry) attempt += 1;
      const cls = event.to === 'FAILED' || event.to === 'STALE' ? 'fail' : (isLast && job.outcome === 'in_progress' ? 'cur' : 'done');
      const label = event.from ? `${STATUS[event.from] || event.from} → ${STATUS[event.to] || event.to}` : `创建任务 · ${STATUS[event.to] || event.to}`;
      return `<li class="${cls}"><div class="s-top"><strong>${esc(label)}</strong><time>${esc(fmtTime(event.at, true))}</time>${delta != null && delta >= 0 ? `<span class="s-sub">+${esc(fmtMs(delta))}</span>` : ''}</div>
        ${retry ? `<div class="s-sub">重新入队，开始第 ${attempt} 次尝试</div>` : ''}</li>`;
    }).join('');
    const nodes = job.nodes || [];
    const maxMs = Math.max(1, ...nodes.map(n => Number(n.duration_ms) || 0));
    const nodeRows = nodes.map(n => `<tr>
        <td class="mono">${esc(n.node)}</td>
        <td>${n.status === 'failed' ? tag('失败', 'risk') : n.status === 'completed' ? tag('完成', 'ok') : tag(n.status || '未知', 'plain')}${n.error_class ? `<div class="s-sub mono">${esc(n.error_class)}</div>` : ''}</td>
        <td class="bar-cell">${n.duration_ms != null ? `<div class="dur"><i style="width:${Math.max(2, (Number(n.duration_ms) / maxMs) * 100).toFixed(1)}%"></i></div>` : ''}</td>
        <td class="r">${n.duration_ms != null ? esc(fmtMs(n.duration_ms)) : '未报告'}</td></tr>`).join('');
    const b = job.budget || {};
    const fig = (label, value, unit) => `<div><dt>${esc(label)}</dt>${value == null ? '<dd class="na">未报告</dd>' : `<dd>${esc(value)}${unit ? `<small>${esc(unit)}</small>` : ''}</dd>`}</div>`;
    const conf = b.configured || {};
    const obs = b.observed || {};
    const est = b.estimated || {};
    const budget = b.status === 'not_reported' && !b.configured
      ? '<div class="empty"><strong>预算与用量未报告</strong>这个任务没有运行时记录，通常是因为它早于审计功能创建，或者在路由之前就失败了。</div>'
      : `<div class="grid-2">
          <div><div class="eyebrow" style="margin-bottom:10px">配置预算 · configured</div><dl class="figures">
            ${fig('max_tokens', fmtNum(conf.max_tokens))}${fig('Provider 超时', conf.provider_timeout_seconds, 's')}${fig('Worker 节点超时', conf.node_timeout_seconds, 's')}${fig('思考模式', conf.thinking_mode)}</dl></div>
          <div><div class="eyebrow" style="margin-bottom:10px">实测 · observed</div><dl class="figures">
            ${fig('输入 Token', fmtNum(obs.provider_input_tokens))}${fig('输出 Token', fmtNum(obs.provider_output_tokens))}${fig('推理 Token', fmtNum(obs.provider_reasoning_tokens))}
            ${fig('最近节点耗时', fmtMs(obs.last_node_duration_ms))}${fig('全程耗时', fmtMs(obs.review_duration_ms))}${fig('估算输入 Token', fmtNum(est.input_tokens))}</dl></div>
        </div>
        <p class="source-note">Token 只在 Provider 返回 usage 时显示；“估算”是本地估算，不代表实际消耗；max_tokens 是上限，不是用量。</p>`;
    const features = job.risk_features && typeof job.risk_features === 'object' ? job.risk_features : null;
    const featureLabels = { changed_surface: '变更规模', executable_change: '可执行代码变更', sensitive_sink: '敏感操作', test_gap: '缺少测试', static_warnings: '静态告警', risk_surfaces: '风险面' };
    const fv = (k, v) => {
      if (typeof v === 'boolean') return v ? '是' : '否';
      if (Array.isArray(v)) return v.length ? v.map(x => esc(k === 'risk_surfaces' ? (SURFACE[x] || x) : x)).join('、') : '无';
      return esc(v);
    };
    return `<section class="section"><h2>状态迁移 <small>来自 review_events，共 ${events.length} 条</small></h2>
        ${steps ? `<ol class="steps">${steps}</ol>` : '<div class="empty"><strong>状态迁移未报告</strong></div>'}</section>
      <section class="section"><h2>执行节点 <small>Worker 实测耗时，不等于模型延迟</small></h2>
        ${nodes.length ? `<table class="nodes"><thead><tr><th>节点</th><th>状态</th><th>耗时占比</th><th style="text-align:right">耗时</th></tr></thead><tbody>${nodeRows}</tbody></table>`
          : '<div class="empty"><strong>节点记录未报告</strong>任务早于运行时记录功能创建，或者在路由前就结束了。</div>'}</section>
      <section class="section"><h2>预算与用量</h2>${budget}</section>
      <section class="section"><h2>路由依据</h2>
        ${features ? `<dl class="figures">${Object.entries(features).map(([k, v]) => `<div><dt>${esc(featureLabels[k] || k)}</dt><dd style="font:500 14px/1.5 var(--sans)">${fv(k, v)}</dd></div>`).join('')}</dl>`
          : '<div class="empty"><strong>路由特征未报告</strong></div>'}</section>`;
  }

  /* ---------- audit tab ---------- */
  let auditFilter = { source: '', failed: false };

  function auditPane(job) {
    const events = job.audit_events || [];
    const sources = [...new Set(events.map(e => e.source))];
    const shown = events.filter(e => (!auditFilter.source || e.source === auditFilter.source) && (!auditFilter.failed || e.status === 'failed'));
    const failed = events.filter(e => e.status === 'failed').length;
    const rows = shown.map(e => {
      const open = state.openEvents.has(e.event_id);
      const statusTag = e.status === 'failed' ? tag('失败', 'risk') : e.status === 'completed' ? tag('完成', 'ok') : e.status === 'running' ? tag('开始', 'plain') : tag(e.status, 'warn');
      return `<tr class="ev ${e.status === 'failed' ? 'failed' : ''}" data-action="ev" data-id="${esc(e.event_id)}" tabindex="0" aria-expanded="${open}">
          <td class="mono">#${esc(e.attempt)}</td><td class="mono">${esc(e.source)} · ${esc(e.node)}</td><td class="mono">${esc(e.event_type)}</td>
          <td>${statusTag}${e.error_class ? `<div class="s-sub mono">${esc(e.error_class)}</div>` : ''}</td><td class="r">${e.duration_ms != null ? esc(fmtMs(e.duration_ms)) : '—'}</td></tr>
        ${open ? `<tr class="detail"><td colspan="5"><pre>${esc(JSON.stringify({ event_id: e.event_id, trace_id: e.trace_id, schema_version: e.schema_version, started_at: e.started_at, finished_at: e.finished_at, error_code: e.error_code, metadata: e.metadata }, null, 2))}</pre></td></tr>` : ''}`;
    }).join('');
    const ev = state.evolution;
    return `<section class="section"><h2>运行审计 <small>append-only 事件，点击行查看 metadata</small></h2>
        ${events.length ? `<div class="audit-tools">
            <div class="seg" role="group" aria-label="按来源筛选">
              <button type="button" data-action="audit-src" data-src="" aria-pressed="${!auditFilter.source}">全部<em>${events.length}</em></button>
              ${sources.map(s => `<button type="button" data-action="audit-src" data-src="${esc(s)}" aria-pressed="${auditFilter.source === s}">${esc(s)}<em>${events.filter(e => e.source === s).length}</em></button>`).join('')}
            </div>
            <label style="font-size:13px;display:inline-flex;gap:6px;align-items:center;cursor:pointer"><input type="checkbox" data-action="audit-failed" ${auditFilter.failed ? 'checked' : ''} ${failed ? '' : 'disabled'} /> 只看失败（${failed}）</label>
          </div>
          ${shown.length ? `<table class="events"><thead><tr><th>尝试</th><th>来源 · 节点</th><th>事件</th><th>状态</th><th style="text-align:right">耗时</th></tr></thead><tbody>${rows}</tbody></table>` : '<div class="empty">没有符合筛选条件的事件。</div>'}`
          : `<div class="empty"><strong>审计事件未报告</strong>${job.outcome === 'in_progress' ? 'Worker 还没有写入审计事件，开始处理后会出现在这里。' : '这个任务没有审计事件，通常是因为它早于审计事件表创建。'}页面不会补造日志，状态迁移仍可在“过程”里查看。</div>`}
      </section>
      <section class="section"><h2>策略演化 <small>只读；候选策略必须经过离线 Validation/Holdout 才能激活</small></h2>
        ${ev ? `<dl class="figures">${Object.entries(ev.feedback_by_kind || {}).map(([k, v]) => `<div><dt>${esc(FEEDBACK[k] || k)}</dt><dd>${esc(v)}</dd></div>`).join('')}<div><dt>候选策略</dt><dd>${esc((ev.candidates || []).length)}</dd></div></dl><p class="source-note">${esc(ev.note)}</p>`
          : '<button class="btn" type="button" data-action="evolution">载入全局反馈汇总</button>'}
      </section>`;
  }

  function redrawPane() {
    const job = state.details.get(current.id)?.data;
    if (job) drawPane(job, current, false);
  }

  /* ---------- events ---------- */
  document.addEventListener('click', async event => {
    const el = event.target.closest('[data-action]');
    if (!el) return;
    const action = el.dataset.action;
    switch (action) {
      case 'reload': render(); break;
      case 'refresh': {
        el.disabled = true;
        try {
          const job = await loadJob(current.id, true);
          $main.dataset.sig = '';
          drawJob(job, current, current);
          toast('已刷新');
        } catch (err) { toast(`刷新失败（${err.message}）`, true); el.disabled = false; }
        break;
      }
      case 'filter': updateListQuery({ f: el.dataset.f }); break;
      case 'copy':
        try { await navigator.clipboard.writeText(el.dataset.copy); toast('已复制完整 SHA'); }
        catch { toast('浏览器不允许写入剪贴板', true); }
        break;
      case 'fb': sendFeedback(el.dataset.kind, el.dataset.finding, '', el); break;
      case 'missed-open': state.missedOpen = !state.missedOpen; redrawPane(); if (state.missedOpen) document.getElementById('missed-note')?.focus(); break;
      case 'missed-close': state.missedOpen = false; redrawPane(); break;
      case 'file': selectFile(el.dataset.path); break;
      case 'step-file': {
        const job = state.details.get(current.id)?.data;
        const files = diffFiles(job);
        const i = files.findIndex(file => file.path === pickFile(job, current)?.path);
        const next = files[i + Number(el.dataset.step)];
        if (next) selectFile(next.path);
        break;
      }
      case 'more': {
        state.expanded.add(el.dataset.key);
        const job = state.details.get(current.id)?.data;
        const y = window.scrollY;
        document.getElementById('diff').innerHTML = diffHtml(job, pickFile(job, current), current);
        window.scrollTo(0, y);
        break;
      }
      case 'tree-expand':
      case 'tree-collapse': {
        const open = action === 'tree-expand';
        const set = state.treeOpen.get(current.id);
        document.querySelectorAll('#tree details').forEach(d => { d.open = open; if (set) open ? set.add(d.dataset.dir) : set.delete(d.dataset.dir); });
        break;
      }
      case 'ev': {
        const id = el.dataset.id;
        state.openEvents.has(id) ? state.openEvents.delete(id) : state.openEvents.add(id);
        redrawPane();
        document.querySelector(`tr.ev[data-id="${CSS.escape(id)}"]`)?.focus({ preventScroll: true });
        break;
      }
      case 'audit-src': auditFilter.source = el.dataset.src; redrawPane(); break;
      case 'audit-failed': auditFilter.failed = el.checked; redrawPane(); break;
      case 'evolution':
        el.disabled = true;
        try { state.evolution = await getJSON('/api/evolution'); redrawPane(); }
        catch (err) { toast(`载入失败（${err.message}）`, true); el.disabled = false; }
        break;
      default: break;
    }
  });

  // Remember which tree directories the user opened, so re-renders keep the layout.
  document.addEventListener('toggle', event => {
    const d = event.target;
    if (!(d instanceof HTMLDetailsElement) || !d.dataset.dir || !current || current.name !== 'job') return;
    if (document.getElementById('tree-q')?.value || document.getElementById('only-findings')?.checked) return;
    const set = state.treeOpen.get(current.id);
    if (set) d.open ? set.add(d.dataset.dir) : set.delete(d.dataset.dir);
  }, true);

  document.addEventListener('submit', event => {
    const form = event.target.closest('form[data-form="missed"]');
    if (!form) return;
    event.preventDefault();
    const note = form.elements.note.value.trim();
    if (!note) { form.elements.note.focus(); toast('请写明漏掉的风险和位置', true); return; }
    sendFeedback('missed_risk', null, note);
  });

  let searchTimer = null;
  document.addEventListener('input', event => {
    if (event.target.id === 'q') {
      clearTimeout(searchTimer);
      const value = event.target.value;
      searchTimer = setTimeout(() => updateListQuery({ q: value.trim() }), 120);
    } else if (event.target.id === 'tree-q') {
      redrawTree();
    }
  });
  document.addEventListener('change', event => {
    if (event.target.id === 'only-findings') redrawTree();
  });

  document.addEventListener('keydown', event => {
    const t = event.target;
    const typing = t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement || t.isContentEditable;
    if (t.id === 'q' && event.key === 'Enter') {
      event.preventDefault();
      const value = t.value.trim();
      const route = parseRoute();
      const rows = visibleJobs(route.params.get('f') || '', value);
      if (rows.length) location.hash = jobHref(rows[0].job_id);
      else if (value) location.hash = jobHref(value);
      return;
    }
    if (event.key === 'Enter' && t.matches?.('tr.ev')) { t.click(); return; }
    if (event.key === 'Escape') {
      if (typing) { t.blur(); return; }
      if (current?.name === 'job') { location.hash = state.lastListHash; }
      return;
    }
    if (typing || event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.key === '/') {
      const box = document.getElementById('q') || document.getElementById('tree-q');
      if (box) { event.preventDefault(); box.focus(); box.select(); }
    }
  });

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && current?.name === 'job' && state.poll) { state.details.delete(current.id); renderJob(current, current); }
  });

  window.addEventListener('hashchange', render);
  if ('scrollRestoration' in history) history.scrollRestoration = 'manual';
  render();
})();
