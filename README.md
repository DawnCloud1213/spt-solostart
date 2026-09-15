# SPTSoloStart — SPT 单机启动 + 联机存档自动同步

## 这是干嘛的

联机（Fika）结束后，主人在游戏里点「**下载存档**」，档案会被写到：

```
<SPT根>\SPT\user\fika\<profileId>\<profileId>.json
```

而单机（自建服）实际读 / 写的是**另一条路径**：

```
<SPT根>\SPT_Runtime\user\profiles\<profileId>.json
```

两边从来不会自动对接 → 单机启动永远读到联机前的旧档，而且旧档一进一出还会被回写钉死。

本工具双击即用：

1. 检查没有 SPT / 游戏进程在跑（在跑就跳过同步，防止内存里的旧档回写覆盖新档）
2. 选出最新的「自己的档」（游戏内下载档 / 本地档 / 额外目录，比文件时间）
3. 比本地档新 → **先备份旧档** → 覆盖到 `profiles\<id>.json`
4. 把 Launcher 的自动连接目标切到「本地服务器」
5. 启 `SPT.Server.exe` → 等 `127.0.0.1:6969` 监听成功 → 启 `SPT.Launcher.exe`

> Launcher 起来后会自动连本地服务器并登录档案，主人只需点「**开始游戏**」。

## 部署位置

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
    ├── config.json
    └── README.md
```

脚本用「自身位置上一级」自动推导 SPT 根（`SPT_ROOT = 脚本上一级`），**零配置**；
搬运 = 整个游戏目录拷走，工具跟着走。

**快捷方式**：`桌面\game\SPT单机启动.lnk`
→ `pythonw.exe "<SPT根>\SPTSoloStart\spt_solostart.py"`，工作目录同文件夹，图标取 `SPT.Launcher.exe,0`。

## 配置文件（config.json）

| 字段 | 默认 | 说明 |
|------|------|------|
| `profile_id` | `""` | 留空=自动（LauncherSettings.PreferredProfile → `SPT\user\fika` 里最新的目录） |
| `sync_profile` | `true` | 是否启用存档同步（false = 纯启动器） |
| `extra_source_dirs` | `[]` | 额外存档来源目录。填了就会一起比时间（例：QQ 接收文件夹 / 下载夹）。文件名带 profileId 的按名匹配，不带的按内容识别（只查最近 25 个 json） |
| `only_if_newer` | `true` | 仅当来源比本地档新才覆盖（**建议保持 true**：单机玩完后再启动，不会用旧下载档把新进度冲掉） |
| `backup_dir` | `SPT_Runtime/user/profiles/backups_manual` | 覆盖前备份落点（自建目录，不动原生 `backups\`、`bak\`） |
| `keep_backups` | `20` | 只保留最近 N 份**本工具自建**的备份 |
| `start_server` | `true` | 是否启动 SPT 服务端 |
| `start_launcher` | `true` | 是否启动 Launcher |
| `server_new_console` | `true` | 服务端**强制新开独立控制台窗口**（就是那个黑窗口）。开着才能实时看启动日志/报错；关掉的话出问题只能翻日志文件 |
| `server_wait_seconds` | `150` | 等服务端就绪的上限 |
| `spt_port` | `6969` | 本地服务端口 |
| `local_server_id` | `1721162719` | SPT Launcher 内置「本地服务器」的 ServerId（由 `sp-tarkov/launcher` 源码 `Server.LocalServerId` 得出，一般不用改） |
| `auto_connect_local` | `true` | 把 Launcher 自动连接目标切到本地服务器 |

## 安全边界（硬规矩）

- 只写四处：`profiles\<自己的id>.json`、`backups_manual\`（自建）、日志目录（自建）、`LauncherSettings.json`（只改 `PreferredProfile` / `AutoConnectLastProfile`，改前留 `.bak`）
- **绝不删除任何既有文件**；`keep_backups` 只清理本工具自己创建的历史备份
- 只处理**自己的 profileId**，别人的档案一概不碰
- 进程在跑 → 绝不同步（防止内存旧档回写覆盖新档）
- 来源 JSON 解析失败 → 拒绝覆盖，宁可不用
- 幂等：内容一致就不动文件，可反复双击

## 排障

- **服务端窗口**：双击启动后会出现一个独立的控制台窗口（`SPT.Server.exe`），
  里面实时刷 `服务端已开启，游戏愉快` / 请求日志 / 报错。看到它 = 服务端起得来。
  ⚠️ 在 Win11 上这个窗口通常**由 Windows Terminal 托管**，标题是服务端自己设的构建名
  （如 **`SPT 4.1.5`**），任务栏/Alt+Tab 里显示的也是这个名字 —— 不是「找不到窗口」，
  窗口本来就不属于服务端进程（所以用 `Get-Process SPT.Server` 看 `MainWindowHandle` 会是 0）。
  （若被 `server_new_console:false` 关掉了，就只能看日志文件了。）
- 日志：`<SPT根>\SPT_Runtime\user\sptappdata\spt_solostart_logs\spt_solostart.log`
- 服务端自己的日志：`<SPT根>\SPT_Runtime\user\logs\spt\spt<日期>.log`
- Launcher 日志：`<SPT根>\SPT_Runtime\user\logs\Launcher.log`
- 「检测到进程在运行 → 跳过存档同步」：先完全退出游戏 / 服务端 / 启动器，再双击
- 「没找到任何可用的存档来源」：先去游戏里点一次「下载存档」；或把存档所在文件夹填进 `extra_source_dirs`
- 「等待服务端监听超时」：看 SPT.Server.exe 窗口的报错（多半是 .NET 运行库或端口占用）
- 想确认判定结果而不动手：双击 `dry_run.bat`

## 机制来源（可复核）

| 结论 | 出处 |
|------|------|
| 「下载存档」写到 `SPT\user\fika\<id>\<id>.json`（并留 `.json.BAK`） | `project-fika/Fika-Plugin` → `Fika.Core/UI/Patches/MenuTaskBar_Patch.cs` |
| 服务端没改档案存储，走 SPT 原生 `SaveServer` | `project-fika/Fika-Server-CSharp`（`SaveProfileAsync` / `GetProfile`） |
| 本地服务器 ServerId = `1721162719`，固定 `127.0.0.1:6969`，恒在服务器列表首位 | `sp-tarkov/launcher` → `SPTarkov.Core/Configuration/Server.cs` |
| 自动连接逻辑：读 `PreferredProfile` → 连服务器 → 登录档案 | 同上 → `SPTarkov.Launcher/Pages/Home.razor:TryAutoConnect()` |
