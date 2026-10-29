import queue
import sys
import threading
import time
from datetime import datetime
import math
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

import serial
from PIL import Image, ImageTk, ImageFilter, ImageDraw
from window_recorder import WindowRecorder, window_handle

PORT = 'COM8'
RESOLUTIONS = ((160, 120), (320, 240), (640, 480), (100, 64), (640, 480))
PHOTOS = Path(__file__).resolve().parent.parent / 'OV7670_Camera' / 'photos'
RECORDINGS = PHOTOS.parent / 'recordings'
DEMO_HOLD_SECONDS = 10
DEMO_CROP_SHIFT_SECONDS = 3


class Camera:
    def __init__(self):
        self.port = None
        self.settings = (31, 2, 2)
        self.fast_prefetched = False

    def close(self):
        if self.port:
            self.port.close()
            self.port = None
        self.fast_prefetched = False

    def connect(self):
        if self.port:
            return
        self.port = serial.Serial(PORT, 1000000, timeout=90, write_timeout=3)
        self.settings = (31, 2, 2)
        result = self.port.readline().strip()
        if result != b'READY':
            raise RuntimeError(f'Camera did not initialize: {result!r}')

    def get_image(self, divider, exposure, mode=2, progress=None, crop_x=270, crop_y=208, phase=None, experimental=False, prefetch=False):
        try:
            width, height = RESOLUTIONS[mode]
            if not self.port and phase:
                phase('Connecting to camera')
            self.connect()
            use_prefetched = self.fast_prefetched and (divider, exposure, mode) == self.settings
            if self.fast_prefetched and not use_prefetched:
                if phase:
                    phase('Finishing the queued fast frame')
                self.port.timeout = 10
                header = self.port.readline().strip()
                discarded = self.port.read(160 * 120) if header == b'FRAME' else b''
                trailer = self.port.readline().strip() if len(discarded) == 160 * 120 else b''
                self.port.timeout = 90
                if header != b'FRAME' or len(discarded) != 160 * 120 or trailer != b'DONE':
                    raise RuntimeError('Could not finish the queued fast-view frame.')
                self.fast_prefetched = False
            if (divider, exposure, mode) != self.settings:
                if phase:
                    phase('Applying camera settings')
                self.port.write(f'S {divider} {exposure} {mode}\n'.encode('ascii'))
                result = self.port.readline().strip()
                if result != b'OK':
                    raise RuntimeError(f'Camera settings failed: {result!r}')
                self.settings = (divider, exposure, mode)
            if phase:
                phase('Waiting for the next frame')
            crop_command = 'J' if experimental else 'G'
            if use_prefetched:
                self.fast_prefetched = False
            else:
                self.port.write(f'{crop_command} {crop_x} {crop_y}\n'.encode('ascii') if mode == 3 else b'V' if mode == 4 else b'C')
            result = self.port.readline().strip()
            if result != b'FRAME':
                raise RuntimeError(f'Camera could not start capture: {result!r}')
            if mode == 3 and phase:
                phase('Waiting for crop pixels and capturing on the Mega')
            elif mode == 0 and phase:
                phase('Receiving fast view from the camera')
            self.port.timeout = 10
            strips = []
            for index in range(0, height, 8):
                strip_rows = min(8, height - index)
                strip = self.port.read(width * strip_rows)
                if len(strip) != width * strip_rows:
                    detail = strip[-80:].decode('ascii', 'replace')
                    advice = 'Try a higher clock divider, such as 25 or 31.' if mode == 4 else 'Check the camera connection and try the normal crop divider (3).'
                    raise RuntimeError(f'Incomplete stream near row {index}. Camera output: {detail!r}. {advice}')
                strips.append(strip)
                if progress:
                    progress(index + strip_rows, strip)
                if prefetch and mode == 0 and index + strip_rows == height - 16:
                    self.port.write(b'C')
                    self.fast_prefetched = True
            result = self.port.readline().strip()
            self.port.timeout = 90
            if result != b'DONE':
                raise RuntimeError(f'Camera did not finish: {result!r}')
            return Image.frombytes('L', (width, height), b''.join(strips))
        except Exception:
            self.close()
            raise


def main():
    root = tk.Tk()
    root.title('OV7670 Camera — direct full-resolution capture')
    camera, events = Camera(), queue.Queue()
    state = {'busy': False, 'closed': False, 'save_next': False, 'view_next': False,
             'rows': 0, 'started': 0, 'divider_status': '', 'display_mode': None,
             'display_crop': (270, 208), 'phase': 'Connecting to camera',
             'last_picture': None, 'last_mode': None, 'last_crop': None, 'last_draw': 0.0}
    divider, exposure = tk.IntVar(value=31), tk.IntVar(value=2)
    resolution = tk.IntVar(value=2)
    raw_stream = tk.BooleanVar(value=False)
    crop_x, crop_y = tk.IntVar(value=270), tk.IntVar(value=208)
    resolution_label = tk.StringVar(value='640 × 480')
    preview = tk.BooleanVar(value=False)
    experimental = tk.BooleanVar(value=False)
    experiment_note = tk.StringVar(value='')
    status = tk.StringVar(value='Take a full image, then click the area you want to crop.')
    recording_status = tk.StringVar(value='')
    demo_status = tk.StringVar(value='')
    recording_events = queue.Queue()
    recording = {'recorder': None, 'deadline': None, 'closing': False}
    demo = {'active': False, 'index': 0, 'waiting': False, 'target': None, 'after': None,
            'crop_after': None, 'crop_index': 0, 'overview': None}

    ttk.Label(root, textvariable=demo_status, font=('Segoe UI', 11, 'bold'),
              foreground='#155b9a', wraplength=650).pack(padx=20, pady=(10, 0))

    image_label = ttk.Label(root, text='Your photo will appear here.', anchor='center')
    image_label.pack(padx=20, pady=12, fill='both', expand=True)
    controls = ttk.LabelFrame(root, text='Camera settings')
    controls.pack(padx=20, fill='x')
    ttk.Label(controls, text='Clock divider (0–31; lower is faster)').grid(row=0, column=0, sticky='w', padx=8, pady=5)
    ttk.Spinbox(controls, from_=0, to=31, textvariable=divider, width=6).grid(row=0, column=1, padx=8)
    ttk.Label(controls, text='Manual brightness / exposure (1–255)').grid(row=1, column=0, sticky='w', padx=8, pady=5)
    ttk.Spinbox(controls, from_=1, to=255, textvariable=exposure, width=6).grid(row=1, column=1, padx=8)
    tk.Scale(controls, from_=1, to=255, orient='horizontal', variable=exposure,
             showvalue=False, resolution=1, length=180).grid(row=1, column=2, padx=8)
    ttk.Label(controls, text='Resolution (applies to next photo)').grid(row=2, column=0, sticky='w', padx=8, pady=5)
    def update_resolution(value):
        mode = int(float(value))
        width, height = RESOLUTIONS[mode]
        resolution_label.set(f'{width} × {height}' + (' raw crop' if mode == 3 else ' fast view' if mode == 0 else ' raw stream' if mode == 4 else ''))
        raw_stream.set(mode == 4)
        if mode == 0 and divider.get() in (31, 22, 3):
            divider.set(2)
        elif mode == 3 and (experimental.get() or divider.get() in (31, 2, 3)):
            divider.set(2 if experimental.get() else 3)
        elif mode == 4 and divider.get() in (2, 3, 31):
            divider.set(22)
        elif mode in (1, 2) and divider.get() in (2, 3):
            divider.set(31)
    tk.Scale(controls, from_=0, to=4, orient='horizontal', variable=resolution,
             showvalue=False, resolution=1, command=update_resolution, length=180).grid(row=2, column=1, padx=8)
    ttk.Label(controls, textvariable=resolution_label).grid(row=2, column=2, padx=8)
    ttk.Label(controls, text='Click the image to choose a 100 × 64 crop.').grid(row=3, column=0, columnspan=3, sticky='w', padx=8, pady=5)
    ttk.Label(controls, textvariable=experiment_note, foreground='#9b3b00').grid(row=4, column=0, columnspan=3, sticky='w', padx=8)
    ttk.Label(root, textvariable=status, wraplength=620).pack(padx=20, pady=8)
    buttons = ttk.Frame(root)
    buttons.pack(pady=8)

    def capture(save):
        if state['closed']:
            return
        if state['busy']:
            if save:
                state['save_next'] = True
                status.set('Photo queued for the next frame.')
            return
        try:
            d, e, mode = divider.get(), exposure.get(), resolution.get()
            x, y = crop_x.get(), crop_y.get()
            use_experimental = bool(experimental.get() and mode == 3)
            if not 0 <= d <= 31 or not 1 <= e <= 255 or not 0 <= mode <= 4:
                raise ValueError('Clock divider must be 0–31 and exposure 1–255.')
            if mode == 3 and (not 0 <= x <= 540 or x % 2 or not 0 <= y <= 416):
                raise ValueError('Crop X must be even and 0–540; Y must be 0–416.')
            if use_experimental and d != 2 and not (demo['active'] and d == 1):
                raise ValueError('Experimental crop requires clock divider 2.')
        except (ValueError, tk.TclError) as exc:
            preview.set(False)
            messagebox.showerror('Invalid settings', str(exc))
            return
        state['busy'] = True
        state['rows'] = 0
        state['started'] = time.monotonic()
        state['divider_status'] = ''
        state['phase'] = 'Preparing frame'
        width, height = RESOLUTIONS[mode]
        state['width'], state['height'] = width, height
        state['mode'] = mode
        state['capture_crop'] = (x, y)
        state['capture_experimental'] = use_experimental
        prefetch_fast = bool(mode == 0 and preview.get() and not save)
        same_view = state['last_mode'] == mode and (mode != 3 or state['last_crop'] == (x, y))
        state['partial'] = state['last_picture'].copy() if same_view and state['last_picture'] else Image.new('L', (width, height), color=32)
        status.set('Preparing frame...')

        def work():
            start = time.monotonic()
            try:
                picture = camera.get_image(d, e, mode, lambda row, data: events.put(('progress', (row, data))), x, y,
                                           lambda phase: events.put(('phase', phase)), use_experimental, prefetch_fast)
                if mode == 3:
                    picture = picture.filter(ImageFilter.GaussianBlur(0.6))
                path = None
                if save:
                    PHOTOS.mkdir(parents=True, exist_ok=True)
                    path = PHOTOS / (datetime.now().strftime('photo_%Y%m%d_%H%M%S_%f') + '.png')
                    picture.save(path)
                    if mode == 4:
                        path.with_suffix('.bin').write_bytes(picture.tobytes())
                events.put(('done', (picture, path, time.monotonic()-start)))
            except Exception as exc:
                events.put(('error', str(exc)))

        threading.Thread(target=work, daemon=True).start()

    button = ttk.Button(buttons, text='Take Photo', command=lambda: capture(True))
    button.pack(side='left', padx=8)

    def request_view():
        if state['busy']:
            state['view_next'] = True
            status.set('View queued for the next frame.')
        else:
            capture(False)

    def show_full():
        preview.set(True)
        resolution.set(2)
        update_resolution('2')
        request_view()

    ttk.Button(buttons, text='Show full view', command=show_full).pack(side='left', padx=8)

    def fast_full():
        preview.set(True)
        resolution.set(0)
        divider.set(2)
        update_resolution('0')
        request_view()

    ttk.Button(buttons, text='Fast full view', command=fast_full).pack(side='left', padx=8)

    def toggle_raw_stream():
        mode = 4 if raw_stream.get() else 2
        resolution.set(mode)
        update_resolution(str(mode))
        preview.set(True)
        request_view()

    ttk.Checkbutton(controls, text='Raw sensor stream (640 × 480)', variable=raw_stream,
                    command=toggle_raw_stream).grid(row=6, column=0, columnspan=3, sticky='w', padx=8, pady=5)

    def toggle_experimental():
        enabled = experimental.get()
        experiment_note.set('Experimental crop: rows can shift by a pixel.' if enabled else '')
        if resolution.get() == 3:
            divider.set(2 if enabled else 3)
            request_view()

    ttk.Checkbutton(controls, text='Experimental faster crop', variable=experimental,
                    command=toggle_experimental).grid(row=5, column=0, columnspan=3, sticky='w', padx=8, pady=5)

    def select_area(event):
        displayed = state['display_mode']
        if displayed is None:
            status.set('First select Show full view or Take Photo, then click the image.')
            return
        left = (image_label.winfo_width() - 640) // 2
        top = (image_label.winfo_height() - 480) // 2
        px, py = event.x - left, event.y - top
        if not (0 <= px < 640 and 0 <= py < 480):
            return
        if displayed == 3:
            old_x, old_y = state['display_crop']
            center_x = old_x + px * 100 // 640
            center_y = old_y + py * 64 // 480
        else:
            center_x, center_y = px, py
        x = max(0, min(540, center_x - 50)) & ~1
        y = max(0, min(416, center_y - 32))
        crop_x.set(x)
        crop_y.set(y)
        resolution.set(3)
        update_resolution('3')
        preview.set(True)
        request_view()

    image_label.bind('<Button-1>', select_area)

    def apply_divider():
        try:
            value = divider.get()
            if not 0 <= value <= 31:
                raise ValueError('Clock divider must be 0–31.')
        except (ValueError, tk.TclError) as exc:
            messagebox.showerror('Invalid setting', str(exc))
            return
        if state['busy']:
            state['divider_status'] = f'Divider {value} will apply to the next photo.'
        else:
            status.set(f'Divider {value} will be applied to the next photo.')

    ttk.Button(buttons, text='Apply divider to next photo', command=apply_divider).pack(side='left', padx=8)

    def toggle_preview():
        if preview.get(): capture(False)

    ttk.Checkbutton(buttons, text='Live preview', variable=preview, command=toggle_preview).pack(side='left', padx=8)

    demo_modes = (
        ('Fast full view — 160 × 120, divider 2', 0, 2, False),
        ('Selected crop — normal, divider 3', 3, 3, False),
        ('Experimental crop — divider 1; rows may shift', 3, 1, True),
        ('Full view — 640 × 480, divider 20', 2, 20, False),
        ('Raw sensor stream — 640 × 480, divider 20', 4, 20, False),
    )

    def stop_demo():
        demo['active'] = False
        demo['waiting'] = False
        if demo['after'] is not None:
            root.after_cancel(demo['after'])
            demo['after'] = None
        if demo['crop_after'] is not None:
            root.after_cancel(demo['crop_after'])
            demo['crop_after'] = None
        demo['overview'] = None
        demo_status.set('')

    crop_positions = ((270, 208), (0, 0), (540, 0), (540, 416), (0, 416))

    def shift_demo_crop():
        demo['crop_after'] = None
        if not demo['active'] or not demo['target'] or demo['target'][0] != 3:
            return
        demo['crop_index'] = (demo['crop_index'] + 1) % len(crop_positions)
        x, y = crop_positions[demo['crop_index']]
        crop_x.set(x)
        crop_y.set(y)
        request_view()
        demo['crop_after'] = root.after(int(DEMO_CROP_SHIFT_SECONDS * 1000), shift_demo_crop)

    def render_picture(picture, mode, crop):
        shown = picture.resize((640, 480), Image.Resampling.NEAREST)
        if demo['active'] and mode == 3 and demo['overview'] is not None:
            overview = demo['overview'].resize((256, 192), Image.Resampling.NEAREST).convert('RGB')
            draw = ImageDraw.Draw(overview)
            x, y = crop
            box = (round(x * 256 / 640), round(y * 192 / 480),
                   round((x + 100) * 256 / 640) - 1, round((y + 64) * 192 / 480) - 1)
            draw.rectangle(box, outline='#ff3030', width=3)
            shown = shown.convert('RGB')
            draw = ImageDraw.Draw(shown)
            draw.rectangle((375, 3, 637, 220), fill='white')
            draw.text((380, 6), 'Selected area', fill='black')
            shown.paste(overview, (378, 24))
        return shown

    def next_demo_mode():
        if not demo['active'] or state['closed']:
            return
        demo['after'] = None
        if demo['crop_after'] is not None:
            root.after_cancel(demo['crop_after'])
            demo['crop_after'] = None
        name, mode, clock, timed = demo_modes[demo['index']]
        demo['index'] = (demo['index'] + 1) % len(demo_modes)
        demo['target'] = (mode, timed)
        demo['waiting'] = True
        demo_status.set(f'Switching mode: {name}')
        preview.set(True)
        experimental.set(timed)
        experiment_note.set('Experimental crop: rows can shift by a pixel.' if timed else '')
        resolution.set(mode)
        if mode == 3:
            demo['crop_index'] = 0
            crop_x.set(crop_positions[0][0])
            crop_y.set(crop_positions[0][1])
        update_resolution(str(mode))
        divider.set(clock)
        request_view()

    ttk.Label(root, textvariable=recording_status, wraplength=620).pack(padx=20, pady=(0, 6))

    def recording_countdown():
        deadline = recording['deadline']
        if deadline is None or state['closed']:
            return
        remaining = deadline - time.monotonic()
        if remaining > 0:
            recording_status.set(f'Recording starts in {math.ceil(remaining)} seconds.')
            root.after(100, recording_countdown)
            return
        recording['deadline'] = None
        try:
            hwnd = window_handle(root.winfo_id())
            filename = 'demo_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.mp4'
            recorder = WindowRecorder(hwnd, RECORDINGS / filename, recording_events)
            recording['recorder'] = recorder
            recording_status.set('Starting recording...')
            record_button.configure(text='Stop recording')
            threading.Thread(target=recorder.run, daemon=True).start()
        except Exception as exc:
            record_button.configure(text='Record demo')
            recording_status.set('Recording could not start.')
            messagebox.showerror('Recording error', str(exc))

    def toggle_recording():
        if recording['deadline'] is not None:
            recording['deadline'] = None
            record_button.configure(text='Record demo')
            recording_status.set('Recording cancelled.')
        elif recording['recorder'] is not None:
            stop_demo()
            recording['recorder'].stop()
            record_button.configure(state='disabled')
            recording_status.set('Finishing video...')
        else:
            recording['deadline'] = time.monotonic() + 5
            record_button.configure(text='Cancel recording')
            recording_countdown()

    record_button = ttk.Button(buttons, text='Record demo', command=toggle_recording)
    record_button.pack(side='left', padx=8)

    def poll_recording():
        try:
            while True:
                kind, value = recording_events.get_nowait()
                if kind == 'started':
                    recording_status.set('Recording this window. Click Stop recording when finished.')
                    if not recording['recorder'].stop_event.is_set():
                        demo['active'] = True
                        demo['index'] = 0
                        next_demo_mode()
                elif kind == 'saved':
                    path, frames, fps = value
                    recording['recorder'] = None
                    record_button.configure(text='Record demo', state='normal')
                    recording_status.set(f'Video saved: {path} ({frames / fps:.1f} s)')
                elif kind == 'error':
                    recording['recorder'] = None
                    record_button.configure(text='Record demo', state='normal')
                    recording_status.set('Recording failed.')
                    if not recording['closing']:
                        messagebox.showerror('Recording error', value)
                if recording['closing'] and recording['recorder'] is None:
                    root.destroy()
                    return
        except queue.Empty:
            pass
        root.after(100, poll_recording)

    def poll():
        try:
            while True:
                kind, value = events.get_nowait()
                if kind == 'phase':
                    state['phase'] = value
                    continue
                if kind == 'divider':
                    applied, automatic = value
                    if automatic:
                        divider.set(applied)
                        state['divider_status'] = f'Timing failed; retried at divider {applied}.'
                    else:
                        state['divider_status'] = f'Divider {applied} applied.'
                    continue
                if kind == 'progress':
                    row, data = value
                    strip_rows = len(data) // state['width']
                    strip = Image.frombytes('L', (state['width'], strip_rows), data)
                    state['partial'].paste(strip, (0, row - strip_rows))
                    now = time.monotonic()
                    if row != state['height'] and now - state['last_draw'] >= 0.08:
                        shown = state['partial'].filter(ImageFilter.GaussianBlur(0.6)) if state['mode'] == 3 else state['partial']
                        image_label.photo = ImageTk.PhotoImage(render_picture(shown, state['mode'], state['capture_crop']))
                        image_label.config(image=image_label.photo, text='')
                        state['last_draw'] = now
                    state['rows'] = row
                    state['display_mode'] = state['mode']
                    state['display_crop'] = state['capture_crop']
                    continue
                state['busy'] = False
                if kind == 'done':
                    picture, path, seconds = value
                    image_label.photo = ImageTk.PhotoImage(render_picture(picture, state['mode'], state['capture_crop']))
                    image_label.config(image=image_label.photo, text='')
                    state['display_mode'] = state['mode']
                    state['display_crop'] = state['capture_crop']
                    state['last_picture'] = picture
                    state['last_mode'] = state['mode']
                    state['last_crop'] = state['capture_crop']
                    if demo['active'] and state['mode'] == 0 and demo['target'] == (0, False):
                        demo['overview'] = picture.copy()
                    if demo['active'] and demo['waiting'] and demo['target'] == (state['mode'], state.get('capture_experimental', False)):
                        demo['waiting'] = False
                        name = demo_modes[(demo['index'] - 1) % len(demo_modes)][0]
                        demo_status.set(f'Mode: {name}')
                        demo['after'] = root.after(int(DEMO_HOLD_SECONDS * 1000), next_demo_mode)
                        if state['mode'] == 3:
                            demo['crop_after'] = root.after(int(DEMO_CROP_SHIFT_SECONDS * 1000), shift_demo_crop)
                    if path:
                        status.set(f'Saved PNG and raw .bin: {path}' if state['mode'] == 4 else f'Saved: {path}')
                    else:
                        status.set(f'Preview frame: {seconds:.1f} seconds')
                    if state['save_next']:
                        state['save_next'] = False
                        root.after(1, lambda: capture(True))
                    elif state['view_next']:
                        state['view_next'] = False
                        root.after(1, lambda: capture(False))
                    elif preview.get():
                        root.after(1, lambda: capture(False))
                else:
                    preview.set(False)
                    state['save_next'] = False
                    state['view_next'] = False
                    status.set('Capture failed. See the error message for details.')
                    messagebox.showerror('Camera error', value)
        except queue.Empty:
            pass
        if state['busy']:
            elapsed = int(time.monotonic() - state['started'])
            if state['rows'] == 0:
                status.set(f'{state["phase"]}: {elapsed} seconds. {state["divider_status"]}')
            else:
                status.set(f'Received {state["rows"]} of {state["height"]} rows in {elapsed} seconds. {state["divider_status"]}')
        if not state['closed']: root.after(2 if state['busy'] or preview.get() else 100, poll)

    def close():
        state['closed'] = True
        stop_demo()
        recording['deadline'] = None
        if not state['busy']:
            camera.close()
        if recording['recorder'] is not None:
            recording['closing'] = True
            recording_status.set('Finishing video before closing...')
            recording['recorder'].stop()
            return
        root.destroy()

    root.protocol('WM_DELETE_WINDOW', close)
    poll()
    poll_recording()
    if '--capture' in sys.argv:
        root.after(500, lambda: capture(True))
    elif '--fast' in sys.argv:
        root.after(500, fast_full)
    elif '--view' in sys.argv:
        preview.set(True)
        root.after(500, lambda: capture(False))
    root.mainloop()


if __name__ == '__main__':
    main()
