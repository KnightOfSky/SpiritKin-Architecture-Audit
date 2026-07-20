# SpiritKinAI 云端部署 Runbook

Date: 2026-06-21

这份文档记录本次把 SpiritKinAI 轻量云控部署到云服务器的完整流程。目标是先做 owner-only 验收，不对外开放管理面。

## 1. 服务器选择

当前验证阶段推荐配置：

```text
地域：香港
实例：通用型
规格：2 vCPU / 2 GiB RAM / 40 GiB SSD
镜像：Ubuntu 22.04 LTS
公网：1 个固定公网 IPv4
```

不建议 1 GiB 内存起步。Docker、Caddy、MinIO、control-plane 可以跑，但余量太小，排错成本高。

## 2. DNS 配置

建议用子域名，不占用主站：

```text
记录类型：A
主机记录：control
记录值：云服务器公网 IP
```

本次使用：

```text
control.spiritkinai.cn -> 8.218.183.171
```

云端 `.env.cloud` 中对应：

```env
CADDY_HOST=control.spiritkinai.cn
```

## 3. 登录服务器

本机 PowerShell：

```powershell
ssh root@8.218.183.171
```

如果首次连接提示 SSH 指纹，确认 IP 是自己的云服务器后输入：

```text
yes
```

如果出现：

```text
Permission denied (publickey)
```

说明当前登录方式要求密钥，或密码登录没有打开。可以在云控制台重置 root 密码，或者使用 Workbench/VNC 登录。

Workbench 登录后，本次默认用户是 `admin`。安装系统组件时使用 `sudo`。

## 4. 准备项目目录

服务器 Workbench/SSH：

```bash
sudo mkdir -p /opt/SpiritKinAI
sudo chown -R admin:admin /opt/SpiritKinAI
cd /opt/SpiritKinAI
pwd
```

期望输出：

```text
/opt/SpiritKinAI
```

`mkdir` 成功时通常没有输出。可以用下面命令确认：

```bash
ls -ld /opt/SpiritKinAI
```

## 5. 安装 Docker

服务器上执行：

```bash
sudo apt update
sudo apt install -y ca-certificates curl git ufw
curl -fsSL https://get.docker.com | sudo sh
sudo systemctl enable docker
sudo systemctl start docker
sudo docker version
sudo docker compose version
```

配置 Docker registry mirror：

```bash
sudo mkdir -p /etc/docker
cat <<'EOF' | sudo tee /etc/docker/daemon.json
{
  "registry-mirrors": ["https://docker.1panel.live"]
}
EOF
sudo systemctl restart docker
sudo docker info | grep -A3 "Registry Mirrors"
```

本机曾实测可用：

```text
docker.1panel.live
mcr.microsoft.com
```

本机曾实测不可用或不稳定：

```text
registry-1.docker.io
docker.anyhub.us.kg
dockerhub.icu
docker.awsl9527.cn
docker.m.daocloud.io
```

## 6. 开放服务器防火墙

服务器执行：

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable
sudo ufw status
```

云厂商安全组也要开放：

```text
22/tcp
80/tcp
443/tcp
```

不要对公网开放：

```text
8791/tcp
9001/tcp
```

`8791` 是 control-plane 容器直连端口，`9001` 是 MinIO console，本次都只绑定到 `127.0.0.1`。

## 7. 本机打包项目

本机 PowerShell：

```powershell
cd D:\SpiritKinAI
$PKG = "spiritkin-cloud-2026.06.25.3.tar.gz"
tar -czf "D:\SpiritKinAI\$PKG" Dockerfile docker-compose.yml .env.cloud.example .dockerignore deploy docs scripts tests mobile-link-bridge
Get-Item "D:\SpiritKinAI\$PKG"
```

以后云端部署包统一使用固定命名，避免按功能模块来回变更：

```text
spiritkin-cloud-YYYY.MM.DD.N.tar.gz
```

不要打包：

```text
.env
.env.cloud
state
desktop
backend
frontend
tools
```

原因：

- `.env` 是本机桌面/后端配置。
- `.env.cloud` 含本机端口和密钥，不应直接上云。
- `state` 是本机运行态数据。
- 这次云控镜像只需要根 `Dockerfile`、`docker-compose.yml`、`deploy`、`docs`、`scripts`、`tests`、`mobile-link-bridge`。
- Android APK 版本和云端包版本是两套版本号。云端 UI/控制面改动只更新 `spiritkin-cloud-*`；只有 Android 代码或 Manifest 改动才提升 APK 版本。

## 8. 上传项目包

本机 PowerShell：

```powershell
$PKG = "spiritkin-cloud-2026.06.25.3.tar.gz"
scp "D:\SpiritKinAI\$PKG" root@8.218.183.171:/tmp/
```

如果用 `admin` 用户上传：

```powershell
$PKG = "spiritkin-cloud-2026.06.25.3.tar.gz"
scp "D:\SpiritKinAI\$PKG" admin@8.218.183.171:/tmp/
```

注意：这条命令必须在本机 PowerShell 执行，不能在云服务器 Linux shell 中执行。`D:\SpiritKinAI\...` 是本机路径。

## 9. 解压项目包

服务器执行：

```bash
PKG=spiritkin-cloud-2026.06.25.3.tar.gz
cd /opt/SpiritKinAI
sudo tar -xzf /tmp/$PKG
sudo chown -R admin:admin /opt/SpiritKinAI
ls
ls -la
```

期望看到：

```text
Dockerfile
docker-compose.yml
.env.cloud.example
deploy
docs
mobile-link-bridge
scripts
tests
```

`ls` 默认不显示 `.env.cloud.example`，需要用 `ls -la`。

部署正常后可以删除 `/tmp` 里的上传压缩包，避免旧包越积越多：

```bash
ls -lh /tmp/spiritkin*.tar.gz
sudo find /tmp -maxdepth 1 -type f -name 'spiritkin*.tar.gz' -delete
```

不要在 `/opt/SpiritKinAI` 里按包名删除文件；那里是已经解压后的运行目录。

## 10. 生成云端环境文件

如果 Workbench 的编辑器不方便复制粘贴，可以直接用命令生成 `.env.cloud`。

服务器在 `/opt/SpiritKinAI` 下执行：

```bash
MGMT_TOKEN=$(openssl rand -hex 32)
WORKER_SECRET=$(openssl rand -hex 32)
MINIO_SECRET=$(openssl rand -hex 32)

cat > .env.cloud <<EOF
# Public HTTPS hostname served by Caddy.
CADDY_HOST=control.spiritkinai.cn
HTTP_BIND=80
HTTPS_BIND=443

# Keep raw Python receiver private to the VM.
CONTROL_PLANE_DIRECT_BIND=127.0.0.1:8791

# MinIO console is private.
MINIO_CONSOLE_BIND=127.0.0.1:9001

SPIRITKIN_PRODUCTION_MODE=1
SPIRITKIN_MANAGEMENT_TOKEN=$MGMT_TOKEN
SPIRITKIN_WORKER_MANIFEST_SIGNING_SECRET=$WORKER_SECRET

SPIRITKIN_ARTIFACT_BACKEND=s3
SPIRITKIN_ARTIFACT_S3_ENDPOINT_URL=http://minio:9000
SPIRITKIN_ARTIFACT_S3_BUCKET=spiritkin-artifacts
SPIRITKIN_ARTIFACT_S3_REGION=us-east-1
SPIRITKIN_ARTIFACT_S3_PREFIX=prod
SPIRITKIN_ARTIFACT_S3_PUBLIC_BASE_URL=
SPIRITKIN_ARTIFACT_S3_PATH_STYLE=1

AWS_ACCESS_KEY_ID=spiritkin-minio
AWS_SECRET_ACCESS_KEY=$MINIO_SECRET
EOF

chmod 600 .env.cloud
grep -E '^(CADDY_HOST|CONTROL_PLANE_DIRECT_BIND|MINIO_CONSOLE_BIND|SPIRITKIN_PRODUCTION_MODE|AWS_ACCESS_KEY_ID)=' .env.cloud
```

期望输出：

```text
CADDY_HOST=control.spiritkinai.cn
CONTROL_PLANE_DIRECT_BIND=127.0.0.1:8791
MINIO_CONSOLE_BIND=127.0.0.1:9001
SPIRITKIN_PRODUCTION_MODE=1
AWS_ACCESS_KEY_ID=spiritkin-minio
```

## 11. 启动云控

服务器执行：

```bash
cd /opt/SpiritKinAI
sudo docker compose --env-file .env.cloud up -d --build
sudo docker compose --env-file .env.cloud ps
```

期望：

```text
control-plane   Up / healthy
caddy           Up
minio           Up
minio-init      Exited (0)
```

如果镜像拉取慢，先确认 Docker mirror：

```bash
sudo docker info | grep -A3 "Registry Mirrors"
```

## 12. 验收命令

服务器内部直连：

```bash
curl http://127.0.0.1:8791/android/health
```

公网 HTTPS：

```bash
curl https://control.spiritkinai.cn/android/health
```

期望输出：

```json
{
  "ok": true,
  "service": "spiritkin-control-plane",
  "production_mode": true
}
```

本次已通过：

```text
http://127.0.0.1:8791/android/health
https://control.spiritkinai.cn/android/health
```

## 13. 常用维护命令

所有服务器命令默认先进入部署目录：

```bash
cd /opt/SpiritKinAI
```

查看容器状态：

```bash
sudo docker compose --env-file .env.cloud ps
```

健康检查：

```bash
curl http://127.0.0.1:8791/android/health
curl https://control.spiritkinai.cn/android/health
```

查看 Android APK 更新 manifest：

```bash
curl -s https://control.spiritkinai.cn/android/apk/manifest
```

只看 Android APK 版本：

```bash
curl -s https://control.spiritkinai.cn/android/apk/manifest | grep version_name
```

当前 `spiritkin-cloud-*` 是云端部署包版本；`version_name` 是 Android APK 版本。两者不要求一致。
本包期望 Android APK 版本为：

```text
2026.06.25.2
```

查看日志：

```bash
sudo docker compose --env-file .env.cloud logs --tail=100 control-plane
sudo docker compose --env-file .env.cloud logs --tail=100 caddy
sudo docker compose --env-file .env.cloud logs --tail=100 minio
```

持续跟随 control-plane 日志：

```bash
sudo docker compose --env-file .env.cloud logs -f --tail=100 control-plane
```

上传新包后重建部署：

```bash
PKG=spiritkin-cloud-2026.06.25.3.tar.gz
cd /opt/SpiritKinAI
sudo tar -xzf /tmp/$PKG
sudo chown -R admin:admin /opt/SpiritKinAI
sudo docker compose --env-file .env.cloud up -d --build
sudo docker compose --env-file .env.cloud ps
curl https://control.spiritkinai.cn/android/health
```

只重启，不重建镜像：

```bash
sudo docker compose --env-file .env.cloud restart
```

只重启 control-plane：

```bash
sudo docker compose --env-file .env.cloud restart control-plane
```

停止，但保留数据卷：

```bash
sudo docker compose --env-file .env.cloud down
```

重新启动：

```bash
sudo docker compose --env-file .env.cloud up -d
```

谨慎清空数据卷：

```bash
sudo docker compose --env-file .env.cloud down -v
```

`down -v` 会删除 Docker volume，包括 MinIO 数据和 control-plane 状态。只有明确要重置环境时才用。

查看 Docker 占用：

```bash
sudo docker system df
```

清理不用的构建缓存和悬空镜像：

```bash
sudo docker builder prune -f
sudo docker image prune -f
```

清理 `/tmp` 里的旧上传包：

```bash
ls -lh /tmp/spiritkin*.tar.gz
sudo find /tmp -maxdepth 1 -type f -name 'spiritkin*.tar.gz' -delete
```

查看云端环境关键项，不打印密钥：

```bash
grep -E '^(CADDY_HOST|CONTROL_PLANE_DIRECT_BIND|MINIO_CONSOLE_BIND|SPIRITKIN_PRODUCTION_MODE|SPIRITKIN_ARTIFACT_BACKEND)=' .env.cloud
```

如果主控端按钮返回 `401` 或 `403`，通常是浏览器里保存的管理 token 不对。重新复制云端 token：

```bash
grep '^SPIRITKIN_MANAGEMENT_TOKEN=' .env.cloud
```

把等号后面的值填入主控端 `控制权限`。

## 14. 云服务器要不要关

如果还要继续做 Android Bridge、remote worker、远程 Agent 接入验收，不要关服务器。

保持运行的原因：

- 远程 Agent 需要固定 HTTPS 入口。
- Android Bridge 和 worker 需要持续心跳。
- Caddy 证书、MinIO、control-plane 状态都在服务器上。

可以关的情况：

- 今天不继续验收。
- 暂时不需要远程设备连接。
- 想省费用。

关机影响：

- `https://control.spiritkinai.cn` 会不可用。
- Android Bridge/worker 会断线。
- 下次开机后需要确认容器是否自动恢复。

开机后检查：

```bash
cd /opt/SpiritKinAI
sudo docker compose --env-file .env.cloud ps
curl https://control.spiritkinai.cn/android/health
```

如果容器没有自动起来：

```bash
sudo docker compose --env-file .env.cloud up -d
```

当前建议：在完成 Android Bridge 和 remote worker 接入前，先保持服务器运行。

## 15. 下一步验收

云控入口通过后，下一步是设备和 Agent 接入：

```text
1. Android Bridge 检查安装包更新，确认版本为 2026.06.25.4。
2. Android Bridge 使用 https://control.spiritkinai.cn 请求配对码并绑定。
3. 主控端批准配对请求并设置有效期。
4. 手机端应自动从“等待主控批准中”更新为“已绑定 + 有效期”，不需要手动点“立即同步一次”。
5. 主控端工作区设备列表中确认 Android 手机端属于正确 workspace。
6. 如果手机重装或清数据后再次请求绑定，主控端同一台 Android 的旧有效绑定应自动移到“历史/待恢复绑定记录”，状态为“等待恢复绑定”；主控批准后应沿用原 token 和原有效期。
7. 主控端对该设备下发“检查状态”或“打开 PDD”，手机端应由后台同步自动执行。
8. 若手机端显示 heartbeat 失败，先确认是否未绑定、配对请求仍待批准、token 已过期；新版 APK 会保留本地 token，只有连续 3 次 401/403 才清除绑定。
9. Android 重装或升级后，系统会自动关闭无障碍服务，这是系统安全策略，不是 heartbeat/token 失效。此时手机仍应显示“主控已同步”，主控端应显示“手机在线，绑定有效；需要重新开启无障碍”。从主控下发“打开手机无障碍设置”，或在手机端点“打开无障碍设置”，重新启用 SpiritKin PDD Automation。
10. Android 重装、覆盖安装、开机或解锁后会尝试自动恢复后台同步；若 vivo 等系统仍杀后台，在手机端点“允许后台持续同步”，并在系统里允许本应用后台运行/忽略电池优化。
11. 对该 Android 设备暂停 ecommerce.auto_listing.v1，验证带 inputs.device_id 的工作流会被拒绝。
12. 再启用该设备工作流，验证工作流可重新启动。
13. 从控制台下发 android.screenshot.request_permission，再下发 android.screenshot.capture。
14. 验证截图 artifact 能上传、存储、下载。
15. remote worker 使用云端 pairing token 绑定，验证 worker heartbeat、任务领取、结果回传。
```
