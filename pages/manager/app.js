const CONFIG_CATEGORIES = [
  { id: 'basic', title: '基础设置', keys: ['enable_friend_request','enable_group_request','auto_approve_group_join','whitelist','whitelist_group'] },
  { id: 'welcome', title: '欢迎 / 退群', keys: ['welcome_text','welcome_image_url','leave_text','leave_image_url'] },
  { id: 'mute', title: '自动禁言', keys: ['enable_auto_mute','mute_keywords','mute_ai_review','mute_ai_prompt','mute_duration','mute_recall','mute_whitelist','mute_reply'] },
  { id: 'blacklist_settings', title: '黑名单策略', keys: ['blacklist_mute_duration','blacklist_mute_reply','enable_admin_commands','blacklist_admin','enable_auto_kick'] },
  { id: 'poke', title: '戳一戳', keys: ['poke_enabled','poke_back_replies','poke_noreply_replies'] },
  { id: 'llm', title: 'LLM 回复过滤', keys: ['llm_filter_rules'] },
];

let bridge = null;
let configData = null;
let groupsList = [];
let friendsList = [];
let groupsListLoading = false;
let friendsListLoading = false;
let currentScope = { type: 'default', id: null };

async function init() {
  bridge = window.AstrBotPluginPage;
  const ctx = await bridge.ready();
  syncTheme(ctx);
  bridge.onContext((newCtx) => syncTheme(newCtx));

  await loadData();
  setupSidebarEvents();
  setupFabBar();
  setupThemeBtn();
  setupBlacklistModal();
  showScope('default', null);
}

function syncTheme(ctx) {
  const isDark = ctx.isDark;
  document.documentElement.setAttribute('data-theme', isDark ? 'dark' : 'light');
  const btn = document.getElementById('theme-btn');
  if (btn) btn.textContent = isDark ? '☀️' : '🌙';
}

function setupThemeBtn() {
  const btn = document.getElementById('theme-btn');
  if (!btn) return;
  const cur = document.documentElement.getAttribute('data-theme');
  btn.textContent = cur === 'dark' ? '☀️' : '🌙';
  btn.addEventListener('click', () => {
    const now = document.documentElement.getAttribute('data-theme') === 'dark';
    document.documentElement.setAttribute('data-theme', now ? 'light' : 'dark');
    btn.textContent = now ? '🌙' : '☀️';
  });
}

async function loadData() {
  try {
    configData = await bridge.apiGet('config');
    render();
  } catch (err) { toast('加载配置失败: ' + err.message, 'error'); }
  loadGroupsList();
  loadFriendsList();
}

async function loadGroupsList() {
  if (groupsListLoading) return;
  groupsListLoading = true;
  try { groupsList = await bridge.apiGet('groups'); } catch (_) {}
  groupsListLoading = false;
  renderSublist('groups');
  renderSublist('blacklist');
}

async function loadFriendsList() {
  if (friendsListLoading) return;
  friendsListLoading = true;
  try { friendsList = await bridge.apiGet('friends'); } catch (_) {}
  friendsListLoading = false;
  renderSublist('friends');
}

function render() {
  if (!configData) return;
  renderDefaultConfigForms();
  renderSublist('groups');
  renderSublist('friends');
  renderSublist('blacklist');
}

function toast(msg, type = 'info', dur = 3000) {
  const el = document.createElement('div');
  el.className = 'toast-msg ' + type;
  el.textContent = msg;
  document.getElementById('toast-container').appendChild(el);
  setTimeout(() => {
    el.style.opacity = '0';
    el.style.transition = 'opacity 0.3s';
    setTimeout(() => el.remove(), 300);
  }, dur);
}

function confirmAction(title, message) {
  return new Promise((resolve) => {
    document.getElementById('confirm-title').textContent = title;
    document.getElementById('confirm-message').textContent = message;
    document.getElementById('confirm-modal').style.display = 'flex';
    const ok = document.getElementById('confirm-ok');
    const cancel = document.getElementById('confirm-cancel');
    const cleanup = () => {
      document.getElementById('confirm-modal').style.display = 'none';
      ok.removeEventListener('click', onOk);
      cancel.removeEventListener('click', onCancel);
    };
    const onOk = () => { cleanup(); resolve(true); };
    const onCancel = () => { cleanup(); resolve(false); };
    ok.addEventListener('click', onOk);
    cancel.addEventListener('click', onCancel);
    document.getElementById('confirm-modal').addEventListener('click', (e) => {
      if (e.target === e.currentTarget) { cleanup(); resolve(false); }
    }, { once: true });
  });
}

// ── Sidebar ──
function setupSidebarEvents() {
  document.querySelectorAll('.nav-group-header').forEach((hdr) => {
    hdr.addEventListener('click', () => toggleAccordion(hdr.dataset.group));
  });
  document.querySelectorAll('.nav-item[data-tab]').forEach((item) => {
    item.addEventListener('click', () => showScope('default', null));
  });
  document.getElementById('sidebar-search').addEventListener('input', () => {
    const q = document.getElementById('sidebar-search').value.trim().toLowerCase();
    filterSidebar(q);
  });
}

function toggleAccordion(group) {
  const sub = document.getElementById('sublist-' + group);
  const hdr = document.querySelector(`.nav-group-header[data-group="${group}"]`);
  const open = sub.classList.contains('open');
  sub.classList.toggle('open', !open);
  hdr.classList.toggle('open', !open);
}

function openAccordion(group) {
  document.getElementById('sublist-' + group).classList.add('open');
  document.querySelector(`.nav-group-header[data-group="${group}"]`).classList.add('open');
}

function filterSidebar(q) {
  document.querySelectorAll('.nav-subitem').forEach((item) => {
    item.classList.toggle('hidden', q && !item.textContent.toLowerCase().includes(q));
  });
  if (q) {
    document.querySelectorAll('.nav-group').forEach((g) => {
      const sub = g.querySelector('.nav-sublist');
      if (!sub) return;
      const has = [...sub.querySelectorAll('.nav-subitem')].some(i => !i.classList.contains('hidden'));
      sub.classList.toggle('open', has);
      g.querySelector('.nav-group-header').classList.toggle('open', has);
    });
  }
}

// ── FAB ──
let fabContext = { type: 'default', id: null };

function setupFabBar() {
  document.getElementById('fab-save').addEventListener('click', onFabSave);
  document.getElementById('fab-clear').addEventListener('click', onFabClear);
  document.getElementById('fab-bl-add').addEventListener('click', onFabBlAdd);
}

function updateFabBar(type, id) {
  fabContext = { type, id };
  const save = document.getElementById('fab-save');
  const clear = document.getElementById('fab-clear');
  const blAdd = document.getElementById('fab-bl-add');
  save.style.display = 'none';
  clear.style.display = 'none';
  blAdd.style.display = 'none';

  const svg = {
    save: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17,21 17,13 7,13 7,21"/><polyline points="7,3 7,8 15,8"/></svg>',
    undo: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><polyline points="1,4 1,10 7,10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>',
    add: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>',
  };

  if (type === 'default') {
    save.style.display = '';
    save.innerHTML = '<span class="fab-label">保存配置</span><span class="fab-icon">' + svg.save + '</span>';
  } else if (type === 'group') {
    save.style.display = '';
    save.innerHTML = '<span class="fab-label">保存配置</span><span class="fab-icon">' + svg.save + '</span>';
    clear.style.display = '';
    clear.innerHTML = '<span class="fab-label">恢复默认配置</span><span class="fab-icon">' + svg.undo + '</span>';
  } else if (type === 'friend') {
    save.style.display = '';
    save.innerHTML = '<span class="fab-label">保存配置</span><span class="fab-icon">' + svg.save + '</span>';
    clear.style.display = '';
    clear.innerHTML = '<span class="fab-label">恢复默认配置</span><span class="fab-icon">' + svg.undo + '</span>';
  } else if (type === 'blacklist') {
    save.style.display = '';
    save.innerHTML = '<span class="fab-label">保存配置</span><span class="fab-icon">' + svg.save + '</span>';
    if (id !== 'global') {
      clear.style.display = '';
      clear.innerHTML = '<span class="fab-label">恢复默认配置</span><span class="fab-icon">' + svg.undo + '</span>';
    }
    blAdd.style.display = '';
    blAdd.innerHTML = '<span class="fab-label">添加黑名单</span><span class="fab-icon">' + svg.add + '</span>';
  }
}

function onFabSave() {
  if (fabContext.type === 'default') saveDefaultConfig();
  else if (fabContext.type === 'group') doSaveGroupConfig(fabContext.id);
  else if (fabContext.type === 'friend') doSaveFriendConfig(fabContext.id);
  else if (fabContext.type === 'blacklist') saveBlacklistPolicy(fabContext.id);
}

function onFabClear() {
  if (fabContext.type === 'group') doClearGroupConfig(fabContext.id);
  else if (fabContext.type === 'friend') doClearFriendConfig(fabContext.id);
  else if (fabContext.type === 'blacklist' && fabContext.id !== 'global') resetGroupBlacklistPolicy(fabContext.id);
}

function onFabBlAdd() {
  if (fabContext.type === 'blacklist' && fabContext.id && fabContext.id !== 'global') {
    openBlModal(fabContext.id);
  } else {
    openBlModal();
  }
}

// ── Sublist rendering ──
function renderSublist(type) {
  if (type === 'groups') renderGroupsSublist();
  else if (type === 'friends') renderFriendsSublist();
  else if (type === 'blacklist') renderBlacklistSublist();
}

function renderGroupsSublist() {
  const container = document.getElementById('sublist-groups');
  const configured = configData?.scoped?.groups || {};
  const cids = Object.keys(configured);
  if (groupsListLoading) { container.innerHTML = '<div class="nav-subitem" style="cursor:default;color:var(--text-muted)">加载中...</div>'; return; }
  if (!groupsList.length && !cids.length) { container.innerHTML = '<div class="nav-subitem" style="cursor:default;color:var(--text-muted)">暂无可配置的群</div>'; return; }
  const nm = {};
  groupsList.forEach(g => { nm[String(g.group_id)] = g.group_name || g.group_id; });
  const all = [...new Set([...groupsList.map(g => String(g.group_id)), ...cids])];
  container.innerHTML = all.map(id =>
    `<button class="nav-subitem${currentScope.type === 'group' && currentScope.id === id ? ' active' : ''}" data-gid="${id}">${nm[id] || id} (${id})${cids.includes(id) ? ' <span class="badge cfg">⚙</span>' : ''}</button>`
  ).join('');
  container.querySelectorAll('.nav-subitem').forEach(btn => btn.addEventListener('click', () => showScope('group', btn.dataset.gid)));
}

function renderFriendsSublist() {
  const container = document.getElementById('sublist-friends');
  const configured = configData?.scoped?.friends || {};
  const cids = Object.keys(configured);
  if (friendsListLoading) { container.innerHTML = '<div class="nav-subitem" style="cursor:default;color:var(--text-muted)">加载中...</div>'; return; }
  if (!friendsList.length && !cids.length) { container.innerHTML = '<div class="nav-subitem" style="cursor:default;color:var(--text-muted)">暂无可配置的好友</div>'; return; }
  const nm = {};
  friendsList.forEach(f => { nm[String(f.user_id)] = f.nickname || f.user_id; });
  const all = [...new Set([...friendsList.map(f => String(f.user_id)), ...cids])];
  container.innerHTML = all.map(id =>
    `<button class="nav-subitem${currentScope.type === 'friend' && currentScope.id === id ? ' active' : ''}" data-fid="${id}">${nm[id] || id} (${id})${cids.includes(id) ? ' <span class="badge cfg">⚙</span>' : ''}</button>`
  ).join('');
  container.querySelectorAll('.nav-subitem').forEach(btn => btn.addEventListener('click', () => showScope('friend', btn.dataset.fid)));
}

function renderBlacklistSublist() {
  const container = document.getElementById('sublist-blacklist');
  const globalBl = configData?.scoped?.global_blacklist || [];
  const groupBl = configData?.scoped?.group_blacklist || {};
  const scopedGroups = configData?.scoped?.groups || {};
  const trackedBl = configData?.scoped?.tracked_groups || [];
  const nm = {};
  groupsList.forEach(g => { nm[String(g.group_id)] = g.group_name || g.group_id; });

  let html = `<button class="nav-subitem${currentScope.type === 'blacklist' && currentScope.id === 'global' ? ' active' : ''}" data-bl="global">全局黑名单 <span class="badge">${globalBl.length}</span></button>`;

  const groupIds = new Set([
    ...Object.keys(groupBl).filter(gid => (groupBl[gid] || []).length > 0),
    ...Object.keys(scopedGroups),
    ...trackedBl,
  ]);
  if (currentScope.type === 'blacklist' && currentScope.id && currentScope.id !== 'global') {
    groupIds.add(currentScope.id);
  }

  groupIds.forEach((gid) => {
    const blCnt = (groupBl[gid] || []).length;
    const hasCfg = Object.keys(scopedGroups[gid] || {}).length > 0;
    const badge = blCnt ? `<span class="badge">${blCnt}</span>` : '';
    const cfgMark = hasCfg && !blCnt ? ' <span class="badge cfg">⚙</span>' : '';
    html += `<button class="nav-subitem${currentScope.type === 'blacklist' && currentScope.id === gid ? ' active' : ''}" data-bl="group" data-gid="${gid}">${nm[gid] || gid} (${gid}) ${badge}${cfgMark}</button>`;
  });

  container.innerHTML = html;
  container.querySelectorAll('.nav-subitem').forEach(btn => {
    if (!btn.dataset.bl) return;
    btn.addEventListener('click', () => showScope('blacklist', btn.dataset.bl === 'group' ? btn.dataset.gid : btn.dataset.bl));
  });
}

// ── Scope switching ──
function showScope(type, id) {
  currentScope = { type, id };
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.querySelectorAll('.nav-subitem').forEach(n => n.classList.remove('active'));
  document.querySelectorAll('.nav-group-header').forEach(n => n.classList.remove('active'));

  if (type === 'default') {
    document.querySelector('.nav-item[data-tab="default"]').classList.add('active');
    switchContent('default');
    updateFabBar('default', null);
    return;
  }

  if (type === 'group') {
    const s = document.querySelector(`.nav-subitem[data-gid="${id}"]`);
    if (s) s.classList.add('active');
    document.querySelector('.nav-group-header[data-group="groups"]').classList.add('active');
    openAccordion('groups');
    showGroupConfig(id);
    switchContent('groups');
    updateFabBar('group', id);
    return;
  }

  if (type === 'friend') {
    const s = document.querySelector(`.nav-subitem[data-fid="${id}"]`);
    if (s) s.classList.add('active');
    document.querySelector('.nav-group-header[data-group="friends"]').classList.add('active');
    openAccordion('friends');
    showFriendConfig(id);
    switchContent('friends');
    updateFabBar('friend', id);
    return;
  }

  if (type === 'blacklist') {
    if (!id) { showScope('default', null); return; }
    const sel = id === 'global' ? '[data-bl="global"]' : `[data-bl="group"][data-gid="${id}"]`;
    const s = document.querySelector(sel);
    if (s) s.classList.add('active');
    document.querySelector('.nav-group-header[data-group="blacklist"]').classList.add('active');
    openAccordion('blacklist');
    renderBlacklistContent(id);
    switchContent('blacklist');
    updateFabBar('blacklist', id);
    // 进入该群黑名单页时，同步跟踪到后端
    if (id !== 'global') {
      bridge.apiPost('config/track_group', { id }).catch(() => {});
    }
    return;
  }
}

function switchContent(tab) {
  document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.id === 'tab-' + tab));
}

// ── Blacklist content ──
function renderBlacklistContent(id) {
  const container = document.getElementById('blacklist-content');
  const isGlobal = id === 'global';
  const items = isGlobal
    ? (configData?.scoped?.global_blacklist || configData?.default?.blacklist || [])
    : (configData?.scoped?.group_blacklist?.[id] || []);

  const label = isGlobal ? '全局黑名单' : `群 ${id} 黑名单`;
  const groupId = isGlobal ? null : id;

  let html = `<div class="config-toolbar"><span class="toolbar-title">${label}</span>`;
  if (!isGlobal) {
    html += '<button class="btn btn-danger" id="btn-clear-group-bl" style="font-size:12px;padding:4px 10px;">清除该群黑名单配置</button>';
  }
  html += '</div>';

  // Blacklist members
  html += `<div class="config-section"><h3>黑名单成员</h3><div class="blacklist-items" id="bl-members">`;
  if (Array.isArray(items) && items.length) {
    items.forEach(uid => {
      html += `<span class="blacklist-tag">${uid}<span class="remove" data-uid="${uid}">×</span></span>`;
    });
  } else {
    html += '<div class="empty-state">暂无</div>';
  }
  html += '</div></div>';

  // Policy config (global or per-group)
  html += `<div class="config-section"><h3>禁言策略${!isGlobal ? '（继承全局，可单独覆盖）' : ''}</h3><div id="bl-policy-fields"></div></div>`;

  container.innerHTML = html;

  // Bind remove handlers
  container.querySelectorAll('.blacklist-tag .remove').forEach(btn => {
    btn.addEventListener('click', async () => {
      const payload = { user_id: btn.dataset.uid, type: isGlobal ? 'global' : 'group' };
      if (!isGlobal) payload.group_id = groupId;
      try {
        await bridge.apiPost('blacklist/remove', payload);
        toast('已移除', 'success');
        await loadData();
        renderBlacklistContent(id);
      } catch (err) { toast('移除失败: ' + err.message, 'error'); }
    });
  });

  // Policy config form
  const policyContainer = document.getElementById('bl-policy-fields');
  const blCat = CONFIG_CATEGORIES.find(c => c.id === 'blacklist_settings');
  if (blCat && configData) {
    if (isGlobal) {
      renderConfigFormFields(policyContainer, configData.default, configData.schema, { includeCats: ['blacklist_settings'] });
    } else {
      const defaults = configData.default;
      const overrides = configData.scoped?.groups?.[groupId] || {};
      const effective = { ...defaults, ...overrides };
      renderConfigFormFields(policyContainer, effective, configData.schema, {
        includeCats: ['blacklist_settings'],
        scopeType: 'group', scopeId: groupId, overrides,
      });
      // 绑定清除按钮
      document.getElementById('btn-clear-group-bl').addEventListener('click', () => fullDeleteGroupConfig(groupId));
    }
  }
}

// ── Blacklist modal ──
function setupBlacklistModal() {
  document.getElementById('bl-modal-close').addEventListener('click', closeBlModal);
  document.getElementById('bl-modal-cancel').addEventListener('click', closeBlModal);
  document.getElementById('bl-modal-confirm').addEventListener('click', doBlModalAdd);
  document.getElementById('bl-modal').addEventListener('click', e => { if (e.target === e.currentTarget) closeBlModal(); });
  document.getElementById('bl-modal-user-id').addEventListener('keydown', e => { if (e.key === 'Enter') doBlModalAdd(); });
  document.querySelectorAll('#bl-modal-scope .radio-item').forEach(item => {
    item.addEventListener('click', () => {
      document.querySelectorAll('#bl-modal-scope .radio-item').forEach(r => r.classList.remove('active'));
      item.classList.add('active');
      const show = item.dataset.value === 'group' && groupsList.length > 0;
      document.getElementById('bl-modal-group-id').style.display = show ? '' : 'none';
      document.getElementById('bl-modal-group-label').style.display = show ? '' : 'none';
      document.getElementById('bl-member-list').innerHTML = '';
    });
  });
  let memberTimer = null;
  document.getElementById('bl-modal-group-id').addEventListener('input', () => {
    clearTimeout(memberTimer);
    const gid = document.getElementById('bl-modal-group-id').value.replace(/\s*\-.*$/, '').trim();
    if (!gid) { document.getElementById('bl-member-list').innerHTML = ''; return; }
    memberTimer = setTimeout(async () => {
      try {
        const members = await bridge.apiGet('groups/' + gid + '/members');
        const dl = document.getElementById('bl-member-list');
        dl.innerHTML = '';
        (members || []).forEach(m => {
          const o = document.createElement('option');
          o.value = String(m.user_id) + ' - ' + (m.nickname || '');
          dl.appendChild(o);
        });
      } catch (_) {}
    }, 500);
  });
}

function openBlModal(groupId) {
  document.getElementById('bl-modal').style.display = 'flex';
  document.getElementById('bl-modal-user-id').focus();
  const dl = document.getElementById('bl-group-list');
  dl.innerHTML = '';
  groupsList.forEach(g => { const o = document.createElement('option'); o.value = String(g.group_id) + ' - ' + g.group_name; dl.appendChild(o); });

  const scopeRadios = document.querySelectorAll('#bl-modal-scope .radio-item');
  const groupInput = document.getElementById('bl-modal-group-id');
  const groupLabel = document.getElementById('bl-modal-group-label');

  // 先重置
  scopeRadios.forEach(r => { r.classList.remove('active'); r.style.display = ''; });
  groupInput.style.display = 'none';
  groupLabel.style.display = 'none';
  if (groupId) {
    // 默认选中"群黑名单"并填入当前群，但允许用户切换类型或改群号
    document.querySelector('#bl-modal-scope .radio-item[data-value="group"]').classList.add('active');
    groupInput.style.display = '';
    groupLabel.style.display = '';
    const found = groupsList.find(g => String(g.group_id) === groupId);
    if (found) groupInput.value = String(found.group_id) + ' - ' + found.group_name;
    else groupInput.value = groupId;
    // 触发群成员加载
    groupInput.dispatchEvent(new Event('input', { bubbles: true }));
  } else {
    document.querySelector('#bl-modal-scope .radio-item[data-value="global"]').classList.add('active');
  }
}

function closeBlModal() { document.getElementById('bl-modal').style.display = 'none'; }

function resetBlModalFields() {
  document.getElementById('bl-modal-user-id').value = '';
  document.getElementById('bl-modal-group-id').value = '';
  document.getElementById('bl-member-list').innerHTML = '';
  document.querySelectorAll('#bl-modal-scope .radio-item').forEach(r => { r.classList.remove('active'); r.style.display = ''; });
  document.querySelector('#bl-modal-scope .radio-item[data-value="global"]').classList.add('active');
  document.getElementById('bl-modal-group-id').style.display = 'none';
  document.getElementById('bl-modal-group-label').style.display = 'none';
}

async function doBlModalAdd() {
  const raw = document.getElementById('bl-modal-user-id').value.trim();
  if (!raw) return toast('请输入用户 QQ 号', 'error');
  const uid = raw.replace(/\s*\-.*$/, '').trim();
  const active = document.querySelector('#bl-modal-scope .radio-item.active');
  const scope = active ? active.dataset.value : 'global';
  const payload = { user_id: uid, type: scope };
  if (scope === 'group') {
    const r = document.getElementById('bl-modal-group-id').value.trim();
    if (!r) return toast('请输入群号', 'error');
    payload.group_id = r.replace(/\s*\-.*$/, '').trim();
  }
  try {
    await bridge.apiPost('blacklist/add', payload);
    toast('已添加黑名单', 'success');
    resetBlModalFields();
    closeBlModal();
    await loadData();
    if (currentScope.type === 'blacklist') renderBlacklistContent(currentScope.id);
  } catch (err) { toast('添加失败: ' + err.message, 'error'); }
}

// ── Form rendering helpers ──
function renderConfigFormFields(container, values, schema, options = {}) {
  const { scopeType = null, scopeId = null, overrides = null, excludeKeys = [], includeCats = null, excludeCats = [] } = options;
  CONFIG_CATEGORIES.forEach(cat => {
    if (includeCats && !includeCats.includes(cat.id)) return;
    if (excludeCats.includes(cat.id)) return;
    const sec = document.createElement('div');
    sec.className = 'config-section';
    sec.innerHTML = `<h3>${cat.title}</h3>`;
    const wrap = document.createElement('div');
    cat.keys.forEach(key => {
      if (excludeKeys.includes(key)) return;
      const sch = schema[key];
      if (!sch) return;
      const val = values?.[key] !== undefined ? values[key] : sch.default;
      const overVal = overrides?.[key];
      const hasOver = scopeType && overrides && key in overrides;
      const item = document.createElement('div');
      item.className = 'config-item';
      item.dataset.key = key;
      const info = document.createElement('div');
      info.className = 'config-info';
      info.innerHTML = `<span class="config-label">${sch.description || key}</span><span class="config-hint">${sch.hint || ''}</span>`;
      const ctrl = document.createElement('div');
      ctrl.className = 'config-control';
      const inp = createInput(key, sch, hasOver ? overVal : val);
      ctrl.appendChild(inp);
      if (hasOver && scopeType) {
        const rb = document.createElement('button');
        rb.className = 'btn btn-outline-danger';
        rb.textContent = '恢复默认';
        rb.addEventListener('click', () => {
          const n = { ...overrides };
          delete n[key];
          saveScopeOverrides(scopeType, scopeId, n);
        });
        ctrl.appendChild(rb);
      }
      item.appendChild(info);
      item.appendChild(ctrl);
      wrap.appendChild(item);
    });
    sec.appendChild(wrap);
    container.appendChild(sec);
  });
}

function createInput(key, sch, value) {
  if (sch.type === 'bool') {
    const wrap = document.createElement('div');
    wrap.className = 'toggle-wrap input-toggle';
    const div = document.createElement('div');
    div.className = 'toggle' + (value ? ' active' : '');
    const inp = document.createElement('input');
    inp.type = 'checkbox'; inp.hidden = true;
    inp.checked = !!value; inp.dataset.key = key;
    function ut() { if (inp.disabled) return; inp.checked = !inp.checked; div.classList.toggle('active', inp.checked); const s = wrap.querySelector('.toggle-status'); if (s) s.textContent = inp.checked ? '已启用' : '已禁用'; }
    div.addEventListener('click', ut);
    wrap.appendChild(div);
    wrap.appendChild(inp);
    const ss = document.createElement('span');
    ss.className = 'toggle-status';
    ss.style.cssText = 'font-size:12px;color:var(--text-secondary)';
    ss.textContent = value ? '已启用' : '已禁用';
    wrap.appendChild(ss);
    return wrap;
  }
  if (sch.type === 'int') { const i = document.createElement('input'); i.type = 'number'; i.className = 'input'; i.value = value ?? 0; i.min = 0; i.dataset.key = key; return i; }
  if (sch.type === 'list') { const ta = document.createElement('textarea'); ta.className = 'input'; ta.rows = 3; ta.dataset.key = key; ta.placeholder = '每行一个项目'; if (Array.isArray(value) && value.length > 0) ta.value = value.join('\n'); return ta; }
  const isLong = key.endsWith('_prompt') || key.endsWith('_text') || key.endsWith('_reply');
  if (isLong) { const ta = document.createElement('textarea'); ta.className = 'input'; ta.rows = 3; ta.dataset.key = key; ta.value = value ?? ''; return ta; }
  const i = document.createElement('input'); i.type = 'text'; i.className = 'input'; i.value = value ?? ''; i.dataset.key = key; return i;
}

function collectFormValues(container) {
  const values = {};
  container.querySelectorAll('[data-key]').forEach(el => {
    const key = el.dataset.key;
    if (el.type === 'checkbox') values[key] = el.checked;
    else if (el.type === 'number') values[key] = parseInt(el.value, 10) || 0;
    else if (el.tagName === 'TEXTAREA' || el.type === 'text') values[key] = el.value;
  });
  for (const k of Object.keys(values)) {
    const sch = getSchema(k);
    if (sch?.type === 'list') values[k] = values[k].split('\n').map(s => s.trim()).filter(Boolean);
  }
  return values;
}

function collectScopeFormValues(container) { return collectFormValues(container); }

function getSchema(key) { return configData?.schema?.[key] || null; }

async function saveScopeOverrides(scopeType, scopeId, overrides) {
  try {
    await bridge.apiPost('config/' + scopeType + '/' + scopeId, overrides);
    await loadData();
    toast('配置已更新', 'success');
  } catch (err) { toast('保存失败: ' + err.message, 'error'); }
}

async function saveBlacklistPolicy(id) {
  const isGlobal = id === 'global';
  const container = document.getElementById('bl-policy-fields');
  if (!container) return;
  if (isGlobal) {
    const vals = collectFormValues(container);
    try {
      await bridge.apiPost('config/default', vals);
      toast('全局禁言策略已保存', 'success');
      await loadData();
      renderBlacklistContent(id);
    } catch (e) { toast('保存失败: ' + e.message, 'error'); }
  } else {
    const vals = collectScopeFormValues(container);
    try {
      await bridge.apiPost('config/group/' + id, vals);
      toast('群禁言策略已保存', 'success');
      await loadData();
      renderBlacklistContent(id);
    } catch (e) { toast('保存失败: ' + e.message, 'error'); }
  }
}

async function resetGroupBlacklistPolicy(id) {
  if (!await confirmAction('重置黑名单策略', '确定要重置群聊 ' + id + ' 的黑名单策略吗？恢复后将跟随全局禁言策略。')) return;
  try {
    await bridge.apiPost('config/group/' + id + '/reset_policy', {});
    toast('黑名单策略已重置', 'info');
    await loadData();
    renderSublist('blacklist');
    renderBlacklistContent(id);
  } catch (e) { toast('重置失败: ' + e.message, 'error'); }
}

async function fullDeleteGroupConfig(id) {
  if (!await confirmAction('删除群聊配置', '确定要删除群聊 ' + id + ' 的所有配置吗？该群将从侧栏移除。')) return;
  try {
    // 从后端跟踪列表中移除
    try { await bridge.apiPost('config/untrack_group', { id: id }); } catch (_) {}
    // 删除配置覆盖
    await bridge.apiPost('config/group/' + id + '/delete', {});
    // 清空该群的黑名单成员
    const members = configData?.scoped?.group_blacklist?.[id] || [];
    for (const uid of members) {
      try { await bridge.apiPost('blacklist/remove', { user_id: uid, type: 'group', group_id: id }); } catch (_) {}
    }
    toast('群聊配置及黑名单已删除', 'info');
    await loadData();
    showScope('blacklist', 'global');
  } catch (e) { toast('删除失败: ' + e.message, 'error'); }
}

// ── Default config ──
function renderDefaultConfigForms() {
  const c = document.getElementById('default-config-forms');
  c.innerHTML = '';
  if (!configData) return;
  renderConfigFormFields(c, configData.default, configData.schema, { excludeCats: ['blacklist_settings'] });
}

async function saveDefaultConfig() {
  const vals = collectFormValues(document.getElementById('default-config-forms'));
  try {
    await bridge.apiPost('config/default', vals);
    toast('默认配置已保存', 'success');
    await loadData();
  } catch (err) { toast('保存失败: ' + err.message, 'error'); }
}

// ── Group config ──
async function showGroupConfig(gid) {
  const overrides = configData.scoped?.groups?.[gid] || {};
  const effective = { ...configData.default, ...overrides };
  document.getElementById('group-config-title').textContent = `群 ${gid} 的配置`;
  document.getElementById('group-config-form').style.display = 'block';
  document.getElementById('group-config-empty').style.display = 'none';
  const wlGroup = (configData.default?.whitelist_group || []).map(String);
  const enabled = wlGroup.length === 0 || wlGroup.includes(String(gid));
  const wrap = document.getElementById('group-enable-wrap');
  const toggle = document.getElementById('group-enable-toggle');
  const label = document.getElementById('group-enable-label');
  wrap.style.display = 'inline-flex';
  toggle.classList.toggle('active', enabled);
  label.textContent = enabled ? '已启用' : '已禁用';
  const nt = toggle.cloneNode(true);
  toggle.parentNode.replaceChild(nt, toggle);
  nt.addEventListener('click', async () => {
    const now = nt.classList.contains('active');
    const payload = { scope: 'group', id: gid, enabled: !now };
    if (now && wlGroup.length === 0 && groupsList.length > 0) payload.all_ids = groupsList.map(g => String(g.group_id));
    try {
      const res = await bridge.apiPost('whitelist/toggle', payload);
      const ns = (res && res.enabled !== undefined) ? res.enabled : !now;
      nt.classList.toggle('active', ns);
      document.getElementById('group-enable-label').textContent = ns ? '已启用' : '已禁用';
      toast(ns ? '群已加入白名单' : '群已移出白名单', 'success');
      await loadData();
      const fw = (configData.default?.whitelist_group || []).map(String);
      const fe = fw.length === 0 || fw.includes(gid);
      nt.classList.toggle('active', fe);
      document.getElementById('group-enable-label').textContent = fe ? '已启用' : '已禁用';
    } catch (err) { toast('操作失败: ' + err.message, 'error'); }
  });
  const container = document.getElementById('group-config-fields');
  container.innerHTML = '';
  renderConfigFormFields(container, effective, configData.schema, {
    scopeType: 'group', scopeId: gid, overrides, excludeKeys: ['whitelist_group', 'whitelist'],
  });
}

async function doSaveGroupConfig(gid) {
  const vals = collectScopeFormValues(document.getElementById('group-config-fields'));
  try { await bridge.apiPost('config/group/' + gid, vals); toast('群配置已保存', 'success'); await loadData(); showGroupConfig(gid); } catch (e) { toast('保存失败: ' + e.message, 'error'); }
}

async function doClearGroupConfig(gid) {
  if (!await confirmAction('重置群配置', '确定要重置群聊 ' + gid + ' 的所有配置吗？恢复后该群将完全跟随默认配置。')) return;
  try { await bridge.apiPost('config/group/' + gid, {}); toast('群配置已清除', 'info'); await loadData(); renderSublist('groups'); showGroupConfig(gid); } catch (e) { toast('清除失败: ' + e.message, 'error'); }
}

// ── Friend config ──
async function showFriendConfig(fid) {
  const overrides = configData.scoped?.friends?.[fid] || {};
  const effective = { ...configData.default, ...overrides };
  document.getElementById('friend-config-title').textContent = `好友 ${fid} 的配置`;
  document.getElementById('friend-config-form').style.display = 'block';
  document.getElementById('friend-config-empty').style.display = 'none';
  const wl = (configData.default?.whitelist || []).map(String);
  const enabled = wl.length === 0 || wl.includes(String(fid));
  const wrap = document.getElementById('friend-enable-wrap');
  const toggle = document.getElementById('friend-enable-toggle');
  const label = document.getElementById('friend-enable-label');
  wrap.style.display = 'inline-flex';
  toggle.classList.toggle('active', enabled);
  label.textContent = enabled ? '已启用' : '已禁用';
  const nt = toggle.cloneNode(true);
  toggle.parentNode.replaceChild(nt, toggle);
  nt.addEventListener('click', async () => {
    const now = nt.classList.contains('active');
    const payload = { scope: 'friend', id: fid, enabled: !now };
    if (now && wl.length === 0 && friendsList.length > 0) payload.all_ids = friendsList.map(f => String(f.user_id));
    try {
      const res = await bridge.apiPost('whitelist/toggle', payload);
      const ns = (res && res.enabled !== undefined) ? res.enabled : !now;
      nt.classList.toggle('active', ns);
      document.getElementById('friend-enable-label').textContent = ns ? '已启用' : '已禁用';
      toast(ns ? '好友已加入白名单' : '好友已移出白名单', 'success');
      await loadData();
      const fw = (configData.default?.whitelist || []).map(String);
      const fe = fw.length === 0 || fw.includes(fid);
      nt.classList.toggle('active', fe);
      document.getElementById('friend-enable-label').textContent = fe ? '已启用' : '已禁用';
    } catch (err) { toast('操作失败: ' + err.message, 'error'); }
  });
  const container = document.getElementById('friend-config-fields');
  container.innerHTML = '';
  renderConfigFormFields(container, effective, configData.schema, { scopeType: 'friend', scopeId: fid, overrides, includeCats: ['poke', 'llm'] });
}

async function doSaveFriendConfig(fid) {
  const vals = collectScopeFormValues(document.getElementById('friend-config-fields'));
  try { await bridge.apiPost('config/friend/' + fid, vals); toast('好友配置已保存', 'success'); await loadData(); showFriendConfig(fid); } catch (e) { toast('保存失败: ' + e.message, 'error'); }
}

async function doClearFriendConfig(fid) {
  if (!await confirmAction('清除好友配置', '确定要清除好友 ' + fid + ' 的所有配置覆盖吗？恢复后该好友将完全跟随默认配置。')) return;
  try { await bridge.apiPost('config/friend/' + fid, {}); toast('好友配置已清除', 'info'); await loadData(); renderSublist('friends'); showFriendConfig(fid); } catch (e) { toast('清除失败: ' + e.message, 'error'); }
}

document.addEventListener('DOMContentLoaded', init);
