# MySQL DBA Eval

这里是 MySQL DBA Eval 的可执行 suite 源码、fixture、知识材料和评分规则。通用命令、参数、判定方式和报告说明见 [Evaluation 文档](../../docs/reliability/evaluation.md)，case 目录见 [MySQL DBA Cases](../../docs/reliability/mysql-dba-eval.md)。

fixture 使用 `mysql:8.4`。默认 `praxis` profile 走真实后端与 HTTP/SSE Chat；`model` profile 使用固定模型对比 harness。所有账号、数据和策略均为隔离环境中的合成内容。
