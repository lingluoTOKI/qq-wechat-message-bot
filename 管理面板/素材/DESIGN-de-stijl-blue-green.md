# 风格派 × 青绿山水

## 展签
主 · 风格派 De Stijl，副 · 青绿山水 Blue-Green Landscape

**风格派 De Stijl** · 骨架 · 1917–1931 · 荷兰

风格派 1917 年由杜斯伯格和蒙德里安在荷兰创立，同名杂志是它的阵地。它把画面简化成水平线、垂直线和红黄蓝三原色，追求普遍、客观的和谐。

这次负责：
- 排版：不对称矩形分割
- 标题字体：Archivo Black

**青绿山水 Blue-Green Landscape** · 表皮 · 隋唐至宋 · 中国

青绿山水以石青、石绿为主色，隋唐时成熟，北宋王希孟的《千里江山图》是巅峰。它先勾出山石轮廓，再用矿物颜料层层罩染，画的是理想中的江山。

这次负责：
- 配色：石青做主色和 CTA
- 质感：绢本经纬纹（被风格派调淡）
- 主视觉：层叠山峰、细线勾勒

## 主张
风格派搭骨架：不对称矩形分割；青绿山水上颜色：石青做主色和 CTA。

## 色彩
```css
:root {
  --bg: oklch(0.93 0.025 90);
  --surface: oklch(0.88 0.035 120);
  --ink: oklch(0.22 0.03 200);
  --muted: oklch(0.42 0.04 190);
  --line: oklch(0.66 0.1 85);
  --accent: oklch(0.45 0.1 230);
  --on-accent: oklch(0.96 0.02 90);
  --accent2: oklch(0.6 0.1 165);
  --accent3: oklch(0.65 0.08 60);
}
```
用法：石青做主色和 CTA，石绿做大面积面板，赭石点缀在山脚，细线用淡金色。
只使用 3 个强调色。

## 字体
- 标题：font-family: "Archivo Black", "PingFang SC", "Noto Sans SC", sans-serif；字重 400；全大写；字距 0
- 正文：font-family: "Archivo", "PingFang SC", "Noto Sans SC", sans-serif；字重 400
- 中文标题：拉丁标题字体没有中文字形，中文会落到回退字体，所以中文标题单独指定 700–900 字重，和拉丁标题的分量一致；字距不要用负值
- Google Fonts：Archivo Black / Archivo:wght@400;600

## 布局
- 栅格：不对称矩形分割，格子由 6–10px 粗黑线分开
- 规则：
  - 整个页面被粗黑线切成大小不一的矩形格
  - 只有水平和垂直方向，禁止斜线和曲线
  - 彩色格子少于三成，其余是白格
  - 文字放在白格里，靠格子大小表达层级
  - 规则里的颜色词只表示角色：黑线 = --line，黑字 = --ink，白格 = --bg，彩色格 = 强调色。
- 圆角：0；边框：6–10px 黑线，格子之间的间隙就是线；阴影：无；密度：中等偏疏

## 质感
绢本经纬纹，强度 0.2
强度是质感的浓淡（0–1），按观感理解：0.15 几乎看不见，0.3 能看出，0.5 明显，0.6 很重。不同质感换算成 CSS 不透明度时系数可以不同，以观感为准。质感层铺在页面底色上、正文下层，不进卡片，不压文字。强度上限由主流派的 textureCap 决定，比如极简主义最多 0.15，几乎看不见。

## 母题
层叠山峰、细线勾勒、青绿罩染、楼阁与长卷
母题首先用在主视觉和插图里。纯粹形态或没有冲突时，母题也可以做成界面上的单个图形（比如包豪斯的圆、方、三角），但不要铺成重复的底纹。有冲突时按 mixing.md 第 4 条只放进插图。

## 动效
格子展开、线条生长；只沿水平或垂直方向，不旋转，240ms cubic-bezier(0.3, 0, 0.2, 1)。按下时也要有反馈。用键盘快捷键触发的状态切换不加过渡，遵守 prefers-reduced-motion。

## 禁止
- 斜线
- 曲线与圆形
- 阴影
- 灰暗的水墨调
- 荧光色
- 西式透视

## 无障碍
- 正文与背景对比度 ≥ 4.5:1。
- 文字放在 --accent 上用 --on-accent；放在 --accent2、--accent3 上时，在 --ink 和 --on-accent 里选对比度更高的那个，并保证 ≥ 4.5:1。
- 焦点环 2px，颜色在 --ink 和 --accent 里选与所在背景对比度 ≥ 3:1 的那个。即使主流派禁止描边，焦点环也必须保留。

## 提示词

### 写代码用
```text
请按下面的设计系统实现页面：风格派（De Stijl）的结构 × 青绿山水（Blue-Green Landscape）的色彩与质感。
风格派搭骨架：不对称矩形分割；青绿山水上颜色：石青做主色和 CTA。

【CSS 变量】
:root {
  --bg: oklch(0.93 0.025 90);
  --surface: oklch(0.88 0.035 120);
  --ink: oklch(0.22 0.03 200);
  --muted: oklch(0.42 0.04 190);
  --line: oklch(0.66 0.1 85);
  --accent: oklch(0.45 0.1 230);
  --on-accent: oklch(0.96 0.02 90);
  --accent2: oklch(0.6 0.1 165);
  --accent3: oklch(0.65 0.08 60);
}
用色：石青做主色和 CTA，石绿做大面积面板，赭石点缀在山脚，细线用淡金色。
只使用 3 个强调色。

【字体】
标题：Archivo Black 400，全大写，字距 0
正文：Archivo 400
中文回退："PingFang SC", "Noto Sans SC", sans-serif
Google Fonts：Archivo Black / Archivo:wght@400;600

【布局】
栅格：不对称矩形分割，格子由 6–10px 粗黑线分开
- 整个页面被粗黑线切成大小不一的矩形格
- 只有水平和垂直方向，禁止斜线和曲线
- 彩色格子少于三成，其余是白格
- 文字放在白格里，靠格子大小表达层级
规则里的颜色词只表示角色：黑线 = --line，黑字 = --ink，白格 = --bg，彩色格 = 强调色。
圆角 0；边框 6–10px 黑线，格子之间的间隙就是线；阴影 无；密度 中等偏疏

【质感】绢本经纬纹，强度 0.20（0–1），只铺在背景上，不盖住文字
【母题】层叠山峰、细线勾勒、青绿罩染、楼阁与长卷。首先用在主视觉和插图里；和骨架规则不冲突时，也可以做成界面上的单个图形，但不要铺成重复的底纹，不改变上面的布局规则。
【动效】格子展开、线条生长；只沿水平或垂直方向，不旋转，240ms cubic-bezier(0.3, 0, 0.2, 1)。按下时也要有反馈；用键盘快捷键触发的状态切换不加过渡，遵守 prefers-reduced-motion。
【禁止】斜线、曲线与圆形、阴影、灰暗的水墨调、荧光色、西式透视。不要使用渐变文字、默认玻璃卡片、通用圆角阴影卡片网格。
【无障碍】正文与背景对比度 ≥ 4.5:1；文字放在 --accent 上用 --on-accent，放在 --accent2、--accent3 上时在 --ink 和 --on-accent 里选对比度更高的那个；焦点环 2px，必须保留。
```

### 生图用
```text
a poster for an independent bookstore called Paper Pages, Chinese blue-green landscape painting, mineral azurite and malachite on silk, fine ink outlines, handscroll, asymmetric rectangular division by thick dark horizontal and vertical lines, palette of azurite blue, malachite green, pale gold line, ochre, silk, featuring layered peaks, fine contour lines, azurite and malachite washes, pavilions along a handscroll

Negative: diagonal lines, curves, circles, monochrome, neon, western perspective, 3d render
```
