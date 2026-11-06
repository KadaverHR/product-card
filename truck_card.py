"""Truck-card renderer with optional specification rows and shared passenger fonts."""
import argparse
import json
from pathlib import Path
import numpy as np

from PIL import Image, ImageDraw, ImageFont

from branded import FONT, BOLD
from build import ROOT, cutout, paste_fit, finish_tyre
from card_output import save_card
from truck_sizes import split_truck_size
from work_backgrounds import category_background

BACKGROUND = ROOT/'assets/truck-tread-background-v1.png'
LIME, PURPLE = '#d7f329', '#7c45f5'


def is_solid_tyre(data):
    features = str(data.get('features') or '').casefold().replace('ё', 'е')
    return data.get('product_type') == 'special' and 'цельнолит' in features


def compose_truck(tyre, data, background=BACKGROUND, enhance=True, theme=None):
    theme = theme or data.get('theme', 'dark')
    if theme not in ('dark', 'light'):
        raise ValueError('Theme must be dark or light')
    dark = theme == 'dark'
    foreground = 'white' if dark else '#25252d'
    secondary = '#c3bccb' if dark else '#66616e'
    product_type = data.get('product_type','truck')
    category_style = product_type in ('agricultural','special')
    category_design = product_type in ('agricultural','special') and Path(background) == BACKGROUND
    image = category_background(product_type,theme) if category_design else Image.open(background).convert('RGBA').resize((1200,1600), Image.Resampling.LANCZOS)
    if not dark and not category_design:
        # Retain the tread geometry and texture with a restrained light palette.
        pixels = np.asarray(image).astype(float)
        rgb = pixels[:,:,:3]
        r,g,b = rgb[:,:,0],rgb[:,:,1],rgb[:,:,2]
        purple = (b-r > 25) & (b-g > 35)
        lime = (g > r*.85) & (r > b*1.4)
        luminance = rgb.mean(axis=2)/255
        light = np.repeat((249-luminance*14)[:,:,None],3,axis=2)
        light[purple] = np.array([124,69,245])*.22 + light[purple]*.78
        light[lime] = np.array([215,243,41])*.65 + light[lime]*.35
        pixels[:,:,:3] = light
        image = Image.fromarray(pixels.astype('uint8'),'RGBA')
    # Place the tyre behind typography so it cannot cover the size badge.
    tyre = tyre.convert('RGBA')
    # Sparse solid-tyre cards use the product itself to fill the lower half.
    solid = is_solid_tyre(data)
    sparse_special = solid and not any(
        data.get(key) for key in ('application','ply_rating','construction','equipment','load_index','speed_index'))
    tyre_height = 1540 if sparse_special else 1390
    tyre = tyre.resize((round(tyre.width*tyre_height/tyre.height),tyre_height),Image.Resampling.LANCZOS)
    if enhance and not solid:
        tyre = finish_tyre(tyre)
    image.alpha_composite(tyre,(300,650) if sparse_special else (480,610))
    panel_color = tuple(int(v) for v in np.median(np.asarray(image.crop((20,200,200,600)))[:,:,:3].reshape(-1,3),axis=0)) if dark else 'white'
    draw = ImageDraw.Draw(image)
    def label(x,y,value,size,bold=False,fill=None,maxwidth=None):
        fill = fill or foreground
        value = str(value)
        font = ImageFont.truetype(BOLD if bold else FONT,size)
        while maxwidth and draw.textlength(value,font=font)>maxwidth and size>14:
            size -= 1
            font = ImageFont.truetype(BOLD if bold else FONT,size)
        draw.text((x,y),value,font=font,fill=fill,anchor='lt')
        return draw.textbbox((x,y),value,font=font,anchor='lt')
    header_y = 86
    draw.rounded_rectangle((72,header_y,82,header_y+36),radius=3,fill=LIME if dark and product_type=='agricultural' else PURPLE)
    product_label = {'truck':'ГРУЗОВЫЕ ШИНЫ', 'agricultural':'СЕЛЬХОЗ ШИНЫ',
                     'special':'ШИНЫ ДЛЯ СПЕЦТЕХНИКИ'}.get(data.get('product_type','truck'))
    if product_label is None:
        raise ValueError('Unsupported product type for work tyre card')
    label(112,header_y,product_label,38,True,maxwidth=610)
    paste_fit(image,Image.open(ROOT/'assets'/('logo-white.png' if dark else 'logo.png')).convert('RGBA'),(776,50,360,86))
    brand_offset = -40
    brand_bounds = label(64,318+brand_offset,data['brand'].upper(),94,True,maxwidth=1068)
    model_y = max(444+brand_offset,brand_bounds[3]+30)
    model_bounds = label(68,model_y,data['model'],62,maxwidth=1068)
    badge_y = max(533+brand_offset,model_bounds[3]+26)
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
        lime_badge = not dark or product_type == 'agricultural'
        draw.rounded_rectangle((bx,badge_y,bx+dw+48,badge_y+146),radius=24,fill=LIME if lime_badge else PURPLE)
    draw.text((58,badge_y+32),size,font=font,fill=foreground,anchor='lt')
    if diameter:
        draw.text((bx+24,badge_y+32),diameter,font=font,fill='#25252d' if lime_badge else foreground,anchor='lt')
    rows = [(caption,data.get(key)) for key,caption in (
        ('application','ПРИМЕНЯЕМОСТЬ'),('ply_rating','СЛОЙНОСТЬ'),
        ('construction','КОНСТРУКЦИЯ'),('equipment','КОМПЛЕКТАЦИЯ'),
        ('features','ОСОБЕННОСТИ'),('bead','БУРТ'))
        if data.get(key) is not None and str(data.get(key)).strip()]
    y = 760
    indices = [(data.get(key),caption) for key,caption in (
        ('load_index','НАГРУЗКА'),('speed_index','СКОРОСТЬ')) if data.get(key)]
    content = Image.new('RGBA',image.size)
    draw = ImageDraw.Draw(content)
    panel = ImageDraw.Draw(image)
    def category_panel(top,bottom):
        if product_type == 'agricultural':
            panel.rounded_rectangle((58,top+7,474,bottom+7),radius=18,fill='#11121a' if dark else '#c7bfd9')
            panel.rounded_rectangle((52,top,468,bottom),radius=18,
                fill='#24242e' if dark else 'white',outline='#62576f' if dark else '#bba8dc',width=2)
            panel.line((76,top,186,top),fill=LIME,width=5)
        else:
            points=[(52,top),(450,top),(468,top+18),(468,bottom),(70,bottom),(52,bottom-18)]
            panel.polygon([(x+6,y+7) for x,y in points],fill='#11121a' if dark else '#c7bfd9')
            panel.polygon(points,fill='#24242e' if dark else 'white')
            panel.line(points+[points[0]],fill='#62576f' if dark else '#bba8dc',width=2)
            panel.line((76,top,148,top),fill=LIME,width=5)
    row_step = 128 if len(rows)>4 else 136
    for caption,value in rows:
        if product_type == 'special' and caption == 'ОСОБЕННОСТИ':
            # A concise feature badge reads better than one lonely specification box.
            panel.rounded_rectangle((58,y-1,474,y+97),radius=10,fill='#11121a' if dark else '#c7bfd9')
            panel.rounded_rectangle((52,y-8,468,y+90),radius=10,fill=LIME if dark else PURPLE)
            label(78,y+23,str(value).upper(),38,True,fill='#25252d' if dark else 'white',maxwidth=364)
            y += row_step
            continue
        if category_style:
            category_panel(y-22,y+106)
        draw.rounded_rectangle((72,y-3,80,y+83),radius=3,fill=LIME if dark else PURPLE)
        label(104,y,caption,28,True,fill=secondary,maxwidth=326)
        label(104,y+39,value,56,maxwidth=334)
        if not category_style:
            draw.line((72,y+112,435,y+112),fill='#393940' if dark else '#d8d1e4',width=1)
        y += row_step
    index_labels = []
    caption_y = y+96
    for index,(value,caption) in enumerate(indices):
        x = 96+index*204
        bounds = label(x,y+8,value,104,True,fill=LIME if dark else PURPLE,maxwidth=170)
        caption_y = max(caption_y,bounds[3]+18)
        index_labels.append((x+2,caption))
    # Keep captions aligned below the actual glyphs, including descenders in J.
    for x,caption in index_labels:
        label(x,caption_y,caption,25,True,fill=secondary,maxwidth=165)
    if category_style and indices:
        category_panel(y-16,draw.textbbox((0,caption_y),indices[0][1],font=ImageFont.truetype(BOLD,25),anchor='lt')[3]+28)
    bounds = content.getbbox()
    if bounds and not category_style:
        top,bottom = bounds[1]-36,bounds[3]+36
        panel = ImageDraw.Draw(image)
        if not dark:
            panel.rounded_rectangle((58,top+6,474,bottom+6),radius=24,fill='#e9e4f3')
        panel.rounded_rectangle((52,top,468,bottom),radius=24,fill=panel_color,
                                outline='#555463' if dark else '#d8c9f5',width=2)
    image.alpha_composite(content)
    return image.convert('RGB')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input',type=Path,help='Original tyre photograph, not an already designed card')
    parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--background',type=Path,default=BACKGROUND)
    parser.add_argument('--theme',choices=('dark','light'),default='dark')
    args = parser.parse_args()
    data = json.loads(args.data.read_text(encoding='utf-8'))
    for key in ('brand','model','size'):
        if not str(data.get(key) or '').strip():
            parser.error(f'Missing {key}')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    save_card(compose_truck(cutout(args.input),data,args.background,theme=args.theme),args.output)
    print(args.output.resolve())


if __name__ == '__main__':
    main()
