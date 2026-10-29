import serial
import time

port = serial.Serial('COM8', 1000000, timeout=3, write_timeout=3)
try:
    print('ready:', port.readline(), flush=True)
    for i in range(5):
        port.write(b'B')
        header = port.readline()
        start = time.monotonic()
        data = port.read(32768 + 6)
        expected = bytes(range(256)) * 128 + b'DONE\r\n'
        mismatches = sum(a != b for a, b in zip(data, expected))
        first = next((j for j, (a,b) in enumerate(zip(data,expected)) if a != b), None)
        print(f'run={i} header={header!r} bytes={len(data)} first_mismatch={first} mismatches={mismatches} tail={data[-10:].hex()} time={time.monotonic()-start:.3f}', flush=True)
finally:
    port.close()
