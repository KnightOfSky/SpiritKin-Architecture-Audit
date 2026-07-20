(function () {
  const concepts = [
    {
      name: "Atelier Editorial",
      variant: "atelier",
      skin: "atelier",
      accent: "oklch(72% 0.14 62)",
      accent2: "oklch(66% 0.06 48)",
      canvas: "oklch(18% 0.012 66)",
      surface: "oklch(22% 0.014 64)",
      surface2: "oklch(26% 0.016 62)",
      surface3: "oklch(31% 0.018 60)",
      line: "oklch(40% 0.016 60)",
      lineStrong: "oklch(52% 0.022 58)",
      text: "oklch(94% 0.01 70)",
      muted: "oklch(72% 0.015 62)",
      faint: "oklch(58% 0.014 60)",
      title: "SpiritKin Desktop · Atelier Editorial",
      meta: "当前会话 · 编辑室 · 长文阅读",
      chatTitle: "与 Spirit 的对话 · 编辑室视图",
      confirm: "require_approval · start_workflow_run · queue_android_command",
      assistantText: "这一版换掉驾驶舱面板语言：暖炭底、无边框、排版驱动。聊天是居中阅读栏，工作台降为安静的仪表带，让人和 AI 的对话本身成为主角。",
      toolText: "worker_heartbeat · record_artifact · avatar_state_event · sync_context",
      workbench: "工作台 / 安静仪表带",
      module: "Workflows",
      mobile: "移动端沿用暖炭排版体系：无边框、大留白、单一琥珀铜点睛。",
      avatar: "3D 模型作为编辑室一角的安静陪伴，暖光打亮，不抢阅读焦点。"
    },
    {
      name: "Linear Product Frame",
      variant: "tight",
      accent: "oklch(64% 0.19 256)",
      accent2: "oklch(76% 0.10 196)",
      canvas: "oklch(12% 0.035 328)",
      surface: "oklch(18% 0.038 326)",
      surface2: "oklch(23% 0.045 324)",
      surface3: "oklch(29% 0.052 322)",
      line: "oklch(39% 0.055 322)",
      lineStrong: "oklch(52% 0.07 322)",
      title: "SpiritKin Desktop · Product Screenshot Frame",
      meta: "当前会话 · 产品框架 · 运行状态",
      chatTitle: "主会话 · issue-like product frame",
      confirm: "pending: policy_scope · permission_gate · action_log",
      assistantText: "把桌面端当作产品截图本体处理：减少外部装饰，让真实聊天、管理模块和 3D 舞台做主角。",
      toolText: "surface-1 panels · status_badge · focus_ring · changelog_row",
      workbench: "Management shell",
      module: "Tasks",
      mobile: "移动端只做同风格延展：深色面板、紧凑状态、同一焦点色。",
      avatar: "Avatar 阶段、情绪、字幕和安全状态在同一个 surface ladder 里。"
    },
    {
      name: "Warp Terminal Dock",
      variant: "ops",
      accent: "oklch(64% 0.19 256)",
      accent2: "oklch(76% 0.10 196)",
      canvas: "oklch(17% 0.045 62)",
      surface: "oklch(23% 0.05 58)",
      surface2: "oklch(28% 0.055 54)",
      surface3: "oklch(34% 0.06 52)",
      line: "oklch(45% 0.06 58)",
      lineStrong: "oklch(57% 0.075 58)",
      title: "SpiritKin Desktop · Terminal Dock",
      meta: "当前会话 · 终端任务 · 工具事件",
      chatTitle: "Shell-first session · terminal blocks visible",
      confirm: "shell_command · terminal_panel · safety_stop",
      assistantText: "适合开发者长时间盯屏：中间聊天更像终端记录，右侧状态区用温暖深色降低刺眼度。",
      toolText: "$ python scripts/start_desktop_console.py --open-mode wpf",
      workbench: "Environment / Terminal",
      module: "Logs",
      mobile: "手机端同步用低色彩 warm-dark，不额外开一套视觉。",
      avatar: "3D 舞台保留，但用终端状态 HUD 叠加，而不是扩大成视觉资产。"
    },
    {
      name: "Cursor Timeline Chat",
      variant: "light",
      accent: "oklch(64% 0.19 256)",
      accent2: "oklch(76% 0.10 196)",
      canvas: "oklch(94% 0.018 205)",
      surface: "oklch(98% 0.008 205)",
      surface2: "oklch(100% 0 0)",
      surface3: "oklch(90% 0.028 198)",
      line: "oklch(82% 0.035 198)",
      lineStrong: "oklch(70% 0.055 198)",
      title: "SpiritKin Desktop · AI Timeline Workspace",
      meta: "当前会话 · AI Timeline · 编辑状态",
      chatTitle: "AI timeline · thinking / reading / editing / done",
      confirm: "confirm: edit_files · attach_context · record_artifact",
      assistantText: "这一版降低暗色压迫感，借用 AI Timeline 表示模型状态：Thinking、Reading、Editing、Done。",
      toolText: "Thinking -> Reading -> Editing -> Done",
      workbench: "IDE-style workbench",
      module: "Context",
      mobile: "移动端保持暖色底和橙色关键动作，不另起品牌。",
      avatar: "Avatar 作为右侧 IDE preview，不遮挡聊天阅读。"
    },
    {
      name: "Robin Soft Console",
      variant: "tight",
      accent: "oklch(64% 0.19 256)",
      accent2: "oklch(76% 0.10 196)",
      canvas: "oklch(14% 0.04 178)",
      surface: "oklch(20% 0.04 176)",
      surface2: "oklch(25% 0.047 174)",
      surface3: "oklch(31% 0.055 172)",
      line: "oklch(42% 0.055 176)",
      lineStrong: "oklch(54% 0.07 176)",
      title: "SpiritKin Desktop · Soft AI Console",
      meta: "当前会话 · 助手状态 · 语音输出",
      chatTitle: "Quick chat · soft assistant layer",
      confirm: "need_user: approve_plan · confirm_remote_device",
      assistantText: "保留工程信息密度，但降低硬边冲突：聊天更亲和，3D 模型像可见的助手状态层。",
      toolText: "assistant_state · voice_output · runtime_narration",
      workbench: "Companion state",
      module: "Agents",
      mobile: "多端延展使用同一柔和绿蓝，不改功能层级。",
      avatar: "3D 模型区域加强字幕和情绪，不再只是右下小图。"
    },
    {
      name: "Companion Relay Desk",
      variant: "theatre",
      skin: "stage",
      accent: "oklch(76% 0.12 192)",
      accent2: "oklch(82% 0.16 82)",
      canvas: "oklch(10% 0.036 282)",
      surface: "oklch(16% 0.044 276)",
      surface2: "oklch(21% 0.052 268)",
      surface3: "oklch(27% 0.06 260)",
      line: "oklch(38% 0.062 262)",
      lineStrong: "oklch(54% 0.078 226)",
      text: "oklch(96% 0.012 250)",
      muted: "oklch(78% 0.028 242)",
      faint: "oklch(62% 0.036 240)",
      title: "SpiritKin Desktop · Companion Relay Desk",
      badge: "06",
      meta: "当前会话 · Avatar Runtime · Decision Relay",
      chatTitle: "Companion relay · stage and chat linked",
      confirm: "approve_avatar_cue · inspect_diff · sync_mobile · owner_gate",
      assistantText: "第二版把舞台从“展示主角”改成“协作中枢”：左侧 Avatar 负责情绪和播报，中栏聊天恢复可读，右侧只承接审批、证据和工具状态。",
      toolText: "avatar_state_event · append_message · record_artifact · queue_android_command",
      workbench: "Relay Desk",
      module: "3D 模型",
      mobile: "移动端同步当前提词、审批结论和运行证据；桌面端保留完整舞台与聊天上下文。",
      avatar: "Avatar 保持可见人格，但不吞掉工作台：表情、字幕、动作队列和审批状态都和聊天记录并排呈现。"
    },
    {
      name: "Safety Operations",
      variant: "ops",
      accent: "oklch(64% 0.19 256)",
      accent2: "oklch(76% 0.10 196)",
      canvas: "oklch(13% 0.045 8)",
      surface: "oklch(19% 0.052 8)",
      surface2: "oklch(24% 0.058 10)",
      surface3: "oklch(30% 0.064 12)",
      line: "oklch(41% 0.064 12)",
      lineStrong: "oklch(54% 0.08 14)",
      title: "SpiritKin Desktop · Safety Operations",
      meta: "当前会话 · 安全治理 · 权限门",
      chatTitle: "Guarded chat · high-risk action visible",
      confirm: "blocked: destructive_command · remote_device · require_permission",
      assistantText: "安全停止、恢复、权限、确认门在聊天头部和右侧面板同时可见，适合远端设备与命令网关场景。",
      toolText: "safety_stop=armed · permission_scope=workspace · action_log=immutable",
      workbench: "Safety / Governance",
      module: "Safety",
      mobile: "移动端继承红绿安全语义，只保留 approve / deny / stop / resume。",
      avatar: "Avatar 直接显示 interrupted / need_user / running 状态。"
    },
    {
      name: "Workflow Blueprint",
      variant: "tight",
      accent: "oklch(64% 0.19 256)",
      accent2: "oklch(76% 0.10 196)",
      canvas: "oklch(13% 0.052 222)",
      surface: "oklch(19% 0.056 218)",
      surface2: "oklch(24% 0.064 214)",
      surface3: "oklch(30% 0.07 210)",
      line: "oklch(41% 0.064 214)",
      lineStrong: "oklch(54% 0.08 210)",
      title: "SpiritKin Desktop · Workflow Blueprint",
      meta: "当前会话 · 工作流节点 · 运行图",
      chatTitle: "Workflow chat · node explanation",
      confirm: "run_node · rollback_node · archive_workflow_run",
      assistantText: "把工作流节点图固定进右侧管理区，聊天区解释当前节点，3D 模型播报节点阶段。",
      toolText: "collect_context -> plan_patch -> generate_design -> export_png",
      workbench: "Workflow graph",
      module: "Workflows",
      mobile: "移动端只展示当前节点、风险和确认动作。",
      avatar: "Avatar 与 Workflow 节点状态绑定：running、blocked、done。"
    },
    {
      name: "Model Lab Console",
      variant: "tight",
      accent: "oklch(64% 0.19 256)",
      accent2: "oklch(76% 0.10 196)",
      canvas: "oklch(13% 0.045 184)",
      surface: "oklch(19% 0.048 184)",
      surface2: "oklch(24% 0.055 184)",
      surface3: "oklch(30% 0.064 184)",
      line: "oklch(41% 0.06 184)",
      lineStrong: "oklch(54% 0.075 184)",
      title: "SpiritKin Desktop · Model Lab",
      meta: "当前会话 · 模型路由 · Skill 调用",
      chatTitle: "Model collaboration · @Codex / @Claude / @Agent",
      confirm: "select_model · invoke_skill · attach_knowledge",
      assistantText: "模型协作、@Agent、Skill、知识库命中都在 Composer 和右侧管理模块里显性化。",
      toolText: "skill_invoke=product-design · model_route=ui-pro-max · kb_hit=3",
      workbench: "Models / Agents / Skills",
      module: "Models",
      mobile: "多端显示模型路由和待确认项，视觉沿用实验室绿蓝。",
      avatar: "Avatar 代表当前模型协作者的前台运行状态。"
    },
    {
      name: "Knowledge Radar",
      variant: "tight",
      accent: "oklch(64% 0.19 256)",
      accent2: "oklch(76% 0.10 196)",
      canvas: "oklch(13% 0.04 172)",
      surface: "oklch(19% 0.045 176)",
      surface2: "oklch(24% 0.052 180)",
      surface3: "oklch(30% 0.06 184)",
      line: "oklch(41% 0.058 180)",
      lineStrong: "oklch(54% 0.072 178)",
      title: "SpiritKin Desktop · Knowledge Radar",
      meta: "当前会话 · 知识检索 · Context Trace",
      chatTitle: "Grounded chat · context trace visible",
      confirm: "search_web · retrieve_knowledge · attach_context",
      assistantText: "知识库、搜索检索、上下文压缩不再藏到菜单里，右侧给出命中、引用和上下文包状态。",
      toolText: "search_query -> knowledge_hit -> context_pack -> citation_map",
      workbench: "Knowledge / Context",
      module: "Knowledge",
      mobile: "移动端同步当前引用、摘要和下一步问题。",
      avatar: "Avatar 显示正在搜、读、归纳还是等待用户确认。"
    },
    {
      name: "Mobile Bridge Control",
      variant: "tight",
      accent: "oklch(64% 0.19 256)",
      accent2: "oklch(76% 0.10 196)",
      canvas: "oklch(13% 0.048 216)",
      surface: "oklch(19% 0.052 214)",
      surface2: "oklch(24% 0.06 212)",
      surface3: "oklch(30% 0.066 210)",
      line: "oklch(41% 0.062 212)",
      lineStrong: "oklch(54% 0.078 210)",
      title: "SpiritKin Desktop · Mobile Bridge Control",
      meta: "当前会话 · 移动桥接 · 设备同步",
      chatTitle: "Cross-device chat · session continuity",
      confirm: "queue_android_command · push_to_pwa · resume_desktop_session",
      assistantText: "桌面端是完整控制台，移动端只承接确认、同步、命令队列与通知，不拆成另一个产品。",
      toolText: "android_command=queued · pwa_state=resumed · sync_context=complete",
      workbench: "Mobile / Sync",
      module: "Mobile",
      mobile: "Android Bridge、iOS/PWA、Desktop Console 共用同一色彩和状态语义。",
      avatar: "Avatar 的人格与运行态跨端一致，桌面保持最完整控制。"
    },
    {
      name: "Vercel Precision Desk",
      variant: "ops",
      accent: "oklch(64% 0.19 256)",
      accent2: "oklch(76% 0.10 196)",
      canvas: "oklch(10% 0.018 220)",
      surface: "oklch(16% 0.024 220)",
      surface2: "oklch(21% 0.03 220)",
      surface3: "oklch(27% 0.038 220)",
      line: "oklch(38% 0.04 220)",
      lineStrong: "oklch(52% 0.052 220)",
      title: "SpiritKin Desktop · Precision Desk",
      meta: "当前会话 · QA 检查 · 生产诊断",
      chatTitle: "Minimal command chat · no decorative noise",
      confirm: "deploy_preview · capture_screenshot · export_contact_sheet",
      assistantText: "极简黑白方向，靠严谨布局和真实模块密度建立高级感；适合最终产品默认专业主题。",
      toolText: "node --check · playwright_screenshot · image_dimensions_ok",
      workbench: "Production QA",
      module: "Diagnostics",
      mobile: "移动端同步为同一黑白精密系统，只保留关键操作。",
      avatar: "3D 舞台保留真实画面，但 HUD 和边框降到最低。"
    }
  ];

  const sessions = [
    ["生成12版UI设计", "5 条 · 2026/7/10 20:47", "活动"],
    ["@model_deepseek @main", "5 条 · Chats", "活动"],
    ["移动端桥接", "Android queue · PWA", "Sync"],
    ["工作流运行", "node blueprint", "Run"],
    ["安全治理", "permission gate", "Gate"]
  ];

  const modules = ["任务", "工作流", "模块", "技能", "MCP", "移动端", "服务", "日志", "诊断", "模型", "知识库", "上下文", "协作", "集群"];

  function esc(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function vars(c) {
    // atelier(01)的色温由 CSS 的 .skin-atelier / .skin-atelier.day 完全驱动，
    // 不注入 inline 色值，否则 inline 会压过昼夜切换的类。
    if (c.skin === "atelier") return "";
    return [
      ["--accent", c.accent],
      ["--accent-2", c.accent2],
      ["--canvas", c.canvas],
      ["--surface", c.surface],
      ["--surface-2", c.surface2],
      ["--surface-3", c.surface3],
      ["--line", c.line],
      ["--line-strong", c.lineStrong],
      ["--text", c.text],
      ["--muted", c.muted],
      ["--faint", c.faint]
    ]
      .filter(([, value]) => value)
      .map(([name, value]) => `${name}:${value}`)
      .join(";");
  }

  function renderTitlebar(c) {
    return `
      <div class="titlebar">
        <div class="title-left">
          <span class="app-dot"></span>
          <span class="title-name">SpiritKin Desktop</span>
          <div class="menu-row"><span>文件</span><span>编辑</span><span>视图</span><span>窗口</span><span>帮助</span></div>
        </div>
        <div class="title-center">
          <span>${esc(c.name)}</span>
          <strong>${esc(c.title)}</strong>
        </div>
        <div class="title-right">
          <span class="kpi">FPS N/A</span>
          <span class="kpi">GPU 8%</span>
          <span class="kpi">CPU 47%</span>
          <div class="window-controls"><span>□</span><span>−</span><span>×</span></div>
        </div>
      </div>
    `;
  }

  function renderSidebar(c) {
    return `
      <aside class="sidebar">
        <div class="sidebar-head">
          <span class="brand-bar"></span>
          <div>
            <div class="brand-title">SpiritKin${c.badge ? `<span class="brand-badge">${esc(c.badge)}</span>` : ""}</div>
            <div class="brand-subtitle">桌面工作台 · D:\\SpiritKinAI</div>
          </div>
          <span class="sync-dot"></span>
        </div>
        <div class="nav-row">
          <div class="nav-item active">快速会话</div>
          <div class="nav-item">管理</div>
          <div class="small-muted">会话显示 · 活动</div>
        </div>
        <div class="sidebar-scroll">
          <div class="section-line">
            <span>Projects · 活动 0</span>
            <span class="side-actions"><span class="icon-btn">...</span><span class="icon-btn">⌄</span><span class="icon-btn">+</span></span>
          </div>
          <div class="side-list">
            <div class="side-card active"><div class="side-title">生成12版UI设计</div><div class="side-meta">当前桌面端优先调整</div></div>
          </div>
          <div class="section-line">
            <span>Chats · 活动 7</span>
            <span class="side-actions"><span class="icon-btn">...</span><span class="icon-btn">⌄</span><span class="icon-btn">+</span></span>
          </div>
          <div class="side-list">
            ${sessions.map((s, i) => `
              <div class="side-card ${i === 1 ? "active" : ""}">
                <div class="side-title">${esc(s[0])}</div>
                <div class="side-meta">${esc(s[1])} · ${esc(s[2])}</div>
              </div>
            `).join("")}
          </div>
        </div>
        <div class="backend-box">
          <div class="small-muted">后端功能映射保留</div>
          <div class="backend-chip"><span>chat</span><code>append_message</code></div>
          <div class="backend-chip"><span>workflow</span><code>start_workflow_run</code></div>
          <div class="backend-chip"><span>3D</span><code>avatar_state_event</code></div>
          <div class="backend-chip"><span>mobile</span><code>queue_android_command</code></div>
        </div>
      </aside>
    `;
  }

  function renderChat(c) {
    return `
      <main class="chat">
        <header class="chat-head">
          <div>
            <div class="chat-title">${esc(c.chatTitle)}</div>
            <div class="chat-meta">
              <span>5 条 · 更新 2026/7/10</span>
              <span class="member-chip">Spirit</span>
              <span class="member-chip">模型 Deepseek</span>
            </div>
          </div>
          <div class="pill-row">
            <span class="tiny-btn">...</span>
            <span class="status-pill ok">安全正常</span>
            <span class="status-pill danger">停止</span>
            <span class="status-pill ok">实时已连接</span>
          </div>
        </header>
        <section class="confirm-bar">
          <div>
            <div class="confirm-title">等待确认 / Permission Gate</div>
            <div class="confirm-copy">${esc(c.confirm)}</div>
          </div>
          <button class="primary-btn">确认执行</button>
        </section>
        <section class="message-pane">
          <article class="message user">
            <div class="msg-name">User</div>
            <div class="msg-text">3D 模型、聊天区域、管理模块不能丢；桌面端优先，其他端同风格。</div>
          </article>
          <article class="message assistant">
            <div class="msg-name">Spirit</div>
            <div class="msg-text">${esc(c.assistantText)}</div>
            <div class="timeline-row">
              <span class="timeline-pill thinking">Thinking</span>
              <span class="timeline-pill reading">Reading</span>
              <span class="timeline-pill editing">Editing</span>
              <span class="timeline-pill done">Done</span>
            </div>
          </article>
          <article class="stream-card">
            <div class="stream-head">
              <span>Streaming Output / Runtime Delta</span>
              <span class="stream-status">tokens 1,428 · tool events 4 · artifacts 2</span>
            </div>
            <div class="stream-line">
              <span class="stream-token">assistant.delta</span>
              正在整合桌面端布局、后端函数链路、流式输出事件和 3D 舞台状态，保留真实产品功能层级。
              <span class="stream-caret"></span>
            </div>
            <div class="backend-chain">
              <div class="backend-step"><strong>append_message</strong><span>chat stream</span></div>
              <div class="backend-step"><strong>start_workflow_run</strong><span>node graph</span></div>
              <div class="backend-step"><strong>record_artifact</strong><span>preview png</span></div>
              <div class="backend-step"><strong>avatar_state_event</strong><span>3D HUD</span></div>
            </div>
          </article>
          <article class="message tool">
            <div class="msg-name">Runtime tool stream</div>
            <div class="msg-text">${esc(c.toolText)}</div>
          </article>
        </section>
        <section class="artifact-strip">
          <div>
            <div class="artifact-title">Artifacts / Inline preview</div>
            <div class="artifact-meta">desktop_wpf_current.png · avatar_3d_stage_clean.png · streaming event snapshots</div>
          </div>
          <span class="status-pill warn">2 pending</span>
        </section>
        <section class="composer">
          <div class="prompt">发送给 Runtime 的文本，例如：打开管理模块、让 3D 模型播报当前阶段、批准 Android 命令队列。</div>
          <div class="composer-bottom">
            <span class="quick-chip">模型协作</span>
            <span class="quick-chip">联网检索</span>
            <span class="quick-chip">+</span>
            <span class="quick-chip">@ Agent</span>
            <span class="quick-chip">完全访问</span>
            <span class="quick-chip">Quick commands</span>
            <button class="send">发送</button>
          </div>
        </section>
      </main>
    `;
  }

  function renderGraph(c) {
    return `
      <div class="graph">
        <span class="link" style="left:18%;top:38%;width:32%;transform:rotate(10deg)"></span>
        <span class="link" style="left:48%;top:44%;width:30%;transform:rotate(24deg)"></span>
        <span class="link" style="left:27%;top:76%;width:36%;transform:rotate(-27deg)"></span>
        <div class="node" style="left:8%;top:18%">Chat<small>append</small></div>
        <div class="node" style="left:38%;top:27%">${esc(c.module)}<small>active</small></div>
        <div class="node" style="left:65%;top:54%">3D<small>stage</small></div>
        <div class="node" style="left:25%;top:68%">Mobile<small>sync</small></div>
      </div>
    `;
  }

  function renderWorkbench(c) {
    return `
      <aside class="workbench">
        <div class="workbench-top">
          <div class="workbench-title">桌面状态</div>
          <span class="tiny-btn">▤</span>
        </div>
        <section class="workbench-body">
          <div class="status-card">
            <div class="card-title">${esc(c.workbench)}</div>
            <div class="card-subtitle">D:\\SpiritKinAI</div>
            <div class="stat-grid">
              <div class="stat"><div class="stat-value">127.0.0.1:8788</div><div class="stat-label">命令网关</div></div>
              <div class="stat"><div class="stat-value">127.0.0.1:8765</div><div class="stat-label">事件通道</div></div>
              <div class="stat"><div class="stat-value">WPF</div><div class="stat-label">official desktop</div></div>
              <div class="stat"><div class="stat-value">Live</div><div class="stat-label">avatar runtime</div></div>
            </div>
          </div>
          <div class="management-card">
            <div class="card-title">管理模块</div>
            <div class="module-row">
              ${modules.map(m => `<span class="module-pill ${m === c.module ? "active" : ""}">${esc(m)}</span>`).join("")}
            </div>
            ${renderGraph(c)}
            <div class="event-list">
              <div class="event">worker_heartbeat · coding-agent</div>
              <div class="event">record_artifact · design PNG</div>
              <div class="event">sync_context · desktop/mobile</div>
            </div>
            <div class="mobile-sync">
              <div class="phone-mini"><i></i><i></i><i></i><i></i></div>
              <div class="sync-text">
                <strong>多端同风格</strong>
                <span>${esc(c.mobile)}</span>
              </div>
            </div>
          </div>
        </section>
        <div class="avatar-splitter"></div>
        <section class="avatar-pane">
          <div class="avatar-head">
            <div>
              <div class="avatar-title">3D 模型</div>
              <div class="avatar-sub">桌面嵌入视图 · Avatar Runtime</div>
            </div>
            <div class="stage-chip-row"><span class="stage-chip">LIVE</span><span class="stage-chip">HUD</span></div>
          </div>
          <div class="avatar-stage">
            <img src="./assets/avatar_3d_stage_clean.png" alt="SpiritKin 3D Avatar stage" />
            <div class="avatar-hud">
              <div class="hud-row">
                <span class="hud">phase: idle</span>
                <span class="hud">emotion: neutral</span>
                <span class="hud">speech: idle</span>
              </div>
              <div></div>
              <div class="subtitle">${esc(c.avatar)}</div>
            </div>
          </div>
        </section>
      </aside>
    `;
  }

  function renderStageDesktop(c) {
    return `
      <section class="stage-desktop">
        ${renderTitlebar(c)}
        <section class="stage-grid">
          <aside class="stage-rail">
            <div class="stage-brand">
              <span class="brand-bar"></span>
              <div>
                <div class="brand-title">SpiritKin<span class="brand-badge">${esc(c.badge || "06")}</span></div>
                <div class="brand-subtitle">Desktop-first stage</div>
              </div>
            </div>
            <div class="stage-session">
              <div class="stage-label">Live Session</div>
              <strong>@model_deepseek @main</strong>
              <span>5 条 · Companion Relay Desk · D:\\SpiritKinAI</span>
            </div>
            <div class="stage-nav">
              <button class="stage-nav-item active">Stage</button>
              <button class="stage-nav-item">Chat</button>
              <button class="stage-nav-item">Motion</button>
              <button class="stage-nav-item">Memory</button>
            </div>
            <div class="stage-panel">
              <div class="stage-panel-head">
                <span>Runtime Queue</span>
                <strong>04</strong>
              </div>
              <div class="stage-step complete"><span>01</span><strong>load_model</strong><em>ready</em></div>
              <div class="stage-step active"><span>02</span><strong>avatar_speak</strong><em>armed</em></div>
              <div class="stage-step"><span>03</span><strong>subtitle_on</strong><em>live</em></div>
              <div class="stage-step"><span>04</span><strong>sync_mobile</strong><em>idle</em></div>
            </div>
            <div class="stage-memory">
              <span class="stage-label">Memory Context</span>
              <strong>当前任务：桌面端 06 视觉改稿</strong>
              <small>聊天、审批、管理模块和 3D 舞台全部保持同屏。</small>
            </div>
            <div class="stage-safety">
              <span class="status-pill ok">安全正常</span>
              <span class="status-pill warn">2 pending</span>
            </div>
          </aside>

          <main class="stage-main">
            <section class="mission-surface">
              <section class="hero-stage mission-avatar">
                <img src="./assets/avatar_3d_stage_clean.png" alt="SpiritKin 3D Avatar stage" />
                <div class="stage-light top"></div>
                <div class="stage-light floor"></div>
                <div class="stage-director-bar">
                  <div>
                    <span>LIVE AVATAR RUNTIME</span>
                    <strong>Companion Relay Desk</strong>
                  </div>
                  <em>voice idle · subtitles on · mobile synced</em>
                </div>
                <div class="stage-hud top-left">
                  <span>phase: idle</span>
                  <span>emotion: neutral</span>
                  <span>speech: idle</span>
                </div>
                <div class="stage-caption">
                  <strong>Avatar Runtime</strong>
                  <span>${esc(c.avatar)}</span>
                </div>
              </section>

              <section class="conversation-stage">
                <div class="conversation-mode">
                  <button class="active" type="button">Relay</button>
                  <button type="button">Chat Focus</button>
                  <button type="button">Stage</button>
                </div>
                <header class="conversation-head">
                  <span>Linked conversation</span>
                  <strong>对话不再被压成字幕</strong>
                  <small>每条消息都能触发 Avatar cue、工具调用或人工确认。</small>
                </header>
                <div class="conversation-thread">
                  <article class="thread-message user">
                    <span>User</span>
                    <p>3D 模型、聊天区域、管理模块不能丢；桌面端优先，其他端同风格。</p>
                  </article>
                  <article class="thread-message assistant">
                    <span>Spirit</span>
                    <p>${esc(c.assistantText)}</p>
                  </article>
                  <article class="stream-mini">
                    <span>Runtime delta</span>
                    <p>${esc(c.toolText)}</p>
                  </article>
                </div>
                <div class="artifact-mini">
                  <span>Latest evidence</span>
                  <strong>06-companion-relay-desk.png</strong>
                  <em>visual QA · text overflow clear</em>
                </div>
              </section>
            </section>

            <section class="run-pulse-board">
              <div class="pulse-card primary">
                <span>Next cue</span>
                <strong>播报当前设计判断</strong>
                <em>avatar_speak · subtitle_on</em>
              </div>
              <div class="run-line">
                <i class="done"></i>
                <i class="active"></i>
                <i></i>
                <i></i>
              </div>
              <div class="pulse-card">
                <span>Decision</span>
                <strong>保留舞台，恢复聊天</strong>
                <em>balanced mode</em>
              </div>
              <div class="pulse-card">
                <span>Management</span>
                <strong>证据 / 移动 / 工具归右栏</strong>
                <em>workflow · mobile · artifacts</em>
              </div>
              <button class="stage-primary">确认执行</button>
            </section>
          </main>

          <aside class="stage-command">
            <section class="command-card confirm">
              <div>
                <span class="stage-label">Permission Gate</span>
                <strong>等待确认</strong>
                <p>${esc(c.confirm)}</p>
              </div>
              <button class="primary-btn">批准</button>
            </section>
            <section class="command-card decision-timeline">
              <div class="stage-label">Decision Timeline</div>
              <div class="decision-step complete">
                <b>01</b>
                <strong>Opus 01 reviewed</strong>
                <span>对话优先、克制、长期可读</span>
              </div>
              <div class="decision-step active">
                <b>02</b>
                <strong>06 v2 balanced</strong>
                <span>舞台和聊天并排，降低秀场感</span>
              </div>
              <div class="decision-step">
                <b>03</b>
                <strong>Next selection</strong>
                <span>从可用产品方向里挑主线</span>
              </div>
            </section>
            <section class="command-card management-modules">
              <div class="stage-label">Management Modules</div>
              <div class="module-lattice">
                <span class="active">3D 模型</span>
                <span>工作流</span>
                <span>移动端</span>
                <span>产物</span>
                <span>日志</span>
                <span>诊断</span>
              </div>
              <div class="mini-signal-graph">
                <i class="node-a">Chat</i>
                <i class="node-b">Stage</i>
                <i class="node-c">Mobile</i>
                <i class="node-d">Tools</i>
              </div>
            </section>
            <section class="command-card toolchain">
              <div class="stage-label">Runtime Tool Stream</div>
              <div class="tool-line"><strong>load_model</strong><span>complete</span></div>
              <div class="tool-line"><strong>runtime_stage</strong><span>active</span></div>
              <div class="tool-line"><strong>subtitle_on</strong><span>live</span></div>
              <div class="tool-line"><strong>motion_queue</strong><span>idle</span></div>
            </section>
            <section class="stage-composer">
              <div>发送给 Runtime：调整表情、播报阶段、批准移动端同步。</div>
              <button class="send">发送</button>
            </section>
          </aside>
        </section>
      </section>
    `;
  }

  function render(c) {
    const light = c.variant === "light" ? " variant-light" : "";
    const skin = c.skin ? ` skin-${esc(c.skin)}` : "";
    return `
      <main class="viewport${light} variant-${esc(c.variant)}${skin}" style="${vars(c)}">
        ${c.skin === "stage" ? renderStageDesktop(c) : `
          <section class="desktop">
            ${renderTitlebar(c)}
            <section class="workspace">
              ${renderSidebar(c)}
              <div class="splitter"></div>
              ${renderChat(c)}
              <div class="splitter"></div>
              ${renderWorkbench(c)}
            </section>
          </section>
        `}
      </main>
    `;
  }

  function init() {
    const params = new URLSearchParams(location.search);
    const index = Math.max(0, Math.min(concepts.length - 1, Number(params.get("i") || 0)));
    const concept = concepts[index];
    document.title = `${String(index + 1).padStart(2, "0")} ${concept.name}`;
    const app = document.getElementById("app");
    app.innerHTML = render(concept);

    // atelier(01) 昼夜双色温：夜里=暖炉(默认)，白天=清醒冷光。?theme=day 直接进白天。
    // 化身炉火是编辑室里的常驻活体存在(见 avatar-stage 的 hearthBreath 呼吸)，不再做可切换的主场模式。
    if (concept.skin === "atelier") {
      const viewport = app.querySelector(".viewport");
      const startDay = params.get("theme") === "day";
      const applyTheme = (isDay) => {
        viewport.classList.toggle("day", isDay);
        // 色温状态提升到外层背景与 body,避免白天视口浮在深藏青"玻璃幕"上。
        app.classList.toggle("atelier-day", isDay);
        document.body.classList.toggle("atelier-day", isDay);
        const btn = app.querySelector(".daynight-toggle");
        if (btn) btn.textContent = isDay ? "☀ 白天" : "🌙 夜里";
      };
      const toggle = document.createElement("button");
      toggle.className = "daynight-toggle";
      toggle.type = "button";
      toggle.addEventListener("click", () => applyTheme(!viewport.classList.contains("day")));
      viewport.appendChild(toggle);
      applyTheme(startDay);
    }
  }

  window.SpiritKinDesktopPriorityConcepts = concepts;
  init();
})();
