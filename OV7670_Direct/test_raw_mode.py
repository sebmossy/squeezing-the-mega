from camera_direct import Camera
import time
import argparse

parser=argparse.ArgumentParser()
parser.add_argument('--divider', type=int, default=22)
parser.add_argument('--frames', type=int, default=6)
args=parser.parse_args()

camera=Camera()
try:
    camera.get_image(args.divider,2,4)
    camera.port.write(b'T')
    assert camera.port.readline().strip()==b'OK'
    frames=[]
    lut={1:0,2:1,4:2,8:3,16:4,32:5,64:6,128:7}
    for i in range(args.frames):
        start=time.monotonic()
        image=camera.get_image(args.divider,2,4)
        data=image.tobytes()
        assert image.size==(640,480) and len(data)==307200
        errors=0
        for y in range(480):
            row=data[y*640:(y+1)*640]
            errors+=sum(row[x] not in lut or row[x+1] not in lut or (lut[row[x+1]]-lut[row[x]])%8 != 1 for x in range(639))
        frames.append(data)
        print('raw frame',i,'seconds',round(time.monotonic()-start,2),'bad steps',errors,flush=True)
    assert all(frame == frames[0] for frame in frames)
    camera.port.write(b'N')
    assert camera.port.readline().strip()==b'OK'
    image=camera.get_image(3,2,3,crop_x=270,crop_y=208)
    assert image.size==(100,64)
    image=camera.get_image(2,2,0)
    assert image.size==(160,120)
    print('Switched raw to crop to fast whole view successfully.',flush=True)
finally:
    camera.close()
