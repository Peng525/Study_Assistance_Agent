# QA PASS 标记机制

## 设计原则

**各 subagent 各自背书，不依赖主 agent 手动写标记。** 代码检查 subagent 完成且通过后自己写 `code-review-pass.flag`，测试 subagent 完成且通过后自己写 `test-pass-pass.flag`。git 提交门禁检查两个标记都存在且都是 PASS。

## 为什么不用单个 qa-pass.flag（原方案已废弃）

| 原方案问题 | 新方案解决 |
|---|---|
| 主 agent 读完两个报告再手动写标记，容易遗漏 | subagent 完成检查即写标记，确定性执行 |
| 单个标记无法区分缺哪个检查 | 两个独立标记，hook 精确告知"代码检查未通过"还是"测试未通过" |
| 主 agent 是判断者，subagent 做了检查却由主 agent 背书 | subagent 对自己的结论负责，职责清晰 |

## 标记文件

| 文件 | 写入者 | 路径 | 内容 |
|---|---|---|---|
| 代码检查标记 | 代码检查 subagent | `.codebuddy/code-review-pass.flag` | `PASS` / `FAIL` / `PENDING` |
| 测试标记 | 测试 subagent | `.codebuddy/test-pass.flag` | `PASS` / `FAIL` / `PENDING` |

## 完整工作流程

```
1. 主 Agent 完成功能开发
2. 主 Agent 派生「代码检查」subagent
   - subagent 审查代码
   - 审查通过 → subagent 自己写入：echo "PASS" > .codebuddy/code-review-pass.flag
   - 审查不通过 → subagent 写入：echo "FAIL" > .codebuddy/code-review-pass.flag
   - 报告输出到 _scratch/code-review.md
3. 主 Agent 读取报告，若 FAIL 则修复，修复后重新派生代码检查 subagent
4. 主 Agent 派生「测试」subagent
   - subagent 运行 E2E 测试
   - 测试通过 → subagent 自己写入：echo "PASS" > .codebuddy/test-pass.flag
   - 测试不通过 → subagent 写入：echo "FAIL" > .codebuddy/test-pass.flag
   - 报告输出到 _scratch/test-report.md
5. 主 Agent 读取报告，若 FAIL 则修复，修复后重新派生测试 subagent
6. 主 Agent 执行 git commit
7. pre-commit-guard.sh hook 检测到 git commit：
   - 检查 code-review-pass.flag 和 test-pass.flag
   - 两个都存在且内容为 PASS → 放行
   - 任一缺失或非 PASS → 阻止提交，精确告知缺哪个
8. git commit 成功后，两个标记文件清空为 PENDING（防下次直接放行）：
   echo "PENDING" > .codebuddy/code-review-pass.flag
   echo "PENDING" > .codebuddy/test-pass.flag
```

## 标记状态

| 内容 | 含义 |
|---|---|
| 文件不存在 | 该检查未执行，提交会被拦截 |
| `PASS` | 该检查已通过 |
| `FAIL` | 该检查未通过，需修复后重跑 |
| `PENDING` | 上次提交后已重置，需重新检查 |

## 权限说明

- 标记文件不是 `src/` 业务源码，subagent 写标记文件不违反"业务源码仅主 agent 可写"铁律
- 代码检查 subagent 可写：`_scratch/*.md` + `.codebuddy/code-review-pass.flag`
- 测试 subagent 可写：`*.test.*` + `_scratch/*.md` + `.codebuddy/test-pass.flag`
- 主 agent 不写标记文件，只读标记文件判断是否可提交

## 注意

- 两个标记文件在 `.gitignore` 中被忽略，不提交到版本库
- 标记由各自 subagent 负责写入，主 agent 负责提交后清空（防下次直接放行）
- 如果跳过 subagent 直接提交，hook 会拦截并精确告知缺哪个检查
