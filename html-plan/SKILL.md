---
name: html-plan
description: MUST use when a request combines HTML output intent with an existing plan-like file. Trigger for “HTML 文件”, “HTML 预览页”, “配套 HTML”, “用 HTML 展示”, “展示成 HTML”, “render as HTML”, “不要改计划内容”, plus files like plan.md, implementation-plan.md, release-plan.md, roadmap.yaml, tasks.json, or .txt plans. Roadmap and tasks files count as plan 文件 even if the user does not say plan. Do not use for merely drafting a plan with no plan file and no HTML output. 这个 skill 不负责制定计划本身，不预设 sections；HTML 必须基于输入 plan 文件的实际内容和顺序生成。
---

# HTML Plan

把一个已有的 plan 文件转换成更易懂、可审阅、方便分享的 HTML 展示页。plan 文件是事实来源；HTML 不应只是原文换壳，而要把计划里的关系、阶段、重点、风险和验证路径整理成更容易理解的视觉表达。

## 触发边界

- 已经有明确的 plan 文件，例如 `plan.md`、`implementation-plan.md`、`roadmap.yaml`、`tasks.json`。
- 用户要求把某个计划、方案、路线图、任务清单或执行计划“展示成 HTML”“生成预览页”“方便审阅/分享”。
- 你在当前工作流中已经创建了 plan 文件，并且需要给用户一个可打开的 HTML 视图。
- 如果没有 plan 文件，先不要直接使用此 skill 生成 HTML。先创建或定位 plan 文件，再基于该文件生成展示页。
- 如果用户只是在要求你制定计划，而没有要求 HTML 展示，也没有 plan 文件输出需求，正常回答或创建 plan 文件即可，不要强行调用此 skill。

## 核心原则

- Source first：先完整阅读输入 plan 文件，再生成 HTML。
- Understanding first：优先帮助读者更快理解计划，不要机械复制 Markdown 段落。
- Source-grounded synthesis：可以重组、归纳、对照和可视化源文件中的信息，但不得加入源文件没有依据的新计划内容。
- Flexible structure：不要强制输出固定 sections；根据输入 plan 的真实内容选择适合的解释结构。
- Traceable details：重组后的图、表、摘要和重点必须能回溯到源 plan 中的段落、列表、表格或代码块。
- One-way rendering：plan 文件是事实来源。更新计划时先更新 plan 文件，再重新生成或同步 HTML。

## 输入处理

1. 从用户消息、当前工作流或 workspace 中确定 plan 文件路径。路径不明确时，优先查找显眼的 `plan.md`、`*-plan.md`、`roadmap.*`、`tasks.*`。
2. 读取整个 plan 文件。Markdown 是首选格式；JSON、YAML、TXT、CSV 也可以展示。
3. 识别源文件中的真实结构和可解释对象：
   - Markdown 标题转为同名 HTML sections。
   - 普通段落、引用、列表按原顺序展示。
   - 任务列表 `- [ ]` / `- [x]` 转成 checklist。
   - 表格放入可横向滚动容器。
   - 代码块使用 `<pre><code class="language-xxx">`。
   - `mermaid` 代码块转成 Mermaid 图。
   - 状态词如 `todo`、`doing`、`done`、`blocked`、`pending`、`in progress` 可视觉化为 badge，但不要改写原含义。
   - JSON/YAML 的对象键和数组项按原层级展示，键名就是展示标题，不映射到固定 section 名。
   - 阶段、依赖、模块边界、架构流、风险、验收标准、禁止事项、文件路径、时间顺序等内容应提取为可视化候选。
4. 如果源文件包含 frontmatter 或元数据，可以展示为页面 meta；不要把元数据当成固定内容区。

## 生成流程

1. 选择输出路径：默认与 plan 文件同目录，文件名为 `<plan-basename>.html`。用户指定路径时遵从用户指定。
2. 使用 `assets/plan-template.html` 作为视觉外壳和 CSS 起点；替换其中的占位内容。页面结构必须由输入 plan 推导，不套固定模板。
3. 设置页面标题：
   - 优先使用 plan 文件的第一个一级标题。
   - 没有一级标题时使用文件名。
4. 设置页面 meta，只放生成信息，例如 source path、updated time、format。不要在 meta 中补造计划内容。
5. 先生成一个理解层，再保留必要细节：
   - 用 3-6 个重点卡片总结最关键的结论、约束、风险或下一步。
   - 对架构、流程、依赖、状态迁移或事件流生成 Mermaid 或内联 SVG 图。
   - 对阶段计划、测试矩阵、模块边界、取舍和禁止事项生成表格或矩阵。
   - 对必须注意的判断、风险、阻塞和验收点使用 callout 或强调样式。
   - 长原文不要整段堆在页面顶部；可放在“详细依据”“源计划细节”等后半部分，或只展示被整理后的结构。
6. 生成后快速检查 HTML 是否能打开，内容是否完整，特殊字符是否正确转义，图表在脚本失败时仍有可读文本替代。
7. 最终回复给出 HTML 文件的绝对路径链接，并注明它来自哪个 plan 文件。

## 视觉与内容要求

- 保持信息密度高、层级清楚、可打印；移动端和桌面端都能阅读。
- 使用可访问的对比度、语义化标题、简洁表格、清晰列表和稳定布局。
- 不要新增和输入 plan 无关的 hero 文案、营销文案、解释性教程、外部字体、动画堆砌或装饰元素。
- 不要把所有内容塞进宽表格，也不要只做目录加原文复制。长文本要被整理成关系、阶段、矩阵、重点或依据区。
- 即使源文件没有现成图表，也可以根据源文件里的流程、阶段、依赖、架构或测试关系生成辅助图表；图表内容必须来自源文件。
- 允许增强阅读体验，例如重点卡片、状态 badge、任务 checkbox、代码高亮、表格滚动、目录导航、风险矩阵、阶段时间线、模块边界图；增强必须服务于原文内容。
- 不在 HTML 中写入密钥、个人隐私、内部令牌或不该展示的大段日志。

## 允许的 CDN

- 默认生成自包含 HTML，核心内容不依赖网络。
- Prism.js：只有当计划包含代码块且真实语法高亮有收益时使用。可用 `https://cdn.jsdelivr.net/npm/prismjs/themes/prism.min.css`、`https://cdn.jsdelivr.net/npm/prismjs/prism.min.js`，再按需添加语言组件。
- Mermaid：只有当计划包含 Mermaid 图时使用 `https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js` 并调用 `mermaid.initialize({ startOnLoad: true })`。
- CDN 只用于增强展示；核心计划内容必须在脚本加载失败时仍可阅读。
- 不要引入字体、图标库、UI 框架、分析脚本或与计划无关的远程资源。

## 实现片段

- Prism 代码块：`<pre><code class="language-typescript">...</code></pre>`。
- Mermaid 图：`<pre class="mermaid">graph TD; A[输入] --> B[执行] --> C[验证]</pre>`。
- 状态 badge：`<span class="status done">done</span>`。
- 任务项：`<li class="task done"><input type="checkbox" checked disabled> 完成的任务</li>`。
- 重点卡片：`<div class="insight-grid"><article class="insight-card">...</article></div>`。
- 阶段表：`<div class="table-wrap"><table>...</table></div>`。
- 风险或决策提示：`<aside class="callout important">...</aside>`。

## 更新规则

- 如果 plan 文件变化，重新渲染 HTML，避免只在 HTML 中手工修改导致源文件和展示页分叉。
- 如果用户要求补充或调整计划内容，先更新 plan 文件，再生成 HTML。
- 如果输入 plan 文件很短，也照实展示；不要为了“看起来完整”扩写固定 sections。
- 如果 plan 文件格式损坏或无法解析，保留可读的原始文本区块，并在最终回复中说明限制。
