#知识载体 #技术 #Go并发

**技术_Go并发编程_Go受管后台任务**

**来源**：网络

**概念**：<mark>supervised fire-and-forget</mark>指用固定数量的 worker 从一个 buffered channel 中取出 `func()` 闭包来运行的后台任务执行模式，所有 fire-and-forget 任务都必须经由此池，而不是裸 `go func()`，从而将并发上限、错误处理、panic 隔离、上下文生命周期和优雅关停等都集中到同一个 `Background` 结构中收口。

**子概念**：
- <mark>简易 buffer+workers 池</mark>：`make(chan func(), capacity)` 加 `for range workers` 起的固定数 worker，把每个闭包当 `func()` 投递；buffer 满则 send 阻塞，从而把"无界 goroutine"换成"有界队列"。
- <mark>RWMutex 分隔 send 与 close</mark>：`Submit` 持读锁、`Stop` 持写锁关闭 channel，避免"send on closed channel" panic；用 RWMutex 而不是纯 Mutex 是因为 send 本身已经对多 goroutine 安全，只需要把 close 与 send 互斥开即可。
- <mark>上下文脱钩</mark>：后台闭包用 `context.WithoutCancel(r.Context())` 拿到请求的 values（如 request ID）但丢掉父 context 的取消，再用 `context.WithTimeout(..., taskTimeout)` 自己设 deadline；超时从 worker 真正调用闭包那一刻开始计，不是入队时。
- <mark>优雅关停顺序</mark>：`main` 里先 `server.Shutdown(ctx)` 等所有 in-flight handler 的 `Submit` 落地，然后由 `defer background.Stop()` 关 channel、等 `sync.WaitGroup`，确保已接受的闭包跑完再退出。
- <mark>panic 隔离</mark>：`run` 用 `defer recover()` 把任务 panic 转交给 `onPanic` 回调，回调本身再套一层 `defer recover()`，从而单个 worker 的崩溃不会拖垮整个进程。
