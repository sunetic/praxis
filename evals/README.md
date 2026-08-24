# Praxis Evals

本目录保存可版本化的 Eval runner、fixture 与公开 case corpus。常规测试与真实模型 Eval 的边界、运行方式、评分和报告说明统一维护在文档站：

- [Eval 评估](../docs/reliability/evaluation.md)
- [PostgreSQL DBA Cases](../docs/reliability/pg-dba-eval.md)
- [MySQL DBA Cases](../docs/reliability/mysql-dba-eval.md)

快速运行 PostgreSQL：`make eval`；运行 MySQL：`make eval EVAL_SUITE=mysql`；固定 harness 模型对比：`make eval EVAL_PROFILE=model`。API Key 只应存在于本地设置或环境变量中，不得写入 case、报告或提交。
