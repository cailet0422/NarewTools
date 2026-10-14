# NarewTools — Islet Online 模组工具包

[한국어](README.md) · [English](README.en.md) · **简体中文** · [繁體中文](README.zh-TW.md) · [日本語](README.ja.md)

通过逆向分析 Islet Online（아일렛 온라인）的自研引擎 **NarewEngine2** 而制作的 Windows 10/11 模组工具包。
无需直接改动游戏文件，即可用**模组**添加方块、家具、物品、音效，修改贴图、GUI 界面、声音、模型和世界（地图），
再通过模组管理器放入游戏，并随时恢复原版。还附带一个 MCP 服务器，让 AI 智能体（Claude Code 等）直接使用同样的功能。

**[⬇ 下载最新版本（Releases）](https://github.com/cailet0422/NarewTools/releases/latest)**

![在 NarewStudio 世界编辑器中打开自己的世界](docs/images/studio-world.png)
<sub>在世界编辑器中打开的单人世界。这座城堡是 AI 智能体仅用 NarewStudio 的工具建造的。</sub>

> [!IMPORTANT]
> 这是非官方的粉丝工具，与游戏开发商、发行商无关。本仓库和发布包中**不包含任何游戏文件**。
> 需要已正版安装的游戏才能使用。游戏带有联网功能，请在单人模式（自己的世界）中测试，并自行确认游戏的用户协议。
> 应用模组前会自动备份原文件，但使用风险由用户自行承担。

> [!NOTE]
> 程序界面目前**仅支持韩语**。本文中按钮和菜单名称写成"韩语原文（中文翻译）"，例如 **새 모드**（新建模组），方便在界面上查找。
> `docs/` 中的技术文档为韩语。

## 目录

- [组成](#组成)
- [功能](#功能)
- [系统要求](#系统要求)
- [安装](#安装)
- [首次设置](#首次设置)
- [快速入门：制作第一个模组](#快速入门制作第一个模组)
- [NarewStudio 使用方法](#narewstudio-使用方法)
- [NarewModManager 使用方法](#narewmodmanager-使用方法)
- [模组格式](#模组格式)
- [连接 AI 智能体](#连接-ai-智能体)
- [命令行工具（narew）](#命令行工具narew)
- [从源码运行 · 构建 · 测试](#从源码运行--构建--测试)
- [文件位置](#文件位置)
- [常见问题](#常见问题)
- [游戏内验证状态与已知限制](#游戏内验证状态与已知限制)
- [目录结构与技术文档](#目录结构与技术文档)
- [许可证](#许可证)

## 组成

| 程序 | 作用 |
|---|---|
| **NarewStudio.exe** | 制作模组。游戏文件**只读**，修改的文件和新增的条目都保存到模组项目中 |
| **NarewModManager.exe** | 启用/禁用模组、调整顺序、应用到游戏、恢复原版 |
| **NarewAgent.exe** | AI 智能体连接程序（控制台）。通过 MCP 和命令行把 NarewStudio 的功能提供为 111 个工具 |
| `narew`（Python） | 命令行工具：解包/打包 .pack、模型 ↔ glTF、方块（体素）文件转换等（从源码运行） |

## 功能

**NarewStudio**

| 页面 | 功能 |
|---|---|
| 리소스 편집（资源编辑） | 浏览、搜索 20 个游戏包 + `Language\` 中的所有文件。图片（原版/修改版对比，自动转换为原格式和尺寸，调色）、声音、文本·XML（保留编码，语法高亮）、字体、十六进制查看。拖入文件即可替换 |
| 블록 텍스처（方块贴图） | 按格替换、调色 `terrain.png` 方块图集，显示空格 |
| 가구 텍스처（家具贴图） | 替换 936 种家具模型的各材质贴图 + 3D 预览 |
| 새 리소스 추가（添加新内容） | **新方块 / 新家具 / 新物品 / 新音效** 向导（自动计算空闲编号、自动生成图标、合成配方） |
| GUI 편집（GUI 编辑） | 用真实游戏图片显示 169 个界面布局：移动、调整大小、修改文字和颜色，添加新图片、按钮、文字 |
| 사운드 편집（音效编辑） | 波形显示，剪切/粘贴/淡入淡出/音量/标准化/倒放/变速，按原格式保存 |
| 월드 에디터（世界编辑器） | 3D 编辑世界地图（`.bwr`）和建筑预制件（`.iaf`），摆放家具，**打开/捕获自己的世界（游戏存档）并放回游戏** |
| 모델 뷰어（模型查看器） | 3,633 个模型和动画播放，组合角色部件，导出/导入 glTF（Blender） |
| 팩 탐색기（包浏览器） | 解包、校验、打包 `.pack` 文件 |
| 내 모드（我的模组） | 修改列表与撤销、安装到模组管理器、生成用于分发的 .zip |

**NarewModManager**：安装模组（拖入 zip）、启用/禁用、优先级、冲突显示、XML 表**按条目合并**（多个模组向同一张表添加条目也都会生效）、应用到游戏 / 恢复原版、检测 Steam 更新。

**AI 智能体**：可以直接处理"在出生点旁边盖一间小屋，放一张床"、"把背包关闭按钮移到左下角"之类的请求。智能体用 ASCII 地图和图片检查结果，打开着的 NarewStudio 窗口会立即重新读取改动。

### 截图

| 模型查看器 —— 组合角色部件、播放动画 | GUI 编辑 —— 用真实游戏图片编辑界面布局 |
|---|---|
| ![模型查看器](docs/images/studio-models.png) | ![GUI 编辑](docs/images/studio-gui.png) |
| **音效编辑 —— 波形与区间编辑** | **添加新内容 —— 新家具** |
| ![音效编辑](docs/images/studio-sound.png) | ![新家具](docs/images/studio-create.png) |
| **家具贴图 —— 各材质贴图与 3D 预览** | **方块贴图 —— 方块图集** |
| ![家具贴图](docs/images/studio-furniture.png) | ![方块贴图](docs/images/studio-blocks.png) |

## 系统要求

- Windows 10 / 11（64 位）
- 已安装 Islet Online（游戏文件夹 = 包含 `IsletClient_release(x64).exe` 的文件夹）
- 3D 画面（世界编辑器、模型查看器、家具预览）需要支持 **OpenGL 3.3** 的显卡驱动。没有也可以使用其他功能。
- 仅从源码运行时：Python 3.10 或更高版本（已在 3.12 上验证）

## 安装

1. 从 [Releases](https://github.com/cailet0422/NarewTools/releases/latest) 下载 `NarewTools-<版本>.zip`。
2. 解压到任意位置（例如 `C:\Tools\`）。无需安装。
3. 运行 `NarewTools\NarewStudio.exe`。

```
NarewTools\
  NarewStudio.exe        制作模组
  NarewModManager.exe    应用 / 恢复模组
  NarewAgent.exe         AI 智能体用（控制台）
  사용법.txt              简要说明（韩语）
  _internal\             三个程序共用的 Qt/Python 运行库 —— 请勿删除或单独移动
```

- 三个 exe 共用 `_internal` 文件夹，移动时请**整个 `NarewTools` 文件夹**一起移动。
- 程序没有数字签名，Windows SmartScreen 可能会显示"Windows 已保护你的电脑"。点击 **更多信息 → 仍要运行** 即可。
- **自动更新**（2.2.0 起）：NarewStudio 和模组管理器启动时会检查新版本（最多每 6 小时一次，可关闭），并显示更新内容。点击 **업데이트**（更新）会下载并校验 SHA-256，在关闭程序时替换为新文件并重新启动。替换前的文件备份到 `%LOCALAPPDATA%\NarewTools\update\backup-<版本>\`，出错时恢复原文件。也可以点击标题栏右侧的版本按钮（`v2.3.0`）立即检查。放在 `Program Files` 等不可写位置的文件夹无法自动更新，请移到"文档"等位置。
- 2.1.0 及以下版本请直接替换整个文件夹。设置、模组项目和已安装的模组保存在用户文件夹中，不受影响（见[文件位置](#文件位置)）。

## 首次设置

### 1. 选择游戏文件夹

首次运行 NarewStudio 和 NarewModManager 时会要求选择游戏文件夹。请选择包含 `IsletClient_release(x64).exe` 的文件夹。

- Steam 默认位置：`C:\Program Files (x86)\Steam\steamapps\common\Islet Online`
- 安装在其他库时：`<库>\steamapps\common\Islet Online`
- 不确定时：在 Steam 库中右键点击游戏 → **管理 → 浏览本地文件**，打开的就是游戏文件夹。

两个程序和 AI 智能体共用同一份设置。之后要更改，在任一程序中重新选择游戏文件夹即可。

### 2. 创建模组项目

点击 NarewStudio 顶部的 **새 모드**（新建模组），填写 모드 이름（模组名称）、만든 사람（作者）、버전（版本）、설명（说明）和 저장 위치（保存位置）。
项目默认保存在 `Documents\NarewStudio\Projects\`（"文档"文件夹）下。之后的所有编辑只会进入这个项目，游戏文件夹不会被改动。

### 3. 检查

- 如果 3D 画面全黑或只显示提示文字，请把显卡驱动更新到最新（需要 OpenGL 3.3）。
- 出错时请查看 `%APPDATA%\NarewStudio\log.txt` 和 `%LOCALAPPDATA%\NarewModManager\log.txt`。

## 快速入门：制作第一个模组

示例：添加一个新方块并在游戏中查看。

1. **NarewStudio** → **새 모드**（新建模组）创建项目。
2. 左侧 **새 리소스 추가**（添加新内容）→ **새 블록**（新方块）标签。
   - 填写名称和说明，选择空闲的方块编号（会自动推荐）以及要复制属性的相似方块。
   - 放入贴图（所有面相同，或分别指定顶/侧/底）。会自动调整为 128×128。
   - 需要的话添加合成配方（在材料栏输入物品名称或编号的一部分会弹出列表）。
   - 点击创建后，图集贴图、方块表条目、方块物品、名称、图标和配方都会加入模组。
3. 左侧 **내 모드**（我的模组）→ **모드 매니저에 설치**（安装到模组管理器）。
4. **在游戏关闭的状态下**打开 **NarewModManager**，勾选模组并点击 **게임에 적용**（应用到游戏）。
5. 启动游戏确认。
6. 要撤销时，关闭游戏，在 NarewModManager 中点击 **원본 복구**（恢复原版）。

## NarewStudio 使用方法

### 리소스 편집（资源编辑）

- 在左侧树中浏览包和文件夹，或在上方搜索框输入文件名的一部分。
- **图片**：原版与修改版并排对比。拖入新图片会自动转换为原格式和尺寸。可调整色相、饱和度、亮度、对比度和着色。
- **文本 · XML · 着色器**：保留原编码（EUC-KR 等）、BOM 和换行符进行编辑。语法高亮、检查、查找。
- **声音**：播放、以相同格式替换。精细编辑请用"사운드 편집기로 열기"（在音效编辑器中打开）。
- **외부 편집기로 열기**（用外部编辑器打开）：可在 Photoshop、记事本等中修改后应用。
- 修改过的文件进入模组的 `files\`。可在 **내 모드**（我的模组）页面逐个撤销。

### 블록 텍스처 · 가구 텍스처（方块贴图 · 家具贴图）

- **方块贴图**：`terrain.png`（颜色）和 `terrain_specular.png`（高光）16×16 格图集。选中一格会显示使用它的方块，可替换图片（自动适配 128×128）、调色或恢复原版。
- **家具贴图**：选择家具后显示模型各材质的贴图和 3D 预览。只关联游戏实际读取的贴图。

### 새 리소스 추가（添加新内容）

| 标签 | 输入 | 加入模组的内容 |
|---|---|---|
| 새 블록（新方块） | 名称·说明（韩/英）、空闲方块编号、相似方块（复制硬度、采集工具、分类）、贴图（所有面 / 顶·侧·底）、高光（可选）、配方（可选） | 图集贴图、方块表条目、方块物品、名称、自动图标、配方 |
| 새 가구（新家具） | 名称·说明、空闲家具编号、物品编号、功能模板（复制坐、躺、照明等行为的家具）、模型（**复制现有家具 + 换贴图** 或 **导入 glTF**）、图标（图片或 3D 预览截图）、配方 | `.nwmt` 模型（+贴图）、家具表条目、物品、名称、图标、配方 |
| 새 아이템（新物品） | 名称·说明、物品编号、模板物品（食物、药水、材料、工具等 —— 效果和数值沿用模板）、图标、配方 | 物品表条目、名称、图标、配方 |
| 새 사운드（新音效） | 声音文件（.wav/.ogg/.mp3）、名称、用作背景音乐时的天气·时间和音量，或用作 UI 音效的位置 | 声音文件、`sky.xml` / `Gui\sound.xml` 条目 |
| 만든 것（已创建） | — | 本模组添加/修改的条目列表，可逐条移除 |

引擎接受的编号范围（以 2026-10 的游戏为准）：

| 类型 | 可用编号 | 备注 |
|---|---|---|
| 方块 | 149、160–170、192–239 | 171–191 被旧预制件使用，避开 |
| 家具 | 58、59、597–599、942–1023 | 引擎上限 1–1023 |
| 物品 | 建议 19300–19999 | 引擎上限 1–19999。方块物品的编号与方块编号相同 |
| 合成配方 | 从 2599 往下 | 引擎上限 ≤ 2599 |

NarewStudio 每次打开模组时都会重新计算空闲编号。游戏更新后，请重新打开模组，确认编号没有与官方内容冲突。

### GUI 편집（GUI 编辑）

- 用真实游戏图片绘制游戏界面布局（`gui.pack` 中的 `Gui\*.xml`）。
- 点击元素拖动，或用**方向键**移动（Shift = 10px）。**Ctrl+点击** 或在空白处拖动可多选。
- 输入位置和尺寸数值，修改文字、字号、颜色，用 **＋ 이미지 / 버튼 / 글자**（+ 图片 / 按钮 / 文字）添加新元素（游戏图片或自己的 PNG），按 **Delete** 删除。
- 预览屏幕尺寸（1920×1080 / 1600×900 / 1280×720）、显示隐藏元素、直接编辑 XML、撤销。
- 按钮图片可通过"그림 편집"（编辑图片）直接跳转到资源编辑。只改写变动的数值，保持原文件格式。

### 사운드 편집（音效编辑）

- 显示并播放游戏声音（WAV、OGG、MP3）的波形。拖动（或 Shift+点击）选择区间，滚轮缩放。
- 剪切、复制、粘贴、仅保留选区、静音、淡入/淡出、音量（dB）、标准化、倒放、变速，撤销/重做。
- 从其他文件导入（自动匹配采样率和声道），导出为 WAV/OGG/MP3。
- **모드에 저장**（保存到模组）会保持原格式、采样率和声道。

### 월드 에디터（世界编辑器）

以 3D 方式编辑游戏地图（`.bwr`）和建筑预制件（`.iaf`）。

| 操作 | 按键 |
|---|---|
| 移动 | W A S D，上/下 Q E |
| 旋转 / 平移 / 缩放 | 左键拖动 / 中键拖动 / 滚轮 |
| 撤销 / 重做 / 保存 | Ctrl+Z / Ctrl+Y / Ctrl+S |
| 家具旋转 90° | R |
| 旋转已放置的家具 / 开关灯 / 删除（"✋ 설치물 편집"） | R / T / Delete |

- 工具：查看、放置、删除、涂色、吸管、填充/挖空长方体、粘贴预制件、**图片像素画**、**放置家具**、**编辑家具**、出生位置、删除对象。
- **放置家具**：在右侧"설치물"（家具）标签中选择家具后点击。绿色 = 可放置，红色 = 被挡住。家具下方的隐藏碰撞格会自动填充。灯具放置时为打开状态。
- **编辑家具**（"✋ 설치물 편집"）：点击已放置的家具将其选中（浅蓝色方框），按 **R** 以其所在格为中心旋转 90°（碰撞格一起移动，箱子里的物品保留），按 **T** 开关灯，按 **Delete** 删除。灯的标记：黄色 = 开，灰色 = 关。关着的灯在游戏中不发光。
- **图片像素画**（"🖼 이미지 고르기…"）：选择图片文件（或剪贴板中的图片），设置大小、方向（朝四个方向之一的墙或地面）、双面、颜色加深、抖动以及要使用的方块，并查看预览。点击地图后，图片底边的中点会立在该格（墙为正面，地面为中心）。每个像素使用颜色最接近的方块；默认不使用花纹明显的方块，透明像素保持原样。双面墙从背面看文字也是正的。撤销一次即可删除。
- 大地图只加载镜头周围（可调节视距）。点击小地图可跳转。

**编辑自己的世界**（单人世界）

1. 在 **💾 내 월드**（我的世界）中打开游戏存档（`<游戏文件夹>\Local\<世界>.bwr`）。打开的是游戏最后一次保存的状态。
2. 修改后点击 **저장**（保存）→ 原文件保持不变，作为新文件保存到 `Documents\NarewStudio\Worlds\`。
3. 打开该文件，在**游戏关闭的状态下**点击 **🎮 내 월드에 적용**（应用到我的世界）→ 用这个文件替换游戏存档。
   - 替换前会先把当前存档（`.bwr .lnd .npc .db`）备份到 `Documents\NarewStudio\WorldBackups\<世界>_<时间>\`。
   - 点击 **↩ 백업으로**（恢复备份）可随时还原。
   - 游戏运行中会拒绝执行。如果游戏的保存时间晚于修改后的文件，会给出警告。

**世界捕获** —— 把正在玩的世界保存为 `.bwr`

- **📸 현재 월드 저장**（保存当前世界）或 **캡처 창**（捕获窗口，始终置顶的小窗口）。游戏中也可以按 **Ctrl+Alt+S** 保存。
- 开启"게임이 저장할 때마다 자동으로 남기기"（游戏每次保存时自动保留）后，游戏每保存一次就保留一份。
- 边玩边只显示捕获窗口：`NarewStudio.exe --capture`。
- 保存的是**游戏最后一次保存的状态**（不向游戏注入代码）。只能捕获保存在自己电脑上的世界。

### 모델 뷰어 · Blender（模型查看器）

- 按类型浏览模型并播放动画。角色可双击衣服、头发、脸、鞋等部件，组合到同一场景中。
- **导出为 glTF** → 在 Blender 中修改 → **从 glTF 导入**，即可修改模型和动画。也支持添加新动画和修改播放速度。
- Blender 提示：导入前把场景帧率设为 **30 fps**，导出时开启 **Include → Custom Properties**（保留原始数据）。

### 팩 탐색기 · 내 모드（包浏览器 · 我的模组）

- **包浏览器**：包列表、文件详情（压缩、MD5）、解包单个包/文件夹/全部（MD5 校验）、从文件夹打包。
- **我的模组**：模组信息和预览图、修改文件与表合并列表（逐条撤销）、**모드 매니저에 설치**（安装到模组管理器）、**배포용 .zip 만들기**（生成分发用 .zip）。

## NarewModManager 使用方法

1. 选择游戏文件夹（与 NarewStudio 共用设置）。
2. 安装模组：把模组 `.zip` 拖入窗口 / **＋ .zip 설치…**（安装 .zip）/ **폴더에서 설치…**（从文件夹安装）/ NarewStudio 中的 **모드 매니저에 설치**。
3. 勾选以启用，拖动或用 ▲▼ 调整顺序（**越靠上优先级越高**）。右侧可查看模组的文件、表合并以及被其他模组覆盖的文件。
4. **在游戏关闭的状态下**点击 **게임에 적용**（应用到游戏）。要撤销请点 **원본 복구**（恢复原版）。

![NarewModManager —— 已安装的模组与优先级](docs/images/modmanager.png)

应用方式：

| 方式 | 说明 |
|---|---|
| 包补丁（默认，推荐） | 首次把原版包保存为 `data\<包>.pack.orig`，之后每次应用都用原版 + 已启用的模组重新生成包。未改动的文件直接复制，不重新压缩 |
| 散文件（实验性） | 把模组文件以散文件形式放到游戏文件夹中，不改动包。仅验证过 GUI 图片 |

- **表合并**：按优先级从低到高依次应用已启用模组的 `merge\` 补丁。两个模组修改同一条目时，靠上的模组生效。保留原编码和换行符。
- 游戏文件夹中的散数据文件（`Language\string.xml` 等）在首次修改前会备份为 `<文件>.orig`。绝不改动可执行文件和 DLL。
- 当 **Steam 更新**或"验证游戏文件完整性"改变了文件时，下次启动会检测到并提示。届时请再次点击 **게임에 적용**（应用到游戏）。

## 模组格式

模组是如下结构的文件夹（分发时打包为 zip）。

```
my-mod/
  mod.json        {"id": "pastel-blocks", "name": "粉彩方块", "version": "1.0.0", "author": "", "description": "", "format": 2}
  preview.png     （可选）列表中显示的图片
  files/          整体替换或新增的文件 —— 与游戏包内路径相同（不区分大小写）
    terrain.png
    Item/Icon/brick/icon_brick_149.png
  merge/          按条目添加或修改游戏表（XML）的补丁
    XML/item_table.xml
    Language/string.xml
```

`merge\` 补丁示例（UTF-8）。存在相同键的条目时整条替换，否则追加到表末尾。

```xml
<?xml version="1.0" encoding="UTF-8"?>
<merge>
  <brick id="149"> … 整个条目 … </brick>
  <item id="149"> … </item>
</merge>
```

- `files\` 替换整个文件。两个模组替换同一文件时，只有优先级高的生效。
- `merge\` 按条目生效，因此多个模组可以各自向同一张表添加条目。
- **请勿整体分发原版游戏文件。** 只包含你修改或新建的内容（NarewStudio 的"배포용 .zip 만들기"就是这样生成的）。
- 详细规则（韩语）：[docs/07-new-content.md](docs/07-new-content.md)

## 连接 AI 智能体

NarewStudio 的全部功能以 **111 个工具**提供（地图·建筑·家具摆放，新方块·家具·物品·音效，任意游戏文件，XML 表，GUI，声音，模型，包，模组管理器）。
智能体通过 ASCII 地图和图片（俯视地图、3D、GUI、模型、波形）检查结果。

| 3D 图（`world_render` view=iso） | 俯视平面图（view=top） |
|---|---|
| ![智能体看到的 3D 图](docs/images/agent-castle-3d.png) | ![智能体看到的平面图](docs/images/agent-castle-top.png) |

<sub>智能体建造顶部截图中的城堡时用来检查结果的图片。橙色方框 = 家具。</sub>

### Claude Code

**使用源码（本仓库）** —— 最简单。

```bash
git clone https://github.com/cailet0422/NarewTools.git
```
```bash
pip install -r NarewTools/requirements.txt
```

在 `NarewTools` 文件夹中启动 Claude Code，就会加载 `.mcp.json` 中的 **narew-studio** 服务器（首次需批准）。给智能体的说明在 `AGENTS.md`（由 `CLAUDE.md` 引入）中。

**使用发布版 exe** —— 无需 Python：

```bash
claude mcp add narew-studio -- "C:\Tools\NarewTools\NarewAgent.exe" mcp
```

### 其他 MCP 客户端（Claude Desktop 等）

把 `NarewAgent.exe mcp` 注册为 stdio 服务器。例如 Claude Desktop 的 `claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "narew-studio": {
      "command": "C:\\Tools\\NarewTools\\NarewAgent.exe",
      "args": ["mcp"]
    }
  }
}
```

从源码运行时：`python -m studio.agent mcp`（工作目录 = `NarewTools`）。

### 仅支持命令行的智能体

```bash
NarewAgent.exe tools
```
```bash
NarewAgent.exe call status --pretty
```
```bash
NarewAgent.exe guide world
```

- `call <工具> 键=值 …` 单次调用，`run steps.json` 多个步骤（保持世界会话），`serve` 逐行收发 JSON。
- 从源码运行时用 `python -m studio.agent` 代替 `NarewAgent.exe`。
- 说明主题：`overview`、`world`、`content`、`gui`、`sound`、`models`、`mod`、`safety`。

### 与 NarewStudio 窗口配合 · 安全机制

- 智能体与窗口共用设置（游戏文件夹、模组项目）。智能体保存后，窗口会在 1 秒内重新读取（窗口中有未保存的编辑时不会覆盖）。
- 智能体能知道窗口当前显示的世界和镜头位置，所以"就在这里盖"这种说法也能理解。
- 游戏文件夹只读。真正改动游戏的工具（`world_apply_to_game`、`world_restore_backup`、`manager_apply`、`manager_restore`）在没有 `confirm=true` 时只报告将会改变什么，智能体**只有在询问你之后**才会执行。游戏运行中会拒绝，并在修改前先备份。
- 详细说明（韩语）：[docs/09-ai-agents.md](docs/09-ai-agents.md)

## 命令行工具（narew）

只需 Python 3.10+（把贴图放进 `.glb` 时可选 Pillow）。在仓库文件夹中运行。

```bash
python -m narew --help
```

**包文件**

```bash
python -m narew info "<游戏文件夹>\data"
```
```bash
python -m narew list "<游戏文件夹>\data\gui_skin.pack" -f "*login*"
```
```bash
python -m narew extract "<游戏文件夹>\data" -o D:\extracted --verify
```
```bash
python -m narew verify "<游戏文件夹>\data\item.pack"
```
```bash
python -m narew build D:\extracted\xml -o D:\out\xml.pack
```
```bash
python -m narew patch D:\work\gui_skin.pack D:\mymod
```

- `extract` 生成 `<输出>\<包名>\` 文件夹和 `<包名>.narew.json`（原始顺序和属性）。`build` 会使用它，未修改时可生成与原版逐字节相同的包。
- `patch` 用覆盖文件夹中的文件替换/添加包内文件（原地修补时首次会生成 `.orig` 备份）。放入游戏时建议使用模组管理器。

**模型 ↔ glTF**

```bash
python -m narew model2gltf D:\extracted\effect\Effect\brick_effect.nwmt -o brick.glb
```
```bash
python -m narew gltf2model D:\work\f1_edit.glb -o D:\work\f1_mod
```
```bash
python -m narew gltf2anim D:\work\f1_edit.glb -s f1.nwbt -t f1_combat_attack_onewp01.nwat -o out.nwat
```

**方块（体素）文件** —— `.iaf` 预制件、`.bwr` 世界

```bash
python -m narew brick info D:\extracted\map\map\64_0.bwr
```
```bash
python -m narew brick export D:\extracted\architecture\Architecture\building\Choga_big_A01.iaf -o choga.vox
```
```bash
python -m narew brick import mybuilding.vox -o mybuilding.iaf
```
```bash
python -m narew brick paste D:\work\64_0.bwr mybuilding.iaf --at 24,110,24 -o D:\work\64_0_mod.bwr
```

- `export` 按扩展名选择格式：`.vox`（MagicaVoxel）、`.png`（`--view top|front|side`）、`.json`。
- 详细说明（韩语）：[docs/04-model-formats.md](docs/04-model-formats.md)、[docs/05-brick-formats.md](docs/05-brick-formats.md)

## 从源码运行 · 构建 · 测试

```bash
git clone https://github.com/cailet0422/NarewTools.git
```
```bash
cd NarewTools
```
```bash
pip install -r requirements.txt
```
```bash
python -m studio
```
```bash
python -m modmanager
```

**构建 exe**（需要 PyInstaller）

```bash
pip install pyinstaller
```
```bash
python tools\build_exe.py
```

三个 exe 会生成到 `dist\NarewTools\`，各自通过自检（`--selftest`，智能体为 MCP 握手）后生成 `dist\NarewTools-<版本>.zip` 和 `.zip.sha256`。自动更新只会获取同时附带**这两个文件**的 GitHub 发布（标签 `v<版本>`）。构建检查还会用生成的 NarewAgent 向临时文件夹应用一次模拟更新。
如果 `dist\NarewTools` 正在使用中，可用 `--dist <其他文件夹>` 构建到别处。

**测试** —— 真实游戏文件夹只读；应用类测试在复制了部分包的假游戏文件夹中进行。

```bash
python tests\test_agent.py "<游戏文件夹>" D:\scratch\t_agent
```
```bash
python tests\test_agent_live.py "<游戏文件夹>" D:\scratch\t_live
```

其他测试（`tests\test_*.py`、`smoke_*.py`）的参数请参考各文件开头的说明。

## 文件位置

| 内容 | 位置 |
|---|---|
| NarewStudio 设置与日志 | `%APPDATA%\NarewStudio\`（`log.txt`） |
| 模组项目 | `Documents\NarewStudio\Projects\`（新建模组时可更改） |
| 保存/捕获的世界 | `Documents\NarewStudio\Worlds\` |
| 应用到我的世界前的备份 | `Documents\NarewStudio\WorldBackups\` |
| 模组管理器（已安装模组、设置、日志） | `%LOCALAPPDATA%\NarewModManager\`（`mods\`、`settings.json`、`deployed.json`、`log.txt`） |
| 应用模组时的原版备份 | `<游戏文件夹>\data\<包>.pack.orig`、`<游戏文件夹>\<文件>.orig` |
| AI 智能体 ↔ 窗口连接 | `%APPDATA%\NarewStudio\agent\`（不使用网络或端口） |
| 自动更新（设置 · 下载、备份、日志） | `%APPDATA%\NarewTools\update.json` · `%LOCALAPPDATA%\NarewTools\update\` |

## 常见问题

| 现象 | 解决方法 |
|---|---|
| 出现"Windows 已保护你的电脑" | 程序没有数字签名。点击 **更多信息 → 仍要运行** |
| 3D 画面全黑或只显示提示 | 更新显卡驱动（需要 OpenGL 3.3）。笔记本请用高性能显卡运行 |
| 无法应用到游戏 | 完全关闭游戏后重试。错误详情见 `%LOCALAPPDATA%\NarewModManager\log.txt` |
| Steam 更新后模组消失 | 更新覆盖了包文件。请在模组管理器中再次点击 **게임에 적용**（应用到游戏） |
| 游戏异常 / 想完全恢复原版 | 在模组管理器中点击 **원본 복구**（恢复原版）。仍不行时：Steam → 属性 → 已安装文件 → **验证游戏文件的完整性** |
| 想撤销对我的世界的修改 | 世界编辑器中的 **↩ 백업으로**（恢复备份），或使用 `Documents\NarewStudio\WorldBackups\` 中的文件 |
| 看不到 AI 智能体工具 | 在 Claude Code 中用 `/mcp` 确认 narew-studio 已批准。从源码运行时确认已执行 `pip install -r requirements.txt` |

## 游戏内验证状态与已知限制

| 功能 | 状态 |
|---|---|
| 新方块、新家具、徒手合成配方、名称文本 | ✅ 已在游戏中验证 |
| 替换 UI 音效、修改 GUI 图片和布局 | ✅ 已在游戏中验证 |
| 世界编辑（方块、家具）+ 应用到我的世界 | ✅ 已在游戏中验证 |
| 修改后的模型/动画在游戏中加载 | ⚠ 尚未验证 |
| "新物品"中普通物品的效果、天气/时间背景音乐 | ⚠ 尚未验证 |
| 多人游戏 | ⚠ 尚未验证 |

- 世界编辑器的家具摆放暂不支持需要自身数据的 80 种家具（告示牌、照片、菜园、商店、邮箱等）。
- 程序界面目前仅支持韩语。
- 未验证的功能出现问题时，请用 **원본 복구**（恢复原版）还原。

## 目录结构与技术文档

```
narew/       格式库 + 命令行工具（pack、brick、模型 ↔ glTF、模组格式、表合并）
studio/      NarewStudio —— core、svc_*、creator（新内容）、worldedit（无界面世界编辑）、gl/（OpenGL）、ui/（Qt）、agent/（AI 智能体）
modmanager/  NarewModManager —— core（应用引擎）、ui
tests/       测试（使用假游戏文件夹）
tools/       build_exe.py（构建 exe）、PE/RTTI 分析与模型校验脚本
docs/        技术文档（韩语）
AGENTS.md    给 AI 智能体的说明；.mcp.json = Claude Code 的 MCP 服务器注册
```

| 文档（韩语） | 内容 |
|---|---|
| [01 包格式](docs/01-pack-format.md) | `.pack` 结构（全部 20 个包可逐字节重建） |
| [02 引擎结构](docs/02-engine-architecture.md) | NarewEngine2 分层与类概览 |
| [03 路线图](docs/03-roadmap.md) | 后续计划 |
| [04 模型格式](docs/04-model-formats.md) | `.nwmt/.nwbt/.nwat` ↔ glTF |
| [05 方块格式](docs/05-brick-formats.md) | `.iaf/.bwr` 体素世界 |
| [06 程序说明书](docs/06-studio-modmanager.md) | NarewStudio 与 NarewModManager 详解 |
| [07 新内容](docs/07-new-content.md) | 引擎上限、表合并规则 |
| [08 GUI 布局](docs/08-gui-layout.md) | 编辑 `Gui\*.xml` |
| [09 AI 智能体](docs/09-ai-agents.md) | 111 个工具、连接、安全机制 |

文档中提到的 `re/` 分析产物（游戏二进制转储、反编译结果）未包含在本仓库中。

## 许可证

[MIT](LICENSE)。Islet Online 及其相关商标和资源的权利归各自的权利人所有。
