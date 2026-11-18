# 事件驱动进程检测：评估与落地（v2.5.0-③）

结论先行：**采用路线 a（ctypes 直连 WMI COM，`Win32_ProcessStartTrace` /
`Win32_ProcessStopTrace`）**，事件只做「唤醒器」，轮询降为低频兜底。
不可用/权限不足时自动回落纯轮询，已由测试覆盖。

## 一、三条路线对比

| 维度 | a) ctypes 直连 WMI COM | b) ctypes SetWinEventHook | c) `import wmi` |
| --- | --- | --- | --- |
| 新增依赖 | 0（纯 ctypes + 标准库） | 0 | pywin32 全家（pythoncom / win32com / PyWinTypesse…） |
| 打包增量 | 0（不含本模块自身字节码） | 0 | 实测环境已装 pywin32（`import pythoncom` 可用）；按 wheel 内容估 >1 MB 压缩，**远超 60 KB 预算**，否决，未做打包验证 |
| 覆盖面 | 进程启动+停止，全量、无窗口进程（反作弊服务、小飞机后台组件）也收得到 | 只覆盖窗口事件；无窗口进程收不到；游戏/弹窗场景覆盖不全 | 同 a |
| 空闲 CPU | 服务线程阻塞在内核等待，无事件时 CPU≈0；守护轮询降频后**空闲 CPU 下降** | 全系统窗口事件风暴（每秒数十次回调），空闲 CPU 反升 | 同 a |
| 与闸门/插件兼容 | 判定逻辑零改动（只改「什么时候跑一轮」）；`procs is None` 降级规则不受影响 | 同左，但事件语义不精确（窗口≠进程） | 同 a |
| 权限要求 | 提权订阅（守护由计划任务 HIGHEST 启动，满足）；未提权时订阅失败 → 回落轮询 | 无特殊要求 | 同 a |
| 实现风险 | COM vtable 手写，下标/签名/flags 有坑（见下） | 低 | 低 |

选择标准逐条核对（主理人拍板的三条）：
1. Setup 增量 ≤ 60 KB：路线 a 增量只有 `proc_events.pyc` 自身（见交付报告数字）；
2. 空闲 CPU 不升反降：轮询 2s → 30s 兜底，实测见交付报告；
3. 闸门 / 插件 `Tick` 语义兼容：判定代码零改动，事件只提前「跑一轮」的时机。

## 二、落地架构

- `src/proc_events.py`：`ProcEventWatcher`（两条订阅线程）+ `attach()` /
  `wait_for_wake()`。守护 `main()` 挂载，`Guard.run()` 的等待从
  `sleep(poll_delay)` 换成 `wait_for_wake(ev.wake, wait_delay)`。
- 名单过滤在回调侧做（`g.watch` 每事件实时取）：watch 变化无需重新订阅；
  全系统进程事件频率低（每秒个位数），线程唤醒开销可忽略。
- 兜底间隔：空闲 `poll_interval_sec × 15`（2s → 30s），会话中
  `poll_fast_sec × 5`（0.4s → 2.0s，游戏退出有 StopTrace 即时兜住）；
  插件 `request_fast_poll` 登记的快节拍（弹窗监视期）**绝不放宽**。
- 自动回落：订阅失败（未提权 / COM 被禁 / 运行期异常）→ `ok=False` →
  纯轮询，记一行日志；守护绝不因事件机制而停摆。

## 三、实现踩坑记录（写代码者必读）

1. `oleaut32` 必须用 `windll`：`oledll` 会把返回值当 HRESULT 校验，
   `SysAllocStringLen` 返回 BSTR 指针（64 位大数）→ OverflowError。
2. `ExecNotificationQuery` 的 vtable 下标是 **22** 不是 24（权威顺序见
   MS-WMI 协议规范 3.1.4.3 的 opnum 表：ExecQuery=20、ExecQueryAsync=21、
   ExecNotificationQuery=22、ExecNotificationQueryAsync=23、ExecMethod=24）。
   且 `lFlags` **必须** = `WBEM_FLAG_RETURN_IMMEDIATELY | WBEM_FLAG_FORWARD_ONLY`
   （0x30），否则恒返回 `WBEM_E_INVALID_PARAMETER`。
3. `IWbemClassObject::Get` 在 vtable 下标 **4**（GetQualifierSet=3, Get=4）。
4. trace 事件对象（fastprox 实现）的 `Get` **不接受 NULL 的 pType/pFlavor
   出参**（报 `WBEM_E_TYPE_MISMATCH`）；类对象却容忍 NULL。必须传真实缓冲区。
5. VARIANT 的 Win64 ABI：总长 24 B，值区（union）在偏移 8 且必须容纳
   DECIMAL（16 B）。union 放错偏移拿到垃圾指针。
6. 外线程对阻塞中的 `ConnectNamedPipe` 调 `DisconnectNamedPipe` 会死锁
   （心跳管道踩过同类坑）：停机用「自连接唤醒」模式，主线程不碰服务线程句柄。

## 四、测试与实测数据

见 `test/test_proc_events.py`（真实 WMI 启动/停止事件均验证到达）与
交付报告的 CPU 对比数字。
