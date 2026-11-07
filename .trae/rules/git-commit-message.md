---
alwaysApply: true
scene: git_message
---

在此处编写规则，自定义 AI 生成提交信息的风格。
请严格遵循 Conventional Commits 规范生成提交信息。
格式要求：`<type>(<scope>): <subject>`
- type 必须是以下之一：feat（新功能）、fix（修复 bug）、docs（文档）、style（格式）、refactor（重构）、test（测试）、chore（构建/工具）
- subject 必须简洁明了，不超过 50 个字符，使用祈使句，结尾不加句号。
- 语言使用中文。