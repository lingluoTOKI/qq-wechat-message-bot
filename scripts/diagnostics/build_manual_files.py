"""Build a bookmarked, phone-friendly PDF and UTF-8 text from the live catalog."""
import importlib.util
import json
import re
import shutil
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A5
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import BaseDocTemplate,Frame,PageTemplate,Paragraph,Spacer,PageBreak,KeepTogether
from reportlab.platypus.tableofcontents import TableOfContents

ROOT=next(p for p in Path(__file__).resolve().parents if (p/'.git').exists())
PLUGIN=ROOT/'plugins/astrbot_plugin_dafeiyu_help'


def main():
    spec=importlib.util.spec_from_file_location('manual_data',PLUGIN/'manual.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    font='ManualChinese';pdfmetrics.registerFont(TTFont(font,'C:/Windows/Fonts/simhei.ttf'))
    out=ROOT/'output/pdf';out.mkdir(parents=True,exist_ok=True)
    pdf=out/'大肥鱼完整说明书.pdf';txt=ROOT/'output/大肥鱼完整说明书.txt'
    blue=colors.HexColor('#135C81');ink=colors.HexColor('#20333E');muted=colors.HexColor('#4E606B')
    styles={
        'cover':ParagraphStyle('cover',fontName=font,fontSize=27,leading=36,textColor=blue,spaceAfter=14),
        'subtitle':ParagraphStyle('subtitle',fontName=font,fontSize=18,leading=26,textColor=ink,spaceAfter=16),
        'heading':ParagraphStyle('sectionHeading',fontName=font,fontSize=15,leading=21,textColor=blue,spaceBefore=18,spaceAfter=12,keepWithNext=True),
        'body':ParagraphStyle('body',fontName=font,fontSize=10.5,leading=16,wordWrap='CJK',textColor=ink,spaceAfter=7),
        'cmd':ParagraphStyle('cmd',fontName=font,fontSize=10.8,leading=16,wordWrap='CJK',textColor=blue,spaceAfter=3),
        'note':ParagraphStyle('note',fontName=font,fontSize=9.2,leading=14,wordWrap='CJK',textColor=muted,leftIndent=8,spaceAfter=7),
    }
    def p(text,kind='body'):return Paragraph(escape(text.replace('\u2011','-')),styles[kind])
    class ManualDoc(BaseDocTemplate):
        def afterFlowable(self,flowable):
            if isinstance(flowable,Paragraph) and flowable.style.name=='sectionHeading':
                key=flowable._manual_key
                self.canv.bookmarkPage(key)
                self.canv.addOutlineEntry(flowable.getPlainText(),key,0,False)
                self.notify('TOCEntry',(0,flowable.getPlainText(),self.page,key))
    def decorate(canvas,doc):
        w,h=A5;canvas.saveState()
        canvas.setStrokeColor(colors.HexColor('#D4E4EC'));canvas.line(26,h-25,w-26,h-25)
        canvas.setFont(font,8);canvas.setFillColor(muted)
        canvas.drawString(26,h-18,'大肥鱼 / 小洛 · 完整功能说明书')
        canvas.drawString(26,17,'v1.10.2  |  2026.10.30')
        canvas.drawRightString(w-26,17,str(doc.page))
        canvas.restoreState()
    doc=ManualDoc(str(pdf),pagesize=A5,leftMargin=26,rightMargin=26,topMargin=36,bottomMargin=33,
                  title='大肥鱼（小洛）完整功能说明书',author='大肥鱼 / 小洛',pageCompression=1)
    doc.addPageTemplates(PageTemplate(id='manual',frames=[Frame(26,33,A5[0]-52,A5[1]-69,id='body',leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0)],onPage=decorate))
    story=[Spacer(1,65),p('大肥鱼 / 小洛','cover'),p('完整功能说明书','subtitle'),
           p('25类功能 · QQ官方机器人 · 可点击目录'),Spacer(1,16),
           p('/说明书：直接取PDF','cmd'),p('/功能 TXT：取轻量文字文件','cmd'),
           p('/功能：常用入口和分类目录','cmd'),p('/功能 分页 1：逐页浏览文字','cmd'),Spacer(1,22),
           p('群里@机器人，私聊直接说话。说明书和规则查询不调用聊天模型。'),
           p('功能是否可用取决于账号授权、音源、后端和当前设置。首次使用请先看对应分类；管理员操作另列。','note'),PageBreak(),p('目录','subtitle')]
    toc=TableOfContents()
    toc.levelStyles=[ParagraphStyle('toc',fontName=font,fontSize=10.6,leading=16.5,leftIndent=0,firstLineIndent=0,rightIndent=18,spaceBefore=2,textColor=blue)]
    toc.dotsMinLevel=0;story.extend([toc,PageBreak()])
    text=['大肥鱼（小洛）完整说明书','2026-10-30 · v1.10.2','',
          '/说明书 或 /功能 全部：发送PDF','/功能 TXT：发送文字附件','/功能 分页 页码：逐页浏览','']
    for index,(name,title,rows,_) in enumerate(m.SECTIONS,1):
        if name=='风格':story.append(PageBreak())
        heading=p(f'{index:02d}  {name} · {title}','heading');heading._manual_key='section_'+str(index)
        story.append(heading);text+=['',f'{index:02d} {name} · {title}','']
        for row in rows:
            cmd,desc=row[:2];notes=row[2] if len(row)>2 else None
            if cmd:
                block=[p(cmd,'cmd'),p(desc)]
                if notes:block.append(p(notes,'note'))
                story.append(KeepTogether(block));text.append(cmd+'：'+desc)
            else:
                story.append(p(desc,'note'));text.append('说明：'+desc)
            if notes:text.append('  '+notes)
        story.append(Spacer(1,5))
    story.extend([Spacer(1,12),p('使用与验证说明','heading')])
    story[-1]._manual_key='verification'
    story.append(p('命令入口、分页和消息长度经过核对。本说明书不代表所有外部服务已经真人验收；未开放或需要授权的功能以实际返回状态为准。','note'))
    doc.multiBuild(story)
    txt.write_text('\n'.join(text)+'\n',encoding='utf-8-sig')
    resources=PLUGIN/'resources';resources.mkdir(exist_ok=True)
    shutil.copy2(pdf,resources/pdf.name);shutil.copy2(txt,resources/txt.name)
    import fitz
    preview=ROOT/'tmp/pdfs/manual';preview.mkdir(parents=True,exist_ok=True)
    with fitz.open(pdf) as pages:
        content=''.join(page.get_text() for page in pages)
        normalized=re.sub(r'\s+','',content)
        missing=[row[0] for _,_,rows,_ in m.SECTIONS for row in rows if row[0] and re.sub(r'\s+','',row[0]) not in normalized]
        assert not missing,missing
        for i,page in enumerate(pages):
            page.get_pixmap(matrix=fitz.Matrix(1.3,1.3)).save(preview/f'page-{i+1:02d}.png')
        report={'pages':len(pages),'bookmarks':len(pages.get_toc()),'missing_commands':missing,'pdf_bytes':pdf.stat().st_size,'text_bytes':txt.stat().st_size}
    (ROOT/'review/manual-file-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
