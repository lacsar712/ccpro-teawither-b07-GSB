# TeaWither-01 · 茶萎凋台账

Django 5 + PostgreSQL 服务端渲染应用：Templates + HTMX + 自定义 CSS，无 Vue/React SPA。

## 技术栈

- Django 5、PostgreSQL
- Session 登录
- HTMX（CDN）局部刷新列表
- Docker Compose：`web` + `db`

## 端口与数据库

| 服务 | 端口 |
|------|------|
| Web  | **4100** |
| Postgres | **5440**（容器内 5432） |

数据库账号：`teawither` / `teawither` / 库名 `teawither`

## 快速启动

```bash
cd TeaWither/TeaWither-01
docker compose up --build -d
```

浏览器打开：http://localhost:4100

演示账号：

- `admin` / `123456`（超级用户）
- `witherer` / `123456`（普通用户）

容器启动时会自动：`migrate` → `seed_data` → `collectstatic` → `gunicorn`

## 本地开发（可选）

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
# 确保本机 Postgres 监听 5440，或先 docker compose up -d db
set POSTGRES_HOST=localhost
set POSTGRES_PORT=5440
python manage.py migrate
python manage.py seed_data
python manage.py runserver 0.0.0.0:4100
```

## 业务模型

1. **Garden（茶园）**：`name`、`altitudeBand`、`notes`
2. **Trough（萎凋槽）**：归属茶园、`troughCode`、`cultivar`、`loadKg`、状态 `loading|withering|ready`；同一茶园内槽位编号唯一
3. **WitherBatch（萎凋批次）**：归属槽位、`startedAt`、`targetMoisture`、`actualMoisture`（可空）、`rollGrade`

**业务规则**：将槽位状态设为 `ready`（可下槽）时，若最新批次的 `actualMoisture` 为空或大于 40，抛出中文 `ValidationError`。

**「可下槽」锁定规则**：

- **触发**：槽位状态为 `ready`（可下槽）时，其**最新批次**进入锁定态。
- **锁定字段**：最新批次的 `targetMoisture`（目标含水率）与 `startedAt`（开始时间）禁止修改，尝试保存会被后端以中文说明拒绝。
- **仍可修改**：`actualMoisture`（实测含水率）与 `rollGrade`（揉捻等级）可继续编辑，走原有校验。
- **非最新批次不受此锁**：历史批次可自由编辑。
- **解除**：槽位状态改回 `loading`（装叶中）或 `withering`（萎凋中）后锁定即解除，解除后目标含水率可正常修改。
- **同源判定**：锁定判定集中在 `apps/gardens/models.py` 的 `latest_batch_of` / `is_trough_locked` / `is_batch_locked` / `locked_batch_fields`，改态入口（`Trough.clean`）、批次编辑保存（`WitherBatch.clean`）、编辑页只读渲染与槽/批次列表的「锁定」徽标全部共用；首页「可下槽」统计与槽列表 `?status=ready` 筛选同一口径，可点击对账。

## 种子数据

```bash
python manage.py seed_data
```

幂等：已有茶园则只保证账号存在。亦可在环境变量 `TEAWITHER_AUTO_SEED=1` 时于 `post_migrate` 自动播种。

## 目录结构

```
TeaWither-01/
  manage.py
  requirements.txt
  Dockerfile
  entrypoint.sh
  docker-compose.yml
  config/           # 项目配置
  apps/gardens/     # 模型、视图、种子命令
  templates/        # Django 模板
  static/css/       # 自定义样式（茶绿色顶栏）
```
