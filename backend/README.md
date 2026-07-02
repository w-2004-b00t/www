# EduAgent Studio 后端

这是项目的 FastAPI 后端，负责课程、画像、资源、Agent、测评、错题本、路径、报告和向量化推荐。

## 启动

```bash
pip install -r requirements.txt
python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8001
```

## 可选能力：真实画像向量化

如果你想启用 **BGE-M3 / bge-large-zh** + **ChromaDB / Milvus**，再执行：

```bash
pip install -r requirements-vector.txt
```

然后在 `backend/.env` 中配置：

### 本地向量库方案（推荐）

```env
VECTOR_EMBEDDING_MODE=real
EMBEDDING_MODEL=BAAI/bge-m3
VECTOR_STORE=chroma
CHROMA_PATH=backend/data/chroma
```

> 首次切到 `VECTOR_EMBEDDING_MODE=real` 时，如果本机还没有缓存 BGE 模型，启动会先下载模型权重，时间会比 fallback 长一些。

### Milvus 方案

```env
VECTOR_EMBEDDING_MODE=real
EMBEDDING_MODEL=BAAI/bge-m3
VECTOR_STORE=milvus
MILVUS_URI=http://127.0.0.1:19530
MILVUS_TOKEN=
```

## 说明

- `VECTOR_EMBEDDING_MODE=fallback` 时，系统会使用本地哈希向量兜底，保证不装额外依赖也能运行。
- `VECTOR_EMBEDDING_MODE=real` 时，系统会加载 `sentence-transformers`，并优先使用配置的真实向量模型。
- 画像确认后会自动写入向量库，资源中心会按画像相似度排序，并展示“为什么推荐”。

## 核心接口

- `GET /api/health`
- `POST /api/auth/login`
- `GET /api/profile/me`
- `POST /api/profile/extract`
- `POST /api/profile/confirm`
- `POST /api/knowledge/search`
- `POST /api/resources/generate`
- `GET /api/tasks/{task_id}`
- `GET /api/resources`
- `GET /api/learning-paths/me`
- `POST /api/tutor/chat`
- `POST /api/assessments/generate`

## 演示账号

- 学生：`student_demo`
- 教师：`teacher_demo`
- 管理员：`admin_demo`

密码均为：`123456`

## 推荐运行模式

1. 先用默认 fallback 跑通全链路
2. 再切到 `VECTOR_EMBEDDING_MODE=real`
3. 最后把 `VECTOR_STORE` 切到 `chroma` 或 `milvus`
