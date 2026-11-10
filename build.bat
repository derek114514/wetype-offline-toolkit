@echo off
rem 把工具箱打包成单个 exe。需要先安装依赖：
rem   pip install pyinstaller sherpa-onnx numpy sounddevice pynput pyperclip
rem 生成的文件在 dist\微信输入法离线工具箱.exe

pyinstaller --onefile --noconsole --clean ^
  --name "微信输入法离线工具箱" ^
  --collect-all sherpa_onnx ^
  --hidden-import numpy ^
  --hidden-import sounddevice ^
  --hidden-import pynput ^
  --hidden-import pyperclip ^
  toolkit.py

echo.
echo 完成后 exe 在 dist\ 目录。双击运行会请求管理员权限。
pause
