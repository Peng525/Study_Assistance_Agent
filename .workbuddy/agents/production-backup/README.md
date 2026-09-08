# 生产级 QA 提示词备份

这里保存 Demo 轻量 QA 改造前的严格版 `code_reviewer` 和 `tester` 提示词。

- `code-reviewer.toml` 原文件 SHA-256：`BC53E4D5F9CC70D5528EF5B08F36D680EC00FB8DE1C0A8D32E69461218538E3E`
- `tester.toml` 原文件 SHA-256：`3EBBBE4B39164A51D44A08B25C11EADD2FEC3B6FDBEBFF2AA90997013488206E`
- 严格 Hook 仍保留在 `.codex/hooks/`：`set-qa-status.ps1`、`validate-qa-gates.ps1`、`pre-commit-guard.ps1`、`post-code-check.ps1` 和 `hook-utils.ps1`。
- 原生 Git 严格入口备份为 `.githooks/pre-commit.production`。

恢复生产级 QA 时：

1. 将本目录的两个 TOML 恢复到 `.codex/agents/`。
2. 将 `.codex/hooks.json` 中 Demo Hook 名称恢复为对应的严格 Hook 名称。
3. 将 `.githooks/pre-commit.production` 恢复为 `.githooks/pre-commit`。
4. 对最终候选重新运行严格 `tester` 和 `code_reviewer`，不得沿用 Demo PASS。

生产恢复是显式操作，Demo 阶段不会自动切换。
