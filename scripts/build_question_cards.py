"""Deterministic question share cards. Authoring only: Pillow 12.3.0."""
import json
import textwrap
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT=Path(__file__).resolve().parents[1]
PUBLIC=ROOT/'frontend/public' if (ROOT/'backend/seiche').is_dir() else ROOT

def render(brand, category, title, date, origin):
    img=Image.new('RGB',(1200,630),'#09121a');draw=ImageDraw.Draw(img)
    accent={'LiquiLens':'#b5d6fa','Seiche':'#86ddd2','Undertow':'#f1cf8a'}[brand]
    def text(x,y,value,size,colour):
        draw.text((x,y),value,font=ImageFont.load_default(size=size),fill=colour)
    text(64,40,brand,30,'#edf3f4');text(720,48,origin.removeprefix('https://'),20,'#b3c3cd')
    draw.line((64,110,1136,110),fill='#2d4553',width=2)
    text(64,147,category.upper(),20,accent)
    for i,line in enumerate(textwrap.wrap(title,width=31)):
        text(60,207+i*68,line,56,'#edf3f4')
    draw.rectangle((64,430,1136,510),fill='#10212d')
    text(87,450,'DATED EVIDENCE  /  SOURCE LINKS  /  EXPLICIT LIMITS',24,accent)
    text(64,552,'Explanation reviewed '+date,20,'#b3c3cd')
    text(773,552,'RESEARCH, NOT A QUOTE',19,'#b3c3cd')
    return img

def main():
    c=json.loads((PUBLIC/'questions/pages.json').read_text())
    for p in c['pages']+ [{'slug':'','card_title':c['category']+' explained'}]:
        folder=PUBLIC/'questions'/p['slug'];folder.mkdir(exist_ok=True,parents=True)
        render(c['brand'],c['category'],p['card_title'],c['reviewed_on'],c['origin']).save(folder/'share.png',optimize=True)
    print(c['brand'],len(c['pages'])+1,'contextual question cards rendered')

if __name__=='__main__':main()
