import tkinter as tk
from tkinter import ttk
from PIL import Image
import camera_direct as app

calls = []

class FakeCamera:
    def get_image(self, divider, exposure, mode=2, progress=None, crop_x=270, crop_y=208, phase=None, experimental=False, prefetch=False):
        calls.append((divider, mode, crop_x, crop_y, experimental))
        width, height = app.RESOLUTIONS[mode]
        for row in range(0, height, 8):
            count = min(8, height-row)
            progress(row+count, bytes([100]) * width * count)
        return Image.new('L', (width, height), 100)
    def close(self):
        pass

original_tk = tk.Tk
class TestTk(original_tk):
    def __init__(self):
        super().__init__()
        self.after(300, self.start_test)
        self.after(1200, self.click_image)
        self.after(1800, self.toggle_experimental)
        self.after(2400, self.toggle_raw)
        self.after(3200, self.fast_full)
        self.after(3900, self.toggle_raw)
        self.after(4600, self.click_image)
        self.after(5300, self.finish_test)

    def walk(self, root):
        for child in root.winfo_children():
            yield child
            yield from self.walk(child)

    def start_test(self):
        next(w for w in self.walk(self) if isinstance(w,ttk.Button) and w.cget('text')=='Show full view').invoke()

    def click_image(self):
        label = next(w for w in self.walk(self) if isinstance(w,ttk.Label) and hasattr(w,'photo'))
        label.event_generate('<Button-1>', x=label.winfo_width()//2, y=label.winfo_height()//2)

    def finish_test(self):
        for ident in self.tk.call('after', 'info'):
            self.after_cancel(ident)
        self.destroy()

    def toggle_experimental(self):
        next(w for w in self.walk(self) if isinstance(w,ttk.Checkbutton) and w.cget('text')=='Experimental faster crop').invoke()

    def toggle_raw(self):
        next(w for w in self.walk(self) if isinstance(w,ttk.Checkbutton) and w.cget('text')=='Raw sensor stream (640 × 480)').invoke()

    def fast_full(self):
        next(w for w in self.walk(self) if isinstance(w,ttk.Button) and w.cget('text')=='Fast full view').invoke()

app.Camera = FakeCamera
app.tk.Tk = TestTk
app.main()
assert calls and calls[0][1]==2 and (3,3,270,208,False) in calls and (2,3,270,208,True) in calls and (22,4,270,208,False) in calls, calls
assert any(divider == 2 and mode == 0 for divider, mode, _, _, _ in calls[calls.index((22,4,270,208,False))+1:]), calls
assert any(mode == 3 and experimental for _, mode, _, _, experimental in calls[calls.index((22,4,270,208,False))+1:]), calls
print('Crop click and experimental and raw toggles select the correct capture modes.')
