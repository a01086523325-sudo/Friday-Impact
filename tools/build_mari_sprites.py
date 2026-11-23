#!/usr/bin/env python3
"""Build Mari's Psych Engine assets from five transparent full-body PNGs.

Requires Pillow (python3 -m pip install Pillow). With the bundled full-body
sources at tools/mari_source/mari_{idle,left,down,up,right}.png and head-icon
sources at tools/mari_source/mari_icon_{normal,lose,win}.png, run:

    python3 tools/build_mari_sprites.py

--source-dir changes the input directory; --output-root redirects all output,
which is useful for testing. Existing Asuka assets in this mod are read only
and serve as the reference for frame sizes, baseline, character height and
JSON settings. Existing Mari output files are replaced after validation.

Every animation frame has a fixed, untrimmed canvas, so zero Psych animation
offsets are sufficient. A tiny, foot-anchored scale change produces movement
without making the character's feet slide vertically. Health-icon frames use
the separately supplied normal, losing, and winning head illustrations, in
that order, with transparent padding and a pixel-art-style nearest resize.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET

try:
    from PIL import Image, ImageFile
except ImportError:
    sys.exit('Pillow is required: python3 -m pip install Pillow')

ImageFile.LOAD_TRUNCATED_IMAGES = False
RESAMPLE = Image.Resampling.LANCZOS
MOD_ROOT = Path(__file__).resolve().parents[1]
SOURCE_NAMES = ('idle', 'left', 'down', 'up', 'right')
GAME_ANIMS = ('idle', 'singLEFT', 'singDOWN', 'singUP', 'singRIGHT')
# In the source, opacity below 8 consists mostly of background-removal noise.
ALPHA_CUTOFF = 8
FRAME_MARGIN = 8
ICON_SIZE = 150
ICON_MARGIN = 8
# The reference Rei icon occupies roughly 111 pixels across a 150-pixel cell.
ICON_ART_SIZE = 111
ICON_SOURCE_NAMES = ('normal', 'lose', 'win')


def read_source(path: Path) -> Image.Image:
    """Read an entire silhouette, discard near-transparent background noise."""
    with Image.open(path) as original:
        original.load()
        if 'A' not in original.getbands() and 'transparency' not in original.info:
            raise ValueError(f'{path}: PNG must have a transparency/alpha channel')
        im = original.convert('RGBA')
    alpha = im.getchannel('A')
    if alpha.getextrema()[0] != 0:
        raise ValueError(f'{path}: no fully transparent background detected')
    alpha = alpha.point(lambda a: a if a >= ALPHA_CUTOFF else 0)
    bbox = alpha.getbbox()
    if bbox is None:
        raise ValueError(f'{path}: image has no visible character')
    if bbox[0] == 0 or bbox[1] == 0 or bbox[2] == im.width or bbox[3] == im.height:
        raise ValueError(f'{path}: visible artwork touches a source edge and may already be clipped')
    hist = alpha.histogram()
    if sum(hist[64:]) < im.width * im.height * 0.005:
        raise ValueError(f'{path}: too little opaque character artwork')
    im.putalpha(alpha)
    return im.crop(bbox)


def reference_layout(xml_path: Path, png_path: Path) -> tuple[int, int, int, int]:
    """Return canvas width/height and reference idle silhouette height/baseline."""
    root = ET.parse(xml_path).getroot()
    frames = [e for e in root.findall('SubTexture') if e.attrib.get('name', '').startswith('idle')]
    if not frames:
        raise ValueError(f'{xml_path}: no reference idle frame')
    a = frames[0].attrib
    x, y, w, h = (int(a[k]) for k in ('x', 'y', 'width', 'height'))
    with Image.open(png_path) as image:
        if x < 0 or y < 0 or x + w > image.width or y + h > image.height:
            raise ValueError(f'{xml_path}: reference frame lies outside image')
        alpha = image.convert('RGBA').crop((x, y, x + w, y + h)).getchannel('A')
    bbox = alpha.point(lambda p: 255 if p >= ALPHA_CUTOFF else 0).getbbox()
    if not bbox:
        raise ValueError(f'{png_path}: reference idle frame is empty')
    return w, h, bbox[3] - bbox[1], bbox[3]


def common_height(poses: dict[str, Image.Image], names: tuple[str, ...],
                  width: int, reference_height: int) -> int:
    """Scale by full-silhouette height; use one height across all poses.

    If a wide pose would not fit, uniformly reduce the common height instead of
    clipping that pose or changing its aspect ratio independently.
    """
    max_aspect = max(poses[name].width / poses[name].height for name in names)
    height = min(reference_height, math.floor((width - 2 * FRAME_MARGIN) / max_aspect))
    if height < reference_height * 0.55:
        raise ValueError('A pose is too wide to fit without making every character frame unnaturally small; check input artwork')
    return height


def make_frame(pose: Image.Image, width: int, height: int, ground: int,
               base_height: int, factor: float) -> Image.Image:
    target_h = max(1, round(base_height * factor))
    target_w = max(1, round(pose.width * target_h / pose.height))
    if target_w > width - 2 * FRAME_MARGIN or target_h > ground - FRAME_MARGIN:
        raise ValueError(f'Sprite does not fit in {width}x{height} frame: {target_w}x{target_h}')
    sprite = pose.resize((target_w, target_h), RESAMPLE)
    # Account for an occasional completely transparent boundary after resampling.
    bbox = sprite.getchannel('A').getbbox()
    if bbox is None:
        raise ValueError('A resized animation frame is empty')
    sprite = sprite.crop(bbox)
    frame = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    x = (width - sprite.width) // 2
    y = ground - sprite.height
    if x < 1 or y < 1 or x + sprite.width >= width or y + sprite.height >= height:
        raise ValueError('Sprite is too close to the frame edge')
    # No mask: copying RGBA directly preserves partially transparent edge pixels.
    frame.paste(sprite, (x, y))
    return frame


def game_frames(poses: dict[str, Image.Image], layout: tuple[int, int, int, int]):
    w, h, reference_h, ground = layout
    base = common_height(poses, SOURCE_NAMES, w, reference_h)
    result = []
    # Four-beat up/down bounce, made with small uniform scales and fixed feet.
    for i, factor in enumerate((1.000, 0.992, 0.984, 0.992)):
        result.append((f'idle{i:04}', make_frame(poses['idle'], w, h, ground, base, factor)))
    for direction in ('LEFT', 'DOWN', 'UP', 'RIGHT'):
        pose = poses[direction.lower()]
        for i, factor in enumerate((1.000, 0.992)):
            result.append((f'sing{direction}{i:04}', make_frame(pose, w, h, ground, base, factor)))
    return result


def menu_frames(poses: dict[str, Image.Image], layout: tuple[int, int, int, int]):
    w, h, reference_h, ground = layout
    base = common_height(poses, ('idle', 'up'), w, reference_h)
    result = []
    # MenuCharacter uses 24 fps: one second of gentle idle, half a second confirm.
    for i in range(24):
        factor = 1.0 - 0.016 * (1.0 - math.cos(2.0 * math.pi * i / 24.0)) / 2.0
        result.append((f'idle{i:04}', make_frame(poses['idle'], w, h, ground, base, factor)))
    for i in range(12):
        factor = 1.0 - 0.012 * (1.0 - math.cos(2.0 * math.pi * i / 12.0)) / 2.0
        result.append((f'confirm{i:04}', make_frame(poses['up'], w, h, ground, base, factor)))
    return result


def make_atlas(frames: list[tuple[str, Image.Image]], cols: int, filename: str):
    if not frames:
        raise ValueError('No atlas frames')
    w, h = frames[0][1].size
    atlas = Image.new('RGBA', (cols * w, math.ceil(len(frames) / cols) * h), (0, 0, 0, 0))
    root = ET.Element('TextureAtlas', {'imagePath': filename})
    for index, (name, frame) in enumerate(frames):
        if frame.size != (w, h):
            raise ValueError(f'{name}: inconsistent frame dimensions')
        x, y = (index % cols) * w, (index // cols) * h
        atlas.paste(frame, (x, y))
        ET.SubElement(root, 'SubTexture', {
            'name': name, 'x': str(x), 'y': str(y), 'width': str(w), 'height': str(h),
        })
    ET.indent(root, space='  ')
    return atlas, root


def portrait(head: Image.Image) -> Image.Image:
    """Fit a complete supplied head into a padded, pixel-style icon tile."""
    # read_source has already removed near-transparent background noise and
    # cropped to the entire silhouette; do not crop off hair or accessories.
    scale = min(ICON_ART_SIZE / head.width, ICON_ART_SIZE / head.height)
    dims = (max(1, round(head.width * scale)), max(1, round(head.height * scale)))
    head = head.resize(dims, Image.Resampling.NEAREST)
    tile = Image.new('RGBA', (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    tile.paste(head, ((ICON_SIZE - head.width) // 2, (ICON_SIZE - head.height) // 2))
    return tile


def make_icons(source_dir: Path) -> Image.Image:
    strip = Image.new('RGBA', (ICON_SIZE * 3, ICON_SIZE), (0, 0, 0, 0))
    for i, name in enumerate(ICON_SOURCE_NAMES):
        head = read_source(source_dir / f'mari_icon_{name}.png')
        strip.paste(portrait(head), (i * ICON_SIZE, 0))
    return strip


def validate_atlas(png: Path, xml: Path, expected: list[tuple[str, Image.Image]],
                   counts: dict[str, int], ground: int):
    root = ET.parse(xml).getroot()
    if root.tag != 'TextureAtlas' or root.get('imagePath') != png.name:
        raise ValueError(f'{xml}: invalid TextureAtlas/imagePath')
    with Image.open(png) as im:
        im.load()
        atlas = im.convert('RGBA')
    nodes = root.findall('SubTexture')
    if len(nodes) != len(expected):
        raise ValueError(f'{xml}: wrong frame count')
    seen = set()
    rectangles = []
    for node, (expected_name, original) in zip(nodes, expected):
        a = node.attrib
        name = a.get('name')
        if name in seen or name != expected_name:
            raise ValueError(f'{xml}: duplicate or unexpected frame {name}')
        seen.add(name)
        x, y, w, h = (int(a[k]) for k in ('x', 'y', 'width', 'height'))
        if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > atlas.width or y + h > atlas.height:
            raise ValueError(f'{name}: XML frame lies outside {png.name}')
        if (w, h) != original.size:
            raise ValueError(f'{name}: XML dimensions differ from frame')
        if any(x < rx2 and x + w > rx1 and y < ry2 and y + h > ry1
               for rx1, ry1, rx2, ry2 in rectangles):
            raise ValueError(f'{name}: overlapping atlas rectangles')
        rectangles.append((x, y, x + w, y + h))
        actual = atlas.crop((x, y, x + w, y + h))
        if actual.tobytes() != original.tobytes():
            raise ValueError(f'{name}: XML coordinates do not identify the intended image')
        bbox = actual.getchannel('A').getbbox()
        if not bbox or bbox[0] <= 0 or bbox[1] <= 0 or bbox[2] >= w or bbox[3] >= h:
            raise ValueError(f'{name}: empty frame or clipped artwork')
        if bbox[3] != ground:
            raise ValueError(f'{name}: incorrect foot baseline {bbox[3]} (expected {ground})')
    for prefix, count in counts.items():
        matches = [n for n in seen if n.startswith(prefix)]
        if len(matches) != count:
            raise ValueError(f'{xml}: {prefix} has {len(matches)} frames, expected {count}')


def validate_json(stage: Path):
    game = json.loads((stage / 'characters/mari.json').read_text(encoding='utf-8'))
    menu = json.loads((stage / 'images/menucharacters/mari.json').read_text(encoding='utf-8'))
    if game['image'] != 'characters/mari' or game['healthicon'] != 'mari':
        raise ValueError('Game character image or healthicon path is incorrect')
    if [(a['anim'], a['name']) for a in game['animations']] != [(n, n) for n in GAME_ANIMS]:
        raise ValueError('Game animation names do not match XML prefixes')
    if any(a['indices'] != [] or a['offsets'] != [0, 0] for a in game['animations']):
        raise ValueError('Game animation indices or offsets are invalid')
    if (menu['image'], menu['idle_anim'], menu['confirm_anim']) != ('mari', 'idle', 'confirm'):
        raise ValueError('Menu JSON names do not match atlas prefixes')


def validate_icons(path: Path, expected: Image.Image):
    with Image.open(path) as im:
        im.load()
        if im.mode != 'RGBA':
            raise ValueError('Health icon must be an RGBA PNG')
        strip = im.convert('RGBA')
    if strip.size != (ICON_SIZE * 3, ICON_SIZE):
        raise ValueError('Health icon must be exactly 450x150')
    if strip.tobytes() != expected.tobytes():
        raise ValueError('Health icon pixels do not match the normal/lose/win source frames')
    for i in range(3):
        tile = strip.crop((i * ICON_SIZE, 0, (i + 1) * ICON_SIZE, ICON_SIZE))
        bbox = tile.getchannel('A').getbbox()
        if not bbox or bbox[0] < ICON_MARGIN or bbox[1] < ICON_MARGIN or bbox[2] > ICON_SIZE - ICON_MARGIN or bbox[3] > ICON_SIZE - ICON_MARGIN:
            raise ValueError(f'Icon frame {i} is empty, clipped, or lacks the required margin')


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def install_files(stage: Path, output_root: Path, paths: tuple[str, ...]):
    # Validate the complete staged set first; then replace only the seven Mari
    # files. Each individual replacement is atomic on its destination filesystem.
    for relative in paths:
        source = stage / relative
        dest = output_root / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(dir=dest.parent, prefix='.mari-', delete=False) as temp:
                temp_path = Path(temp.name)
                os.fchmod(temp.fileno(), 0o644)
                with source.open('rb') as src:
                    shutil.copyfileobj(src, temp)
            os.replace(temp_path, dest)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)


def build(source_dir: Path, output_root: Path):
    source_files = {name: source_dir / f'mari_{name}.png' for name in SOURCE_NAMES}
    missing = [str(p) for p in source_files.values() if not p.is_file()]
    if missing:
        raise FileNotFoundError('Missing input PNG(s); nothing was written:\n  ' + '\n  '.join(missing))
    poses = {name: read_source(path) for name, path in source_files.items()}
    reference_game = reference_layout(MOD_ROOT / 'images/characters/asuka.xml', MOD_ROOT / 'images/characters/asuka.png')
    reference_menu = reference_layout(MOD_ROOT / 'images/menucharacters/asuka.xml', MOD_ROOT / 'images/menucharacters/asuka.png')
    game = game_frames(poses, reference_game)
    menu = menu_frames(poses, reference_menu)
    game_atlas, game_xml = make_atlas(game, 5, 'mari.png')
    menu_atlas, menu_xml = make_atlas(menu, 6, 'mari.png')
    icons = make_icons(source_dir)

    reference = json.loads((MOD_ROOT / 'characters/asuka.json').read_text(encoding='utf-8'))
    character = copy.deepcopy(reference)
    original_anims = {a['anim']: a for a in character['animations']}
    character['animations'] = [copy.deepcopy(original_anims[name]) for name in GAME_ANIMS]
    for anim in character['animations']:
        anim.update(name=anim['anim'], offsets=[0, 0], indices=[])
    character.update(image='characters/mari', healthicon='mari', healthbar_colors=[235, 103, 163])
    menu_config = json.loads((MOD_ROOT / 'images/menucharacters/asuka.json').read_text(encoding='utf-8'))
    menu_config.update(image='mari', idle_anim='idle', confirm_anim='confirm')
    paths = ('images/characters/mari.png', 'images/characters/mari.xml', 'characters/mari.json',
             'images/menucharacters/mari.png', 'images/menucharacters/mari.xml',
             'images/menucharacters/mari.json', 'images/icons/icon-mari.png')
    with tempfile.TemporaryDirectory(prefix='build-mari-') as temp:
        stage = Path(temp)
        for relative in paths:
            (stage / relative).parent.mkdir(parents=True, exist_ok=True)
        game_atlas.save(stage / paths[0])
        ET.ElementTree(game_xml).write(stage / paths[1], encoding='utf-8', xml_declaration=True)
        save_json(stage / paths[2], character)
        menu_atlas.save(stage / paths[3])
        ET.ElementTree(menu_xml).write(stage / paths[4], encoding='utf-8', xml_declaration=True)
        save_json(stage / paths[5], menu_config)
        icons.save(stage / paths[6])
        validate_atlas(stage / paths[0], stage / paths[1], game,
                       {'idle': 4, 'singLEFT': 2, 'singDOWN': 2, 'singUP': 2, 'singRIGHT': 2}, reference_game[3])
        validate_atlas(stage / paths[3], stage / paths[4], menu,
                       {'idle': 24, 'confirm': 12}, reference_menu[3])
        validate_json(stage)
        validate_icons(stage / paths[6], icons)
        install_files(stage, output_root, paths)
    print(f'Built and validated 7 files in {output_root}')
    print(f'Game: {reference_game[0]}x{reference_game[1]}, 12 frames; '
          f'menu: {reference_menu[0]}x{reference_menu[1]}, 36 frames; icons: 450x150')
    for relative in paths:
        print('  ' + str(output_root / relative))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--source-dir', type=Path, default=MOD_ROOT / 'tools' / 'mari_source',
                        help='directory containing the five pose PNGs and mari_icon_{normal,lose,win}.png (default: tools/mari_source)')
    parser.add_argument('--output-root', type=Path, default=MOD_ROOT,
                        help='mod root where the seven Mari files will be placed (default: this mod)')
    args = parser.parse_args()
    try:
        build(args.source_dir.resolve(), args.output_root.resolve())
    except (OSError, ValueError, KeyError, ET.ParseError) as exc:
        print(f'Mari build failed: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
