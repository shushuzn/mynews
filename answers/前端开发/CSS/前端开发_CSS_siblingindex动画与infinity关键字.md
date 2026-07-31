#信号笔记 #前端开发 #CSS

**前端开发_CSS_siblingindex动画与infinity关键字**

**来源**：网络

**概念**：sibling-index() 是 CSS 新增的 Baseline 函数，用于获取元素在其同级兄弟节点中的索引位置，可驱动 stagger（交错）动画效果

Firefox 154（2026年8月18日发布）将默认支持 sibling-index()，标志其进入 Baseline: Newly Available 状态。结合 @keyframes 动画可实现同级元素依次入场、错位排列等视觉效果，无需 JavaScript 计算 DOM 位置。

**概念**：calc() 的 infinity 关键字可表示数学无穷大（支持 -infinity 负无穷），在 CSS 中有实际用途

infinity 在 corner-shape 属性中等同于 superellipse(infinity)，可将圆角还原为直角（square）；而 superellipse(-infinity) 则生成缺口形（notch）。这是通过数学函数控制几何形状的技巧。

**概念**：Container Scroll State Queries 中的 stuck 查询可检测元素是否被固定（stuck）在滚动容器中

通过 @container scroll-state(stuck: <keyword>) 可监听元素在滚动时是否吸附在容器边缘，目前 Safari 和 Firefox 尚未支持。

**概念**：Lea Verou 提出将 loading="lazy" 设为 <img> 和 <video> 的默认行为，简化渐进式加载配置

当前需手动在每个媒体元素显式声明 loading="lazy"，提案建议默认启用 lazy，再通过 loading="eager" 按需覆盖，提升开发体验。

**概念**：Firefox 153 实现了 Picture-in-Picture API 的 Baseline 支持，Chrome 151 引入了 <usermedia> 元素

Picture-in-Picture API 现已跨浏览器可用；<usermedia> 目前仅有 Chrome 支持，Safari 和 Firefox 尚在实现中。
