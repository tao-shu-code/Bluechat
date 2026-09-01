# 企业级 RAG 知识库问答系统

基于 **FastAPI + LangChain 1.3.16 + Celery(RabbitMQ) + Vue 3 + pgvector** 的企业级知识库问答系统：文档上传后经解析、清洗、分块、向量化流水线入库（docx/PDF 按标题章节分块，PDF 支持**表格转 Markdown** 与**图片 OCR**），问答时采用 **向量 + BM25 关键词混合检索**（单 SQL 跨库向量召回 + RRF 融合），可选 **Rerank 重排**，LLM 以 SSE 流式生成答案并附引用来源；文档管理页支持**源文档在线预览**（PDF/Word/Excel/TXT/Markdown）与**文档检索调试工具**（BM25 + 向量双路召回，RRF 融合）；每次问答记录 **Token 用量**，用户可对回答**点赞/点踩**，管理员可查看全部问答记录与用量汇总；内置 JWT 认证、RBAC 角色、知识库 ACL、限流、拒答与审计日志。

- 后端：FastAPI 0.115 / SQLAlchemy 2.0 / Celery 5.4 / LangChain 1.3.16（OpenAI 兼容接口）
- 存储：PostgreSQL 16（**ParadeDB 镜像**：pgvector 向量检索 + pg_search 原生 BM25/lindera 中文分词）、Redis 7（缓存/会话上下文）、MinIO（对象存储）
- 异步：RabbitMQ 3.13 作为 Celery 消息队列，worker 承担解析/分块/向量化
- 前端：Vue 3 + Element Plus + Pinia + Vite（容器内由 nginx 托管并反代 API；docx/xlsx 预览库按需动态加载）

## 系统架构

```
┌──────────────────┐  /api 反代  ┌─────────────────────┐  发布任务  ┌──────────────────┐
│ frontend         │ ─────────▶ │ backend (FastAPI)   │ ────────▶ │ RabbitMQ         │
│ Vue 3 + nginx    │            │ :8000 (REST + SSE)  │  (Celery) │ :5672 (管理:15672)│
│ :80 / dev :5173  │            └──────────┬──────────┘           └────────┬─────────┘
└──────────────────┘                       │                               │ 消费
                     ┌─────────────────────┼─────────────┐                ▼
                     ▼                     ▼             ▼        ┌──────────────────┐
             ┌──────────────┐      ┌──────────────┐    │        │ worker (Celery)  │
             │ Redis 7      │      │ MinIO        │    │        │ 解析→分块→向量化  │
             │ :6379        │      │ :9000/:9001  │    │        └────────┬─────────┘
             │ 缓存/会话     │      │ 对象存储      │    │                 │
             └──────────────┘      └──────────────┘    ▼                 │
                             ┌───────────────────────────┐◀──────────────┘
                             │ PostgreSQL 16 :5432       │  向量/元数据读写
                             │ ParadeDB(pgvector+BM25)   │
                             └───────────────────────────┘
```

## 快速开始

### 准备工作

1. 安装 Docker（含 compose 插件）、Python 3.11+、Node.js（前端用 pnpm）。
2. 在**仓库根目录**创建环境配置：

```bash
cp .env.example .env   # Windows 可用 copy，按需修改；.env 已被 .gitignore 忽略，勿提交
```

说明：

- **COMPOSE_FILE 变量已内置** `docker/docker-compose.yml;docker/docker-compose.app.yml`（Windows 分隔符为 `;`，Linux/macOS 改为 `:`），因此所有 `docker compose` 命令**在仓库根目录执行**即可，无需 `-f` 参数。
- `.env` 中 URL 主机统一写 `localhost`（供模式 A 本地运行使用）；模式 B 下容器内地址（postgres/redis/rabbitmq/minio 等服务名）由 `docker/docker-compose.app.yml` 的 `environment` 自动覆盖，无需手改。
- 若在 `docker/` 目录内直接操作 compose，必须显式 `--env-file ../.env`，否则 `${VAR}` 插值读不到根 `.env`（`env_file` 注入不受影响）。

### 模式 A：开发调试（仅中间件容器化）

`docker compose up -d` 只启动中间件 **postgres / redis / rabbitmq / minio**（应用容器均带 `profiles=["app"]`，不会启动）。

```bash
# 1. 启动中间件（仓库根目录执行）
docker compose up -d

# 2. 后端本地运行（首次先 pip install -r requirements.txt）
cd backend
uvicorn app.main:app --reload --port 8000

# 3. Celery worker 本地运行（另开终端，仍在 backend 目录）
#    Windows 下 prefork 池不可用，必须加 -P solo
celery -A app.tasks.celery_app worker --loglevel=info -P solo

# 4. 前端本地运行（另开终端）
cd frontend
pnpm install
pnpm dev
```

访问：

- 前端页面：http://localhost:5173 （Vite dev server，`/api` 已代理到 `localhost:8000`）
- 后端 API 文档（Swagger）：http://localhost:8000/docs
- 健康检查：http://localhost:8000/health

### 模式 B：全容器化一键部署

```bash
# 方式一：显式激活 app profile
docker compose --profile app up -d

# 方式二：在根 .env 中取消注释 COMPOSE_PROFILES=app 后
docker compose up -d
```

将一并拉起 backend（:8000）、worker、frontend（:80）。

- 访问入口：**http://localhost**（前端 nginx 80 端口，`/api` 反向代理到 `backend:8000`）
- postgres 自建镜像（`kbase-postgres:pg16-paradedb`）基于 **ParadeDB 官方镜像**（内置 pg_search 0.25.6 原生 BM25 与 pgvector），构建很快；首次拉取基础镜像 `paradedb/paradedb:latest-pg16` 体积较大（约 500MB）。
- backend/worker 依赖 postgres/redis/rabbitmq/minio 健康检查通过后才会启动。

## 环境变量清单

以下为 `.env.example` 全部变量（复制为根目录 `.env` 后按需修改）。**使用问答功能必须正确填写 LLM 与 EMBEDDING 的 API 地址/密钥**；Rerank 默认关闭，需 `RERANK_ENABLED=true` 并配置接口后才启用。

### Compose 编排（机制变量，非业务变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `COMPOSE_FILE` | `docker/docker-compose.yml;docker/docker-compose.app.yml` | compose 自动加载的编排文件，已内置双文件配置；分隔符 Windows 为 `;`，Linux/macOS 为 `:` |
| `COMPOSE_PROFILES` | （注释，未启用） | 设为 `app` 后 `docker compose up -d` 即包含 backend/worker/frontend（模式 B 开关） |

### PostgreSQL

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `POSTGRES_USER` | `kbase` | 数据库用户 |
| `POSTGRES_PASSWORD` | `kbase123` | 数据库密码 |
| `POSTGRES_DB` | `kbase` | 数据库名 |
| `DATABASE_URL` | `postgresql+psycopg2://kbase:kbase123@localhost:5432/kbase` | SQLAlchemy 连接串，本地 IDE 运行 backend/worker 时使用；模式 B 容器内由 compose 覆盖 |

### Redis / RabbitMQ

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis 连接（缓存 / Celery 结果后端 / 会话上下文） |
| `RABBITMQ_DEFAULT_USER` | `kbase` | RabbitMQ 用户 |
| `RABBITMQ_DEFAULT_PASSWORD` | `kbase123` | RabbitMQ 密码 |
| `RABBITMQ_URL` | `amqp://kbase:kbase123@localhost:5672//` | Celery 消息队列地址 |

### MinIO（对象存储）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MINIO_ROOT_USER` | `minioadmin` | MinIO 用户（后端兼容读取为 Access Key） |
| `MINIO_ROOT_PASSWORD` | `minioadmin123` | MinIO 密码 |
| `MINIO_ENDPOINT` | `localhost:9000` | MinIO API 地址 |
| `MINIO_BUCKET` | `kbase` | 文档存储桶名 |
| `MINIO_SECURE` | `false` | 是否启用 HTTPS 连接 MinIO |

### JWT 认证

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `JWT_SECRET` | 示例随机串 | **生产环境务必更换**为随机长串，如 `python -c "import secrets;print(secrets.token_hex(32))"` |
| `JWT_EXPIRE_MINUTES` | `120` | Token 有效期（分钟） |

### LLM（问答生成，OpenAI 兼容接口，**必填**）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `LLM_API_BASE` | `https://api.openai.com/v1` | LLM 接口地址（任何 OpenAI 兼容服务均可） |
| `LLM_API_KEY` | `sk-your-api-key` | LLM 密钥 |
| `LLM_MODEL` | `gpt-4o-mini` | 问答生成模型名 |
| `REWRITE_MODEL` | （空） | 多轮改写专用模型（留空用 `LLM_MODEL`）；主模型为思考型时建议配轻量非思考模型 |

### Embedding（向量化，OpenAI 兼容接口，**必填**）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `EMBEDDING_API_BASE` | `https://api.openai.com/v1` | Embedding 接口地址 |
| `EMBEDDING_API_KEY` | `sk-your-api-key` | Embedding 密钥 |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | 向量化模型名 |
| `EMBEDDING_DIM` | `1536` | 向量维度，**必须与所选模型实际输出一致** |
| `EMBEDDING_BATCH_SIZE` | `10` | 批量向量化每批文本数（DashScope 上限 10，OpenAI 可到 2048） |
| `EMBEDDING_MAX_CONCURRENCY` | `4` | 嵌入分批请求的批间并发上限（多批并行加速大文档入库，注意供应商限流） |

### Rerank（可选，默认关闭）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `RERANK_ENABLED` | `false` | 是否启用重排（设 `true` 后需同时配置下方三项） |
| `RERANK_API_BASE` | （空） | 重排接口地址（任何 OpenAI 兼容 /rerank 服务） |
| `RERANK_API_KEY` | （空） | 重排密钥 |
| `RERANK_MODEL` | （空） | 重排模型名 |
| `RERANK_TIMEOUT` | `5` | 重排请求超时（秒）；超时/失败自动降级为 RRF 融合排序 |

### 检索与分块

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `FTS_CONFIG` | `chinese` | 仅 pg_search 不可用时的 FTS 回退配置；正常情况无需关注，不可用时回退 `simple` |
| `VECTOR_TOP_K` | `10` | 向量检索召回条数 |
| `KEYWORD_TOP_K` | `10` | BM25/关键词检索召回条数 |
| `RERANK_TOP_N` | `10` | RRF 融合 + Rerank 后进入 LLM 上下文的最终条数 |
| `RELEVANCE_THRESHOLD` | `0.35` | 相似度阈值（0~1）：向量路归一化相似度低于该值时 QA 可拒答 |
| `PDF_OCR_ENABLED` | `true` | PDF 图片 OCR（RapidOCR 本地识别扫描件/页面内图片）；关闭后纯图片 PDF 解析为空 |
| `CHUNK_SIZE` | `500` | 分块大小（字符），全局唯一配置点（所有知识库统一生效） |
| `CHUNK_OVERLAP` | `50` | 分块重叠（字符） |

### 业务限制与日志

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `RATE_LIMIT_PER_MIN` | `10` | 每分钟问答限流次数（每用户） |
| `MAX_UPLOAD_MB` | `50` | 单文件上传上限（MB） |
| `HISTORY_ROUNDS` | `5` | 问答带入的多轮历史轮数 |
| `LOG_LEVEL` | `INFO` | 日志级别 |

### LangSmith 追踪（可选）

配置 `LANGCHAIN_API_KEY` 并将 `LANGCHAIN_TRACING_V2=true` 后，问答与文档流水线自动上报 [LangSmith](https://smith.langchain.com/)（项目名 `LANGCHAIN_PROJECT`，默认 `kbase-qa`），可在网页查看：

- 每阶段耗时树：query 改写（多轮时）→ 混合检索（向量路含 `embed_query` 向量化 / `vector_sql` 数据库查询子节点，BM25 路）→ Rerank → Prompt 组装 → LLM 生成 → 引用组装；文档侧为 解析 → 分块 → 向量化
- 完整提示词与 LLM 输出内容（ChatOpenAI 调用自动追踪）
- 各检索通路返回的 chunk 内容与分数

未配置 Key 时追踪自动关闭，不影响任何功能。

## 初始账号

系统初始化脚本（`docker/postgres/init/02_schema.sql`，仅容器**首次**建库时执行）预置：

| 项目 | 值 |
| --- | --- |
| 用户名 | `admin` |
| 密码 | `admin123` |
| 角色 | `ADMIN`（系统管理员） |

预置角色共三种：`ADMIN`（系统管理员）、`KNOWLEDGE_MANAGER`（知识库管理员）、`EMPLOYEE`（普通员工）。

除预置账号外，系统支持**自助注册**：登录页「注册」Tab 或 `POST /api/auth/register`（用户名 3~32 位字母/数字/`_.-`，密码至少 6 位），注册成功即自动登录，角色固定为 `EMPLOYEE`，管理员可在「权限管理」中调整角色。

> **务必在正式上线后修改 admin 密码**（系统管理界面），并同步更换 `JWT_SECRET` 与各中间件默认口令。

## 验收测试

```bash
# 后端单元/接口测试（pytest 未包含在 requirements.txt 中，需先安装）
cd backend
pip install pytest
pytest tests/ -v
```

接口联测与压测脚本位于 `backend/scripts/`：

```bash
# 全流程 API 联测（登录→建库→上传→解析轮询→问答→多轮→越权→401）
# 认证/权限/知识库/上传硬断言；LLM 未配置或解析失败时问答步骤自动 SKIP 不判 FAIL
python backend/scripts/api_test.py --base-url http://localhost:8000 --admin-user admin --admin-pass admin123

# 问答接口压测（默认 50 并发，可调 -c/-n；报告 P50/P95/P99/吞吐/错误率）
python backend/scripts/load_test.py -c 50 -n 200 --base-url http://localhost:8000
```

## 目录结构

```
ai-knowledge-base/
├── .env.example                # 环境变量模板（复制为根目录 .env 后按需修改）
├── README.md
├── backend/
│   ├── app/
│   │   ├── main.py             # FastAPI 应用入口（含 /health）
│   │   ├── core/               # 全局配置(config.py)、数据库、日志
│   │   ├── models/             # SQLAlchemy 实体与枚举
│   │   ├── auth/               # 认证（JWT 登录/登出/当前用户）
│   │   ├── admin/              # 系统管理（用户/部门/角色/问答记录，仅 ADMIN）
│   │   ├── document/           # 知识库/文档管理、解析器（docx/PDF 分节聚合、表格 Markdown、OCR）、清洗、MinIO 客户端
│   │   ├── chunking/           # 文档分块（md 按标题 / 递归字符切分）
│   │   ├── embedding/          # 向量化与 pgvector 存储
│   │   ├── retrieval/          # 混合检索（向量+关键词+RRF 融合+Rerank）
│   │   ├── qa/                 # LLM 问答（SSE 流式/限流/拒答/审计）
│   │   ├── session/            # 会话管理（Redis 缓存上下文）
│   │   ├── tasks/              # Celery 应用与文档处理异步任务
│   │   └── common/             # 统一响应/依赖注入/审计/异常
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── src/                    # views / components / api / stores / router
│   ├── vite.config.js          # dev 端口 5173，/api 代理到 localhost:8000
│   ├── nginx.conf              # 容器内 nginx：80 端口，/api 反代 backend:8000
│   ├── package.json            # pnpm scripts: dev / build / preview
│   └── Dockerfile
└── docker/
    ├── docker-compose.yml      # 基础编排：仅中间件（模式 A）
    ├── docker-compose.app.yml  # 应用层覆盖：backend/worker/frontend（模式 B）
    └── postgres/
        ├── Dockerfile          # 基于 ParadeDB 官方镜像（内置 pg_search BM25 + pgvector）
        └── init/
            ├── 01_init.sql     # 扩展（vector/pg_search/pg_trgm，zhparser 存在时兜底安装）
            └── 02_schema.sql   # 业务表结构、预置角色与 admin 账号
```

## 常见问题（FAQ）

**1. BM25 检索（pg_search）说明与存量库升级**
- postgres 基础镜像为 **ParadeDB `paradedb/paradedb:latest-pg16`**（PG 16.15，pg_search 0.25.6，含 pgvector 0.8.4 与 lindera 中文分词）；BM25 索引建在业务表 `chunks(content)` 上，中文分词优先 `chinese_lindera`，不可用自动降级 `ngram`。
- **存量库升级**（旧镜像建的库无需重建数据卷）：需手动执行 `CREATE EXTENSION IF NOT EXISTS pg_search;`，并运行 `ALTER SYSTEM SET shared_preload_libraries = 'pg_search';` 后重启 postgres 容器（pg_search 必须预加载）；BM25 索引由应用首次检索时自动创建。新初始化的库由镜像与 init SQL 自动完成。
- pg_search 不可用时关键词检索自动回退 PG 内置 FTS（`FTS_CONFIG` 配置），问答与检索功能不中断。

**2. RabbitMQ 管理台**
http://localhost:15672 ，账号密码取 `.env` 的 `RABBITMQ_DEFAULT_USER` / `RABBITMQ_DEFAULT_PASSWORD`（默认 `kbase` / `kbase123`）。
若 Celery 报 `AccessRefused(403)`（Windows 下容器首启注入的默认密码可能带不可见字符），执行一次重置即可：
`docker exec kbase-rabbitmq rabbitmqctl change_password kbase kbase123`（密码会持久化在 `rabbitmqdata` 卷中，仅删除该卷重建后需要重新执行）。

**3. MinIO 控制台**
控制台 http://localhost:9001 （API 为 :9000），账号密码取 `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`（默认 `minioadmin` / `minioadmin123`）。

**4. 修改分块参数后需要重建索引**
`CHUNK_SIZE` / `CHUNK_OVERLAP` 的变更只对**之后入库**的文档生效；已入库文档的分块与向量不会自动重算，需删除后重新上传（触发解析→分块→向量化流水线），否则新旧分块粒度不一致会拉低检索质量。

**5. HTTPS 部署建议**
生产环境建议在反向代理层终结 TLS（最简单即在 frontend 的 nginx 或外层网关上配置），后端与中间件仍走内网明文。nginx 示例：

```nginx
server {
    listen 443 ssl;
    server_name your-domain.com;
    ssl_certificate     /etc/nginx/tls/fullchain.pem;
    ssl_certificate_key /etc/nginx/tls/privkey.pem;
    # location / 与 /api/ 配置同 frontend/nginx.conf，并将 /api 反代到 backend:8000
}
server {
    listen 80;
    return 301 https://$host$request_uri;
}
```
