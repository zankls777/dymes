/**
 * PDA 扫码桥（通用）
 * ------------------------------------------------------------------
 * APK 壳（android_shell）收到扫描器 Intent 广播后，会调用
 * window.__bomPdaScan(条码)，本脚本负责把条码“喂”给当前页面。
 *
 * 优先级：
 *   1) 页面自行实现了 window.__bomPdaScan（如资材入库页），保留页面实现，本脚本不覆盖
 *   2) 页面通过 window.bomPdaSetHandler(fn) 注册了处理函数
 *   3) 通用兜底：写入目标输入框并派发 input/change/Enter 事件，触发页面已有的查询逻辑
 *
 * 目标输入框的挑选顺序：
 *   ① [data-pda-target="1"] 显式声明
 *   ② 最近聚焦的 .scan-input 扫码专用框（如线束扫描页 回路号/号机 两框自动切换）
 *   ③ 首个可见的 .scan-input
 *   ④ 最近聚焦的可编辑框（排除数量框等非扫码框）
 *   ⑤ 首个可见的可编辑输入框
 */
(function () {
    'use strict';

    if (window.__bomPdaBridgeInstalled) { return; }
    window.__bomPdaBridgeInstalled = true;

    var lastFocus = null;     // 最近获得焦点的可编辑元素
    var customHandler = null; // 页面注册的自定义处理

    // 判定元素是否为可接收扫码内容的输入框
    function isEditable(el) {
        if (!el || !el.tagName) { return false; }
        var tag = el.tagName.toUpperCase();
        if (tag === 'TEXTAREA') { return !el.disabled && !el.readOnly && isVisible(el); }
        if (tag !== 'INPUT') { return false; }
        var type = (el.type || 'text').toLowerCase();
        if (['text', 'search', 'number', 'tel', 'url', 'email', 'password'].indexOf(type) < 0) { return false; }
        return !el.disabled && !el.readOnly && isVisible(el);
    }

    function isVisible(el) {
        if (!el) { return false; }
        return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
    }

    function hasScanClass(el) {
        return !!(el && el.classList && el.classList.contains('scan-input'));
    }

    // 选择本次扫码要写入的输入框
    function pickTarget() {
        var explicit = document.querySelector('[data-pda-target="1"]');
        if (isEditable(explicit)) { return explicit; }

        if (hasScanClass(lastFocus) && isEditable(lastFocus)) { return lastFocus; }

        var scans = document.querySelectorAll('.scan-input');
        for (var i = 0; i < scans.length; i++) {
            if (isEditable(scans[i])) { return scans[i]; }
        }

        if (isEditable(lastFocus) && !hasQtyClass(lastFocus)) { return lastFocus; }

        var all = document.querySelectorAll('input, textarea');
        for (var j = 0; j < all.length; j++) {
            if (isEditable(all[j]) && !hasQtyClass(all[j])) { return all[j]; }
        }
        return null;
    }

    // 数量/金额类输入框不作为默认扫码目标，避免误覆盖
    function hasQtyClass(el) {
        if (!el || !el.classList) { return false; }
        return el.classList.contains('qty-edit') || el.classList.contains('qty-input')
            || el.classList.contains('pda-ignore');
    }

    // 派发普通事件（兼容老内核）
    function fire(el, type) {
        try {
            el.dispatchEvent(new Event(type, { bubbles: true }));
        } catch (e) {
            try {
                var ev = document.createEvent('Event');
                ev.initEvent(type, true, true);
                el.dispatchEvent(ev);
            } catch (e2) { /* 忽略 */ }
        }
    }

    // 派发键盘事件（页面多绑定 keydown 的 Enter 触发查询）
    function fireKey(el, type, key, code) {
        try {
            var ev = new KeyboardEvent(type, {
                key: key, code: key, keyCode: code, which: code, bubbles: true, cancelable: true
            });
            el.dispatchEvent(ev);
        } catch (e) { /* 老内核忽略 */ }
    }

    // 通用兜底：写入输入框并触发页面自身的查询逻辑
    function pushToPage(code) {
        var text = String(code === null || code === undefined ? '' : code).trim();
        if (!text) { return 'empty'; }

        var el = pickTarget();
        if (!el) { return 'no-target'; }

        try { el.focus(); } catch (e) { /* 忽略 */ }
        el.value = text;
        fire(el, 'input');
        fire(el, 'change');
        fireKey(el, 'keydown', 'Enter', 13);
        fireKey(el, 'keyup', 'Enter', 13);
        return 'ok';
    }

    // 统一入口：页面有自定义处理优先用，否则走通用兜底
    function bridge(code) {
        if (typeof customHandler === 'function') {
            try { return customHandler(code); } catch (e) { /* 落到兜底 */ }
        }
        return pushToPage(code);
    }

    /** 页面注册自定义扫码处理：window.bomPdaSetHandler(function(code){...}) */
    window.bomPdaSetHandler = function (fn) { customHandler = fn; };

    // 记录最近聚焦的输入框（capture 阶段，避免页面 stopPropagation 影响）
    document.addEventListener('focusin', function (e) {
        if (isEditable(e.target)) { lastFocus = e.target; }
    }, true);

    // 页面若已自行实现桥（如资材入库页），保留页面实现，不覆盖
    if (typeof window.__bomPdaScan !== 'function') { window.__bomPdaScan = bridge; }
    if (typeof window.bomPdaScan !== 'function') { window.bomPdaScan = bridge; }

    // APK 在页面脚本之前注入的结果（记录在队列里），页面就绪后补处理
    var queue = window.__bomPdaQueue;
    if (queue && queue.length) {
        var pending = queue.slice();
        queue.length = 0;
        pending.forEach(function (c) { bridge(c); });
    }
})();
