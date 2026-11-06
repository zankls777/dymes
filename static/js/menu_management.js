/**
 * 侧边栏分组管理（菜单管理页）
 * 数据来源：GET /api/sidebar_menu → { layout, catalog }
 * 保存方式：POST /api/sidebar_menu，仅提交 { groups: [{key, title, entries: []}] }
 * 说明：页面标题、图标、权限由后端内置目录控制，此处只调整归类与顺序。
 */
(function () {
    'use strict';

    var API_BASE = '/api/sidebar_menu';
    var catalog = {};                 // 条目 key -> {title, icon, kind, summary}
    var layout = { groups: [] };      // 当前编辑中的分组布局

    var groupsEl = document.getElementById('mmGroups');
    var statusEl = document.getElementById('mmStatus');
    var saveBtn = document.getElementById('mmSave');
    var resetBtn = document.getElementById('mmReset');
    var addBtn = document.getElementById('mmAddGroup');

    /** 统一提示：成功为绿色，失败为红色 */
    function setStatus(message, isError) {
        statusEl.textContent = message || '';
        statusEl.className = 'mm-status' + (isError ? ' err' : ' ok');
    }

    function request(url, options) {
        var opts = Object.assign({ credentials: 'same-origin' }, options || {});
        return fetch(url, opts).then(function (res) {
            return res.json().catch(function () {
                return { success: false, error: '服务返回格式异常' };
            });
        });
    }

    /** 生成小图标按钮（上移/下移/删除） */
    function iconButton(icon, title, disabled, danger, onClick) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'mm-icon-btn' + (danger ? ' danger' : '');
        btn.title = title;
        btn.setAttribute('aria-label', title);
        btn.disabled = !!disabled;
        var i = document.createElement('i');
        i.className = 'fa ' + icon;
        btn.appendChild(i);
        btn.addEventListener('click', onClick);
        return btn;
    }

    /** 分组卡片：可改名、上下移动、删除（至少保留一个分组） */
    function renderGroup(group, index) {
        var card = document.createElement('div');
        card.className = 'mm-group';
        card.dataset.key = group.key;

        var head = document.createElement('div');
        head.className = 'mm-group-head';

        var input = document.createElement('input');
        input.className = 'mm-title-input';
        input.value = group.title || '';
        input.maxLength = 20;
        input.setAttribute('aria-label', '分组名称');
        input.addEventListener('change', function () {
            group.title = input.value.trim() || '未命名分组';
            input.value = group.title;
        });

        var count = document.createElement('span');
        count.className = 'mm-count';
        count.textContent = group.entries.length + ' 项';

        var actions = document.createElement('div');
        actions.className = 'd-flex';
        actions.style.gap = '4px';
        actions.appendChild(iconButton('fa-arrow-up', '上移分组', index === 0, false, function () {
            swapGroups(index, index - 1);
        }));
        actions.appendChild(iconButton('fa-arrow-down', '下移分组', index === layout.groups.length - 1, false, function () {
            swapGroups(index, index + 1);
        }));
        actions.appendChild(iconButton('fa-trash', '删除分组', layout.groups.length <= 1, true, function () {
            removeGroup(index);
        }));

        head.appendChild(input);
        head.appendChild(count);
        head.appendChild(actions);

        var entries = document.createElement('div');
        entries.className = 'mm-entries';
        if (group.entries.length) {
            group.entries.forEach(function (entryKey, entryIndex) {
                entries.appendChild(renderEntry(entryKey, index, entryIndex));
            });
        } else {
            var empty = document.createElement('div');
            empty.className = 'mm-empty';
            empty.textContent = '暂无菜单，可从其它分组移动进来';
            entries.appendChild(empty);
        }

        card.appendChild(head);
        card.appendChild(entries);
        return card;
    }

    /** 单个菜单条目：上下移动 + 跨分组移动 */
    function renderEntry(entryKey, groupIndex, entryIndex) {
        var meta = catalog[entryKey] || { title: entryKey, icon: 'fa-circle-o', summary: '' };
        var group = layout.groups[groupIndex];

        var row = document.createElement('div');
        row.className = 'mm-entry';

        var icon = document.createElement('i');
        icon.className = 'fa ' + meta.icon;

        var title = document.createElement('span');
        title.className = 'mm-entry-title';
        title.textContent = meta.title;

        var summary = document.createElement('span');
        summary.className = 'mm-entry-summary';
        summary.textContent = meta.summary || '';
        summary.title = meta.summary || '';

        var upBtn = iconButton('fa-arrow-up', '上移菜单', entryIndex === 0, false, function () {
            moveEntryWithinGroup(groupIndex, entryIndex, entryIndex - 1);
        });
        var downBtn = iconButton('fa-arrow-down', '下移菜单', entryIndex === group.entries.length - 1, false, function () {
            moveEntryWithinGroup(groupIndex, entryIndex, entryIndex + 1);
        });

        var moveSelect = document.createElement('select');
        moveSelect.className = 'mm-move';
        moveSelect.setAttribute('aria-label', '移动到其他分组');
        var placeholder = document.createElement('option');
        placeholder.value = '';
        placeholder.textContent = '移动到…';
        moveSelect.appendChild(placeholder);
        layout.groups.forEach(function (otherGroup, otherIndex) {
            if (otherIndex === groupIndex) { return; }
            var option = document.createElement('option');
            option.value = String(otherIndex);
            option.textContent = otherGroup.title || '未命名分组';
            moveSelect.appendChild(option);
        });
        moveSelect.addEventListener('change', function () {
            if (!moveSelect.value) { return; }
            moveEntryToGroup(groupIndex, entryIndex, parseInt(moveSelect.value, 10));
        });

        row.appendChild(icon);
        row.appendChild(title);
        row.appendChild(summary);
        row.appendChild(upBtn);
        row.appendChild(downBtn);
        row.appendChild(moveSelect);
        return row;
    }

    function render() {
        groupsEl.innerHTML = '';
        if (!layout.groups.length) {
            groupsEl.innerHTML = '<div class="mm-empty">暂无分组，请先新增分组</div>';
            return;
        }
        layout.groups.forEach(function (group, index) {
            groupsEl.appendChild(renderGroup(group, index));
        });
    }

    function swapGroups(from, to) {
        if (to < 0 || to >= layout.groups.length) { return; }
        var moved = layout.groups.splice(from, 1)[0];
        layout.groups.splice(to, 0, moved);
        render();
    }

    function removeGroup(index) {
        if (layout.groups.length <= 1) { return; }
        var target = layout.groups[index];
        if (!window.confirm('删除分组“' + (target.title || '未命名分组') + '”？其中菜单将合并到相邻分组。')) { return; }
        var receiver = index === 0 ? layout.groups[1] : layout.groups[index - 1];
        receiver.entries = receiver.entries.concat(target.entries);
        layout.groups.splice(index, 1);
        render();
        setStatus('已删除分组，菜单已合并到“' + receiver.title + '”，请保存后生效');
    }

    function moveEntryWithinGroup(groupIndex, from, to) {
        var entries = layout.groups[groupIndex].entries;
        if (to < 0 || to >= entries.length) { return; }
        var moved = entries.splice(from, 1)[0];
        entries.splice(to, 0, moved);
        render();
    }

    function moveEntryToGroup(fromGroup, entryIndex, toGroup) {
        var entries = layout.groups[fromGroup].entries;
        var moved = entries.splice(entryIndex, 1)[0];
        layout.groups[toGroup].entries.push(moved);
        render();
    }

    function addGroup() {
        var now = new Date();
        var key = 'g' + now.getTime();
        layout.groups.push({ key: key, title: '新分组', entries: [] });
        render();
        setStatus('已新增分组，请修改名称并保存');
    }

    function save() {
        saveBtn.disabled = true;
        setStatus('正在保存…', false);
        request(API_BASE, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ layout: layout })
        }).then(function (res) {
            saveBtn.disabled = false;
            if (!res.success) {
                setStatus(res.error || '保存失败', true);
                return;
            }
            layout = res.layout;
            render();
            setStatus(res.message || '配置已保存，刷新页面即可看到最新分组');
        }).catch(function () {
            saveBtn.disabled = false;
            setStatus('网络异常，保存失败', true);
        });
    }

    function reset() {
        if (!window.confirm('恢复默认分组会覆盖当前配置，是否继续？')) { return; }
        resetBtn.disabled = true;
        request(API_BASE + '/reset', { method: 'POST' }).then(function (res) {
            resetBtn.disabled = false;
            if (!res.success) {
                setStatus(res.error || '恢复失败', true);
                return;
            }
            layout = res.layout;
            render();
            setStatus(res.message || '已恢复默认分组');
        }).catch(function () {
            resetBtn.disabled = false;
            setStatus('网络异常，恢复失败', true);
        });
    }

    function load() {
        request(API_BASE, { method: 'GET' }).then(function (res) {
            if (!res.success) {
                groupsEl.innerHTML = '';
                setStatus(res.error || '加载失败', true);
                return;
            }
            catalog = {};
            (res.catalog && res.catalog.entries || []).forEach(function (item) {
                catalog[item.key] = item;
            });
            layout = res.layout || { groups: [] };
            render();
        }).catch(function () {
            groupsEl.innerHTML = '';
            setStatus('网络异常，加载失败', true);
        });
    }

    addBtn.addEventListener('click', addGroup);
    saveBtn.addEventListener('click', save);
    resetBtn.addEventListener('click', reset);
    load();
})();
