# Claude Code Usage Monitor

每小时自动扫描 Claude Code 本地会话日志，汇总 token 用量，通过邮件发送可视化仪表盘。

## 功能

- **自动扫描** - 增量读取 `~/.claude/projects/` 下的 JSONL 会话文件，解析 token 用量
- **SQLite 存储** - 按小时/模型维度聚合数据，支持断点续扫
- **邮件仪表盘** - 精美 HTML 邮件，包含：
  - 过去 1 小时用量摘要（含环比变化）
  - 本周 output/input/请求数概览
  - 7 天 x 24 小时使用热力图
  - 每日用量统计表
- **配额追踪** - 支持设置周 output 配额和短时窗口配额
- **智能发送** - 仅在有活动时发送邮件，重置日前自动发送周报

## 依赖

- Python 3
- [jinja2](https://pypi.org/project/Jinja2/) - 模板渲染
- [msmtp](https://marlam.de/msmtp/) - 邮件发送

## 安装

```bash
# 使用默认邮箱安装
./install.sh

# 自定义邮箱
./install.sh your@email.com

# 自定义邮箱和安装目录
./install.sh your@email.com /path/to/hooks
```

安装脚本会：
1. 检查依赖（python3、msmtp、jinja2）
2. 复制脚本到 hooks 目录（默认 `~/.claude/hooks/`）
3. 初始化 SQLite 数据库并执行首次扫描
4. 添加 cron 任务，每小时整点执行

## 卸载

```bash
./uninstall.sh
```

会移除 cron 任务和脚本文件，保留 `usage.db` 数据库作为备份。

## 配置

配置存储在 SQLite 数据库的 `config` 表中，可通过 SQL 修改：

```bash
DB="$HOME/.claude/hooks/usage.db"

# 修改周 output 配额（默认 6000000）
sqlite3 "$DB" "UPDATE config SET value='8000000' WHERE key='weekly_output_quota'"

# 修改收件邮箱
sqlite3 "$DB" "UPDATE config SET value='new@email.com' WHERE key='to_email'"

# 修改重置时间（默认周四 13:59）
sqlite3 "$DB" "UPDATE config SET value='4' WHERE key='reset_weekday'"   # 0=周一, 6=周日
sqlite3 "$DB" "UPDATE config SET value='13' WHERE key='reset_hour'"
sqlite3 "$DB" "UPDATE config SET value='59' WHERE key='reset_minute'"

# 修改短时窗口（默认 5 小时）
sqlite3 "$DB" "UPDATE config SET value='3' WHERE key='short_window_hours'"
```

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `reset_weekday` | `4` (周五) | 周期重置的星期几（0=周一） |
| `reset_hour` | `13` | 重置时刻（小时） |
| `reset_minute` | `59` | 重置时刻（分钟） |
| `timezone` | `Asia/Shanghai` | 时区 |
| `weekly_output_quota` | `6000000` | 周 output token 配额 |
| `weekly_input_quota` | （空） | 周 input token 配额 |
| `short_window_hours` | `5` | 短时窗口小时数 |
| `short_output_quota` | （空） | 短时窗口 output 配额 |
| `to_email` | `w_wt_t@126.com` | 收件邮箱 |

## 文件说明

```
usage_monitor.py      # 主程序：扫描、聚合、渲染、发邮件
usage_template.html   # Jinja2 邮件模板
install.sh            # 安装脚本
uninstall.sh          # 卸载脚本
```
