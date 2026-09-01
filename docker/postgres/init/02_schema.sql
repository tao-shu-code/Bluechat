-- ============================================================
-- 02_schema.sql : 业务表结构与预置数据
-- 由 postgres 容器首次初始化时自动执行（docker-entrypoint-initdb.d）
-- 主键统一使用 UUID（gen_random_uuid()，PG13+ 内置）
-- ============================================================

-- ---------- 枚举类型 ----------
CREATE TYPE kb_visibility AS ENUM ('ALL', 'DEPARTMENT', 'USER');
CREATE TYPE document_status AS ENUM ('UPLOADED', 'PARSING', 'CHUNKING', 'EMBEDDING', 'READY', 'FAILED');
CREATE TYPE message_role AS ENUM ('user', 'assistant');

-- ---------- 部门表（支持树形结构） ----------
CREATE TABLE departments (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name       VARCHAR(128) NOT NULL,
    parent_id  UUID REFERENCES departments(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- 用户表 ----------
CREATE TABLE users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username      VARCHAR(64) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    display_name  VARCHAR(128),
    email         VARCHAR(255),
    department_id UUID REFERENCES departments(id) ON DELETE SET NULL,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- 角色表 ----------
CREATE TABLE roles (
    id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code VARCHAR(64) NOT NULL UNIQUE,
    name VARCHAR(128) NOT NULL
);

-- ---------- 用户-角色关联 ----------
CREATE TABLE user_roles (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_id UUID NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, role_id)
);

-- ---------- 知识库表 ----------
CREATE TABLE knowledge_bases (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(255) NOT NULL,
    description     TEXT,
    visibility      kb_visibility NOT NULL DEFAULT 'ALL',
    embedding_model VARCHAR(128),
    created_by      UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- 知识库访问控制（部门/用户级 ACL，二选一） ----------
CREATE TABLE kb_acl (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kb_id         UUID NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    department_id UUID REFERENCES departments(id) ON DELETE CASCADE,
    user_id       UUID REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT kb_acl_target_required CHECK (department_id IS NOT NULL OR user_id IS NOT NULL)
);

-- ---------- 文档表 ----------
CREATE TABLE documents (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kb_id         UUID NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    filename      VARCHAR(512) NOT NULL,
    object_key    VARCHAR(1024) NOT NULL,
    file_type     VARCHAR(32),
    file_size     BIGINT,
    status        document_status NOT NULL DEFAULT 'UPLOADED',
    error_message TEXT,
    uploaded_by   UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- 文档分片表 ----------
CREATE TABLE chunks (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    content     TEXT NOT NULL,
    title_path  TEXT,
    page_number INTEGER,
    chunk_index INTEGER NOT NULL,
    token_count INTEGER,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- 会话表 ----------
CREATE TABLE conversations (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title      VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- 消息表 ----------
CREATE TABLE messages (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id   UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role              message_role NOT NULL,
    content           TEXT NOT NULL,
    sources           JSONB,
    -- 问答 token 用量（assistant 消息记录本次回答的 LLM 用量；历史/拒答消息为 NULL）
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    total_tokens      INTEGER,
    -- 回答反馈：like / dislike（NULL=未评价）
    feedback          VARCHAR(16),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- 审计日志 ----------
CREATE TABLE audit_logs (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID REFERENCES users(id) ON DELETE SET NULL,
    action     VARCHAR(128) NOT NULL,
    detail     JSONB,
    ip         VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- 常用索引 ----------
CREATE INDEX idx_users_department      ON users(department_id);
CREATE INDEX idx_roles_code            ON roles(code);
CREATE INDEX idx_user_roles_role       ON user_roles(role_id);
CREATE INDEX idx_kb_acl_kb             ON kb_acl(kb_id);
CREATE INDEX idx_kb_acl_department     ON kb_acl(department_id);
CREATE INDEX idx_kb_acl_user           ON kb_acl(user_id);
CREATE INDEX idx_documents_kb          ON documents(kb_id);
CREATE INDEX idx_documents_status      ON documents(status);
CREATE INDEX idx_chunks_document       ON chunks(document_id);
CREATE INDEX idx_conversations_user    ON conversations(user_id);
CREATE INDEX idx_messages_conversation ON messages(conversation_id);
CREATE INDEX idx_audit_logs_user       ON audit_logs(user_id);
CREATE INDEX idx_audit_logs_created    ON audit_logs(created_at);

-- ---------- 预置角色 ----------
INSERT INTO roles (code, name) VALUES
    ('ADMIN',             '系统管理员'),
    ('KNOWLEDGE_MANAGER', '知识库管理员'),
    ('EMPLOYEE',          '普通员工')
ON CONFLICT (code) DO NOTHING;

-- ---------- 预置管理员 ----------
-- 用户名: admin / 密码: admin123（bcrypt cost=12，由 python passlib/bcrypt 生成）
INSERT INTO users (username, password_hash, display_name, is_active)
VALUES ('admin', '$2b$12$Q.aN3cXa3d7YiWJPgmweSOn16ogyKtB5w6thOhvfWHP9vlBnU6VZe', '系统管理员', TRUE)
ON CONFLICT (username) DO NOTHING;

-- 关联 ADMIN 角色
INSERT INTO user_roles (user_id, role_id)
SELECT u.id, r.id
FROM users u, roles r
WHERE u.username = 'admin' AND r.code = 'ADMIN'
ON CONFLICT DO NOTHING;
