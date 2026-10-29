"""Exercise continuous fast view and transitions on the connected camera."""
import time
from camera_direct import Camera

c = Camera()
try:
    c.get_image(2, 2, 0)
    c.port.write(b'T')
    assert c.port.readline().strip() == b'OK'
    reference = c.get_image(2, 2, 0, prefetch=True).tobytes()
    for i in range(15):
        start = time.monotonic()
        frame = c.get_image(2, 2, 0, prefetch=True).tobytes()
        assert frame == reference, f'Pattern frame {i} differs'
        print(f'frame={i} seconds={time.monotonic()-start:.3f} identical=True', flush=True)
    crop = c.get_image(3, 2, 3, crop_x=270, crop_y=208)
    assert crop.size == (100, 64)
    print('queued fast view -> crop: OK', flush=True)
    c.get_image(2, 2, 0, prefetch=True)
    changed = c.get_image(2, 8, 0, prefetch=True)
    assert changed.size == (160, 120)
    print('queued fast view -> new exposure: OK', flush=True)
    c.get_image(3, 2, 3)
    c.port.write(b'N')
    assert c.port.readline().strip() == b'OK'
finally:
    c.close()
