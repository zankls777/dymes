/**
 * 用户与权限管理 - 前端逻辑模块
 *
 * 提供：
 *   1. 用户列表：分页 / 排序 / 多条件筛选（fetch /api/user-mgmt/list）
 *   2. 多选与批量权限入口联动
 *   3. Excel 批量导入（模板下载 / 上传 / 逐行结果反馈）
 *   4. 批量权限管理（批量分配权限组 + 用户级权限覆盖）
 *
 * 依赖：Bootstrap 5（bootstrap.Modal）、jQuery（$.notify 可选，降级为 alert）
 * 兼容：现代浏览器 + IE 不保证（项目其余页面同）
 */
(function () {
    'use strict';

    var API_BASE = '/api/user-mgmt';

    // 全局状态
    var state = {
        page: 1,
        size: 20,
        sortBy: 'id',
        sortDir: 'asc',
        keyword: '',
        department: '',
        group: '',
        status: '',
        isAdmin: '',
        isOperator: '',
        total: 0,
        pages: 0,
        selected: {},          // {userId: true}
        rows: [],              // 当前页数据
        meta: { departments: [], groups: [], permission_tree: [] }
    };

    // ------------------------------------------------------------------
    // 工具
    // ------------------------------------------------------------------
    function $(id) { return document.getElementById(id); }

    function esc(s) {
        if (s === null || s === undefined) { return ''; }
        return String(s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function notify(message, ok) {
        var alert = document.createElement('div');
        alert.className = 'alert ' + (ok === false ? 'alert-danger' : 'alert-success') +
            ' alert-dismissible fade show';
        alert.setAttribute('role', 'alert');
        alert.innerHTML = esc(message) +
            '<button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>';
        document.body.prepend(alert);
        setTimeout(function () {
            if (alert.parentNode) { alert.parentNode.removeChild(alert); }
        }, 4000);
    }

    function fetchJSON(url, options) {
        return fetch(url, options).then(function (resp) {
            return resp.json().then(function (data) {
                if (!resp.ok || data.success === false) {
                    throw new Error((data && data.error) || ('请求失败(' + resp.status + ')'));
                }
                return data;
            });
        });
    }

    // ------------------------------------------------------------------
    // 列表：加载 / 渲染
    // ------------------------------------------------------------------
    function buildQuery() {
        var p = new URLSearchParams();
        p.set('page', state.page);
        p.set('size', state.size);
        p.set('sort_by', state.sortBy);
        p.set('sort_dir', state.sortDir);
        if (state.keyword) { p.set('keyword', state.keyword); }
        if (state.department) { p.set('department', state.department); }
        if (state.group) { p.set('group', state.group); }
        if (state.status) { p.set('status', state.status); }
        if (state.isAdmin !== '') { p.set('is_admin', state.isAdmin); }
        if (state.isOperator !== '') { p.set('is_operator', state.isOperator); }
        return p.toString();
    }

    function loadList() {
        var tbody = $('userTableBody');
        if (tbody) {
            tbody.innerHTML = '<tr><td colspan="9" class="text-center text-muted py-4">' +
                '<span class="spinner-border spinner-border-sm mr-2"></span>加载中...</td></tr>';
        }
        return fetchJSON(API_BASE + '/list?' + buildQuery())
            .then(function (data) {
                state.rows = data.data || [];
                state.total = data.total || 0;
                state.page = data.page || 1;
                state.pages = data.pages || 0;
                renderTable();
                renderPagination();
                syncSelectionAfterLoad();
            })
            .catch(function (err) {
                if (tbody) {
                    tbody.innerHTML = '<tr><td colspan="9" class="text-center text-danger py-4">' +
                        '加载失败：' + esc(err.message) + '</td></tr>';
                }
            });
    }

    function renderTable() {
        var tbody = $('userTableBody');
        if (!tbody) { return; }
        if (!state.rows.length) {
            tbody.innerHTML = '<tr><td colspan="9" class="text-center text-muted py-4">' +
                '<i class="fa fa-inbox mr-2"></i>暂无符合条件的用户</td></tr>';
            return;
        }

        var canManagePwd = window.UM_CAN_MANAGE_PERM !== false;
        var canAdd = window.UM_CAN_ADD_USER !== false;
        var selfId = window.UM_SELF_ID;

        var html = state.rows.map(function (u) {
            var checked = state.selected[u.id] ? ' checked' : '';
            var disabled = u.is_admin ? ' disabled title="管理员无需单独授权"' : '';
            var statusBadge = u.is_active
                ? '<span class="badge bg-success">启用</span>'
                : '<span class="badge bg-secondary">已禁用</span>';
            var adminBadge = u.is_admin
                ? '<span class="badge bg-warning text-dark">管理员</span>'
                : '<span class="text-muted">-</span>';
            // 作业者标记：勾选的账号才会出现在生产入库的作业者下拉中
            var operatorBadge = u.is_operator
                ? '<span class="badge bg-primary">作业者</span>'
                : '<span class="text-muted">-</span>';

            var groups = (u.group_names && u.group_names.length)
                ? u.group_names.map(function (g) {
                    return '<span class="badge bg-info text-dark mr-1">' + esc(g) + '</span>';
                }).join('')
                : '<span class="text-muted">未分配</span>';

            var permBadge = '<span class="badge bg-light text-dark border" title="有效权限数量">' +
                (u.permission_count || 0) + '</span>';

            var selfTag = u.is_self ? ' <span class="badge bg-primary">我</span>' : '';

            var actions = [];
            if (canAdd) {
                actions.push(btn('changePasswordModal', u, 'fa-key', '修改密码', 'btn-outline-secondary'));
                actions.push(btn('resetPasswordModal', u, 'fa-refresh', '重置密码', 'btn-outline-warning'));
            }
            if (canManagePwd) {
                actions.push(btn('permissionManagementModal', u, 'fa-shield', '权限管理', 'btn-outline-info'));
                actions.push(btn('copyPermissionModal', u, 'fa-copy', '复用权限', 'btn-outline-primary'));
            }
            actions.push(btn('toggleUserStatusModal', u, 'fa-power-off',
                u.is_active ? '禁用' : '启用', 'btn-outline-dark', { 'data-active': u.is_active ? '1' : '0' }));
            // 作业者标记开关：切换是否为作业者（影响生产入库作业者下拉）
            if (canAdd) {
                actions.push('<button type="button" class="btn ' +
                    (u.is_operator ? 'btn-primary' : 'btn-outline-primary') +
                    '" title="' + (u.is_operator ? '取消作业者标记' : '设为作业者') +
                    '" data-toggle-operator="' + u.id + '" data-operator-val="' +
                    (u.is_operator ? '0' : '1') + '"><i class="fa fa-user"></i></button>');
            }
            if (canAdd) {
                actions.push(btn('deleteUserModal', u, 'fa-trash', '删除', 'btn-outline-danger'));
            }

            return '<tr data-id="' + u.id + '">' +
                '<td><input type="checkbox" class="form-check-input row-select" value="' + u.id + '"' +
                checked + disabled + '></td>' +
                '<td>' + u.id + '</td>' +
                '<td><strong>' + esc(u.username) + '</strong>' + selfTag + '</td>' +
                '<td>' + groups + '</td>' +
                '<td>' + adminBadge + '</td>' +
                '<td>' + operatorBadge + '</td>' +
                '<td>' + statusBadge + '</td>' +
                '<td>' + permBadge + '</td>' +
                '<td><div class="btn-group btn-group-sm" role="group">' + actions.join('') + '</div></td>' +
                '</tr>';
        }).join('');

        tbody.innerHTML = html;
        bindRowCheckboxes();
        bindOperatorToggle();
    }

    function btn(target, u, icon, title, cls, extra) {
        var attrs = ' data-bs-toggle="modal" data-bs-target="#' + target + '"' +
            ' data-user-id="' + u.id + '" data-username="' + esc(u.username) + '"';
        if (extra) {
            Object.keys(extra).forEach(function (k) {
                attrs += ' ' + k + '="' + esc(extra[k]) + '"';
            });
        }
        return '<button type="button" class="btn ' + cls + '" title="' + title + '"' + attrs + '>' +
            '<i class="fa ' + icon + '"></i></button>';
    }

    // ------------------------------------------------------------------
    // 分页
    // ------------------------------------------------------------------
    function renderPagination() {
        var info = $('paginationInfo');
        var ul = $('pagination');
        if (!ul) { return; }
        if (info) {
            var from = state.total ? (state.page - 1) * state.size + 1 : 0;
            var to = Math.min(state.page * state.size, state.total);
            info.textContent = '共 ' + state.total + ' 条，当前显示 ' + from + '-' + to + ' 条';
        }
        if (state.pages <= 1) { ul.innerHTML = ''; return; }

        var items = [];
        items.push(pageItem(state.page - 1, '&laquo;', state.page <= 1));

        var start = Math.max(1, state.page - 2);
        var end = Math.min(state.pages, start + 4);
        start = Math.max(1, end - 4);
        if (start > 1) {
            items.push(pageItem(1, '1', false));
            if (start > 2) { items.push('<li class="page-item disabled"><span class="page-link">…</span></li>'); }
        }
        for (var i = start; i <= end; i++) {
            items.push(pageItem(i, String(i), false, i === state.page));
        }
        if (end < state.pages) {
            if (end < state.pages - 1) { items.push('<li class="page-item disabled"><span class="page-link">…</span></li>'); }
            items.push(pageItem(state.pages, String(state.pages), false));
        }
        items.push(pageItem(state.page + 1, '&raquo;', state.page >= state.pages));

        ul.innerHTML = items.join('');
        Array.prototype.forEach.call(ul.querySelectorAll('a.page-link'), function (a) {
            a.addEventListener('click', function (e) {
                e.preventDefault();
                var p = parseInt(a.getAttribute('data-page'), 10);
                if (p && p !== state.page) { state.page = p; loadList(); }
            });
        });
    }

    function pageItem(page, label, disabled, active) {
        if (disabled) {
            return '<li class="page-item disabled"><span class="page-link">' + label + '</span></li>';
        }
        return '<li class="page-item' + (active ? ' active' : '') + '">' +
            '<a class="page-link" href="#" data-page="' + page + '">' + label + '</a></li>';
    }

    // ------------------------------------------------------------------
    // 多选
    // ------------------------------------------------------------------
    function bindRowCheckboxes() {
        Array.prototype.forEach.call(document.querySelectorAll('.row-select'), function (cb) {
            cb.addEventListener('change', function () {
                var id = cb.value;
                if (cb.checked) { state.selected[id] = true; } else { delete state.selected[id]; }
                updateSelectionUI();
            });
        });
    }

    // 作业者标记开关（事件委托，绑定一次即可，适配行重绘）
    function bindOperatorToggle() {
        var tbody = $('userTableBody');
        if (!tbody || tbody.dataset.opBound === '1') { return; }
        tbody.dataset.opBound = '1';
        tbody.addEventListener('click', function (e) {
            var btn = e.target.closest('[data-toggle-operator]');
            if (!btn) { return; }
            var uid = btn.getAttribute('data-toggle-operator');
            var val = btn.getAttribute('data-operator-val') === '1';
            btn.disabled = true;
            fetchJSON(API_BASE + '/' + uid + '/profile', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ is_operator: val })
            }).then(function () {
                notify(val ? '已设为作业者' : '已取消作业者标记', true);
                loadList();
            }).catch(function (err) {
                notify('操作失败：' + err.message, false);
                btn.disabled = false;
            });
        });
    }

    function syncSelectionAfterLoad() {
        var selectAll = $('selectAllUsers');
        if (selectAll) { selectAll.checked = false; }
        updateSelectionUI();
    }

    function selectedIds() {
        return Object.keys(state.selected).map(function (k) { return parseInt(k, 10); });
    }

    function updateSelectionUI() {
        var ids = selectedIds();
        var countEl = $('batchSelectedCount');
        if (countEl) { countEl.textContent = String(ids.length); }
        var btn = $('batchPermissionBtn');
        if (btn) { btn.disabled = ids.length === 0; }

        // 全选状态
        var selectAll = $('selectAllUsers');
        if (selectAll) {
            var boxes = document.querySelectorAll('.row-select:not(:disabled)');
            var checked = document.querySelectorAll('.row-select:not(:disabled):checked');
            selectAll.checked = boxes.length > 0 && boxes.length === checked.length;
            selectAll.indeterminate = checked.length > 0 && checked.length < boxes.length;
        }
    }

    function bindSelectAll() {
        var selectAll = $('selectAllUsers');
        if (!selectAll) { return; }
        selectAll.addEventListener('change', function () {
            var on = selectAll.checked;
            Array.prototype.forEach.call(document.querySelectorAll('.row-select:not(:disabled)'), function (cb) {
                cb.checked = on;
                if (on) { state.selected[cb.value] = true; } else { delete state.selected[cb.value]; }
            });
            updateSelectionUI();
        });
    }

    // ------------------------------------------------------------------
    // 筛选 / 排序
    // ------------------------------------------------------------------
    function bindFilters() {
        var applyBtn = $('applyFilterBtn');
        if (applyBtn) {
            applyBtn.addEventListener('click', function () {
                state.keyword = ($('filterKeyword') || {}).value || '';
                state.department = ($('filterDepartment') || {}).value || '';
                state.group = ($('filterRole') || {}).value || '';
                state.status = ($('filterStatus') || {}).value || '';
                state.isAdmin = ($('filterIsAdmin') || {}).value || '';
                state.isOperator = ($('filterIsOperator') || {}).value || '';
                state.page = 1;
                loadList();
            });
        }
        var resetBtn = $('resetFilterBtn');
        if (resetBtn) {
            resetBtn.addEventListener('click', function () {
                ['filterKeyword', 'filterDepartment', 'filterRole', 'filterStatus', 'filterIsAdmin', 'filterIsOperator']
                    .forEach(function (id) { if ($(id)) { $(id).value = ''; } });
                state.keyword = state.department = state.group = state.status = state.isAdmin = state.isOperator = '';
                state.page = 1;
                loadList();
            });
        }
        var kw = $('filterKeyword');
        if (kw) {
            kw.addEventListener('keydown', function (e) {
                if (e.key === 'Enter') { applyBtn && applyBtn.click(); }
            });
        }
    }

    function bindSort() {
        Array.prototype.forEach.call(document.querySelectorAll('th.sortable'), function (th) {
            th.style.cursor = 'pointer';
            th.addEventListener('click', function () {
                var field = th.getAttribute('data-sort');
                if (state.sortBy === field) {
                    state.sortDir = state.sortDir === 'asc' ? 'desc' : 'asc';
                } else {
                    state.sortBy = field;
                    state.sortDir = 'asc';
                }
                updateSortIcons();
                state.page = 1;
                loadList();
            });
        });
    }

    function updateSortIcons() {
        Array.prototype.forEach.call(document.querySelectorAll('th.sortable'), function (th) {
            var icon = th.querySelector('i');
            if (!icon) { return; }
            if (th.getAttribute('data-sort') === state.sortBy) {
                icon.className = 'fa ' + (state.sortDir === 'asc' ? 'fa-sort-asc' : 'fa-sort-desc');
            } else {
                icon.className = 'fa fa-sort text-muted';
            }
        });
    }

    // ------------------------------------------------------------------
    // 元数据
    // ------------------------------------------------------------------
    function loadMeta() {
        return fetchJSON(API_BASE + '/meta').then(function (data) {
            state.meta.departments = data.departments || [];
            state.meta.groups = data.groups || [];
            state.meta.permission_tree = data.permission_tree || [];
            fillFilterOptions();
            fillBatchRoleOptions();
        }).catch(function () { /* 元数据失败不阻塞列表 */ });
    }

    function fillFilterOptions() {
        var depSel = $('filterDepartment');
        if (depSel) {
            state.meta.departments.forEach(function (d) {
                var o = document.createElement('option');
                o.value = d; o.textContent = d;
                depSel.appendChild(o);
            });
        }
        var roleSel = $('filterRole');
        if (roleSel) {
            state.meta.groups.forEach(function (g) {
                var o = document.createElement('option');
                o.value = g.id; o.textContent = g.name;
                roleSel.appendChild(o);
            });
        }
    }

    function fillBatchRoleOptions() {
        var box = $('batchRoleList');
        if (!box) { return; }
        box.innerHTML = state.meta.groups.map(function (g) {
            return '<div class="form-check">' +
                '<input class="form-check-input" type="checkbox" id="batch_role_' + g.id + '" value="' + g.id + '">' +
                '<label class="form-check-label" for="batch_role_' + g.id + '">' + esc(g.name) +
                (g.code ? ' <small class="text-muted">(' + esc(g.code) + ')</small>' : '') +
                '</label></div>';
        }).join('') || '<div class="text-muted small">暂无权限组</div>';
    }

    // ------------------------------------------------------------------
    // Excel 批量导入
    // ------------------------------------------------------------------
    function bindImport() {
        var fileInput = $('importFile');
        var uploadBtn = $('importUploadBtn');
        if (uploadBtn) {
            uploadBtn.addEventListener('click', doImport);
        }
        if (fileInput) {
            fileInput.addEventListener('change', function () {
                var name = fileInput.files.length ? fileInput.files[0].name : '';
                if ($('importFileName')) { $('importFileName').textContent = name || '未选择文件'; }
            });
        }
        var modalEl = $('importUserModal');
        if (modalEl) {
            modalEl.addEventListener('hidden.bs.modal', function () {
                if ($('importResult')) { $('importResult').style.display = 'none'; }
                if ($('importResultBody')) { $('importResultBody').innerHTML = ''; }
                if ($('importSummary')) { $('importSummary').innerHTML = ''; }
                if (fileInput) { fileInput.value = ''; }
                if ($('importFileName')) { $('importFileName').textContent = '未选择文件'; }
            });
        }
    }

    function doImport() {
        var fileInput = $('importFile');
        if (!fileInput || !fileInput.files.length) {
            notify('请先选择要导入的 Excel 文件', false);
            return;
        }
        var fd = new FormData();
        fd.append('file', fileInput.files[0]);

        var loader = $('importLoader');
        if (loader) { loader.style.display = 'inline-block'; }
        var uploadBtn = $('importUploadBtn');
        if (uploadBtn) { uploadBtn.disabled = true; }

        fetch(API_BASE + '/import', { method: 'POST', body: fd })
            .then(function (resp) {
                return resp.json().then(function (data) {
                    if (!resp.ok || data.success === false) {
                        throw new Error((data && data.error) || '导入失败');
                    }
                    return data;
                });
            })
            .then(function (data) {
                renderImportResult(data);
                loadList();
            })
            .catch(function (err) {
                notify('导入失败：' + err.message, false);
            })
            .then(function () {
                if (loader) { loader.style.display = 'none'; }
                if (uploadBtn) { uploadBtn.disabled = false; }
            });
    }

    function renderImportResult(data) {
        var box = $('importResult');
        var summary = $('importSummary');
        var tbody = $('importResultBody');
        if (!box || !tbody) { return; }

        if (summary) {
            summary.innerHTML =
                '<span class="badge bg-primary mr-2">总计 ' + data.total + '</span>' +
                '<span class="badge bg-success mr-2">成功 ' + data.success_count + '</span>' +
                '<span class="badge bg-danger">失败 ' + data.failed_count + '</span>';
        }

        tbody.innerHTML = (data.results || []).map(function (r) {
            var tag = r.ok
                ? '<span class="badge bg-success">成功</span>'
                : '<span class="badge bg-danger">失败</span>';
            var msg = esc(r.error || '');
            if (r.ok && r.error) { tag = '<span class="badge bg-warning text-dark">部分成功</span>'; }
            return '<tr class="' + (r.ok ? '' : 'table-danger') + '">' +
                '<td>' + r.row + '</td>' +
                '<td>' + esc(r.username || '') + '</td>' +
                '<td>' + tag + '</td>' +
                '<td class="small">' + msg + '</td></tr>';
        }).join('') || '<tr><td colspan="4" class="text-center text-muted">无数据</td></tr>';

        box.style.display = 'block';
    }

    // ------------------------------------------------------------------
    // 批量权限管理
    // ------------------------------------------------------------------
    function bindBatchPermission() {
        var modalEl = $('batchPermissionModal');
        if (!modalEl) { return; }
        modalEl.addEventListener('show.bs.modal', function () {
            var ids = selectedIds();
            if ($('batchTargetCount')) { $('batchTargetCount').textContent = String(ids.length); }
            if ($('batchTargetList')) {
                $('batchTargetList').innerHTML = ids.slice(0, 30).map(function (id) {
                    var row = state.rows.filter(function (r) { return r.id === id; })[0];
                    return '<span class="badge bg-light text-dark border mr-1">' +
                        esc(row ? row.username : ('#' + id)) + '</span>';
                }).join('') + (ids.length > 30 ? '<span class="text-muted small">…等 ' + ids.length + ' 人</span>' : '');
            }
            renderPermissionOverrideTree();
        });

        var rolesBtn = $('batchApplyRolesBtn');
        if (rolesBtn) { rolesBtn.addEventListener('click', applyBatchRoles); }
        var permBtn = $('batchApplyPermsBtn');
        if (permBtn) { permBtn.addEventListener('click', applyBatchPermissions); }
    }

    function renderPermissionOverrideTree() {
        var box = $('batchPermTree');
        if (!box) { return; }
        var tree = state.meta.permission_tree || [];
        if (!tree.length) {
            box.innerHTML = '<div class="text-muted small">暂无权限目录</div>';
            return;
        }
        box.innerHTML = tree.map(function (mod) {
            var items = (mod.permissions || []).map(function (p) {
                var code = p.code || p.id;
                return '<div class="col-md-6 col-lg-4">' +
                    '<div class="d-flex align-items-center justify-content-between border-bottom py-1">' +
                    '<span class="small" title="' + esc(code) + '">' + esc(p.name || code) + '</span>' +
                    '<select class="form-select form-select-sm perm-override" data-code="' + esc(code) + '" style="width:88px;">' +
                    '<option value="inherit" selected>继承</option>' +
                    '<option value="allow">允许</option>' +
                    '<option value="deny">禁止</option>' +
                    '</select></div></div>';
            }).join('');
            return '<div class="mb-2">' +
                '<div class="fw-bold small text-primary mb-1"><i class="fa fa-folder-open-o mr-1"></i>' +
                esc(mod.module_name || mod.module_key || '') + '</div>' +
                '<div class="row g-1">' + items + '</div></div>';
        }).join('');
    }

    function collectOverrideChanges() {
        var changes = {};
        Array.prototype.forEach.call(document.querySelectorAll('.perm-override'), function (sel) {
            var effect = sel.value;
            // 仅收集被显式设置的项（inherit 表示不改变）
            if (effect === 'allow' || effect === 'deny') {
                changes[sel.getAttribute('data-code')] = effect;
            }
        });
        return changes;
    }

    function applyBatchRoles() {
        var ids = selectedIds();
        if (!ids.length) { notify('请先选择用户', false); return; }
        var groupIds = [];
        Array.prototype.forEach.call(document.querySelectorAll('#batchRoleList input[type=checkbox]:checked'),
            function (cb) { groupIds.push(parseInt(cb.value, 10)); });
        var mode = ($('batchRoleMode') || {}).value || 'add';

        if (mode === 'replace' && !groupIds.length) {
            if (!confirm('覆盖模式且未选择任何权限组，将清空所选用户的全部权限组，确认继续？')) { return; }
        }

        var loader = $('batchRoleLoader');
        if (loader) { loader.style.display = 'inline-block'; }
        fetchJSON(API_BASE + '/batch-groups', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_ids: ids, group_ids: groupIds, mode: mode })
        }).then(function (data) {
            notify('批量分配权限组完成：成功 ' + data.changed + ' 人' +
                (data.errors && data.errors.length ? '，' + data.errors.length + ' 人未变更' : ''), true);
            if (data.errors && data.errors.length) { console.warn('批量权限组未变更明细:', data.errors); }
            loadList();
        }).catch(function (err) {
            notify('批量分配权限组失败：' + err.message, false);
        }).then(function () {
            if (loader) { loader.style.display = 'none'; }
        });
    }

    function applyBatchPermissions() {
        var ids = selectedIds();
        if (!ids.length) { notify('请先选择用户', false); return; }
        var overrides = collectOverrideChanges();
        if (!Object.keys(overrides).length) {
            notify('未设置任何权限覆盖项（全部为“继承”）', false);
            return;
        }
        var mode = ($('batchPermMode') || {}).value || 'merge';

        var loader = $('batchPermLoader');
        if (loader) { loader.style.display = 'inline-block'; }
        fetchJSON(API_BASE + '/batch-permissions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_ids: ids, overrides: overrides, mode: mode })
        }).then(function (data) {
            notify('批量权限设置完成：成功 ' + data.changed + ' 人' +
                (data.errors && data.errors.length ? '，' + data.errors.length + ' 人未变更' : ''), true);
            if (data.errors && data.errors.length) { console.warn('批量权限未变更明细:', data.errors); }
            loadList();
        }).catch(function (err) {
            notify('批量权限设置失败：' + err.message, false);
        }).then(function () {
            if (loader) { loader.style.display = 'none'; }
        });
    }

    // ------------------------------------------------------------------
    // 初始化
    // ------------------------------------------------------------------
    function init() {
        if (!$('userTableBody')) { return; }  // 非用户管理页

        bindSelectAll();
        bindFilters();
        bindSort();
        bindImport();
        bindBatchPermission();

        Promise.all([loadMeta()]).then(function () {
            loadList();
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // 暴露给页面（供权限判断等使用）
    window.UserManagement = {
        reload: loadList,
        state: state,
        selectedIds: selectedIds
    };
})();
