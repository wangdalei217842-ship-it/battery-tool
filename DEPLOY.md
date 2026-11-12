# 部署到自己的服务器（Ubuntu + 域名 + HTTPS）

把台账放到腾讯云/阿里云的 Ubuntu 主机上，用 `https://你的域名` 访问，并且能像 App 一样装到桌面和手机主屏。

全程大约 15 分钟，其中 10 分钟是等 DNS 和证书生效。

---

## 0. 先确认三件事，否则一定会卡住

| 检查项 | 怎么做 | 为什么 |
|---|---|---|
| **域名已解析到服务器** | 域名后台加一条 A 记录，指向服务器公网 IP。`ping 你的域名` 能返回那个 IP | 证书申请要靠它验证域名归属 |
| **安全组放行了 80 / 443** | 腾讯云控制台 → 轻量/云服务器 → 防火墙 → 添加规则，TCP 80、443 | 不放行的话外网根本连不上，表现为一直转圈 |
| **域名已 ICP 备案** | 在腾讯云备案系统查。国内主机没备案，80/443 会被运营商拦截 | 这是国内主机的硬性规定，不是技术问题 |

> 备案没下来之前想先看效果：直接用 `http://服务器IP:8787` 也能访问（临时开一下安全组的 8787）。正式用还是得走域名。

---

## 1. 把代码传到服务器

**方式 A：命令行（Windows 10/11 自带 ssh / scp）**

在本地打开 PowerShell 或 CMD：

```powershell
scp -r D:\vibe\battery-tracker root@服务器IP:/root/
```

如果提示 `Permission denied`，说明你登录服务器的用户名不是 root，换成你的用户名，然后后面所有 `sudo` 命令照用。

**方式 B：WinSCP / FileZilla 图形界面**

连上服务器，把整个 `battery-tracker` 文件夹拖到 `/root/` 下即可。

> 传的时候 `data/` 会一起上传。**如果服务器上已经有正式数据，不要直接覆盖 data 目录**——只传代码文件（`server.py`、`store.py`、`accounts.py`、`static/`），数据文件用 `scp` 单独处理。确认要正式用了，再在网页「设置 → 危险操作 → 清空全部数据」。

---

## 2. 一条命令完成部署

SSH 登录服务器：

```bash
ssh root@服务器IP
cd /root/battery-tracker
sudo bash deploy/install.sh 你的域名 你的邮箱
```

例如：

```bash
sudo bash deploy/install.sh battery.example.com me@qq.com
```

脚本会自动做这些事（不用你手动装任何东西）：

1. 安装 python3 / nginx / certbot / rsync
2. 建一个不能登录的系统用户 `battery` 来跑程序（不用 root 跑业务）
3. 把程序放到 `/opt/battery-tracker`，导入你的数据库
4. 首次启动自动创建管理员账号 `admin`，随机密码写进 `data/admin-password.txt`
5. 注册开机自启的 systemd 服务，崩了自动拉起
6. 配好 Nginx 反向代理 + 登录接口限速（防密码爆破）
7. 申请 Let's Encrypt 免费证书，自动续期，HTTP 自动跳 HTTPS
8. 配每日 03:10 自动备份，保留 30 天

跑完会打印类似：

```
==================== 部署完成 ====================
  访问网址：  https://battery.example.com
  管理员账号：admin / yu5548v869
  用户数据：  /opt/battery-tracker/data/users/
```

第一次打开网页用 `admin` + 那个密码登录，**登录后立刻在右上角头像菜单里改掉密码**。新增用户也在那里（用户管理）。

---

## 3. 装成桌面 App / 手机 App（PWA）

不需要下载任何东西，浏览器自带「安装」。

| 平台 | 操作 | 效果 |
|---|---|---|
| **Windows** Chrome / Edge | 打开网址 → 地址栏右侧的「安装」图标 ⊕（或菜单 → 应用 → 安装此站点） | 桌面出现图标，双击打开是**独立窗口**，没有地址栏和标签页，像原生程序 |
| **macOS** Chrome / Edge | 同上，菜单 → 应用 → 安装 | 出现在启动台 / 应用程序文件夹 |
| **安卓** Chrome | 右上角菜单 → 「添加到主屏幕」/「安装应用」 | 主屏图标，全屏打开 |
| **iPhone / iPad** Safari | 底部分享 → 「添加到主屏幕」 | 主屏图标，全屏打开（iOS 只认 Safari，Chrome 不行） |

装好之后：

- 图标就是那颗薄荷绿的电池
- 长按图标有快捷菜单：**通道看板 / 状态报送 / 电池档案** 直接跳
- 断网时能打开界面（但看不到最新数据，会提示加载失败）—— 数据永远走网络，不会给你看缓存里的旧台账
- 服务端更新后，App 下次打开自动换新版本，不用重装

### 为什么不做成微信小程序

你提到的是「上线微信小程序然后添加到桌面」，这条路和 PWA 是**两套东西**，代价差很多：

| | PWA（现在这套） | 微信小程序 |
|---|---|---|
| 现有前端代码 | 直接复用，加了 3 个文件 | **必须重写**（WXML/WXSS，`fetch` 改 `wx.request`），等于把界面再做一遍 |
| 上线门槛 | 有域名和 HTTPS 就能用 | 要注册小程序账号、填类目、提交审核，内部工具类目容易被打回 |
| 添加到桌面 | 浏览器直接装，桌面/手机都有图标 | 只有安卓能加；点了会**先打开微信**再进小程序，iOS 只能从微信里找 |
| 更新 | 改完刷新就行 | 每次都要重新提审 |
| 数据 | 你自己的 SQLite | 必须 HTTPS 域名且加入「服务器域名白名单」 |

**结论**：想「像 App 一样打开」，PWA 已经完全满足，而且今天就能用。真需要小程序（比如要给群里几十个同事用、要微信登录、要发通知），那是另一个项目，可以在这个后端上加一层小程序前端复用同一套 API —— 需要时再说，先把现在这套跑稳。

---

## 4. 日常运维（都是几条命令）

```bash
# 看服务状态
systemctl status battery-tracker

# 看实时日志（Ctrl+C 退出）
journalctl -u battery-tracker -f

# 重启
systemctl restart battery-tracker

# 改密码：推荐直接在网页右上角头像菜单里改
# 忘了密码只能在服务器上重置（把 u1 换成对应用户的 id）：
cd /opt/battery-tracker && sudo -u battery python3 -c "import accounts; accounts.change_password(1, '新密码')"

# 手动备份一次（备份账号库 + 所有人的数据）
sudo /opt/battery-tracker/deploy/backup.sh

# 看有哪些备份
ls -lht /var/backups/battery-tracker/ | head
```

### 升级代码

本地改完代码，重新上传（`scp -r` 或 WinSCP），然后：

```bash
cd /root/battery-tracker
sudo bash deploy/update.sh
```

`update.sh` 会先备份数据库再同步代码，`data/` 目录不动。

> 界面上看不到变化时：把 `static/sw.js` 第一行的 `VERSION` 和 `SHELL` 里的 `?v=N`，以及 `static/index.html` 里引用 `style.css?v=N` / `app.js?v=N` 的版本号**一起改成同一个新数字**再发布，强制所有客户端换缓存。只改其中一个会导致部分用户卡在旧样式上（浏览器对 CSS/JS 有 1 小时 HTTP 缓存）。

### 恢复某个用户的备份

每次备份是一个目录，里面 `accounts.db` 是账号，`u1.db u2.db…` 是各人的数据。只恢复某个人：

```bash
systemctl stop battery-tracker
sudo cp /var/backups/battery-tracker/db-20261018-031000/u1.db /opt/battery-tracker/data/users/u1.db
sudo chown battery:battery /opt/battery-tracker/data/users/u1.db
systemctl start battery-tracker
```

要整站回滚（账号也一起回），把 `accounts.db` 也用同样方式覆盖回去。

---

## 5. 出问题了先看这里

| 现象 | 原因 | 解决 |
|---|---|---|
| 浏览器 502 Bad Gateway | 后端程序没起来 | `systemctl status battery-tracker` + `journalctl -u battery-tracker -n 50` |
| 一直转圈、连不上 | 安全组没放行 80/443 | 腾讯云控制台加防火墙规则 |
| 证书申请失败 | 域名没解析 / 80 被占 / 未备案 | 先 `dig +short 你的域名` 看是不是服务器 IP，确认后再补跑：`certbot --nginx -d 域名 --redirect` |
| 打开是「网站未备案」提示页 | 域名没备案 | 去腾讯云备案，或用 IP 临时访问 |
| 报送时间差了 8 小时 | 服务器时区是 UTC | `sudo timedatectl set-timezone Asia/Shanghai` 然后重启服务（安装脚本已自动设好） |
| 输密码后一直回到登录页 | 浏览器禁用了 Cookie / 用了无痕模式 | 换普通窗口，或把站点加进允许 Cookie 的名单 |
| 手机装不了 PWA | 用的 iOS 第三方浏览器 | iOS 必须用 Safari 的「添加到主屏幕」 |
| 提示 429 Too Many Requests | 登录接口限速生效（1 分钟 > 12 次） | 等 1 分钟再试，属于正常保护 |

---

## 6. 这台服务器上到底跑了什么

```
公网 443 (HTTPS)
   ↓  腾讯云安全组放行
Nginx  ← 证书、限速、日志
   ↓  127.0.0.1:8787（只监听本机，外网碰不到）
server.py  (python3, 标准库)
   ↓
data/accounts.db  账号
data/users/uN.db  每个用户一个 SQLite（物理隔离）
```

- 只监听了 `127.0.0.1`，所以就算有人扫到 8787 端口也连不上，必须走 Nginx + HTTPS
- 备份是 SQLite 官方的在线备份 API，业务在写入时备份也不会坏库
- 所有东西都是标准库，没有 pip 依赖，服务器上不用维护 Python 包版本

---

## 附：<SERVER_IP> 这台机器的实际状态

> 这一节记录这台机器上真实跑着什么，别和其它文档混了。以后换人维护先看这里。

**机器**

| 项 | 值 |
|---|---|
| 实例 | 腾讯云**轻量应用服务器**（Lighthouse），`VM-0-14-ubuntu` |
| 系统 | Ubuntu 24.04.4 LTS |
| 配置 | 2G 内存 / 40G 磁盘（已用 6.3G） |
| 登录 | `root` + 部署密钥（`~/.ssh/battery_deploy`） |

**这台机器上现在只跑电池台账**

| 应用 | 位置 | 端口 | 说明 |
|---|---|---|---|
| `battery-tracker` 电池台账 | `/opt/battery-tracker` | 8787（**仅 127.0.0.1**） | 当前唯一业务，外网只能经 Nginx 443 进入 |

**2026-10-18 16:39 已升级为多用户版**，数据布局：

| 路径 | 内容 |
|---|---|
| `data/accounts.db` | 账号（用户名、密码哈希、角色） |
| `data/users/u1.db` | admin 的数据，原来那 40 个通道在这里 |
| `data/users/uN.db` | 后续新增用户各自的数据 |
| `data/shared.db` | **实验方法库**（团队共享，所有人同一份） |
| `data/uploads/<方法id>/` | 方法附件实体文件 |
| `data/battery.db` | 迁移前的旧库，**只作备份保留，程序不再读写** |

迁移脚本是 `deploy/migrate_multiuser.py`，只在首次升级时跑一次，目标库已有数据会自动跳过（不会覆盖）。备份已改为「账号库 + 所有用户库」的目录快照，见 `deploy/backup.sh`。

原 `qywx_scrm`（企业微信 SCRM，gunicorn + Flask，5000 端口）已于 2026-10-18 按要求彻底删除：服务、Nginx 站点、`/opt/qywx_scrm_app`（35M）全部移除，进程和单元已确认无残留。

删除前的完整快照在 `/var/backups/qywx_scrm-20261018-1602/`（76K），含源码包、`config.py`（企业微信 corp_id/secret）、`keyword_rules.db`、Nginx 配置、systemd 单元和一份六步恢复说明。需要还原时按目录里的 `恢复说明.txt` 操作即可。

Nginx 现在承载 `<YOUR_DOMAIN>`：80 全部 301 跳 HTTPS，443 反代到 `127.0.0.1:8787`，站点文件 `/etc/nginx/sites-available/battery-tracker`（由 `deploy/nginx-ssl.conf.template` 生成）。`default` 站点已移除。

**2026-10-23 09:43 已切到域名 + HTTPS**

- 访问地址：**https://<YOUR_DOMAIN>**（Let's Encrypt，到期 2027-01-21，`certbot` 定时任务自动续）
- 程序改为 `BT_HOST=127.0.0.1` + `BT_SECURE=1`（drop-in：`/etc/systemd/system/battery-tracker.service.d/domain.conf`）
- `http://<SERVER_IP>:8787` 已不可达；安全组里 8787 的入站规则可以删掉
- 原来「80 被备案拦」的判断在这台机器上没发生：实测 80 可以直连到 nginx，所以证书走的 HTTP-01（webroot）
- 回滚：删掉上面那个 drop-in → `systemctl daemon-reload && systemctl restart battery-tracker`

**日常操作**

```bash
systemctl status battery-tracker      # 状态
journalctl -u battery-tracker -f      # 日志
systemctl restart battery-tracker     # 重启
cat /opt/battery-tracker/data/admin-password.txt   # 初始管理员密码
ls -lh /var/backups/battery-tracker/  # 备份（每天 03:10，保留 30 天）
```

**升级代码**（本地改完，在本地执行）

```bash
cd D:\vibe\battery-tracker
tar czf - --exclude='__pycache__' --exclude='dist' --exclude='docs/icon-src' battery-tracker \
  | ssh -i ~/.ssh/battery_deploy root@<SERVER_IP> "tar xzf - -C /opt && chown -R battery:battery /opt/battery-tracker && systemctl restart battery-tracker"
```

> 注意：这样覆盖不会动 `data/`？会——`data/` 也在包里。如果服务器上已经有正式数据，**别用这条命令**，改成只传代码：`scp server.py static/* root@...:/opt/battery-tracker/` 之后再 `chown` 重启。

**以后再换域名 / 重装**

> 详细步骤见 [`docs/域名访问.md`](docs/域名访问.md)，一条命令：
> `sudo bash deploy/enable_domain.sh 域名 邮箱 [DNSPod_ID DNSPod_Token]`

1. 域名解析加一条 A 记录指向 `<SERVER_IP>`
2. 安全组放行 443（80 用于证书验证，也建议留着）
3. 跑脚本：签证书 → 生成 Nginx 站点 → 改 systemd 监听 → 自检
4. 安全组删掉 8787 的入站规则

> 注意：`deploy/enable_domain.sh` 必须是 **LF** 换行，`scp` 前若在 Windows 编辑过，先转换一次，否则会报 `set: pipefail: invalid option name`。

切完之后 PWA 才能装到桌面——安装按钮需要有效 HTTPS 证书，这是浏览器的硬要求。
