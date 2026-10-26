/* 侧边栏页面脚本（L-8：由 templates/_sidebar.html 内联块外置）
 * 负责：子菜单展开/折叠与状态记忆、菜单文本编辑、管理员模式、菜单统计等。
 * 说明：与应用级导航交互（static/js/sidebar.js）职责不同，二者互不覆盖。
 */
document.addEventListener('DOMContentLoaded', function() {
    // 获取元素
    const sidebar = document.getElementById('sidebar');
    const sidebarToggle = document.getElementById('sidebar-toggle');
    const menuItems = document.querySelectorAll('.menu-item.has-submenu');
    const adminSettingsBtn = document.getElementById('admin-settings-btn');
    const adminModal = document.getElementById('admin-modal');
    const adminLoginBtn = document.getElementById('admin-login');
    const adminCancelBtn = document.getElementById('admin-cancel');
    const adminPasswordInput = document.getElementById('admin-password');
    const adminIndicator = document.getElementById('admin-indicator');
    const totalMenuItems = document.getElementById('total-menu-items');
    const totalSubmenus = document.getElementById('total-submenus');
    const totalLevels = document.getElementById('total-levels');
    const currentDate = document.getElementById('current-date');
    
    // 全局变量
    let isAdminMode = localStorage.getItem('isAdminMode') === 'true';
    let menuTexts = JSON.parse(localStorage.getItem('menuTexts') || '{}');
    
    // 侧边栏切换功能
    if (sidebarToggle) {
        sidebarToggle.addEventListener('click', function() {
            sidebar.classList.toggle('collapsed');
        });
    }
    
    // 子菜单展开/折叠功能
    menuItems.forEach(item => {
        const link = item.querySelector('.menu-link');
        
        link.addEventListener('click', function(e) {
            e.preventDefault();
            item.classList.toggle('open');
            
            // 保存菜单展开状态到本地存储
            saveMenuState();
        });
    });
    
    // 保存菜单状态到本地存储
    function saveMenuState() {
        const menuState = {};
        menuItems.forEach(item => {
            const id = item.querySelector('.menu-link span').textContent;
            menuState[id] = item.classList.contains('open');
        });
        localStorage.setItem('menuState', JSON.stringify(menuState));
    }
    
    // 从本地存储恢复菜单状态
    function loadMenuState() {
        const savedState = localStorage.getItem('menuState');
        if (savedState) {
            try {
                const menuState = JSON.parse(savedState);
                menuItems.forEach(item => {
                    const id = item.querySelector('.menu-link span').textContent;
                    if (menuState[id]) {
                        item.classList.add('open');
                    }
                });
            } catch (error) {
                console.error('Failed to load menu state:', error);
            }
        }
    }
    
    // 自动滚动至选中菜单项
    function scrollToActiveItem() {
        const activeItem = document.querySelector('.menu-item.active');
        if (activeItem) {
            activeItem.scrollIntoView({
                behavior: 'smooth',
                block: 'center'
            });
        }
    }
    
    // 初始化时滚动至选中项
    scrollToActiveItem();
    
    // 处理响应式菜单
    function handleResponsiveMenu() {
        if (window.innerWidth <= 768) {
            // 创建遮罩层
            let backdrop = document.querySelector('.sidebar-backdrop');
            if (!backdrop) {
                backdrop = document.createElement('div');
                backdrop.className = 'sidebar-backdrop';
                document.body.appendChild(backdrop);
                
                // 点击遮罩关闭侧边栏
                backdrop.addEventListener('click', function() {
                    sidebar.classList.remove('open');
                    backdrop.classList.remove('show');
                });
            }
            
            // 点击切换按钮打开侧边栏
            sidebarToggle.addEventListener('click', function() {
                sidebar.classList.toggle('open');
                backdrop.classList.toggle('show');
                // 打开侧边栏后滚动至选中项
                setTimeout(scrollToActiveItem, 100);
            });
        }
    }
    
    // 初始化响应式菜单
    handleResponsiveMenu();
    
    // 菜单配置导入导出功能
    function initMenuImportExport() {
        const exportBtn = document.getElementById('export-menu');
        const importBtn = document.getElementById('import-menu');
        const fileInput = document.getElementById('menu-file');
        
        // 导出菜单配置
        if (exportBtn) {
            exportBtn.addEventListener('click', function() {
                const menuConfig = {
                    menuState: JSON.parse(localStorage.getItem('menuState') || '{}'),
                    menuTexts: menuTexts,
                    timestamp: new Date().toISOString(),
                    version: '1.0'
                };
                
                const blob = new Blob([JSON.stringify(menuConfig, null, 2)], {
                    type: 'application/json'
                });
                
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `menu-config-${new Date().toISOString().slice(0, 10)}.json`;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
            });
        }
        
        // 导入菜单配置
        if (importBtn) {
            importBtn.addEventListener('click', function() {
                fileInput.click();
            });
        }
        
        if (fileInput) {
            fileInput.addEventListener('change', function(e) {
                const file = e.target.files[0];
                if (file) {
                    const reader = new FileReader();
                    reader.onload = function(e) {
                        try {
                            const menuConfig = JSON.parse(e.target.result);
                            if (menuConfig.menuState) {
                                localStorage.setItem('menuState', JSON.stringify(menuConfig.menuState));
                                // 重新加载菜单状态
                                loadMenuState();
                            }
                            if (menuConfig.menuTexts) {
                                menuTexts = menuConfig.menuTexts;
                                localStorage.setItem('menuTexts', JSON.stringify(menuTexts));
                                // 更新菜单文本
                                updateMenuTexts();
                            }
                            alert('菜单配置导入成功！');
                        } catch (error) {
                            console.error('Failed to import menu config:', error);
                            alert('导入菜单配置失败，请检查文件格式！');
                        }
                    };
                    reader.readAsText(file);
                }
                // 重置文件输入
                fileInput.value = '';
            });
        }
    }
    
    // 初始化菜单导入导出功能
    initMenuImportExport();
    
    // 管理员模式功能
    function initAdminMode() {
        // 更新管理员模式状态
        updateAdminModeState();
        
        // 管理员设置按钮点击事件
        if (adminSettingsBtn) {
            adminSettingsBtn.addEventListener('click', function(e) {
                e.preventDefault();
                if (!isAdminMode) {
                    // 显示管理员登录模态框
                    adminModal.classList.add('show');
                } else {
                    // 退出管理员模式
                    isAdminMode = false;
                    localStorage.setItem('isAdminMode', 'false');
                    updateAdminModeState();
                    alert('已退出管理员模式');
                }
            });
        }
        
        // 管理员登录按钮点击事件
        if (adminLoginBtn) {
            adminLoginBtn.addEventListener('click', function() {
                const password = adminPasswordInput.value.trim();
                // 简单密码验证（实际应用中应该使用更安全的验证方式）
                if (password === 'admin123') {
                    isAdminMode = true;
                    localStorage.setItem('isAdminMode', 'true');
                    updateAdminModeState();
                    adminModal.classList.remove('show');
                    adminPasswordInput.value = '';
                    alert('管理员登录成功！');
                } else {
                    alert('密码错误，请重试！');
                }
            });
        }
        
        // 管理员取消按钮点击事件
        if (adminCancelBtn) {
            adminCancelBtn.addEventListener('click', function() {
                adminModal.classList.remove('show');
                adminPasswordInput.value = '';
            });
        }
        
        // 点击模态框外部关闭
        adminModal.addEventListener('click', function(e) {
            if (e.target === adminModal) {
                adminModal.classList.remove('show');
                adminPasswordInput.value = '';
            }
        });
        
        // 键盘事件处理
        document.addEventListener('keydown', function(e) {
            // 按Escape关闭模态框
            if (e.key === 'Escape' && adminModal.classList.contains('show')) {
                adminModal.classList.remove('show');
                adminPasswordInput.value = '';
            }
            
            // 按Enter登录
            if (e.key === 'Enter' && adminModal.classList.contains('show')) {
                adminLoginBtn.click();
            }
            
            // Alt + S 切换侧边栏
            if (e.altKey && e.key === 's') {
                e.preventDefault();
                sidebar.classList.toggle('collapsed');
            }
        });
    }
    
    // 更新管理员模式状态
    function updateAdminModeState() {
        if (isAdminMode) {
            adminIndicator.style.display = 'block';
            adminSettingsBtn.querySelector('span').textContent = '退出管理员模式';
            adminSettingsBtn.querySelector('i').className = 'fa fa-unlock';
        } else {
            adminIndicator.style.display = 'none';
            adminSettingsBtn.querySelector('span').textContent = '管理员设置';
            adminSettingsBtn.querySelector('i').className = 'fa fa-lock';
        }
    }
    
    // 初始化菜单编辑功能
    function initMenuEditing() {
        // 获取所有菜单项文本
        const menuSpans = document.querySelectorAll('.menu-link span');
        
        // 更新菜单文本
        updateMenuTexts();
        
        // 为每个菜单项添加双击事件
        menuSpans.forEach(span => {
            span.addEventListener('dblclick', function() {
                if (!isAdminMode) {
                    alert('请先进入管理员模式以编辑菜单项');
                    return;
                }
                
                const originalText = this.textContent;
                const menuKey = originalText;
                
                // 创建输入框
                const input = document.createElement('input');
                input.type = 'text';
                input.className = 'menu-edit-input';
                input.value = originalText;
                input.style.width = `${Math.max(this.offsetWidth + 20, 100)}px`;
                
                // 保存原始元素
                const parent = this.parentElement;
                const originalElement = this;
                
                // 替换为输入框
                parent.replaceChild(input, this);
                
                // 聚焦输入框并选择文本
                input.focus();
                input.select();
                
                // 键盘事件处理
                input.addEventListener('keydown', function(e) {
                    // 按Enter保存
                    if (e.key === 'Enter') {
                        const newText = this.value.trim();
                        if (newText) {
                            // 保存到本地存储
                            menuTexts[menuKey] = newText;
                            localStorage.setItem('menuTexts', JSON.stringify(menuTexts));
                            
                            // 恢复为文本
                            originalElement.textContent = newText;
                            parent.replaceChild(originalElement, this);
                            
                            // 更新所有相同文本的菜单项
                            updateMenuTexts();
                        }
                    }
                    
                    // 按Escape取消
                    if (e.key === 'Escape') {
                        parent.replaceChild(originalElement, this);
                    }
                });
                
                // 失去焦点事件
                input.addEventListener('blur', function() {
                    const newText = this.value.trim();
                    if (newText) {
                        // 保存到本地存储
                        menuTexts[menuKey] = newText;
                        localStorage.setItem('menuTexts', JSON.stringify(menuTexts));
                        
                        // 恢复为文本
                        originalElement.textContent = newText;
                        parent.replaceChild(originalElement, this);
                        
                        // 更新所有相同文本的菜单项
                        updateMenuTexts();
                    } else {
                        parent.replaceChild(originalElement, this);
                    }
                });
            });
        });
    }
    
    // 更新菜单文本
    function updateMenuTexts() {
        const menuSpans = document.querySelectorAll('.menu-link span');
        menuSpans.forEach(span => {
            const originalText = span.textContent;
            if (menuTexts[originalText]) {
                span.textContent = menuTexts[originalText];
            }
        });
    }
    
    // 初始化菜单统计信息
    function initMenuStatistics() {
        // 计算菜单统计信息
        function calculateMenuStatistics() {
            const allMenuItems = document.querySelectorAll('.menu-item');
            const allSubmenus = document.querySelectorAll('.submenu');
            
            // 计算菜单层级
            let maxLevel = 0;
            allMenuItems.forEach(item => {
                let level = 0;
                let parent = item.parentElement;
                while (parent && parent.classList.contains('submenu')) {
                    level++;
                    parent = parent.parentElement.parentElement;
                }
                maxLevel = Math.max(maxLevel, level);
            });
            
            // 更新统计信息
            if (totalMenuItems) {
                totalMenuItems.textContent = allMenuItems.length;
            }
            if (totalSubmenus) {
                totalSubmenus.textContent = allSubmenus.length;
            }
            if (totalLevels) {
                totalLevels.textContent = maxLevel + 1;
            }
        }
        
        // 更新当前日期
        function updateCurrentDate() {
            const date = new Date();
            const options = { year: 'numeric', month: '2-digit', day: '2-digit' };
            if (currentDate) {
                currentDate.textContent = date.toLocaleDateString('zh-CN', options);
            }
        }
        
        // 初始化统计信息
        calculateMenuStatistics();
        updateCurrentDate();
        
        // 每天更新日期
        setInterval(updateCurrentDate, 1000 * 60 * 60 * 24);
    }
    
    // 保存菜单状态到本地存储
    function saveMenuState() {
        const menuState = {};
        menuItems.forEach(item => {
            const id = item.querySelector('.menu-link span').textContent;
            menuState[id] = item.classList.contains('open');
        });
        localStorage.setItem('menuState', JSON.stringify(menuState));
    }
    
    // 从本地存储恢复菜单状态
    function loadMenuState() {
        const savedState = localStorage.getItem('menuState');
        if (savedState) {
            try {
                const menuState = JSON.parse(savedState);
                menuItems.forEach(item => {
                    const id = item.querySelector('.menu-link span').textContent;
                    if (menuState[id]) {
                        item.classList.add('open');
                    }
                });
            } catch (error) {
                console.error('Failed to load menu state:', error);
            }
        }
    }
    
    // 初始化所有功能
    function initAll() {
        // 加载菜单状态
        loadMenuState();
        
        // 初始化管理员模式
        initAdminMode();
        
        // 初始化菜单编辑功能
        initMenuEditing();
        
        // 初始化菜单统计信息
        initMenuStatistics();
        
        // 自动滚动至选中菜单项
        scrollToActiveItem();
    }
    
    // 初始化所有功能
    initAll();
    
    // 窗口大小改变时重新处理响应式菜单
    window.addEventListener('resize', handleResponsiveMenu);
});