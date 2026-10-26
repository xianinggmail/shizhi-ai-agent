"""Generate labelled synthetic Chinese PDF for browser checks; not textbook content."""
from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont

root=Path(__file__).resolve().parents[1]
(root/'work').mkdir(exist_ok=True)
output=root/'work'/'演示参考书_合成测试材料.pdf'
pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
c=canvas.Canvas(str(output))
c.setTitle('合成测试材料：传播学学习流程')
lessons=[('传播与符号','传播是人们借助符号交流信息的过程。符号的意义依赖语境，理解传播时需要同时关注信息、参与者以及社会环境。'),('传播过程','传播过程包括信息的形成、传递与理解。过程中的反馈可以帮助参与者修正表达，不能仅凭信息发出就认定传播已经成功。'),('大众传播','大众传播涉及媒介组织对信息的生产与分发。研究这一过程时，需要同时考虑组织规范、技术条件和受众的使用情境。'),('受众与解读','受众会结合自己的知识与经验理解信息。同一段内容可能产生不同解读，因此不能把传播效果简单归结为发送者的单方面意图。'),('媒介素养','媒介素养包括识别信息来源、核查证据以及反思媒介使用习惯。判断信息可信度时，应关注材料是否提供了能够核对的原始依据。'),('研究与证据','研究结论应说明证据范围与方法限制。相关关系不能直接证明因果关系，解释传播现象时需要避免超出研究材料支持的范围。')]
for i,(title,body) in enumerate(lessons):
    c.bookmarkPage('p'+str(i))
    if i in (0,3):c.addOutlineEntry('第一章 基础概念' if i==0 else '第二章 阅读与研究','p'+str(i),0)
    c.setFont('STSong-Light',20);c.drawString(55,780,title)
    c.setFont('STSong-Light',12);c.drawString(55,745,'演示用合成文字，非教材引文，请勿作为专业学习依据。')
    t=c.beginText(55,700);t.setLeading(25)
    for start in range(0,len(body),30):t.textLine(body[start:start+30])
    c.drawText(t);c.drawString(55,60,'PDF 物理页码 '+str(i+1));c.showPage()
c.save()
print(output)
