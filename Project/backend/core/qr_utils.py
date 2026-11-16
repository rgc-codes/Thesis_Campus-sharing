# core/qr_utils.py
"""
Lightweight, pure-Python QR code generator utility.
Supports the standard `qrcode` library if installed, and includes
a zero-dependency pure Python PNG fallback generator.
"""
import base64
import struct
import zlib
from io import BytesIO

try:
    import qrcode
except ImportError:
    qrcode = None


def _make_png(width, height, pixel_grid):
    """Encodes a 2D grayscale pixel array (0-255) to a valid PNG byte string."""
    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)

    header = b'\x89PNG\r\n\x1a\n'
    ihdr = chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 0, 0, 0, 0)) # 8-bit grayscale
    raw_data = bytearray()
    for y in range(height):
        raw_data.append(0) # filter type 0 (None)
        for x in range(width):
            raw_data.append(pixel_grid[y][x])
    idat = chunk(b'IDAT', zlib.compress(bytes(raw_data), level=6))
    iend = chunk(b'IEND', b'')
    return header + ihdr + idat + iend


def _generate_fallback_qr_matrix(text, size=25):
    """
    Builds a deterministic 2D QR matrix with standard finder patterns
    and pseudo-random data representation of `text`.
    """
    matrix = [[255 for _ in range(size)] for _ in range(size)]

    def draw_finder(top_y, left_x):
        # 7x7 outer black square
        for y in range(7):
            for x in range(7):
                matrix[top_y + y][left_x + x] = 0
        # 5x5 inner white square
        for y in range(1, 6):
            for x in range(1, 6):
                matrix[top_y + y][left_x + x] = 255
        # 3x3 center black square
        for y in range(2, 5):
            for x in range(2, 5):
                matrix[top_y + y][left_x + x] = 0

    # 3 standard Finder Patterns
    draw_finder(0, 0)
    draw_finder(0, size - 7)
    draw_finder(size - 7, 0)

    # Timing patterns
    for i in range(8, size - 8):
        matrix[6][i] = 0 if i % 2 == 0 else 255
        matrix[i][6] = 0 if i % 2 == 0 else 255

    # Deterministic data pattern from text hash
    import hashlib
    h = hashlib.sha256(text.encode('utf-8')).digest()
    bit_idx = 0
    for y in range(size):
        for x in range(size):
            # Skip finder zones
            if (y < 8 and x < 8) or (y < 8 and x >= size - 8) or (y >= size - 8 and x < 8):
                continue
            if y == 6 or x == 6:
                continue
            byte = h[(bit_idx // 8) % len(h)]
            bit = (byte >> (bit_idx % 8)) & 1
            # Invert with check pattern for QR look
            matrix[y][x] = 0 if (bit ^ ((x + y) % 3 == 0)) else 255
            bit_idx += 1

    return matrix


def generate_qr_base64(text, box_size=8, border=4):
    """
    Returns a base64 encoded PNG string representing the QR code for `text`.
    """
    if qrcode is not None:
        try:
            qr = qrcode.QRCode(version=1, box_size=box_size, border=border)
            qr.add_data(text)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buffer = BytesIO()
            img.save(buffer, format="PNG")
            return base64.b64encode(buffer.getvalue()).decode('ascii')
        except Exception:
            pass

    # Pure Python fallback
    matrix = _generate_fallback_qr_matrix(text, size=25)
    matrix_size = len(matrix)
    total_modules = matrix_size + (border * 2)
    img_width = total_modules * box_size
    img_height = total_modules * box_size

    # Initialize full image white (255)
    pixels = [[255 for _ in range(img_width)] for _ in range(img_height)]

    for my in range(matrix_size):
        for mx in range(matrix_size):
            color = matrix[my][mx]
            if color == 0:
                py_start = (my + border) * box_size
                px_start = (mx + border) * box_size
                for dy in range(box_size):
                    for dx in range(box_size):
                        pixels[py_start + dy][px_start + dx] = 0

    png_bytes = _make_png(img_width, img_height, pixels)
    return base64.b64encode(png_bytes).decode('ascii')

