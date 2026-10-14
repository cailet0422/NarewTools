# NarewTools — Islet Online 模組工具包

[한국어](README.md) · [English](README.en.md) · [简体中文](README.zh-CN.md) · **繁體中文** · [日本語](README.ja.md)

透過逆向分析 Islet Online（아일렛 온라인）的自製引擎 **NarewEngine2** 所製作的 Windows 10/11 模組工具包。
不需直接修改遊戲檔案，就能用**模組**新增方塊、家具、物品、音效，修改貼圖、GUI 介面、聲音、模型與世界（地圖），
再透過模組管理器放進遊戲，並隨時還原原版。另附一個 MCP 伺服器，讓 AI 代理（Claude Code 等）直接使用相同的功能。

**[⬇ 下載最新版本（Releases）](https://github.com/cailet0422/NarewTools/releases/latest)**

![在 NarewStudio 世界編輯器中開啟自己的世界](docs/images/studio-world.png)
<sub>在世界編輯器中開啟的單人世界。這座城堡是 AI 代理只用 NarewStudio 的工具蓋出來的。</sub>

> [!IMPORTANT]
> 這是非官方的玩家自製工具，與遊戲開發商、發行商無關。本儲存庫與發行檔中**不包含任何遊戲檔案**。
> 必須有正式安裝的遊戲才能使用。遊戲具有連線功能，請在單人模式（自己的世界）中測試，並自行確認遊戲的使用條款。
> 套用模組前會自動備份原始檔案，但使用風險由使用者自行承擔。

> [!NOTE]
> 程式介面目前**僅支援韓文**。本文中按鈕與選單名稱寫成「韓文原文（中文翻譯）」，例如 **새 모드**（新增模組），方便在畫面上尋找。
> `docs/` 中的技術文件為韓文。

## 目錄

- [組成](#組成)
- [功能](#功能)
- [系統需求](#系統需求)
- [安裝](#安裝)
- [首次設定](#首次設定)
- [快速入門：製作第一個模組](#快速入門製作第一個模組)
- [NarewStudio 使用方式](#narewstudio-使用方式)
- [NarewModManager 使用方式](#narewmodmanager-使用方式)
- [模組格式](#模組格式)
- [連接 AI 代理](#連接-ai-代理)
- [命令列工具（narew）](#命令列工具narew)
- [從原始碼執行 · 建置 · 測試](#從原始碼執行--建置--測試)
- [檔案位置](#檔案位置)
- [疑難排解](#疑難排解)
- [遊戲內驗證狀態與已知限制](#遊戲內驗證狀態與已知限制)
- [資料夾結構與技術文件](#資料夾結構與技術文件)
- [授權](#授權)

## 組成

| 程式 | 用途 |
|---|---|
| **NarewStudio.exe** | 製作模組。遊戲檔案**唯讀**，修改的檔案與新增的項目都儲存到模組專案中 |
| **NarewModManager.exe** | 啟用/停用模組、調整順序、套用到遊戲、還原原版 |
| **NarewAgent.exe** | AI 代理連接程式（主控台）。透過 MCP 與命令列，把 NarewStudio 的功能提供為 113 個工具 |
| `narew`（Python） | 命令列工具：解包/打包 .pack、模型 ↔ glTF、方塊（體素）檔案轉換等（從原始碼執行） |

## 功能

**NarewStudio**

| 頁面 | 功能 |
|---|---|
| 리소스 편집（資源編輯） | 瀏覽、搜尋 20 個遊戲封包 + `Language\` 中的所有檔案。圖片（原版/修改版對照，自動轉成原格式與尺寸，調色）、聲音、文字·XML（保留編碼，語法醒目提示）、字型、十六進位檢視。拖入檔案即可替換 |
| 블록 텍스처（方塊貼圖） | 逐格替換、調色 `terrain.png` 方塊圖集，顯示空格 |
| 가구 텍스처（家具貼圖） | 替換 936 種家具模型各材質的貼圖 + 3D 預覽 |
| 새 리소스 추가（新增內容） | **新方塊 / 新家具 / 新物品 / 新音效** 精靈（自動計算可用編號、自動產生圖示、合成配方） |
| GUI 편집（GUI 編輯） | 以實際遊戲圖片顯示 169 個畫面配置：移動、調整大小、修改文字與顏色，新增圖片、按鈕、文字 |
| 사운드 편집（音效編輯） | 波形顯示，剪下/貼上/淡入淡出/音量/正規化/反轉/變速，以原格式儲存 |
| 월드 에디터（世界編輯器） | 以 3D 編輯世界地圖（`.bwr`）與建築預製件（`.iaf`），擺放家具，**開啟/擷取自己的世界（遊戲存檔）並放回遊戲** |
| 모델 뷰어（模型檢視器） | 3,633 個模型與動畫播放，組合角色部件，匯出/匯入 glTF（Blender） |
| 팩 탐색기（封包瀏覽器） | 解包、驗證、打包 `.pack` 檔案 |
| 내 모드（我的模組） | 變更清單與復原、安裝到模組管理器、產生供發佈的 .zip |

**NarewModManager**：安裝模組（拖入 zip）、啟用/停用、優先順序、衝突顯示、XML 表格**逐項合併**（多個模組在同一張表格新增項目也都會生效）、套用到遊戲 / 還原原版、偵測 Steam 更新。

**AI 代理**：可以直接處理「在出生點旁邊蓋一間小屋，放一張床」、「把背包的關閉按鈕移到左下角」這類要求。代理會用 ASCII 地圖與圖片確認結果，開著的 NarewStudio 視窗會立即重新讀取變更。

### 螢幕截圖

| 模型檢視器 —— 組合角色部件、播放動畫 | GUI 編輯 —— 以實際遊戲圖片編輯畫面配置 |
|---|---|
| ![模型檢視器](docs/images/studio-models.png) | ![GUI 編輯](docs/images/studio-gui.png) |
| **音效編輯 —— 波形與區間編輯** | **新增內容 —— 新家具** |
| ![音效編輯](docs/images/studio-sound.png) | ![新家具](docs/images/studio-create.png) |
| **家具貼圖 —— 各材質貼圖與 3D 預覽** | **方塊貼圖 —— 方塊圖集** |
| ![家具貼圖](docs/images/studio-furniture.png) | ![方塊貼圖](docs/images/studio-blocks.png) |

## 系統需求

- Windows 10 / 11（64 位元）
- 已安裝 Islet Online（遊戲資料夾 = 含有 `IsletClient_release(x64).exe` 的資料夾）
- 3D 畫面（世界編輯器、模型檢視器、家具預覽）需要支援 **OpenGL 3.3** 的顯示卡驅動程式。沒有也能使用其他功能。
- 僅從原始碼執行時：Python 3.10 以上（已在 3.12 驗證）

## 安裝

1. 從 [Releases](https://github.com/cailet0422/NarewTools/releases/latest) 下載 `NarewTools-<版本>.zip`。
2. 解壓縮到任意位置（例如 `C:\Tools\`）。不需安裝。
3. 執行 `NarewTools\NarewStudio.exe`。

```
NarewTools\
  NarewStudio.exe        製作模組
  NarewModManager.exe    套用 / 還原模組
  NarewAgent.exe         AI 代理用（主控台）
  사용법.txt              簡易說明（韓文）
  _internal\             三個程式共用的 Qt/Python 執行環境 —— 請勿刪除或單獨移動
```

- 三個 exe 共用 `_internal` 資料夾，移動時請**整個 `NarewTools` 資料夾**一起移動。
- 程式沒有數位簽章，Windows SmartScreen 可能會顯示「Windows 已保護您的電腦」。點選 **其他資訊 → 仍要執行** 即可。
- **自動更新**（2.2.0 起）：NarewStudio 與模組管理器啟動時會檢查新版本（最多每 6 小時一次，可關閉），並顯示更新內容。點選 **업데이트**（更新）會下載並驗證 SHA-256，在關閉程式時替換為新檔案並重新啟動。替換前的檔案備份到 `%LOCALAPPDATA%\NarewTools\update\backup-<版本>\`，出錯時還原原檔案。也可以點選標題列右側的版本按鈕（`v2.5.0`）立即檢查。放在 `Program Files` 等無法寫入位置的資料夾無法自動更新，請移到「文件」等位置。
- 2.1.0 及以下版本請直接替換整個資料夾。設定、模組專案與已安裝的模組儲存在使用者資料夾中，不受影響（見[檔案位置](#檔案位置)）。

## 首次設定

### 1. 選擇遊戲資料夾

第一次執行 NarewStudio 與 NarewModManager 時會要求選擇遊戲資料夾。請選擇含有 `IsletClient_release(x64).exe` 的資料夾。

- Steam 預設位置：`C:\Program Files (x86)\Steam\steamapps\common\Islet Online`
- 安裝在其他收藏庫時：`<收藏庫>\steamapps\common\Islet Online`
- 不確定時：在 Steam 收藏庫中對遊戲按右鍵 → **管理 → 瀏覽本機檔案**，開啟的就是遊戲資料夾。

兩個程式與 AI 代理共用同一份設定。之後要變更，在任一程式中重新選擇遊戲資料夾即可。

### 2. 建立模組專案

點選 NarewStudio 上方的 **새 모드**（新增模組），填寫 모드 이름（模組名稱）、만든 사람（作者）、버전（版本）、설명（說明）與 저장 위치（儲存位置）。
專案預設儲存在 `Documents\NarewStudio\Projects\`（「文件」資料夾）下。之後的所有編輯只會進入這個專案，遊戲資料夾不會被修改。

### 3. 確認

- 如果 3D 畫面全黑或只顯示提示文字，請把顯示卡驅動程式更新到最新（需要 OpenGL 3.3）。
- 發生錯誤時請查看 `%APPDATA%\NarewStudio\log.txt` 與 `%LOCALAPPDATA%\NarewModManager\log.txt`。

## 快速入門：製作第一個模組

範例：新增一個方塊並在遊戲中查看。

1. **NarewStudio** → **새 모드**（新增模組）建立專案。
2. 左側 **새 리소스 추가**（新增內容）→ **새 블록**（新方塊）分頁。
   - 填寫名稱與說明，選擇可用的方塊編號（會自動建議）以及要複製屬性的相似方塊。
   - 放入貼圖（所有面相同，或分別指定頂/側/底）。會自動調整為 128×128。
   - 需要的話加入合成配方（在材料欄輸入物品名稱或編號的一部分會跳出清單）。
   - 按下建立後，圖集貼圖、方塊表格項目、方塊物品、名稱、圖示與配方都會加入模組。
3. 左側 **내 모드**（我的模組）→ **모드 매니저에 설치**（安裝到模組管理器）。
4. **在遊戲關閉的狀態下**開啟 **NarewModManager**，勾選模組並點選 **게임에 적용**（套用到遊戲）。
5. 啟動遊戲確認。
6. 要復原時，關閉遊戲，在 NarewModManager 中點選 **원본 복구**（還原原版）。

## NarewStudio 使用方式

### 리소스 편집（資源編輯）

- 在左側樹狀結構中瀏覽封包與資料夾，或在上方搜尋框輸入檔名的一部分。
- **圖片**：原版與修改版並排對照。拖入新圖片會自動轉成原格式與尺寸。可調整色相、飽和度、亮度、對比與著色。
- **文字 · XML · 著色器**：保留原編碼（EUC-KR 等）、BOM 與換行字元進行編輯。語法醒目提示、檢查、尋找。
- **聲音**：播放、以相同格式替換。細部編輯請用「사운드 편집기로 열기」（在音效編輯器中開啟）。
- **외부 편집기로 열기**（以外部編輯器開啟）：可在 Photoshop、記事本等修改後套用。
- 修改過的檔案會進入模組的 `files\`。可在 **내 모드**（我的模組）頁面逐一復原。

### 블록 텍스처 · 가구 텍스처（方塊貼圖 · 家具貼圖）

- **方塊貼圖**：`terrain.png`（顏色）與 `terrain_specular.png`（反光）16×16 格圖集。選取一格會顯示使用它的方塊，可替換圖片（自動符合 128×128）、調色或還原原版。
- **家具貼圖**：選擇家具後會顯示模型各材質的貼圖與 3D 預覽。只連結遊戲實際讀取的貼圖。

### 새 리소스 추가（新增內容）

| 分頁 | 輸入 | 加入模組的內容 |
|---|---|---|
| 새 블록（新方塊） | 名稱·說明（韓/英）、可用方塊編號、相似方塊（複製硬度、採集工具、分類）、貼圖（所有面 / 頂·側·底）、反光（選用）、配方（選用） | 圖集貼圖、方塊表格項目、方塊物品、名稱、自動圖示、配方 |
| 새 가구（新家具） | 名稱·說明、可用家具編號、物品編號、功能範本（複製坐、躺、照明等行為的家具）、模型（**複製現有家具 + 換貼圖** 或 **匯入 glTF**）、圖示（圖片或 3D 預覽擷取）、配方 | `.nwmt` 模型（+貼圖）、家具表格項目、物品、名稱、圖示、配方 |
| 새 아이템（新物品） | 名稱·說明、物品編號、範本物品（食物、藥水、材料、工具等 —— 效果與數值沿用範本）、圖示、配方 | 物品表格項目、名稱、圖示、配方 |
| 새 사운드（新音效） | 聲音檔（.wav/.ogg/.mp3）、名稱、作為背景音樂時的天氣·時間與音量，或作為 UI 音效的位置 | 聲音檔、`sky.xml` / `Gui\sound.xml` 項目 |
| 만든 것（已建立） | — | 本模組新增/修改的項目清單，可逐項移除 |

引擎接受的編號範圍（以 2026-10 的遊戲為準）：

| 類型 | 可用編號 | 備註 |
|---|---|---|
| 方塊 | 149、160–170、192–239 | 171–191 被舊預製件使用，避開 |
| 家具 | 58、59、597–599、942–1023 | 引擎上限 1–1023 |
| 物品 | 建議 19300–19999 | 引擎上限 1–19999。方塊物品的編號與方塊編號相同 |
| 合成配方 | 從 2599 往下 | 引擎上限 ≤ 2599 |

NarewStudio 每次開啟模組時都會重新計算可用編號。遊戲更新後，請重新開啟模組，確認編號沒有與官方內容衝突。

### GUI 편집（GUI 編輯）

- 以實際遊戲圖片繪製遊戲畫面配置（`gui.pack` 中的 `Gui\*.xml`）。
- 點選元素拖曳，或用**方向鍵**移動（Shift = 10px）。**Ctrl+點選** 或在空白處拖曳可多選。
- 輸入位置與尺寸數值，修改文字、字型大小、顏色，用 **＋ 이미지 / 버튼 / 글자**（+ 圖片 / 按鈕 / 文字）新增元素（遊戲圖片或自己的 PNG），按 **Delete** 刪除。
- 預覽螢幕尺寸（1920×1080 / 1600×900 / 1280×720）、顯示隱藏元素、直接編輯 XML、復原。
- 按鈕圖片可透過「그림 편집」（編輯圖片）直接跳到資源編輯。只改寫變動的數值，保持原檔案格式。

### 사운드 편집（音效編輯）

- 顯示並播放遊戲聲音（WAV、OGG、MP3）的波形。拖曳（或 Shift+點選）選取區間，滾輪縮放。
- 剪下、複製、貼上、僅保留選取、靜音、淡入/淡出、音量（dB）、正規化、反轉、變速，復原/重做。
- 從其他檔案匯入（自動配合取樣率與聲道），匯出為 WAV/OGG/MP3。
- **모드에 저장**（儲存到模組）會保持原格式、取樣率與聲道。

### 월드 에디터（世界編輯器）

以 3D 方式編輯遊戲地圖（`.bwr`）與建築預製件（`.iaf`）。

| 操作 | 按鍵 |
|---|---|
| 移動 | W A S D，上/下 Q E |
| 旋轉 / 平移 / 縮放 | 左鍵拖曳 / 中鍵拖曳 / 滾輪 |
| 復原 / 重做 / 儲存 | Ctrl+Z / Ctrl+Y / Ctrl+S |
| 家具旋轉 90° | R |
| 旋轉已擺放的家具 / 開關燈 / 刪除（「✋ 설치물 편집」） | R / T / Delete |

- 工具：檢視、放置、刪除、塗色、滴管、填滿/挖空方塊區域、貼上預製件、**圖片像素畫**、**擺放家具**、**編輯家具**、出生位置、刪除物件。
- **擺放家具**：在右側「설치물」（家具）分頁選擇家具後點選。綠色 = 可放置，紅色 = 被擋住。家具下方的隱藏碰撞格會自動填入。燈具擺放時為開啟狀態。
- **編輯家具**（「✋ 설치물 편집」）：點選已擺放的家具將其選取（淺藍色方框），按 **R** 以其所在格為中心旋轉 90°（碰撞格一起移動，箱子裡的物品保留），按 **T** 開關燈，按 **Delete** 刪除。燈的標記：黃色 = 開，灰色 = 關。關著的燈在遊戲中不會發光。
- **圖片像素畫**（「🖼 이미지 고르기…」）：選擇圖片檔（或剪貼簿中的圖片），設定大小、方向（朝四個方向之一的牆或地面）、雙面、顏色加深、抖色以及要使用的方塊，並查看預覽。點選地圖後，圖片底邊的中點會立在該格（牆為正面，地面為中心）。每個像素使用顏色最接近的方塊；預設不使用花紋明顯的方塊，透明像素保持原樣。雙面牆從背面看文字也是正的。復原一次即可刪除。
- 大型地圖只載入鏡頭周圍（可調整視距）。點選小地圖可跳轉。

**編輯自己的世界**（單人世界）

1. 在 **💾 내 월드**（我的世界）中開啟遊戲存檔（`<遊戲資料夾>\Local\<世界>.bwr`）。開啟的是遊戲最後一次儲存的狀態。
2. 修改後點選 **저장**（儲存）→ 原檔案保持不變，以新檔案儲存到 `Documents\NarewStudio\Worlds\`。
3. 開啟該檔案，在**遊戲關閉的狀態下**點選 **🎮 내 월드에 적용**（套用到我的世界）→ 用這個檔案取代遊戲存檔。
   - 取代前會先把目前的存檔（`.bwr .lnd .npc .db`）備份到 `Documents\NarewStudio\WorldBackups\<世界>_<時間>\`。
   - 點選 **↩ 백업으로**（還原備份）可隨時復原。
   - 遊戲執行中會拒絕。若遊戲的儲存時間晚於修改後的檔案，會發出警告。

**世界擷取** —— 把正在玩的世界保存為 `.bwr`

- **📸 현재 월드 저장**（儲存目前世界）或 **캡처 창**（擷取視窗，永遠在最上層的小視窗）。遊戲中也可以按 **Ctrl+Alt+S** 儲存。
- 開啟「게임이 저장할 때마다 자동으로 남기기」（遊戲每次存檔時自動保留）後，遊戲每存檔一次就保留一份。
- 邊玩邊只顯示擷取視窗：`NarewStudio.exe --capture`。
- 儲存的是**遊戲最後一次存檔的狀態**（不會向遊戲注入程式碼）。只能擷取儲存在自己電腦上的世界。

### 모델 뷰어 · Blender（模型檢視器）

- 依類型瀏覽模型並播放動畫。角色可雙擊衣服、頭髮、臉、鞋子等部件，組合到同一個場景中。
- **匯出為 glTF** → 在 Blender 中修改 → **從 glTF 匯入**，即可修改模型與動畫。也支援新增動畫與變更播放速度。
- Blender 提示：匯入前把場景影格速率設為 **30 fps**，匯出時開啟 **Include → Custom Properties**（保留原始資料）。

### 팩 탐색기 · 내 모드（封包瀏覽器 · 我的模組）

- **封包瀏覽器**：封包清單、檔案詳細資訊（壓縮、MD5）、解包單一封包/資料夾/全部（MD5 驗證）、從資料夾打包。
- **我的模組**：模組資訊與預覽圖、變更檔案與表格合併清單（逐項復原）、**모드 매니저에 설치**（安裝到模組管理器）、**배포용 .zip 만들기**（產生供發佈的 .zip）。

## NarewModManager 使用方式

1. 選擇遊戲資料夾（與 NarewStudio 共用設定）。
2. 安裝模組：把模組 `.zip` 拖入視窗 / **＋ .zip 설치…**（安裝 .zip）/ **폴더에서 설치…**（從資料夾安裝）/ NarewStudio 中的 **모드 매니저에 설치**。
3. 勾選以啟用，拖曳或用 ▲▼ 調整順序（**越上面優先順序越高**）。右側可查看模組的檔案、表格合併，以及被其他模組蓋掉的檔案。
4. **在遊戲關閉的狀態下**點選 **게임에 적용**（套用到遊戲）。要復原請點 **원본 복구**（還原原版）。

![NarewModManager —— 已安裝的模組與優先順序](docs/images/modmanager.png)

套用方式：

| 方式 | 說明 |
|---|---|
| 封包修補（預設，建議） | 第一次把原版封包保存為 `data\<封包>.pack.orig`，之後每次套用都以原版 + 已啟用的模組重新產生封包。未變更的檔案直接複製，不重新壓縮 |
| 散檔（實驗性） | 把模組檔案以散檔形式放到遊戲資料夾中，不動封包。僅驗證過 GUI 圖片 |

- **表格合併**：依優先順序由低到高，依序套用已啟用模組的 `merge\` 修補。兩個模組修改同一項目時，上面的模組生效。保留原編碼與換行字元。
- 遊戲資料夾中的散資料檔（`Language\string.xml` 等）第一次修改前會備份為 `<檔案>.orig`。絕不修改執行檔。引擎 DLL 只由**含程式碼修補的模組（格式 3）**修改，修改前先備份一次為 `<dll>.orig`；只有原始位元組與 DLL 版本（SHA-256）相符時才寫入，一個修補檔要嘛寫入它的所有 DLL，要嘛一個都不寫。遊戲更新導致版本改變時會略過該修補。
- 當 **Steam 更新**或「驗證遊戲檔案完整性」變更了檔案時，下次啟動會偵測並提示。屆時請再次點選 **게임에 적용**（套用到遊戲）。

## 模組格式

模組是下列結構的資料夾（發佈時壓縮成 zip）。

```
my-mod/
  mod.json        {"id": "pastel-blocks", "name": "粉彩方塊", "version": "1.0.0", "author": "", "description": "", "format": 2}
  preview.png     （選用）清單中顯示的圖片
  files/          整個取代或新增的檔案 —— 與遊戲封包內的路徑相同（不分大小寫）
    terrain.png
    Item/Icon/brick/icon_brick_149.png
  merge/          逐項新增或修改遊戲表格（XML）的修補
    XML/item_table.xml
    Language/string.xml
  patches/        （格式 3）引擎 DLL 程式碼修補 — 只修改經過驗證的原始位元組
    free_camera.json
```

`merge\` 修補範例（UTF-8）。有相同鍵值的項目時整項取代，否則附加到表格最後。

```xml
<?xml version="1.0" encoding="UTF-8"?>
<merge>
  <brick id="149"> … 整個項目 … </brick>
  <item id="149"> … </item>
</merge>
```

- `files\` 取代整個檔案。兩個模組取代同一檔案時，只有優先順序高的生效。
- `merge\` 逐項生效，因此多個模組可以各自在同一張表格新增項目。
- **請勿整個發佈原版遊戲檔案。** 只放入你修改或新建的內容（NarewStudio 的「배포용 .zip 만들기」就是這樣產生的）。
- 詳細規則（韓文）：[docs/07-new-content.md](docs/07-new-content.md)
- `patches\` 用於修改寫在引擎程式碼而不是資料裡的行為（例如視野距離段位、自由視角）。目標只能是四個引擎 DLL，用戶端 exe 與 Steam 檔案會被拒絕。格式與安全規則（韓文）：[docs/10-code-patches.md](docs/10-code-patches.md)

## 連接 AI 代理

NarewStudio 的全部功能以 **113 個工具**提供（地圖·建築·家具擺放，新方塊·家具·物品·音效，任意遊戲檔案，XML 表格，GUI，聲音，模型，封包，模組管理器）。
代理會透過 ASCII 地圖與圖片（俯視地圖、3D、GUI、模型、波形）確認結果。

**大於 1024 的世界**：遊戲的「建立地圖」一律是 1024×1024，但代理可以用 `world_new` 製作最大 **4096×4096** 的平坦世界，
再用 `world_apply_to_game(new_world="名稱")` 加入為新世界（不影響其他世界）。4096 時遊戲伺服器約使用 4 GB 記憶體。

| 3D 圖（`world_render` view=iso） | 俯視平面圖（view=top） |
|---|---|
| ![代理看到的 3D 圖](docs/images/agent-castle-3d.png) | ![代理看到的平面圖](docs/images/agent-castle-top.png) |

<sub>代理蓋出最上方截圖中的城堡時，用來確認結果的圖片。橘色方框 = 家具。</sub>

### Claude Code

**使用原始碼（本儲存庫）** —— 最簡單。

```bash
git clone https://github.com/cailet0422/NarewTools.git
```
```bash
pip install -r NarewTools/requirements.txt
```

在 `NarewTools` 資料夾中啟動 Claude Code，就會載入 `.mcp.json` 中的 **narew-studio** 伺服器（第一次需核准）。給代理的說明在 `AGENTS.md`（由 `CLAUDE.md` 載入）中。

**使用發行版 exe** —— 不需要 Python：

```bash
claude mcp add narew-studio -- "C:\Tools\NarewTools\NarewAgent.exe" mcp
```

### 其他 MCP 用戶端（Claude Desktop 等）

把 `NarewAgent.exe mcp` 註冊為 stdio 伺服器。例如 Claude Desktop 的 `claude_desktop_config.json`：

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

從原始碼執行時：`python -m studio.agent mcp`（工作目錄 = `NarewTools`）。

### 僅支援命令列的代理

```bash
NarewAgent.exe tools
```
```bash
NarewAgent.exe call status --pretty
```
```bash
NarewAgent.exe guide world
```

- `call <工具> 鍵=值 …` 單次呼叫，`run steps.json` 多個步驟（保持世界工作階段），`serve` 逐行收發 JSON。
- 從原始碼執行時以 `python -m studio.agent` 取代 `NarewAgent.exe`。
- 說明主題：`overview`、`world`、`content`、`gui`、`sound`、`models`、`mod`、`safety`。

### 與 NarewStudio 視窗搭配 · 安全機制

- 代理與視窗共用設定（遊戲資料夾、模組專案）。代理儲存後，視窗會在 1 秒內重新讀取（視窗中有未儲存的編輯時不會覆蓋）。
- 代理能知道視窗目前顯示的世界與鏡頭位置，所以「就蓋在這裡」這種說法也能理解。
- 遊戲資料夾唯讀。真正修改遊戲的工具（`world_apply_to_game`、`world_restore_backup`、`manager_apply`、`manager_restore`）在沒有 `confirm=true` 時只會回報將會變更什麼，代理**只有在詢問你之後**才會執行。遊戲執行中會拒絕，並在修改前先備份。
- 詳細說明（韓文）：[docs/09-ai-agents.md](docs/09-ai-agents.md)

## 命令列工具（narew）

只需要 Python 3.10+（要把貼圖放進 `.glb` 時可選用 Pillow）。在儲存庫資料夾中執行。

```bash
python -m narew --help
```

**封包檔案**

```bash
python -m narew info "<遊戲資料夾>\data"
```
```bash
python -m narew list "<遊戲資料夾>\data\gui_skin.pack" -f "*login*"
```
```bash
python -m narew extract "<遊戲資料夾>\data" -o D:\extracted --verify
```
```bash
python -m narew verify "<遊戲資料夾>\data\item.pack"
```
```bash
python -m narew build D:\extracted\xml -o D:\out\xml.pack
```
```bash
python -m narew patch D:\work\gui_skin.pack D:\mymod
```

- `extract` 會產生 `<輸出>\<封包名>\` 資料夾與 `<封包名>.narew.json`（原始順序與屬性）。`build` 會使用它，未修改時可產生與原版逐位元組相同的封包。
- `patch` 以覆蓋資料夾中的檔案取代/新增封包內的檔案（就地修補時第一次會產生 `.orig` 備份）。要放進遊戲時建議使用模組管理器。

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

**方塊（體素）檔案** —— `.iaf` 預製件、`.bwr` 世界

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

- `export` 依副檔名選擇格式：`.vox`（MagicaVoxel）、`.png`（`--view top|front|side`）、`.json`。
- 詳細說明（韓文）：[docs/04-model-formats.md](docs/04-model-formats.md)、[docs/05-brick-formats.md](docs/05-brick-formats.md)

## 從原始碼執行 · 建置 · 測試

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

**建置 exe**（需要 PyInstaller）

```bash
pip install pyinstaller
```
```bash
python tools\build_exe.py
```

三個 exe 會建置到 `dist\NarewTools\`，各自通過自我檢查（`--selftest`，代理為 MCP 交握）後產生 `dist\NarewTools-<版本>.zip` 與 `.zip.sha256`。自動更新只會取得同時附帶**這兩個檔案**的 GitHub 發行版本（標籤 `v<版本>`）。建置檢查也會用產生的 NarewAgent 對暫存資料夾套用一次模擬更新。
如果 `dist\NarewTools` 正在使用中，可用 `--dist <其他資料夾>` 建置到別處。

**測試** —— 真實遊戲資料夾唯讀；套用類測試在複製了部分封包的假遊戲資料夾中進行。

```bash
python tests\test_agent.py "<遊戲資料夾>" D:\scratch\t_agent
```
```bash
python tests\test_agent_live.py "<遊戲資料夾>" D:\scratch\t_live
```

其他測試（`tests\test_*.py`、`smoke_*.py`）的參數請參考各檔案開頭的說明。

## 檔案位置

| 內容 | 位置 |
|---|---|
| NarewStudio 設定 · 記錄檔 | `Documents\NarewStudio\settings.json` · `%APPDATA%\NarewStudio\log.txt` |
| 模組專案 | `Documents\NarewStudio\Projects\`（新增模組時可變更） |
| 儲存/擷取的世界 | `Documents\NarewStudio\Worlds\` |
| 套用到我的世界前的備份 | `Documents\NarewStudio\WorldBackups\` |
| 模組管理器（已安裝模組、設定 · 記錄檔） | `Documents\NarewStudio\ModManager\`（`mods\`、`settings.json`、`deployed.json`） · `%LOCALAPPDATA%\NarewModManager\log.txt` |
| 套用模組時的原版備份 | `<遊戲資料夾>\data\<封包>.pack.orig`、`<遊戲資料夾>\<檔案>.orig` |
| AI 代理 ↔ 視窗連接 | `Documents\NarewStudio\agent\`（不使用網路或連接埠） |
| 自動更新（設定 · 下載、備份、記錄） | `%APPDATA%\NarewTools\update.json` · `%LOCALAPPDATA%\NarewTools\update\` |

NarewStudio、模組管理器與 AI 代理共用的設定和已安裝模組在 2.4.0 及以前存放在 AppData。從 Claude 桌面應用程式等 Windows 封裝（MSIX）應用程式啟動的代理，會把它在 AppData 新建的檔案寫入只有它自己看得到的副本，因此改為存放在 `Documents\NarewStudio\`。舊位置的設定與模組會在第一次啟動時移轉過來。

## 疑難排解

| 症狀 | 解決方式 |
|---|---|
| 出現「Windows 已保護您的電腦」 | 程式沒有數位簽章。點選 **其他資訊 → 仍要執行** |
| 3D 畫面全黑或只顯示提示 | 更新顯示卡驅動程式（需要 OpenGL 3.3）。筆電請以高效能顯示卡執行 |
| 無法套用到遊戲 | 完全關閉遊戲後重試。錯誤詳情見 `%LOCALAPPDATA%\NarewModManager\log.txt` |
| Steam 更新後模組不見了 | 更新覆蓋了封包。請在模組管理器中再次點選 **게임에 적용**（套用到遊戲） |
| 遊戲異常 / 想完全還原原版 | 在模組管理器中點選 **원본 복구**（還原原版）。仍不行時：Steam → 內容 → 已安裝的檔案 → **驗證遊戲檔案完整性** |
| 想復原對我的世界的修改 | 世界編輯器中的 **↩ 백업으로**（還原備份），或使用 `Documents\NarewStudio\WorldBackups\` 中的檔案 |
| 看不到 AI 代理工具 | 在 Claude Code 中用 `/mcp` 確認 narew-studio 已核准。從原始碼執行時確認已執行 `pip install -r requirements.txt` |

## 遊戲內驗證狀態與已知限制

| 功能 | 狀態 |
|---|---|
| 新方塊、新家具、徒手合成配方、名稱文字 | ✅ 已在遊戲中驗證 |
| 替換 UI 音效、修改 GUI 圖片與配置 | ✅ 已在遊戲中驗證 |
| 世界編輯（方塊、家具）+ 套用到我的世界 | ✅ 已在遊戲中驗證 |
| 大於 1024 的世界（最大 4096×4096，`world_new`） | ✅ 已在遊戲中驗證 |
| 引擎程式碼修補（`patches\`：視野距離滑桿與區域載入、自由視角） | ✅ 已在遊戲中驗證 |
| 修改後的模型/動畫在遊戲中載入 | ⚠ 尚未驗證 |
| 「新物品」一般物品的效果、天氣/時間背景音樂 | ⚠ 尚未驗證 |
| 多人遊戲 | ⚠ 尚未驗證 |

- 世界編輯器的家具擺放尚不支援需要自身資料的 80 種家具（告示牌、照片、菜園、商店、郵筒等）。
- 程式介面目前僅支援韓文。
- 未驗證的功能發生問題時，請用 **원본 복구**（還原原版）復原。

## 資料夾結構與技術文件

```
narew/       格式函式庫 + 命令列工具（pack、brick、模型 ↔ glTF、模組格式、表格合併）
studio/      NarewStudio —— core、svc_*、creator（新內容）、worldedit（無介面世界編輯）、gl/（OpenGL）、ui/（Qt）、agent/（AI 代理）
modmanager/  NarewModManager —— core（套用引擎）、ui
tests/       測試（使用假遊戲資料夾）
tools/       build_exe.py（建置 exe）、mod_view_distance.py / mod_freecam.py（產生程式碼修補範例模組）、PE/RTTI 分析與模型驗證腳本
docs/        技術文件（韓文）
AGENTS.md    給 AI 代理的說明；.mcp.json = Claude Code 的 MCP 伺服器註冊
```

| 文件（韓文） | 內容 |
|---|---|
| [01 封包格式](docs/01-pack-format.md) | `.pack` 結構（全部 20 個封包可逐位元組重建） |
| [02 引擎結構](docs/02-engine-architecture.md) | NarewEngine2 分層與類別概覽 |
| [03 路線圖](docs/03-roadmap.md) | 後續計畫 |
| [04 模型格式](docs/04-model-formats.md) | `.nwmt/.nwbt/.nwat` ↔ glTF |
| [05 方塊格式](docs/05-brick-formats.md) | `.iaf/.bwr` 體素世界 |
| [06 程式說明書](docs/06-studio-modmanager.md) | NarewStudio 與 NarewModManager 詳解 |
| [07 新內容](docs/07-new-content.md) | 引擎上限、表格合併規則 |
| [08 GUI 配置](docs/08-gui-layout.md) | 編輯 `Gui\*.xml` |
| [09 AI 代理](docs/09-ai-agents.md) | 113 個工具、連接、安全機制 |
| [10 程式碼修補](docs/10-code-patches.md) | 模組格式 3 `patches/`、視野距離滑桿與自由視角的分析 |

文件中提到的 `re/` 分析產物（遊戲二進位傾印、反編譯結果）未包含在本儲存庫中。

## 授權

[MIT](LICENSE)。Islet Online 及其相關商標與資源的權利歸各自的權利人所有。
