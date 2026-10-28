"""Truck-card renderer with optional specification rows and shared passenger fonts."""
import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from branded import FONT, BOLD
from build import ROOT, cutout, paste_fit, finish_tyre
from card_output import save_card
from truck_sizes import split_truck_size

BACKGROUND = ROOT/'assets/truck-tread-background-v1.png'
LIME, PURPLE = '#d7f329', '#7c45f5'


def compose_truck(tyre, data, background=BACKGROUND, enhance=True):
    image = Image.open(background).convert('RGBA').resize((1200,1600), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(image)
    def label(x,y,value,size,bold=False,fill='white',maxwidth=None):
        value = str(value)
        font = ImageFont.truetype(BOLD if bold else FONT,size)
        while maxwidth and draw.textlength(value,font=font)>maxwidth and size>14:
            size -= 1
            font = ImageFont.truetype(BOLD if bold else FONT,size)
        draw.text((x,y),value,font=font,fill=fill,anchor='lt')
        return draw.textbbox((x,y),value,font=font,anchor='lt')
    draw.rounded_rectangle((72,146,82,182),radius=3,fill=PURPLE)
    label(112,146,'ГРУЗОВЫЕ ШИНЫ',38,True,maxwidth=610)
    paste_fit(image,Image.open(ROOT/'assets/logo-white.png').convert('RGBA'),(776,110,360,86))
    label(64,278,data['brand'].upper(),94,True,maxwidth=1068)
    label(68,380,data['model'],62,maxwidth=1068)
    parsed = split_truck_size(data['size'],data.get('diameter'))
    size, diameter = parsed.size, parsed.diameter
    if diameter.startswith(('-', '–', '—')):
        size, diameter = size+diameter[0], diameter[1:]
    font_size = 128
    while True:
        font = ImageFont.truetype(BOLD,font_size)
        sw, dw = draw.textlength(size,font=font), draw.textlength(diameter,font=font)
        if sw+(dw+72 if diameter else 0)<=1068 or font_size<=14:
            break
        font_size -= 1
    bx = 58+sw+24
    if diameter:
        draw.rounded_rectangle((bx,433,bx+dw+48,579),radius=24,fill=PURPLE)
    draw.text((58,465),size,font=font,fill='white',anchor='lt')
    if diameter:
        draw.text((bx+24,465),diameter,font=font,fill='white',anchor='lt')
    tyre = tyre.convert('RGBA')
    tyre = tyre.resize((round(tyre.width*1390/tyre.height),1390),Image.Resampling.LANCZOS)
    if enhance:
        tyre = finish_tyre(tyre)
    image.alpha_composite(tyre,(480,610))
    draw = ImageDraw.Draw(image)
    rows = [(caption,data.get(key)) for key,caption in (
        ('application','ПРИМЕНЯЕМОСТЬ'),('ply_rating','СЛОЙНОСТЬ'),
        ('construction','КОНСТРУКЦИЯ'),('equipment','КОМПЛЕКТАЦИЯ'))
        if data.get(key) is not None and str(data.get(key)).strip()]
    y = 740  # Preserve the gap while moving all typography below Ozon overlays.
    for caption,value in rows:
        draw.rounded_rectangle((72,y-3,80,y+83),radius=3,fill=LIME)
        label(104,y,caption,28,True,fill='#c3bccb',maxwidth=326)
        label(104,y+39,value,56,maxwidth=334)
        draw.line((72,y+112,435,y+112),fill='#62576f',width=1)
        y += 136
    indices = [(data.get(key),caption) for key,caption in (
        ('load_index','НАГРУЗКА'),('speed_index','СКОРОСТЬ')) if data.get(key)]
    index_labels = []
    caption_y = y+96
    for index,(value,caption) in enumerate(indices):
        x = 96+index*204
        bounds = label(x,y+8,value,104,True,fill=LIME,maxwidth=170)
        caption_y = max(caption_y,bounds[3]+18)
        index_labels.append((x+2,caption))
    # Keep captions aligned below the actual glyphs, including descenders in J.
    for x,caption in index_labels:
        label(x,caption_y,caption,25,True,fill='#c3bccb',maxwidth=165)
    return image.convert('RGB')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input',type=Path,help='Original tyre photograph, not an already designed card')
    parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--background',type=Path,default=BACKGROUND)
    args = parser.parse_args()
    data = json.loads(args.data.read_text(encoding='utf-8'))
    for key in ('brand','model','size'):
        if not str(data.get(key) or '').strip():
            parser.error(f'Missing {key}')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    save_card(compose_truck(cutout(args.input),data,args.background),args.output)
    print(args.output.resolve())


if __name__ == '__main__':
    main()
