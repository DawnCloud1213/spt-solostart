# -*- coding: utf-8 -*-
"""spt_solostart 自检：端口占用者识别 / 兜底判定 / 备份滚动。

用法（必须在「已部署」的 SPTSoloStart 目录里跑，因为脚本要靠自身位置推导 SPT 根）：
    python test_solo_guard.py                 # 默认测同目录的 spt_solostart.py
    python test_solo_guard.py <另一个路径/spt_solostart.py>

只读 + 只用临时目录：不写游戏档案、不启服务端/Launcher、不碰真实 LauncherSettings。
"""
import importlib.util, subprocess, sys, time, shutil, os
from pathlib import Path

_here = Path(__file__).resolve().parent
TOOL = Path(sys.argv[1]) if len(sys.argv) > 1 else (_here / "spt_solostart.py")
spec = importlib.util.spec_from_file_location("solo", TOOL)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m.load_config()
print(f"[env] SPT_ROOT = {m.SPT_ROOT}")
print(f"[env] SPT_PORT = {m.SPT_PORT}  PORT_OWNER_GUARD = {m.PORT_OWNER_GUARD}  "
      f"CONFIRM_ON_SKIP_PROCESS = {m.CONFIRM_ON_SKIP_PROCESS}  KEEP_LAUNCHER_BACKUPS = {m.KEEP_LAUNCHER_BACKUPS}")

SERVER = m.SPT_ROOT / m.SERVER_EXE_REL
print(f"[env] 被测脚本 = {TOOL}")
fail = []

def check(label, got, want):
    ok = got == want
    if not ok:
        fail.append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got={got!r} want={want!r}")

print("\n=== 1) 无人占用 6969 ===")
check("port_listening", m.port_listening(6969), False)
check("port_owner -> None", m.port_owner(6969), None)
print(f"  describe_owner(None) = {m.describe_owner(None)}")

print("\n=== 2) 起一个假的占用者（python 监听 6969）===")
pidfile = Path(os.environ["LOCALAPPDATA"]) / "Temp" / "solo_test_child.pid"
if pidfile.exists():
    pidfile.unlink()
code = ("import socket,time,os\n"
        "s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)\n"
        "s.bind(('127.0.0.1',6969));s.listen(5)\n"
        f"open(r'{pidfile}','w').write(str(os.getpid()))\n"
        "time.sleep(90)\n")
srv = subprocess.Popen([sys.executable, "-c", code])
try:
    real_pid = None
    for _ in range(40):
        if pidfile.exists() and pidfile.read_text().strip():
            real_pid = pidfile.read_text().strip()
            break
        time.sleep(0.3)
    for _ in range(30):
        if m.port_listening(6969):
            break
        time.sleep(0.5)
    check("port_listening", m.port_listening(6969), True)
    print(f"  外壳 pid={srv.pid} / 真正监听者 pid={real_pid}")

    owner = m.port_owner(6969)
    print(f"  owner = {owner}")
    print(f"  describe_owner = {m.describe_owner(owner)}")
    check("owner.pid == 真正监听者 pid", owner is not None and owner["pid"] == real_pid, True)
    check("owner.exe 可读（PowerShell/wmic 链路通）", bool(owner and owner.get("exe")), True)
    check("owner.start 可读", bool(owner and owner.get("start")), True)

    print("\n  -- 分支 A：占用者==本目录服务端（用假占用者的 exe 冒充）--")
    m.ASSUME_YES = True
    check("check_port_owner(ours) -> True", m.check_port_owner(Path(owner["exe"])), True)

    print("\n  -- 分支 B：占用者是外人（目标=真的 SPT.Server.exe）--")
    real_ask = m._ask_yes_no
    m._ask_yes_no = lambda *a, **k: True
    check("确认「继续」-> True", m.check_port_owner(SERVER), True)
    m._ask_yes_no = lambda *a, **k: False
    check("确认「中止」-> False", m.check_port_owner(SERVER), False)
    m._ask_yes_no = real_ask

    print("\n  -- 分支 C：port_owner_guard=false 时仅警告不拦 --")
    m.PORT_OWNER_GUARD = False
    check("guard 关闭 -> True", m.check_port_owner(SERVER), True)
    m.PORT_OWNER_GUARD = True

    print("\n  -- 分支 D：DRY-RUN 下不弹窗、返回 True --")
    m.DRY_RUN = True
    m._ask_yes_no = lambda *a, **k: (_ for _ in ()).throw(AssertionError("dry-run 不该弹窗！"))
    check("dry-run 不弹窗 -> True", m.check_port_owner(SERVER), True)
    m.DRY_RUN = False
    m._ask_yes_no = real_ask
finally:
    srv.kill()
    srv.wait(timeout=10)
    time.sleep(1)
print(f"  假占用者已收工，port_listening = {m.port_listening(6969)}")

print("\n=== 3) _same_exe 中文路径乱码退化比对 ===")
check("完全一致", m._same_exe(str(SERVER), SERVER), True)
check("路径前段乱码但末两级一致", m._same_exe(r"XXXX\SPT_Runtime\SPT.Server.exe", SERVER), True)
check("末两级不同", m._same_exe(r"D:\other\SPT_Runtime\SPT.Launcher.exe", SERVER), False)
check("None", m._same_exe(None, SERVER), False)

print("\n=== 4) LauncherSettings 时间戳备份 + 滚动清理 ===")
tmp = Path(os.environ["LOCALAPPDATA"]) / "Temp" / "solo_bak_test"
if tmp.exists():
    shutil.rmtree(tmp)
tmp.mkdir(parents=True)
legacy = tmp / "LauncherSettings.json.bak"          # 主人原有的老备份（必须永不动）
legacy.write_text('{"legacy": true}', encoding="utf-8")
settings = tmp / "LauncherSettings.json"
settings.write_text('{"PreferredProfile":{}}', encoding="utf-8")

m.KEEP_LAUNCHER_BACKUPS = 3
for i in range(5):
    m.backup_launcher_settings(settings)
    time.sleep(1.05)          # 时间戳到秒，拉开 mtime
olds = sorted(p.name for p in tmp.glob("LauncherSettings.json.*.bak"))
print(f"  生成的备份: {olds}")
check("时间戳备份保留份数 == 3", len(olds), 3)
check("主人原有的 LauncherSettings.json.bak 仍在", legacy.exists(), True)
check("原 LauncherSettings.json 未被备份逻辑破坏", settings.exists(), True)
shutil.rmtree(tmp, ignore_errors=True)

print("\n=== 5) 多落点扫描 + 经验判据（在临时目录造档，绝不碰真实档案）===")
import json as _json

tmp = Path(os.environ["LOCALAPPDATA"]) / "Temp" / "solo_sync_test"
if tmp.exists():
    shutil.rmtree(tmp)


def _mk(path, aid, pmc_exp, scav_exp=0, pid="testpid"):
    """造一份最小可用的假档案"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json.dumps({
        "info": {"aid": aid, "id": pid, "username": "Tester"},
        "characters": {
            "pmc": {"Info": {"Level": 1, "Experience": pmc_exp},
                    "Inventory": {"items": []}, "Quests": []},
            "scav": {"Info": {"Level": 1, "Experience": scav_exp}},
        },
    }, ensure_ascii=False), encoding="utf-8")
    return path


# 把被测脚本隔离到临时 SPT 根，绝不碰真实游戏目录
m.SPT_ROOT = tmp
m.FIKA_SOURCE_DIRS_REL = [Path("SPT_Runtime") / "user" / "fika", Path("SPT") / "user" / "fika"]
m.FIKA_SOURCE_DIRS = []
m.EXTRA_SOURCE_DIRS = []
m.PROFILES_REL = Path("SPT_Runtime") / "user" / "profiles"
m.BACKUP_DIR_REL = "SPT_Runtime/user/profiles/backups_manual"
m.KEEP_BACKUPS = 20
m.ONLY_IF_NEWER = True
m.ALLOW_EXP_ROLLBACK = False
m.DRY_RUN = False

old_fika = _mk(tmp / "SPT" / "user" / "fika" / "testpid" / "testpid.json", 111, 1000)
new_fika = _mk(tmp / "SPT_Runtime" / "user" / "fika" / "testpid" / "testpid.json", 111, 9000)
local = _mk(tmp / "SPT_Runtime" / "user" / "profiles" / "testpid.json", 111, 5000)
# ⭐ 关键反例：把「内容更旧」的档 mtime 刷成未来 → 纯 mtime 逻辑会错选它
_future = time.time() + 600
os.utime(old_fika, (_future, _future))

check("fika_roots 扫到 2 个候选落点", len(m.fika_roots()), 2)
cands = m.find_source_candidates("testpid")
check("候选 2 份", len(cands), 2)
check("经验最高的排第一（哪怕它 mtime 更旧）", cands[0][0].parts[-5], "SPT_Runtime")
check("mtime 被刷成未来的旧档排最后", cands[-1][0].parts[-5], "SPT")
check("进度指纹：经验优先于 mtime",
      m.profile_progress(m.read_profile_meta(old_fika))
      < m.profile_progress(m.read_profile_meta(new_fika)), True)
check("read_profile_meta 读到 aid/pmc 经验",
      (m.read_profile_meta(new_fika)["aid"], m.read_profile_meta(new_fika)["pmc_exp"]), (111, 9000))

print("\n  -- 场景 A：同账号 + 来源经验更高 → 覆盖并备份 --")
m.sync_profile("testpid")
check("覆盖后本地经验 = 9000", m.read_profile_meta(local)["pmc_exp"], 9000)
check("旧档已备份 1 份",
      len(list((tmp / m.BACKUP_DIR_REL).glob("*_testpid.json"))), 1)

print("\n  -- 场景 B：来源经验倒退（更旧内容却「时间更新」）→ 硬闸拒绝 --")
shutil.rmtree(tmp / "SPT_Runtime" / "user" / "fika", ignore_errors=True)
shutil.rmtree(tmp / "SPT" / "user" / "fika", ignore_errors=True)
local2 = _mk(tmp / "SPT_Runtime" / "user" / "profiles" / "testpid.json", 111, 5000)
stale = _mk(tmp / "SPT" / "user" / "fika" / "testpid" / "testpid.json", 111, 1000)
_f2 = time.time() + 1200
os.utime(stale, (_f2, _f2))
m.sync_profile("testpid")
check("经验倒退被拒绝（本地仍是 5000）", m.read_profile_meta(local2)["pmc_exp"], 5000)

print("\n  -- 场景 C：候选属于别的账号 → 串档拦截 --")
shutil.rmtree(tmp / "SPT" / "user" / "fika", ignore_errors=True)
_mk(tmp / "SPT" / "user" / "fika" / "testpid" / "testpid.json", 999, 99999)
check("串档时放弃同步（返回 None）", m.sync_profile("testpid"), None)
check("串档后本地经验仍是 5000", m.read_profile_meta(local2)["pmc_exp"], 5000)

print("\n  -- 场景 D：同账号 + 经验更高 → 正常覆盖 --")
shutil.rmtree(tmp / "SPT" / "user" / "fika", ignore_errors=True)
_mk(tmp / "SPT_Runtime" / "user" / "fika" / "testpid" / "testpid.json", 111, 9000)
m.sync_profile("testpid")
check("覆盖后本地经验 = 9000", m.read_profile_meta(local2)["pmc_exp"], 9000)

shutil.rmtree(tmp, ignore_errors=True)

print("\n" + ("=" * 46))
print("全部通过 ✨" if not fail else f"失败项: {fail}")
sys.exit(1 if fail else 0)
