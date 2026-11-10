# -*- coding: utf-8 -*-
"""离线语音：下载开源模型、本地识别、后台按键听写。

识别引擎为 sherpa-onnx (Apache-2.0)，模型由用户自行下载。本模块不含任何模型文件。
"""
import glob
import os
import sys
import time

DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                        "WeTypeOfflineToolkit")
MODELS_DIR = os.path.join(DATA_DIR, "models")
SAMPLE_RATE = 16000
HOTKEY = "f9"

BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
# (压缩包 MB, 说明, 名称)
MODELS = [
    (20, "Zipformer CTC 流式小模型 · 中文 · 最小", "sherpa-onnx-streaming-zipformer-small-ctc-zh-int8-2025-04-01"),
    (48, "Zipformer CTC 小模型 · 中文", "sherpa-onnx-zipformer-ctc-small-zh-int8-2025-07-16"),
    (74, "Paraformer 小模型 · 中文", "sherpa-onnx-paraformer-zh-small-2024-03-09"),
    (155, "SenseVoice 量化版 · 中英日韩粤 · 带标点", "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"),
    (223, "Paraformer 标准模型 · 中文", "sherpa-onnx-paraformer-zh-2023-09-14"),
]


def installed_models():
    return sorted(os.path.basename(d) for d in glob.glob(os.path.join(MODELS_DIR, "*")) if os.path.isdir(d))


def download_model(index, progress=None):
    import tarfile
    import urllib.request

    size, desc, name = MODELS[index]
    target = os.path.join(MODELS_DIR, name)
    if os.path.isdir(target):
        return target
    os.makedirs(MODELS_DIR, exist_ok=True)
    archive = os.path.join(MODELS_DIR, name + ".tar.bz2")
    url = f"{BASE}{name}.tar.bz2"
    try:
        with urllib.request.urlopen(url, timeout=60) as resp, open(archive + ".part", "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while chunk := resp.read(1 << 20):
                out.write(chunk)
                done += len(chunk)
                if progress and total:
                    progress(done, total)
        os.replace(archive + ".part", archive)
        if progress:
            progress(-1, 0)  # 解压中
        with tarfile.open(archive, "r:bz2") as tar:
            tar.extractall(MODELS_DIR, filter="data")
    finally:
        for p in (archive, archive + ".part"):
            if os.path.exists(p):
                os.remove(p)
    return target


def find_model_dir(name=None):
    dirs = sorted(d for d in glob.glob(os.path.join(MODELS_DIR, "*")) if os.path.isdir(d))
    if name:
        dirs = [d for d in dirs if os.path.basename(d) == name]
    if not dirs:
        raise RuntimeError("还没有语音模型，请先下载一个。")
    return dirs[0]


def build_recognizer(model_dir):
    import sherpa_onnx

    tokens = os.path.join(model_dir, "tokens.txt")
    model = next((p for p in (os.path.join(model_dir, f) for f in ("model.int8.onnx", "model.onnx"))
                  if os.path.exists(p)), None)
    if not model or not os.path.exists(tokens):
        raise RuntimeError(f"模型文件不完整：{model_dir}")
    name = os.path.basename(model_dir).lower()
    off = sherpa_onnx.OfflineRecognizer
    if "sense-voice" in name:
        return off.from_sense_voice(model=model, tokens=tokens, num_threads=2, language="zh", use_itn=True), False
    if "paraformer" in name:
        return off.from_paraformer(paraformer=model, tokens=tokens, num_threads=2), False
    if "streaming-zipformer" in name and "ctc" in name:
        return sherpa_onnx.OnlineRecognizer.from_zipformer2_ctc(tokens=tokens, model=model, num_threads=2), True
    if "zipformer-ctc" in name:
        return off.from_zipformer_ctc(model=model, tokens=tokens, num_threads=2), False
    raise RuntimeError(f"不支持的模型：{os.path.basename(model_dir)}")


def recognize(recognizer, streaming, samples):
    import numpy as np

    stream = recognizer.create_stream()
    stream.accept_waveform(SAMPLE_RATE, samples)
    if not streaming:
        recognizer.decode_stream(stream)
        return stream.result.text
    stream.accept_waveform(SAMPLE_RATE, np.zeros(int(SAMPLE_RATE * 0.4), dtype=np.float32))
    stream.input_finished()
    while recognizer.is_ready(stream):
        recognizer.decode_stream(stream)
    return recognizer.get_result(stream)


def _paste(text):
    import pyperclip
    from pynput.keyboard import Controller, Key

    try:
        old = pyperclip.paste()
    except Exception:
        old = None
    pyperclip.copy(text)
    kb = Controller()
    with kb.pressed(Key.ctrl):
        kb.press("v")
        kb.release("v")
    if old is not None:
        time.sleep(0.3)
        pyperclip.copy(old)


def run_daemon(log=print):
    """后台监听热键：按住说话，松开识别并粘贴到当前窗口。"""
    import numpy as np
    import queue
    import sounddevice as sd
    from pynput import keyboard

    recognizer, streaming = build_recognizer(find_model_dir())
    hotkey = getattr(keyboard.Key, HOTKEY)
    events, held = queue.Queue(), [False]

    def on_press(key):
        if key == hotkey and not held[0]:
            held[0] = True
            events.put("start")

    def on_release(key):
        if key == hotkey and held[0]:
            held[0] = False
            events.put("stop")

    log(f"离线语音已就绪：按住 {HOTKEY.upper()} 说话")
    mic, chunks = None, []
    with keyboard.Listener(on_press=on_press, on_release=on_release):
        while True:
            try:
                ev = events.get(timeout=0.5)
            except queue.Empty:
                continue
            if ev == "start":
                chunks = []
                mic = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                     callback=lambda d, *_: chunks.append(d.copy()))
                mic.start()
            elif mic is not None:
                mic.stop()
                mic.close()
                mic = None
                samples = np.concatenate(chunks).flatten() if chunks else np.zeros(0, dtype=np.float32)
                if len(samples) / SAMPLE_RATE < 0.3:
                    continue
                try:
                    text = (recognize(recognizer, streaming, samples) or "").strip()
                except Exception as e:
                    log(f"识别失败：{e}")
                    continue
                if text:
                    log(f"识别：{text}")
                    try:
                        _paste(text)
                    except Exception as e:
                        log(f"粘贴失败：{e}")


if __name__ == "__main__":
    try:
        run_daemon()
    except KeyboardInterrupt:
        pass
