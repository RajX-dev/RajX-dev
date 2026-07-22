import os
import sys
import numpy as np
import cv2
from PIL import Image
from rembg import remove

RAMP = " .`:-=+*cs#%@"
COLS = 50
CLAHE_CLIP = 2.2
GAMMA = 1.35
CROP_BOTTOM = 0.0

CHAR_W = 6.6
FONT_SIZE = 11.0
LINE_H = 13.7
ROW_DELAY = 0.09
PAD = 15

def prep(path):
    src = Image.open(path).convert("RGBA")
    cut = remove(src)
    alpha = np.array(cut.split()[-1])
    white = Image.new("RGBA", cut.size, (255, 255, 255, 255))
    gray = np.array(Image.alpha_composite(white, cut).convert("L"))
    gray = cv2.bilateralFilter(gray, 11, 50, 50)
    gray = cv2.createCLAHE(clipLimit=CLAHE_CLIP, tileGridSize=(8, 8)).apply(gray)
    gray[alpha < 20] = 255
    return Image.fromarray(gray)

def to_lines(img, cols=COLS, gamma=GAMMA):
    w, h = img.size
    if CROP_BOTTOM:
        img = img.crop((0, 0, w, int(h * (1 - CROP_BOTTOM))))
        w, h = img.size
    rows = int(cols * (h / w) * 0.48)
    img = img.resize((cols, rows), Image.LANCZOS)
    px = list(img.getdata())
    n = len(RAMP)
    out = []
    for r in range(rows):
        line = "".join(
            RAMP[min(n - 1, int((1 - px[r * cols + c] / 255.0) ** gamma * n))]
            for c in range(cols)
        )
        out.append(line.rstrip())
    while out and not out[0].strip():
        out.pop(0)
    while out and not out[-1].strip():
        out.pop()
    return out


SVG_TEMPLATE = """<?xml version='1.0' encoding='UTF-8'?>
<svg xmlns="http://www.w3.org/2000/svg" font-family="ConsolasFallback,Consolas,monospace" width="985px" height="530px" font-size="16px">
<style>
@font-face {{
src: local('Consolas'), local('Consolas Bold');
font-family: 'ConsolasFallback';
font-display: swap;
-webkit-size-adjust: 109%;
size-adjust: 109%;
}}
.key {{fill: {key_color};}}
.value {{fill: {val_color};}}
.addColor {{fill: #3fb950;}}
.delColor {{fill: #f85149;}}
.cc {{fill: #616e7f;}}
.a {{fill: {ascii_color};}}
text, tspan {{white-space: pre;}}
</style>
<rect width="985px" height="530px" fill="{bg_color}" rx="15"/>

{ascii_nodes}

<text x="355" y="30" fill="{val_color}" class="stats">
<tspan x="360" y="50" class="value" font-weight="bold" font-size="16">raj@laptop</tspan>
<tspan x="360" y="80" class="key">OS</tspan><tspan x="480" y="80" class="value">Windows 11, Linux</tspan>
<tspan x="360" y="100" class="key">Uptime</tspan><tspan x="480" y="100" class="value" id="age_data">21 years, 3 months, 4 days</tspan>
<tspan x="360" y="120" class="key">Host</tspan><tspan x="480" y="120" class="value">Delhi Technological University</tspan>
<tspan x="360" y="140" class="key">Kernel</tspan><tspan x="480" y="140" class="value">Structural Code Intelligence</tspan>
<tspan x="360" y="160" class="key">IDE</tspan><tspan x="480" y="160" class="value">Cursor, VSCode</tspan>
<tspan x="360" y="200" class="key">Languages</tspan><tspan x="480" y="200" class="value">Python, C++, JavaScript</tspan>
<tspan x="360" y="220" class="key">Web Dev</tspan><tspan x="480" y="220" class="value">SQL, HTML, CSS</tspan>
<tspan x="360" y="260" class="key">Current Work</tspan><tspan x="480" y="260" class="value">Founder @ N3MO (n3mo.shop)</tspan>
<tspan x="360" y="280" class="key">Focus</tspan><tspan x="480" y="280" class="value">Structural Code Intelligence</tspan>
<tspan x="360" y="300" class="key">Tech Stack</tspan><tspan x="480" y="300" class="value">Python, PostgreSQL, Tree-sitter</tspan>
<tspan x="360" y="340" class="key">Email</tspan><tspan x="480" y="340" class="value">sraj4090ti@gmail.com</tspan>
<tspan x="360" y="360" class="key">LinkedIn</tspan><tspan x="480" y="360" class="value">raj-shekhar349</tspan>
<tspan x="360" y="400" class="value" font-weight="bold">GitHub Stats</tspan>
<tspan x="360" y="420" class="key">Repos</tspan><tspan x="480" y="420" class="value" id="repo_data">42</tspan>
<tspan x="360" y="440" class="key">Stars</tspan><tspan x="480" y="440" class="value" id="star_data">150</tspan>
<tspan x="360" y="460" class="key">Commits</tspan><tspan x="480" y="460" class="value" id="commit_data">1,234</tspan>
<tspan x="360" y="480" class="key">Followers</tspan><tspan x="480" y="480" class="value" id="follower_data">50</tspan>
<tspan x="360" y="500" class="key">Total LOC</tspan><tspan x="480" y="500" class="value" id="loc_data">50,000</tspan><tspan class="value"> (</tspan><tspan class="addColor" id="loc_add">75,000++</tspan><tspan class="value">, </tspan><tspan class="delColor" id="loc_del">25,000--</tspan><tspan class="value">)</tspan>
</text>
</svg>
"""

def generate_svgs():
    try:
        lines = to_lines(prep("raj.png"))
    except Exception as e:
        print("Error processing raj.png:", e)
        return

    ascii_nodes = []
    
    # We want to center the ascii art vertically if it has fewer lines
    max_lines = 45
    start_y = PAD
    if len(lines) < max_lines:
        start_y += (max_lines - len(lines)) * LINE_H / 2

    for i, line in enumerate(lines):
        y = start_y + i * LINE_H
        begin = f"{i * ROW_DELAY:.2f}s"
        end = f"{(i + 1) * ROW_DELAY:.2f}s"
        w = max(len(line), 1) * CHAR_W
        safe = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        
        ascii_nodes.append(
            f'<clipPath id="c{i}"><rect x="{PAD}" y="{y}" height="{LINE_H}" width="0">'
            f'<animate attributeName="width" from="0" to="{w:.1f}" '
            f'begin="{begin}" dur="{ROW_DELAY}s" fill="freeze"/></rect></clipPath>'
        )
        ascii_nodes.append(
            f'<g clip-path="url(#c{i})"><text xml:space="preserve" x="{PAD}" '
            f'y="{y + 7.5:.1f}" class="a" font-size="{FONT_SIZE}">{safe}</text></g>'
        )
        ascii_nodes.append(
            f'<rect y="{y + 1}" width="4" height="{LINE_H - 1}" class="a" opacity="0">'
            f'<animate attributeName="x" from="{PAD}" to="{PAD + w:.1f}" '
            f'begin="{begin}" dur="{ROW_DELAY}s" fill="freeze"/>'
            f'<set attributeName="opacity" to="0.8" begin="{begin}"/>'
            f'<set attributeName="opacity" to="0" begin="{end}"/></rect>'
        )
        
    ascii_str = '\\n'.join(ascii_nodes)

    dark_content = SVG_TEMPLATE.format(
        key_color="#ffa657",
        val_color="#a5d6ff",
        bg_color="#161b22",
        ascii_color="#c9d1d9",
        ascii_nodes=ascii_str
    )
    
    light_content = SVG_TEMPLATE.format(
        key_color="#b05200",
        val_color="#0969da",
        bg_color="#ffffff",
        ascii_color="#24292f",
        ascii_nodes=ascii_str
    )
    
    with open("dark_mode.svg", "w") as f:
        f.write(dark_content)
        
    with open("light_mode.svg", "w") as f:
        f.write(light_content)

if __name__ == "__main__":
    generate_svgs()
    print("Generated animated dark_mode.svg and light_mode.svg from raj.png")
