-- E0 开发环境初始化（PostgreSQL 启动时自动执行）
-- 依据：60.90.03 §3.7 事实与向量库 = PostgreSQL 16.6 + pgvector 0.8.0

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 验证：SELECT * FROM pg_extension WHERE extname IN ('vector','uuid-ossp');
