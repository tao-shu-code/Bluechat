-- ============================================================
-- 01_init.sql : 数据库扩展初始化
-- 由 postgres 容器首次初始化时自动执行（docker-entrypoint-initdb.d）
-- 基础镜像 paradedb/paradedb:latest-pg16 内置 pg_search（BM25）与 pgvector
-- ============================================================

-- pgvector：向量检索扩展（存储 embedding，支持 HNSW/IVFFlat 索引）
CREATE EXTENSION IF NOT EXISTS vector;

-- pg_search：ParadeDB 全文检索扩展（原生 BM25 索引 + lindera 中文分词）
CREATE EXTENSION IF NOT EXISTS pg_search;

-- pg_trgm：三元组模糊匹配扩展（文件名/关键词 LIKE 模糊检索加速）
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- zhparser 兜底：若基础镜像中存在（旧镜像）则安装，作为 BM25 不可用时
-- keyword_search FTS 回退路径的中文解析器；新镜像无 zhparser 时自动跳过
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'zhparser') THEN
        CREATE EXTENSION IF NOT EXISTS zhparser;
        CREATE TEXT SEARCH CONFIGURATION chinese (PARSER = zhparser);
        ALTER TEXT SEARCH CONFIGURATION chinese
            ADD MAPPING FOR n, v, a, i, e, l, j WITH simple;
    ELSE
        RAISE NOTICE 'zhparser not available, skip creating text search configuration "chinese"';
    END IF;
END
$$;
