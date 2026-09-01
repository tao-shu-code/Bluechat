# ============================================================
# PostgreSQL 16 + pgvector + pg_search（ParadeDB）
# 基础镜像 paradedb/paradedb:latest-pg16 已内置 pg_search（原生 BM25，
# 含 lindera 中文分词）与 pgvector，无需自行编译扩展。
# 说明：QA 关键词通路已切换为 pg_search BM25，zhparser 不再使用。
# ============================================================
FROM paradedb/paradedb:latest-pg16

EXPOSE 5432
