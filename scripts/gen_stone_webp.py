import os
from PIL import Image, ImageOps

SRC = "web/assets/stone.jpg"
DST = "web/assets/stone.webp"
TARGET_LONG_EDGE = 1600
QUALITY = 72

def main():
    before_size = os.path.getsize(SRC)

    img = Image.open(SRC)
    img = ImageOps.exif_transpose(img)
    img = img.convert("L")  # grayscale

    w, h = img.size
    long_edge = max(w, h)
    scale = TARGET_LONG_EDGE / long_edge
    new_w, new_h = round(w * scale), round(h * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)

    img.save(DST, "WEBP", quality=QUALITY, method=6)

    after_size = os.path.getsize(DST)
    print(f"before: {SRC} {before_size/1024:.1f}KB ({w}x{h})")
    print(f"after:  {DST} {after_size/1024:.1f}KB ({new_w}x{new_h})")
    print(f"under 180KB target: {after_size < 180*1024}")

if __name__ == "__main__":
    main()
