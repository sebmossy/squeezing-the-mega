"""Record a Windows application window to MP4, even when it is obscured."""
import ctypes
import math
import subprocess
import threading
import time
from pathlib import Path

from PIL import ImageGrab, ImageOps


def window_handle(tk_id):
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    user32.GetAncestor.restype = ctypes.c_void_p
    result = user32.GetAncestor(tk_id, 2)  # GA_ROOT, includes window decorations
    if not result:
        raise OSError('Could not find the camera window.')
    return result


class WindowRecorder:
    def __init__(self, hwnd, path, events, fps=10):
        self.hwnd, self.path, self.events, self.fps = hwnd, Path(path), events, fps
        self.stop_event = threading.Event()
        self.stop_time = None

    def stop(self):
        self.stop_time = time.monotonic()
        self.stop_event.set()

    def run(self):
        process = None
        try:
            import imageio_ffmpeg
            self.path.parent.mkdir(parents=True, exist_ok=True)
            first = ImageGrab.grab(window=self.hwnd).convert('RGB')
            width, height = first.size
            size = (width - width % 2, height - height % 2)
            if min(size) < 2:
                raise RuntimeError('The camera window is too small to record.')
            command = [imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error',
                       '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{size[0]}x{size[1]}',
                       '-r', str(self.fps), '-i', 'pipe:0', '-an', '-c:v', 'libx264',
                       '-preset', 'ultrafast', '-crf', '23', '-pix_fmt', 'yuv420p',
                       '-movflags', '+faststart', str(self.path)]
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.events.put(('started', self.path))
            start = time.monotonic()
            frames = 0
            last_frame = None
            while not self.stop_event.is_set():
                frame = first if frames == 0 else ImageGrab.grab(window=self.hwnd).convert('RGB')
                if frame.size != size:
                    frame = ImageOps.pad(frame, size, color='black')
                last_frame = frame.tobytes()
                target = max(frames + 1, math.floor((time.monotonic() - start) * self.fps) + 1)
                for _ in range(target - frames):
                    process.stdin.write(last_frame)
                frames = target
                self.stop_event.wait(max(0, start + frames / self.fps - time.monotonic()))
            if last_frame is not None and self.stop_time is not None:
                target = math.ceil(max(0, self.stop_time - start) * self.fps)
                for _ in range(max(0, target - frames)):
                    process.stdin.write(last_frame)
                frames = max(frames, target)
            process.stdin.close()
            code = process.wait(timeout=30)
            if code or not frames:
                raise RuntimeError(f'Video encoder failed (exit {code}, {frames} frames).')
            self.events.put(('saved', (self.path, frames, self.fps)))
        except Exception as exc:
            if process and process.poll() is None:
                process.kill()
                process.wait()
            self.events.put(('error', str(exc)))
