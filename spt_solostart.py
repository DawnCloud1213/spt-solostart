#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
spt_solostart.py — SPT 单机启动 + 联机存档自动同步

【为什么需要它】
Fika 联机结束后在游戏里点「下载存档」，档案落在「服务端根 \\ user \\ fika」：
    <SPT根>\\SPT_Runtime\\user\\fika\\<profileId>\\<profileId>.json   ← SPT 4.1.x 现行落点
    <SPT根>\\SPT\\user\\fika\\<profileId>\\<profileId>.json           ← 旧安装结构（服务端曾在游戏根的 SPT\\ 里）
而单机（自建服）实际读/写的是：
    <SPT根>\\SPT_Runtime\\user\\profiles\\<profileId>.json
两条路径不自动对接 → 单机启动总是读到联机之前的旧档，还被旧档回写钉死。

【本脚本做什么】双击桌面「SPT单机启动」：
  1) 安全检查：SPT/游戏进程在跑 → 弹窗拦住，由主人决定「中止」还是「明知用旧档也要继续」
  2) 选最新的「自己的档」：全部「下载存档」候选落点 / 本地档 / 可选额外目录
     —— 判据 = 账号 aid 归属校验 + PMC 经验(主) → scav 经验(次) → mtime(兜底)；
        经验倒退一律拒覆盖（防复制/解压把旧档的时间戳刷成「最新」造成回滚）
  3) 比本地新 → 先备份本地档 → 覆盖到 profiles\\<id>.json
  4) 把 Launcher 自动连接目标指向「本地服务器」（ServerId=1721162719）
  5) 启服务端前先验 6969 端口占用者身份（必须就是本目录的 SPT.Server.exe）
  6) 启 SPT.Server.exe → 等 6969 监听 → 启 SPT.Launcher.exe

【写入边界（硬规矩）】
  只写这几处：
    - SPT_Runtime\\user\\profiles\\<自己的 profileId>.json
    - SPT_Runtime\\user\\profiles\\backups_manual\\（自建备份目录）
    - SPT_Runtime\\user\\sptappdata\\spt_solostart_logs\\（自建日志）
    - SPT_Runtime\\user\\Launcher\\LauncherSettings.json（仅改 PreferredProfile / AutoConnectLastProfile）
    - SPT_Runtime\\user\\Launcher\\LauncherSettings.json.<时间戳>.bak（自建备份）
  绝不删除任何既有文件；自建备份按 keep_* 上限滚动清理（只清理本工具自己生成的）。
  只处理自己的 profileId，别人的档案一概不碰。

用法：
    python spt_solostart.py              # 正常跑（遇到可疑情况弹窗确认）
    python spt_solostart.py --dry-run    # 只判定不写入、不启动（验证用）
    python spt_solostart.py --yes        # 所有确认环节自动选「继续」（自动化/批量用）
"""

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ==================== 默认配置（同目录 config.json 可覆盖） ====================
SPT_PORT          = 6969                      # 本地 SPT 服务端口
LOCAL_SERVER_ID   = "1721162719"              # SPT Launcher 内置「本地服务器」的 ServerId（sp-tarkov/launcher: Server.LocalServerId）
PROFILE_ID        = ""                        # 留空=自动推导（LauncherSettings.PreferredProfile -> 各「下载存档」落点里进度最新者）
SYNC_PROFILE      = True                      # 是否启用存档同步
FIKA_SOURCE_DIRS  = []                        # 「下载存档」额外落点（绝对路径或相对 SPT 根）；留空=只用内置候选
EXTRA_SOURCE_DIRS = []                        # 额外存档来源目录（如 QQ 接收文件夹/下载夹），留空=只用游戏内「下载存档」落点
ONLY_IF_NEWER     = True                      # 仅当来源比本地档新才覆盖（强烈建议保持 true）
ALLOW_EXP_ROLLBACK = False                    # 允许「来源经验比本地低」也覆盖（默认 False=硬闸拒绝，防旧档回滚掉进度）
BACKUP_DIR_REL    = "SPT_Runtime/user/profiles/backups_manual"
KEEP_BACKUPS      = 20                        # 只保留最近 N 份本工具自建备份
START_SERVER      = True
START_LAUNCHER    = True
SERVER_WAIT_SECS  = 150                       # 等 6969 监听的上限（秒）
AUTO_CONNECT_LOCAL = True                     # 把 Launcher 自动连接目标切到本地服务器
SERVER_NEW_CONSOLE = True                     # 服务端强制新开独立控制台窗口（方便主人看报错；关掉就看不到实时刷屏了）
CONFIRM_ON_SKIP_PROCESS = True                # 检测到进程在跑 → 弹窗确认（关掉=只记日志、默默用旧档继续）
PORT_OWNER_GUARD        = True                # 6969 被「不是本目录的服务端」占用 → 弹窗拦截（关掉=仅警告）
KEEP_LAUNCHER_BACKUPS   = 10                  # LauncherSettings 时间戳备份保留份数（只清理本工具自己生成的）

RUNTIME_REL           = Path("SPT_Runtime")
LAUNCHER_SETTINGS_REL = RUNTIME_REL / "user" / "Launcher" / "LauncherSettings.json"
PROFILES_REL          = RUNTIME_REL / "user" / "profiles"
# 游戏内「下载存档」候选落点 —— Fika 写在「服务端根 \ user \ fika」下，而服务端根随 SPT 安装结构变过：
#   SPT 4.1.x：服务端在 SPT_Runtime\    → 落 SPT_Runtime\user\fika   （现行，实测 2026-10）
#   旧结构    ：服务端在游戏根的 SPT\     → 落 SPT\user\fika
# 三个全扫，按「内容进度」挑最新；config.fika_source_dirs 还能再追加别的（绝对路径或相对 SPT 根）。
FIKA_SOURCE_DIRS_REL  = [
    Path("SPT_Runtime") / "user" / "fika",
    Path("SPT") / "user" / "fika",
    Path("user") / "fika",
]
LOG_REL               = RUNTIME_REL / "user" / "sptappdata" / "spt_solostart_logs"
SERVER_EXE_REL        = RUNTIME_REL / "SPT.Server.exe"
LAUNCHER_EXE_REL      = RUNTIME_REL / "SPT.Launcher.exe"

GAME_PROCESSES   = ("SPT.Server.exe", "SPT.Launcher.exe", "EscapeFromTarkov.exe")
SCAN_SIZE_MIN    = 100 * 1024          # 内容扫描时忽略过小的 json
SCAN_SIZE_MAX    = 60 * 1024 * 1024    # 以及过大的
SCAN_MAX_FILES   = 25                  # 每个额外目录最多内容校验多少个候选
# ==============================================================================

DRY_RUN = "--dry-run" in sys.argv
ASSUME_YES = ("--yes" in sys.argv) or ("-y" in sys.argv)   # 所有确认环节自动选「继续」
CFG = {}
SPT_ROOT = Path(__file__).resolve().parent.parent   # 脚本位于 <SPT根>/SPTSoloStart/


# ------------------------------- 基础工具 -------------------------------

def log(msg: str):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        logdir = SPT_ROOT / LOG_REL
        logdir.mkdir(parents=True, exist_ok=True)
        with open(logdir / "spt_solostart.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _msgbox(text: str, title: str = "SPT 单机启动"):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x40)
    except Exception:
        pass


def _ask_yes_no(text: str, title: str = "SPT 单机启动", default_yes: bool = False) -> bool:
    """弹窗二选一：True=继续 / False=中止。

    - --yes：直接「继续」（自动化/批量场景）
    - 非 Windows / 无桌面 / 弹窗失败：按 default_yes 返回，并记日志，绝不静默吞掉
    """
    if ASSUME_YES:
        log("  （--yes：确认环节自动选择「继续」）")
        return True
    if os.name != "nt":
        log(f"  !! 非 Windows 环境，无法弹窗确认 → 按默认「{'继续' if default_yes else '中止'}」处理")
        return default_yes
    try:
        import ctypes
        MB_YESNO, MB_ICONWARNING, MB_DEFBUTTON2, IDYES = 0x04, 0x30, 0x100, 6
        # MB_DEFBUTTON2 → 回车默认落在「否」，避免手滑直接带旧档开跑
        r = ctypes.windll.user32.MessageBoxW(0, text, title,
                                             MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2)
        if r == IDYES:
            log("  确认：继续")
            return True
        log("  确认：中止（未做任何写入，未启动任何程序）")
        return False
    except Exception as e:
        log(f"  !! 弹窗确认失败（{e}）→ 按默认「{'继续' if default_yes else '中止'}」处理")
        return default_yes


def load_config():
    global SPT_PORT, LOCAL_SERVER_ID, PROFILE_ID, SYNC_PROFILE, EXTRA_SOURCE_DIRS, \
        FIKA_SOURCE_DIRS, ONLY_IF_NEWER, ALLOW_EXP_ROLLBACK, BACKUP_DIR_REL, KEEP_BACKUPS, \
        START_SERVER, START_LAUNCHER, \
        SERVER_WAIT_SECS, AUTO_CONNECT_LOCAL, SERVER_NEW_CONSOLE, \
        CONFIRM_ON_SKIP_PROCESS, PORT_OWNER_GUARD, KEEP_LAUNCHER_BACKUPS, CFG
    cfg_path = Path(__file__).resolve().parent / "config.json"
    if cfg_path.exists():
        try:
            CFG = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception as e:
            log(f"!! config.json 解析失败，使用默认配置: {e}")
            CFG = {}
    SPT_PORT           = int(CFG.get("spt_port", SPT_PORT))
    LOCAL_SERVER_ID    = str(CFG.get("local_server_id", LOCAL_SERVER_ID))
    PROFILE_ID         = str(CFG.get("profile_id", PROFILE_ID) or "")
    SYNC_PROFILE       = bool(CFG.get("sync_profile", SYNC_PROFILE))
    EXTRA_SOURCE_DIRS  = list(CFG.get("extra_source_dirs", EXTRA_SOURCE_DIRS) or [])
    FIKA_SOURCE_DIRS   = list(CFG.get("fika_source_dirs", FIKA_SOURCE_DIRS) or [])
    ONLY_IF_NEWER      = bool(CFG.get("only_if_newer", ONLY_IF_NEWER))
    ALLOW_EXP_ROLLBACK = bool(CFG.get("allow_exp_rollback", ALLOW_EXP_ROLLBACK))
    BACKUP_DIR_REL     = str(CFG.get("backup_dir", BACKUP_DIR_REL))
    KEEP_BACKUPS       = int(CFG.get("keep_backups", KEEP_BACKUPS))
    START_SERVER       = bool(CFG.get("start_server", START_SERVER))
    START_LAUNCHER     = bool(CFG.get("start_launcher", START_LAUNCHER))
    SERVER_WAIT_SECS   = int(CFG.get("server_wait_seconds", SERVER_WAIT_SECS))
    AUTO_CONNECT_LOCAL = bool(CFG.get("auto_connect_local", AUTO_CONNECT_LOCAL))
    SERVER_NEW_CONSOLE = bool(CFG.get("server_new_console", SERVER_NEW_CONSOLE))
    CONFIRM_ON_SKIP_PROCESS = bool(CFG.get("confirm_on_skip_process", CONFIRM_ON_SKIP_PROCESS))
    PORT_OWNER_GUARD        = bool(CFG.get("port_owner_guard", PORT_OWNER_GUARD))
    KEEP_LAUNCHER_BACKUPS   = int(CFG.get("keep_launcher_backups", KEEP_LAUNCHER_BACKUPS))


def _run_capture(cmd, timeout=30):
    """跑命令并拿 stdout（pythonw 下不弹窗）"""
    flags = 0x08000000 if os.name == "nt" else 0   # CREATE_NO_WINDOW
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           creationflags=flags, errors="replace")
        return p.stdout or ""
    except Exception as e:
        log(f"!! 执行 {cmd} 失败: {e}")
        return ""


def running_processes():
    """返回正在运行的目标进程名集合"""
    out = _run_capture(["tasklist", "/NH", "/FO", "CSV"])
    found = set()
    low = out.lower()
    for name in GAME_PROCESSES:
        if name.lower() in low:
            found.add(name)
    return found


def port_listening(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def _b64_ps(script: str, timeout: int = 25):
    """跑一段 PowerShell，输出按 UTF-8→base64 取回（规避中文路径/代码页乱码）"""
    import base64
    wrapper = (script +
               ";if($o.Count){[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($o -join \"`n\")))}")
    out = _run_capture(["powershell", "-NoProfile", "-NonInteractive", "-Command", wrapper],
                       timeout=timeout).strip()
    if not out:
        return []
    try:
        text = base64.b64decode(out).decode("utf-8", "replace")
    except Exception:
        return []
    return [l.strip() for l in text.splitlines() if l.strip()]


def _ps_exec_info(pid: int):
    """用 PowerShell 取某进程的可执行路径 + 启动时间 → (path, start_time)，取不到给 (None, None)"""
    script = ("$ErrorActionPreference='SilentlyContinue';$o=@();"
              f"$p=Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\";"
              "if($p){$o+=$p.ExecutablePath;"
              f"$s=(Get-Process -Id {pid}).StartTime;"
              "if($s){$o+='START='+$s.ToString('yyyy-MM-dd HH:mm:ss')}}")
    lines = _b64_ps(script)
    if not lines:
        return None, None
    path, start = None, None
    for l in lines:
        if l.upper().startswith("START="):
            start = l.split("=", 1)[1].strip()
        elif not path:
            path = l
    return (path or None), start


def _wmic_exec_path(pid: int):
    """PowerShell 不可用时的兜底：wmic 取可执行路径（中文可能乱码，只信末两级）"""
    out = _run_capture(["wmic", "process", "where", f"processid={pid}",
                        "get", "ExecutablePath", "/value"], timeout=20)
    for line in out.splitlines():
        if line.strip().lower().startswith("executablepath="):
            v = line.split("=", 1)[1].strip()
            if v:
                return v
    return None


def port_owner(port: int):
    """查出正在监听 <port> 的进程 → dict(pid, name, exe, start)；查不到返回 None"""
    out = _run_capture(["netstat", "-ano", "-p", "TCP"], timeout=25)
    pid = None
    for line in (out or "").splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        if parts[3].upper() == "LISTENING" and parts[1].endswith(f":{port}"):
            pid = parts[4].strip()
            break
    if not pid or pid == "0":
        return None

    name = None
    tl = _run_capture(["tasklist", "/NH", "/FO", "CSV", "/FI", f"PID eq {pid}"], timeout=25)
    for line in (tl or "").splitlines():
        if line.strip():
            name = line.split('","')[0].lstrip('"').strip()
            break

    exe, start = _ps_exec_info(int(pid))
    if not exe:
        exe = _wmic_exec_path(int(pid))
    if not name and exe:
        name = os.path.basename(exe)
    return {"pid": pid, "name": name or "?", "exe": exe, "start": start}


def describe_owner(o) -> str:
    """把 port_owner 结果写成一行人类可读的说明"""
    if not o:
        return "占用者未知（netstat 没给出 LISTENING 行）"
    bits = [f"PID {o['pid']}", f"进程 {o['name']}"]
    if o.get("start"):
        bits.append(f"启动于 {o['start']}")
    bits.append(f"路径 {o['exe']}" if o.get("exe") else "路径取不到（权限不足？）")
    return "｜".join(bits)


def _same_exe(path, target: Path) -> bool:
    """判断监听进程是不是「本目录的」那个 exe（中文路径乱码时退化成比对末尾两级）"""
    if not path:
        return False
    try:
        if os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(str(target))):
            return True
    except Exception:
        pass
    try:
        a = [p.lower() for p in Path(str(path)).parts[-2:]]
        b = [p.lower() for p in Path(str(target)).parts[-2:]]
        return len(a) == 2 and a == b
    except Exception:
        return False


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def same_content(a: Path, b: Path) -> bool:
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
        return sha256_of(a) == sha256_of(b)
    except Exception:
        return False


def expand_dir(p: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(p))))


# --------------------------- 存档候选与校验 ---------------------------

def fika_roots():
    """所有「下载存档」候选根（只返回真实存在的目录，去重保序）。

    为什么是复数：Fika 把档写在「服务端根 \\ user \\ fika」，而服务端根随 SPT 安装结构变过
    （4.1.x 在 SPT_Runtime\\，旧结构在游戏根的 SPT\\），所以内置多个候选 + config 追加。
    """
    roots, seen = [], set()
    rels = list(FIKA_SOURCE_DIRS_REL) + [expand_dir(p) for p in FIKA_SOURCE_DIRS]
    for rel in rels:
        p = rel if rel.is_absolute() else (SPT_ROOT / rel)
        key = str(p).lower()
        if key in seen:
            continue
        seen.add(key)
        if p.is_dir():
            roots.append(p)
    return roots


def read_profile_meta(path: Path):
    """读一份档案的「元信息」；读不出（损坏 / 不是档案）返回 None。

    aid = 账号号（防把别人的档同步进来）；pid = profileId；user = 昵称；
    pmc_exp / scav_exp = 总经验（判「谁更新」的主判据）；mtime = 文件时间戳（兜底）。
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "characters" not in data:
            return None
        ch = data.get("characters") or {}
        info = data.get("info") or {}

        def _exp(tag):
            v = ((ch.get(tag) or {}).get("Info") or {}).get("Experience")
            return int(v) if isinstance(v, (int, float)) else 0

        return {
            "aid": info.get("aid"),
            "pid": info.get("id"),
            "user": info.get("username") or "?",
            "pmc_exp": _exp("pmc"),
            "scav_exp": _exp("scav"),
            "mtime": path.stat().st_mtime,
        }
    except Exception:
        return None


def profile_progress(meta):
    """进度指纹：(PMC 经验, Scav 经验, mtime) —— 经验优先，mtime 只在前两项并列时兜底。

    为什么不只看 mtime：复制 / 解压 / 拖动 / 改名 / 换盘都会重置文件时间戳，
    旧档会冒充「最新」，覆盖上去 = 回滚掉主人的进度（掉经验、丢装备 + 物品）。
    经验值正常游玩只增不减，才配当「谁更新」的判据。
    """
    if not meta:
        return None
    return (meta["pmc_exp"], meta["scav_exp"], meta["mtime"])


def progress_label(meta) -> str:
    """日志用的一行进度说明"""
    if not meta:
        return "（经验读不出）"
    return f"PMC 经验 {meta['pmc_exp']:,} / Scav 经验 {meta['scav_exp']:,}"


def valid_profile(path: Path, profile_id: str):
    """校验是不是一个合法且属于该 profileId 的档案；返回 (bool, 摘要字符串)"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return False, f"JSON 解析失败 ({e})"
    if not isinstance(data, dict) or "characters" not in data:
        return False, "结构不像 SPT 档案（缺 characters）"
    pid = (data.get("info") or {}).get("id")
    if pid and profile_id and pid != profile_id:
        return False, f"档案 id 不匹配（{pid}）"
    try:
        pmc = data["characters"]["pmc"]
        pinfo = pmc.get("Info") or {}
        level = pinfo.get("Level", "?")
        exp = int(pinfo.get("Experience") or 0)
        items = len((pmc.get("Inventory") or {}).get("items", []))
        quests = sum(1 for q in pmc.get("Quests", []) if q.get("status") == 4)
        inraid = data.get("inraid") or data.get("characters", {}).get("pmc", {}).get("InRaid")
        raid_note = "（战局中保存的档）" if inraid else ""
        return True, f"Lv.{level} / 经验 {exp:,} / 物品 {items} / 完成任务 {quests}{raid_note}"
    except Exception:
        return True, "(摘要读取失败)"


def resolve_profile_id(settings_path: Path):
    """确定目标 profileId：config -> LauncherSettings.PreferredProfile -> fika 目录里最新的"""
    if PROFILE_ID:
        log(f"profileId 取配置: {PROFILE_ID}")
        return PROFILE_ID
    if settings_path.exists():
        try:
            pref = (json.loads(settings_path.read_text(encoding="utf-8")) or {}).get("PreferredProfile") or {}
            pid = pref.get("ProfileId")
            if pid:
                log(f"profileId 取 LauncherSettings.PreferredProfile: {pid}")
                return pid
        except Exception:
            pass
    # 兜底：所有「下载存档」候选落点里，按进度挑最新的目录名当 profileId
    fika_cands = []
    for root in fika_roots():
        for d in root.iterdir():
            f = d / f"{d.name}.json"
            if d.is_dir() and f.exists():
                fika_cands.append(f)
    if fika_cands:
        best = max(fika_cands, key=lambda p: profile_progress(read_profile_meta(p)) or (0, 0, 0.0))
        log(f"profileId 取「下载存档」候选里进度最新者: {best.stem}"
            f"（来自 {best.parent.parent}）")
        return best.stem
    return None


def find_source_candidates(profile_id: str):
    """返回 [(path, 来源说明, meta)]，按「进度」从新到旧（PMC 经验 → Scav 经验 → mtime）"""
    cands = []

    # ① 游戏内「下载存档」落点：<服务端根>\\user\\fika\\<id>\\<id>.json —— 多个候选根全扫
    for root in fika_roots():
        try:
            label = str(root.relative_to(SPT_ROOT))
        except Exception:
            label = str(root)
        f = root / profile_id / f"{profile_id}.json"
        if f.exists():
            cands.append((f, f"Fika「下载存档」({label})"))
            continue
        # 目录名不是 profileId 时（Fika 改了命名 / 手动往里放档）：按内容匹配（限量）
        subs = [d for d in root.iterdir() if d.is_dir() and (d / f"{d.name}.json").exists()]
        subs.sort(key=lambda d: (d / f"{d.name}.json").stat().st_mtime, reverse=True)
        for d in subs[:SCAN_MAX_FILES]:
            p = d / f"{d.name}.json"
            if valid_profile(p, profile_id)[0]:
                cands.append((p, f"Fika「下载存档」({label}，按内容匹配)"))
                break

    # ② 额外来源目录
    for raw in EXTRA_SOURCE_DIRS:
        d = expand_dir(raw)
        if not d.is_dir():
            log(f"  (跳过不存在的额外来源目录: {d})")
            continue
        hits = [p for p in d.glob(f"{profile_id}*.json") if p.is_file()]
        if hits:
            for p in hits:
                cands.append((p, f"额外目录 {d.name}/"))
            continue
        # 文件名不带 id 时：对最近的文件做内容校验（限量）
        files = [p for p in d.glob("*.json")
                 if p.is_file() and SCAN_SIZE_MIN <= p.stat().st_size <= SCAN_SIZE_MAX]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for p in files[:SCAN_MAX_FILES]:
            ok, _ = valid_profile(p, profile_id)
            if ok:
                cands.append((p, f"额外目录 {d.name}/（按内容匹配）"))
                break

    # 去重 + 按进度排序（经验优先，mtime 兜底）
    seen, uniq = set(), []
    for p, src in cands:
        key = str(p.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append((p, src, read_profile_meta(p)))
    uniq.sort(key=lambda x: profile_progress(x[2]) or (0, 0, 0.0), reverse=True)
    return uniq


def prune_backups(backup_dir: Path, profile_id: str):
    """只清理本工具自建的备份（<时间戳>_<id>.json），保留最近 KEEP_BACKUPS 份"""
    if KEEP_BACKUPS <= 0 or not backup_dir.is_dir():
        return
    files = sorted(backup_dir.glob(f"*_{profile_id}.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files[KEEP_BACKUPS:]:
        try:
            p.unlink()
            log(f"  (清理旧备份 {p.name})")
        except Exception as e:
            log(f"  !! 清理旧备份失败 {p.name}: {e}")


def sync_profile(profile_id: str):
    """核心：把「最新的自己的档」同步到单机实际读取路径。

    新旧判据（别再退回纯 mtime）：
      · 归属：来源与本地档必须同一账号（info.aid 一致）→ 防把别人的档同步进来
      · 进度：PMC 经验(主) → Scav 经验(次) → mtime(兜底)
      · 硬闸：来源经验比本地低 → 默认拒绝覆盖（防复制/解压把旧档时间戳刷「新」造成回滚）
    """
    local_path = SPT_ROOT / PROFILES_REL / f"{profile_id}.json"
    backup_dir = SPT_ROOT / expand_dir(BACKUP_DIR_REL)

    if not (SPT_ROOT / PROFILES_REL).is_dir():
        log(f"!! 找不到档案目录: {SPT_ROOT / PROFILES_REL}")
        return None

    local_meta = read_profile_meta(local_path) if local_path.exists() else None
    if not local_path.exists():
        log(f"本地无该档案，将直接写入: {local_path.name}")
    else:
        log(f"本地档: {local_path.name} ({local_path.stat().st_size} B, "
            f"{datetime.fromtimestamp(local_path.stat().st_mtime):%Y-%m-%d %H:%M}, "
            f"{progress_label(local_meta)})")

    cands = find_source_candidates(profile_id)
    if not cands:
        log("没找到任何可用的存档来源（各「下载存档」落点为空，额外目录也没匹配）→ 不覆盖")
        return None

    log("候选来源（按进度 新→旧）:")
    for p, src, m in cands:
        delta = ""
        if local_meta and m:
            d = m["pmc_exp"] - local_meta["pmc_exp"]
            delta = f" | 比本地 {'+' if d >= 0 else ''}{d:,}"
        log(f"  - [{src}] {p.name} ({p.stat().st_size} B, "
            f"{datetime.fromtimestamp(p.stat().st_mtime):%Y-%m-%d %H:%M}, "
            f"{progress_label(m)}{delta})")

    # 挑第一个「合法 + 属于同一账号」的候选（候选已按进度排序，所以就是最优的那个）
    src_path = src_desc = summary = None
    for p, d, m in cands:
        ok, s = valid_profile(p, profile_id)
        if not ok:
            log(f"  (跳过非法候选 {p.name}: {s})")
            continue
        if local_meta and m and local_meta.get("aid") and m.get("aid") \
                and local_meta["aid"] != m["aid"]:
            log(f"  (跳过串档候选 {p.name}: 账号 aid {m.get('aid')} / {m.get('user')} "
                f"≠ 本地 {local_meta['aid']} / {local_meta.get('user')})")
            continue
        src_path, src_desc, summary = p, d, s
        break
    if not src_path:
        log("!! 所有候选均不可用（非法档案或不属于本账号），放弃同步")
        return None

    src_meta = read_profile_meta(src_path)

    if src_path.resolve() == local_path.resolve():
        log(f"最新来源就是本地档，无需同步（{summary}）")
        return local_path

    if local_path.exists() and same_content(src_path, local_path):
        log(f"来源与本地档内容一致，无需同步（{summary}）")
        return local_path

    if local_meta and src_meta:
        d = src_meta["pmc_exp"] - local_meta["pmc_exp"]
        log(f"  进度对比：本地 PMC {local_meta['pmc_exp']:,} → 来源 PMC {src_meta['pmc_exp']:,}"
            f"（{'+' if d >= 0 else ''}{d:,}）")

        # 硬闸：经验倒退 = 旧档 / 时间戳被复制筛新，默认拒绝覆盖
        if src_meta["pmc_exp"] < local_meta["pmc_exp"]:
            if not ALLOW_EXP_ROLLBACK:
                log(f"!! 来源 PMC 经验比本地低 {abs(d):,} → 判定为旧档（或时间戳失真），拒绝覆盖；"
                    f"确要强推请把 config 的 allow_exp_rollback 设为 true")
                return local_path
            log("   （allow_exp_rollback=true → 放行经验倒退覆盖）")

        if ONLY_IF_NEWER and profile_progress(src_meta) <= profile_progress(local_meta):
            log("本地档不落后（经验相同且时间也不更新），保持不动，不覆盖")
            return local_path
    elif local_path.exists() and ONLY_IF_NEWER \
            and src_path.stat().st_mtime <= local_path.stat().st_mtime:
        # 经验读不出时退回 mtime 比较
        log("（经验读不出，退回 mtime 比较）本地档更新，保持不动，不覆盖")
        return local_path

    if DRY_RUN:
        log(f"[DRY-RUN] 应将 [{src_desc}] 覆盖到 {local_path.name}（{summary}）")
        return local_path

    # 备份 → 覆盖
    if local_path.exists():
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.fromtimestamp(local_path.stat().st_mtime).strftime("%Y%m%d_%H%M%S")
        bak = backup_dir / f"{stamp}_{profile_id}.json"
        shutil.copy2(local_path, bak)
        log(f"已备份旧档 -> {bak.relative_to(SPT_ROOT)}")
    shutil.copy2(src_path, local_path)
    log(f"✅ 已同步档案: [{src_desc}] -> profiles\\{local_path.name}（{summary}）")
    prune_backups(backup_dir, profile_id)
    return local_path


# --------------------------- Launcher 自动连本地 ---------------------------

def prune_launcher_backups(launcher_dir: Path):
    """只清理本工具生成的时间戳备份 LauncherSettings.json.<时间戳>.bak（老的 .bak 不动）"""
    if KEEP_LAUNCHER_BACKUPS <= 0 or not launcher_dir.is_dir():
        return
    files = sorted(launcher_dir.glob(f"LauncherSettings.json.*.bak"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files[KEEP_LAUNCHER_BACKUPS:]:
        try:
            p.unlink()
            log(f"  (清理旧 LauncherSettings 备份 {p.name})")
        except Exception as e:
            log(f"  !! 清理旧 LauncherSettings 备份失败 {p.name}: {e}")


def backup_launcher_settings(settings_path: Path):
    """改 LauncherSettings 前留一份带时间戳的备份（不再每次覆盖同一个 .bak）"""
    try:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = settings_path.parent / f"{settings_path.name}.{stamp}.bak"
        bak.write_text(settings_path.read_text(encoding="utf-8"), encoding="utf-8")
        log(f"  已备份 LauncherSettings -> {bak.name}")
        prune_launcher_backups(settings_path.parent)
    except Exception as e:
        log(f"  !! 备份 LauncherSettings 失败（继续修改，但请留意）: {e}")


def set_auto_connect_local(settings_path: Path, profile_id: str):
    """把 PreferredProfile 指向内置本地服务器；顺带确保「自动连接」开着"""
    if not settings_path.exists():
        log(f"!! LauncherSettings.json 不存在: {settings_path}")
        return False
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except Exception as e:
        log(f"!! LauncherSettings.json 解析失败: {e}")
        return False

    pref = data.get("PreferredProfile") or {}
    need_pref = (pref.get("ServerId") != LOCAL_SERVER_ID) or (pref.get("ProfileId") != profile_id)
    need_auto = not data.get("AutoConnectLastProfile", False)

    if not need_pref and not need_auto:
        log(f"Launcher 已指向本地服务器 + 档案 {profile_id}，无需修改")
        return False

    if DRY_RUN:
        log(f"[DRY-RUN] 应把 Launcher 自动连接目标设为 本地服务器({LOCAL_SERVER_ID}) / {profile_id}")
        return True

    backup_launcher_settings(settings_path)

    data["PreferredProfile"] = {"ServerId": LOCAL_SERVER_ID, "ProfileId": profile_id}
    data["AutoConnectLastProfile"] = True
    settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"✅ Launcher 自动连接目标 -> 本地服务器({LOCAL_SERVER_ID}) / 档案 {profile_id}")
    return True


# ------------------------------- 启动流程 -------------------------------

def check_port_owner(server_exe: Path) -> bool:
    """6969 已在监听时，先确认占用者是不是「本目录的」服务端。

    True  = 可以继续（视为本地服务端已在跑，不再另起）
    False = 占用者是别的东西 → 中止整个启动（连上去就是连错服务器）
    """
    owner = port_owner(SPT_PORT)
    desc = describe_owner(owner)

    if owner and _same_exe(owner.get("exe"), server_exe):
        log(f"本地服务端已在运行（127.0.0.1:{SPT_PORT} 监听中）→ 跳过启动服务端")
        log(f"  占用者：{desc}")
        return True

    if owner and not owner.get("exe"):
        if (owner.get("name") or "").lower() == "spt.server.exe":
            log(f"本地服务端已在运行（127.0.0.1:{SPT_PORT} 监听中；路径读不到，靠进程名确认）→ 跳过启动服务端")
            log(f"  占用者：{desc}")
            return True
        log(f"!! {SPT_PORT} 被占用，且进程名不是 SPT.Server.exe：{desc}")
    else:
        log(f"!! {SPT_PORT} 被占用，占用者不是本目录的服务端：{desc}")

    if not PORT_OWNER_GUARD:
        log("   （port_owner_guard=false → 仅警告，继续）")
        return True
    if DRY_RUN:
        log("[DRY-RUN] 应弹窗拦截（6969 被非本目录的服务端占用）")
        return True

    body = (f"{SPT_PORT} 端口已被占用，但占用者不是本目录的服务端：\n\n"
            f"PID：{owner['pid'] if owner else '?'}\n"
            f"进程：{owner['name'] if owner else '?'}\n"
            f"路径：{(owner.get('exe') if owner else None) or '读不到'}\n"
            f"启动于：{(owner.get('start') if owner else None) or '未知'}\n\n"
            f"现在启动 Launcher 会连上这个「不知道是谁」的服务器，\n"
            f"可能跑到别的档、或把档案写到别处。\n\n"
            f"建议先把残留的联机服务端关干净，再重新双击本工具。\n\n"
            f"仍要继续启动吗？")
    if _ask_yes_no(body):
        return True
    log(f"已中止：{SPT_PORT} 被非本目录的服务端占用（未启动任何程序）")
    return False


def start_server_and_launcher():
    server_exe = SPT_ROOT / SERVER_EXE_REL
    launcher_exe = SPT_ROOT / LAUNCHER_EXE_REL

    if port_listening(SPT_PORT):
        if not check_port_owner(server_exe):
            return False
    elif START_SERVER:
        if not server_exe.exists():
            log(f"!! 找不到服务端: {server_exe}")
            _msgbox(f"找不到 SPT 服务端：\n{server_exe}")
            return False
        if DRY_RUN:
            log(f"[DRY-RUN] 应启动服务端: {server_exe}"
                f"{'（新开独立控制台窗口）' if SERVER_NEW_CONSOLE else ''}")
        else:
            log(f"启动服务端: {server_exe}"
                f"{'（新开独立控制台窗口，方便查看报错）' if SERVER_NEW_CONSOLE else ''}")
            log("  （Win11 默认会把新控制台交给 Windows Terminal 托管：窗口/标签页标题是服务端自己设的"
                "构建名，例如「SPT 4.1.5」；找不到窗口就 Alt+Tab 或看任务栏）")
            # CREATE_NEW_CONSOLE：无论脚本是从快捷方式(pythonw)还是终端启动，
            # 都保证服务端拥有自己的窗口 —— 主人能实时看到启动日志与报错。
            flags = 0
            if os.name == "nt" and SERVER_NEW_CONSOLE:
                flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
            subprocess.Popen([str(server_exe)], cwd=str(server_exe.parent), creationflags=flags)
            t0 = time.time()
            while time.time() - t0 < SERVER_WAIT_SECS:
                if port_listening(SPT_PORT):
                    log(f"✅ 服务端就绪（127.0.0.1:{SPT_PORT} 监听成功，用时 {time.time() - t0:.1f}s）")
                    break
                time.sleep(1.0)
            else:
                log(f"!! 等待服务端监听超时（{SERVER_WAIT_SECS}s）")
                _msgbox(f"服务端 {SERVER_WAIT_SECS} 秒内未就绪。\n\n"
                        f"① 先看服务端控制台窗口（黑色窗口）里的报错\n"
                        f"② 服务端日志：\n{SPT_ROOT / RUNTIME_REL / 'user' / 'logs' / 'spt'}\n"
                        f"③ 本工具日志：\n{SPT_ROOT / LOG_REL / 'spt_solostart.log'}")

    if not START_LAUNCHER:
        log("按配置不启动 Launcher")
        return True
    if not launcher_exe.exists():
        log(f"!! 找不到 Launcher: {launcher_exe}")
        _msgbox(f"找不到 SPT Launcher：\n{launcher_exe}")
        return False
    if DRY_RUN:
        log(f"[DRY-RUN] 应启动 Launcher: {launcher_exe}")
        return True
    log(f"启动 Launcher: {launcher_exe}")
    # Launcher 是 WebView2 GUI（自己的日志在 user\logs\Launcher.log）；
    # 输出重定向到 NUL，免得它拖住调用终端/父进程的管道。
    subprocess.Popen([str(launcher_exe)], cwd=str(launcher_exe.parent),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log("✅ Launcher 已启动（会自动连本地服务器并登录档案，点「开始游戏」即可）")
    return True


def main():
    load_config()
    log("=" * 20 + " SPT 单机启动 " + ("[DRY-RUN]" if DRY_RUN else "") + "=" * 20)
    log(f"SPT 根目录: {SPT_ROOT}")
    if not (SPT_ROOT / RUNTIME_REL).is_dir():
        log(f"!! 结构不符：{SPT_ROOT} 下没有 SPT_Runtime\\，请把本工具放到 SPT 游戏根目录下")
        _msgbox("未找到 SPT_Runtime 目录。\n\n请把 SPTSoloStart 文件夹放到 SPT 游戏根目录下（与 SPT_Runtime 平级）。")
        return 2

    settings_path = SPT_ROOT / LAUNCHER_SETTINGS_REL
    profile_id = resolve_profile_id(settings_path)
    if not profile_id:
        log("!! 无法确定 profileId（配置为空 / LauncherSettings 无 PreferredProfile / fika 目录为空）")
        _msgbox("无法确定要使用的档案 ID。\n\n请在 config.json 里填 profile_id，或先用 Launcher 登录一次。")
        return 3

    # ---- 存档同步 ----
    if SYNC_PROFILE:
        procs = running_processes()
        if procs:
            names = ", ".join(sorted(procs))
            log(f"!! 检测到进程在运行: {names} → 跳过存档同步（避免内存旧档回写覆盖新档）")
            log("   （想同步新档请先完全退出游戏/服务端/启动器，再双击本工具）")
            if CONFIRM_ON_SKIP_PROCESS:
                if DRY_RUN:
                    log("[DRY-RUN] 应弹窗确认「是否仍要继续启动（用旧档）」")
                elif not _ask_yes_no(
                        f"检测到这些进程还在运行：\n\n{names}\n\n"
                        f"为避免内存里的旧档回写覆盖新档，本次【不会】同步存档；\n"
                        f"现在启动的话，游戏读的是上一次的档案。\n\n"
                        f"建议先完全退出 游戏 / 服务端 / 启动器，再重新双击本工具。\n\n"
                        f"仍要继续启动吗（用旧档）？", default_yes=False):
                    log("已中止：未做任何写入，未启动任何程序")
                    return 4
        else:
            sync_profile(profile_id)
    else:
        log("按配置不同步档案（sync_profile=false）")

    # ---- Launcher 自动连本地 ----
    if AUTO_CONNECT_LOCAL:
        set_auto_connect_local(settings_path, profile_id)

    # ---- 启动 ----
    if not start_server_and_launcher():
        log("=" * 20 + " 已中止 " + "=" * 20)
        return 5
    log("=" * 20 + " 完成 " + "=" * 20)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        import traceback
        log("!! 未捕获异常:\n" + traceback.format_exc())
        _msgbox(f"SPT 单机启动出错：\n{e}\n\n详见日志：\n{SPT_ROOT / LOG_REL / 'spt_solostart.log'}")
        sys.exit(1)
