# ikuuu机场签到

## 作用
> 每天进行签到，获取额外的流量奖励<br/>
> 2026年4月ikuuu新增登录请求验证Geetest v4，不使用过验证方式已无法正常签到<br/>
> 支持登录邮箱验证码：账号配置提供邮箱密码或授权码时，通过 IMAP 自动读取 8 位验证码<br/>
> 默认使用本地识别，无需 CapSolver / AntiCaptcha API Key；Cookie 有效时复用登录状态。
> 支持图标点选和文字点选分流；无法可靠识别、遇到未知题型或协议变化时明确失败，不随机点击、不回退付费服务。

---

## 推送方式
- 🚀 auto_check_in_ikuuu.py 支持：
  - 青龙面板通知
  - 163、126、QQ/Foxmail、Gmail、Yahoo、Outlook 邮箱
  - 自定义 SMTP、多个收件人

---

# 部署过程
 
## 1. Fork 仓库
点击右上角 Fork 此仓库

---

## 2. 配置环境变量
进入：

Settings → Secrets and variables → Actions

新建以下变量：<br/>

| 参数 | 是否必须 | 说明 |
|------|----------|------|
| ACCOUNTS | ⚠ 必须 | 每行 `账号邮箱:iKuuu密码:邮箱密码或授权码`，第三段可省略 |
| IKUUU_DOMAIN | 可选 | 自定义 iKuuu 域名，支持填写域名或完整 URL |
| MAIL_USER | 可选 | 发件邮箱 |
| MAIL_PASS | 可选 | 邮箱应用密码 |
| MAIL_TO | 可选 | 收件邮箱 |
| MAIL_PROVIDER | 可选 | 邮箱类型，默认自动识别 |
| SMTP_HOST | 自定义时 | SMTP 服务器 |
| SMTP_USERNAME | 可选 | SMTP 登录用户名，默认与发件邮箱相同 |
| SMTP_PORT | 可选 | SMTP 端口 |
| SMTP_SECURITY | 可选 | ssl/starttls/plain |
| IKUUU_CAPTCHA_SOLVER_ENABLED | 可选 | 是否启用验证码(0/1)，默认 1 |
| IKUUU_CAPTCHA_PROVIDER | 可选 | 默认 `local`；显式选择 `capsolver` / `anticaptcha` 才使用外部识别服务 |
| IKUUU_CAPSOLVER_API_KEY | 仅外部服务 | 本地模式不读取、不需要此 Key |
| IKUUU_ANTICAPTCHA_API_KEY | 仅外部服务 | 本地模式不读取、不需要此 Key |
| IKUUU_CAPTCHA_TIMEOUT_SECONDS | 可选 | 总超时，默认 120 秒，本地模式范围 1–600，超时终止识别子进程 |
| IKUUU_CAPTCHA_MAX_ATTEMPTS | 可选 | 本地最多获取几次新题，默认 3，范围 1–5 |
| IKUUU_CAPTCHA_POLL_INTERVAL_SECONDS | 仅外部服务 | 轮询间隔(秒) |
| IKUUU_IMAP_PROVIDER | 可选 | 邮箱类型，默认 auto 自动识别 |
| IKUUU_IMAP_HOST | 自定义时 | IMAP 服务器 |
| IKUUU_IMAP_PORT | 可选 | IMAP 端口，默认 993 |
| IKUUU_IMAP_SECURITY | 可选 | ssl/starttls/plain |
| IKUUU_IMAP_FOLDER | 可选 | 验证码邮件目录，默认 INBOX |
| IKUUU_EMAIL_CODE_TIMEOUT_SECONDS | 可选 | 等待邮箱验证码超时，默认 120 秒 |
| IKUUU_EMAIL_CODE_POLL_INTERVAL_SECONDS | 可选 | 邮箱轮询间隔，默认 2 秒 |

### 本地识别的范围

- 图标：按提示图进行彩色目标分离、旋转与仿射图形匹配和一对一分配，不依赖固定类别的图标分类模型。低匹配分或结果有歧义时拒绝提交。
- 文字：使用 ddddocr 自带的检测、两套 OCR ONNX 模型，在多个角度核对识别结果；提示字识别不一致、缺字或重复匹配时拒绝提交。真实 iKuuu 文字题尚未验证，不能承诺通过率。
- 运行时按极验实际返回的 `captcha_type` 分流，不强制切成图标题。当前仅支持 `icon` / `word`，其他题型明确报错。
- 参考 [GeekedTest](https://github.com/SunSnow1015/GeekedTest) 的协议实现，并保留其 MIT 许可；参考 [Geetest4_yolov8](https://github.com/SunSnow1015/Geetest4_yolov8) 的检测与匹配思路，但不引入其大体积 Torch/Paddle 环境或授权不明确的权重。
- 本地模式即使残留外部 API Key 或 fallback 配置也不会回退付费识别。仍需联网访问 iKuuu、极验官方加载/图片/验证接口，这不是离线签到。
- 模型随 Python 依赖在安装时下载，运行时不下载模型，也不需要 GPU、浏览器或自建识别 HTTP 服务。首次安装需要额外磁盘空间；建议使用 Linux x86_64 的 Actions 或容器。
- 协议更新仍可能导致失败；静态提取客户端参数不等于保证所有后续版本可用，不执行下载的 JavaScript。

当前验收仅限本地：3 组已保存的真实图标题均完成正确顺序匹配，合成中文场景完成检测与双模型识别；这些样本不代表总体通过率。尚未向真实极验提交结果或完成登录签到，真实文字题、GitHub Actions 执行、Docker 运行及 OpenWrt 运行均未实测。

---
**ACCOUNTS 写法：**  
每行一个账号，支持以下两种格式：

```text
账号邮箱:iKuuu密码
账号邮箱:iKuuu密码:邮箱密码或授权码
```

第三段可省略。省略时保持原有登录逻辑；站点要求邮箱验证码时会提示缺少邮箱授权码。填写第三段后，脚本会根据账号邮箱自动识别 QQ/Foxmail、163、126、Gmail、Yahoo 或 Outlook 的 IMAP 服务，读取本次登录邮件中的 8 位验证码并继续登录。

QQ、163、126 等邮箱通常应填写邮箱后台生成的授权码，不要填写网页登录密码。请先在邮箱设置中开启 IMAP 服务。GitHub Actions 用户应将完整多行内容放在 `ACCOUNTS` Secret 中；OpenWrt 配置文件权限会设置为 `600`。

**MAIL_TO：**  
有多个接收通知的账户用逗号 `,` 分割  

不需要通知可不填写邮箱相关参数

---
## 3. 自动运行
### 方式一：GitHub Actions（优先）

仓库包含 `.github/workflows/checkin.yml`，每天北京时间 08:17（UTC 00:17）调度，也支持手动运行；GitHub 定时任务可能延迟。

1. 在仓库的 Actions Secrets 中添加 `ACCOUNTS`，可选添加 `MAIL_USER`、`MAIL_PASS`、`MAIL_TO`。
2. 如需指定域名，在 Actions Variables 中添加 `IKUUU_DOMAIN`；可选设置 `IKUUU_CAPTCHA_TIMEOUT_SECONDS` 和 `IKUUU_CAPTCHA_MAX_ATTEMPTS`。
3. 在 Actions 中启用并手动运行 **Local iKuuu check-in**。自定义 SMTP / IMAP 等配置可作为环境变量加入签到步骤，密码只能使用 Secrets。

此 workflow 固定为本地识别、只读仓库权限；安装依赖和离线测试步骤不接收账号 Secrets。仅缓存 pip 依赖，不缓存或上传 Cookie，因此每个新 runner 通常需要重新登录。任何账号签到失败会返回非零退出码。首次上线应查看实际日志验证，离线测试通过不代表极验服务端一定接受结果。

### 方式二：Python / 青龙 / Docker

Python 3.10–3.12 环境下载完整仓库后执行：

```sh
python -m pip install -r requirements-local.txt
python -m unittest discover -s tests -v
python auto_check_in_ikuuu.py
```

账号和邮箱参数通过环境变量配置。本地模式不能只订阅单个签到脚本：青龙需要保留同目录的 `local_captcha.py`、`captcha_images.py` 和 `geetest_protocol.py`，并使用实际执行脚本的 Python 安装依赖。邮箱的 SSL / STARTTLS 连接会校验证书及主机名；自建邮箱需使用受信任证书，不应通过切换明文连接绕过证书错误。

Docker 构建及单次执行：

```sh
docker build -t ikuuu-checkin:local .
docker run --rm --env-file .env -v ikuuu-data:/app/data ikuuu-checkin:local
```

`.env` 中配置 `ACCOUNTS` 等参数，不提交该文件。容器以非 root 用户执行；使用命名卷保存 Cookie，敏感卷不要上传或共享。容器本身不常驻定时，需要宿主机调度器每日执行一次。

---

### 方式三：OpenWrt / iStoreOS 软件包

原生 OpenWrt 的 musl、MIPS / ARM32 环境通常不能直接安装 ONNX Runtime 官方 wheel，本次不承诺原生路由器支持本地识别，也不自动安装大型模型。现有 LuCI 服务商配置保持不变；具备容器条件的设备优先使用上面的 Docker 方案，或把任务放在 Actions。旧版发布包不包含此次本地后端。

从 [Releases](https://github.com/Ceylan233/ikuuu_checkin/releases) 下载 IPK 后安装：

```sh
opkg install luci-app-ikuuu-checkin_1.2.4-1_all.ipk
```

安装后进入 LuCI 的“服务 → iKuuu 签到”，可配置多账号、自定义 iKuuu 域名、验证码服务、登录邮箱验证、通知邮箱和每日定时，也可手动签到或发送测试邮件。常见邮箱保持“自动识别”即可，自建邮箱可在“邮箱验证”标签页填写 IMAP 参数。

---

#### Linux 启动脚本

```bash
#!/bin/bash
source /etc/profile

cd /root/ikuuu_checkin || exit 1

export ACCOUNTS=""
export MAIL_USER=""
export MAIL_PASS=""
export MAIL_TO=""

export IKUUU_CAPTCHA_SOLVER_ENABLED="1"
export IKUUU_CAPTCHA_PROVIDER="local"
export IKUUU_CAPTCHA_TIMEOUT_SECONDS="120"
export IKUUU_CAPTCHA_MAX_ATTEMPTS="3"

/usr/bin/python3 /root/ikuuu_checkin/auto_check_in_ikuuu.py
```

---

## 4. 最后，可以到Run sign查看签到情况，同时也会也会将签到详情推送。

## 运行截图
<img width="750" height="555" alt="image" src="https://github.com/user-attachments/assets/cc8eef80-286b-4d4d-9ddb-059313780cc9" />
<img width="565" height="564" alt="image" src="https://github.com/user-attachments/assets/4f45b50a-4fb0-41b1-89fd-97850e2c9db6" />


**未添加过验证api：** <br/><br/>
<img src="https://github.com/user-attachments/assets/9103aa0a-d4e9-4f60-9244-0d615386c0d8" width="400"><br/>

---
**添加过验证api：** <br/><br/>
<img src="https://github.com/user-attachments/assets/aaf9a41d-df84-495a-b391-306ad3701c55" width="400"/>


