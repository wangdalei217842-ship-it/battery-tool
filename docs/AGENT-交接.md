# Agent 交接说明

> 给接手本项目的 AI agent / 开发者的操作手册。
> 读完这页就能独立完成「改代码 → 推送 → 上线」，不需要额外上下文。
>
> 文中 `<SERVER_IP>`、`<YOUR_DOMAIN>` 是占位符，真实值见项目根目录的 `DEPLOY.md.local`（不进仓库）。

---

## 〇、先看这条：四条红线

**违反任何一条都可能造成数据丢失或信息泄露，先确认再动手。**

| # | 红线 | 为什么 |
|---|---|---|
| 1 | **绝不提交 `data/`、`*.db`、`admin-password.txt`、`*.local`** | 里面是真实账号、密码哈希、报账、药品、测试数据。公开仓库泄露后不可挽回 |
| 2 | **绝不在服务器上直接改代码** | 下次 `git pull` 会被覆盖，改动白做。要改只在本地改 |
| 3 | **发布前必须备份 `data/`** | 代码可回滚，数据不行 |
| 4 | **改 `static/app.js` 必须同步升 `?v=NN`** | 不升浏览器用缓存，改了像没改（本项目最高频的坑） |

---

## 一、环境信息

### 本地开发

| 项 | 值 |
|---|---|
| 项目路径 | `D:\vibe\battery-tracker`（Windows） |
| 启动（仅本机） | 双击 `启动-仅本机.bat` → `http://127.0.0.1:8787` |
| 启动（局域网） | 双击 `启动.bat`，手机连同一 WiFi 访问 `http://电脑IP:8787` |
| 本地数据 | `data\`，独立测试库，和线上完全隔离 |
| 技术栈 | 后端纯 Python 3.9+ 标准库；前端原生 JS + Canvas；无任何第三方依赖 |

### 代码仓库

| 项 | 值 |
|---|---|
| GitHub | `https://github.com/wangdalei217842-ship-it/battery-tool` |
| 可见性 | **Public** |
| 分支 | `main` |
| 认证方式 | SSH，密钥 `C:\Users\25593\.ssh\id_ed25519_github`，已写进 `~/.ssh/config` |
| 提交身份 | `wangdalei217842-ship-it` / `wangdalei217842@gmail.com` |

### 线上服务器

| 项 | 值 |
|---|---|
| 主机 | `root@<SERVER_IP>`（腾讯云轻量，Ubuntu） |
| SSH 密钥 | `C:\Users\25593\.ssh\battery_deploy` |
| 项目目录 | `/opt/battery-tracker`（**这是 git 仓库**） |
| 运行用户 | `battery`（systemd 服务名 `battery-tracker`） |
| 域名 | `https://<YOUR_DOMAIN>`（Let's Encrypt，自动续期） |
| 数据目录 | `/opt/battery-tracker/data`（**不在 git 里**） |
| 备份目录 | `/opt/backups/`、`/opt/battery-tracker/data/backups/` |

**SSH 登录命令**：

```bash
ssh -i C:\Users\25593\.ssh\battery_deploy root@<SERVER_IP>
```

---

## 二、项目结构

```
D:\vibe\battery-tracker\
├── server.py          HTTP 服务 + 全部路由（63KB，最核心）
├── store.py           通道/分组/快照/电池 数据层
├── accounts.py        账号、会话、角色
├── tests.py           充放电测试（含 dQ/dV 计算）
├── ndax.py            新威 .ndax 二进制解析
├── expenses.py        报账
├── lab.py             药品台账
├── methods.py         实验方法 + 附件
├── cli.py             命令行工具
├── static/            前端（改这里）
│   ├── index.html     页面骨架（含 ?v=NN 版本号）
│   ├── app.js         全部前端逻辑（136KB）
│   ├── style.css
│   └── sw.js          Service Worker（也含版本号）
├── deploy/            部署脚本与 nginx 模板
├── docs/              文档（发布流程、域名配置等）
└── data/              ⚠️ 运行时数据，不进仓库
```

### 数据结构速查

| 库 | 位置 | 内容 |
|---|---|---|
| `accounts.db` | `data/` | 用户、角色（admin/user）、内测标记（beta）、会话 |
| `shared.db` | `data/` | 报账（expenses）、药品（drugs）、实验方法（methods） |
| `users/u{id}.db` | `data/users/` | 每个用户一个独立库：通道、分组、快照、电池、测试数据 |
| `uploads/` | `data/` | 发票 PDF、方法附件 |

**权限模型**：
- `role='admin'` → 全部功能
- `role='user'` + `beta=1` → 报账、药品（但不能删报账、不能改报销状态）
- `role='user'` + `beta=0` → 只有看板、报送等基础功能

---

## 三、发布流程

### 1. 本地改 + 调试

```bash
cd D:\vibe\battery-tracker
# 改代码，双击 启动-仅本机.bat 验证
```

改前端时**先升版本号**：

```bash
# static/index.html 和 static/sw.js 里的 ?v=56 改成 57
# sw.js 里的 bt-v56 改成 bt-v57
```

### 2. 提交推送

```bash
git status                    # 确认没有 data/ 混进来
git add -A
git commit -m "feat: 说明改了什么"
git push
```

### 3. 服务器上线

```bash
# ① 备份数据（必做）
ssh -i ~/.ssh/battery_deploy root@<SERVER_IP>
cd /opt/battery-tracker
tar czf /opt/backups/data-$(date +%Y%m%d-%H%M%S).tar.gz data/

# ② 拉代码（注意必须用 battery 用户）
sudo -u battery git pull

# ③ 重启
systemctl restart battery-tracker
systemctl is-active battery-tracker

# ④ 冒烟
curl -s http://127.0.0.1:8787/api/ping
```

**一条命令搞定后三步**：

```bash
ssh -i ~/.ssh/battery_deploy root@<SERVER_IP> \
  "cd /opt/battery-tracker && sudo -u battery git pull && systemctl restart battery-tracker && sleep 2 && systemctl is-active battery-tracker && curl -s http://127.0.0.1:8787/api/ping"
```

---

## 四、发布后验证清单

按顺序过一遍，全绿才算发布成功：

```bash
# 1. 服务活着
systemctl is-active battery-tracker              # 期望 active
curl -s http://127.0.0.1:8787/api/ping           # 期望 {"ok": true, ...}

# 2. 站点能开
curl -s -o /dev/null -w "%{http_code}\n" https://<YOUR_DOMAIN>/   # 期望 200

# 3. 没有启动报错
journalctl -u battery-tracker -n 30 --no-pager | grep -i "error\|traceback"

# 4. 数据还在（关键！）
ls /opt/battery-tracker/data/
du -sh /opt/battery-tracker/data/
```

**功能级验证**（浏览器）：

| 检查项 | 怎么做 |
|---|---|
| 登录 | 任意账号能进主界面 |
| 看板 | 通道卡片、分组正常显示 |
| 报账 | 列表能加载、金额合计正确 |
| 药品 | 能搜索、能登记 |
| 移动端 | 手机打开，曲线弹窗能全屏、能横滑 |

---

## 五、常见坑（都踩过）

### ① 改完前端没生效

**现象**：改了 `app.js`，刷新页面还是老样子。
**原因**：`index.html` 里 `app.js?v=56` 没改，浏览器用缓存。
**解法**：改 `app.js` 时把 `index.html` 和 `sw.js` 的版本号一起 +1，三处要一致。

### ② 服务器 git 报 dubious ownership

**现象**：`fatal: detected dubious ownership in repository at '/opt/battery-tracker'`
**原因**：目录属于 `battery` 用户，但用 `root` 执行 git。
**解法**：**用 `sudo -u battery git ...`**，或者给当前用户加白名单：

```bash
git config --global --add safe.directory /opt/battery-tracker
```

### ③ 登录后跳回登录页 / 一直转圈

**根因**：302 响应缺 `Content-Length: 0`，HTTP/1.1 长连接下浏览器会一直等 body 直到超时（60 秒）。
**已修复**，但如果以后新增 302 跳转，**必须带 `Content-Length: 0`**。

### ④ 手机上打不开曲线

**原因**：卡片有两套模板（`cardHtmlDense` 紧凑版 / `cardHtmlNormal` 普通版），加按钮要**两处都加**。
**已修复**，新增卡片按钮时注意。

### ⑤ 通道编号"重复"

**设计**：编号只在**同一分组内**唯一，跨分组可以都用 `2-1`。
**注意**：`ux_channel_group_code` 是 `(COALESCE(group_id,0), code)` 组合唯一索引。

### ⑥ `store.add_snapshot()` 写完没生效

**原因**：这个函数**不 commit**，靠调用方提交。
**解法**：调用后必须 `conn.commit()`。

### ⑦ 同名 id 冲突

**现象**：弹窗内容渲染到看不见的地方。
**原因**：页面上和弹窗里有同名 `id`，`document.querySelector` 抓到第一个。
**解法**：用作用域选择器 `#modalBody #xxx`。

---

## 六、回滚

### 代码回滚

```bash
cd /opt/battery-tracker
sudo -u battery git log --oneline -10        # 找目标版本
sudo -u battery git reset --hard <commit>
systemctl restart battery-tracker
```

### 数据回滚

```bash
cd /opt/battery-tracker
ls /opt/backups/                              # 找备份
tar xzf /opt/backups/data-<时间>.tar.gz       # 覆盖 data/
chown -R battery:battery data/
systemctl restart battery-tracker
```

### 完整回退到"改造前"

```bash
cd /opt
tar xzf /opt/backups/code-before-git-20261112-171522.tar.gz
chown -R battery:battery /opt/battery-tracker
systemctl restart battery-tracker
```

---

## 七、当前基线（2026-11-12 17:22）

供迁移时核对，如果对不上说明状态已变：

```
本地 commit      0247d22  docs: 补充本地开发到线上发布的完整流程
服务器 commit    0247d22  （与本地一致）
git 跟踪文件     47 个
前端版本号       app.js?v=56
用户数           22
报账记录         9 条
药品记录         213 条
data 总大小      ~51 MB
站点状态         https 200
```

---

## 八、给 agent 的一段话（可直接复制）

```
你在维护一个实验室用的电池测试数据管理系统。

项目：D:\vibe\battery-tracker（Windows 本地）
仓库：https://github.com/wangdalei217842-ship-it/battery-tool （Public，main 分支）
服务器：root@<SERVER_IP>，密钥 C:\Users\25593\.ssh\battery_deploy
线上目录：/opt/battery-tracker（git 仓库，服务名 battery-tracker，运行用户 battery）

工作流固定为：本地改 → 本地调试 → git push → 服务器 git pull + 重启。

四条红线：
1. 绝不提交 data/、*.db、admin-password.txt、*.local
2. 绝不在服务器上直接改代码
3. 发布前必须 tar 备份 data/
4. 改 static/app.js 必须同步把 index.html 和 sw.js 里的 ?v=NN 升一号

改完必须在浏览器里实际验证（本地 http://127.0.0.1:8787 或线上域名），
不能只看代码写完就报告完成。

详细规范见项目里的 docs/发布流程.md 和 docs/AGENT-交接.md。
```
