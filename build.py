#!/usr/bin/env python3
"""Remove border-connected white background and build repeatable product images."""
import argparse
from collections import deque
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent
SIZE = 1200
PURPLE = '#7C45F5'
LIME = '#D4EB3B'

def cutout(path, threshold=235):
    im = Image.open(path).convert('RGBA')
    data = np.array(im)
    rgb = data[:, :, :3].astype(float)
    lo, hi = rgb.min(2), rgb.max(2)
    candidate = ((lo >= threshold) & (hi-lo <= 18)) | (data[:, :, 3] == 0)
    h, w = candidate.shape
    background = np.zeros((h, w), bool)
    queue = deque()
    def add(y, x):
        if candidate[y, x] and not background[y, x]:
            background[y, x] = True
            queue.append((y, x))
    for x in range(w):
        add(0, x); add(h-1, x)
    for y in range(h):
        add(y, 0); add(y, w-1)
    while queue:
        y, x = queue.popleft()
        for yy, xx in ((y-1,x),(y+1,x),(y,x-1),(y,x+1)):
            if 0 <= yy < h and 0 <= xx < w:
                add(yy, xx)
    # JPEG/resizing can spread the white matte over several boundary pixels.
    # Restrict cleanup to the outer edge; preserve existing semitransparent pixels.
    fringe = max(2, min(4, round(max(w, h)/1200)))
    adjacent = np.array(Image.fromarray(background.astype('uint8')*255).filter(ImageFilter.MaxFilter(2*fringe+1))) > 0
    edge = adjacent & ~background & (lo > 70) & (hi-lo < 32) & (data[:, :, 3] == 255)
    alpha = data[:, :, 3].astype(float)/255
    alpha[background] = 0
    matte_alpha = np.clip((255-lo)/210, 0.01, 1)
    rgb[edge] = np.clip((rgb[edge]-255*(1-matte_alpha[edge,None]))/matte_alpha[edge,None],0,255)
    alpha[edge] *= matte_alpha[edge]
    data[:, :, :3] = rgb.astype('uint8')
    data[:, :, 3] = np.rint(alpha*255).astype('uint8')
    result = Image.fromarray(data)
    bounds = result.getbbox()
    if not bounds:
        raise ValueError(f'No foreground found: {path}')
    return result.crop(bounds)

def paste_fit(canvas, obj, box):
    x, y, w, h = box
    scale = min(w/obj.width, h/obj.height)
    fitted = obj.resize((max(1,round(obj.width*scale)), max(1,round(obj.height*scale))), Image.Resampling.LANCZOS)
    canvas.alpha_composite(fitted, (x+(w-fitted.width)//2,y+(h-fitted.height)//2))


def finish_tyre(image):
    """Apply a restrained tyre preset inside the silhouette, preserving alpha."""
    image = image.convert('RGBA')
    pixels = np.array(image)
    rgb = pixels[:, :, :3].astype(float)
    opaque = pixels[:, :, 3] >= 250
    if not opaque.any():
        return image.copy()
    luminance = rgb @ np.array([0.2126, 0.7152, 0.0722])
    low, high = np.percentile(luminance[opaque], [10, 90])
    # Bounded corrections keep the original rubber texture and highlights.
    gain = np.clip(140/max(high-low, 1), 0.9, 1.15)
    offset = np.clip(35-low*gain, -10, 10)
    toned = Image.fromarray(np.clip(rgb*gain+offset, 0, 255).astype('uint8'))
    sharpened = toned.filter(ImageFilter.UnsharpMask(radius=1.4, percent=90, threshold=3))
    # Erode the mask to avoid sharpening white-matte remnants at the boundary.
    mask = image.getchannel('A').filter(ImageFilter.MinFilter(5)).filter(ImageFilter.GaussianBlur(0.8))
    mask = Image.fromarray(np.minimum(np.array(mask), pixels[:, :, 3]))
    result = Image.composite(sharpened, image.convert('RGB'), mask).convert('RGBA')
    result.putalpha(image.getchannel('A'))
    return result

def card(tyre, style):
    bg = {'light':'#FFFFFF','purple':PURPLE,'dark':'#25252D'}[style]
    im = Image.new('RGBA', (SIZE,SIZE), bg)
    d = ImageDraw.Draw(im)
    logo = Image.open(ROOT/'assets'/('logo.png' if style=='light' else 'logo-white.png')).convert('RGBA')
    if style == 'light':
        d.rounded_rectangle((64,68,74,142), radius=5, fill=PURPLE)
        d.rounded_rectangle((64,1080,200,1090), radius=5, fill=LIME)
        paste_fit(im,logo,(814,68,322,74))
        paste_fit(im,tyre,(170,186,860,856))
    elif style == 'purple':
        paste_fit(im,logo,(814,64,322,74))
        d.rounded_rectangle((54,182,1146,1146), radius=48, fill='#F8F8FA')
        d.rounded_rectangle((80,208,242,222), radius=7, fill=LIME)
        paste_fit(im,tyre,(172,244,856,832))
    else:
        paste_fit(im,logo,(814,64,322,74))
        d.ellipse((145,182,1055,1118), fill='#ECECEF')
        d.rounded_rectangle((64,88,178,108),radius=10,fill=LIME)
        paste_fit(im,tyre,(192,232,816,824))
    return im.convert('RGB')

def preview(paths, out):
    sheet = Image.new('RGB',(1800,728),'#ECECF1')
    draw=ImageDraw.Draw(sheet)
    font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',23)
    labels=['01 / Светлый','02 / Фирменный фиолетовый','03 / Тёмный']
    for i,(path,label) in enumerate(zip(paths,labels)):
        tile=Image.open(path).resize((560,560),Image.Resampling.LANCZOS)
        x=20+i*600
        sheet.paste(tile,(x,88))
        draw.text((x,36),label,fill='#25252D',font=font)
    draw.text((20,678),'Один исходник · настоящее фото товара · сборка скриптом',fill='#676774',font=font)
    sheet.save(out)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('input',type=Path,help='Image or folder (non-recursive)')
    p.add_argument('--output',type=Path,default=ROOT/'output')
    p.add_argument('--style',choices=['all','light','purple','dark'],default='all')
    p.add_argument('--threshold',type=int,default=235)
    p.add_argument('--preview',action='store_true',help='Create comparison for a single source')
    args=p.parse_args()
    sources=([args.input] if args.input.is_file() else sorted(x for x in args.input.iterdir() if x.suffix.lower() in {'.png','.jpg','.jpeg','.webp'}))
    if not sources:
        p.error('No supported images found')
    args.output.mkdir(parents=True,exist_ok=True)
    styles=['light','purple','dark'] if args.style=='all' else [args.style]
    for source in sources:
        tyre=cutout(source,args.threshold)
        tyre.save(args.output/f'{source.name}-cutout.png')
        paths=[]
        for style in styles:
            target=args.output/f'{source.name}-{style}.png'
            card(tyre,style).save(target)
            paths.append(target)
        if args.preview and len(sources)==1 and len(paths)==3:
            preview(paths,args.output/'preview.png')
        print(f'{source.name}: saved cutout and {len(styles)} designs')

if __name__=='__main__':
    main()
