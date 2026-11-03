# Docker 部署（NAS 自托管）

NAS 跑 server，多设备经 HTTP MCP 连同一份记忆库。本机保留 stdio 直跑能力不变（`MEMORY_MCP_TRANSPORT` 默认 stdio）。

## 架构

| 服务 | 端口 | 说明 |
|---|---|---|
| local-memory | 8000 | Streamable HTTP MCP + Bearer 鉴权（核心，必起） |
| ollama | 127.0.0.1:11434 | embedder bge-m3（profile `ollama`，可选） |
| mem-viewer | 8001 | 管理界面（profile `viewer`，可选，无自带鉴权） |

数据根挂载 `./data`（对应本机 `~/.memory`），所有路径 env 可覆盖的既有设计直接适配容器。

## NAS 部署步骤

### 1. 代码上 NAS

```bash
# Mac 上执行（NAS SSH 端口按实际改）
rsync -av --exclude '.git' --exclude 'tests' --exclude 'docs' \
    --exclude '.env' --exclude '__pycache__' \
    ~/Desktop/local-memory/ nas:~/docker/local-memory/
```

### 2. 生成 .env

```bash
cd ~/docker/local-memory
cp .env.example .env
# 编辑 .env：MEMORY_HTTP_TOKEN 设为长随机串（openssl rand -hex 24）
```

### 3. 迁移现有记忆库（Mac 上执行）

```bash
ssh nas "mkdir -p ~/docker/local-memory/data"
rsync -av ~/.memory/registry.json nas:~/docker/local-memory/data/
rsync -av ~/.memory/pools/ nas:~/docker/local-memory/data/pools/
```

迁移后编辑 NAS 上 `data/pools/default/config.json`：

- 用内置 ollama 容器：`embedder.base_url` 改 `http://ollama:11434`
- LLM 段保留原样（NAS 与 LLM 网关同内网，直连可达）；需要密钥的话把 `ANTHROPIC_AUTH_TOKEN` 写进 NAS `.env`

### 4. 启动

```bash
docker compose --profile ollama --profile viewer up -d --build
docker compose exec ollama ollama pull bge-m3   # 约 1.2GB，一次性
```

只用外部 Ollama（比如已有 LAN 内其他机跑 Ollama）则去掉 `--profile ollama`，config.json 的 `base_url` 指向那台机器。

### 5. 验证

```bash
curl -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8000/mcp -d '{}'    # 401
curl -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8000/mcp \
    -H "Authorization: Bearer $MEMORY_HTTP_TOKEN" \
    -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'  # 200
```

## 客户端接入（Mac / 其他设备）

ZCode / Claude Code 的 MCP 配置（stdio 配置替换为 http）：

```json
{
  "mcpServers": {
    "local-memory": {
      "type": "http",
      "url": "https://nas.example.top/memory/mcp",
      "headers": { "Authorization": "Bearer <MEMORY_HTTP_TOKEN>" }
    }
  }
}
```

接入前用 `search_memory` 抽查：本地池向量由同一 bge-m3 计算，迁移后检索结果应与迁移前一致。

## Lucky 反代注意

- Streamable HTTP 是 POST + SSE 流式响应：反代规则需关闭响应缓冲（buffering），放行 `text/event-stream`
- 建议该反代子域走 HTTPS；token 是唯一防线，务必长随机
- mem-viewer（8001）无自带鉴权：公网反代需在 Lucky 层加认证，或仅内网使用

## 备份

`./data` 目录整体即全部数据（SQLite + Chroma + 配置）。注意 SQLite 开了 WAL：server 运行中直接 tar `.db` 会丢最近写入（还在 `-wal` 文件里）。两种安全姿势：

```bash
# 姿势一：不停服，逐库热备（推荐，crontab 里跑）
docker compose exec local-memory python3 -c "
import sys; sys.path.insert(0, '/app/src')
from sqlite_conn import connect_db
from memory_paths import active_db_path, history_db_path, deleted_db_path
for db in (active_db_path(), history_db_path(), deleted_db_path()):
    connect_db(db).close()  # 最后一个连接显式关闭，触发 WAL checkpoint
"
tar czf backup-$(date +%F).tgz data/
```

```bash
# 姿势二：停服快照（最稳）
docker compose stop local-memory && tar czf backup-$(date +%F).tgz data/ && docker compose start local-memory
```

打包后 rclone 推对象存储即可，无需同步协议。
