"""Measure warmed-up capture calls; uses the physical camera and COM8."""
import statistics
import time
from camera_direct import Camera

camera = Camera()
try:
    for label, mode, divider, experimental, count in (
        ('Fast full view 160x120', 0, 2, False, 5),
        ('Normal crop 100x64', 3, 3, False, 5),
        ('Experimental crop 100x64', 3, 2, True, 5),
        ('Raw full frame 640x480', 4, 22, False, 3),
    ):
        camera.get_image(divider, 2, mode, crop_x=270, crop_y=208, experimental=experimental)
        samples = []
        for _ in range(count):
            start = time.perf_counter()
            image = camera.get_image(divider, 2, mode, crop_x=270, crop_y=208, experimental=experimental)
            samples.append(time.perf_counter() - start)
        print(f'{label}: size={image.size} divider={divider} seconds={[round(x, 3) for x in samples]} median={statistics.median(samples):.3f}', flush=True)
finally:
    camera.close()
