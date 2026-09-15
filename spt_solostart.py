#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
spt_solostart.py — SPT 单机启动 + 联机存档自动同步

【为什么需要它】
Fika 联机结束后在游戏里点「下载存档」，档案落在：
    <SPT根>\\SPT\\user\\fika\\<profileId>\\<profileId>.json
而单机（自建服）实际读/写的是：
    <SPT根>\\SPT_Runtime\\user\\profiles\\<profileId>.json
两条路径不自动对接 → 单机启动总是读到联机之前的旧档，还被旧档回写钉死。

【本脚本做什么】双击桌面「SPT单机启动」：
  1) 安全检查：SPT/游戏进程在跑 → 跳过同步（防内存旧档回写覆盖新档）
  2) 选最新的「自己的档」：fika 下载档 / 本地档 / 可选额外目录（比 mtime）
  3) 比本地新 → 先备份本地档 → 覆盖到 profiles\\<id>.json
  4) 把 Launcher 自动连接目标指向「本地服务器」（ServerId=1721162719）
  5) 启 SPT.Server.exe → 等 6969 监听 → 启 SPT.Launcher.exe

【写入边界（硬规矩）】
  只写这四处：
    - SPT_Runtime\\user\\profiles\\<自己的 profileId>.json
    - SPT_Runtime\\user\\profiles\\backups_manual\\（自建备份目录）
    - SPT_Runtime\\user\\sptappdata\\spt_solostart_logs\\（自建日志）
    - SPT_Runtime\\user\\Launcher\\LauncherSettings.json（仅改 PreferredProfile / AutoConnectLastProfile）
  绝不删除任何既有文件；只处理自己的 profileId，别人的档案一概不碰。

用法：
    python spt_solostart.py              # 正常跑
    python spt_solostart.py --dry-run    # 只判定不写入、不启动（验证用）
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
PROFILE_ID        = ""                        # 留空=自动推导（LauncherSettings.PreferredProfile -> SPT/user/fika 最新目录）
SYNC_PROFILE      = True                      # 是否启用存档同步
EXTRA_SOURCE_DIRS = []                        # 额外存档来源目录（如 QQ 接收文件夹/下载夹），留空=只用游戏内「下载存档」落点
ONLY_IF_NEWER     = True                      # 仅当来源比本地档新才覆盖（强烈建议保持 true）
BACKUP_DIR_REL    = "SPT_Runtime/user/profiles/backups_manual"
KEEP_BACKUPS      = 20                        # 只保留最近 N 份本工具自建备份
START_SERVER      = True
START_LAUNCHER    = True
SERVER_WAIT_SECS  = 150                       # 等 6969 监听的上限（秒）
AUTO_CONNECT_LOCAL = True                     # 把 Launcher 自动连接目标切到本地服务器

RUNTIME_REL           = Path("SPT_Runtime")
LAUNCHER_SETTINGS_REL = RUNTIME_REL / "user" / "Launcher" / "LauncherSettings.json"
PROFILES_REL          = RUNTIME_REL / "user" / "profiles"
FIKA_REL              = Path("SPT") / "user" / "fika"
LOG_REL               = RUNTIME_REL / "user" / "sptappdata" / "spt_solostart_logs"
SERVER_EXE_REL        = RUNTIME_REL / "SPT.Server.exe"
LAUNCHER_EXE_REL      = RUNTIME_REL / "SPT.Launcher.exe"

GAME_PROCESSES   = ("SPT.Server.exe", "SPT.Launcher.exe", "EscapeFromTarkov.exe")
SCAN_SIZE_MIN    = 100 * 1024          # 内容扫描时忽略过小的 json
SCAN_SIZE_MAX    = 60 * 1024 * 1024    # 以及过大的
SCAN_MAX_FILES   = 25                  # 每个额外目录最多内容校验多少个候选
# ==============================================================================

DRY_RUN = "--dry-run" in sys.argv
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


def load_config():
    global SPT_PORT, LOCAL_SERVER_ID, PROFILE_ID, SYNC_PROFILE, EXTRA_SOURCE_DIRS, \
        ONLY_IF_NEWER, BACKUP_DIR_REL, KEEP_BACKUPS, START_SERVER, START_LAUNCHER, \
        SERVER_WAIT_SECS, AUTO_CONNECT_LOCAL, CFG
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
    ONLY_IF_NEWER      = bool(CFG.get("only_if_newer", ONLY_IF_NEWER))
    BACKUP_DIR_REL     = str(CFG.get("backup_dir", BACKUP_DIR_REL))
    KEEP_BACKUPS       = int(CFG.get("keep_backups", KEEP_BACKUPS))
    START_SERVER       = bool(CFG.get("start_server", START_SERVER))
    START_LAUNCHER     = bool(CFG.get("start_launcher", START_LAUNCHER))
    SERVER_WAIT_SECS   = int(CFG.get("server_wait_seconds", SERVER_WAIT_SECS))
    AUTO_CONNECT_LOCAL = bool(CFG.get("auto_connect_local", AUTO_CONNECT_LOCAL))


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
        level = (pmc.get("Info") or {}).get("Level", "?")
        items = len((pmc.get("Inventory") or {}).get("items", []))
        quests = sum(1 for q in pmc.get("Quests", []) if q.get("status") == 4)
        inraid = data.get("inraid") or data.get("characters", {}).get("pmc", {}).get("InRaid")
        raid_note = "（战局中保存的档）" if inraid else ""
        return True, f"Lv.{level} / 物品 {items} / 完成任务 {quests}{raid_note}"
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
    fika_root = SPT_ROOT / FIKA_REL
    if fika_root.exists():
        cands = []
        for d in fika_root.iterdir():
            if d.is_dir() and (d / f"{d.name}.json").exists():
                cands.append((d / f"{d.name}.json", (d / f"{d.name}.json").stat().st_mtime))
        if cands:
            newest = max(cands, key=lambda x: x[1])[0]
            log(f"profileId 取 fika 目录里最新目录: {newest.stem}")
            return newest.stem
    return None


def find_source_candidates(profile_id: str):
    """返回 [(path, 来源说明)]，按 mtime 从新到旧"""
    cands = []

    # ① 游戏内「下载存档」落点
    fika_file = SPT_ROOT / FIKA_REL / profile_id / f"{profile_id}.json"
    if fika_file.exists():
        cands.append((fika_file, "Fika 游戏内「下载存档」"))

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

    seen, uniq = set(), []
    for p, src in sorted(cands, key=lambda x: x[0].stat().st_mtime, reverse=True):
        key = str(p.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append((p, src))
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
    """核心：把最新的自己的档同步到单机实际读取路径"""
    local_path = SPT_ROOT / PROFILES_REL / f"{profile_id}.json"
    backup_dir = SPT_ROOT / expand_dir(BACKUP_DIR_REL)

    if not (SPT_ROOT / PROFILES_REL).is_dir():
        log(f"!! 找不到档案目录: {SPT_ROOT / PROFILES_REL}")
        return None
    if not local_path.exists():
        log(f"本地无该档案，将直接写入: {local_path.name}")
    else:
        log(f"本地档: {local_path.name} "
            f"({local_path.stat().st_size} B, {datetime.fromtimestamp(local_path.stat().st_mtime):%Y-%m-%d %H:%M})")

    cands = find_source_candidates(profile_id)
    if not cands:
        log("没找到任何可用的存档来源（游戏内「下载存档」落点为空，额外目录也没匹配）→ 不覆盖")
        return None

    log("候选来源（新→旧）:")
    for p, src in cands:
        log(f"  - [{src}] {p} ({p.stat().st_size} B, "
            f"{datetime.fromtimestamp(p.stat().st_mtime):%Y-%m-%d %H:%M})")

    src_path, src_desc = cands[0]
    ok, summary = valid_profile(src_path, profile_id)
    if not ok:
        log(f"!! 最新候选不是合法档案: {summary} → 拒绝覆盖")
        for p, d in cands[1:]:
            ok2, s2 = valid_profile(p, profile_id)
            if ok2:
                src_path, src_desc, summary = p, d, s2
                log(f"回退使用候选: {p} ({s2})")
                break
        else:
            log("!! 所有候选均不可用，放弃同步")
            return None

    if src_path.resolve() == local_path.resolve():
        log(f"最新来源就是本地档，无需同步（{summary}）")
        return local_path

    if local_path.exists() and same_content(src_path, local_path):
        log(f"来源与本地档内容一致，无需同步（{summary}）")
        return local_path

    if local_path.exists() and ONLY_IF_NEWER and src_path.stat().st_mtime <= local_path.stat().st_mtime:
        log("本地档更新（可能刚单机玩过），保持不动，不覆盖")
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

    try:
        (settings_path.parent / f"{settings_path.name}.bak").write_text(
            settings_path.read_text(encoding="utf-8"), encoding="utf-8")
    except Exception:
        pass

    data["PreferredProfile"] = {"ServerId": LOCAL_SERVER_ID, "ProfileId": profile_id}
    data["AutoConnectLastProfile"] = True
    settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"✅ Launcher 自动连接目标 -> 本地服务器({LOCAL_SERVER_ID}) / 档案 {profile_id}")
    return True


# ------------------------------- 启动流程 -------------------------------

def start_server_and_launcher():
    server_exe = SPT_ROOT / SERVER_EXE_REL
    launcher_exe = SPT_ROOT / LAUNCHER_EXE_REL

    if port_listening(SPT_PORT):
        log(f"本地服务端已在运行（127.0.0.1:{SPT_PORT} 已在监听），跳过启动服务端")
    elif START_SERVER:
        if not server_exe.exists():
            log(f"!! 找不到服务端: {server_exe}")
            _msgbox(f"找不到 SPT 服务端：\n{server_exe}")
            return
        if DRY_RUN:
            log(f"[DRY-RUN] 应启动服务端: {server_exe}")
        else:
            log(f"启动服务端: {server_exe}")
            subprocess.Popen([str(server_exe)], cwd=str(server_exe.parent))
            t0 = time.time()
            while time.time() - t0 < SERVER_WAIT_SECS:
                if port_listening(SPT_PORT):
                    log(f"✅ 服务端就绪（127.0.0.1:{SPT_PORT} 监听成功，用时 {time.time() - t0:.1f}s）")
                    break
                time.sleep(1.0)
            else:
                log(f"!! 等待服务端监听超时（{SERVER_WAIT_SECS}s）")
                _msgbox(f"服务端 {SERVER_WAIT_SECS} 秒内未就绪。\n\n请查看服务端窗口的报错信息。")

    if not START_LAUNCHER:
        log("按配置不启动 Launcher")
        return
    if not launcher_exe.exists():
        log(f"!! 找不到 Launcher: {launcher_exe}")
        _msgbox(f"找不到 SPT Launcher：\n{launcher_exe}")
        return
    if DRY_RUN:
        log(f"[DRY-RUN] 应启动 Launcher: {launcher_exe}")
        return
    log(f"启动 Launcher: {launcher_exe}")
    subprocess.Popen([str(launcher_exe)], cwd=str(launcher_exe.parent))
    log("✅ Launcher 已启动（会自动连本地服务器并登录档案，点「开始游戏」即可）")


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
            log(f"!! 检测到进程在运行: {', '.join(sorted(procs))} → 跳过存档同步（避免内存旧档回写覆盖新档）")
            log("   （想同步新档请先完全退出游戏/服务端/启动器，再双击本工具）")
        else:
            sync_profile(profile_id)
    else:
        log("按配置不同步档案（sync_profile=false）")

    # ---- Launcher 自动连本地 ----
    if AUTO_CONNECT_LOCAL:
        set_auto_connect_local(settings_path, profile_id)

    # ---- 启动 ----
    start_server_and_launcher()
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
