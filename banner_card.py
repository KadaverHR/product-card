#!/usr/bin/env python3
"""Brand/model product cards using a reusable generated paint background."""
import argparse
import json
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
from build import ROOT,cutout,paste_fit,finish_tyre
from branded import text,BOLD
from card_output import save_card

W,H=1200,1600
LIME='#D4EB3B'
PURPLE='#7C45F5'

def compose(tyre,data,background,theme,enhance=True):
    dark=theme=='dark'
    bg='#2B2633' if dark else '#F7F7FA'
    fg='#FFFFFF' if dark else '#25252D'
    secondary='#C3BCCB' if dark else '#66616E'
    im=Image.new('RGBA',(W,H),bg)
    paint=Image.open(background).convert('RGBA').resize((W,H),Image.Resampling.LANCZOS)
    im.alpha_composite(paint)
    if dark:
        # A subtle violet lift separates black rubber from the painted backdrop.
        im.alpha_composite(Image.new('RGBA',(W,H),(104,78,142,24)))
    logo=Image.open(ROOT/'assets'/('logo-white.png' if dark else 'logo.png')).convert('RGBA')
    paste_fit(im,logo,(776,50,360,86))
    d=ImageDraw.Draw(im)
    season_label=data.get('season_label','ЗИМНИЕ ШИНЫ')
    season_size=34
    season_font=ImageFont.truetype(BOLD,season_size)
    while d.textlength(season_label,font=season_font)>650 and season_size>14:
        season_size-=1
        season_font=ImageFont.truetype(BOLD,season_size)
    # Measure rendered glyph pixels, rather than the font's metric bounding box.
    season_mask=Image.new('L',(700,80),0)
    ImageDraw.Draw(season_mask).text((0,0),season_label,font=season_font,fill=255,anchor='lt')
    season_bounds=season_mask.getbbox()
    if season_bounds:
        d.rounded_rectangle((64,78+season_bounds[1],72,78+season_bounds[3]-1),radius=2,fill=PURPLE)
    d.text((88,78),season_label,font=season_font,fill=fg,anchor='lt')
    text(d,(60,175),data['brand'].upper(),94,fg,True,maxwidth=1068)
    text(d,(64,288),data['model'],62,fg,maxwidth=1068)
    # Keep the diameter badge beside the size, with a shared text baseline.
    font_size=128
    while True:
        font=ImageFont.truetype(BOLD,font_size)
        size_width=d.textlength(data['size'],font=font)
        diameter_width=d.textlength(data['diameter'],font=font)
        if size_width+24+diameter_width+48 <= 1068 or font_size <= 14:
            break
        font_size-=1
    badge_x=58+size_width+24
    d.rounded_rectangle((badge_x,386,badge_x+diameter_width+48,532),radius=24,fill=LIME)
    d.text((58,418),data['size'],font=font,fill=fg,anchor='lt')
    d.text((badge_x+24,418),data['diameter'],font=font,fill='#25252D',anchor='lt')
    # Deliberately oversized real source: bottom and right are cropped by canvas.
    scale=1390/tyre.height
    enlarged=tyre.resize((round(tyre.width*scale),1390),Image.Resampling.LANCZOS)
    if enhance:
        enlarged=finish_tyre(enlarged)
    im.alpha_composite(enlarged,(408,610))
    d=ImageDraw.Draw(im)
    # Opaque quiet information field keeps captions legible on the paint.
    panel_fill='#35313F' if dark else '#FFFFFF'
    panel_outline='#62576F' if dark else '#D4C8E8'
    for y,key,label1,label2 in [(775,'load','ИНДЕКС','НАГРУЗКИ'),(1030,'speed','ИНДЕКС','СКОРОСТИ')]:
        d.rounded_rectangle((52,y-24,345,y+198),radius=23,fill=panel_fill,outline=panel_outline,width=3)
        d.rounded_rectangle((79,y+6,86,y+174),radius=3,fill=LIME if dark else PURPLE)
        text(d,(107,y-4),data[key],104,fg,True,maxwidth=202)
        text(d,(109,y+119),label1,25,secondary,True,maxwidth=212)
        text(d,(109,y+154),label2,25,secondary,True,maxwidth=212)
    return im.convert('RGB')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('input',type=Path)
    p.add_argument('--data',type=Path,required=True)
    p.add_argument('--background',type=Path,default=ROOT/'assets/light-paint-background-v8.png')
    p.add_argument('--dark-background',type=Path,default=ROOT/'assets/dark-paint-background-v7.png')
    p.add_argument('--output',type=Path,default=ROOT/'banner-output')
    p.add_argument('--theme',choices=['all','dark','light'],default='all')
    p.add_argument('--raw-photo',action='store_true',help='Disable tyre tone and sharpness preset')
    args=p.parse_args()
    data=json.loads(args.data.read_text(encoding='utf-8'))
    sources=[args.input] if args.input.is_file() else sorted(x for x in args.input.iterdir() if x.suffix.lower() in {'.png','.jpg','.jpeg','.webp'})
    if not sources: p.error('No images found')
    args.output.mkdir(parents=True,exist_ok=True)
    for source in sources:
        row=data if 'brand' in data else data[source.name]
        for key in ['brand','model','size','diameter','load','speed']:
            if not str(row.get(key,'')): raise ValueError(f'Missing {key}: {source.name}')
        tyre=cutout(source)
        for theme in (['dark','light'] if args.theme=='all' else [args.theme]):
            background=args.dark_background if theme=='dark' else args.background
            save_card(compose(tyre,row,background,theme,enhance=not args.raw_photo),args.output/f'{source.stem}-{theme}.jpg')
        print(f'Built {source.name}')

if __name__=='__main__': main()
