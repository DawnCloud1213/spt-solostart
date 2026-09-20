# SPTSoloStart — SPT 单机启动 + 联机存档自动同步

> 联机（Fika）打完 → 游戏里点「下载存档」→ 双击桌面「SPT单机启动」→ 单机接着刚联机的进度玩。
> 不用手抄路径、不用手动改 Launcher 服务器、不怕旧档回写。

---

## 一、为什么需要它

联机结束后，主人在游戏里点「**下载存档**」，档案落在：

```
<SPT根>\SPT\user\fika\<profileId>\<profileId>.json      ← 联机下载档（快照）
```

而单机（自建服）实际读 / 写的是**另一条路径**：

```
<SPT根>\SPT_Runtime\user\profiles\<profileId>.json      ← 单机真正读写的档
```

两条路径**从不自动对接** → 单机启动永远读到联机之前的旧进度，而且旧档一进一出还会被回写钉死。
本工具就是这两条路径之间的「自动摆渡 + 安全闸门」。

## 二、双击之后它干了什么

| # | 步骤 | 关卡 |
|---|------|------|
| 1 | 结构自检：`SPT_Runtime\` 在不在 | 不符 → 弹窗报错退出 |
| 2 | 推导 `profileId`：`config.profile_id` → `LauncherSettings.PreferredProfile` → `SPT\user\fika` 里最新的目录 | 推不出 → 弹窗报错退出 |
| 3 | **进程闸门**：检测 `SPT.Server.exe` / `SPT.Launcher.exe` / `EscapeFromTarkov.exe` 在不在跑 | 在跑 → **弹窗**问「明知用旧档也要继续吗」，默认落「否」 |
| 4 | 选最新的「自己的档」：Fika 下载档 / 本地档 / `extra_source_dirs`（比 mtime） | 一个来源都没有 → 不覆盖，只启动 |
| 5 | 判定要不要覆盖：内容一致 / 本地更新 → 不动；来源更新 → **先备份**再覆盖 | JSON 不合法 / id 不匹配 → 拒绝覆盖 |
| 6 | 把 Launcher 自动连接目标切到「本地服务器」(`ServerId=1721162719`) | 改前留带时间戳的备份 |
| 7 | 起服务端前先**验 6969 占用者身份** | 占用者不是本目录的服务端 → **弹窗**拦截 |
| 8 | 启 `SPT.Server.exe` → 等 `127.0.0.1:6969` 监听成功 → 启 `SPT.Launcher.exe` | 超时 → 弹窗给三条查错路径 |

> Launcher 起来后会自动连本地服务器并登录档案，主人只需点「**开始游戏**」。

## 三、部署位置

```
D:\free games\EFT_0821\           ← SPT 根（含 SPT_Runtime、EscapeFromTarkov.exe）
├── SPT_Runtime\
├── EscapeFromTarkov.exe
├── SPT\user\fika\                ← Fika「下载存档」落点（只读，本工具不删）
├── FikaAutoConnect\              ← 联机工具
└── SPTSoloStart\                 ← 本工具（新建文件夹）
    ├── spt_solostart.py
    ├── spt_solostart.bat         ← 双击入口（静默）
    ├── dry_run.bat               ← 干跑检查（只看判定，不写不启）
    ├── test_solo_guard.py        ← 自检（18 项断言，只读+临时目录）
    ├── config.json
    └── README.md
```

脚本用「自身位置上一级」自动推导 SPT 根（`SPT_ROOT = 脚本上一级`），**零配置**；
搬运 = 整个游戏目录拷走，工具跟着走。

**快捷方式**：`桌面\game\SPT单机启动.lnk`
→ `pythonw.exe "<SPT根>\SPTSoloStart\spt_solostart.py"`，工作目录同文件夹，图标取 `SPT.Launcher.exe,0`。

## 四、命令行

```bash
python spt_solostart.py              # 正常跑（遇到可疑情况弹窗确认）
python spt_solostart.py --dry-run    # 只判定不写入、不启动（全自动，绝不弹窗）
python spt_solostart.py --yes        # 所有确认环节自动选「继续」（自动化/批量）
```

退出码：`0` 成功 · `2` 结构不符 · `3` 推不出 profileId · `4` 进程在跑且主人选了中止 · `5` 端口被别人占用且主人选了中止 · `1` 未捕获异常。

## 五、配置文件（config.json）

| 字段 | 默认 | 说明 |
|------|------|------|
| `profile_id` | `""` | 留空=自动（LauncherSettings.PreferredProfile → `SPT\user\fika` 里最新的目录） |
| `sync_profile` | `true` | 是否启用存档同步（false = 纯启动器） |
| `extra_source_dirs` | `[]` | 额外存档来源目录。填了就会一起比时间（例：QQ 接收文件夹 / 下载夹）。文件名带 profileId 的按名匹配，不带的按内容识别（只查最近 25 个 json） |
| `only_if_newer` | `true` | 仅当来源比本地档新才覆盖（**建议保持 true**：单机玩完后再启动，不会用旧下载档把新进度冲掉） |
| `confirm_on_skip_process` | `true` | 检测到进程在跑时**弹窗确认**（false = 只记日志、默默用旧档继续） |
| `port_owner_guard` | `true` | 6969 被「非本目录的服务端」占用时**弹窗拦截**（false = 仅警告） |
| `backup_dir` | `SPT_Runtime/user/profiles/backups_manual` | 覆盖前备份落点（自建目录，不动原生 `backups\`、`bak\`） |
| `keep_backups` | `20` | 只保留最近 N 份**本工具自建**的档案备份 |
| `keep_launcher_backups` | `10` | 只保留最近 N 份**本工具自建**的 `LauncherSettings.json.<时间戳>.bak` |
| `start_server` | `true` | 是否启动 SPT 服务端 |
| `start_launcher` | `true` | 是否启动 Launcher |
| `server_new_console` | `true` | 服务端**强制新开独立控制台窗口**（就是那个黑窗口）。开着才能实时看启动日志/报错；关掉的话出问题只能翻日志文件 |
| `server_wait_seconds` | `150` | 等服务端就绪的上限 |
| `spt_port` | `6969` | 本地服务端口 |
| `local_server_id` | `1721162719` | SPT Launcher 内置「本地服务器」的 ServerId（由 `sp-tarkov/launcher` 源码 `Server.LocalServerId` 得出，一般不用改） |
| `auto_connect_local` | `true` | 把 Launcher 自动连接目标切到本地服务器 |

## 六、三道安全闸门（为什么值得信任）

### 闸门 1 · 进程在跑 → 弹窗，不再静默

单机工具和**当 host 联机时用的是同一个** `SPT_Runtime\SPT.Server.exe`、**同一个 6969 端口**。
残留进程没退干净时，最阴的结局是：同步被跳过（你没察觉）→ Launcher 连上**残留的联机服务端** →
跑的是它内存里的档，退出时还回写覆盖。

所以检测到 `SPT.Server.exe` / `SPT.Launcher.exe` / `EscapeFromTarkov.exe` 在跑时：
**弹窗**（标题 `SPT 单机启动`，默认按钮落「否」），说清「本次不会同步、现在启动读的是上次的档」，
主人选「否」→ 未做任何写入、未启动任何程序，直接退出。

### 闸门 2 · 6969 占用者身份校验

以前只要 6969 在监听就当成「本地服务端已在运行」，直接启 Launcher —— **连错服务器也不自知**。
现在先查监听者的 `PID / 进程名 / 完整路径 / 启动时间`：

| 情况 | 处理 |
|------|------|
| 占用者就是本目录的 `SPT_Runtime\SPT.Server.exe` | 视为「本地服务端已在运行」，跳过启动，日志打印占用者详情 |
| 路径读不到，但进程名就是 `SPT.Server.exe` | 同上（降级判定，记明原因） |
| 占用者是**别的东西** | ⚠️ 弹窗列出占用者四要素 + 后果说明，默认「否」= 中止整个启动 |

路径比对做了双重保险：绝对路径不匹配时退化成「比对末尾两级」，避免中文路径（`D:\free games\`）
被工具链弄乱码后误判。Windows 上取进程信息优先 PowerShell（输出走 UTF-8→base64，绕开代码页乱码），
失败再退 `wmic`。

### 闸门 3 · LauncherSettings 备份带时间戳

改 `LauncherSettings.json` 前不再每次覆盖同一个 `.bak`，而是写
`LauncherSettings.json.<YYYYmmdd_HHMMSS>.bak`，按 `keep_launcher_backups` 滚动（默认留 10 份）。
**主人原有的 `LauncherSettings.json.bak` 不在清理范围内**（glob 只匹配带时间戳的那种）。

## 七、安全边界（硬规矩）

- 只写这几处：
  - `SPT_Runtime\user\profiles\<自己的id>.json`
  - `SPT_Runtime\user\profiles\backups_manual\`（自建）
  - `SPT_Runtime\user\sptappdata\spt_solostart_logs\`（自建）
  - `SPT_Runtime\user\Launcher\LauncherSettings.json`（仅改 `PreferredProfile` / `AutoConnectLastProfile`）
  - `SPT_Runtime\user\Launcher\LauncherSettings.json.<时间戳>.bak`（自建）
- **绝不删除任何既有文件**；`keep_backups` / `keep_launcher_backups` 只清理**本工具自己生成的**文件
- 只处理**自己的 profileId**，别人的档案一概不碰
- 进程在跑 → 绝不同步（防止内存旧档回写覆盖新档）
- 来源 JSON 解析失败 / 结构不像 SPT 档案 / id 不匹配 → 拒绝覆盖，宁可不用
- 幂等：内容一致就不动文件，可反复双击
- `--dry-run` 全程无弹窗、无写入、无启动

## 八、自检

```bash
cd <SPT根>\SPTSoloStart
python test_solo_guard.py
```

18 项断言：端口占用者识别（起一个假占用者验证 netstat→PID→进程名→路径→启动时间整条链路）、
四种分支判定（本目录服务端 / 外人占用 / guard 关闭 / dry-run 不弹窗）、
中文路径乱码时的退化比对、备份滚动与「老 `.bak` 永不动」。
**只读 + 只用系统临时目录**：不写游戏档案、不启服务端/Launcher、不碰真实 `LauncherSettings.json`。

## 九、排障

- **服务端窗口**：双击启动后会出现一个独立的控制台窗口（`SPT.Server.exe`），
  里面实时刷 `服务端已开启，游戏愉快` / 请求日志 / 报错。看到它 = 服务端起得来。
  ⚠️ 在 Win11 上这个窗口通常**由 Windows Terminal 托管**，标题是服务端自己设的构建名
  （如 **`SPT 4.1.5`**），任务栏/Alt+Tab 里显示的也是这个名字 —— 不是「找不到窗口」，
  窗口本来就不属于服务端进程（所以用 `Get-Process SPT.Server` 看 `MainWindowHandle` 会是 0）。
  （若被 `server_new_console:false` 关掉了，就只能看日志文件了。）
- 日志：`<SPT根>\SPT_Runtime\user\sptappdata\spt_solostart_logs\spt_solostart.log`
- 服务端自己的日志：`<SPT根>\SPT_Runtime\user\logs\spt\spt<日期>.log`
- Launcher 日志：`<SPT根>\SPT_Runtime\user\logs\Launcher.log`
- **弹窗「检测到这些进程还在运行」**：正常闸门。想同步新档 → 点「否」，完全退出游戏/服务端/启动器，再双击
- **弹窗「6969 端口已被占用…不是本目录的服务端」**：多半是残留的联机服务端或别的程序抢了端口。
  点「否」，`taskkill /F /PID <弹窗里的PID>` 或任务管理器结束它，再双击
- 「没找到任何可用的存档来源」：先去游戏里点一次「下载存档」；或把存档所在文件夹填进 `extra_source_dirs`
- 「等待服务端监听超时」：看 SPT.Server.exe 窗口的报错（多半是 .NET 运行库或端口占用）
- 想确认判定结果而不动手：双击 `dry_run.bat`

## 十、实战复盘（2026-09-20 联机 → 单机）

主人在 Fika 联机结束前点了「下载存档」，22:53 干跑实测：

```
本地档: 69568f09….json (2252316 B, 2026-09-15 19:00)     ← 单机侧上一次
候选来源（新→旧）:
  - [Fika 游戏内「下载存档」] …fika\69568f09…\69568f09….json (4166269 B, 2026-09-20 22:47)
[DRY-RUN] 应将 [Fika 游戏内「下载存档」] 覆盖到 69568f09….json（Lv.39 / 物品 3707 / 完成任务 80）
[DRY-RUN] 应把 Launcher 自动连接目标设为 本地服务器(1721162719)
```

四道校验全过 —— 也就是**「刚联机结束的存档」可以直接用来单机启动**：

| 校验层 | 结果 | 依据 |
|---|---|---|
| profileId 推导 | ✅ | `PreferredProfile.ProfileId` 仍正确；虽然 `ServerId` 还指着联机服务器 JR(`1786275933`) 也照样推得出（角色 ID 跨服务器不变） |
| 时间判定 | ✅ | 下载档 `09-20 22:47` > 本地档 `09-15 19:00` → `only_if_newer` 放行 |
| 内容合法性 | ✅ | 纯 UTF-8 JSON（**非压缩**，与本地档格式一致）· 顶层键完全相同 · `info.id` 匹配 · `inraid` 为空 |
| 进度确实新 | ✅ | 本地 **Lv.38 / 3612 物品 / 76 任务** → 下载档 **Lv.39 / 3707 物品 / 80 任务** |

两个已知的**正常现象**，别误判成工具坏了：
- **下载存档是快照**：下载之后又联机玩过但没再点下载 → 单机拿到的是上次下载点的进度（Fika 机制本身）
- **物品数可能变少**：同期 `.BAK` 有 3731 物品、新下载档 3707 物品 → 联机期间的战损/消耗真实反映

## 十一、机制来源（可复核）

| 结论 | 出处 |
|------|------|
| 「下载存档」写到 `SPT\user\fika\<id>\<id>.json`（并留 `.json.BAK`） | `project-fika/Fika-Plugin` → `Fika.Core/UI/Patches/MenuTaskBar_Patch.cs` |
| 服务端没改档案存储，走 SPT 原生 `SaveServer` | `project-fika/Fika-Server-CSharp`（`SaveProfileAsync` / `GetProfile`） |
| 本地服务器 ServerId = `1721162719`，固定 `127.0.0.1:6969`，恒在服务器列表首位 | `sp-tarkov/launcher` → `SPTarkov.Core/Configuration/Server.cs` |
| 自动连接逻辑：读 `PreferredProfile` → 连服务器 → 登录档案 | 同上 → `SPTarkov.Launcher/Pages/Home.razor:TryAutoConnect()` |
| 单机与当 host 联机用的是同一个 `SPT_Runtime\SPT.Server.exe` / 同一个 6969 | 本机实测（`FikaAutoConnect` 只改 LauncherSettings 里的服务器条目 IP，服务端仍是本地这个 exe） |
