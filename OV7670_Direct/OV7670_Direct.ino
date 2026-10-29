#include <Wire.h>

const byte CAM = 0x21;
uint16_t imageWidth = 640, imageHeight = 480;
bool rawCropMode = false;
byte activeDivider = 31;
byte activeMode = 2;
const uint16_t CROP_WIDTH = 100, CROP_HEIGHT = 64;
byte cropBuffer[CROP_WIDTH * CROP_HEIGHT];
int16_t cropWindowX = -1, cropWindowY = -1;
bool cropWindowExperimental = false;
bool cropCanContinue = false;
unsigned long cropFinishedAt = 0;

bool regWrite(byte reg, byte val) {
  Wire.beginTransmission(CAM); Wire.write(reg); Wire.write(val);
  return Wire.endTransmission() == 0;
}

bool level(byte mask, bool high, unsigned long timeout) {
  unsigned long start = millis();
  while (!!(PINL & mask) != high) if (millis() - start > timeout) return false;
  return true;
}

bool setupCamera() {
  const byte regs[][2] = {
    {0x12,0x80}, {0x11,0x1f}, {0x12,0x00}, {0x0c,0x00}, {0x3e,0x00},
    {0x70,0x3a}, {0x71,0x35}, {0x72,0x11}, {0x73,0xf0}, {0xa2,0x02},
    {0x17,0x13}, {0x18,0x01}, {0x32,0xf6}, {0x19,0x02}, {0x1a,0x7a}, {0x03,0x0a},
    {0x3a,0x04}, {0x40,0xc0}, {0x13,0x88}, {0x00,0x00}, {0x07,0x00}, {0x04,0x00}, {0x10,0x02},
    {0x42,0x00}
  };
  for (unsigned i=0; i<sizeof(regs)/sizeof(regs[0]); i++) {
    if (!regWrite(regs[i][0], regs[i][1])) return false;
    if (i==0) delay(100);
  }
  return true;
}

bool setResolution(byte mode) {
  // VGA, QVGA and QQVGA sensor scaling and window settings.
  const byte com3 = mode == 2 ? 0x00 : 0x04;
  const byte com14 = mode == 2 ? 0x00 : mode == 1 ? 0x19 : 0x1a;
  const byte dcw = mode == 0 ? 0x22 : 0x11;
  const byte pclkdiv = mode == 2 ? 0xf0 : mode == 1 ? 0xf1 : 0xf2;
  const byte hstart = mode == 2 ? 0x13 : 0x16;
  const byte hstop = mode == 2 ? 0x01 : 0x04;
  const byte href = mode == 2 ? 0xf6 : mode == 1 ? 0x24 : 0xa4;
  if (!regWrite(0x0c,com3) || !regWrite(0x3e,com14) || !regWrite(0x72,dcw) ||
      !regWrite(0x73,pclkdiv) || !regWrite(0x17,hstart) || !regWrite(0x18,hstop) ||
      !regWrite(0x32,href) || !regWrite(0x19,0x02) || !regWrite(0x1a,0x7a) ||
      !regWrite(0x03,0x0a)) return false;
  imageWidth = mode == 2 ? 640 : mode == 1 ? 320 : 160;
  imageHeight = mode == 2 ? 480 : mode == 1 ? 240 : 120;
  return true;
}

bool edge() {
  uint16_t limit=60000;
  while (PINL & _BV(PL0)) if (!(PINL & _BV(PL2)) || --limit == 0) return false;
  limit=60000;
  while (!(PINL & _BV(PL0))) if (!(PINL & _BV(PL2)) || --limit == 0) return false;
  return true;
}

static inline __attribute__((always_inline)) bool fastViewEdge() {
  uint8_t limit=255;
  while (PINL & _BV(PL0)) if (--limit==0) return false;
  limit=255;
  while (!(PINL & _BV(PL0))) if (--limit==0) return false;
  return PINL & _BV(PL2);
}

static inline __attribute__((always_inline)) bool cropEdge() {
  uint8_t limit=255;
  while (PINL & _BV(PL0)) if (--limit == 0) return false;
  limit=255;
  while (!(PINL & _BV(PL0))) if (--limit == 0) return false;
  return PINL & _BV(PL2);
}

bool setCropWindow(uint16_t x, uint16_t y, bool experimental) {
  // VGA's horizontal coordinates wrap at 784; add 16 pixels of timing margin.
  uint16_t hstart = (157 + x) % 784;
  uint16_t hstop = (hstart + CROP_WIDTH + 16) % 784;
  uint16_t vstart = 10 + y, vstop = vstart + CROP_HEIGHT;
  byte href = (experimental ? 0x80 : 0xc0) | ((hstop & 7) << 3) | (hstart & 7);
  byte vref = ((vstop & 3) << 2) | (vstart & 3);
  bool ok = regWrite(0x17,hstart >> 3) && regWrite(0x18,hstop >> 3) &&
    regWrite(0x32,href) && regWrite(0x19,vstart >> 2) && regWrite(0x1a,vstop >> 2) &&
    regWrite(0x03,vref);
  if (ok) { cropWindowX=x; cropWindowY=y; cropWindowExperimental=experimental; }
  return ok;
}

// Experimental divider-2 reader: synchronize once, then sample every 24 cycles.
bool readTimedCrop(byte *destination) __attribute__((noinline));
bool readTimedCrop(byte *destination) {
  uint8_t success;
  asm volatile (
    "clr %[ok]\n\t"
    "ldi r19, 255\n\t"
    "1: lds r18, %[clockpin]\n\t"
    "sbrs r18, 0\n\t"
    "rjmp 2f\n\t"
    "dec r19\n\t"
    "brne 1b\n\t"
    "rjmp 9f\n\t"
    "2: ldi r19, 255\n\t"
    "3: lds r18, %[clockpin]\n\t"
    "sbrc r18, 0\n\t"
    "rjmp 4f\n\t"
    "dec r19\n\t"
    "brne 3b\n\t"
    "rjmp 9f\n\t"
    "4:\n\t"
    ".rept 100\n\t"
    "in __tmp_reg__, %[pixels]\n\t"
    "st Z+, __tmp_reg__\n\t"
    ".rept 10\n\t"
    "rjmp .+0\n\t"
    ".endr\n\t"
    "nop\n\t"
    ".endr\n\t"
    "ldi %[ok], 1\n\t"
    "9:\n\t"
    : "+z" (destination), [ok] "=&d" (success)
    : [pixels] "I" (_SFR_IO_ADDR(PINC)), [clockpin] "n" (_SFR_MEM_ADDR(PINL))
    : "r0", "r18", "r19", "memory"
  );
  return success;
}

// Experimental test at divider 1: sample every 16 CPU cycles.
bool readTimedCropDivider1(byte *destination) __attribute__((noinline));
bool readTimedCropDivider1(byte *destination) {
  uint8_t success;
  asm volatile (
    "clr %[ok]\n\t"
    "ldi r19, 255\n\t"
    "1: lds r18, %[clockpin]\n\t"
    "sbrs r18, 0\n\t"
    "rjmp 2f\n\t"
    "dec r19\n\t"
    "brne 1b\n\t"
    "rjmp 9f\n\t"
    "2: ldi r19, 255\n\t"
    "3: lds r18, %[clockpin]\n\t"
    "sbrc r18, 0\n\t"
    "rjmp 4f\n\t"
    "dec r19\n\t"
    "brne 3b\n\t"
    "rjmp 9f\n\t"
    "4:\n\t"
    ".rept 100\n\t"
    "in __tmp_reg__, %[pixels]\n\t"
    "st Z+, __tmp_reg__\n\t"
    ".rept 6\n\t"
    "rjmp .+0\n\t"
    ".endr\n\t"
    "nop\n\t"
    ".endr\n\t"
    "ldi %[ok], 1\n\t"
    "9:\n\t"
    : "+z" (destination), [ok] "=&d" (success)
    : [pixels] "I" (_SFR_IO_ADDR(PINC)), [clockpin] "n" (_SFR_MEM_ADDR(PINL))
    : "r0", "r18", "r19", "memory"
  );
  return success;
}

// Experimental test at divider 0: sample every 8 CPU cycles.
bool readTimedCropDivider0(byte *destination) __attribute__((noinline));
bool readTimedCropDivider0(byte *destination) {
  uint8_t success;
  asm volatile (
    "clr %[ok]\n\t"
    "ldi r19, 255\n\t"
    "1: lds r18, %[clockpin]\n\t"
    "sbrs r18, 0\n\t"
    "rjmp 2f\n\t"
    "dec r19\n\t"
    "brne 1b\n\t"
    "rjmp 9f\n\t"
    "2: ldi r19, 255\n\t"
    "3: lds r18, %[clockpin]\n\t"
    "sbrc r18, 0\n\t"
    "rjmp 4f\n\t"
    "dec r19\n\t"
    "brne 3b\n\t"
    "rjmp 9f\n\t"
    "4:\n\t"
    ".rept 100\n\t"
    "in __tmp_reg__, %[pixels]\n\t"
    "st Z+, __tmp_reg__\n\t"
    " .rept 2\n\t"
    "rjmp .+0\n\t"
    ".endr\n\t"
    "nop\n\t"
    ".endr\n\t"
    "ldi %[ok], 1\n\t"
    "9:\n\t"
    : "+z" (destination), [ok] "=&d" (success)
    : [pixels] "I" (_SFR_IO_ADDR(PINC)), [clockpin] "n" (_SFR_MEM_ADDR(PINL))
    : "r0", "r18", "r19", "memory"
  );
  return success;
}

void captureCrop(uint16_t x0, uint16_t y0, bool experimental) {
  if (!rawCropMode) { Serial.println("ERR crop mode not active"); return; }
  if (experimental && activeDivider != 2 && activeDivider != 1 && activeDivider != 0) { Serial.println("ERR timed crop needs divider 0, 1 or 2"); return; }
  bool sameWindow = cropWindowX == x0 && cropWindowY == y0 && cropWindowExperimental == experimental;
  bool continuous = sameWindow && cropCanContinue && millis() - cropFinishedAt < 100;
  cropCanContinue = false;
  if (!sameWindow &&
      !setCropWindow(x0,y0,experimental)) {
    Serial.println("ERR crop window"); return;
  }
  x0=0; y0=0;
  if (!continuous && (!level(_BV(PL1), false, 20000) || !level(_BV(PL1), true, 20000) ||
      !level(_BV(PL1), false, 20000))) { Serial.println(F("ERR sync")); return; }
  Serial.println(F("FRAME")); Serial.flush();
  byte error = 0;
  uint16_t errorRow = 0, errorPixel = 0;
  noInterrupts();
  for (uint16_t row=0; row<y0+CROP_HEIGHT && !error; row++) {
    // The selected window can start near the bottom of a slow VGA frame.
    // Allow enough time to reach it while interrupts are disabled.
    uint32_t timeout=20000000UL;
    while (PINL & _BV(PL2)) if (--timeout==0) { error=1; break; }
    timeout=20000000UL;
    while (!(PINL & _BV(PL2))) if (--timeout==0) { error=1; break; }
    if (row < y0) continue;
    byte *destination = cropBuffer + (row-y0)*CROP_WIDTH;
    if (experimental) {
      if (!cropEdge() || !(activeDivider == 0 ? readTimedCropDivider0(destination) : activeDivider == 1 ? readTimedCropDivider1(destination) : readTimedCrop(destination))) error=2;
    } else {
      for (uint16_t x=0; x<CROP_WIDTH && !error; x++) {
        if (!cropEdge()) { error=2; errorPixel=x; break; }
        *destination++=PINC;
      }
    }
    if (error) errorRow=row;
  }
  interrupts();
  if (error) {
    Serial.print("ERR "); Serial.print(error); Serial.print(" row "); Serial.print(errorRow);
    Serial.print(" pixel "); Serial.println(errorPixel);
  } else {
    Serial.write(cropBuffer, sizeof(cropBuffer));
    Serial.println("DONE");
    cropFinishedAt = millis();
    cropCanContinue = true;
  }
}

void capture() {
  if (!level(_BV(PL1), false, 20000) || !level(_BV(PL1), true, 20000) ||
      !level(_BV(PL1), false, 20000)) { Serial.println("ERR sync"); return; }
  Serial.println("FRAME"); Serial.flush();
  UCSR0A |= _BV(TXC0);
  uint16_t errorRow=0, errorPixel=0;
  byte error=0;
  noInterrupts();
  for (uint16_t row=0; row<imageHeight && !error; row++) {
    uint32_t timeout=4000000UL;
    while (PINL & _BV(PL2)) if (--timeout==0) { error=1; break; }
    timeout=4000000UL;
    while (!(PINL & _BV(PL2))) if (--timeout==0) { error=1; break; }
    for (uint16_t x=0; x<imageWidth && !error; x++) {
      if (!edge()) { error=2; errorPixel=x; break; }
      byte pixel=PINC;
      if (!(UCSR0A & _BV(UDRE0))) { error=3; errorPixel=x; break; }
      UDR0=pixel;
      if (x!=imageWidth-1 && !edge()) { error=2; errorPixel=x; break; }
    }
    if (error) errorRow=row;
  }
  while (!(UCSR0A & _BV(TXC0))) {}
  UCSR0A |= _BV(TXC0);
  interrupts();
  if (error) {
    Serial.print("ERR "); Serial.print(error); Serial.print(" row "); Serial.print(errorRow);
    Serial.print(" pixel "); Serial.println(errorPixel);
  } else Serial.println("DONE");
}

// QQVGA uses the shared crop buffer as a 160-byte line buffer.
void captureFastView() {
  // A command can arrive during VS. Use that pulse instead of discarding it.
  if (!level(_BV(PL1), true, 20000) || !level(_BV(PL1), false, 20000)) {
    Serial.println("ERR sync"); return;
  }
  Serial.println("FRAME"); Serial.flush();
  for (uint16_t row=0; row<120; row++) {
    if (!level(_BV(PL2), false, 3000)) { Serial.println(F("ERR line end")); return; }
    noInterrupts();
    uint32_t timeout=4000000UL;
    while (!(PINL & _BV(PL2))) if (--timeout==0) { interrupts(); Serial.println(F("ERR row wait")); return; }
    for (uint16_t x=0; x<160; x++) {
      if (!fastViewEdge()) { interrupts(); Serial.println(F("ERR pixel")); return; }
      cropBuffer[x]=PINC;
      if (x!=159 && !fastViewEdge()) { interrupts(); Serial.println(F("ERR chroma")); return; }
    }
    interrupts();
    if (!level(_BV(PL2), false, 3000)) { Serial.println(F("ERR line end")); return; }
    Serial.write(cropBuffer,160);
    Serial.flush();
    if (PINL & _BV(PL2)) { Serial.println(F("ERR missed row")); return; }
  }
  Serial.println(F("DONE"));
}

// Stream full-resolution raw Bayer bytes as they arrive, without a frame buffer.
void captureRawDirect() {
  if (!rawCropMode) { Serial.println(F("ERR raw mode inactive")); return; }
  // Accept a VS pulse already in progress; waiting for it to end first
  // would needlessly discard that frame and wait for the next pulse.
  if (!level(_BV(PL1), true, 20000) || !level(_BV(PL1), false, 20000)) {
    Serial.println(F("ERR sync")); return;
  }
  Serial.println(F("FRAME")); Serial.flush();
  UCSR0A |= _BV(TXC0);
  uint16_t errorRow=0, errorPixel=0;
  byte error=0;
  noInterrupts();
  for (uint16_t row=0; row<480 && !error; row++) {
    uint32_t timeout=4000000UL;
    while (PINL & _BV(PL2)) if (--timeout==0) { error=1; break; }
    timeout=4000000UL;
    while (!(PINL & _BV(PL2))) if (--timeout==0) { error=1; break; }
    for (uint16_t x=0; x<640 && !error; x++) {
      if (!fastViewEdge()) { error=2; errorPixel=x; break; }
      byte pixel=PINC;
      if (!(UCSR0A & _BV(UDRE0))) { error=3; errorPixel=x; break; }
      UDR0=pixel;
    }
    if (error) errorRow=row;
  }
  uint16_t timeout=60000;
  while (!(UCSR0A & _BV(TXC0)) && --timeout) {}
  UCSR0A |= _BV(TXC0);
  interrupts();
  if (error) {
    Serial.print(F("ERR ")); Serial.print(error); Serial.print(F(" row ")); Serial.print(errorRow);
    Serial.print(F(" pixel ")); Serial.println(errorPixel);
  } else Serial.println(F("DONE"));
}

bool settle() {
  for (byte n=0; n<2; n++) {
    if (!level(_BV(PL1), false, 20000) || !level(_BV(PL1), true, 20000)) return false;
  }
  return true;
}

void setup() {
  Serial.begin(1000000);
  pinMode(11,OUTPUT); TCCR1A=0; TCCR1B=0; TCNT1=0; OCR1A=0;
  TCCR1A=_BV(COM1A0); TCCR1B=_BV(WGM12)|_BV(CS10);
  DDRC=0; PORTC=0; pinMode(47,INPUT); pinMode(48,INPUT); pinMode(49,INPUT);
  Wire.begin(); Wire.setClock(100000); Wire.setWireTimeout(25000,true);
  delay(1000);
  // Start immediately; early frames may brighten or darken while the sensor settles.
  Serial.println(setupCamera() ? "READY" : "ERR setup");
}

void loop() {
  if (!Serial.available()) return;
  char cmd=Serial.read();
  if (cmd!='G' && cmd!='J') cropCanContinue = false;
  if (cmd=='B') {
    Serial.println(F("BENCH")); Serial.flush();
    UCSR0A |= _BV(TXC0);
    noInterrupts();
    for (uint16_t i=0; i<32768; i++) {
      while (!(UCSR0A & _BV(UDRE0))) {}
      UDR0 = (byte)i;
    }
    while (!(UCSR0A & _BV(TXC0))) {}
    UCSR0A |= _BV(TXC0);
    interrupts();
    Serial.println(F("DONE"));
  }
  if (cmd=='C') {
    if (imageWidth==160 && !rawCropMode) captureFastView();
    else capture();
  }
  if (cmd=='V') captureRawDirect();
  if (cmd=='T') Serial.println(regWrite(0x70,0xba) && regWrite(0x71,0x35) ? "OK" : "ERR test pattern");
  if (cmd=='N') Serial.println(regWrite(0x70,0x3a) && regWrite(0x71,0x35) ? "OK" : "ERR test pattern");
  if (cmd=='G' || cmd=='J') {
    String line=Serial.readStringUntil('\n');
    int x,y;
    if (sscanf(line.c_str(), "%d %d", &x, &y)!=2 || x<0 || x>640-CROP_WIDTH || (x&1) || y<0 || y>480-CROP_HEIGHT) {
      Serial.println("ERR invalid crop"); return;
    }
    captureCrop(x,y,cmd=='J');
  }
  if (cmd=='S') {
    String line = Serial.readStringUntil('\n');
    int divider, exposure, mode;
    if (sscanf(line.c_str(), "%d %d %d", &divider, &exposure, &mode) != 3 ||
        divider < 0 || divider > 31 || exposure < 1 || exposure > 255 || mode < 0 || mode > 4) {
      Serial.println("ERR invalid settings");
    } else {
      bool newRaw = mode == 3 || mode == 4;
      bool ok = true;
      if (mode != activeMode) {
        if (rawCropMode && !newRaw) ok = setupCamera();
        if (ok) ok = setResolution(newRaw ? 2 : mode);
        cropWindowX = -1; cropWindowY = -1;
        if (ok && newRaw && !rawCropMode) {
          ok = regWrite(0x12,0x01) && regWrite(0x3d,0x08) &&
               regWrite(0x41,0x3d) && regWrite(0x76,0xe1);
        }
      }
      if (ok) ok = regWrite(0x11,divider) && regWrite(0x10,exposure);
      if (ok) { rawCropMode = newRaw; activeDivider = divider; activeMode = mode; }
      Serial.println(ok ? "OK" : "ERR camera settings");
    }
  }
}
