# -*- coding: utf-8 -*-
"""对本机已安装的微信输入法做可逆的本地修改：断网、去审查。

本模块只操作用户自己机器上已存在的安装，不包含、不分发任何第三方文件。
所有操作都需要管理员权限，且都可撤销。
"""
import glob
import os
import shutil
import subprocess

# ---- 安装定位 ----------------------------------------------------------

def find_install():
    """返回 (版本目录, libwxhld.dll 路径)；找不到返回 (None, None)。"""
    roots = [
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Tencent", "WeType"),
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Tencent", "WeType"),
    ]
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dll in glob.glob(os.path.join(root, "*", "libwxhld.dll")):
            return os.path.dirname(dll), dll
    return None, None


def list_exes(version_dir):
    return sorted(glob.glob(os.path.join(version_dir, "*.exe")))


# ---- 去审查补丁 --------------------------------------------------------
# 只针对 3.0.0.34 的 libwxhld.dll：把创建敏感词拦截对象的条件跳转改成无条件跳过。
# 两段特征码在整个文件中唯一，版本不符会匹配失败，工具会拒绝修改。

SIG_CENSOR_ON = bytes.fromhex("4883bd40010000000f84d5000000")   # 原始：有拦截
SIG_CENSOR_OFF = bytes.fromhex("4883bd4001000000e9d600000090")  # 补丁：跳过拦截


def censor_state(dll_path):
    """'on' 有审查 / 'off' 已去审查 / 'unknown' 不认识(版本不符)。"""
    data = open(dll_path, "rb").read()
    on = data.count(SIG_CENSOR_ON)
    off = data.count(SIG_CENSOR_OFF)
    if on == 1 and off == 0:
        return "on"
    if off == 1 and on == 0:
        return "off"
    return "unknown"


def _write_dll(dll_path, data):
    bak = dll_path + ".offline-toolkit.bak"
    if not os.path.exists(bak):
        shutil.copy2(dll_path, bak)   # 首次修改前完整备份
    tmp = dll_path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, dll_path)


def set_censor(dll_path, enabled):
    """enabled=False 去审查；enabled=True 还原审查。返回变化说明。"""
    state = censor_state(dll_path)
    if state == "unknown":
        raise RuntimeError("这个 libwxhld.dll 版本不在支持范围内，为安全起见不修改。")
    want = "on" if enabled else "off"
    if state == want:
        return f"已处于目标状态（审查{'开' if enabled else '关'}），无需修改。"
    data = open(dll_path, "rb").read()
    if enabled:
        data = data.replace(SIG_CENSOR_OFF, SIG_CENSOR_ON, 1)
    else:
        data = data.replace(SIG_CENSOR_ON, SIG_CENSOR_OFF, 1)
    _write_dll(dll_path, data)
    return f"已{'还原审查' if enabled else '去除审查'}。重启微信输入法后生效。"


def stop_wetype():
    """修改 DLL 前先结束微信输入法进程，否则文件被占用。"""
    killed = []
    for name in ("wetype_renderer.exe", "wetype_update.exe", "wetype_server.exe", "wetype_service.exe"):
        r = subprocess.run(["taskkill", "/f", "/im", name], capture_output=True, text=True)
        if r.returncode == 0:
            killed.append(name)
    return killed


# ---- 断网：防火墙 + hosts ---------------------------------------------

FW_PREFIX = "WeTypeOffline-"
HOSTS_PATH = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32", "drivers", "etc", "hosts")
HOSTS_BEGIN = "# WeTypeOffline BEGIN"
HOSTS_END = "# WeTypeOffline END"
BLOCK_DOMAINS = [
    "wetype.weixin.qq.com",
    "wetype995.weixin.qq.com",
    "wetype.wxqcloud.qq.com",
    "download.z.weixin.qq.com",
    "z.weixin.qq.com",
    "wxkeyboardcms-1258476243.file.myqcloud.com",
    "oss.weread.qq.com",
    "r.qq.com",
]


def _netsh(*args):
    return subprocess.run(["netsh", "advfirewall", "firewall", *args],
                          capture_output=True, text=True, encoding="gbk", errors="replace")


def firewall_apply(version_dir):
    done = 0
    for exe in list_exes(version_dir):
        name = FW_PREFIX + os.path.basename(exe)
        for direction in ("out", "in"):
            _netsh("delete", "rule", f"name={name}-{direction}")
            r = _netsh("add", "rule", f"name={name}-{direction}", f"dir={direction}",
                       "action=block", f"program={exe}", "enable=yes", "profile=any")
            done += r.returncode == 0
    return done


def firewall_remove():
    # 按前缀逐条删除
    listing = _netsh("show", "rule", "name=all").stdout or ""
    names = set()
    for line in listing.splitlines():
        line = line.strip()
        # 规则名一行，含前缀即收集
        if FW_PREFIX in line:
            idx = line.find(FW_PREFIX)
            names.add(line[idx:].strip())
    for name in names:
        _netsh("delete", "rule", f"name={name}")
    return len(names)


def hosts_state():
    try:
        txt = open(HOSTS_PATH, encoding="utf-8", errors="replace").read()
    except OSError:
        return False
    return HOSTS_BEGIN in txt


def hosts_apply():
    txt = open(HOSTS_PATH, encoding="utf-8", errors="replace").read() if os.path.exists(HOSTS_PATH) else ""
    txt = _hosts_strip(txt)
    block = [HOSTS_BEGIN] + [f"0.0.0.0 {d}" for d in BLOCK_DOMAINS] + [HOSTS_END]
    if txt and not txt.endswith("\n"):
        txt += "\n"
    txt += "\n".join(block) + "\n"
    _write_hosts(txt)


def hosts_remove():
    if not os.path.exists(HOSTS_PATH):
        return
    _write_hosts(_hosts_strip(open(HOSTS_PATH, encoding="utf-8", errors="replace").read()))


def _hosts_strip(txt):
    lines = txt.splitlines()
    out, skip = [], False
    for ln in lines:
        if ln.strip() == HOSTS_BEGIN:
            skip = True
            continue
        if ln.strip() == HOSTS_END:
            skip = False
            continue
        if not skip:
            out.append(ln)
    return "\n".join(out).rstrip("\n") + "\n"


def _write_hosts(txt):
    tmp = HOSTS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(txt)
    os.replace(tmp, HOSTS_PATH)


def network_state(version_dir):
    listing = _netsh("show", "rule", "name=all").stdout or ""
    fw = FW_PREFIX in listing
    return fw, hosts_state()
