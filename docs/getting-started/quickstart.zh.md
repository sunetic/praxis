# Quickstart

## 用 Docker 启动

需要一台能够运行 Docker 的机器，以及一个 OpenAI 兼容模型服务的 API Key。

```bash
docker run -d \
  --name praxis \
  -p 8000:8000 \
  -v ~/.praxis/data:/app/data \
  sunzy2/praxis:latest
```

打开 [http://localhost:8000](http://localhost:8000)。首次启动会进入引导页：选择模型提供商，填写 API Key、模型名和 Base URL。配置保存在本地 Praxis 数据目录中。

!!! warning "生产环境先设置密钥"
    自部署到共享或生产环境时，请设置强随机 `SECRET_KEY` 或独立的 `DATASOURCE_ENCRYPTION_KEY`，并持久化 `/app/data`。加密密钥变化后，已有数据源密码将无法解密。

## 连接第一个数据库

1. 打开 **数据源**，选择“新增数据源”。
2. 选择 MySQL 或 PostgreSQL，填写主机、端口、用户和数据库名。
3. 先点“测试连接”，确认 Praxis 所在机器可以访问目标数据库。
4. 保存后进入 **Chat**，选择该数据源。

建议第一次接入使用只读、最小权限账户。只有在明确需要且完成风险评估后，才为专门的数据源配置写权限。

## 完成第一次任务

可以从一个可核验的问题开始，例如：

> 检查这个数据库当前的连接压力和长事务。先收集证据，再说明风险；不要执行任何修改。

观察回答是否包含实际查询得到的事实、推断与不确定性。如果一套分析方法需要反复使用，可以继续阅读 [Agent](../features/agents.md) 和 [Skills](../features/skills.md)。

## 从源码运行

本地开发需要 Python 3.11+、Node.js 18+ 和 `uv`。

```bash
make install
make migrate
make dev
```

另开终端启动前端：

```bash
cd frontend
npm install
npm run dev
```

后端默认位于 `http://localhost:8000`，前端开发服务默认位于 `http://localhost:5173`。
