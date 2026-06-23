"""将 static/Images/icon.png 转换为 installer/icon.ico（多尺寸）"""
from pathlib import Path
from PIL import Image

src = Path(__file__).parent.parent / "static" / "Images" / "icon.png"
dst = Path(__file__).parent / "icon.ico"

img = Image.open(src).convert("RGBA")
sizes = [(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)]
icons = [img.resize(s, Image.LANCZOS) for s in sizes]
icons[0].save(dst, format="ICO", sizes=sizes, append_images=icons[1:])
print(f"已生成: {dst}")
