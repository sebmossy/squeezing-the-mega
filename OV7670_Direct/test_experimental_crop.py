import time
import argparse
from collections import Counter
from camera_direct import Camera

parser=argparse.ArgumentParser()
parser.add_argument('--divider', type=int, default=2)
parser.add_argument('--frames', type=int, default=10)
args=parser.parse_args()

camera=Camera()
lut={1:0,2:1,4:2,8:3,16:4,32:5,64:6,128:7}
try:
    normal=camera.get_image(3,2,3,crop_x=270,crop_y=208)
    assert normal.size==(100,64)
    camera.get_image(args.divider,2,3,crop_x=270,crop_y=208,experimental=True)
    camera.port.write(b'T')
    assert camera.port.readline().strip()==b'OK'
    for n in range(args.frames):
        start=time.monotonic()
        image=camera.get_image(args.divider,2,3,crop_x=270,crop_y=208,experimental=True)
        starts=Counter()
        bad=0
        for y in range(image.height):
            row=[image.getpixel((x,y)) for x in range(image.width)]
            starts[row[0]]+=1
            bad+=sum(row[x] not in lut or row[x+1] not in lut or
                     (lut[row[x+1]]-lut[row[x]])%8 != 1 for x in range(image.width-1))
        print('frame',n,'seconds',round(time.monotonic()-start,2),'bad transitions',bad,'row starts',dict(starts),flush=True)
    camera.port.write(b'N')
    assert camera.port.readline().strip()==b'OK'
    image=camera.get_image(3,2,3,crop_x=270,crop_y=208)
    assert image.size==(100,64)
    print('Returned to normal crop successfully.',flush=True)
finally:
    camera.close()
