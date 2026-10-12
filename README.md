# wuxia-autoclaim

天刀「周周载愿」活动(actId=625474)**完全本地化**的自动许愿/领取工具: 每日许愿 + 周末一键领取,
凭据自动收割, 可安装为 Windows/Linux 周期性任务静默执行。

由 [wuxia-autosign](https://github.com/wang-zhuo996/wuxia-autosign) 重建而来, 核心变化:

| | wuxia-autosign(旧) | wuxia-autoclaim(本工具) |
|---|---|---|
| 签到执行 | GitHub Actions 云端 | **本地**(schtasks/systemd/cron) |
| 凭据来源 | 游戏缓存 + QQNT 桥 | **NapCat** + QQNT 桥 + 游戏缓存 |
| 服务器部署 | 不支持 | 支持(NapCat 通道无需本地桥/浏览器) |
| 依赖 | PAT + PyNaCl(同步 GitHub) | **核心零依赖**(纯标准库) |

## 工作原理

```
周期任务(每小时) --quiet run
  ├─ 1. 体检   用只读接口(FLOW_INIT)探测 roles.json 里每个角色的凭据
  │           全部有效 -> 跳过收割(绝大多数轮次到此为止, 零副作用)
  ├─ 2. 收割   有失效时按通道顺序修复(默认 napcat -> qqnt -> game):
  │     napcat   NapCat OneBot HTTP 的 clientkey -> ptlogin2 jump 签发全套 cookie
  │     qqnt     本地 QQ 桌面客户端的快速登录桥(127.0.0.1:4301...) 同上签发
  │     game     天刀内嵌浏览器 QBrowser 的本地 cookie 缓存
  ├─ 3. 动作   周末且有存货 -> 一键领取; 今天未许愿 -> 按奖励序号许愿; 已许愿 -> 秒退
  └─ 4. 推送   Server酱微信通知(全部角色都已许愿时不打扰)
```

凭据 = 7 个 QQ 登录态 cookie(`uin/skey/RK/ptcz` + `p_skey/p_uin/pt4_token` 长期正本),
存放在 `<data>/roles.json`。签发链路(clientkey -> `ssl.ptlogin2.qq.com/jump` x2)与旧项目实测打通的
SSO 通道完全一致,NapCat 只是换了一个 clientkey 来源 —— 因此 **Linux 服务器也能收割凭据**。

## 凭据形态选型

| 形态 | 适合 | 用法 |
|---|---|---|
| A. Linux 服务器 + NapCat | 7x24 无人值守(推荐) | 服务器跑 NapCat 保持 QQ 在线, 本工具同机 systemd/cron 周期执行 |
| B. 本机 Windows | 日常开机使用 | NapCat 或官方 QQ 在线即可(双保险), schtasks 周期执行 |
| C. 还没部署 NapCat | 先跑起来 | 用官方 QQ 客户端桥或 `login` 交互登录起步, 之后随时加 NapCat |

## NapCat 部署与 OneBot HTTP 开启

1. 安装 NapCatQQ: <https://napneko.github.io/guide/boot/Shell> (Windows/Linux/Docker 均可),
   登录你要用来签到的 QQ 号。
2. 打开 NapCat WebUI(默认 `http://127.0.0.1:6099/webui`), **网络配置 -> 新建网络服务 -> HTTP 服务器**:
   - 监听地址 `127.0.0.1`(本机) 或 `0.0.0.0`(跨机器, 配合防火墙)
   - 端口任选, 如 `3000`
   - **务必设置 access token**(clientkey 等同登录态, 裸奔等于把号送人)
3. 把端点告诉本工具:

   ```
   wuxia-autoclaim config napcat-add http://127.0.0.1:3000 <你的token>
   ```

   跨机器时把 `127.0.0.1` 换成 NapCat 所在机器的内网地址; 多个 NapCat 实例(多个 QQ)重复添加即可。
4. 验证: `wuxia-autoclaim status`, `channels.napcat` 里能看到登录的 QQ 即通。

> 版本要求: NapCat 需支持 `get_clientkey` 扩展 action(较新版本均有)。
> 若报 retcode 非 0, 升级 NapCat 或回退依赖官方客户端桥通道。

## 安装

需要 Python >= 3.9。

```bash
git clone <本仓库> && cd wuxia-autoclaim

# 方式一: pipx 安装(推荐, 隔离环境)
pipx install .

# 方式二: 源码 venv
python -m venv .venv
.venv/Scripts/pip install -e .        # Windows
.venv/bin/pip install -e .            # Linux/macOS

# 可选: 交互登录/无头绑定需要浏览器内核
pip install "wuxia-autoclaim[browser]"
python -m playwright install chromium
```

不安装也能跑: `PYTHONPATH=src python -m wuxia_autoclaim ...`(周期任务安装时会自动处理)。

## 快速上手

```bash
# 1. 绑定角色(三选一)
wuxia-autoclaim login                  # 浏览器扫码/网页登录(首次推荐, 自动绑定大区)
wuxia-autoclaim harvest --bind-new     # 已配 NapCat/QQ 在线时, 无头自动发现新账号的角色
cp /旧项目/src/wuxia_autosign/roles.json data/roles.json   # 从旧项目迁移(见下)

# 2. 体检
wuxia-autoclaim status                 # 角色/凭据/通道一览

# 3. 实跑一次
wuxia-autoclaim run                    # 体检->收割->许愿/领取, 看 data/autoclaim.log

# 4. 安装为周期任务(每小时静默执行)
wuxia-autoclaim task install
wuxia-autoclaim task status            # 查看下次触发时间
```

## 从 wuxia-autosign 迁移

`roles.json` 格式完全兼容, 直接拷贝即可:

```
Windows:  copy wuxia-autosign\src\wuxia_autosign\roles.json  wuxia-autoclaim\data\
Linux:    cp wuxia-autosign/src/wuxia_autosign/roles.json    wuxia-autoclaim/data/
```

- 旧 `defaults.run_time` 遗留键会被忽略, 可删
- 服务器迁移: 在 Windows 上 `login` 绑定好角色后把 `roles.json` 拷到服务器 `data/`,
  之后凭据由服务器上的 NapCat 通道续期, 不再需要浏览器
- GitHub Actions 双轨不再需要, 想停掉旧仓库的 Actions 直接 Disable workflow 即可

## 命令手册

| 命令 | 说明 |
|---|---|
| `run` | 周期任务执行体: 体检->收割->许愿/领取->推送。`--role` `--gift` `--index` `--claim-only` `--no-harvest` `--no-push` `--channel` `--bind-new` |
| `harvest` | 只做凭据体检+收割。`--channel a,b,c` 临时覆盖通道顺序 |
| `status` | 只读体检: 每角色 iRet/已许愿/待领奖(脱敏) + napcat 端点/桥在线账号/游戏缓存目录。`--json` 脚本化 |
| `login` | 浏览器交互登录绑定角色(需 browser extra)。`--qr`(扫码) `--headed`(网页) `--timeout` |
| `bind` | 无头绑定: 自动发现缺失的 area/roleid(需 browser extra)。`--uin` `--headed` `--timeout` |
| `task install/uninstall/status` | 周期任务管理。`--method auto/schtasks/systemd/cron` `--interval N`(分钟, 默认 60, 最小 10) |
| `config show/set/napcat-add/napcat-clear` | 配置管理(`game_dir` `channel_order` `auto_bind` `gift_index` `push_key`) |

全局: `--data-dir`(数据目录) `--quiet`(只写日志不刷屏), 放在子命令前后均可。
退出码: 0=成功/无事可做, 1=有角色失败, 2=致命(数据文件缺失/依赖缺失等)。

## 配置参考

**数据目录**(roles.json/config.json/日志/任务包装脚本都在这里):

- 源码树运行(含 `pip install -e .`) -> `<仓库>/data/`
- pipx/pip 安装态 -> Windows `%APPDATA%\wuxia-autoclaim`, Linux `~/.config/wuxia-autoclaim`
- 环境变量 `WUXIA_DATA_DIR` 或 `--data-dir` 覆盖

**config.json**(基础设施):

```json
{
  "napcat": [{"url": "http://127.0.0.1:3000", "token": "xxx"}],
  "game_dir": "",
  "channel_order": ["napcat", "qqnt", "game"],
  "auto_bind": false
}
```

**roles.json**(业务, schema 同旧项目): `defaults.gift_index`(许愿奖励序号, 默认 3)、
`defaults.push_key`(Server酱 SendKey, 不填不推送)、`roles[]`(每角色可单独覆盖 `gift_index`)。

**环境变量**: `WUXIA_DATA_DIR` / `WUXIA_GAME_DIR` / `WUXIA_GIFT_INDEX` / `WUXIA_PUSH_KEY`(优先级最高)。

## 定时任务细节

**Windows(schtasks)**: 任务名 `WuxiaAutoclaim`, 用 venv 里的 `pythonw.exe` 静默执行
(无窗口闪现), 包装脚本 `data\task.cmd` 钉死了数据目录与解释器路径, 移动仓库后重跑
`task install` 即可。默认"仅用户登录时运行", 常规使用足够。

**Linux(优先 systemd user timer)**: unit 装在 `~/.config/systemd/user/`, `OnCalendar=hourly`
+ `Persistent=true`(关机错过的小时开机补跑)。服务器无登录会话时需要 linger:
`sudo loginctl enable-linger $USER`(安装时会自动尝试, 失败会给出手动指引)。
日志: `journalctl --user -u wuxia-autoclaim` 或 `data/autoclaim.log`。

**cron 兜底**: 容器/WSL 等无 systemd user 环境自动落 crontab, 带标记行 `# wuxia-autoclaim:v1`,
装卸幂等(装两次不会重复)。

## 排错 FAQ

- **iRet=101 登录态失效** -> 正常轮换, 保持 NapCat/QQ 在线, 下一轮 harvest 自动修复;
  长期不修复说明所有通道都不可用, 看 `harvest` 输出定位
- **iRet=99998 未绑定大区** -> 账号首次进活动要选大区, 桌面环境跑一次 `login --headed`
- **`[qqnt] 桥上没有在线账号`** -> 官方 QQ 桌面客户端没开; 服务器上属预期, 配 NapCat 即可
- **`[napcat] 连不上 ...`** -> NapCat WebUI 里没开 HTTP 服务, 或地址/端口/防火墙错
- **`[napcat] 鉴权失败(HTTP 401)`** -> config 里的 token 与 NapCat 设置不一致
- **`[napcat] action get_clientkey 失败`** -> NapCat 版本过旧, 升级; 或 QQ 掉线重登
- **`[game] 游戏 cookie 库里没有登录态`** -> 游戏里打开一次活动页即可写回
- **活动换期** -> 改 `src/wuxia_autoclaim/ams.py` 顶部的 `ACT_ID/SDID/E_CODE/FLOW_*` 常量

## 安全须知

- `data/roles.json` 与 `config.json` 是**登录态正本**, 等同 QQ 号控制权:
  不要提交进 git(已忽略)、不要截图外发; Linux 下工具会自动 `chmod 600`
- NapCat HTTP 服务**必须设 token**, 只绑 `127.0.0.1` 或内网 + 防火墙
- 日志已对 QQ 号/角色 ID 脱敏, 但仍建议不要公开整份日志

## 开发

```bash
pip install -e ".[dev]"
pytest                    # 31 个纯函数单测, 不触网
```

代码风格: 纯标准库实现核心链路(urllib/argparse/sqlite3), `%` 格式化, 中文注释,
playwright 一律函数内延迟导入(核心环境零第三方依赖)。
