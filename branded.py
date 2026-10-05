#!/usr/bin/env python3
"""Scripted 3:4 product infographics. Product values are supplied through JSON."""
import argparse
import json
from pathlib import Path
import math
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from build import cutout, paste_fit, ROOT

W,H=1200,1600
VIOLET='#7C45F5'
LIME='#D4EB3B'
def find_font(bold=False):
    names = ['DejaVuSans-Bold.ttf', 'arialbd.ttf'] if bold else ['DejaVuSans.ttf', 'arial.ttf']
    folders = [ROOT/'assets/fonts', Path('C:/Windows/Fonts'), Path('/usr/share/fonts/truetype/dejavu')]
    for folder in folders:
        for name in names:
            if (folder/name).is_file():
                return str(folder/name)
    raise FileNotFoundError('Install DejaVu Sans or Arial, or place DejaVuSans fonts in assets/fonts')

FONT=find_font()
BOLD=find_font(True)

def text(draw,xy,s,size,color,bold=False,maxwidth=None):
    font=ImageFont.truetype(BOLD if bold else FONT,size)
    if maxwidth:
        while draw.textbbox((0,0),s,font=font)[2]>maxwidth and size>14:
            size-=1
            font=ImageFont.truetype(BOLD if bold else FONT,size)
    draw.text(xy,s,font=font,fill=color,stroke_width=0)

def flake(draw,x,y,r,color,width=4):
    for i in range(6):
        a=math.pi*i/3
        ex,ey=x+r*math.cos(a),y+r*math.sin(a)
        draw.line((x,y,ex,ey),fill=color,width=width)
        for sign in (-1,1):
            bx,by=x+r*.62*math.cos(a),y+r*.62*math.sin(a)
            aa=a+sign*math.pi/3
            draw.line((bx,by,bx-r*.23*math.cos(aa),by-r*.23*math.sin(aa)),fill=color,width=width)

def shadow(im,box):
    layer=Image.new('RGBA',im.size)
    ImageDraw.Draw(layer).ellipse(box,fill=(0,0,0,90))
    im.alpha_composite(layer.filter(ImageFilter.GaussianBlur(25)))

def backdrop(dark):
    yy,xx=np.mgrid[0:H,0:W]
    t=np.clip(1-np.sqrt(((xx-865)/1250)**2+((yy-850)/1700)**2),0,1)[:,:,None]
    a=np.array([26,24,38] if dark else [244,244,249])
    b=np.array([79,47,135] if dark else [225,219,242])
    return Image.fromarray((a+(b-a)*t).astype('uint8')).convert('RGBA')

def sizebar(d,box,data,dark=False):
    x,y,x2,y2=box
    d.rounded_rectangle(box,radius=26,fill=LIME if dark else '#FFFFFF')
    split=x2-220
    d.rounded_rectangle((split,y,x2,y2),radius=26,fill=VIOLET)
    d.rectangle((split,y,split+30,y2),fill=VIOLET)
    text(d,(x+30,y+21),data['size'],76,'#25252D',True,maxwidth=split-x-60)
    text(d,(split+28,y+21),data['diameter'],76,'#FFFFFF',True,maxwidth=166)

def make(tyre,data,variant):
    dark=variant=='contrast'
    im=backdrop(dark)
    d=ImageDraw.Draw(im)
    logo=Image.open(ROOT/'assets'/('logo-white.png' if dark else 'logo.png')).convert('RGBA')
    paste_fit(im,logo,(790,60,342,80))
    d.rounded_rectangle((68,77,246,137),radius=16,fill=LIME)
    flake(d,97,107,17,'#25252D',3)
    text(d,(126,86),'ЗИМА',25,'#25252D',True)
    if dark:
        text(d,(62,179),data['title'],94,'#FFFFFF',True,maxwidth=1060)
        text(d,(67,297),data['subtitle'],38,'#D3C5EE',maxwidth=1060)
        sizebar(d,(68,383,1132,511),data,True)
        # Large subdued curves echo the tyre sidewall and speedometer.
        d.arc((330,520,1320,1610),210,335,fill='#7250A9',width=4)
        d.arc((390,570,1260,1550),205,330,fill='#7250A9',width=2)
        d.polygon([(0,1490),(1200,1310),(1200,1600),(0,1600)],fill='#201D2E')
        shadow(im,(462,1413,1110,1494))
        paste_fit(im,tyre,(423,548,755,944))
        d=ImageDraw.Draw(im)
        for y,key,label in [(656,'load','ИНДЕКС НАГРУЗКИ'),(858,'speed','ИНДЕКС СКОРОСТИ')]:
            d.rounded_rectangle((68,y,373,y+175),radius=24,fill='#393046')
            d.rounded_rectangle((68,y,77,y+175),radius=4,fill=LIME)
            text(d,(94,y+13),data[key],70,'#FFFFFF',True)
            text(d,(95,y+117),label,19,'#DDD3EA',True,maxwidth=254)
        d.rounded_rectangle((68,1097,373,1287),radius=24,fill=VIOLET)
        flake(d,109,1136,23,LIME,4)
        text(d,(94,1180),'ЗИМНИЕ',30,'#FFFFFF',True)
        text(d,(94,1223),'ШИНЫ',30,'#FFFFFF',True)
        text(d,(68,1514),'ИНТЕРНЕТ-МАГАЗИН ШИН',25,'#C7BDDB',True)
    else:
        text(d,(62,179),data['title'],94,'#25252D',True,maxwidth=1060)
        text(d,(68,297),data['subtitle'],38,'#696374',maxwidth=1060)
        d.rounded_rectangle((44,383,1156,532),radius=32,fill=VIOLET)
        sizebar(d,(60,399,1140,517),data)
        d.ellipse((225,565,1190,1520),fill='#E5DDF6')
        d.arc((152,592,1180,1520),175,295,fill=VIOLET,width=10)
        d.arc((202,634,1130,1475),175,291,fill=LIME,width=18)
        shadow(im,(321,1354,911,1430))
        paste_fit(im,tyre,(220,557,810,854))
        d=ImageDraw.Draw(im)
        d.rounded_rectangle((52,1452,1148,1552),radius=22,fill='#25252D')
        text(d,(80,1474),data['load'],46,LIME,True)
        text(d,(166,1468),'ИНДЕКС',20,'#FFFFFF',True)
        text(d,(166,1500),'НАГРУЗКИ',20,'#FFFFFF',True)
        d.line((414,1470,414,1534),fill='#635D6C',width=2)
        text(d,(447,1474),data['speed'],46,LIME,True)
        text(d,(509,1468),'ИНДЕКС',20,'#FFFFFF',True)
        text(d,(509,1500),'СКОРОСТИ',20,'#FFFFFF',True)
        d.line((755,1470,755,1534),fill='#635D6C',width=2)
        flake(d,800,1502,23,LIME,3)
        text(d,(843,1483),'ЗИМНИЕ ШИНЫ',26,'#FFFFFF',True,maxwidth=271)
    return im.convert('RGB')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('input',type=Path)
    p.add_argument('--data',type=Path,required=True,help='JSON: one object, or filename → object for a folder')
    p.add_argument('--output',type=Path,default=ROOT/'branded-output')
    p.add_argument('--variant',choices=['all','contrast','light'],default='all')
    args=p.parse_args()
    data=json.loads(args.data.read_text(encoding='utf-8'))
    sources=[args.input] if args.input.is_file() else sorted(x for x in args.input.iterdir() if x.suffix.lower() in {'.png','.jpg','.jpeg','.webp'})
    args.output.mkdir(parents=True,exist_ok=True)
    for source in sources:
        row=data if 'title' in data else data[source.name]
        for key in ('title','subtitle','size','diameter','load','speed'):
            if not str(row.get(key,'')):
                raise ValueError(f'Missing {key} for {source.name}')
        tyre=cutout(source)
        tyre.save(args.output/f'{source.stem}-transparent.png')
        for variant in (['contrast','light'] if args.variant=='all' else [args.variant]):
            make(tyre,row,variant).save(args.output/f'{source.stem}-{variant}.png')
        print(f'Built {source.name}')

if __name__=='__main__': main()
