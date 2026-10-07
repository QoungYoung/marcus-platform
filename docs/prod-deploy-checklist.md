# 生产部署清单（账本 §9.750/§9.751）

> 用户 2026-10-07：「将本次回测的配置同步到生产…**生产应当能查询所有工具**，**不存在未来函数的概念**」
> 「dsh 镜像我们升级过了…**生产也要使用新版 dsh**」「现在部署生产吧」

## 0. 前提
- 生产容器在**服务器自己**上（与本机回测容器 `marcus-dsh` **互不相干**）。
- 代码路径：本机 `git push ghdirect main` ⇒ 服务器 `git pull`。
  （注意远端名：本机是 `ghdirect`；`origin` 是 ghfast 镜像代理。）

## 1. 服务器拉代码
```bash
cd /opt/marcus-platform        # 或你的实际目录
git pull
```

## 2. 并入口径 env（**只有这部分同步** ✓）
```bash
cat docker/prod.wolf.env >> .env      # 7 条狼大口径；重复执行会重复追加，先 grep 确认
grep -nE '^WOLF_(WAVE|T_)' .env
```
同步的 7 条：
`WOLF_WAVE_COND_GATE=1`（条件单自动执行也过浪型检查）、`WOLF_WAVE_COND_AMBUSH=allow`（埋伏放行）、
`WOLF_WAVE_COND_OTHER=block`、`WOLF_T_BUY_CAP_104=1`、`WOLF_T_BUY_CAP_PCT=4.0`（低吸挂价 = min(关键位, 成本×1.04)）、
`WOLF_T_ENTRY_LINES=1`（关键位挂单 13/34/60/144）、`WOLF_WAVE_OP_GATE=0`。

✗ **不要**同步（回测专用 / 未来函数）：`BT_*`（`BT_ASOF_DAY`/`BT_NET_OFFLINE`/`BT_CARRY_STATE`/`BT_AGENT_LOOP`…）、
`WOLF_ADJ_PRICE`、`WOLF_SIM_DAY`、`WOLF_HIST_*`、`BT_DASH*`、`QT_*`、`REPLAY`/`SIM`/`RESUME`。
生产是**当日实时** ⇒ 没有 as-of、没有"防未来函数"这套东西。代码侧已自动适配（缺 `BT_ASOF_DAY` 时回退"今天"）。

## 3. 更新 dsh 镜像并起容器（★ **全工具** ✓）
```bash
cd docker
docker compose build dsh            # 用仓库当前 Dockerfile.dsh.020 重建，自动打标签 marcus-dsh:0.2rc4
# 或（若已推 registry）：docker pull marcus-dsh:0.2rc4
docker compose -f docker-compose.yml up -d dsh
```
★ **绝对不要**带 `-f docker-compose.bt.yml`：那是**回测专用 override**（挂空白名单 ⇒ 只注册 0 个工具）。

## 4. 验收
```bash
# ① 工具全开：文件应**不存在**
docker exec marcus-dsh sh -c 'ls /root/.dsh/bt_tools_allow.txt || echo NO-FILE ⇒ 全工具 ✓'
# ② 插件是新版（旧版是 2562 字节）
docker exec marcus-dsh sh -c 'wc -c /opt/dsh-plugins/cordis.patch.yml'      # 期望 3935
# ③ 桥日志应打印"全量注册"，不应出现"只注册 N 个"
docker logs --tail 50 marcus-dsh | grep -iE '工具|tool|注册'
# ④ 生产进程
docker compose ps
```
后端/worker 若也要更新：`docker compose up -d --build backend worker`。

## 5. 回滚
- 镜像：`docker compose up -d dsh` 前先把旧标签另存（如 `docker tag marcus-dsh:0.2rc3 marcus-dsh:rollback`）。
- 口径：删掉 `.env` 里第 2 步追加的那 7 行即可（代码默认全关 ⇒ 行为立即回到旧版）。
