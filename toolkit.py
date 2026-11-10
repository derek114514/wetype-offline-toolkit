# -*- coding: utf-8 -*-
"""微信输入法离线工具箱（第三方工具）

对本机已安装的微信输入法做三件可逆的本地操作：
  1. 彻底断网（防火墙 + hosts）
  2. 去除输入审查（可逆的本地 DLL 补丁，带备份）
  3. 下载并启用离线语音（开源 sherpa-onnx，模型自选下载）

本工具为原创程序，不含、也不分发腾讯的任何文件；它只修改你机器上已存在的安装。
需要管理员权限。所有操作均可撤销。
"""
import ctypes
import os
import sys
import threading
import winreg

import core

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "WeTypeOfflineVoice"


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def relaunch_as_admin():
    params = " ".join(f'"{a}"' for a in sys.argv)
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)


def self_argv(arg):
    """返回以当前程序再次启动的参数列表（exe 模式用 exe 自身，开发期用 python 脚本）。"""
    if getattr(sys, "frozen", False):
        return [sys.executable, arg]
    return [sys.executable, os.path.abspath(__file__), arg]


def self_command(arg):
    return " ".join(f'"{a}"' if " " in a else a for a in self_argv(arg))


def voice_autostart(enable):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_ALL_ACCESS) as k:
        if enable:
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, self_command("--voice-daemon"))
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except FileNotFoundError:
                pass


def voice_autostart_on():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
            return True
    except OSError:
        return False


# ---- 后台语音模式（无界面）-------------------------------------------

def run_voice_daemon():
    import voice
    try:
        voice.run_daemon(log=lambda m: None)
    except Exception:
        sys.exit(1)


# ---- 图形界面 --------------------------------------------------------

def run_gui():
    import tkinter as tk
    from tkinter import messagebox, ttk

    version_dir, dll = core.find_install()

    root = tk.Tk()
    root.title("微信输入法离线工具箱")
    root.geometry("560x560")
    root.resizable(False, False)

    log_var = tk.StringVar(value="就绪。")

    def log(msg):
        log_var.set(msg)
        root.update_idletasks()

    def guard(fn):
        def wrapped():
            try:
                fn()
            except Exception as e:
                messagebox.showerror("出错", str(e))
                log(f"出错：{e}")
            refresh()
        return wrapped

    pad = dict(padx=12, pady=6)

    # 顶部：安装状态
    head = ttk.Label(root, text="", font=("Microsoft YaHei", 10))
    head.pack(anchor="w", **pad)

    # 1. 断网
    f1 = ttk.LabelFrame(root, text="1. 彻底断网")
    f1.pack(fill="x", **pad)
    net_status = ttk.Label(f1, text="")
    net_status.pack(anchor="w", padx=10, pady=4)

    def net_on():
        if not version_dir:
            raise RuntimeError("未找到微信输入法安装。")
        n = core.firewall_apply(version_dir)
        core.hosts_apply()
        log(f"已添加 {n} 条防火墙规则并写入 hosts。")

    def net_off():
        core.firewall_remove()
        core.hosts_remove()
        log("已撤销断网设置。")

    b1 = ttk.Frame(f1)
    b1.pack(anchor="w", padx=10, pady=4)
    ttk.Button(b1, text="开启断网", command=guard(net_on)).pack(side="left", padx=4)
    ttk.Button(b1, text="撤销", command=guard(net_off)).pack(side="left", padx=4)

    # 2. 去审查
    f2 = ttk.LabelFrame(root, text="2. 去除输入审查")
    f2.pack(fill="x", **pad)
    cen_status = ttk.Label(f2, text="")
    cen_status.pack(anchor="w", padx=10, pady=4)

    def cen_off():
        if not dll:
            raise RuntimeError("未找到微信输入法安装。")
        if core.censor_state(dll) == "unknown":
            raise RuntimeError("当前 DLL 版本不受支持，为安全起见不修改。")
        killed = core.stop_wetype()
        msg = core.set_censor(dll, enabled=False)
        log(("已结束进程 " + "、".join(killed) + "；" if killed else "") + msg)

    def cen_on():
        if not dll:
            raise RuntimeError("未找到微信输入法安装。")
        core.stop_wetype()
        log(core.set_censor(dll, enabled=True))

    b2 = ttk.Frame(f2)
    b2.pack(anchor="w", padx=10, pady=4)
    ttk.Button(b2, text="去除审查", command=guard(cen_off)).pack(side="left", padx=4)
    ttk.Button(b2, text="还原", command=guard(cen_on)).pack(side="left", padx=4)

    # 3. 离线语音
    f3 = ttk.LabelFrame(root, text="3. 离线语音输入（独立运行，按住 F9 说话）")
    f3.pack(fill="x", **pad)
    import voice
    voice_status = ttk.Label(f3, text="")
    voice_status.pack(anchor="w", padx=10, pady=4)
    model_pick = ttk.Combobox(f3, state="readonly", width=48,
                              values=[f"{s} MB  {d}" for s, d, _ in voice.MODELS])
    model_pick.current(0)
    model_pick.pack(anchor="w", padx=10, pady=4)
    prog = ttk.Progressbar(f3, length=460, mode="determinate")
    prog.pack(anchor="w", padx=10, pady=4)

    def do_download():
        idx = model_pick.current()

        def worker():
            def on_prog(done, total):
                if done < 0:
                    prog.configure(mode="indeterminate"); prog.start()
                    log("下载完成，解压中 …")
                else:
                    prog.configure(mode="determinate")
                    prog["value"] = done * 100 / total
                    log(f"下载中 {done >> 20}/{total >> 20} MB")
                root.update_idletasks()
            try:
                voice.download_model(idx, progress=on_prog)
                prog.stop(); prog.configure(mode="determinate"); prog["value"] = 100
                log("语音模型已就绪。可点「开机自动启用」。")
            except Exception as e:
                log(f"下载失败：{e}")
            root.after(0, refresh)
        threading.Thread(target=worker, daemon=True).start()

    def voice_enable():
        import subprocess
        if not voice.installed_models():
            raise RuntimeError("请先下载一个语音模型。")
        voice_autostart(True)
        DETACHED = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(self_argv("--voice-daemon"), close_fds=True, creationflags=DETACHED)
        log("已启用后台语音并设为开机自启。按住 F9 说话。")

    def voice_disable():
        voice_autostart(False)
        log("已取消开机自启。当前正在后台运行的语音进程会在你注销或重启后停止。")

    b3 = ttk.Frame(f3)
    b3.pack(anchor="w", padx=10, pady=4)
    ttk.Button(b3, text="下载所选模型", command=do_download).pack(side="left", padx=4)
    ttk.Button(b3, text="开机自动启用", command=guard(voice_enable)).pack(side="left", padx=4)
    ttk.Button(b3, text="停用", command=guard(voice_disable)).pack(side="left", padx=4)

    ttk.Label(root, textvariable=log_var, foreground="#2a7").pack(anchor="w", **pad)

    def refresh():
        if version_dir:
            head.config(text=f"已安装：{version_dir}")
        else:
            head.config(text="未找到微信输入法安装（请先安装官方微信输入法）。")
        if version_dir:
            fw, hs = core.network_state(version_dir)
            net_status.config(text=f"防火墙规则：{'已启用' if fw else '未启用'}    hosts 屏蔽：{'已启用' if hs else '未启用'}")
        if dll:
            st = core.censor_state(dll)
            cen_status.config(text={"on": "当前：有审查（拦截敏感词）",
                                    "off": "当前：已去审查",
                                    "unknown": "当前：DLL 版本不受支持，无法修改"}[st])
        ms = voice.installed_models()
        voice_status.config(text=(f"已下载模型：{len(ms)} 个" if ms else "尚未下载模型") +
                                 f"    开机自启：{'开' if voice_autostart_on() else '关'}")

    refresh()
    root.mainloop()


def main():
    if "--voice-daemon" in sys.argv:
        run_voice_daemon()
        return
    if not is_admin():
        relaunch_as_admin()
        return
    run_gui()


if __name__ == "__main__":
    main()
