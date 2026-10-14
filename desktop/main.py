"""Float Reader 桌面壳:pywebview 无边框窗口 + 自绘标题栏。

- 标题栏(HTML 内 #titlebar):拖拽区 + 📌 置顶 + ─ □ ×,由 JS 调用本文件 Api 实现
- 无边框窗口的边缘缩放:HTML 边缘热区(.rz)mousedown → JS 调 Api.resize_begin/track/end
  → SetWindowPos 完成
- 所有触碰 UI 的操作都经 BeginInvoke 派发到 UI 线程,避免跨线程死锁
- 独立的 WinDLL 实例设置 argtypes,不污染 pywebview 使用的 windll.user32
- exe 需与 index.html 同目录(file:// 源一致才能让 IndexedDB 数据通用)
"""
import ctypes
import os
import sys
import time

import webview

DATA_DIR = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'FloatReader')

dwmapi = ctypes.windll.dwmapi
# pywebview 内部使用 windll.user32 且不带 argtypes;这里用独立实例,避免污染其函数签名
_u32 = ctypes.WinDLL('user32', use_last_error=True)
_u32.GetWindowRect.restype = ctypes.c_int
_u32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_long * 4)]
_u32.SetWindowPos.restype = ctypes.c_int
_u32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]

SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
MIN_W, MIN_H = 280, 360
APP = {'maximized': False, 'rs': None}


def index_path():
    if getattr(sys, 'frozen', False):
        # 优先用 exe 旁边的 index.html(开发者模式,书库 origin 稳定);
        # 没有则把打包进 exe 的 index.html 释放到数据目录(单文件免安装模式)
        beside = os.path.join(os.path.dirname(sys.executable), 'index.html')
        if os.path.exists(beside):
            return beside
        bundled = os.path.join(sys._MEIPASS, 'index.html')
        target = os.path.join(DATA_DIR, 'index.html')
        try:
            import hashlib
            def digest(p):
                with open(p, 'rb') as f:
                    return hashlib.sha256(f.read()).hexdigest()
            if not os.path.exists(target) or digest(target) != digest(bundled):
                os.makedirs(DATA_DIR, exist_ok=True)
                with open(bundled, 'rb') as s, open(target, 'wb') as d:
                    d.write(s.read())
        except Exception as e:
            print('extract index.html failed: %s' % e, file=sys.stderr, flush=True)
            return bundled
        return target
    return os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'index.html'))


class Api:
    def __init__(self):
        self.topmost = False

    def _win(self):
        return webview.windows[0]

    def _ui(self, fn):
        """把 UI 操作派发到 UI 线程(异步),规避跨线程死锁。"""
        try:
            from System.Windows.Forms import MethodInvoker
            self._win().native.BeginInvoke(MethodInvoker(fn))
        except Exception as e:
            print('ui dispatch failed: %s' % e, file=sys.stderr, flush=True)

    def get_state(self):
        return {'topmost': self.topmost, 'maximized': APP['maximized']}

    def toggle_topmost(self):
        self.topmost = not self.topmost
        val = self.topmost
        self._ui(lambda: setattr(self._win().native, 'TopMost', val))
        return {'topmost': self.topmost}

    def min_win(self):
        def _do():
            from System.Windows.Forms import FormWindowState
            self._win().native.WindowState = FormWindowState.Minimized
        self._ui(_do)

    def toggle_max(self):
        if APP['maximized']:
            APP['maximized'] = False
        else:
            APP['maximized'] = True

        def _do():
            from System.Windows.Forms import FormWindowState
            self._win().native.WindowState = (FormWindowState.Maximized if APP['maximized']
                                              else FormWindowState.Normal)
        self._ui(_do)
        return {'maximized': APP['maximized']}

    def resize_begin(self, sx, sy):
        # 记录缩放会话起点(屏幕坐标 + 当前窗口矩形)
        if APP['maximized']:
            return {'ok': False}
        try:
            hwnd = int(self._win().native.Handle.ToInt64())
            r = (ctypes.c_long * 4)()
            _u32.GetWindowRect(hwnd, r)
            APP['rs'] = {'hwnd': hwnd, 'l': r[0], 't': r[1], 'r': r[2], 'b': r[3],
                         'sx': int(sx), 'sy': int(sy)}
            return {'ok': True}
        except Exception as e:
            print('resize_begin failed: %s' % e, file=sys.stderr, flush=True)
            return {'ok': False}

    def resize_track(self, edge, sx, sy):
        s = APP.get('rs')
        if not s:
            return
        edge = str(edge)
        dx = int(sx) - s['sx']
        dy = int(sy) - s['sy']
        l, t, r, b = s['l'], s['t'], s['r'], s['b']
        if 'e' in edge: r = max(s['l'] + MIN_W, r + dx)
        if 's' in edge: b = max(s['t'] + MIN_H, b + dy)
        if 'w' in edge: l = min(s['r'] - MIN_W, l + dx)
        if 'n' in edge: t = min(s['b'] - MIN_H, t + dy)
        _u32.SetWindowPos(ctypes.c_void_p(s['hwnd']), None, l, t, r - l, b - t,
                          SWP_NOZORDER | SWP_NOACTIVATE)

    def resize_end(self):
        APP['rs'] = None

    def close_win(self):
        try:   # 关闭前把阅读进度落盘
            self._win().evaluate_js("typeof Reader !== 'undefined' && Reader._saveNow && Reader._saveNow()")
        except Exception:
            pass
        time.sleep(0.2)
        self._win().destroy()

    def ready(self):
        # 页面加载完成、桥可用的信号(供自动化验证)
        print('READY', flush=True)
        return True


def on_loaded():
    win = webview.windows[0]
    # native(原生窗体)由 UI 线程稍后赋值,轮询等待;Win11 圆角
    for _ in range(50):
        try:
            if win.native is not None:
                break
        except Exception:
            pass
        time.sleep(0.1)
    try:
        pref = ctypes.c_int(2)   # DWMWCP_ROUND
        dwmapi.DwmSetWindowAttribute(int(win.native.Handle.ToInt64()), 33, ctypes.byref(pref), 4)
    except Exception as e:
        print('round corner failed: %s' % e, file=sys.stderr, flush=True)
    print('WINDOW_LOADED', flush=True)


def main():
    url = 'file:///' + index_path().replace('\\', '/')
    # 启动位置:主屏正中(ctypes 取分辨率,避免 pythonnet 未初始化的问题)
    try:
        w = ctypes.windll.user32.GetSystemMetrics(0)   # SM_CXSCREEN
        h = ctypes.windll.user32.GetSystemMetrics(1)   # SM_CYSCREEN
        x = max(0, (w - 420) // 2)
        y = max(0, (h - 620) // 2)
    except Exception:
        x = y = None
    webview.create_window(
        'Float Reader',
        url=url,
        js_api=Api(),
        width=420, height=620,
        x=x, y=y,
        min_size=(280, 360),
        frameless=True,
        shadow=True,
        on_top=False,
        background_color='#f6f4ef',
    )
    webview.start(func=on_loaded, debug=False, private_mode=False, storage_path=DATA_DIR)


if __name__ == '__main__':
    main()
